"""Archidekt-parity pages and change kinds: theme cookie, deck list controls, deck page views,
new deck / settings / clone forms, CSV export, and the set_finish / set_printing changes."""

from __future__ import annotations

import csv
import io

import pytest

from mtg_gateway import modes
from mtg_gateway.decks import DeckError, parse_changes, split_label
from mtg_gateway.scan.scryfall import ScryfallClient

from .fake_scryfall import FakeScryfall
from .test_decks_and_proxy import Browser, Stack, call, linked_user, stack, structured

__all__ = ["stack"]

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


async def _linked_browser(stack: Stack) -> Browser:
    b = Browser(stack.h)
    await b.login("/decks")
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    return b


async def test_theme_cookie_and_shell(stack: Stack) -> None:
    b = await _linked_browser(stack)
    try:
        csrf = await b.csrf()
        page = await b.http.get("/decks", headers=NAV)
        assert "<html lang='en'>" in page.text
        assert "class='tabbar'" in page.text and "Site theme" in page.text
        r = await b.http.post("/theme", data={"csrf": csrf, "theme": "light", "next": "/decks"})
        assert r.status_code == 303 and r.headers["location"] == "/decks"
        assert "mtg_theme=light" in r.headers["set-cookie"]
        page = await b.http.get("/decks", headers=NAV)
        assert "<html lang='en' data-theme=light>" in page.text
        # an unknown theme falls back to system (cookie cleared), an off-site next is ignored
        r = await b.http.post("/theme", data={"csrf": csrf, "theme": "neon", "next": "https://evil.test/"})
        assert r.status_code == 303 and r.headers["location"] == "/account"
        assert 'mtg_theme=""' in r.headers["set-cookie"] or "mtg_theme=;" in r.headers["set-cookie"]
        # no token: nothing is set
        r = await b.http.post("/theme", data={"theme": "dark"})
        assert r.status_code == 303 and "set-cookie" not in r.headers
        # T-046: "Desktop layout" is a cookie too; it asks the device for a wide viewport (as a
        # browser's Desktop site switch would) so the width queries pick the top bar, and the app's
        # forced rail is dropped. "Fit the screen" clears it.
        assert "Site layout" in page.text and "action='/layout'" in page.text
        r = await b.http.post("/layout", data={"csrf": csrf, "layout": "desktop", "next": "/decks"})
        assert r.status_code == 303 and "mtg_layout=desktop" in r.headers["set-cookie"]
        page = await b.http.get("/decks", headers=NAV)
        assert "<meta name='viewport' content='width=1100'>" in page.text
        assert "desktop" in page.text.split("<body class='")[1].split("'")[0]
        app_ua = {**NAV, "User-Agent": "Mozilla/5.0 (Linux; Android 14) MTGAssistant/1"}
        in_app = await b.http.get("/decks", headers=app_ua)
        body_cls = in_app.text.split("<body class='")[1].split("'")[0].split()
        assert "desktop" in body_cls and "app" not in body_cls
        r = await b.http.post("/layout", data={"csrf": csrf, "layout": "auto", "next": "/decks"})
        assert r.status_code == 303 and (
            'mtg_layout=""' in r.headers["set-cookie"] or "mtg_layout=;" in r.headers["set-cookie"]
        )
        page = await b.http.get("/decks", headers=NAV)
        assert "content='width=device-width, initial-scale=1, viewport-fit=cover'" in page.text
        r = await b.http.post("/layout", data={"layout": "desktop"})
        assert r.status_code == 303 and "set-cookie" not in r.headers
    finally:
        await b.aclose()


