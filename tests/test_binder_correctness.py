"""The Binder finds every record and adds up presses (0.7.46). A record is found by Archidekt's own
name filter, not by paging the newest thousand, so the oldest entry of a big binder can be changed
and adding its printing again adds to it. The stepper sends one more or one fewer, so two tabs that
each press + add two copies. An import runs in the background with its progress on the page, and
waits when the member's Archidekt budget runs low instead of failing part way."""

from __future__ import annotations

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked

stack_options = stack_with(COLLECTION)

SLIME = 9010  # Acidic Slime: none of the seeded rows


def _seed(stack: Stack, n: int) -> None:
    """``n`` records newer than the binder's oldest entry (id 1, two Acidic Slime)."""
    ark = stack.ark
    rows = ark.collections.setdefault("alice", {})
    rows[1] = ark._collection_row(1, ark.printing(SLIME), {"quantity": 2})
    pks = (9003, 9001, 9002, 9005, 9008)
    ark.next_coll_id = max(ark.next_coll_id, 1)
    for i in range(n):
        ark.next_coll_id += 1
        body = {
            "quantity": 1,
            "modifier": ("Normal", "Foil", "Etched")[i // 5 % 3],
            "condition": 1 + i // 15 % 5,
        }
        row = ark._collection_row(ark.next_coll_id, ark.printing(pks[i % 5]), body)
        rows[row["id"]] = row


def _slime(stack: Stack) -> dict[int, int]:
    return {
        rid: r["quantity"]
        for rid, r in stack.ark.collections["alice"].items()
        if r["card"]["oracleCard"]["name"] == "Acidic Slime"
    }


async def test_the_oldest_entry_of_a_big_binder_can_be_changed_and_added_to(stack: Stack) -> None:
    _seed(stack, 1_050)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        reads = len(stack.ark.collection_queries)
        r = await api(b, "POST", "/collection/api/rows/1", {"quantity": 3, "name": "Acidic Slime"}, csrf=csrf)
        assert r.status_code == 200, r.text
        assert r.json()["row"]["quantity"] == 3 and _slime(stack) == {1: 3}
        assert len(stack.ark.collection_queries) - reads == 1  # one read by name, not ten pages
        assert stack.ark.collection_queries[-1].get("cardName") == "Acidic Slime"
        # adding the same printing again adds to that record: no second record
        r = await api(b, "POST", "/collection/api/add", {"items": [{"name": "Acidic Slime"}]}, csrf=csrf)
        assert r.status_code == 200, r.text
        assert _slime(stack) == {1: 4}, _slime(stack)
        # the page's own forms (no script) send the name too
        page = (await b.http.get("/collection?q=Acidic")).text
        assert (
            "<input type='hidden' name='id' value='1'><input type='hidden' name='name' value='Acidic Slime'>"
            in page
        )
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "dec", "id": "1", "name": "Acidic Slime"}
        )
        assert r.status_code == 303 and "err=" not in r.headers["location"], r.headers
        assert _slime(stack) == {1: 3}
    finally:
        await b.aclose()


async def test_a_small_binder_is_read_once_for_a_whole_add(stack: Stack) -> None:
    _seed(stack, 20)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        reads = len(stack.ark.collection_queries)
        items = [{"name": n} for n in ("Acidic Slime", "Sol Ring", "Opt", "Swamp")]
        r = await api(b, "POST", "/collection/api/add", {"items": items}, csrf=csrf)
        assert r.status_code == 200, r.text
        assert len(stack.ark.collection_queries) - reads == 1
        assert _slime(stack) == {1: 3}
    finally:
        await b.aclose()


