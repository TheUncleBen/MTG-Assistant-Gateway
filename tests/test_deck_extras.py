"""The deck-side pages of parity release 2 (deck_extras.py, the Package panel and Add template on
Deck settings, New package on My decks, Add to a deck… on My lists): each is the member's own press
on the page. An account action (help, report, contest) goes to its review page, where the member
applies it; a package is reviewed the same way; the registration PDF is a read; the lock, the
package settings, a template added and a new package are applied at once as the page's other
presses are. Every route is refused for an assistant's bearer token. Fakes only."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import html

import pytest

from .conftest import Stack, stack_with
from .test_account_actions import _browser_apply, _contest_ready, _help_ready
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_packages import _new_package

stack_options = stack_with(SHIPPED)
WHO = "alice"
WORDS = " ".join(["word"] * 12)


def _templates(ark) -> None:
    ark.account.templates[WHO] = [
        {"id": 11, "name": "Ramp", "includedInDeck": True, "includedInPrice": True, "isPremier": False},
        {"id": 12, "name": "Draw", "includedInDeck": True, "includedInPrice": False, "isPremier": False},
    ]


def _ramp(ark) -> None:
    """Deck 42 with Ramp and Artifact categories; Sol Ring filed under both (the fake deck keeps
    only Commander from the CSV's suggested categories)."""
    deck = ark.decks[42]
    deck["categories"] += [
        {"name": "Ramp", "isPremier": False, "includedInDeck": True, "includedInPrice": True},
        {"name": "Artifact", "isPremier": False, "includedInDeck": True, "includedInPrice": True},
    ]
    for row in deck["cards"]:
        if row["card"]["oracleCard"]["name"] == "Sol Ring":
            row["categories"] = ["Ramp", "Artifact"]


async def _member(stack: Stack) -> tuple[Browser, str]:
    token = await linked_user(stack)
    b = Browser(stack.h)
    await b.login("/decks/42")
    return b, token


async def _post(b: Browser, path: str, **data):
    data.setdefault("csrf", await b.csrf("/decks/42"))
    return await b.http.post(path, data=data)


def _pid(r) -> str:
    assert r.status_code == 303, r.text
    where = r.headers["location"]
    assert where.startswith("/proposals/"), where
    return where.rpartition("/")[2]


# -- deck help ------------------------------------------------------------------------------------------


async def test_deck_help_is_posted_edited_and_delisted_from_the_deck_page(stack: Stack) -> None:
    ark = stack.ark
    _help_ready(ark)
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks/42/help")).text
        assert "Request deck help" in page and "data-open" in page and "Post the request" in page
        assert "Deck tags: Combo" in page and "Size: 100 cards" in page
        r = await _post(b, "/decks/42/help", goals=WORDS, issues=WORDS)
        pid = _pid(r)
        assert ark.account.help == {}  # the review page's press, not the form's
        done = await _browser_apply(stack.h, pid)
        assert done["state"] == "applied", done
        assert ark.account.help[42]["goals"] and ark.account.help[42]["active"] is True
        deck = (await b.http.get("/decks/42")).text
        assert "Listed for deck help on Archidekt" in deck and "Edit or delist" in deck
        assert "Your help request" in deck and "Save changes" in deck
        # a short box is refused before anything is proposed, with the box drawn open again
        bad = await _post(b, "/decks/42/help", help_action="edit", goals="too short", issues=WORDS)
        assert bad.status_code == 400 and "10 to 200 words" in bad.text and "too short" in bad.text
        r = await _post(b, "/decks/42/help", help_action="edit", goals=WORDS + " more", issues=WORDS)
        done = await _browser_apply(stack.h, _pid(r))
        assert done["state"] == "applied", done
        assert "more" in ark.account.help[42]["goals"]
        page = (await b.http.get("/decks/42/delist")).text
        assert "Take the request off the board?" in page and "data-open" in page
        r = await _post(b, "/decks/42/help", help_action="delist")
        done = await _browser_apply(stack.h, _pid(r))
        assert done["state"] == "applied", done
        assert ark.account.help[42]["active"] is False
        assert "Listed for deck help" not in (await b.http.get("/decks/42")).text
    finally:
        await b.aclose()


# -- reports --------------------------------------------------------------------------------------------


async def test_a_deck_and_a_card_are_reported_through_the_review_page(stack: Stack) -> None:
    ark = stack.ark
    b, _token = await _member(stack)
    try:
        page = html.unescape((await b.http.get("/decks/42/report-to-archidekt")).text)
        assert "Report “Sample Commander Deck” to Archidekt's moderators" in page and "data-open" in page
        # a report's privacy on Archidekt is not known, so the box says nothing of it
        assert "Your assistant can draft a report, but it always waits for your press." in page
        assert "A report is public" not in page
        r = await _post(b, "/decks/42/report-to-archidekt", message="Stolen list.")
        done = await _browser_apply(stack.h, _pid(r))
        assert done["state"] == "applied", done
        assert ark.account.reports[-1]["by"] == WHO and "Stolen list." in str(ark.account.reports[-1])
        page = html.unescape((await b.http.get("/decks/42/report-to-archidekt?card=Sol%20Ring")).text)
        assert "Report “Sol Ring” to Archidekt's moderators" in page
        r = await _post(
            b, "/decks/42/report-to-archidekt", report_kind="card", card="Sol Ring", message="Bad art"
        )
        done = await _browser_apply(stack.h, _pid(r))
        assert done["state"] == "applied", done
        assert "Sol Ring: Bad art" in str(ark.account.reports[-1])
        empty = await _post(b, "/decks/42/report-to-archidekt", message="  ")
        assert empty.status_code == 400 and "needs a message" in empty.text
    finally:
        await b.aclose()


async def test_a_custom_card_is_reported_as_one_and_cannot_be_locked(stack: Stack) -> None:
    ark = stack.ark
    _templates(ark)
    row = dict(ark.decks[42]["cards"][0])
    row.update(id=99001, customCard={"id": 5}, categories=["Ramp"])
    row["card"] = {**row["card"], "oracleCard": {**row["card"]["oracleCard"], "name": "My Custom"}}
    ark.decks[42]["cards"].append(row)
    b, _token = await _member(stack)
    try:
        assert "data-custom='1'" in (await b.http.get("/decks/42")).text
        r = await _post(
            b, "/decks/42/report-to-archidekt", report_kind="card", card="My Custom", message="Copied"
        )
        pid = _pid(r)
        shown = (await b.http.get(f"/api/v1/proposals/{pid}")).json()
        assert "custom_card" in str(shown) and "My Custom" in str(shown), shown
        locked = await _post(b, "/decks/42/lock", card="My Custom")
        assert locked.status_code == 400 and "Not for custom cards" in locked.text
        assert ark.account.defaults.get(WHO, {}) == {}
    finally:
        await b.aclose()


# -- registration PDF ----------------------------------------------------------------------------------


async def test_the_registration_pdf_answers_with_its_address_and_keeps_no_details(stack: Stack) -> None:
    ark = stack.ark
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks/42/registration")).text
        assert "Registration PDF" in page and "data-open" in page and "Make the PDF" in page
        assert (
            "Main deck: every card whose first category counts in the deck. "
            "Sideboard: the Sideboard category." in html.unescape(page)
        )
        csrf = await b.csrf("/decks/42")
        r = await b.http.post(
            "/decks/42/registration",
            data={"csrf": csrf, "first_name": "Ben", "last_name": "Smith", "event": "FNM", "sort": "type"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200 and r.json()["ok"] is True, r.text
        assert r.json()["pdf_url"] == "https://storage.example.com/pdf/1.pdf"
        assert ark.account.pdfs[0]["event"] == "FNM"
        # without scripts: the page with the box open and the link
        r = await _post(b, "/decks/42/registration", first_name="Ben", last_name="Smith", event="FNM")
        assert r.status_code == 200 and "Open the PDF" in r.text and "storage.example.com/pdf/2.pdf" in r.text
        rows = stack.h.app.state.gateway.db._conn.execute("SELECT detail_json FROM audit_log").fetchall()
        assert not [x for x in rows if x[0] and ("Smith" in x[0] or "FNM" in x[0])]
        bad = await _post(b, "/decks/42/registration", dci_number="x")
        assert bad.status_code == 400 and len(ark.account.pdfs) == 2
    finally:
        await b.aclose()


# -- packages ---------------------------------------------------------------------------------------------


async def test_a_package_is_added_through_the_review_page_and_listed_for_the_box(stack: Stack) -> None:
    ark = stack.ark
    b, token = await _member(stack)
    try:
        made = await _browser_apply(
            stack.h, (await _new_package(stack, token, decklist_text="1 Arcane Signet"))["proposal_id"]
        )
        pkg_id = str(made["result"]["deck_id"])
        listed = (await b.http.get("/decks/api/packages")).json()
        assert listed["ok"] and [p["id"] for p in listed["packages"]] == [pkg_id]
        assert listed["packages"][0]["name"] == "Fetches" and "$$$" in listed["packages"][0]["line"]
        page = (await b.http.get("/decks/42/package")).text
        assert "High when it adds more than 10 cards." in page
        assert "More rows than your low-risk limit also wait for your press." in page
        assert "Add a package" in page and "data-open" in page and "Review the cards" in page
        empty = await _post(b, "/decks/42/package", package="")
        assert empty.status_code == 400 and "paste a package link or id" in empty.text
        r = await _post(b, "/decks/42/package", package=pkg_id, categories="default", skip_existing="1")
        pid = _pid(r)
        assert "Arcane Signet" not in {c["card"]["oracleCard"]["name"] for c in ark.decks[42]["cards"]}
        done = await _browser_apply(stack.h, pid)
        assert done["state"] == "applied", done
        assert "Arcane Signet" in {c["card"]["oracleCard"]["name"] for c in ark.decks[42]["cards"]}
    finally:
        await b.aclose()


async def test_a_new_package_is_made_from_my_decks(stack: Stack) -> None:
    ark = stack.ark
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks")).text
        assert "New package" in page and "href='/decks/new-package'" in page
        page = (await b.http.get("/decks/new-package")).text
        assert "data-open" in page and "Create package" in page and "Intended formats" in page
        csrf = await b.csrf("/decks")
        bad = await b.http.post("/decks/new-package", data={"csrf": csrf, "name": "", "keywords": "x"})
        assert bad.status_code == 400 and "Give the package a name." in bad.text
        bad = await b.http.post("/decks/new-package", data={"csrf": csrf, "name": "Staples", "keywords": " "})
        assert bad.status_code == 400 and "Give it at least one keyword." in bad.text
        r = await b.http.post(
            "/decks/new-package",
            data={
                "csrf": csrf,
                "name": "Staples",
                "keywords": "golgari, staples",
                "budget": "2",
                "format": ["commander", "pauper"],
                "private": "1",
            },
        )
        assert r.status_code == 303, r.text
        where = r.headers["location"]
        assert where.endswith("?ok=created"), where
        deck_id = int(where.split("/")[2].partition("?")[0])
        block = ark.decks[deck_id]["cardPackage"]
        assert block["keywords"] == "golgari, staples" and block["budget"] == 2
        assert sorted(block["intendedFormats"]) == sorted([3, 10]) or len(block["intendedFormats"]) == 2
        assert ark.decks[deck_id]["private"] is True and ark.decks[deck_id]["cards"] == []
        expired = await b.http.post("/decks/new-package", data={"name": "X", "keywords": "y"})
        assert expired.status_code == 403 and "This form expired" in expired.text
    finally:
        await b.aclose()


async def test_package_settings_are_saved_from_deck_settings(stack: Stack) -> None:
    ark = stack.ark
    b, token = await _member(stack)
    try:
        assert "id='package'" not in (await b.http.get("/decks/42/settings")).text  # not a package
        made = await _browser_apply(stack.h, (await _new_package(stack, token))["proposal_id"])
        pkg_id = str(made["result"]["deck_id"])
        page = (await b.http.get(f"/decks/{pkg_id}/settings")).text
        assert "id='package'" in page and "Save package" in page and "value='fetchlands'" in page
        csrf = await b.csrf("/decks")
        r = await b.http.post(
            f"/decks/{pkg_id}/package-settings",
            data={"csrf": csrf, "keywords": "fetches", "budget": "", "format": ["commander", "modern"]},
        )
        assert (
            r.status_code == 303 and r.headers["location"] == f"/decks/{pkg_id}/settings?ok=package#package"
        )
        block = ark.decks[int(pkg_id)]["cardPackage"]
        assert (
            block["keywords"] == "fetches" and block["budget"] is None and len(block["intendedFormats"]) == 2
        )
        page = (await b.http.get(r.headers["location"])).text
        assert "Package settings saved to Archidekt and read back" in page
        same = await b.http.post(
            f"/decks/{pkg_id}/package-settings",
            data={"csrf": csrf, "keywords": "fetches", "budget": "", "format": ["commander", "modern"]},
        )
        assert same.status_code == 400 and "nothing would change" in same.text
        not_one = await b.http.post("/decks/42/package-settings", data={"csrf": csrf, "keywords": "x"})
        assert not_one.status_code == 400 and "not a card package" in not_one.text
    finally:
        await b.aclose()


# -- lock personal categories ---------------------------------------------------------------------------


async def test_lock_saves_the_cards_template_categories_as_its_defaults(stack: Stack) -> None:
    ark = stack.ark
    _templates(ark)
    _ramp(ark)
    b, _token = await _member(stack)
    try:
        got = (await b.http.get("/decks/api/templates")).json()
        assert got["ok"] and [t["name"] for t in got["templates"]] == ["Ramp", "Draw"]
        page = (await b.http.get("/decks/42/lock?card=Sol%20Ring")).text
        assert "data-open" in page and "Lock personal categories for “Sol Ring”" in page
        assert "Only names that are category templates are kept; others are left out.</p>" in page
        assert "it goes into <b data-cats>Ramp</b>" in page and "Left out, not a template: Artifact." in page
        r = await _post(b, "/decks/42/lock", card="Sol Ring")
        assert r.status_code == 303 and r.headers["location"] == "/decks/42?ok=locked&n=Sol+Ring", r.text
        assert ark.account.defaults[WHO] == {9003: ["Ramp"]}
        page = (await b.http.get("/decks/42?ok=locked&n=Sol+Ring")).text
        assert "Default categories for “Sol Ring” saved on Archidekt and read back." in page
        # a card whose categories are not templates: nothing to lock
        no = await _post(b, "/decks/42/lock", card="Acidic Slime")
        assert no.status_code == 400 and "nothing to lock" in no.text
        gone = await _post(b, "/decks/42/lock", card="Black Lotus")
        assert gone.status_code == 400 and "is not in this deck" in gone.text
        csrf = await b.csrf("/decks/42")
        j = await b.http.post(
            "/decks/42/lock", data={"csrf": csrf, "card": "Sol Ring"}, headers={"Accept": "application/json"}
        )
        assert j.status_code == 200 and j.json()["kept"] == ["Ramp"], j.text
    finally:
        await b.aclose()


# -- contests -------------------------------------------------------------------------------------------


async def test_a_contest_entry_and_its_withdrawal_go_through_the_review_page(stack: Stack) -> None:
    ark = stack.ark
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks/42/contest")).text
        assert "Enter “Sample Commander Deck” in a contest" in page and "data-open" in page
        assert "replaces the deck's description" in page or "replaces" in page
        r = await _post(b, "/decks/42/contest", contest="881", summary="Simic ramp")
        done = await _browser_apply(stack.h, _pid(r))
        assert done["state"] == "applied", done
        assert [e["deck"]["id"] for e in ark.account.entries[881]] == [42]
        bad = await _post(b, "/decks/42/contest", contest="881", summary="")
        assert bad.status_code == 400 and "summary" in bad.text
        page = (await b.http.get("/decks/42/withdraw")).text
        assert "Withdraw “Sample Commander Deck” from a contest?" in page and "data-open" in page
        r = await _post(b, "/decks/42/contest", entry_action="withdraw", contest="881")
        done = await _browser_apply(stack.h, _pid(r))
        assert done["state"] == "applied", done
        assert ark.account.entries[881] == []
    finally:
        await b.aclose()


# -- templates into a deck -------------------------------------------------------------------------------


async def test_a_template_is_added_as_a_category_from_deck_settings(stack: Stack) -> None:
    ark = stack.ark
    _templates(ark)
    _ramp(ark)
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks/42/settings?templates=1")).text
        assert "Add a template to this deck" in page and "data-open" in page
        assert "name='template' value='Draw'" in page
        assert "name='template' value='Ramp'" not in page  # Ramp is a category already
        csrf = await b.csrf("/decks/42/settings")
        none = await b.http.post("/decks/42/categories", data={"csrf": csrf, "action": "template"})
        assert none.status_code == 400 and "Tick at least one template." in none.text
        r = await b.http.post(
            "/decks/42/categories", data={"csrf": csrf, "action": "template", "template": ["Draw"]}
        )
        assert r.status_code == 303, r.text
        assert r.headers["location"] == "/decks/42/settings?ok=template_added#categories"
        made = next(c for c in ark.decks[42]["categories"] if c["name"] == "Draw")
        assert made["includedInPrice"] is False and made["includedInDeck"] is True
        page = (await b.http.get(r.headers["location"])).text
        assert "Category added on Archidekt" in page
    finally:
        await b.aclose()


async def test_a_template_is_added_to_a_deck_from_my_lists(stack: Stack) -> None:
    ark = stack.ark
    _templates(ark)
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/lists/templates")).text
        assert "Add to a deck…" in page and "data-box='tpl-deck'" in page
        page = (await b.http.get("/lists/templates?add=12")).text
        assert "Add “Draw” to a deck" in page and "data-open" in page
        assert "<option value='42'" in page and "Sample Commander Deck" in page
        csrf = await b.csrf("/lists/templates")
        r = await b.http.post(
            "/lists/templates",
            data={"csrf": csrf, "action": "add_to_deck", "was_name": "Draw", "deck": "42"},
        )
        pid = _pid(r)
        assert not [c for c in ark.decks[42]["categories"] if c["name"] == "Draw"]
        done = await _browser_apply(stack.h, pid)
        assert done["state"] == "applied", done
        assert [c for c in ark.decks[42]["categories"] if c["name"] == "Draw"]
        again = await b.http.post(
            "/lists/templates",
            data={"csrf": csrf, "action": "add_to_deck", "was_name": "Draw", "deck": "42"},
        )
        assert again.status_code == 400 and "Add “Draw” to a deck" in again.text
    finally:
        await b.aclose()


# -- an assistant cannot use the pages ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/decks/42/help", {"goals": WORDS, "issues": WORDS}),
        ("/decks/42/report-to-archidekt", {"message": "x"}),
        ("/decks/42/registration", {"first_name": "A"}),
        ("/decks/42/package", {"package": "1"}),
        ("/decks/42/contest", {"contest": "881", "summary": "x"}),
        ("/decks/42/lock", {"card": "Sol Ring"}),
        ("/decks/42/package-settings", {"keywords": "x"}),
        ("/decks/42/categories", {"action": "template", "template": "Draw"}),
        ("/decks/new-package", {"name": "X", "keywords": "y"}),
        ("/lists/templates", {"action": "add_to_deck", "was_name": "Draw", "deck": "42"}),
    ],
)
async def test_an_assistant_token_cannot_use_the_pages(stack: Stack, path: str, data: dict) -> None:
    ark = stack.ark
    _help_ready(ark)
    _contest_ready(ark)
    _templates(ark)
    token = await linked_user(stack)
    before = (
        dict(ark.account.help),
        list(ark.account.reports),
        list(ark.account.pdfs),
        dict(ark.account.entries),
        dict(ark.account.defaults),
        sorted(ark.decks),
        [c["name"] for c in ark.decks[42]["categories"]],
    )
    r = await stack.h.http.post(path, data=data, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (302, 303, 401, 403), (path, r.status_code, r.text[:200])
    assert "/proposals/" not in r.headers.get("location", "")
    after = (
        dict(ark.account.help),
        list(ark.account.reports),
        list(ark.account.pdfs),
        dict(ark.account.entries),
        dict(ark.account.defaults),
        sorted(ark.decks),
        [c["name"] for c in ark.decks[42]["categories"]],
    )
    assert after == before
    for api in ("/decks/api/templates", "/decks/api/packages"):
        j = await stack.h.http.get(api, headers={"Authorization": f"Bearer {token}"})
        assert j.status_code == 401, api


async def test_a_page_proposal_still_needs_the_members_press_on_the_review_page(stack: Stack) -> None:
    ark = stack.ark
    _help_ready(ark)
    b, token = await _member(stack)
    try:
        pid = _pid(await _post(b, "/decks/42/help", goals=WORDS, issues=WORDS))
        out = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": pid}))
        assert out["ok"] is False and out["error"] == "browser_required" and ark.account.help == {}
    finally:
        await b.aclose()


# -- the package listing UI (plan rows 6.1 and 6.4) ----------------------------------------------------


async def test_the_add_a_package_box_carries_its_filters_and_a_preset(stack: Stack) -> None:
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks/42/package")).text
        for whose in ("mine", "followed", "all"):
            assert f"data-whose='{whose}'" in page, whose
        assert "aria-pressed='true' data-whose='mine'" in page
        assert "<select id='dx-pkfmt'>" in page and "<option value='commander'>Commander</option>" in page
        assert "<select id='dx-pkbud'>" in page and "<option value='4'>$$$$</option>" in page
        assert (
            "<option value=''>Any budget</option>" in page and "<option value=''>Any format</option>" in page
        )
        assert "type='search'" in page and "Package name" in page
        assert "Load more" in page and "dx-more" in page
        assert "name='package' value=''" in page
        # a package named in the address (Add to a deck… on My decks) fills the field; the
        # switches keep their fresh defaults
        page = (await b.http.get("/decks/42/package?package=50")).text
        assert "name='package' value='50'" in page
        assert page.count("value='1' checked") == 3, page.count("value='1' checked")
        page = (await b.http.get("/decks/42/package?package=<x>")).text
        assert "value='&lt;x&gt;'" in page
    finally:
        await b.aclose()


async def test_packages_json_rows_carry_budget_and_formats_for_the_tiles(stack: Stack) -> None:
    b, token = await _member(stack)
    try:
        await _browser_apply(stack.h, (await _new_package(stack, token))["proposal_id"])
        listed = (await b.http.get("/decks/api/packages?whose=mine")).json()
        assert listed["ok"] and listed["packages"][0]["budget"] == "$$$"
        assert listed["packages"][0]["formats"] == "Commander"
    finally:
        await b.aclose()


async def test_my_decks_has_a_show_filter_that_the_address_keeps(stack: Stack) -> None:
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks")).text
        assert "<select id='f-show' name='show'>" in page
        assert "<option value='' selected>Decks and packages</option>" in page
        assert "<option value='decks'>Decks only</option>" in page
        assert "<option value='packages'>Packages only</option>" in page
        page = (await b.http.get("/decks?show=packages")).text
        assert "<option value='packages' selected>Packages only</option>" in page
        page = (await b.http.get("/decks?show=nonsense")).text
        assert "<option value='' selected>Decks and packages</option>" in page
        j = (await b.http.get("/api/decks/mine?shape=list&show=packages")).json()
        assert j["ok"] is True and "Sample Commander Deck" in j["html"]
    finally:
        await b.aclose()


async def test_the_add_a_package_box_offers_the_decks_tags_and_my_color_tags(stack: Stack) -> None:
    """The mockup's Color tag select beside Categories: None, the tags this deck already uses
    (from its cards, no extra read) and the member's own color tags, read only when the box opens
    (the script asks /decks/api/color_tags; the box's own address reads them for the page without
    scripts), never on a plain deck page load. The choice is kept when the box comes back with a
    refusal, even one the script added."""
    ark = stack.ark
    ark.account.tags[WHO] = [
        {"id": 21, "name": "Cut", "color": "#ff4d4f"},
        {"id": 22, "name": "Have", "color": "#37d67a"},
    ]
    for row in ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] == "Sol Ring":
            row["label"] = "Have,#37d67a"
        elif row["card"]["oracleCard"]["name"] == "Beast Within":
            row["label"] = "Maybe,#2185d0"
    b, _token = await _member(stack)
    try:
        page = (await b.http.get("/decks/42")).text
        assert "<label for='dx-ptag'>Color tag</label>" in page
        assert "<select id='dx-ptag' name='color_tag' data-src='/decks/api/color_tags'>" in page
        assert "<option value='' selected>None</option>" in page
        assert "<option value='Have,#37d67a'>Have</option>" in page
        assert "<option value='Maybe,#2185d0'>Maybe</option>" in page
        assert "<option value='Cut,#ff4d4f'>" not in page
        assert ("GET", "/api/decks/colorTags/") not in ark.calls  # the deck page's speed rule
        # the box by its own address (no scripts): the member's tags too, each name once
        page = (await b.http.get("/decks/42/package")).text
        assert "<option value='Cut,#ff4d4f'>Cut</option>" in page
        assert page.count("<option value='Have,#37d67a'>Have</option>") == 1
        assert ("GET", "/api/decks/colorTags/") in ark.calls
        # what the script reads when the box opens
        got = (await b.http.get("/decks/api/color_tags")).json()
        assert got == {
            "ok": True,
            "tags": [{"name": "Cut", "color": "#ff4d4f"}, {"name": "Have", "color": "#37d67a"}],
        }
        # a refusal keeps the choice
        r = await _post(b, "/decks/42/package", package="", color_tag="Keep,#123456")
        assert r.status_code == 400
        assert "<option value='Keep,#123456' selected>Keep</option>" in r.text
        r = await _post(b, "/decks/42/package", package="", color_tag="Have,#37d67a")
        assert "<option value='Have,#37d67a' selected>Have</option>" in r.text
        assert "<option value='' selected>None</option>" not in r.text
        # the swatch beside the select carries the chosen tag's colour (only a plain hex colour)
        assert "class='tagdot dx-tagdot' style='background:#37d67a'" in r.text
        r = await _post(b, "/decks/42/package", package="", color_tag="Odd,url(x)")
        assert "url(x)" not in r.text and "<option value='Odd' selected>Odd</option>" in r.text
    finally:
        await b.aclose()
