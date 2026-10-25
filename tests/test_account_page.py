"""The Account page after the UI overhaul: the shared On/Off and Active labels, connected apps as
rows with the app's full name and no client id, red-outline Unlink, Disconnect, Disconnect all
apps and Delete my data that each open an "are you sure" box (solid red only inside it), one
orange button on the page (Save approval mode while linked, Link account while not), the
approved shorter wording, and the old one-press addresses opening the page with its box open."""

from __future__ import annotations

import html
import re

from mtg_gateway import pages

from .conftest import Harness, Stack
from .test_companion import linked_browser
from .test_decks_and_proxy import Browser

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


def card(page: str, card_id: str) -> str:
    return page.split(f"id='{card_id}'", 1)[1].split("</div><div class='card'", 1)[0]


def orange(text: str) -> int:
    """The orange buttons on the page itself: a box's own final button (the picture box's Use
    this picture, hidden until the box opens) is the one orange button of that box, not the page's."""
    text = re.sub(
        r"<div class='ui-box mtgbox[^>]*id='picture'.*?</div><div class='acts'>.*?</div></div>",
        "",
        text,
        flags=re.S,
    )
    return len(re.findall(r"class='primary'|class='btn btn-primary'|class='btn-primary'", text))


def box(page: str, box_id: str) -> str:
    start = page.index(f"<div class='ui-box mtgbox' id='{box_id}' role='dialog'")
    return page[start : page.index("</div><div class='acts'>", start) + 200]


async def test_labels_wording_and_the_one_orange_button_while_not_linked(gw: Harness) -> None:
    b = Browser(gw)
    try:
        await b.login()
        page = (await b.http.get("/account/details")).text
        assert "<div class='acc'>" in page and ".acc .apps" in page and "/static/pages.js" in page
        # the writes badge is the shared label (On green, Off grey); the note is the approved short one
        writes = "<span class='ui-lbl green'>" if gw.settings.writes_enabled else "<span class='ui-lbl grey'>"
        word = "On" if gw.settings.writes_enabled else "Off"
        assert writes in page and f"{word}</span></dd></dl>" in page
        assert "<span class='badge ok'>on</span>" not in page and "<span class='badge'>off</span>" not in page
        assert (
            "<p class='muted small'>Set for the whole gateway by the person who runs it. While they are off, "
            "changes can be reviewed but not applied.</p>" in page
        )
        assert "Archidekt writes are switched on or off" not in page
        # approval mode: the shorter intro; Save is plain while Link account is the one orange button
        assert (
            "<p>How much your assistant may change on Archidekt without asking you first. It applies to your "
            "account only, and to nobody else.</p>" in page
        )
        assert "<button class='btn-plain'>Save approval mode</button>" in page
        assert "<button class='primary'>Link account</button>" in page
        assert orange(page) == 1, re.findall(r"class='primary'[^>]*>[^<]*", page)
        # the tick hint under the link form is gone; the disclosure strip is still there
        assert "accept-risk-hint" not in page and "aria-describedby" not in card(page, "archidekt")
        assert "<div class='notice disclosure' id='archidekt-disclosure'>" in page
        # Delete my data: the merged list, a red-outline opener with the trash icon, the box behind it
        delete = card(page, "delete-data")
        assert f"<p>Delete everything this gateway keeps for your account: {pages.DELETED_LIST}." in delete
        assert "your simulator card models" in delete and "deck covers" in delete and "view choices" in delete
        assert (
            "<a class='btn btn-outline-danger' href='/account/details?delete=1'"
            " data-box-open='delete'>" in delete
        )
        assert "Delete my data…</a>" in delete and "<button class='danger'>" not in delete
        dbox = box(page, "delete")
        assert "<h2 id='delete-title'>Delete my data?</h2>" in dbox
        assert (
            "<p>Everything this gateway keeps for your account is deleted and you are signed out. Your decks "
            "on Archidekt are not touched. This can't be undone.</p>" in dbox
        )
        assert (
            "<label class='check'><input type='checkbox' id='delete-tick' name='confirm' value='yes' "
            "required>" in dbox
        )
        assert "<button type='button' class='btn-plain' data-cancel>Cancel</button>" in dbox
        assert (
            "<button type='submit' class='danger' form='deletef' data-enabled-by='delete-tick'>Delete my data"
            "</button>" in dbox
        )
        assert "hidden>" in page.split("id='delete' role='dialog'", 1)[1][:200]  # closed until opened
        # the forget-choices card keeps its plain button behind its tick
        assert "<div class='ui-actions'><button>Forget my view choices</button></div>" in page
    finally:
        await b.aclose()


