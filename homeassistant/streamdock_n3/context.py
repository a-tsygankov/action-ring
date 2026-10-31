"""Which context layout applies, from the LG TV and Apple TV foreground app.

Pure functions of a state lookup and the `tabs.context` config section, so it
is testable without the device or Home Assistant (spec §5.1).

The Apple TV reports its *now-playing* app, not the one on screen: after
leaving a paused video its app stays reported while the user browses the Home
screen or another app, and the TV app reports trailers and previews. AtvApp
filters that down to an app that is really showing content (see below).
"""

from __future__ import annotations

from keyspec import StateLookup, variant

_RAW = object()  # foreground_layout: read the Apple TV app from its entity


class AtvApp:
    """The Apple TV app that counts for the context layout, or None.

    An app counts once it has played for `min_play_s` without a break since it
    became the reported app (menu previews play for a few seconds); a pause
    keeps it. One reported while not playing (the Home screen, an app's menus,
    a paused app the Apple TV falls back to) does not count. Apps in
    `content_apps` (the TV app) count only for real content
    (media_content_type "video", not trailers or previews), each title anew.
    Stateful: call update() on every state change, and while `pending` also
    without one (the play time can run out between changes); callers serialize.
    """

    def __init__(self, cfg: dict) -> None:
        self.entity = cfg.get("atv")
        self.content_apps = set(cfg.get("content_apps") or [])
        self.min_play_s = float(cfg.get("min_play_s", 0))
        self._key: tuple | None = None  # what counts: (app,) or (app, content id)
        self._playing: tuple[tuple, float] | None = None  # (key, since): playing, not counted yet

    @property
    def pending(self) -> bool:
        """Something is playing that will count once its play time is reached."""
        return self._playing is not None

    def update(self, state_of: StateLookup, now: float = 0.0) -> str | None:
        st = (state_of(self.entity) if self.entity else None) or {}
        attrs = st.get("a") or {}
        app = attrs.get("app_id")
        if not app or (self._key is not None and self._key[0] != app):
            self._key = None  # another app took over: it must play again to count
        if not app:
            self._playing = None
            return None
        content = app in self.content_apps
        if content and attrs.get("media_content_type") != "video":
            self._playing = None
            return None  # a trailer or preview; a title that counts stays remembered
        key = (app, attrs.get("media_content_id")) if content else (app,)
        if st.get("s") == "playing" and key != self._key:
            if self._playing is None or self._playing[0] != key:
                self._playing = (key, now)
            if now - self._playing[1] >= self.min_play_s:
                self._key, self._playing = key, None
        else:
            self._playing = None  # paused or stopped before the play time: start over
        return app if key == self._key else None


def _attrs(state_of: StateLookup, entity: str | None) -> dict:
    return ((state_of(entity) or {}).get("a") or {}) if entity else {}


def foreground_layout(state_of: StateLookup, cfg: dict, watching_game: bool = False,
                      atv_app: str | None | object = _RAW) -> str:
    """One of idle | streaming | youtube | lg_media | lg_app | live_game.

    atv_app: the Apple TV app that counts (AtvApp.update); by default the
    entity's raw app_id.
    """
    lg = state_of(cfg.get("lg") or "")
    if variant((lg or {}).get("s")) != "on":
        return "idle"  # off / standby / unavailable / unknown / missing
    lg_attrs = (lg or {}).get("a") or {}
    source = lg_attrs.get("source")
    yt = cfg.get("youtube_apps") or {}

    if source and source == cfg.get("atv_source"):
        app = _attrs(state_of, cfg.get("atv")).get("app_id") if atv_app is _RAW else atv_app
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