async def test_two_tabs_each_pressing_plus_add_two_copies(stack: Stack) -> None:
    _seed(stack, 3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        # both tabs show 2 copies; each sends one more
        for _tab in range(2):
            r = await api(
                b, "POST", "/collection/api/rows/1", {"delta": 1, "name": "Acidic Slime"}, csrf=csrf
            )
            assert r.status_code == 200, r.text
        assert _slime(stack) == {1: 4}
        r = await api(b, "POST", "/collection/api/rows/1", {"delta": -4, "name": "Acidic Slime"}, csrf=csrf)
        assert r.status_code == 200 and r.json()["row"] is None and _slime(stack) == {}
        bad = await api(b, "POST", "/collection/api/rows/2", {"delta": "1"}, csrf=csrf)
        assert bad.status_code == 400
    finally:
        await b.aclose()


async def _poll(b, url: str, until: tuple[str, ...], *, tries: int = 400) -> dict:
    import asyncio

    for _ in range(tries):
        d = (await b.http.get(url)).json()
        if d.get("state") in until:
            return d
        await asyncio.sleep(0.02)
    raise AssertionError(f"never reached {until}: {d}")


async def test_an_import_runs_in_the_background_and_waits_for_room(stack: Stack, monkeypatch) -> None:
    """30 lines cost about 60 Archidekt calls; with 40 a window the import adds what it can, keeps a
    quarter back for the member's own changes, says it is paused, then goes on when there is room."""
    from mtg_gateway import collection
    from mtg_gateway.decks import RateBudget

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    _seed(stack, 3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        stack.gw.decks.archidekt_budget = RateBudget(40)
        text = "Quantity,Name\n" + "\n".join("1,Acidic Slime" for _ in range(30))  # 30 rows, kept apart
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        assert r.status_code == 303, r.text
        where = r.headers["location"]
        assert where.startswith("/collection?import="), where  # back at once, with the job
        jid = where.split("=", 1)[1]
        page = (await b.http.get(where)).text
        assert f"data-import='{jid}'" in page and "lines done" in page
        paused = await _poll(b, f"/collection/api/import/{jid}", ("paused",))
        assert "Paused" in paused["text"] and 0 < paused["done"] < 30, paused
        assert "minute" not in paused["text"] and "second" not in paused["text"]
        # the member still has room for their own changes while it waits
        assert (
            stack.gw.decks.archidekt_budget.room(next(iter(stack.gw.decks.archidekt_budget._buckets))) >= 10
        )
        r = await api(b, "POST", "/collection/api/rows/1", {"delta": 1, "name": "Acidic Slime"}, csrf=csrf)
        assert r.status_code == 200, r.text
        stack.gw.decks.archidekt_budget = RateBudget(1_000)  # room again
        done = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert done["state"] == "done" and done["done"] == 30, done
        assert _slime(stack) == {1: 33}  # 2, 30 imported and the member's own +1
        landed = await b.http.get(done["next"])
        assert "Imported 30 cards into your binder." in landed.text
    finally:
        await b.aclose()


async def test_an_import_that_stops_says_what_was_added(stack: Stack, monkeypatch) -> None:
    from mtg_gateway import collection

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    _seed(stack, 3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        writes = {"n": 0}
        orig = stack.ark.transport.handler

        def failing(req):
            if req.method in ("PUT", "PATCH") and "/collection/" in req.url.path:
                writes["n"] += 1
                if writes["n"] > 4:
                    import httpx

                    return httpx.Response(500, json={"detail": "down"})
            return orig(req)

        stack.ark.transport.handler = failing
        text = "Quantity,Name\n" + "\n".join("1,Acidic Slime" for _ in range(10))
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        jid = r.headers["location"].split("=", 1)[1]
        done = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert done["state"] == "failed", done
        landed = (await b.http.get(done["next"])).text
        assert "Imported 4 cards" in landed and "stopped" in landed, landed[:2000]
        assert "HTTP" not in done["text"] and "500" not in done["text"]
    finally:
        stack.ark.transport.handler = orig
        await b.aclose()


async def test_the_add_box_takes_up_to_9999_copies(stack: Stack) -> None:
    b = await linked(stack)
    try:
        page = (await b.http.get("/collection?add=1")).text
        assert "id='addqty' type='number' name='quantity' value='1' min='1' max='9999'" in page
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "Acidic Slime", "quantity": "1500"}
        )
        assert r.status_code == 303 and "ok=added" in r.headers["location"], r.headers
        assert list(_slime(stack).values()) == [1500]
        # a count typed before the name still counts when Copies is left at 1
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "3 Acidic Slime", "quantity": "1"}
        )
        assert list(_slime(stack).values()) == [1503]
    finally:
        await b.aclose()


# -- the held Binder after adds (0.7.46 final polish Q2) -------------------------------------------

# ten new records: five cards the binder doesn't have, each in two conditions
ADDED = [
    {"name": n, "condition": c}
    for n in ("Forest", "Island", "Opt", "Swamp", "Aesi, Tyrant of Gyre Strait")
    for c in ("NM", "LP")
]


def _held(stack: Stack, b, path: str):
    """``path`` read while Archidekt doesn't answer (the caller opens it again): the Binder as it
    was last read."""
    import time

    stack.gw.collection.first_page_ttl = 0
    stack.gw.decks.client.pacer._open_until = time.monotonic() + 120
    return b.http.get(path)


