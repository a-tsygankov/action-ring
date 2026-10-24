"""Diagnostics: dump raw MRP now-playing messages from the Apple TV.

Home Assistant only exposes a summary of the Apple TV's now-playing state.
Apps can put more in the media-remote protocol (MRP): every content-item
metadata field, plus app-supplied property lists (nowPlayingInfoData,
userInfoData, ...). This module hooks every pyatv MRP session in this process
(the app's own session over the AirPlay tunnel, see atv_touch.py) and writes
each now-playing message as one JSON line to /config/mrp_raw.log, with plist
blobs decoded, to look for app-specific ad markers (e.g. Pluto TV).

Enabled by "mrp_raw_log": true in the "atv" config section; needs the
AirPlay pairing (pair.sh atv airplay).
"""

from __future__ import annotations

import base64
import json
import plistlib
import time
from pathlib import Path
from typing import Any

from pyatv.protocols.mrp import protobuf
from pyatv.protocols.mrp import protocol as mrp_protocol

LOG_FILE = Path("/config/mrp_raw.log")
MAX_BYTES = 5_000_000
SKIP_FIELDS = {"artworkData", "remoteArtworks", "animatedArtworks"}  # images, not signals
MESSAGES = {
    protobuf.SET_STATE_MESSAGE: "set_state",
    protobuf.UPDATE_CONTENT_ITEM_MESSAGE: "update_content_item",
    protobuf.SET_NOW_PLAYING_CLIENT_MESSAGE: "now_playing_client",
    protobuf.SET_NOW_PLAYING_PLAYER_MESSAGE: "now_playing_player",
}


def _plain(value: Any) -> Any:
    """Make decoded plist values JSON-safe (bytes, dates, UIDs...)."""
    if isinstance(value, bytes):
        return {"b64": base64.b64encode(value[:4096]).decode(), "len": len(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _decode_blobs(value: Any) -> Any:
    """Walk MessageToDict output; *Data bytes fields arrive base64-encoded:
    decode them as plists when possible, drop images."""
    if isinstance(value, dict):
        out = {}
        for key, v in value.items():
            if key in SKIP_FIELDS:
                out[key] = "<omitted>"
            elif key.endswith("Data") and isinstance(v, str):
                raw = base64.b64decode(v)
                try:
                    out[key] = {"plist": _plain(plistlib.loads(raw))}
                except Exception:
                    out[key] = {"b64": v[:4096], "len": len(raw)}
            else:
                out[key] = _decode_blobs(v)
        return out
    if isinstance(value, list):
        return [_decode_blobs(v) for v in value]
    return value


def _jsonable(message: Any) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    return _decode_blobs(MessageToDict(message, preserving_proto_field_name=True))


def _write(record: dict[str, Any]) -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_BYTES:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as err:
        print(f"mrp-raw: cannot write {LOG_FILE}: {err}", flush=True)


def _listener(name: str):
    async def handle(message) -> None:
        inner = protobuf.extract_inner(message)
        record = {"t": time.strftime("%H:%M:%S"), "msg": name, **_jsonable(inner)}
        _write(record)

    return handle


def install() -> None:
    """Attach the dump listeners to every MrpProtocol created from now on."""
    original = mrp_protocol.MrpProtocol.__init__
    if getattr(original, "_mrp_raw", False):
        return

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        for message_type, name in MESSAGES.items():
            self.listen_to(message_type, _listener(name))
        print("mrp-raw: logging now-playing messages to mrp_raw.log", flush=True)

    init._mrp_raw = True
    mrp_protocol.MrpProtocol.__init__ = init
