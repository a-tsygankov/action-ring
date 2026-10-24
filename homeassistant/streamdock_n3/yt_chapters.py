"""Chapter start times for a YouTube video, without an API key.

Reads the public watch page. Prefers YouTube's own chapter markers
("chapterRenderer": creator chapters and auto-generated ones), and falls back
to timestamps in the description using YouTube's chapter rules (starts at
0:00, at least 3 ascending timestamps).
"""

from __future__ import annotations

import json
import re
import urllib.request

_CHAPTER = re.compile(
    r'"chapterRenderer":\{"title":\{"simpleText":"((?:[^"\\]|\\.)*)"\},'
    r'"timeRangeStartMillis":(\d+)'
)
_DESCRIPTION = re.compile(r'"shortDescription":"((?:[^"\\]|\\.)*)"')
_TIMESTAMP = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.8",
    "Cookie": "SOCS=CAI; CONSENT=YES+1",  # skip the EU consent interstitial
}


def fetch_page(video_id: str, timeout: float = 10) -> str:
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={video_id}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_chapters(page: str) -> list[tuple[float, str]]:
    """Sorted (start_seconds, title) list; empty if the video has no chapters."""
    marks = {int(ms) / 1000: json.loads(f'"{title}"') for title, ms in _CHAPTER.findall(page)}
    if len(marks) >= 2:
        return sorted(marks.items())
    m = _DESCRIPTION.search(page)
    if not m:
        return []
    description = json.loads(f'"{m.group(1)}"')
    found: list[tuple[float, str]] = []
    for line in description.splitlines():
        t = _TIMESTAMP.search(line)
        if t:
            h, mnt, s = (int(x) if x else 0 for x in t.groups())
            title = (line[: t.start()] + line[t.end():]).strip(" -–—|:·\t")
            found.append((h * 3600 + mnt * 60 + s, title))
    starts = [t for t, _ in found]
    if len(found) >= 3 and starts[0] == 0 and starts == sorted(set(starts)):
        return found
    return []


def chapters(video_id: str) -> list[tuple[float, str]]:
    return parse_chapters(fetch_page(video_id))


def next_start(chs: list[tuple[float, str]], position: float, margin: float = 1.0):
    """First chapter starting after position (+margin), or None."""
    return next(((t, title) for t, title in chs if t > position + margin), None)


if __name__ == "__main__":
    import sys

    for start, title in chapters(sys.argv[1]):
        print(f"{int(start // 60)}:{int(start % 60):02d}  {title}")