async def test_deck_list_controls_and_views(stack: Stack) -> None:
    # the fixture deck has no Scryfall ids; give the commander one so the page has art
    for row in stack.ark.decks[42]["cards"]:
        if row.get("categories") == ["Commander"]:
            row["card"]["uid"] = "be68e315-ffef-40a8-8a46-1c042b148c03"
    b = await _linked_browser(stack)
    try:
        lst = await b.http.get("/decks", headers=NAV)
        assert lst.status_code == 200
        assert "Total decks: 1" in lst.text and "decklist grid'" in lst.text
        assert "class='cbar'" in lst.text and "/decks/new" in lst.text and "Sample Commander Deck" in lst.text
        rows = await b.http.get("/decks?view=list&order=name&q=sample", headers=NAV)
        assert rows.status_code == 200 and "decklist list'" in rows.text
        none = await b.http.get("/decks?q=zzz", headers=NAV)
        assert none.status_code == 200 and "No decks yet matching" in none.text
        # the deck page: text, stacks and grid views; grouping and sorting; the local filter
        deck = await b.http.get("/decks/42", headers=NAV)
        assert deck.status_code == 200
        assert "class='banner'" in deck.text and "Quick add" in deck.text and "Clone deck" in deck.text
        # Archidekt's own playtester in its own tab (D-13); the simulation (the same run as
        # run_deck_report) is one click; the compare view is under More
        assert "href='https://archidekt.com/playtester-v2/42' target='_blank'" in deck.text
        assert "Run simulation" in deck.text
        assert "href='/decks/42/compare'" in deck.text and "Run deck report" not in deck.text
        assert "/decks/42/settings" in deck.text and "Deck stats" in deck.text
        assert "cards.scryfall.io" in deck.headers["content-security-policy"]
        for view in ("stacks", "grid"):
            r = await b.http.get(f"/decks/42?view={view}&group=type&sort=mv", headers=NAV)
            assert r.status_code == 200 and f"deckview {view}" in r.text, view
            assert "Creature" in r.text and "Land" in r.text
        filtered = await b.http.get("/decks/42?q=sol+ring", headers=NAV)
        # the card rows are filtered; the stats panel's draw-odds data still names every card
        assert (
            filtered.status_code == 200
            and "data-card='Sol Ring'" in filtered.text
            and "data-card='Acidic Slime'" not in filtered.text
        )
        bad = await b.http.get("/decks/42?view=nope&group=nope&sort=nope", headers=NAV)
        assert bad.status_code == 200  # unknown choices fall back to the defaults
        # the deck list caches the cover art once a deck has been opened, and its CSP lets the
        # browser load it (the covers are Scryfall images, like the deck page's)
        covers = stack.h.app.state.gateway.db.deck_covers(["42"])
        assert covers.get("42", {}).get("scryfall_uid")
        lst = await b.http.get("/decks", headers=NAV)
        assert "https://cards.scryfall.io/art_crop/" in lst.text
        assert "img-src 'self' https://cards.scryfall.io" in lst.headers["content-security-policy"]
    finally:
        await b.aclose()


