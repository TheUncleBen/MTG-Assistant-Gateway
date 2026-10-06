"""The Android app download page: login required, APK served with its metadata, absent app."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

FAKE_APK = b"PK\x03\x04" + b"\0" * 4000
FAKE_CERT = hashlib.sha256(b"fake signing certificate").hexdigest()


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch):
    app_dir = tmp_path / "app-dist"
    app_dir.mkdir()
    (app_dir / "mtg-assistant-gateway.apk").write_bytes(FAKE_APK)
    (app_dir / "mtg-assistant-gateway.json").write_text(
        json.dumps(
            {
                "version_name": "0.1.0",
                "version_code": 1,
                "sha256": hashlib.sha256(FAKE_APK).hexdigest(),
                "cert_sha256": FAKE_CERT,
                "ignored": ["not", "shown"],
            }
        )
    )
    monkeypatch.setenv("MTG_APP_DIR", str(app_dir))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        yield h


async def test_app_pages_need_a_browser_login(harness: Harness) -> None:
    for path in ("/app", "/app/mtg-assistant-gateway.apk"):
        r = await harness.http.get(path)
        assert r.status_code == 302
        assert r.headers["location"] == "/login?next=/app"


async def test_signed_in_user_gets_page_and_apk(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/app")
        page = await b.http.get("/app")
        assert page.status_code == 200
        assert "/app/mtg-assistant-gateway.apk" in page.text
        assert "0.1.0" in page.text
        assert hashlib.sha256(FAKE_APK).hexdigest() in page.text
        assert "not" not in page.text.split("SHA-256")[1][:200]
        assert "default-src 'none'" in page.headers["content-security-policy"]
        # The signing certificate is shown, with a pointer to the independently published value,
        # and the page never tells people to click through Play Protect.
        assert "Signing certificate SHA-256" in page.text
        assert FAKE_CERT in page.text
        assert "https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases" in page.text
        assert "If they differ, do not install" in page.text
        assert "Install anyway" not in page.text
        assert "is harmful" in page.text

        r = await b.http.get("/app/mtg-assistant-gateway.apk")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/vnd.android.package-archive"
        assert 'filename="mtg-assistant-gateway.apk"' in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "no-store"
        assert r.content == FAKE_APK

        account = await b.http.get("/account")
        assert "href='/app'" in account.text
    finally:
        await b.aclose()


async def test_gateway_without_the_app(tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MTG_APP_DIR", str(tmp_path / "nowhere"))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        b = Browser(h)
        try:
            await b.login("/app")
            page = await b.http.get("/app")
            assert page.status_code == 404
            assert "does not ship the app" in page.text
            r = await b.http.get("/app/mtg-assistant-gateway.apk")
            assert r.status_code == 404
        finally:
            await b.aclose()


async def test_page_without_a_recorded_certificate(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_dir = tmp_path / "app-dist"
    app_dir.mkdir()
    (app_dir / "mtg-assistant-gateway.apk").write_bytes(FAKE_APK)
    # A malformed fingerprint is not shown as if it were one.
    (app_dir / "mtg-assistant-gateway.json").write_text(
        json.dumps({"version_name": "0.1.0", "cert_sha256": "<b>nope</b>"})
    )
    monkeypatch.setenv("MTG_APP_DIR", str(app_dir))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        b = Browser(h)
        try:
            await b.login("/app")
            page = await b.http.get("/app")
            assert page.status_code == 200
            assert "nope" not in page.text
            assert "Signing certificate SHA-256" not in page.text
            assert "did not record its signing certificate" in page.text
            assert "Install anyway" not in page.text
        finally:
            await b.aclose()