def _rows(text: str) -> list[tuple[int, str]]:
    """The Binder list's rows, top to bottom: (record id, the row as drawn)."""
    import re

    return [
        (int(m.group(1)), m.group(0))
        for m in re.finditer(
            r"<li class='tr row' role='row' data-name='[^']*' data-id='(\d+)'.*?</li>", text, re.S
        )
    ]


async def _add_ten_then_go_down(stack: Stack, reads: tuple[str, ...]):
    """Read each of ``reads``, add ADDED one card at a time (as a scan does), then a second copy of
    the last one (a card added since) and Sol Ring (a card the binder had, if it has: its record
    takes it). Returns the browser and the new records' ids, in the order added."""
    b = await linked(stack)
    csrf = await b.csrf("/collection")
    for path in reads:
        assert (await b.http.get(path)).status_code == 200, path
    ids = []
    for item in [*ADDED, ADDED[-1], {"name": "Sol Ring"}]:
        r = await api(b, "POST", "/collection/api/add", {"items": [item], "source": "scan"}, csrf=csrf)
        assert r.status_code == 200 and len(r.json().get("added") or []) == 1, (item, r.text[:400])
        row = r.json()["added"][0]
        if row["went_to"] == "new":
            ids.append(int(row["id"]))
    return b, ids


async def test_the_held_binder_shows_the_cards_added_since_first_in_recently_added(stack: Stack) -> None:
    """The member read the Binder, added 10 cards, then Archidekt went down. In Recently added (the
    default sort) the held Binder shows the 10 first, newest first, each marked as added since and
    each once (the second copy is its row's 2 copies), then the cards it read (Sol Ring, added to,
    in its place with its 2 copies, not marked); the strip says so; no price the gateway doesn't
    have."""
    import re

    _seed(stack, 3)
    newest_first = "/collection?view=list&sort=added&dir=desc"
    b, ids = await _add_ten_then_go_down(stack, (newest_first,))
    try:
        assert len(ids) == 10, ids
        read = sorted(stack.ark.collections["alice"], reverse=True)
        read = [i for i in read if i not in ids]
        held = await _held(stack, b, newest_first)
        assert held.status_code == 200, held.text[:300]
        strip = re.search(r"<p class='notice warn held' role='status'>(.*?)</p>", held.text, re.S)
        assert strip and "as it was read" in strip.group(1), held.text[:400]
        assert "Cards you added since are at the top." in strip.group(1), strip.group(1)
        rows = _rows(held.text)
        assert [i for i, _ in rows] == ids[::-1] + read, ([i for i, _ in rows], ids, read)
        assert all("Added since" in r for _, r in rows[:10]), rows[0][1][:600]
        assert not any("Added since" in r for _, r in rows[10:])
        copies = re.compile(r"<output aria-label='Copies'>(\d+)</output>")
        assert copies.findall(rows[0][1]) == ["2"], rows[0][1][:800]  # the last one, twice
        ring = next(r for _, r in rows if ">Sol Ring<" in r)
        assert copies.findall(ring) == ["2"] and "Added since" not in ring, ring[:800]
        # a price only when the gateway has one: none is known for these, so none shows
        for _, r in rows[:10]:
            assert "$" not in r, r[:800]
        assert "No cards yet" not in held.text
    finally:
        stack.gw.decks.client.pacer._open_until = 0
        await b.aclose()


async def test_the_held_binder_in_another_sort_says_how_many_added_cards_it_leaves_out(stack: Stack) -> None:
    """In any other order (Set release here; the Binder has no name sort) the place of a card added
    since isn't known: the held Binder shows the cards it read and one line saying how many added
    cards aren't shown in this order. An empty binder read before the adds never says "No cards
    yet" while the gateway knows of added cards."""
    stack.ark.collections["alice"] = {}
    by_set = "/collection?view=list&sort=edition&dir=desc"
    newest_first = "/collection?view=list&sort=added&dir=desc"
    b, ids = await _add_ten_then_go_down(stack, (by_set, newest_first))
    try:
        assert len(ids) == 11, ids  # Sol Ring is new to an empty binder too
        held = await _held(stack, b, by_set)
        assert held.status_code == 200, held.text[:300]
        assert "No cards yet" not in held.text
        assert "11 cards you added aren\u2019t shown in this order." in held.text, held.text[-3000:]
        assert _rows(held.text) == []
        assert "<p class='muted col-nomatch' hidden>" in held.text
        # the default sort shows them, so it isn't empty either
        held = await _held(stack, b, newest_first)
        assert "No cards yet" not in held.text
        assert [i for i, _ in _rows(held.text)] == ids[::-1]
    finally:
        stack.gw.decks.client.pacer._open_until = 0
        await b.aclose()


