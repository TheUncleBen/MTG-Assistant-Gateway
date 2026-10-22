"""The admin list of the simulator's card models: admins only, who added each
model, and Remove for any model, with the form token and an audit entry. Each member keeps their
own models, so Remove names the card and the member."""

from __future__ import annotations

import html
import re
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
        # the admin tab bar marks the current page (UI standard: tinted, not an orange button)
        assert "href='/admin/models' aria-current='page'>Card models</a>" in r.text
        gw.db.put_card_model("sol ring", "Sol Ring", MODEL, "user-9", 100)
        gw.db.put_card_model("<b>x", "<b>Bad name", MODEL, "user-9", 200)
        gw.db.put_card_model("sol ring", "Sol Ring", MODEL, "admin-1", 50)  # the admin's own
        r = await b.http.get("/admin/models", headers=NAVIGATE)
        text = html.unescape(r.text)
        assert "All card models <span class='ui-sub'>3</span>" in text and "added by bram" in text
        assert "Each model is used only in its owner's simulations. Only admins see who added it." in text
        assert "<b>Bad name" not in r.text and "&lt;b&gt;Bad name" in r.text
        # Remove sits only in the row's menu: the ⋮ is a link to the page with the "are you sure"
        # box open, whose last button is the solid red Remove model with the same screen reader name
        assert "aria-label='Remove the model of Sol Ring added by bram'" not in text
        href = "/admin/models?remove=sol+ring&by=user-9"
        assert f"href='{href}' aria-label='Actions for Sol Ring' aria-haspopup='menu'" in text
        box = html.unescape((await b.http.get(href, headers=NAVIGATE)).text)
        assert "<div class='ui-box mtgbox' id='remove' role='dialog'" in box and "data-open>" in box
        assert "Remove the model of “Sol Ring”?" in box
        assert "“bram” added it. The simulator stops using it in bram's simulations. " in box
        assert "This can't be undone." in box
        assert (
            "<button type='submit' class='danger' form='rmf' "
            "aria-label='Remove the model of Sol Ring added by bram'>Remove model</button>" in box
        )
        csrf = await b.csrf("/admin/models")
        one = {"csrf": csrf, "card_key": "sol ring", "added_by": "user-9"}
        r = await b.http.post("/admin/models", data={**one, "back": "/admin/models?member=user-9"})
        assert r.status_code == 303
        assert r.headers["location"] == "/admin/models?member=user-9&ok=model_removed"
        # the old form's page number still lands on the right page
        assert (await b.http.get("/admin/models?page=1", headers=NAVIGATE)).status_code == 200
        # only bram's model of the card goes; the admin's own stays
        left = [(m["card_key"], m["added_by"]) for m in gw.db.list_card_models()]
        assert left == [("<b>x", "user-9"), ("sol ring", "admin-1")]
        audit = [a for a in gw.db.audit_recent(5, sub="admin-1") if a["event"] == "card_model_removed"]
        removed = {"card": "Sol Ring", "card_key": "sol ring", "added_by": "user-9"}
        assert audit and audit[0]["detail"] == removed
        # both activity logs say it in words, with whose model it was
        for path in ("/admin/activity", "/activity"):
            text = html.unescape((await b.http.get(path, headers=NAVIGATE)).text)
            # the stored JSON is only in the admin page's row details, never the Detail column
            cells = "".join(re.findall(r"<span class='c-det'>(.*?)</span><span class='c-app'>", text))
            assert "Sol Ring (added by bram)" in cells and '"card_key"' not in cells, path
        assert "Card model removed" in html.unescape((await b.http.get("/activity", headers=NAVIGATE)).text)
        r = await b.http.post("/admin/models", data=one)
        assert r.headers["location"].startswith("/admin/models?err=model_gone")
        shown = await b.http.get(r.headers["location"], headers=NAVIGATE)
        assert "That card model was already removed." in html.unescape(shown.text)
        # no form token: nothing removed
        r = await b.http.post("/admin/models", data={"card_key": "<b>x", "added_by": "user-9"})
        assert r.status_code == 403 and gw.db.count_all_card_models() == 2
    finally:
        await b.aclose()


async def test_members_cannot_see_or_use_the_list(gw: Harness) -> None:
    gw.db.put_card_model("opt", "Opt", MODEL, "user-9", 100)
    gw.idp.user = {**ADMIN, "sub": "user-9", "groups": ["mtg-users"]}
    member = Browser(gw)
    await member.login("/account")
    try:
        assert (await member.http.get("/admin/models", headers=NAVIGATE)).status_code == 404
        r = await member.http.post("/admin/models", data={"card_key": "opt", "added_by": "user-9"})
        assert r.status_code == 404 and gw.db.count_all_card_models() == 1
    finally:
        await member.aclose()


async def test_the_log_line_survives_the_adders_account_being_deleted(gw: Harness) -> None:
    gw.db.put_card_model("opt", "Opt", MODEL, "user-9", 100)
    assert gw.db.admin_delete_card_model("opt", "user-9", by="admin-1")
    gw.db.delete_member_data("user-9")
    b = await admin_browser(gw)
    try:
        text = html.unescape((await b.http.get("/admin/activity", headers=NAVIGATE)).text)
        assert "Opt (added by a deleted account)" in text
    finally:
        await b.aclose()


async def test_a_page_past_the_end_shows_the_last_page(gw: Harness) -> None:
    """Any page number, however large, shows the last page (no error)."""
    gw.db.put_card_model("opt", "Opt", MODEL, "user-9", 100)
    b = await admin_browser(gw)
    try:
        for page in ("2", "99999999999999999999999", "-5", "x"):
            r = await b.http.get(f"/admin/models?page={page}", headers=NAVIGATE)
            assert r.status_code == 200 and "Opt" in r.text, page
    finally:
        await b.aclose()


async def test_an_account_without_a_name_is_named_in_words_on_the_list(gw: Harness) -> None:
    """The list and the Remove box say "a member with no name", as the Activity page does, not
    the account's raw id."""
    gw.db.upsert_user("user-8", email=None, name=None, preferred_username=None, groups=[])
    gw.db.put_card_model("opt", "Opt", MODEL, "user-8", 100)
    b = await admin_browser(gw)
    try:
        text = html.unescape((await b.http.get("/admin/models", headers=NAVIGATE)).text)
        assert "added by a member with no name" in text and "user-8</span>" not in text
        box = html.unescape((await b.http.get("/admin/models?remove=opt&by=user-8", headers=NAVIGATE)).text)
        assert "aria-label='Remove the model of Opt added by a member with no name'" in box
    finally:
        await b.aclose()


async def test_an_account_without_a_name_is_not_called_deleted(gw: Harness) -> None:
    """The words "a deleted account" only when the account is gone."""
    gw.db.upsert_user("user-8", email=None, name=None, preferred_username=None, groups=[])
    gw.db.put_card_model("opt", "Opt", MODEL, "user-8", 100)
    assert gw.db.admin_delete_card_model("opt", "user-8", by="admin-1")
    b = await admin_browser(gw)
    try:
        text = html.unescape((await b.http.get("/admin/activity", headers=NAVIGATE)).text)
        assert "Opt (added by a member with no name)" in text
    finally:
        await b.aclose()