async def test_new_deck_settings_and_clone_forms(stack: Stack) -> None:
    ark = stack.ark
    b = await _linked_browser(stack)
    try:
        csrf = await b.csrf()
        form = await b.http.get("/decks/new", headers=NAV)
        assert form.status_code == 200 and "Super awesome deck name 2000" in form.text
        r = await b.http.post(
            "/decks/new",
            data={
                "csrf": csrf,
                "name": "Fresh brew",
                "format": "commander",
                "private": "1",
                "source": "1 Sol Ring",
            },
        )
        # the member's own form is their approval: created at once, the new deck opens
        loc = r.headers["location"]
        assert r.status_code == 303 and loc.startswith("/decks/") and loc.endswith("?ok=created"), r.text
        page = await b.http.get(loc, headers=NAV)
        assert page.status_code == 200 and "Fresh brew" in page.text and "Created on Archidekt" in page.text
        new_deck = int(loc.split("/")[2].split("?")[0])
        assert ark.decks[new_deck]["name"] == "Fresh brew"
        # settings: a details proposal, applied at once
        settings = await b.http.get("/decks/42/settings", headers=NAV)
        assert settings.status_code == 200 and "Unlisted" in settings.text and "Categories" in settings.text
        r = await b.http.post(
            "/decks/42/settings",
            data={
                "csrf": csrf,
                "name": "Sample Commander Deck",
                "deck_format": "commander",
                "edh_bracket": "3",
                "description": "Now with a primer.",
                "private": "",
                "unlisted": "",
            },
        )
        assert r.status_code == 303 and r.headers["location"] == "/decks/42?ok=saved", r.text
        page = await b.http.get(r.headers["location"], headers=NAV)
        assert "Now with a primer." in page.text and "Saved to Archidekt" in page.text
        # recorded as an applied proposal, with its snapshot, so History can undo it
        listed = (await b.http.get("/api/v1/proposals")).json()["proposals"]
        assert any(x["kind"] == "details" and x["state"] == "applied" for x in listed), listed
        # nothing changed: the form comes back with the reason
        same = await b.http.post(
            "/decks/42/settings",
            data={
                "csrf": csrf,
                "name": "Sample Commander Deck",
                "deck_format": "commander",
                "edh_bracket": "3",
                "description": "Now with a primer.",
                "private": "",
                "unlisted": "",
            },
        )
        assert same.status_code == 400 and "nothing" in same.text.lower()
        # clone: copies the deck into the root folder at once and opens the copy
        r = await b.http.post("/decks/42/clone", data={"csrf": csrf})
        loc = r.headers["location"]
        assert r.status_code == 303 and loc.startswith("/decks/") and loc.endswith("?ok=created"), r.text
        new_id = int(loc.split("/")[2].split("?")[0])
        assert ark.decks[new_id]["name"] == "Copy of - Sample Commander Deck"
        assert len(ark.decks[new_id]["cards"]) == len(ark.decks[42]["cards"])
        # someone else's deck cannot be cloned or configured through the forms
        other = await b.http.post("/decks/43/clone", data={"csrf": csrf})
        assert other.status_code == 303 and other.headers["location"].startswith("/decks/43?err=")
        assert (await b.http.get("/decks/43/settings", headers=NAV)).status_code in (403, 404)
        # a form without its token is refused
        assert (await b.http.post("/decks/42/clone", data={})).status_code == 403
    finally:
        await b.aclose()


