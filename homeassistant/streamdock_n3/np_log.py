"""Log now-playing changes of media players, to find ad markers per app.

Each HA state change of a watched media player that alters a tracked field
(state, app, title, series/episode, content type/id, duration) produces one
line naming only the changed fields. A position that doesn't match
continuous playback since the last report is logged as pos_jump. Lines go to
the app log ("np: ...") and to /config/now_playing.log (capped; the previous
file is kept as now_playing.log.1).
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

FIELDS = (
    "app_id",
    "app_name",
    "media_title",
    "media_artist",
    "media_album_name",
    "media_series_title",
    "media_season",
    "media_episode",
    "media_content_type",
    "media_content_id",
    "media_duration",
    # Apps may disable seek/pause during ads (Tubi reports "paused" for a
    # whole ad break), which shows up as a supported_features change.
    "supported_features",
)
LOG_FILE = Path("/config/now_playing.log")
MAX_BYTES = 2_000_000
JUMP_SECONDS = 3.0


def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


class NowPlayingLog:
    def __init__(self, entities: list[str], log_file: Path = LOG_FILE) -> None:
        self.entities = set(entities)
        self.log_file = log_file
        self._last: dict[str, dict[str, Any]] = {}

    def update(self, entity_id: str, st: dict[str, Any] | None) -> str | None:
        if entity_id not in self.entities:
            return None
        attrs = (st or {}).get("a") or {}
        cur = {"state": (st or {}).get("s"), **{k: attrs.get(k) for k in FIELDS}}
        pos = attrs.get("media_position")
        upd = _parse_ts(attrs.get("media_position_updated_at"))
        prev = self._last.get(entity_id)

        if prev is None:  # first report: only the fields that are set
            changes = {k: v for k, v in cur.items() if v is not None}
        else:
            changes = {k: v for k, v in cur.items() if prev["fields"].get(k) != v}
        jump = None
        if pos is not None and prev and prev["pos"] is not None and upd and prev["upd"]:
            if upd != prev["upd"]:
                elapsed = (upd - prev["upd"]).total_seconds()
                expected = prev["pos"] + (elapsed if prev["fields"]["state"] == "playing" else 0)
                if abs(float(pos) - expected) > JUMP_SECONDS:
                    jump = f"{expected:.0f}->{float(pos):.0f}"
        self._last[entity_id] = {
            "fields": cur,
            "pos": float(pos) if pos is not None else (prev or {}).get("pos"),
            "upd": upd or (prev or {}).get("upd"),
        }
        if not changes and not jump:
            return None

        parts = [f"{k}={v!r}" for k, v in changes.items()]
        if jump:
            parts.append(f"pos_jump={jump}")
        if pos is not None:
            parts.append(f"pos={float(pos):.0f}")
        if upd is not None:
            parts.append(f"pos_at={upd.astimezone():%H:%M:%S}")  # local, like the line time
        line = f"{entity_id.split('.', 1)[-1]} " + " ".join(parts)
        self._write(line)
        return line

    def _write(self, line: str) -> None:
        print(f"np: {line}", flush=True)
        try:
            if self.log_file.exists() and self.log_file.stat().st_size > MAX_BYTES:
                self.log_file.replace(self.log_file.with_suffix(".log.1"))
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        except OSError as err:
            print(f"np: cannot write {self.log_file}: {err}", flush=True)
