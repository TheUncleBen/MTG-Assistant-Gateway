"""The admin list of the simulator's card models (card Q138 item 4): admins only, who added each
model, and Remove for any model, with the form token and an audit entry."""

from __future__ import annotations

import html
from pathlib import Path

import pytest

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import ADMIN, NAVIGATE, _with_admin, admin_browser
from .test_decks_and_proxy import Browser

MODEL = '{"effects":[]}'


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        h.db.upsert_user("user-9", email=None, name=None, preferred_username="bram", groups=["mtg-users"])
        yield h


async def test_admins_see_who_added_each_model_and_can_remove_any(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await b.http.get("/admin/models", headers=NAVIGATE)
        assert r.status_code == 200 and "No card models are kept yet." in r.text
        assert "href='/admin/models'>Card models</a>" in r.text
        gw.db.put_card_model("sol ring", "Sol Ring", MODEL, "user-9", 100)
        gw.db.put_card_model("<b>x", "<b>Bad name", MODEL, "user-9", 200)
        r = await b.http.get("/admin/models", headers=NAVIGATE)
        text = html.unescape(r.text)
        assert "2 card models kept." in text and "added by bram" in text
        assert "<b>Bad name" not in r.text and "&lt;b&gt;Bad name" in r.text
        csrf = await b.csrf("/admin/models")
        r = await b.http.post("/admin/models", data={"csrf": csrf, "card_key": "sol ring", "page": "1"})
        assert r.status_code == 303 and r.headers["location"] == "/admin/models?ok=model_removed&page=1"
        assert [m["card_key"] for m in gw.db.list_card_models()] == ["<b>x"]
        audit = [a for a in gw.db.audit_recent(5, sub="admin-1") if a["event"] == "card_model_removed"]
        removed = {"card": "Sol Ring", "card_key": "sol ring", "added_by": "user-9"}
        assert audit and audit[0]["detail"] == removed
        # both activity logs say it in words, with whose model it was (re-proofread issue 8)
        for path in ("/admin/activity", "/activity"):
            text = html.unescape((await b.http.get(path, headers=NAVIGATE)).text)
            assert "Sol Ring (added by bram)" in text and '"card_key"' not in text, path
        assert "Card model removed" in html.unescape((await b.http.get("/activity", headers=NAVIGATE)).text)
        r = await b.http.post("/admin/models", data={"csrf": csrf, "card_key": "sol ring"})
        assert r.headers["location"].startswith("/admin/models?err=model_gone")
        shown = await b.http.get(r.headers["location"], headers=NAVIGATE)
        assert "That card model was already removed." in html.unescape(shown.text)
        # no form token: nothing removed
        r = await b.http.post("/admin/models", data={"card_key": "<b>x"})
        assert r.status_code == 403 and gw.db.count_all_card_models() == 1
    finally:
        await b.aclose()


async def test_members_cannot_see_or_use_the_list(gw: Harness) -> None:
    gw.db.put_card_model("opt", "Opt", MODEL, "user-9", 100)
    gw.idp.user = {**ADMIN, "sub": "user-9", "groups": ["mtg-users"]}
    member = Browser(gw)
    await member.login("/account")
    try:
        assert (await member.http.get("/admin/models", headers=NAVIGATE)).status_code == 404
        r = await member.http.post("/admin/models", data={"card_key": "opt"})
        assert r.status_code == 404 and gw.db.count_all_card_models() == 1
    finally:
        await member.aclose()


async def test_the_log_line_survives_the_adders_account_being_deleted(gw: Harness) -> None:
    gw.db.put_card_model("opt", "Opt", MODEL, "user-9", 100)
    assert gw.db.admin_delete_card_model("opt", by="admin-1")
    gw.db.delete_member_data("user-9")
    b = await admin_browser(gw)
    try:
        text = html.unescape((await b.http.get("/admin/activity", headers=NAVIGATE)).text)
        assert "Opt (added by a deleted account)" in text
    finally:
        await b.aclose()


async def test_a_page_past_the_end_shows_the_last_page(gw: Harness) -> None:
    """Review round 2, R2-5: any page number, however large, shows the last page (no error)."""
    gw.db.put_card_model("opt", "Opt", MODEL, "user-9", 100)
    b = await admin_browser(gw)
    try:
        for page in ("2", "99999999999999999999999", "-5", "x"):
            r = await b.http.get(f"/admin/models?page={page}", headers=NAVIGATE)
            assert r.status_code == 200 and "Opt" in r.text, page
    finally:
        await b.aclose()