async def test_csv_export_round_trips(stack: Stack) -> None:
    from mtg_gateway.archidekt_csv import parse_export

    b = await _linked_browser(stack)
    try:
        page = await b.http.get("/decks/42/export", headers=NAV)
        assert page.status_code == 200 and "/decks/42/export.csv" in page.text
        r = await b.http.get("/decks/42/export.csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert r.headers["content-disposition"].endswith('.csv"')
        rows = list(csv.reader(io.StringIO(r.text)))
        assert rows[0][:3] == ["Quantity", "Name", "Finish"] and "Scryfall ID" in rows[0]
        cards = parse_export(r.text)
        assert sum(c.quantity for c in cards) == sum(c["quantity"] for c in stack.ark.decks[42]["cards"])
        assert any(c.name == "Sol Ring" and c.set_code == "cmr" for c in cards)
    finally:
        await b.aclose()


async def test_editor_page_carries_printing_data_and_quick_add(stack: Stack) -> None:
    h = stack.h
    h.app.state.gateway.scan.scryfall = ScryfallClient(
        "https://scryfall.test", min_interval=0.0, http=FakeScryfall().client()
    )
    b = await _linked_browser(stack)
    try:
        edit = await b.http.get("/decks/42/edit?add=Arcane+Signet", headers=NAV)
        assert edit.status_code == 200, edit.text
        cfg = __import__("json").loads(
            edit.text.split("id='editor-config' type='application/json'>")[1].split("</script>")[0]
        )
        assert cfg["prefill"] == [{"action": "add", "card_name": "Arcane Signet", "quantity": 1}]
        card = next(c for c in cfg["cards"] if c["name"] == "Sol Ring")
        assert (
            card["modifier"] == "Normal" and card["set_code"] == "cmr" and card["collector_number"] == "472"
        )
        assert cfg["writesEnabled"] is True and cfg["maxChanges"] == 40
        assert "<template id='icon-swap'>" in edit.text and "class='editbar'" in edit.text
        assert "Save changes" in edit.text and "Undo" in edit.text
        # uncategorised cards sit under their auto category, never a bare "Other"
        assert [g["name"] for g in cfg["groups"]][0] == "Commander"
        assert "Other" not in [g["name"] for g in cfg["groups"]]
    finally:
        await b.aclose()


async def test_set_finish_and_set_printing_changes(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    # a finish change: diff rows name it, the apply sends one modify with the new modifier
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [{"action": "set_finish", "card_name": "Sol Ring", "finish": "foil"}],
            },
        )
    )
    assert p["ok"], p
    assert "Sol Ring" in p["diff"] and "foil" in p["diff"].lower()
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"], a
    assert a["state"] == "applied" and a["result"]["verified"] is True
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    assert row["modifier"] == "Foil"
    # no-op finish is refused as "nothing to change"
    same = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [{"action": "set_finish", "card_name": "Sol Ring", "finish": "foil"}],
            },
        )
    )
    assert not same["ok"]
    # a printing change: the row is removed and added back as the other printing, same quantity.
    # SLD 1074 only comes Etched, so carrying the Foil row over is refused and nothing is sent...
    swap = {"action": "set_printing", "card_name": "Sol Ring", "set_code": "sld", "collector_number": "1074"}
    p2 = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [swap]}))
    assert p2["ok"], p2
    assert "SLD" in p2["diff"].upper()
    before = len(ark.patches)
    refused = structured(await call(h, token, "apply_proposal", {"proposal_id": p2["proposal_id"]}))
    assert not refused["ok"] and "does not come in Foil" in refused["message"], refused
    assert len(ark.patches) == before
    # ...while naming the finish the printing offers goes through
    p2 = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{**swap, "finish": "etched"}]},
        )
    )
    assert p2["ok"], p2
    a2 = structured(await call(h, token, "apply_proposal", {"proposal_id": p2["proposal_id"]}))
    assert a2["ok"], a2
    assert a2["result"]["verified"] is True
    rows = [c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring"]
    assert len(rows) == 1 and rows[0]["card"]["edition"]["editioncode"] == "sld" and rows[0]["quantity"] == 1
    assert rows[0]["modifier"] == "Etched"
    # the clash rules: a count change and a printing change of one card do not mix
    bad = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "set_finish", "card_name": "Forest", "finish": "foil"},
                    {"action": "set_quantity", "card_name": "Forest", "quantity": 10},
                ],
            },
        )
    )
    assert not bad["ok"] and "same proposal" in bad["message"]
    unknown = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [{"action": "set_finish", "card_name": "Black Lotus", "finish": "foil"}],
            },
        )
    )
    assert not unknown["ok"]