# -- the held Binder's counts, its line and the price sort (0.7.46 audit B3, B4, B6) ---------------


# three new records whatever _seed made (it has no Forest and no Opt)
THREE = [ADDED[0], ADDED[1], ADDED[4]]


def _counts(text: str) -> dict[str, int]:
    """Every count the Binder page shows of its entries: the Cards tab, the At a glance line, the
    Entries tile, the results bar and the Load more line."""
    import re

    def one(pattern: str) -> int:
        m = re.search(pattern, text, re.S)
        assert m, pattern
        return int(next(g for g in m.groups() if g is not None).replace(",", ""))

    return {
        "tab": one(r"<a href='/collection' aria-current=page>Cards<span class='cn'>([\d,]+)</span>"),
        "glance": one(r"<span>([\d,]+) entries · \d+ added this week</span>"),
        "tile": one(r"Entries</span><span class='big'>([\d,]+)</span>"),
        "bar": one(r"id='col-count' aria-live='polite'>([\d,]+) entries</h2>"),
        "more": one(r"<p class='muted'>\d+ of ([\d,]+)</p>|All ([\d,]+) entries shown"),
    }


def _week(text: str) -> tuple[int, int]:
    """Added this week: the tile and the At a glance line."""
    import re

    tile = re.search(r"Added this week</span><span class='big'>(\d+)</span>", text)
    line = re.search(r"entries · (\d+) added this week</span>", text)
    assert tile and line
    return int(tile.group(1)), int(line.group(1))


