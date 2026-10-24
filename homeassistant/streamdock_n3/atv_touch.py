"""Direct Apple TV connection over the Companion protocol (pyatv).

Home Assistant's Apple TV integration doesn't expose touch gestures, which
are what give tvOS scrubbing with thumbnail previews. This module keeps its
own Companion connection with its own pairing (separate from HA's, so the two
sessions never share an identity) and offers blocking touch calls to the knob
engine.

Optionally it is also paired for AirPlay: with AirPlay credentials pyatv
opens the media-remote (MRP) tunnel, which mrp_raw.py taps for diagnostics.

Pairing is driven through files in the app's config share (/config here,
/addon_configs/local_streamdock_n3 outside):

1. write atv_pair_request.json: {"address": "<Apple TV IP>",
   "protocol": "companion" (default) | "airplay"}
2. the TV shows a PIN; the log says "pairing: waiting for PIN"
3. write the PIN to atv_pair_pin
4. credentials are saved to atv_companion.json (secret; not in the repo):
   "credentials" (Companion) and "airplay_credentials"
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Awaitable, Callable

import pyatv
from pyatv.conf import AppleTV
from pyatv.const import Protocol, TouchAction

CONFIG_DIR = Path("/config")
CRED_FILE = CONFIG_DIR / "atv_companion.json"
PAIR_REQUEST = CONFIG_DIR / "atv_pair_request.json"
PAIR_PIN = CONFIG_DIR / "atv_pair_pin"
PIN_WAIT_SECONDS = 300
RECONNECT_SECONDS = 10

_MODES = {"press": TouchAction.Press, "hold": TouchAction.Hold, "release": TouchAction.Release}
PROTOCOLS = {"companion": Protocol.Companion, "airplay": Protocol.AirPlay}
CRED_KEYS = {"companion": "credentials", "airplay": "airplay_credentials"}


def log(msg: str) -> None:
    print(f"atv-touch: {msg}", flush=True)


class AppleTVTouch:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._atv: Any = None
        threading.Thread(target=self._loop.run_forever, daemon=True, name="pyatv").start()
        self._submit(self._supervise())

    # -- blocking API (knob sender thread) ---------------------------------
    def available(self) -> bool:
        return self._atv is not None

    def action(self, x: int, y: int, mode: str, timeout: float = 2.0) -> None:
        atv = self._atv
        if atv is None:
            raise RuntimeError("not connected")
        self._submit(atv.touch.action(int(x), int(y), _MODES[mode])).result(timeout)

    def run(self, fn: Callable[[Any], Awaitable[Any]], timeout: float = 10) -> Any:
        """Await fn(atv) on the pyatv loop and return its result (probe.py)."""
        atv = self._atv
        if atv is None:
            raise RuntimeError("not connected")
        future = self._submit(fn(atv))
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()  # don't leave it running on the loop
            raise

    # -- connection management (pyatv loop) ----------------------------------
    def _submit(self, coro) -> Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _supervise(self) -> None:
        while True:
            try:
                if PAIR_REQUEST.exists():
                    await self._pair(json.loads(PAIR_REQUEST.read_text()))
                elif self._atv is None and CRED_FILE.exists():
                    await self._connect(json.loads(CRED_FILE.read_text()))
            except Exception as err:  # keep supervising; retry later
                log(f"{type(err).__name__}: {err}")
                await asyncio.sleep(RECONNECT_SECONDS)
            await asyncio.sleep(2)

    async def _config(self, address: str, protocols: list[str]) -> AppleTV:
        found = await pyatv.scan(self._loop, hosts=[address], timeout=5)
        if not found:
            raise RuntimeError(f"no Apple TV answered at {address}")
        scanned = found[0]
        # Only the protocols this app paired: unpaired ones would fail to
        # connect. The device identifier comes from the added services.
        conf = AppleTV(scanned.address, scanned.name, device_info=scanned.device_info)
        for name in protocols:
            service = scanned.get_service(PROTOCOLS[name])
            if service is None:
                raise RuntimeError(f"{scanned.name} offers no {name} service")
            conf.add_service(service)
        return conf

    async def _pair(self, request: dict[str, Any]) -> None:
        # Every outcome consumes the request, so a caller can tell the attempt
        # is over: success leaves a newer atv_companion.json, failure doesn't.
        PAIR_PIN.unlink(missing_ok=True)
        try:
            protocol = request.get("protocol", "companion")
            if protocol not in PROTOCOLS:
                raise ValueError(f"unknown protocol {protocol!r}")
            await self._pair_with(request["address"], protocol)
        except Exception as err:
            log(f"pairing failed: {type(err).__name__}: {err}")
        finally:
            PAIR_PIN.unlink(missing_ok=True)
            PAIR_REQUEST.unlink(missing_ok=True)
        if self._atv is not None:  # reconnect with the new credentials
            self._atv.close()
            self._atv = None

    async def _pair_with(self, address: str, protocol: str) -> None:
        conf = await self._config(address, [protocol])
        pairing = await pyatv.pair(conf, PROTOCOLS[protocol], self._loop, name="ActionRing")
        try:
            await pairing.begin()
            log(f"pairing: waiting for PIN shown on {conf.name} (write it to atv_pair_pin)")
            for _ in range(PIN_WAIT_SECONDS):
                if PAIR_PIN.exists():
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError("pairing: no PIN within 5 minutes; write atv_pair_request.json again")
            pairing.pin(int(PAIR_PIN.read_text().strip()))
            await pairing.finish()
            if not pairing.has_paired:
                raise RuntimeError("pairing: rejected (wrong PIN?)")
            # Keep the other protocol's credentials; add/replace this one.
            data = json.loads(CRED_FILE.read_text()) if CRED_FILE.exists() else {}
            data.update({"address": address, "name": conf.name,
                         CRED_KEYS[protocol]: pairing.service.credentials})
            CRED_FILE.write_text(json.dumps(data))
            os.chmod(CRED_FILE, 0o600)
            log(f"paired with {conf.name} ({protocol}); credentials saved")
        finally:
            await pairing.close()

    async def _connect(self, data: dict[str, Any]) -> None:
        protocols = [p for p, key in CRED_KEYS.items() if data.get(key)]
        conf = await self._config(data["address"], protocols)
        for name in protocols:
            conf.set_credentials(PROTOCOLS[name], data[CRED_KEYS[name]])
        atv = await pyatv.connect(conf, self._loop)
        atv.listener = _Listener(self)
        self._atv = atv
        log(f"connected to {conf.name} ({', '.join(protocols)})")

    def _lost(self, reason: str) -> None:
        log(f"connection lost: {reason}; reconnecting")
        self._atv = None


class _Listener:
    """pyatv DeviceListener: drop the handle so _supervise reconnects."""

    def __init__(self, owner: AppleTVTouch) -> None:
        self._owner = owner

    def connection_lost(self, exception: Exception) -> None:
        self._owner._lost(repr(exception))

    def connection_closed(self) -> None:
        self._owner._lost("closed")
