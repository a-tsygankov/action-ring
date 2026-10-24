"""Reduce an mrp_raw.log capture to a shareable test fixture.

Keeps only what ad analysis needs: time, message kind, app bundle, player
kind, playback state, whether supported commands were published, and the
clock fields of content items. Drops titles, content/profile identifiers,
device identifiers and app blobs, so the result is safe for a public repo.

    python homeassistant/tools/sanitize_mrp.py mrp_raw.log > fixture.jsonl
"""

from __future__ import annotations

import json
import sys

CLOCK_FIELDS = ("elapsedTime", "elapsedTimeTimestamp", "duration", "playbackRate")


def player_kind(identifier: str, seen: dict[str, str]) -> str:
    if identifier == "MediaRemote-DefaultPlayer":
        return identifier
    if identifier.startswith("avkit-"):
        return seen.setdefault(identifier, f"avkit-{len(seen) + 1}")
    return "other"


def sanitize(lines):
    seen: dict[str, str] = {}
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        pp = r.get("playerPath") if isinstance(r.get("playerPath"), dict) else {}
        out = {
            "t": r.get("t"),
            "msg": r.get("msg"),
            "bundle": pp.get("client", {}).get("bundleIdentifier"),
            "player": player_kind(pp.get("player", {}).get("identifier", ""), seen),
        }
        if "playbackState" in r:
            out["playbackState"] = r["playbackState"]
        if isinstance(r.get("supportedCommands"), dict):
            out["supportedCommands"] = True
        items = r.get("contentItems")
        if not isinstance(items, list):
            queue = r.get("playbackQueue")
            items = queue.get("contentItems") if isinstance(queue, dict) else None
        clocks = []
        for item in items or []:
            if isinstance(item, dict):
                md = item.get("metadata") or {}
                clocks.append({k: md[k] for k in CLOCK_FIELDS if k in md})
        if clocks:
            out["clocks"] = clocks
        yield out


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        for rec in sanitize(fh):
            print(json.dumps(rec))