async def test_the_old_addresses_open_the_page_with_the_box_open(gw: Harness) -> None:
    b = Browser(gw)
    try:
        await b.login()
        page = (await b.http.get("/account/details?delete=1")).text
        opened = page.split("id='delete' role='dialog'", 1)[1][:200]
        assert "data-open" in opened and "hidden" not in opened
        assert "<a class='bx' href='/account/details' aria-label='Close'" in page
        # a stray press without the tick comes back to the box, with the notice
        csrf = await b.csrf()
        r = await b.http.post("/account", data={"csrf": csrf, "action": "delete_data"})
        assert r.status_code == 303
        assert r.headers["location"] == "/account/details?delete=1&err=confirm_delete"
    finally:
        await b.aclose()


async def test_connected_apps_rows_and_their_boxes(gw: Harness) -> None:
    first = await gw.register(client_name="Claude")
    second = await gw.register(client_name="")
    await gw.tokens_for(first)
    await gw.tokens_for(second)
    b = Browser(gw)
    try:
        await b.login()
        page = (await b.http.get("/account/details")).text
        apps = card(page, "apps")
        assert "<p class='muted small'>Disconnect any you don't recognize.</p>" in apps
        assert "Assistants that can use your account now" not in apps
        # each row: the app chip (full name, one neutral icon), the date, no client id
        assert apps.count("<li>") == 2
        assert "<span class='nm'><span class='ui-app'>" in apps and "<span class='nm'>Claude</span>" in apps
        assert "<span class='ui-app unnamed'>" in apps and "Unnamed app</span>" in apps
        # the client id is only in the form's hidden field and the no-script address, never shown
        shown = re.sub(r"(href|value)='[^']*'", "", apps)
        assert first["client_id"] not in shown and "<code class='small'>" not in apps
        assert "<span class='sub'>Last signed in or refreshed <time class='ui-when'" in apps
        # Disconnect: red outline, the old form's address, the box with its solid red final button
        href = f"/account/details?disconnect={first['client_id']}"
        m = re.search(
            rf"<a class='btn btn-outline-danger' href='{re.escape(href)}' data-box-open='(disc\d)'>", apps
        )
        assert m and m.group(0).endswith(">") and "Disconnect</a>" in apps[m.end() : m.end() + 20]
        bid = m.group(1)  # the rows are in the database's order, so Claude may be either row
        other = "disc2" if bid == "disc1" else "disc1"
        d1 = box(page, bid)
        assert f"<h2 id='{bid}-title'>Disconnect “Claude”?</h2>" in d1
        assert (
            "<p>“Claude” can no longer use your account. To use it again, connect it again and approve "
            "it.</p>" in d1
        )
        assert f"<input type='hidden' name='client_id' value='{first['client_id']}'>" in d1
        assert f"<button type='submit' class='danger' form='{bid}f'>Disconnect</button>" in d1
        assert "Disconnect “Unnamed app”?" in page
        # Disconnect all apps: the same pattern
        assert (
            "<a class='btn btn-outline-danger' href='/account/details?disconnect_all=1'"
            " data-box-open='discall'>"
            "Disconnect all apps</a>" in apps
        )
        dall = box(page, "discall")
        assert "<h2 id='discall-title'>Disconnect all apps?</h2>" in dall
        assert (
            "<p>Every assistant connected to your account loses access at once. Each one has to be connected "
            "and approved again.</p>" in dall
        )
        assert "<button type='submit' class='danger' form='discallf'>Disconnect all apps</button>" in dall
        assert "<button class='danger'>" not in page  # no one-press solid red anywhere
        # the old address opens the matching box only
        opened = (await b.http.get(href)).text
        assert "data-open" in opened.split(f"id='{bid}' role='dialog'", 1)[1][:120]
        assert "data-open" not in opened.split(f"id='{other}' role='dialog'", 1)[1][:120]
        # disconnecting names the app in the notice; disconnecting all says so plainly
        csrf = await b.csrf()
        r = await b.http.post(
            "/account", data={"csrf": csrf, "action": "disconnect", "client_id": first["client_id"]}
        )
        assert r.status_code == 303
        after = (await b.http.get(r.headers["location"].split("#")[0])).text
        assert "<div class='notice ok'>Disconnected “Claude”.</div>" in after
        assert "pending proposals were rejected" not in after
        r = await b.http.post("/account", data={"csrf": csrf, "action": "disconnect_all"})
        after = (await b.http.get(r.headers["location"].split("#")[0])).text
        assert "<div class='notice ok'>All apps were disconnected.</div>" in after
        assert "No assistant is connected to your account." in after
    finally:
        await b.aclose()


