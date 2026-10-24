"""Which context layout applies, from the LG TV and Apple TV foreground app.

Pure function of a state lookup and the `tabs.context` config section, so it is
testable without the device or Home Assistant (spec §5.1).
"""

from __future__ import annotations

from keyspec import StateLookup, variant


def _attrs(state_of: StateLookup, entity: str | None) -> dict:
    return ((state_of(entity) or {}).get("a") or {}) if entity else {}


def foreground_layout(state_of: StateLookup, cfg: dict, watching_game: bool = False) -> str:
    """One of idle | streaming | youtube | lg_media | lg_app | live_game."""
    lg = state_of(cfg.get("lg") or "")
    if variant((lg or {}).get("s")) != "on":
        return "idle"  # off / standby / unavailable / unknown / missing
    lg_attrs = (lg or {}).get("a") or {}
    source = lg_attrs.get("source")
    yt = cfg.get("youtube_apps") or {}

    if source and source == cfg.get("atv_source"):
        app = _attrs(state_of, cfg.get("atv")).get("app_id")
        if not app:
            return "idle"  # Apple TV on its home screen / asleep
        if app in (yt.get("atv") or []):
            return "youtube"
        return "live_game" if watching_game else "streaming"

    app = lg_attrs.get("app_id")
    if source == "YouTube" or (app and app in (yt.get("lg") or [])):
        return "youtube"
    if app and app in (cfg.get("lg_media_apps") or []):
        return "lg_media"
    if not app or any(app.startswith(p) for p in cfg.get("lg_idle_app_prefixes") or []):
        return "idle"
    return "lg_app"
