"""The 0.7.44 UI review's page-side fixes (the independent review of 0.7.43): the plain words on
Account, Notifications and My lists (L17, L1); the Account switches' hidden labels and the Price
sources row (M2); a thread page's Forum › group › section trail once its section's list has been
read (L11), its line before Open on Archidekt (L12), the forum front page without the section
chips (L13); a thread's reply count agreeing with the section table (L20); the phone rows' one
second line (M6); the New package box's details as New deck's (L10); no Custom mark beside the
name in the list rows (L16); the Package panel's Budget in the controls' column (L15); a count
on every My lists tab on a first visit (L7); and the icons the box and the menu scripts carry
(L3, L4)."""

from __future__ import annotations

import copy
import re

import pytest

from mtg_gateway import account_lists
from mtg_gateway.theme import esc

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_deck_custom_cards import _custom
from .test_decks_and_proxy import Browser, linked_user
from .test_forum_page import _reply

stack_options = stack_with(SHIPPED)
SECTION = "/forum/Archidekt/General"


@pytest.fixture
async def member(stack: Stack):
    await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/forum")
    try:
        yield b
    finally:
        await b.aclose()


def _card(page: str, card_id: str) -> str:
    m = re.search(
        rf"<div class='card' id='{card_id}'>.*?</div>\s*(?=<div class='card'|</main>|$)", page, re.S
    )
    assert m, card_id
    return m.group(0)


async def test_the_words_say_only_what_the_gateway_knows(member: Browser, stack: Stack) -> None:
    page = (await member.http.get("/account")).text
    prof = _card(page, "profile")
    assert "A JPEG or PNG up to 1 MB." in prof and "Only you can change it" not in prof
    mail = _card(page, "email")
    # the inverted switch reads the right way round: on means notifications
    assert (
        "Archidekt notifications</label>" in mail
        and esc("Shows Archidekt's notifications in the bell.") in mail
    )
    assert "Turn off" not in mail and "Turns off" not in mail
    # on means notifications: the switch is checked exactly when Archidekt's "no notifications" is off
    off = bool(stack.ark.account.profile("alice")["accountSettings"].get("noArchidektNotifications"))
    checked = "id='pr-site_notifications' type='checkbox' name='on' value='1' checked" in mail
    assert checked is (not off)
    defaults = _card(page, "defaults")
    assert "The deck page shows prices from these two shops, first one first." in defaults
    assert "Two shops, in order." not in defaults
    assert ">EDH bracket</option>" in defaults and "EDH Bracket Info" not in defaults
    notes = (await member.http.get("/notifications")).text
    assert "Your notifications from Archidekt." in notes and "bell holds" not in notes
    lists = (await member.http.get("/lists/custom")).text
    assert "Cards you made on Archidekt." in lists and "made up" not in lists
    # the card viewer's note on a custom card (static/cardview.js): no claim about price lists
    js = (await member.http.get("/static/cardview.js")).text
    assert "Custom card, made on Archidekt: not on Scryfall, and not counted in the deck's price here." in js
    assert "or in price lists" not in js


async def test_the_account_switches_and_the_price_sources_row(member: Browser) -> None:
    page = (await member.http.get("/account")).text
    mail = _card(page, "email")
    # each switch names itself for a reader only: the row's bold label is the one shown
    assert "<span class='sr-only'>Other members can contact me</span>" in mail
    assert "sw-text" not in page
    assert mail.count("class='ui-row sw'") == 4
    defaults = _card(page, "defaults")
    two = re.search(r"<div class='ui-row two'>.*?</div></div>", defaults, re.S).group(0)
    assert "Price sources" in two and "aria-label='First price source'" in two
    assert "aria-label='Second price source'" in two


async def test_a_thread_page_knows_its_section_once_the_list_has_been_read(
    member: Browser, stack: Stack
) -> None:
    ark = stack.ark
    r = await member.http.get(SECTION)
    assert r.status_code == 200
    page = (await member.http.get("/forum/thread/700002")).text
    crumbs = re.search(r"<nav class='crumbs'.*?</nav>", page).group(0)
    assert "href='/forum'>Forum</a>" in crumbs
    assert "href='/forum#group-Archidekt'>Archidekt</a>" in crumbs
    assert "href='/forum/Archidekt/General'>General</a>" in crumbs
    # a thread no section list has named yet: the trail is Forum alone (Archidekt's thread data
    # carries no section)
    ark.forum_threads[790002] = {"title": "Fresh thread", "section": "General", "type": 0, "owner": "amy"}
    page = (await member.http.get("/forum/thread/790002")).text
    crumbs = re.search(r"<nav class='crumbs'.*?</nav>", page).group(0)
    assert crumbs.count("<a ") == 1 and "General" not in crumbs
    # the title, its line, then Open on Archidekt on its own line (the section page's order)
    head = page[page.index("<h1>Fresh thread</h1>") :]
    assert head.index("<p class='ui-sub'>started by amy · 0 replies</p>") < head.index("Open on Archidekt")
    assert "<p class='fm-links'><a class='btn btn-plain'" in head and "phead" not in head


