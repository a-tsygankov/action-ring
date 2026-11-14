import re
import urllib.error
from pathlib import Path

import pytest

from services.scenes.fetch import UA, Blocked, Fetcher
from services.testing import fake_context

FIX = Path(__file__).parent / "fixtures"


def test_gap_between_requests_to_one_site():
    ctx = fake_context("scenes", {}, http={"a.test": "ok", "b.test": "ok"})
    f = Fetcher(ctx, gap_s=3)
    t0 = ctx.monotonic()
    f.get("a", "https://a.test/1", op="x"); f.get("a", "https://a.test/2", op="x")
    assert ctx.monotonic() - t0 == 3            # slept once
    f.get("b", "https://b.test/1", op="x")
    assert ctx.monotonic() - t0 == 3            # another site: no wait
    assert f.count == 3


def test_challenge_page_and_403_are_blocked():
    body = (FIX / "challenge.html").read_text(encoding="utf-8")
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    for reply in (body, err, urllib.error.HTTPError("u", 429, "Too Many", {}, None)):
        ctx = fake_context("scenes", {}, http={"a.test": reply})
        with pytest.raises(Blocked):
            Fetcher(ctx).get("a", "https://a.test/", op="x")


def test_other_errors_propagate():
    ctx = fake_context("scenes", {}, http={"a.test": TimeoutError()})
    with pytest.raises(TimeoutError):
        Fetcher(ctx).get("a", "https://a.test/", op="x")


def test_server_error_propagates_and_browser_headers_are_sent(monkeypatch):
    seen = {}

    def opener(method, url, body, headers, timeout):
        seen.update(headers)
        return 200, b"ok"
    ctx = fake_context("scenes", {}, http={"a.test": "ok"})
    monkeypatch.setattr(ctx.http, "_opener", opener)
    assert Fetcher(ctx).get("a", "https://a.test/", op="x") == "ok"
    assert seen["User-Agent"] == UA and "en" in seen["Accept-Language"]
    err = urllib.error.HTTPError("u", 500, "Boom", {}, None)
    ctx = fake_context("scenes", {}, http={"a.test": err})
    with pytest.raises(urllib.error.HTTPError):
        Fetcher(ctx).get("a", "https://a.test/", op="x")


def test_fixtures_hold_no_email_and_no_embedded_image():
    email = re.compile(r"[\w.+-]+@[\w-]+\.(?:com|net|org|io|app|me)\b")   # not CDN "pkg@1.2.3" or @media
    for p in FIX.iterdir():
        text = p.read_text(encoding="utf-8", errors="replace")
        assert not email.search(text), p.name
        assert '"submitter":{' not in text, p.name
        if p.suffix == ".skp":
            assert text.rstrip().endswith("data:image/jpeg;base64,AAAA"), p.name
