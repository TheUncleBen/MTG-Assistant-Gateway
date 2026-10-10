"""The newer-release notice for admins (update_check.py): GitHub's answer is read strictly, only a
newer MAJOR.MINOR.PATCH counts, admins see it on every page, members never do, and the off switch
sends nothing."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from mtg_gateway import __version__, update_check
from mtg_gateway.config import load_settings

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import NAVIGATE, _env, _with_admin, admin_browser
from .test_decks_and_proxy import Browser


def _bump(v: str) -> str:
    major, minor, patch = (int(x) for x in v.split("."))
    return f"{major}.{minor}.{patch + 1}"


NEWER = _bump(__version__)


@pytest.fixture(autouse=True)
def fresh_state():
    saved = dict(update_check.last)
    yield
    update_check.last.clear()
    update_check.last.update(saved)


def _github(answer: dict | None, status: int = 200, seen: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=answer or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _release(tag: str, **extra) -> dict:
    return {
        "tag_name": tag,
        "html_url": f"{update_check.RELEASES_PAGE}/tag/{tag}",
        "draft": False,
        "prerelease": False,
        **extra,
    }


def test_versions_parse_strictly() -> None:
    assert update_check.parse_version("v0.7.12") == (0, 7, 12)
    assert update_check.parse_version("1.0.0") == (1, 0, 0)
    for bad in ("v0.7", "0.7.12-rc1", "latest", "v01.2.3", "", None, 7):
        assert update_check.parse_version(bad) is None


async def test_a_newer_release_is_found_with_an_anonymous_request() -> None:
    seen: list[httpx.Request] = []
    checker = update_check.UpdateChecker(True, http=_github(_release(f"v{NEWER}"), seen=seen))
    await checker.check_once()
    assert update_check.newer() == {"version": NEWER, "url": f"{update_check.RELEASES_PAGE}/tag/v{NEWER}"}
    (req,) = seen
    assert str(req.url) == update_check.LATEST_API
    assert "authorization" not in req.headers and "cookie" not in req.headers
    assert req.headers["user-agent"].startswith(f"mtg-assistant-gateway/{__version__}")
    assert update_check.status()["update_available"] is True


@pytest.mark.parametrize(
    "answer",
    [
        _release(f"v{__version__}"),  # the same version
        _release("v0.0.1"),  # older
        _release(f"v{NEWER}", prerelease=True),
        _release(f"v{NEWER}", draft=True),
        _release("nightly"),
    ],
)
async def test_only_a_newer_plain_release_counts(answer: dict) -> None:
    checker = update_check.UpdateChecker(True, http=_github(answer))
    try:
        await checker.check_once()
    except ValueError:
        pass
    assert update_check.newer() is None


async def test_a_link_elsewhere_is_replaced_by_the_project_releases_page() -> None:
    answer = _release(f"v{NEWER}", html_url="https://evil.example/download")
    await update_check.UpdateChecker(True, http=_github(answer)).check_once()
    assert update_check.newer() == {"version": NEWER, "url": update_check.RELEASES_PAGE}


async def test_a_failed_lookup_is_reported_and_retried(monkeypatch) -> None:
    checker = update_check.UpdateChecker(True, http=_github({"message": "rate limited"}, status=403))
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)
        if len(delays) > 2:
            raise RuntimeError("stop")

    monkeypatch.setattr(update_check.asyncio, "sleep", fake_sleep)
    with pytest.raises(RuntimeError):
        await checker.loop()
    assert delays == [update_check.FIRST_DELAY, update_check.RETRY, update_check.RETRY]
    assert update_check.last["error"] == "GitHub answered HTTP 403"
    assert update_check.newer() is None


def test_the_off_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(tmp_path, monkeypatch)
    assert load_settings().update_check is True
    monkeypatch.setenv("MTG_UPDATE_CHECK", "false")
    assert load_settings().update_check is False
    update_check.UpdateChecker(False)
    update_check.last.update(latest=NEWER)
    assert update_check.newer() is None and update_check.status() == {"running": __version__, "check": "off"}


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        yield h


async def test_admins_see_the_notice_and_the_steps_members_do_not(gw: Harness) -> None:
    update_check.UpdateChecker(True)
    page = update_check.RELEASES_PAGE
    update_check.last.update(at=1_800_000_000, ok=True, latest=NEWER, url=page, error=None)
    b = await admin_browser(gw)
    try:
        r = await b.http.get("/decks", headers=NAVIGATE)
        assert f"Gateway {NEWER} is available" in r.text and "/admin#updates" in r.text
        r = await b.http.get("/admin", headers=NAVIGATE)
        assert "id='updates'" in r.text and f"Version {NEWER} is available" in r.text
        assert f"MTG_TAG={NEWER}" in r.text and "docker compose pull" in r.text and "Re-pull" in r.text
        assert f"Gateway {NEWER} is available" not in r.text  # the card, not the banner as well
        o = (await b.http.get("/api/v1/admin/overview")).json()
        assert o["system"]["updates"]["latest"] == NEWER and o["system"]["updates"]["update_available"]
    finally:
        await b.http.aclose()

    gw.idp.user = {"sub": "user-2", "email": "u2@example.test", "name": "u2", "groups": ["mtg-users"]}
    m = Browser(gw)
    await m.login("/decks")
    try:
        r = await m.http.get("/decks", headers=NAVIGATE)
        assert r.status_code == 200 and "is available" not in r.text and NEWER not in r.text
    finally:
        await m.http.aclose()
    json.dumps(update_check.status())  # the API answer stays JSON