async def test_the_forum_front_page_has_no_section_chips(member: Browser) -> None:
    page = (await member.http.get("/forum")).text
    forum = page[page.index("<div class='fm'>") : page.index("</main>")]
    assert "ui-chip" not in forum and "aria-label='Sections'" not in forum
    assert forum.count("<a class='tr' href='/forum/Archidekt/") == 2  # the tables' rows alone


async def test_a_threads_reply_count_agrees_with_the_section_table(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    first = _reply(ark, 700003, "amy", "Sol Ring.")
    _reply(ark, 700003, "alice", "And Arcane Signet.", parent=first)  # nested under the first
    section = (await member.http.get(SECTION)).text
    row = re.search(r"<a class='tr' href='/forum/thread/700003'.*?</a>", section, re.S).group(0)
    thread = (await member.http.get("/forum/thread/700003")).text
    header = re.search(r"<p class='ui-sub'>started by amy · (\d+ repl\w+)</p>", thread).group(1)
    assert header == "1 reply" and header in row, (header, row)


async def test_phone_rows_keep_the_date_on_the_one_second_line(member: Browser) -> None:
    page = (await member.http.get(SECTION)).text
    row = re.search(r"<a class='tr' href='/forum/thread/700002'.*?</a>", page, re.S).group(0)
    # owner · replies · date, the date inline (its separator inside the phone-only span)
    assert re.search(
        r"<span class='sub'><span>amy · \d+ repl\w+<span class='sm-only fm-when'> · <time[^>]*>", row
    ), row
    assert ".fm-when{display:block}" not in page


async def test_the_new_package_box_matches_new_deck(member: Browser) -> None:
    page = (await member.http.get("/decks")).text
    box = re.search(r"<div[^>]*id='newpkg'.*?</form>", page, re.S).group(0)
    assert "placeholder='e.g. Golgari staples'" in box
    assert "aria-live='polite'>0/120</span>" in box and "0 / 120" not in box
    assert "data-count='np-name-c'" not in box  # counted by newpackage.js alone, as New deck's name
    assert ".dx-box .body .dx-two + .dx-two{margin-top:.75rem}" in page


async def test_list_rows_of_a_custom_card_say_custom_once(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    ark.account.customs["alice"] = [_custom(41, "Grizzled Mentor")]
    ark.decks[42]["customCards"] = [
        {"id": 3100, "quantity": 1, "categories": ["Ramp"], "card": _custom(41, "Grizzled Mentor")}
    ]
    for view in ("text", "table"):
        page = (await member.http.get(f"/decks/42?view={view}")).text
        row = re.search(r"<li class='row custom[^>]*>.*?</li>", page, re.S).group(0)
        assert "<span class='set'" in row and ">Custom</span>" in row
        assert "custom-badge" not in row, view
    grid = (await member.http.get("/decks/42?view=grid")).text
    tile = re.search(r"<div class='c custom'.*?</div>", grid, re.S).group(0)
    assert "custom-badge" in tile  # no Set column there: the mark stays


async def test_the_package_panels_budget_sits_in_the_controls_column(member: Browser, stack: Stack) -> None:
    ark = stack.ark
    pkg = copy.deepcopy(ark.decks[42])
    pkg.update(
        id=50,
        name="Golgari staples",
        deckFormat=None,
        customCards=[],
        cardPackage={
            "id": 900,
            "keywords": "golgari, staples",
            "budget": 2,
            "intendedFormats": [3],
            "importCount": 0,
        },
    )
    ark.decks[50] = pkg
    ark.users["alice"]["decks"].append(50)
    page = (await member.http.get("/decks/50/settings")).text
    panel = re.search(r"<form[^>]*id='package'>.*?</form>", page, re.S).group(0)
    budget = re.search(
        r"<div class='ui-row[^']*'><p class='lab'>Budget</p>.*?</div></div>", panel, re.S
    ).group(0)
    assert "wctl" not in budget  # the same column as the Keywords box and the format pills
    assert "<p class='help'>How much the cards cost, roughly.</p>" in budget


async def test_every_tab_has_its_count_on_a_first_visit(member: Browser) -> None:
    account_lists.forget_counts("user-1")
    page = (await member.http.get("/lists/pins")).text
    tabs = re.search(r"<nav class='adm-nav lp-tabs'.*?</nav>", page).group(0)
    assert tabs.count("<span class='n'>") == 5, tabs


async def test_the_scripts_carry_the_icons_their_buttons_ask_for(member: Browser) -> None:
    send = "M4 12l16-8-6 16-2-7z"
    for name in ("box.js", "cardmenu.js"):
        js = (await member.http.get(f"/static/{name}")).text
        assert f"send: \"<path d='{send}'/>\"" in js, name  # Post reply's icon (L3)
    menu = (await member.http.get("/static/cardmenu.js")).text
    assert '"vote-up": "<path d=\'M6 15l6-6 6 6\'/>"' in menu
    social = (await member.http.get("/static/social.js")).text
    assert 'icon("vote-up")' in social and 'icon("plus")' not in social  # ▲, never "+ 0" (L4)
