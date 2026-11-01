"""Account details in the settings-list style (mockup screen 7, 0.7.46): a grouped list whose
rows open their own pages (Archidekt account, Archidekt profile, Email and alerts, New device
defaults, Push notifications, Your view choices) or a box (Sign out on all my devices, Unlink,
Delete my data, solid red only inside it); the shared On/Off and Active labels, connected apps as
rows with the app's full name and no client id, one orange button on a page (Link account while
not linked), the approved shorter wording, and the old one-press addresses opening the list with
its box open."""

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
    return page[start : page.index("</div><div class='acts'>", start) + 400]


async def test_labels_wording_and_the_one_orange_button_while_not_linked(gw: Harness) -> None:
    b = Browser(gw)
    try:
        await b.login()
        page = (await b.http.get("/account/details")).text
        assert (
            "<div class='acc acc-list'>" in page and ".ydetails .yfold" in page and "/static/pages.js" in page
        )
        # the list: the four groups in the mockup's order, two columns balanced by their rows
        # (Archidekt over Your data, 8 rows; Assistant over This gateway, 7: review 2, L10), the
        # member's card at the top
        groups = re.findall(r"<section class='ygrp' data-g='(\d)'(?: id='apps')?><h2>([^<]*)</h2>", page)
        assert groups == [("0", "Archidekt"), ("3", "Your data"), ("1", "Assistant"), ("2", "This gateway")]
        # on a phone the style orders them by data-g: the mockup's order, Your data last
        assert page.count("<div class='ycol'>") == 2 and "<div class='yme'>" in page
        assert "src='/account/avatar'" in page and "Signed in as" not in page
        # Archidekt writes: a row with its value, folded open to the shared label (On green, Off
        # grey) and the approved short note
        on = gw.settings.writes_enabled
        word = "On" if on else "Off"
        assert f"<span class='lbl'>Archidekt writes</span><span class='val'>{word}</span>" in page
        writes = "<span class='ui-lbl green'>" if on else "<span class='ui-lbl grey'>"
        assert f"<p>{writes}" in page and f"{word}</span></p>" in page
        assert "<span class='badge ok'>on</span>" not in page and "<span class='badge'>off</span>" not in page
        assert (
            "<p class='muted small'>Set for the whole gateway by the person who runs it. While they are off, "
            "changes can be reviewed but not applied.</p>" in page
        )
        assert "Archidekt writes are switched on or off" not in page
        # Price alert defaults (mockup screen 8): a row under This gateway that opens the
        # watchlist's box, its value the shipped default until the member saves their own, short
        # enough for the row's one line (review 2, L10)
        assert "<a class='yrow' href='/watchlist?box=defaults'><svg" in page
        # short enough for one line beside its label (independent review 2, L10)
        assert "<span class='lbl'>Price alert defaults</span><span class='val'>10% drop</span>" in page
        # Sign out on all my devices asks first in a box, so it ends in "…" as its neighbours do
        assert "<span class='lbl'>Sign out on all my devices…</span>" in page
        # approval mode and connected apps: one Assistants row (each assistant has its own mode) and
        # the default's row; no form sits on the list
        assert "<span class='lbl'>Assistants</span><span class='val'>None connected</span>" in page
        assert "href='/account/assistants/default'" in page and "Ask me every time" in page
        assert "Save approval mode" not in page and "<div class='card' id='mode'>" not in page
        assert "<div class='card'" not in page and orange(page) == 0
        # not linked: the Archidekt row says so and opens the Link page; the profile rows wait
        assert "<span class='lbl'>Archidekt account</span><span class='val'>Not linked</span>" in page
        assert "Archidekt profile" not in page and "Unlink Archidekt" not in page
        link = (await b.http.get("/account/details/archidekt")).text
        assert "<h1>Archidekt account</h1>" in link and "<a class='yback' href='/account/details'>" in link
        assert "<button class='primary'>Link account</button>" in link
        assert orange(link) == 1, re.findall(r"class='primary'[^>]*>[^<]*", link)
        # the tick hint under the link form is gone; the disclosure strip is still there
        assert "accept-risk-hint" not in link and "aria-describedby" not in card(link, "archidekt")
        assert "<div class='notice disclosure' id='archidekt-disclosure'>" in link
        for section in ("profile", "email", "defaults"):  # nothing to show before linking
            r = await b.http.get(f"/account/details/{section}")
            assert r.status_code == 303 and r.headers["location"] == "/account/details/archidekt"
        assert (await b.http.get("/account/details/nothing")).status_code == 404
        # Delete my data: the one red row, with the trash icon, opens the box; the merged list of
        # what goes is inside the box
        assert (
            "<a class='yrow ydanger' href='/account/details?delete=1' id='delete-data' "
            "data-box-open='delete'>" in page
        )
        assert "<span class='lbl'>Delete my data…</span></a>" in page
        assert "<button class='danger'>" not in page
        assert page.count("class='yrow ydanger'") == 1  # (the style names it too)
        dbox = box(page, "delete")
        assert f"<p>Delete everything this gateway keeps for your account: {pages.DELETED_LIST}." in dbox
        assert "your card models" in dbox and "deck covers" in dbox and "view choices" in dbox
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
        # Sign out on all my devices asks first: its row opens a box with the /logout form
        assert (
            "<a class='yrow' href='/account/details?signout=1' data-box-open='signout'>" in page
            and "href='/logout'" not in page
        )
        sbox = box(page, "signout")
        assert "<h2 id='signout-title'>Sign out on all my devices?</h2>" in sbox
        assert "<form id='signoutf' class='inbox' method='post' action='/logout'>" in sbox
        assert (
            "<button type='submit' class='danger' form='signoutf' name='everywhere' value='1' "
            "data-busy-text='Signing out…'>" in sbox
        )
        opened = (await b.http.get("/account/details?signout=1")).text
        assert "data-open" in opened.split("id='signout' role='dialog'", 1)[1][:120]
        # the forget-choices page keeps its plain button behind its tick
        choices = (await b.http.get("/account/details/choices")).text
        assert "<h1>Your view choices</h1>" in choices and "<h2>Your view choices</h2>" not in choices
        assert "<div class='ui-actions'><button>Forget my view choices</button></div>" in choices
        assert "href='/account/details/choices'" in page
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
    """The connected apps are on Account › Assistants (one row each, its own page with Disconnect);
    Account details keeps one Assistants row with the count."""
    first = await gw.register(client_name="Claude")
    second = await gw.register(client_name="")
    await gw.tokens_for(first)
    await gw.tokens_for(second)
    b = Browser(gw)
    try:
        await b.login()
        details = (await b.http.get("/account/details")).text
        assert "<span class='lbl'>Assistants</span><span class='val'>2 connected</span>" in details
        page = (await b.http.get("/account/assistants")).text
        # each row: the full name (a name alone earns no logo), the neutral icon, no client id
        assert page.count("class='yrow app'") == 2
        assert "<span class='nm'>Claude" in page and "<span class='nm'>Unnamed app" in page
        assert "/static/logos/claude.svg" not in page
        shown = re.sub(r"(href|value)='[^']*'", "", page)
        assert first["client_id"] not in shown and second["client_id"] not in page
        # Disconnect all: a red outline, its box with the solid red final button
        assert (
            "<a class='btn btn-outline-danger' href='/account/assistants?disconnect_all=1'"
            " data-box-open='discall'>" in page
        )
        dall = box(page, "discall")
        assert "<h2 id='discall-title'>Disconnect all assistants?</h2>" in dall
        assert "<button type='submit' class='danger' form='discallf'>Disconnect all</button>" in dall
        assert "<button class='danger'>" not in page  # no one-press solid red anywhere
        # one app's page: Disconnect as a red outline, the box naming it
        href = re.search(
            r"<a class='yrow app' href='(/account/assistants/app-[0-9a-f]+)'>(?:(?!</a>).)*Claude", page
        )
        assert href, page
        one = (await b.http.get(href.group(1))).text
        assert (
            f"<a class='btn btn-outline-danger' href='{href.group(1)}?disconnect=1' data-box-open='disc'>"
            in one
        )
        d1 = box(one, "disc")
        assert "<h2 id='disc-title'>Disconnect “Claude”?</h2>" in d1
        assert "<button type='submit' class='danger' form='discf'>Disconnect</button>" in d1
        # the Connected apps card's old addresses open the Assistants page
        r = await b.http.get(f"/account/details?disconnect={first['client_id']}")
        assert r.status_code == 303 and r.headers["location"] == "/account/assistants"
        r = await b.http.get("/account/details?disconnect_all=1")
        assert r.headers["location"] == "/account/assistants?disconnect_all=1"
        opened = (await b.http.get(r.headers["location"])).text
        assert "data-open" in opened.split("id='discall' role='dialog'", 1)[1][:120]
        # disconnecting one says so; disconnecting all says so plainly
        csrf = await b.csrf("/account/assistants")
        r = await b.http.post(href.group(1), data={"csrf": csrf, "action": "disconnect"})
        assert r.status_code == 303
        after = (await b.http.get(r.headers["location"])).text
        assert "<div class='notice ok'>Disconnected.</div>" in after and after.count("class='yrow app'") == 1
        r = await b.http.post("/account/assistants", data={"csrf": csrf, "action": "disconnect_all"})
        after = (await b.http.get(r.headers["location"])).text
        assert "<div class='notice ok'>Every assistant was disconnected.</div>" in after
        assert "No assistant is connected to your account yet." in after
    finally:
        await b.aclose()


