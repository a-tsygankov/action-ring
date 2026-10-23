"""Direct Apple TV connection over the Companion protocol (pyatv).

Home Assistant's Apple TV integration doesn't expose touch gestures, which
are what give tvOS scrubbing with thumbnail previews. This module keeps its
own Companion connection with its own pairing (separate from HA's, so the two
sessions never share an identity) and offers blocking touch calls to the knob
engine.

Pairing is driven through files in the app's config share (/config here,
/addon_configs/local_streamdock_n3 outside):

1. write atv_pair_request.json: {"address": "<Apple TV IP>"}
2. the TV shows a PIN; the log says "pairing: waiting for PIN"
3. write the PIN to atv_pair_pin
4. credentials are saved to atv_companion.json (secret; not in the repo)
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

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

    async def _companion_config(self, address: str) -> AppleTV:
        found = await pyatv.scan(self._loop, hosts=[address], timeout=5)
        if not found:
            raise RuntimeError(f"no Apple TV answered at {address}")
        scanned = found[0]
        service = scanned.get_service(Protocol.Companion)
        if service is None:
            raise RuntimeError(f"{scanned.name} offers no Companion service")
        # Companion only: HA owns the other protocols, and unpaired ones
        # would fail to connect anyway.
        # The device identifier comes from the added service.
        conf = AppleTV(scanned.address, scanned.name, device_info=scanned.device_info)
        conf.add_service(service)
        return conf

    async def _pair(self, request: dict[str, Any]) -> None:
        address = request["address"]
        PAIR_PIN.unlink(missing_ok=True)
        conf = await self._companion_config(address)
        pairing = await pyatv.pair(conf, Protocol.Companion, self._loop, name="ActionRing")
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
            data = {
                "address": address,
                "name": conf.name,
                "credentials": pairing.service.credentials,
            }
            CRED_FILE.write_text(json.dumps(data))
            os.chmod(CRED_FILE, 0o600)
            log(f"paired with {conf.name}; credentials saved")
        finally:
            await pairing.close()
            PAIR_PIN.unlink(missing_ok=True)
            PAIR_REQUEST.unlink(missing_ok=True)
        if self._atv is not None:
            self._atv.close()
            self._atv = None

    async def _connect(self, data: dict[str, Any]) -> None:
        conf = await self._companion_config(data["address"])
        conf.set_credentials(Protocol.Companion, data["credentials"])
        atv = await pyatv.connect(conf, self._loop)
        atv.listener = _Listener(self)
        self._atv = atv
        log(f"connected to {conf.name} (Companion)")

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
