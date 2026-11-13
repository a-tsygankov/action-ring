"""Like / dislike the playing YouTube video (YouTube Data API v3).

The Lounge link (yt_lounge.py) knows which video is playing but has no
rating command, so ratings go to the Data API as the signed-in account:
videos.getRating for the key's lit state, videos.rate to change it.

Sign-in is Google's device flow, driven by files in the app's config share
(/config here, /addon_configs/local_streamdock_n3 outside):

- yt_oauth_client.json: the OAuth client ("TVs and Limited Input devices")
  as downloaded from the Google Cloud console, or {"client_id", "client_secret"}.
- yt_rate_auth: create it (empty) to sign in. It is consumed at once; the log
  then says which URL to open and which code to enter. On success the
  refresh token goes to yt_oauth.json (secret; not in the repo).

Ratings toggle: "like" on a liked video clears the rating (as the YouTube
app does), likewise "dislike". The key shows the new rating at once and
falls back if the API refuses it.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

CONFIG_DIR = Path("/config")
SCOPE = "https://www.googleapis.com/auth/youtube"
DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3/videos"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
RETRY_S = 60.0  # after a failed getRating, before asking again for that video
CACHE_SIZE = 50
RATINGS = ("like", "dislike", "none")

# (method, url, form or None, headers) -> (HTTP status, parsed JSON body or {})
Http = Callable[[str, str, "dict | None", dict], "tuple[int, dict]"]


def log(msg: str) -> None:
    print(f"youtube-rating: {msg}", flush=True)


def urllib_http(method: str, url: str, form: dict | None, headers: dict) -> tuple[int, dict]:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    if method == "POST" and data is None:
        data = b""
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status, body = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, body = err.code, err.read()
    try:
        return status, json.loads(body) if body else {}
    except ValueError:
        return status, {"error": body[:200].decode(errors="replace")}


def _client(path: Path) -> tuple[str, str] | None:
    """(client_id, client_secret) from the console download or a flat file."""
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    for part in [doc, *(v for v in doc.values() if isinstance(v, dict))]:
        if part.get("client_id") and part.get("client_secret"):
            return part["client_id"], part["client_secret"]
    raise ValueError(f"{path.name}: no client_id / client_secret")


class YouTubeRating:
    def __init__(
        self,
        video_id: Callable[[], "str | None"],
        on_change: Callable[[], None] | None = None,
        http: Http = urllib_http,
        config_dir: Path = CONFIG_DIR,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._video_id = video_id
        self._on_change = on_change
        self._http = http
        self._clock = clock
        self._sleep = sleep
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self.client_file = config_dir / "yt_oauth_client.json"
        self.token_file = config_dir / "yt_oauth.json"
        self.auth_trigger = config_dir / "yt_rate_auth"
        self._lock = threading.Lock()
        self._token_lock = threading.Lock()  # one refresh at a time
        self._ratings: dict[str, str] = {}  # video id -> like | dislike | none
        self._fetching: set[str] = set()
        self._failed: dict[str, float] = {}  # video id -> when getRating last failed
        self._access: tuple[str, float] | None = None  # (token, expires at)
        self._warned = False
        tokens = json.loads(self.token_file.read_text()) if self.token_file.exists() else {}
        self._refresh_token: str | None = tokens.get("refresh_token")

    # -- queries (render thread) ----------------------------------------------

    def rating(self) -> str | None:
        """like | dislike | none for the playing video; None while unknown.

        An unknown rating is fetched in the background (on_change follows).
        """
        vid = self._video_id()
        if not vid or not self._refresh_token:
            return None
        with self._lock:
            if vid in self._ratings:
                return self._ratings[vid]
            failed = self._failed.get(vid)
            if vid in self._fetching or (failed is not None and self._clock() - failed < RETRY_S):
                return None
            self._fetching.add(vid)
        self._spawn(lambda: self._fetch(vid))
        with self._lock:
            return self._ratings.get(vid)  # usually still None: the fetch runs on

    def liked(self) -> bool:
        return self.rating() == "like"

    def disliked(self) -> bool:
        return self.rating() == "dislike"

    # -- commands (a worker thread) -------------------------------------------

    def rate(self, kind: str) -> None:
        """"like" / "dislike" the playing video, or clear that rating if it has it."""
        if kind not in ("like", "dislike"):
            raise ValueError(f"unknown rating {kind!r}")
        vid = self._video_id()
        if not vid:
            log(f"{kind}: no linked YouTube video")
            return
        if not self._ready_to_call(kind):
            return
        with self._lock:
            before = self._ratings.get(vid)
            new = "none" if before == kind else kind
            self._ratings[vid] = new  # optimistic: the key changes at once
        self._notify()
        status, body = self._api("POST", f"{API}/rate?" + urllib.parse.urlencode({"id": vid, "rating": new}))
        if status in (200, 204):
            log(f"{vid}: {new}")
            return
        with self._lock:
            if before is None:
                self._ratings.pop(vid, None)
            else:
                self._ratings[vid] = before
        log(f"{vid}: rating {new} failed: HTTP {status} {_reason(body)}")
        self._notify()

    # -- sign-in (its own thread) ---------------------------------------------

    def run_forever(self) -> None:
        """Watch for the sign-in trigger file."""
        while True:
            try:
                if self.auth_trigger.exists():
                    self.auth_trigger.unlink(missing_ok=True)
                    self.sign_in()
            except Exception as err:
                log(f"sign-in: {type(err).__name__}: {err}")
            self._sleep(2)

    def sign_in(self) -> bool:
        client = _client(self.client_file)
        if client is None:
            log(f"sign-in: put the OAuth client in {self.client_file.name} first")
            return False
        client_id, secret = client
        status, dev = self._http("POST", DEVICE_URL, {"client_id": client_id, "scope": SCOPE}, {})
        if status != 200:
            log(f"sign-in: device code refused: HTTP {status} {_reason(dev)}")
            return False
        url = dev.get("verification_url") or dev.get("verification_uri")
        log(f"sign-in: open {url} and enter {dev['user_code']} "
            f"(within {int(dev.get('expires_in', 1800)) // 60} min)")
        interval = float(dev.get("interval", 5))
        deadline = self._clock() + float(dev.get("expires_in", 1800))
        form = {"client_id": client_id, "client_secret": secret,
                "device_code": dev["device_code"], "grant_type": DEVICE_GRANT}
        while self._clock() < deadline:
            self._sleep(interval)
            status, tok = self._http("POST", TOKEN_URL, form, {})
            if status == 200 and tok.get("refresh_token"):
                self._save(tok)
                log("sign-in: done; ratings are on")
                self._notify()
                return True
            error = tok.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            log(f"sign-in: failed: HTTP {status} {_reason(tok)}")
            return False
        log("sign-in: the code expired; create yt_rate_auth again")
        return False

    # -- internals --------------------------------------------------------------

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()

    def _save(self, tok: dict) -> None:
        self._refresh_token = tok["refresh_token"]
        self._access = (tok["access_token"], self._clock() + float(tok.get("expires_in", 3600)) - 60)
        self.token_file.write_text(json.dumps({"refresh_token": self._refresh_token}))
        os.chmod(self.token_file, 0o600)
        with self._lock:
            self._ratings.clear()
            self._failed.clear()

    def _ready_to_call(self, what: str) -> bool:
        if self._refresh_token:
            return True
        if not self._warned:
            self._warned = True
            log(f"{what}: not signed in (create {self.auth_trigger.name} in the app's config folder)")
        return False

    def _access_token(self, force: bool = False) -> str | None:
        with self._token_lock:
            if not force and self._access and self._clock() < self._access[1]:
                return self._access[0]
            client = _client(self.client_file)
            if client is None or not self._refresh_token:
                return None
            status, tok = self._http("POST", TOKEN_URL, {
                "client_id": client[0], "client_secret": client[1],
                "refresh_token": self._refresh_token, "grant_type": "refresh_token"}, {})
            if status == 200 and tok.get("access_token"):
                self._access = (tok["access_token"], self._clock() + float(tok.get("expires_in", 3600)) - 60)
                return self._access[0]
            if tok.get("error") == "invalid_grant":  # revoked or expired: sign in again
                log("the sign-in was revoked or expired; create yt_rate_auth to sign in again")
                self._refresh_token = None
                self.token_file.unlink(missing_ok=True)
            else:
                log(f"token refresh failed: HTTP {status} {_reason(tok)}")
            self._access = None
            return None

    def _api(self, method: str, url: str) -> tuple[int, dict]:
        for force in (False, True):
            token = self._access_token(force)
            if token is None:
                return 401, {"error": "not signed in"}
            status, body = self._http(method, url, None, {"Authorization": f"Bearer {token}"})
            if status != 401:
                return status, body
        return status, body

    def _fetch(self, vid: str) -> None:
        try:
            status, body = self._api("GET", f"{API}/getRating?" + urllib.parse.urlencode({"id": vid}))
            items = body.get("items") or []
            rating = items[0].get("rating") if status == 200 and items else None
            with self._lock:
                if rating in RATINGS or rating == "unspecified":
                    self._ratings[vid] = "none" if rating == "unspecified" else rating
                    while len(self._ratings) > CACHE_SIZE:
                        self._ratings.pop(next(iter(self._ratings)))
                else:
                    self._failed[vid] = self._clock()
            if rating is None:
                log(f"{vid}: getRating failed: HTTP {status} {_reason(body)}")
            else:
                self._notify()
        except Exception as err:
            with self._lock:
                self._failed[vid] = self._clock()
            log(f"{vid}: getRating: {type(err).__name__}: {err}")
        finally:
            with self._lock:
                self._fetching.discard(vid)


def _reason(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    err = body.get("error")
    if isinstance(err, dict):  # Data API: {"error": {"code", "message", "errors": [...]}}
        return str(err.get("message") or err)[:200]
    return str(body.get("error_description") or err or "")[:200]