async def test_linked_archidekt_card_has_the_active_label_dates_and_the_unlink_box(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/account/details")).text
        # the list: the row says whose account, and the profile rows are there now
        assert "<span class='lbl'>Archidekt account</span><span class='val'>alice · linked</span>" in page
        for section, label in (
            ("profile", "Archidekt profile"),
            ("email", "Email and alerts on Archidekt"),
            ("defaults", "New device defaults"),
        ):
            assert f"href='/account/details/{section}'><svg" in page and label in page, section
        assert "<a class='yrow' href='/account/details?unlink=1' data-box-open='unlink'>" in page
        assert "<span class='lbl'>Unlink Archidekt…</span></a>" in page
        page = (await b.http.get("/account/details/archidekt")).text
        arch = card(page, "archidekt")
        assert "<h1>Archidekt account</h1>" in page and "<h2>Archidekt</h2>" not in page
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
        # no orange button once linked: Save approval mode moved to Default for new assistants
        assert orange(page) == 0, re.findall(r"class='primary'[^>]*>[^<]*", page)
        assert orange((await b.http.get("/account/details")).text) == 0
        default = (await b.http.get("/account/assistants/default")).text
        assert "<button class='primary'>Save approval mode</button>" in default
        assert orange(default) == 1, re.findall(r"class='primary'[^>]*>[^<]*", default)
        # the old address opens the box on the list, and ?unlink=1 on the page too; unlinking says
        # so in the new words
        opened = (await b.http.get("/account/details?unlink=1")).text
        assert "data-open" in opened.split("id='unlink' role='dialog'", 1)[1][:120]
        opened = (await b.http.get("/account/details/archidekt?unlink=1")).text
        assert "data-open" in opened.split("id='unlink' role='dialog'", 1)[1][:120]
        csrf = await b.csrf()
        r = await b.http.post("/account", data={"csrf": csrf, "action": "unlink"})
        assert r.status_code == 303 and r.headers["location"] == "/account/details?ok=unlinked"
        after = (await b.http.get("/account/details?ok=unlinked")).text
        assert (
            "<div class='notice ok'>Unlinked. The gateway&#x27;s copy of the session was deleted.</div>"
            in after
        )
        assert "Not linked" in after and "Unlink Archidekt" not in after
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


async def test_the_forms_without_an_action_post_to_account_details(stack: Stack) -> None:
    """Account details' own forms (Link account, Forget my choices, the Push notifications card)
    carry no action, so the browser posts them to their own page's address: every section's
    address takes the same posts as /account (a 405 there broke linking and the mode)."""
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/account/details/choices")).text
        bare = re.findall(r"<form method='post'(?![^>]*action=)[^>]*>", page)
        assert len(bare) >= 1, bare  # at least Forget my choices (Save approval mode moved, see below)
        csrf = await b.csrf()
        r = await b.http.post(
            "/account/details/choices", data={"csrf": csrf, "action": "approval_mode", "mode": "manual"}
        )
        # the default's own page (Assistants › Default for new assistants) posts to /account
        assert (
            r.status_code == 303 and r.headers["location"] == "/account/assistants/default?ok=mode_manual"
        ), r.text
        default = (await b.http.get("/account/assistants/default")).text
        assert "<form method='post' action='/account'>" in default
        # an expired form is refused the same way on every address
        for path in ("/account/details", "/account/details/choices"):
            r = await b.http.post(path, data={"csrf": "stale", "action": "approval_mode", "mode": "manual"})
            assert r.status_code == 403, path
    finally:
        await b.aclose()


async def test_ben_can_flip_the_sections_to_fold_open_on_the_list(gw: Harness, monkeypatch) -> None:
    """Sam's open choice (mockup screen 7): each section on its own page (the default), or every
    section folded under its row on the list. One constant flips it; with it off a section's
    address is its fold on the list, and the old #part links need no script."""
    monkeypatch.setattr(pages, "DETAIL_PAGES", False)
    b = Browser(gw)
    await b.login()
    try:
        assert pages.section_href("notifications", "ok=notify_off", "summary") == (
            "/account/details?ok=notify_off#summary"
        )
        r = await b.http.get("/account/details/choices?err=confirm_forget")
        assert r.status_code == 303
        assert r.headers["location"] == "/account/details?err=confirm_forget#choices"
        page = (await b.http.get("/account/details")).text
        assert "<details class='yrow' id='choices'>" in page and "Forget my view choices</button>" in page
        assert "<details class='yrow' id='archidekt'>" in page and "Link account</button>" in page
        assert "href='/account/details/" not in page and "data-section-pages='{}'" in page
    finally:
        await b.aclose()
