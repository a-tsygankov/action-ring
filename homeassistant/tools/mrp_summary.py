"""Summarize an MRP capture (raw mrp_raw.log or a sanitized fixture).

Prints one line per message: time, kind, player, playback state, whether
supported commands were (re)published, and each content item's clock. For the
app's default player ("show clock") it also classifies each update against the
previous one: ADVANCING (clock moved with real time), FROZEN (clock unchanged
for 10 s+), JUMP (moved further than real time), which is how Pluto TV ads
were found.

    python homeassistant/tools/mrp_summary.py capture.jsonl [--from 23:55:00] [--app tv.pluto.ios]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also under python -I
from sanitize_mrp import sanitize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--from", dest="start", default="")
    ap.add_argument("--app", default="")
    args = ap.parse_args()

    with open(args.capture, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    # Accept both raw logs and already-sanitized fixtures.
    recs = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        recs.append(r if "bundle" in r else next(iter(sanitize([line])), None))

    prev_clock = None
    for r in filter(None, recs):
        if (r.get("t") or "") < args.start or (args.app and r.get("bundle") != args.app):
            continue
        line = f'{r["t"]} {r["msg"]:<20} {r["player"]:<26} state={r.get("playbackState", "-"):<8}'
        if r.get("supportedCommands"):
            line += " +commands"
        for c in r.get("clocks", [])[:1]:
            el, ts = c.get("elapsedTime"), c.get("elapsedTimeTimestamp")
            line += f" | el={el} dur={c.get('duration')} rate={c.get('playbackRate')}"
            if r["player"] == "MediaRemote-DefaultPlayer" and r["msg"] == "update_content_item" \
                    and el is not None and ts is not None:
                if prev_clock:
                    d_el, d_ts = el - prev_clock[0], ts - prev_clock[1]
                    kind = ("ADVANCING" if abs(d_el - d_ts) < 5
                            else "FROZEN" if abs(d_el) < 1 and d_ts >= 10
                            else "JUMP")
                    line += f"  show clock {d_el:+.0f}s / {d_ts:.0f}s -> {kind}"
                prev_clock = (el, ts)
        print(line)


if __name__ == "__main__":
    main()