async def test_linked_archidekt_card_has_the_active_label_dates_and_the_unlink_box(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/account/details")).text
        arch = card(page, "archidekt")
        assert (
            "<p>Linked to <strong>alice</strong> <span class='ui-lbl green'>" in arch
            and "Active</span></p>" in arch
        )
        assert "<span class='badge ok'>active</span>" not in arch
        # the shared list dates ("just now", full date on hover) for linked and last used
        assert "<p class='muted small'>Linked <time class='ui-when' datetime=" in arch
        assert "data-fmt='rel'" in arch
        # Unlink: red outline, then the box; the fold stays under its divider line
        assert (
            "<a class='btn btn-outline-danger' href='/account/details?unlink=1'"
            " data-box-open='unlink'>" in arch
        )
        assert "Unlink and delete stored session</a>" in arch
        ubox = box(page, "unlink")
        assert "<h2 id='unlink-title'>Unlink Archidekt?</h2>" in ubox
        assert (
            "<p>The gateway deletes its copy of your Archidekt session at once. Whether the session also "
            "stops working on Archidekt's side is not known. Your decks on Archidekt are not touched.</p>"
            in ubox
        )
        assert (
            "<button type='submit' class='danger' form='unlinkf'>Unlink and delete stored session</button>"
            in ubox
        )
        assert "<details class='disclosure' id='archidekt-disclosure'>" in arch
        # one orange button: Save approval mode, now that there is no Link account
        assert "<button class='primary'>Save approval mode</button>" in page
        assert orange(page) == 1, re.findall(r"class='primary'[^>]*>[^<]*", page)
        # the old address opens the box; unlinking says so in the new words
        opened = (await b.http.get("/account/details?unlink=1")).text
        assert "data-open" in opened.split("id='unlink' role='dialog'", 1)[1][:120]
        csrf = await b.csrf()
        r = await b.http.post("/account", data={"csrf": csrf, "action": "unlink"})
        assert r.status_code == 303 and r.headers["location"] == "/account/details?ok=unlinked"
        after = (await b.http.get("/account/details?ok=unlinked")).text
        assert (
            "<div class='notice ok'>Unlinked. The gateway&#x27;s copy of the session was deleted.</div>"
            in after
        )
    finally:
        await b.aclose()


def test_no_setting_names_or_verify_in_the_member_wording() -> None:
    text = html.unescape(pages.DELETED_LIST + pages.ACCOUNT_CSS)
    for word in ("verify", "MTG_", "client ID"):
        assert word not in text, word


async def test_the_disconnected_notice_names_only_the_members_own_app(stack: Stack) -> None:
    """N-4 (review 2026-10-22): ?ok=disconnected&app=<id> names the app only when it is one the
    member connected; any other client id reads as an unnamed app, its name never looked up."""
    b = await linked_browser(stack)
    try:
        db = stack.h.app.state.gateway.db
        db.save_client("someone-elses", {"client_name": "Secret Helper"})
        page = (await b.http.get("/account/details?ok=disconnected&app=someone-elses")).text
        assert "Secret Helper" not in page
        assert "<div class='notice ok'>Disconnected “Unnamed app”.</div>" in page
    finally:
        await b.aclose()
