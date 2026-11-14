"""Live soccer on LG Channels (the LG's free channels): guide parsing and tuning keys.

The guide is LG's public schedule, `GUIDE` with the `x-device-country` /
`x-device-language` headers (no key). The body is base64 of zlib-compressed
JSON: categories[].channels[] (channelNumber, channelName) with programs[]
(programTitle, description, startDateTime / endDateTime in UTC), about 6 h
ahead. Only the configured channels count, and only programmes marked live in
their title ("Live:", "(Live)", "En vivo"): replays and highlights are left
out, and so are women's matches and other sports (soccer words in the channel
name, title or description).

Tuning has no deep link that works (the app's contentTarget wants an id the
guide does not give): the channel number is typed with remote keys on the
app's home page, right after a fresh launch, then ENTER. Over full-screen
playback the first digit is swallowed (it only wakes the player), so the app is
closed and relaunched first. Checked with TV screen captures
(`tv/executeOneShot`). Research: docs/JOURNAL.md, 2026-11-14.

Everything here is pure.
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from datetime import datetime

from .listing import Game, is_womens

GUIDE = "https://api.lgchannels.com/api/v1.0/schedulelist"
APP = "com.webos.app.lgchannels"
PROVIDER = "lgchannels"
CLOSE_S = 3  # after closing LG Channels, before launching it again
FIRST_DIGIT_S = 1.5  # the number box opens on the first digit; the rest wait for it
DIGIT_S = 0.4  # between the other digits

# A soccer channel's name, or soccer words in a programme (the channels also air boxing, basketball...).
_SOCCER = re.compile(r"soccer|football|f[uú]tbol|fu(ß|ss)ball|\bliga\b|\buefa\b|\bfifa\b|\bmls\b", re.IGNORECASE)
_LIVE = re.compile(r"\blive\b|\ben vivo\b|\ben directo\b", re.IGNORECASE)
# "U19 EURO Qualifier 2026/27 Live: Portugal - Germany" -> league, match
_SPLIT = re.compile(r"^(?P<league>.*?)\s*\blive\s*:\s*(?P<match>.+)$", re.IGNORECASE)


def headers(country: str = "US", language: str = "en") -> dict[str, str]:
    return {"x-device-country": country, "x-device-language": language}


def decode(body: str) -> dict:
    """The guide's JSON: base64 of zlib, or plain JSON (as LG may serve it)."""
    text = body.strip()
    if text.startswith("{"):
        return json.loads(text)
    return json.loads(zlib.decompress(base64.b64decode(text)))


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _channels(payload: dict, numbers: list[str]) -> list[dict]:
    """The configured channels in the guide, each once, in config order."""
    wanted = [str(n) for n in numbers]
    found: dict[str, dict] = {}
    for category in payload.get("categories") or []:
        for channel in (category or {}).get("channels") or []:
            if not isinstance(channel, dict):
                continue
            number = str(channel.get("channelNumber") or "")
            if number in wanted and number not in found:
                found[number] = channel
    return [found[n] for n in wanted if n in found]


def _live_programs(payload: dict, numbers: list[str]):
    """(channel, program, start, end) for every live-marked men's programme on the channels."""
    for channel in _channels(payload, numbers):
        for program in channel.get("programs") or []:
            if not isinstance(program, dict):
                continue
            title = str(program.get("programTitle") or "")
            start, end = _epoch(program.get("startDateTime")), _epoch(program.get("endDateTime"))
            if start is None or end is None or not _LIVE.search(title):
                continue
            description = str(program.get("description") or "")
            if is_womens(title, description):
                continue
            if not _SOCCER.search(" ".join((str(channel.get("channelName") or ""), title, description))):
                continue
            yield channel, program, start, end


def _game(channel: dict, program: dict, start: float) -> Game:
    title = str(program.get("programTitle") or "")
    split = _SPLIT.match(title)
    league, match = (split["league"], split["match"]) if split else (str(channel.get("channelName") or ""), title)
    number = str(channel.get("channelNumber") or "")
    return Game(
        id=str(program.get("programId") or f"lg:{number}:{int(start)}"),
        title=match.strip(),
        league=league.strip(" -:") or str(channel.get("channelName") or ""),
        kickoff=start,
        provider=PROVIDER,
        url="",
        app=APP,
        channel=number,
    )


def parse_games(payload: dict, now: float, numbers: list[str]) -> list[Game]:
    """Live men's soccer on the given LG Channels numbers at `now` (epoch s)."""
    return [_game(c, p, start) for c, p, start, end in _live_programs(payload, numbers) if start <= now < end]


def next_start(payload: dict, now: float, numbers: list[str]) -> float | None:
    """Epoch seconds of the next live-marked programme's start, or None."""
    return min((start for _c, _p, start, _e in _live_programs(payload, numbers) if start > now), default=None)


def tune_keys(channel: str) -> list[tuple[str, float]]:
    """The remote keys that tune `channel`, each with the wait after it."""
    digits = [d for d in channel if d.isdigit()]
    if not digits:
        return []
    keys = [(digits[0], FIRST_DIGIT_S)] + [(d, DIGIT_S) for d in digits[1:]]
    return keys + [("ENTER", 0.0)]
