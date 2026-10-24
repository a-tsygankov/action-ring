"""Replay an MRP capture through the app's AdDetector (no Apple TV needed).

Feeds a raw mrp_raw.log or a sanitized fixture into ad_detect.AdDetector
with the capture's own timestamps, against a fake speaker (TV source,
unmuted), and prints every detector decision and mute call. With --expect it
checks the detected ad windows.

    python homeassistant/tools/replay_ad_detect.py homeassistant/tools/fixtures/pluto_ads_20261023.jsonl \
        --expect 23:57:19-23:59:51 00:09:21-00:11:52
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))  # sanitize_mrp (also under python -I)
sys.path.insert(0, str(_HERE.parent / "streamdock_n3"))  # the app's ad_detect
from sanitize_mrp import sanitize  # noqa: E402
import ad_detect  # noqa: E402

STATES = {"Playing": 1, "Paused": 2, "Stopped": 3, "Interrupted": 4, "Seeking": 5}


def secs(t: str) -> int:
    h, m, s = map(int, t.split(":"))
    return h * 3600 + m * 60 + s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--expect", nargs="*", default=None, help="ad windows HH:MM:SS-HH:MM:SS")
    ap.add_argument("--app", action="append", help="app bundle(s) to watch (default: detector's)")
    args = ap.parse_args()

    out: list[tuple[str, str]] = []
    now_t = {"t": ""}
    ad_detect.log = lambda m: out.append((now_t["t"], m))
    speaker = {"s": "playing", "a": {"source": "TV", "is_volume_muted": False}}

    def call(domain, service, data):
        speaker["a"]["is_volume_muted"] = data["is_volume_muted"]
        out.append((now_t["t"], f"CALL {service} is_volume_muted={data['is_volume_muted']}"))

    cfg = {"speaker": "media_player.speaker"}
    if args.app:
        cfg["apps"] = args.app
    det = ad_detect.AdDetector(cfg, call, lambda e: speaker)

    with open(args.capture, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        r = r if "bundle" in r else next(iter(sanitize([line])), None)
        if not r:
            continue
        now_t["t"], now = r["t"], secs(r["t"])
        state = r.get("playbackState")
        state = STATES.get(state, state) if isinstance(state, str) else state
        if r["msg"] == "set_state" and r["player"].startswith("avkit-"):
            det.on_stream_state(r["bundle"], state, bool(r.get("supportedCommands")), now=now)
        elif r["msg"] == "update_content_item" and r["player"] == "MediaRemote-DefaultPlayer":
            for c in r.get("clocks", []):
                if "elapsedTime" in c and "elapsedTimeTimestamp" in c:
                    det.on_show_clock(r["bundle"], c["elapsedTime"], c["elapsedTimeTimestamp"], now=now)
        det.tick(now=now)
        time.sleep(0.01)  # mute calls run on threads; keep their order
    time.sleep(0.2)

    for t, m in out:
        print(f"{t}  {m}")
    if args.expect is None:
        return 0
    starts = [t for t, m in out if m.startswith("ad started")]
    ends = [t for t, m in out if m.startswith("ad ended")]
    got = [f"{s}-{e}" for s, e in zip(starts, ends)]
    ok = got == args.expect
    print(("OK" if ok else "MISMATCH") + f": detected {got}, expected {args.expect}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
