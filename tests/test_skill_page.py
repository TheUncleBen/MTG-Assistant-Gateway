"""The skill download page: login required, ZIP layout, ChatGPT text, missing files."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from mtg_gateway import skill_page

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

REPO_PLUGIN = Path(__file__).resolve().parent.parent / "plugin"
REPO_SKILL = REPO_PLUGIN / "mtg-gateway"


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        yield h


async def test_skill_pages_need_a_browser_login(harness: Harness) -> None:
    for path in ("/skill", "/skill/mtg-gateway.zip"):
        r = await harness.http.get(path)
        assert r.status_code == 302
        assert r.headers["location"] == "/login?next=/skill"


async def test_signed_in_user_gets_page_and_zip(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/skill")
        page = await b.http.get("/skill")
        assert page.status_code == 200
        assert "/skill/mtg-gateway.zip" in page.text
        assert "<textarea readonly" in page.text
        expected = skill_page.chatgpt_text(REPO_SKILL)
        assert expected and expected.splitlines()[0][:40] in page.text
        assert "default-src 'none'" in page.headers["content-security-policy"]

        r = await b.http.get("/skill/mtg-gateway.zip")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert 'filename="mtg-gateway.zip"' in r.headers["content-disposition"]
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert "mtg-gateway/SKILL.md" in names
        assert "mtg-gateway/reference/tools.md" in names
        assert all(n.startswith("mtg-gateway/") for n in names)

        account = await b.http.get("/account")
        assert "href='/skill'" in account.text
    finally:
        await b.aclose()


async def test_landing_page_links_the_skill(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/")
        r = await b.http.get("/")
        assert "href='/skill'" in r.text
    finally:
        await b.aclose()


def test_zip_is_reproducible_and_skips_links_and_hidden_files(tmp_path: Path) -> None:
    root = tmp_path / "skills" / "mtg-gateway"
    (root / "reference").mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: mtg-gateway\n---\n")
    (root / "reference" / "tools.md").write_text("tools")
    (root / ".DS_Store").write_text("junk")
    outside = tmp_path / "secret.txt"
    outside.write_text("do not ship")
    (root / "link.txt").symlink_to(outside)
    first = skill_page.build_zip(tmp_path)
    assert first == skill_page.build_zip(tmp_path)
    names = zipfile.ZipFile(io.BytesIO(first)).namelist()
    assert names == ["mtg-gateway/SKILL.md", "mtg-gateway/reference/tools.md"]


async def test_missing_skill_files_give_a_clear_503(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(tmp_path / "nowhere"))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        b = Browser(h)
        try:
            await b.login("/skill")
            assert (await b.http.get("/skill")).status_code == 503
            assert (await b.http.get("/skill/mtg-gateway.zip")).status_code == 503
        finally:
            await b.aclose()