def rows_now(ark) -> list[dict]:
    return [c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring"]


async def test_printing_swap_keeps_finish_categories_and_companion(stack: Stack) -> None:
    """A set_printing without a finish carries each row's finish over; a card filed in two
    categories keeps both rows, and the adds go out before the removes. A printing that does not
    come in a row's finish is refused before anything is sent."""
    from . import fake_archidekt

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    row["modifier"] = "Foil"
    row["categories"] = ["Ramp"]
    row["companion"] = True
    ark.decks[42]["cards"].append(
        {
            **{k: v for k, v in row.items() if k != "id"},
            "id": 1999,
            "modifier": "Normal",
            "categories": ["Artifacts"],
        }
    )
    swap = {"action": "set_printing", "card_name": "Sol Ring", "set_code": "sld", "collector_number": "1074"}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [swap]}))
    assert p["ok"], p
    assert "Foil / Normal" in p["diff"] or "Normal / Foil" in p["diff"]
    before = len(ark.patches)
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert not a["ok"] and "does not come in" in a["message"] and "Etched" in a["message"], a
    assert len(ark.patches) == before
    # an explicit finish the printing does not offer is refused the same way
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [{**swap, "set_code": "cmr", "collector_number": "472", "finish": "etched"}],
            },
        )
    )
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert not a["ok"] and "does not come in Etched" in a["message"], a
    assert len(ark.patches) == before
    # a printing that offers both finishes keeps each row's finish, categories and companion flag
    # (the deck's rows are the CMR printing already, so file them as another printing first)
    for c in rows_now(ark):
        c["card"] = {**c["card"], "id": 9003, "edition": {"editioncode": "msc"}, "collectorNumber": "211"}
    cmr = next(x for x in fake_archidekt.PRINTINGS if x["id"] == 91043)
    saved = list(cmr["options"])
    cmr["options"] = ["Normal", "Foil"]
    try:
        p = structured(
            await call(
                h,
                token,
                "propose_deck_changes",
                {"deck_id": "42", "changes": [{**swap, "set_code": "cmr", "collector_number": "472"}]},
            )
        )
        assert p["ok"], p
        a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert a["ok"], a
        assert a["result"]["verified"] is True
    finally:
        cmr["options"] = saved
    rows = [c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring"]
    assert len(rows) == 2 and {c["card"]["edition"]["editioncode"] for c in rows} == {"cmr"}
    assert sorted((c["categories"][0], c["modifier"]) for c in rows) == [
        ("Artifacts", "Normal"),
        ("Ramp", "Foil"),
    ]
    assert all(c["card"]["id"] == 91043 for c in rows)
    sent = [e for p_ in ark.patches for e in p_.get("cards", [p_])] if ark.patches else []
    actions = [e["action"] for e in sent if isinstance(e, dict) and e.get("cardid") in (91043, 9003)]
    assert actions and actions.index("add") < actions.index("remove")


async def test_csv_export_neutralises_formula_cells(stack: Stack) -> None:
    """Cells that would be read as formulas by a spreadsheet are prefixed with an apostrophe,
    and the importer strips it again."""
    from mtg_gateway.archidekt_csv import parse_export
    from mtg_gateway.deckpage import image_url

    ark = stack.ark
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    row["categories"] = ['=HYPERLINK("https://evil.test")', "@SUM(1)"]
    row["label"] = "-1+1"
    b = await _linked_browser(stack)
    try:
        r = await b.http.get("/decks/42/export.csv")
        assert r.status_code == 200
        assert "'=HYPERLINK" in r.text and "'@SUM" in r.text and "'-1+1" in r.text
        assert ",=HYPERLINK" not in r.text and '"=HYPERLINK' not in r.text
        cards = parse_export(r.text)
        sol = next(c for c in cards if c.name == "Sol Ring")
        assert sol.category == '=HYPERLINK("https://evil.test")'
    finally:
        await b.aclose()
    assert image_url("be68e315-ffef-40a8-8a46-1c042b148c03\n") is None
    assert image_url("be68e315-ffef-40a8-8a46-1c042b148c03") is not None


async def test_settings_and_new_deck_form_edge_cases(stack: Stack) -> None:
    b = await _linked_browser(stack)
    try:
        csrf = await b.csrf()
        # a huge bracket value is just "no bracket", not a 500
        r = await b.http.post(
            "/decks/42/settings",
            data={
                "csrf": csrf,
                "name": "Sample Commander Deck",
                "description": "",
                "edh_bracket": "9" * 5000,
            },
        )
        assert r.status_code == 400 and "nothing" in r.text.lower()
        # an unchecked box stays unchecked when the form comes back with an error
        r = await b.http.post(
            "/decks/42/settings",
            data={"csrf": csrf, "name": "", "description": "", "edh_bracket": "", "unlisted": "1"},
        )
        assert r.status_code == 400
        form = r.text.split("name='private'")[1].split(">")[0]
        assert "checked" not in form
        assert "name='unlisted' value='1' checked" in r.text
        r = await b.http.post(
            "/decks/new", data={"csrf": csrf, "name": "", "format": "commander", "source": ""}
        )
        assert r.status_code == 400 and "name='private' value='1'>" in r.text
        # a clone name that is not a string is refused
        bad = await b.http.post(
            "/api/v1/proposals",
            json={"kind": "clone", "deck_id": "42", "name": ["x"]},
            headers={"X-CSRF-Token": csrf},
        )
        assert bad.status_code == 400 and bad.json()["error"] == "invalid"
    finally:
        await b.aclose()


async def test_settings_for_a_deck_without_a_format_keep_it_unset(stack: Stack) -> None:
    # The format list used to fall back to its first entry, so saving any other setting quietly
    # gave a deck with no format "1v1 Commander".
    ark = stack.ark
    ark.decks[42]["deckFormat"] = None
    b = await _linked_browser(stack)
    try:
        csrf = await b.csrf()
        page = await b.http.get("/decks/42/settings", headers=NAV)
        assert page.status_code == 200
        assert "<option value='' selected>No format set</option>" in page.text
        r = await b.http.post(
            "/decks/42/settings",
            data={
                "csrf": csrf,
                "name": "Sample Commander Deck",
                "deck_format": "",
                "edh_bracket": "",
                "description": "Only the description changes.",
                "private": "",
                "unlisted": "",
            },
        )
        assert r.status_code == 303 and r.headers["location"] == "/decks/42?ok=saved", r.text
        assert ark.decks[42]["deckFormat"] is None
        assert ark.decks[42]["description"] == "Only the description changes."
    finally:
        await b.aclose()


async def test_set_label_puts_and_takes_off_a_colour_tag(stack: Stack) -> None:
    """set_label (0.7.19): Archidekt's colour tag on every row of a card, sent in
    modifications.label as "Name,#rrggbb" (its own editor's shape), verified on the re-read."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)

    async def propose(change: dict) -> dict:
        args = {"deck_id": "42", "changes": [change]}
        return structured(await call(h, token, "propose_deck_changes", args))

    p = await propose({"action": "set_label", "name": "Sol Ring", "label": "Have", "color": "#37D67A"})
    assert p["ok"], p
    assert "Sol Ring: colour tag no tag -> Have (#37d67a)" in p["diff"]
    before = len(ark.patches)
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"] is True, a
    sent = [e for patch in ark.patches[before:] for e in patch.get("cards", [patch])]
    assert any(e.get("modifications", {}).get("label") == "Have,#37d67a" for e in sent), sent
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    assert row["label"] == "Have,#37d67a" and row["quantity"] == 1
    # the same tag again is nothing to change
    assert not (await propose({"action": "set_label", "name": "Sol Ring", "label": "Have,#37d67a"}))["ok"]
    # a bad colour or a comma in the name is refused before anything is stored
    bad = await propose({"action": "set_label", "name": "Sol Ring", "label": "Have", "color": "red"})
    assert not bad["ok"]
    bad = await propose({"action": "set_label", "name": "Sol Ring", "label": "a,b", "color": "#000000"})
    assert not bad["ok"]
    # an empty label takes the tag off
    off = await propose({"action": "set_label", "name": "Sol Ring", "label": ""})
    assert off["ok"] and "Have (#37d67a) -> no tag" in off["diff"], off
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": off["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"] is True, a
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    assert not row.get("label")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"label": "a,b", "color": "#000000"}, "without commas"),
        ({"label": "x" * 41}, "at most 40"),
        ({"label": "Have", "color": "red"}, "#37d67a"),
        ({"label": "Have", "color": "#12345"}, "#37d67a"),
        ({}, "needs a label"),
        ({"label": "Have", "quantity": 2}, "takes only"),
    ],
)
def test_set_label_is_refused_when_malformed(change: dict, message: str) -> None:
    with pytest.raises(DeckError) as err:
        parse_changes([{"action": "set_label", "card_name": "Sol Ring", **change}])
    assert message in str(err.value)


def test_set_label_reads_archidekts_own_form_and_short_colours() -> None:
    (ch,) = parse_changes([{"action": "set_label", "card_name": "Sol Ring", "label": "Have,#37D67A"}])
    assert ch.label == "Have,#37d67a"
    assert split_label("Proxy,#fff") == ("Proxy", "#ffffff")
    assert split_label("X,url(evil)") == ("X", "")  # never reaches a style attribute
    (side,) = parse_changes([{"action": "set_label", "card_name": "Sol Ring", "label": "", "zone": "side"}])
    assert side.zone == "side" and side.label == ""
    assert modes.risk_of("edit", [{"kind": "label", "name": "Sol Ring"}])[0] == "low"


async def test_set_label_on_the_maybeboard_keeps_the_companion_and_escapes_the_name(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.add_side_row(42, "Sol Ring")
    main = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Cultivate")
    main["companion"] = True
    hostile = "<img src=x onerror=1>'\""
    for change in (
        {"action": "set_label", "name": "Sol Ring", "label": hostile[:40], "zone": "side"},
        {"action": "set_label", "name": "Cultivate", "label": "Have"},
    ):
        p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
        assert p["ok"], p
        a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert a["ok"] and a["result"]["verified"] is True, a
    side = [
        c
        for c in ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == "Sol Ring" and c["categories"] == ["Maybeboard"]
    ]
    assert side[0]["label"].startswith("<img")
    assert main["companion"] is True and main["label"] == "Have,#656565"
    b = await _linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42?view=text", headers=NAV)).text
        assert "<img src=x" not in page and "&lt;img src=x" in page
    finally:
        await b.aclose()


async def test_set_label_that_archidekt_ignores_is_not_reported_verified(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    row["label"] = "Have,#37d67a"
    ark.empty_label_ignored = True
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "set_label", "name": "Sol Ring", "label": ""}]},
        )
    )
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert not (a.get("ok") and (a.get("result") or {}).get("verified") is True), a
    assert row["label"] == "Have,#37d67a"


async def test_set_mana_value_sets_and_clears_the_custom_mana_value(stack: Stack) -> None:
    """set_mana_value (0.7.20): Archidekt's custom mana value on every row of a card, sent in
    modifications.customCmc as its own editor does, verified on the re-read; null clears it.
    A later edit of the row (its quantity) keeps the value and the colour tag."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)

    async def propose(change: dict) -> dict:
        args = {"deck_id": "42", "changes": [change]}
        return structured(await call(h, token, "propose_deck_changes", args))

    p = await propose({"action": "set_mana_value", "name": "Sol Ring", "mana_value": 3})
    assert p["ok"] and p["risk"] == "low", p
    assert "Sol Ring: custom mana value none -> 3" in p["diff"]
    before = len(ark.patches)
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"] is True, a
    sent = [e for patch in ark.patches[before:] for e in patch.get("cards", [patch])]
    assert any(e.get("modifications", {}).get("customCmc") == 3 for e in sent), sent
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    assert row["customCmc"] == 3
    # the same value again is nothing to change
    assert not (await propose({"action": "set_mana_value", "name": "Sol Ring", "mana_value": 3}))["ok"]
    # another edit of the row carries the value and a tag along, as Archidekt's own editor does
    row["label"] = "Have,#37d67a"
    q = await propose({"action": "set_quantity", "name": "Sol Ring", "quantity": 2})
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": q["proposal_id"]}))
    assert a["ok"], a
    assert row["customCmc"] == 3 and row["label"] == "Have,#37d67a" and row["quantity"] == 2
    off = await propose({"action": "set_mana_value", "name": "Sol Ring", "mana_value": None})
    assert off["ok"] and "custom mana value 3 -> none" in off["diff"], off
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": off["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"] is True, a
    assert row["customCmc"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"mana_value": -1}, "0 to 20"),
        ({"mana_value": 21}, "0 to 20"),
        ({"mana_value": 2.5}, "0 to 20"),
        ({"mana_value": "x"}, "0 to 20"),
        ({"mana_value": True}, "0 to 20"),
        ({}, "needs mana_value"),
        ({"mana_value": 2, "quantity": 2}, "takes only"),
    ],
)
def test_set_mana_value_is_refused_when_malformed(change: dict, message: str) -> None:
    with pytest.raises(DeckError) as err:
        parse_changes([{"action": "set_mana_value", "card_name": "Sol Ring", **change}])
    assert message in str(err.value)


def test_set_mana_value_is_low_risk_and_works_on_the_maybeboard() -> None:
    (ch,) = parse_changes(
        [{"action": "set_mana_value", "card_name": "Sol Ring", "mana_value": "4", "zone": "side"}]
    )
    assert ch.mana_value == 4 and ch.zone == "side"
    assert ch.as_dict() == {
        "action": "set_mana_value",
        "card_name": "Sol Ring",
        "mana_value": 4,
        "zone": "side",
    }
    assert modes.risk_of("edit", [{"kind": "mana_value", "name": "Sol Ring"}])[0] == "low"
