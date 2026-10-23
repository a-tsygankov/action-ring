"""Teach the installed streamdock-n3 package about the ActionRing N3's USB ID.

The unit on hand enumerates as 5548:1001 ("HOTSPOTEKUSB HID DEMO"), which the
upstream N3 table (6603:1003 etc.) does not list. Fails the image build loudly
if upstream changes shape, rather than shipping a daemon that finds no device.
"""

from pathlib import Path

import streamdock_n3

VID, PID = "5548", "1001"
pkg = Path(streamdock_n3.__file__).parent


def patch(rel: str, old: str, new: str) -> None:
    path = pkg / rel
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"patch_ids: expected text not found in {rel}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


patch(
    "_vendor/StreamDock/ProductIDs.py",
    "    # N3\n",
    f"    # N3\n    (0x{VID}, 0x{PID}, StreamDockN3),  # ActionRing N3\n",
)
for module in ("daemon.py", "debug_tool.py"):
    patch(module, 'VID = "6603"\nPID = "1003"', f'VID = "{VID}"\nPID = "{PID}"')

print(f"patch_ids: added {VID}:{PID} as StreamDockN3")
