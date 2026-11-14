"""VideoSkip exchange: search, film page, skip pages, skip-file download."""

from __future__ import annotations

import json
import math
import re
from html import unescape
from urllib.parse import quote_plus

from ..fetch import Blocked, Fetcher
from ..match import Candidate, pick
from ..model import Film, Scene, SourceResult, parse_time

BASE = "https://videoskip.herokuapp.com"
SITE = "videoskip"
_MAX_SKIP_PAGES = 3

_H3 = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S | re.I)
_ROW_LINK = re.compile(r'<a\s+href="(/exchange/videos/\d+/)"[^>]*>(.*?)</a>', re.S | re.I)
_TITLE_YEAR = re.compile(r"^(.*?)\s*\((\d{4})\)\s*$", re.S)
_SKIP_REF = re.compile(r'href="(/exchange/skip/\d+/)"')
_TAGS = re.compile(r"<[^>]+>")
_BLOCK_CAT = re.compile(r"^(nudity|sex)\b", re.I)
_IMG = "data:image/jpeg;base64,"


def _text(fragment: str) -> str:
    return " ".join(unescape(_TAGS.sub(" ", fragment)).split())


def parse_search(html: str) -> list[Candidate]:
    """Films under "Videos with skip files" only."""
    start = None
    end = len(html)
    for m in _H3.finditer(html):
        heading = _text(m.group(1)).lower()
        if start is None:
            if heading.startswith("videos with skip files"):
                start = m.end()
        else:
            end = m.start()
            break
    if start is None:
        raise ValueError("videoskip search page has no skip-files section")
    out = []
    for m in _ROW_LINK.finditer(html, start, end):
        label = _text(m.group(2))
        ym = _TITLE_YEAR.match(label)
        title, year = (ym.group(1), int(ym.group(2))) if ym else (label, None)
        if title:
            out.append(Candidate(ref=m.group(1), title=title, year=year))
    return out


def parse_film(html: str) -> tuple[dict, list[str]]:
    """(meta {year, imdb_id}, skip refs). The page lists filters and downloads per skip file but
    not the sync service, so the skip pages are still read."""
    meta: dict = {}
    m = re.search(r"Released:\s*([^<]*?\b(\d{4}))\s*<", html)
    if m:
        meta["year"] = int(m.group(2))
    m = re.search(r"imdb\.com/title/(tt\d+)", html)
    if m:
        meta["imdb_id"] = m.group(1)
    refs: list[str] = []
    start = html.find("Skip Files:")
    if start < 0:
        raise ValueError("videoskip film page has no skip-files section")
    for m in _SKIP_REF.finditer(html, start):
        if m.group(1) not in refs:
            refs.append(m.group(1))
    return meta, refs


def parse_skip_page(html: str) -> dict:
    def field(label: str) -> str:
        m = re.search(r"<li>\s*" + label + r":(.*?)</li>", html, re.S | re.I)
        return _text(m.group(1)) if m else ""

    if not field("Downloaded") or not field("Filters"):
        raise ValueError("videoskip skip page has no details list")
    dm = re.search(r"\d+", field("Downloaded"))
    return {"synced_with": field("Synced with"), "filters": field("Filters"),
            "downloads": int(dm.group()) if dm else 0}


def parse_skip_file(text: str) -> tuple[list[Scene], dict]:
    """Sex and nudity blocks (times as in the file, offsets not applied) and the offsets dict."""
    head = text.split(_IMG, 1)[0]
    brace = head.find("{")
    offsets: dict = {}
    if brace >= 0:
        try:
            raw = json.loads(head[brace:head.rfind("}") + 1])
            if isinstance(raw, dict):
                offsets = {k: v for k, v in raw.items()
                           if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
        except ValueError:
            offsets = {}
        head = head[:brace]
    scenes = []
    for block in re.split(r"\n\s*\n", head.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2 or "-->" not in lines[0] or "//" in block:
            continue
        cat = _BLOCK_CAT.match(lines[1])
        if not cat:
            continue
        a, _, b = lines[0].partition("-->")
        try:
            start, end = parse_time(a), parse_time(b)
        except ValueError:
            continue
        if end <= start:
            continue
        scenes.append(Scene(start, end, cat.group(1).lower()))
    return scenes, offsets


def synced_with_service(synced_with: str, service: str | None) -> bool:
    """Loose match of a skip page's "Synced with" text against an offsets key like "disneyplus"."""
    if not service:
        return False
    norm = re.sub(r"[^a-z0-9]", "", synced_with.lower().replace("+", "plus"))
    return service.lower() in norm


def _wants(filters: str) -> bool:
    low = filters.lower()
    return "sex" in low or "nud" in low


def lookup(fetch: Fetcher, film: Film, cfg: dict) -> SourceResult:
    """cfg["service"]: the VideoSkip service key/name the film is played on, or None.
    `requests` counts this lookup's own requests (the Fetcher is shared by all sources)."""
    first = fetch.count
    try:
        return _lookup(fetch, film, cfg.get("service"), first)
    except Blocked:
        return SourceResult("blocked", requests=fetch.count - first)
    except Exception:
        return SourceResult("error", requests=fetch.count - first)


def _lookup(fetch: Fetcher, film: Film, service: str | None, first: int) -> SourceResult:
    def done(status: str, scenes=(), meta=None) -> SourceResult:
        return SourceResult(status, tuple(scenes), meta or {}, fetch.count - first)

    html = fetch.get(SITE, f"{BASE}/exchange/search/?q={quote_plus(film.title)}", op="videoskip_search")
    cand, status = pick(film, parse_search(html))
    if cand is None:
        return done(status)
    meta, refs = parse_film(fetch.get(SITE, BASE + cand.ref, op="videoskip_film"))
    refs = sorted(refs, key=lambda r: int(re.search(r"\d+", r).group()), reverse=True)[:_MAX_SKIP_PAGES]
    pages = []
    for ref in refs:
        info = parse_skip_page(fetch.get(SITE, BASE + ref, op="videoskip_skip"))
        if _wants(info["filters"]):
            pages.append((ref, info))
    if not pages:
        return done("miss", meta=meta)
    pages.sort(key=lambda p: (synced_with_service(p[1]["synced_with"], service),
                              p[1]["downloads"]), reverse=True)
    ref, info = pages[0]
    found, offsets = parse_skip_file(fetch.get(SITE, BASE + ref + "download/", op="videoskip_download"))
    if not found:
        return done("miss", meta=meta)
    shift = offsets[service] if service and service in offsets else 0
    scenes = [Scene(s.start_s + shift, s.end_s + shift, s.category, source=SITE,
                    synced_with=info["synced_with"]) for s in found]
    return done("hit", scenes, meta)
