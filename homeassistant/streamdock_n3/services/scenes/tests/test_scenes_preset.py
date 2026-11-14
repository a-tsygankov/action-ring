"""The scenes icons ship, and the preset puts the key on the streaming page."""

import json
import struct
from pathlib import Path

from services import scenes

ROOT = Path(__file__).resolve().parents[3]


def _png_size(path):
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_icons_exist_and_preset_places_the_key():
    icons = Path(scenes.__file__).parent / "icons"
    for n in ("scene.png", "scene_off.png"):
        assert _png_size(icons / n) == (112, 112)
    preset = json.loads((ROOT / "presets" / "tv_room.json").read_text())
    assert preset["tabs"]["pages"]["context:streaming"]["keys"]["4"] == {"service_key": "scenes.next"}
    assert preset["services"]["scenes"]["atv"].startswith("media_player.")
    # nudidex has no parser: asking it would only write "blocked" rows that count against the daily cap
    assert preset["services"]["scenes"]["sources"] == ["videoskip", "nsfwhen"]