async def _then_down(stack: Stack, reads: tuple[str, ...], items: list[dict], remove: tuple[int, ...] = ()):
    """Read each of ``reads``, add ``items`` one at a time and remove the records ``remove``; the
    caller then reads the pages while Archidekt is down (_held). Returns the browser and the new
    records' ids."""
    b = await linked(stack)
    csrf = await b.csrf("/collection")
    for path in reads:
        assert (await b.http.get(path)).status_code == 200, path
    ids = []
    for item in items:
        r = await api(b, "POST", "/collection/api/add", {"items": [item], "source": "scan"}, csrf=csrf)
        assert r.status_code == 200 and len(r.json().get("added") or []) == 1, (item, r.text[:400])
        ids.append(int(r.json()["added"][0]["id"]))
    for rid in remove:
        name = stack.ark.collections["alice"][rid]["card"]["oracleCard"]["name"]
        r = await b.http.delete(f"/collection/api/rows/{rid}?name={name}", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
    return b, ids


async def test_every_count_on_the_held_binder_agrees_after_adds_and_an_off_page_removal(stack: Stack) -> None:
    """B3: 70 entries read; 3 added and the oldest (far off the screen) removed, then Archidekt is
    down. The Cards tab, the At a glance line, the Entries tile, the results bar and Load more all
    say 72 in Recently added and in Set release alike; Added this week counts the 3 too."""
    _seed(stack, 69)
    newest = "/collection?view=list&sort=added&dir=desc"
    by_set = "/collection?view=list&sort=edition&dir=desc"
    b, ids = await _then_down(stack, (by_set, newest), THREE, remove=(1,))
    try:
        assert len(stack.ark.collections["alice"]) == 72
        for path in (newest, by_set):
            held = await _held(stack, b, path)
            assert held.status_code == 200 and "as it was read" in held.text, path
            counts = _counts(held.text)
            assert set(counts.values()) == {72}, (path, counts)
            assert _week(held.text) == (72, 72), (path, _week(held.text))
        held = await _held(stack, b, newest)
        assert [i for i, _ in _rows(held.text)][:3] == ids[::-1]
    finally:
        stack.gw.decks.client.pacer._open_until = 0
        await b.aclose()


async def test_the_not_shown_line_is_an_info_notice_that_links_to_recently_added(stack: Stack) -> None:
    """B4: in Set release, the line about added cards is an info notice (not the green success
    one) and ends with "Show them first", to the same view in Recently added, filters kept; there
    the added cards that match the name filter come first."""
    import html
    import re

    _seed(stack, 3)
    by_set = "/collection?view=list&sort=edition&dir=desc&q=Forest"
    newest = "/collection?q=Forest&sort=added&dir=desc&view=list"
    b, ids = await _then_down(stack, (by_set, newest), THREE)  # Forest NM, Forest LP, Opt NM
    try:
        held = await _held(stack, b, by_set)
        line = re.search(
            r"<div class='([^']*)'[^>]*>(?:(?!</div>).)*?you added(?:(?!</div>).)*?</div>", held.text, re.S
        )
        assert line, held.text[-3000:]
        assert line.group(1) == "notice", line.group(0)
        assert "2 cards you added aren’t shown in this order." in line.group(0), line.group(0)
        link = re.search(r"<a href='([^']*)'>Show them first</a>", line.group(0))
        assert link and html.unescape(link.group(1)) == newest, line.group(0)
        held = await _held(stack, b, html.unescape(link.group(1)))
        rows = _rows(held.text)
        assert [i for i, _ in rows][:2] == ids[1::-1], rows  # the two Forests, newest first
        assert ids[2] not in [i for i, _ in rows]  # Opt isn't in a Forest filter
        assert "you added aren" not in held.text
    finally:
        stack.gw.decks.client.pacer._open_until = 0
        await b.aclose()


async def test_the_held_price_sort_before_any_price_puts_added_cards_where_they_are(stack: Stack) -> None:
    """B6: before the daily price check the Price sort is Recently added (listing falls back to
    newest first, highest first; oldest first, lowest first), so the held copy shows the cards
    added since first in Price, highest first, and says it can't place them in lowest first."""
    _seed(stack, 3)
    high = "/collection?view=list&sort=price&dir=desc"
    low = "/collection?view=list&sort=price&dir=asc"
    b, ids = await _then_down(stack, (low, high), THREE)
    try:
        held = await _held(stack, b, high)
        assert [i for i, _ in _rows(held.text)][:3] == ids[::-1], _rows(held.text)
        assert "you added aren" not in held.text and "Cards you added since are at the top." in held.text
        held = await _held(stack, b, low)
        assert ids[0] not in [i for i, _ in _rows(held.text)]
        assert "3 cards you added aren’t shown in this order." in held.text
    finally:
        stack.gw.decks.client.pacer._open_until = 0
        await b.aclose()


# -- the held Binder read empty, then added to (final polish S1, S2) -----------------------------


async def test_a_held_binder_read_empty_counts_the_cards_added_since_in_every_tile(stack: Stack) -> None:
    """S1, the reviewer's repro: the Binder read empty, 10 cards added, Archidekt down. The tiles
    (Entries with its copies, Added this week, Foils) and the phone's At a glance line count the
    10, as the tab and the list do; nothing says 0."""
    import re

    stack.ark.collections["alice"] = {}
    newest = "/collection?view=list&sort=added&dir=desc"
    b, ids = await _then_down(stack, (newest,), ADDED)
    try:
        held = await _held(stack, b, newest)
        assert held.status_code == 200 and "as it was read" in held.text
        assert set(_counts(held.text).values()) == {10}, _counts(held.text)
        assert _week(held.text) == (10, 10)
        assert "<span>10 entries · 10 added this week</span>" in held.text
        copies = re.search(
            r"Entries</span><span class='big'>10</span><span class='det'>([^<]*)</span>", held.text
        )
        assert copies and copies.group(1) == "10 copies", copies and copies.group(1)
        foils = re.search(r"Foils</span><span class='big'>(\d+)</span>", held.text)
        assert foils and foils.group(1) == "0"  # all ten added as Normal
    finally:
        stack.gw.decks.client.pacer._open_until = 0
        await b.aclose()


def test_an_added_rows_mark_is_in_the_name_cell_at_every_width() -> None:
    """S2: the list's added rows say "Added since" in the name cell, in the cell's own muted small
    line (.s), never only in the lines the table hides at one width or another (.sub over 64rem,
    .smline over phone width, .lg under 64rem, .md-hide columns); the page's CSS hides no .s there."""
    import re

    from mtg_gateway import collection

    row = {
        "id": 7,
        "name": "Forest",
        "finish": "nonfoil",
        "condition": "NM",
        "lang": "EN",
        "quantity": 1,
        "set_code": "m21",
        "collector_number": "1",
        "type_line": "Basic Land",
        "held_added": True,
    }
    html = collection.row_html(row, "c", view="list")
    cell = re.search(r"<span class='tx'>(.*?)<span class='sub'>", html, re.S)
    assert cell, html
    assert cell.group(1).count("<span class='s'>Added since</span>") == 1, cell.group(1)
    assert html.count("Added since") == 1, html  # not again in the lines that come and go
    plain = collection.row_html({**row, "held_added": False}, "c", view="list")
    assert "Added since" not in plain
    css = collection.COLLECTION_CSS
    for rule in re.findall(r"([^{}]*\.s\b[^{}]*)\{([^}]*)\}", css):
        if "colllist" in rule[0] and "display:none" in rule[1].replace(" ", ""):
            assert not re.search(r"\.cardc \.s\b|colllist \.s\b", rule[0]), rule
