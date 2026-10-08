"""The in-chat cards beyond Approve (cards.py): which tools carry a card, what the assistant's
copy of a result keeps out, the signed links a card reads its data through, and the form
questions a client that supports them gets."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from mtg_gateway import cards
from mtg_gateway.cards import (
    ACCOUNT_CARD_URI,
    CARD_META_KEY,
    DECK_CARD_URI,
    LINK_PATH,
    PICKER_CARD_URI,
    PRINTINGS_CARD_URI,
    CardLinks,
    ask_choices,
    card_html,
)
from mtg_gateway.scan.scryfall import ScryfallClient

from .conftest import FakeIdP, Harness, make_settings, running, sse_json
from .fake_archidekt import FakeArchidekt
from .fake_scryfall import FakeScryfall
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import _client, call, linked_user, mcp_token, structured

AESI = "6511f317-bd38-46d0-b800-7125a3f420da"
CARDED = {
    "get_deck": DECK_CARD_URI,
    "get_snapshot": DECK_CARD_URI,
    "card_printings": PRINTINGS_CARD_URI,
    "resolve_cards": PICKER_CARD_URI,
    "account_status": ACCOUNT_CARD_URI,
    "whoami": ACCOUNT_CARD_URI,
}


class Stack:
    def __init__(self, h: Harness, ark: FakeArchidekt, sf: FakeScryfall):
        self.h = h
        self.ark = ark
        self.sf = sf


def _stack_with(tmp_path, idp: FakeIdP, **over):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path,
        writes_enabled=True,
        approval_mode_default="auto",
        archidekt_base="https://ark.test/api",
        **over,
    )
    return Harness(settings, idp, archidekt=_client(settings, ark)), ark


def _fake_scryfall(h: Harness) -> FakeScryfall:
    sf = FakeScryfall()
    h.app.state.gateway.scan.scryfall = ScryfallClient(
        "https://scryfall.test", min_interval=0.0, http=sf.client()
    )
    return sf


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP):
    h, ark = _stack_with(tmp_path, idp)
    async with running(h):
        yield Stack(h, ark, _fake_scryfall(h))


@pytest.fixture
async def no_card(tmp_path, idp: FakeIdP):
    h, ark = _stack_with(tmp_path, idp, apply_in_chat=False)
    async with running(h):
        yield Stack(h, ark, _fake_scryfall(h))


async def _tools(h, token: str) -> dict[str, dict]:
    r = await h.mcp(token, "tools/list", {}, rid=3)
    assert r.status_code == 200, r.text
    return {t["name"]: t for t in sse_json(r)["result"]["tools"]}


async def _resources(h, token: str) -> dict[str, dict]:
    r = await h.mcp(token, "resources/list", {}, rid=4)
    assert r.status_code == 200, r.text
    return {x["uri"]: x for x in sse_json(r)["result"]["resources"]}


# -- what the client sees -----------------------------------------------------------------------


async def test_tools_carry_their_cards_and_the_cards_declare_only_what_they_load(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    tools = await _tools(stack.h, token)
    for name, uri in CARDED.items():
        assert tools[name]["_meta"]["ui"]["resourceUri"] == uri, name
        assert tools[name]["_meta"]["ui/resourceUri"] == uri, name
    for name in ("list_my_decks", "deck_stats", "compare_decks", "parse_decklist"):
        assert "ui" not in (tools[name].get("_meta") or {}), name
    res = await _resources(stack.h, token)
    for uri in set(CARDED.values()):
        assert uri in res, uri
        meta = res[uri]["_meta"]["ui"]
        assert res[uri]["mimeType"] == "text/html;profile=mcp-app"
        assert meta.get("prefersBorder") is True
    csp = {uri: res[uri]["_meta"]["ui"].get("csp") or {} for uri in set(CARDED.values())}
    # Pictures from Scryfall; the deck and printings cards fetch their data from the gateway; the
    # deck card reads rules text from Scryfall's API; the account card loads nothing.
    assert "https://cards.scryfall.io" in csp[PRINTINGS_CARD_URI]["resourceDomains"]
    assert csp[PRINTINGS_CARD_URI]["connectDomains"] == ["https://mtg.test"]
    assert csp[DECK_CARD_URI]["connectDomains"] == ["https://mtg.test", "https://api.scryfall.com"]
    assert "connectDomains" not in csp[PICKER_CARD_URI] or not csp[PICKER_CARD_URI]["connectDomains"]
    assert not csp[ACCOUNT_CARD_URI].get("resourceDomains") and not csp[ACCOUNT_CARD_URI].get(
        "connectDomains"
    )
    assert "frameDomains" not in json.dumps(csp)


async def test_switching_the_card_off_removes_every_card(no_card: Stack) -> None:
    token = await mcp_token(no_card.h)
    tools = await _tools(no_card.h, token)
    for name in CARDED:
        assert "ui" not in (tools[name].get("_meta") or {}), name
    r = await no_card.h.mcp(token, "resources/list", {}, rid=4)
    assert r.status_code == 200 and not any(
        x["uri"].startswith("ui://") for x in sse_json(r)["result"].get("resources", [])
    )
    # The data endpoint is not even registered.
    r = await no_card.h.http.get(LINK_PATH + "anything")
    assert r.status_code == 404


def test_every_card_is_one_self_contained_document() -> None:
    for name in ("printings-card", "picker-card", "deck-card", "account-card"):
        html = card_html(name)
        assert "/*@BRIDGE@*/" not in html and "/*@BASE@*/" not in html
        assert "var Bridge = " in html and "ui/initialize" in html and "ui/notifications/size-changed" in html
        assert "innerHTML" not in html and "<script src" not in html and "eval(" not in html
        assert 'content="light dark"' in html and "prefers-color-scheme:dark" in html


# -- the assistant's copy versus the card's --------------------------------------------------------


async def test_deck_reads_keep_their_text_and_carry_a_signed_link_only_in_meta(stack: Stack) -> None:
    token = await linked_user(stack)
    for tool, args in (("get_deck", {"deck_ref": "42"}), ("get_deck", {"deck_ref": "42", "view": "cards"})):
        result = await call(stack.h, token, tool, args)
        sc = result["structuredContent"]
        assert sc["ok"] and sc["cards"] and sc["decklist_text"]
        card = result["_meta"][CARD_META_KEY]
        assert card["name"] == sc["name"] and card["link"].startswith("https://mtg.test" + LINK_PATH)
        assert LINK_PATH not in result["content"][0]["text"] and LINK_PATH not in json.dumps(sc)
    # An error carries no link.
    err = await call(stack.h, token, "get_deck", {"deck_ref": "999999"})
    assert err["structuredContent"]["ok"] is False and not (err.get("_meta") or {}).get(CARD_META_KEY)


async def test_the_deck_link_serves_the_deck_grouped_for_the_card_without_any_cookie(stack: Stack) -> None:
    token = await linked_user(stack)
    link = (await call(stack.h, token, "get_deck", {"deck_ref": "42"}))["_meta"][CARD_META_KEY]["link"]
    path = link[len("https://mtg.test") :]
    # Preflight and the read: open CORS (no credentials are ever involved), no caching, nosniff.
    pre = await stack.h.http.options(path, headers={"Origin": "https://abc123.claudemcpcontent.com"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "*"
    r = await stack.h.http.get(path, headers={"Origin": "null"})
    assert r.status_code == 200, r.text
    assert (
        r.headers["access-control-allow-origin"] == "*" and r.headers["cache-control"] == "private, no-store"
    )
    assert r.headers["x-content-type-options"] == "nosniff" and "set-cookie" not in r.headers
    deck = r.json()
    assert deck["ok"] and str(deck["id"]) == "42" and deck["card_count"] > 0 and deck["categories"]
    first = deck["categories"][0]
    assert {"name", "count", "in_deck", "cards"} <= set(first)
    row = first["cards"][0]
    assert {"name", "qty", "set", "cn", "finish", "uid", "mana", "mv", "type", "in_deck"} <= set(row)
    assert (
        deck["gateway_url"] == "https://mtg.test/decks/42" and deck["url"] == "https://archidekt.com/decks/42"
    )
    assert "oracle_text" not in json.dumps(deck)  # the card reads rules text from Scryfall itself
    assert isinstance(deck["curve"], dict)


async def test_links_are_bound_to_one_member_one_object_and_expire(stack: Stack, monkeypatch) -> None:
    h = stack.h
    token = await linked_user(stack)
    link = (await call(h, token, "get_deck", {"deck_ref": "42"}))["_meta"][CARD_META_KEY]["link"]
    tok = link.rsplit("/", 1)[1]
    # Tampering, truncation and made-up tokens: 404, never a 500.
    for bad in (tok[:-4] + "AAAA", tok[:20], "x" * 40, ""):
        r = await h.http.get(LINK_PATH + bad)
        assert r.status_code in (404, 405), bad
        if r.status_code == 404 and r.text.startswith("{"):
            assert r.json()["error"] in ("expired", "not_found")
    # The token names the member: a link for one person's private deck does not open for another.
    links: CardLinks = h.app.state.gateway.cards
    other = links.issue("someone-else", "deck", "42")
    assert links.open(other.rsplit("/", 1)[1])["sub"] == "someone-else"
    assert links.open(tok)["sub"] != "someone-else"
    # Expiry: the same token after its lifetime is refused.
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + cards.LINK_TTL + 5)
    try:
        assert links.open(tok) is None
        r = await h.http.get(LINK_PATH + tok)
        assert r.status_code == 404 and r.json()["error"] == "expired"
    finally:
        monkeypatch.setattr(time, "time", real_time)
    # A kind the gateway does not issue is never accepted, even with a valid signature.
    with pytest.raises(ValueError):
        links.issue("x", "proposal", "p-1")
    forged = links.fernet.encrypt(
        json.dumps({"s": "x", "k": "proposal", "r": "p-1", "t": 0}).encode()
    ).decode()
    assert links.open(forged) is None
    # A blob under the same key from another use of it (no card-link tag) is not a link either.
    untagged = links.fernet.encrypt(json.dumps({"s": "x", "k": "deck", "r": "42", "t": 0}).encode()).decode()
    assert links.open(untagged) is None


async def test_a_replayed_link_is_answered_from_cache_and_runs_out(stack: Stack) -> None:
    """The link is a bearer secret handed to the host's sandbox: replaying it must not spend the
    member's Archidekt budget, and it stops working after LINK_MAX_USES fetches."""
    h = stack.h
    token = await linked_user(stack)
    link = (await call(h, token, "get_deck", {"deck_ref": "42"}))["_meta"][CARD_META_KEY]["link"]
    path = link[len("https://mtg.test") :]
    before = len(stack.ark.calls)
    first = await h.http.get(path)
    assert first.status_code == 200
    reads = len(stack.ark.calls) - before
    assert reads >= 1
    for _ in range(cards.LINK_MAX_USES - 1):
        r = await h.http.get(path)
        assert r.status_code == 200 and r.json() == first.json()
    assert len(stack.ark.calls) - before == reads  # one Archidekt read for all of them
    r = await h.http.get(path)
    assert r.status_code == 404 and r.json()["error"] == "expired"
    # Error bodies are the gateway's own words, never Archidekt's.
    links: CardLinks = h.app.state.gateway.cards
    gone = links.issue(links.open(link.rsplit("/", 1)[1])["sub"], "deck", "999999")
    r = await h.http.get(gone[len("https://mtg.test") :])
    assert r.status_code == 404 and r.json()["message"] == cards.FIXED_MESSAGES["not_found"]


async def test_a_link_stops_working_for_a_disabled_member(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    link = (await call(h, token, "get_deck", {"deck_ref": "42"}))["_meta"][CARD_META_KEY]["link"]
    path = link[len("https://mtg.test") :]
    assert (await h.http.get(path)).status_code == 200
    sub = (
        h.app.state.gateway.db.list_users()[0]["sub"]
        if hasattr(h.app.state.gateway.db, "list_users")
        else None
    )
    if sub is None:
        sub = h.app.state.gateway.cards.open(link.rsplit("/", 1)[1])["sub"]
    h.app.state.gateway.db.set_user_disabled(sub, True)
    r = await h.http.get(path)
    assert r.status_code == 403 and r.json()["error"] == "forbidden"


async def test_snapshot_reads_carry_the_deck_card_with_the_snapshot_named(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
        )
    )
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["snapshot_id"]
    result = await call(h, token, "get_snapshot", {"snapshot_id": a["snapshot_id"]})
    assert result["structuredContent"]["snapshot_id"] == a["snapshot_id"]
    link = result["_meta"][CARD_META_KEY]["link"]
    r = await h.http.get(link[len("https://mtg.test") :])
    assert r.status_code == 200 and r.json()["snapshot"]["id"] == a["snapshot_id"]
    # Another member's link to the same snapshot id answers not found.
    links: CardLinks = h.app.state.gateway.cards
    other = links.issue("someone-else", "snapshot", a["snapshot_id"])
    r = await h.http.get(other[len("https://mtg.test") :])
    assert r.status_code in (403, 404)


async def test_printings_link_serves_every_printing_with_pictures(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    result = await call(h, token, "card_printings", {"oracle_id": AESI})
    meta = result["_meta"][CARD_META_KEY]
    assert meta["oracle_id"] == AESI and meta["total"] == 4 and meta["name"]
    r = await h.http.get(meta["link"][len("https://mtg.test") :])
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] and data["oracle_id"] == AESI and len(data["cards"]) == 4
    assert data["cards"][0]["image_small"].startswith("https://") and data["cards"][0]["scryfall_uri"]
    # A link for a member the gateway does not know is refused; a bad object for a real member
    # is not found; neither is a 500.
    links: CardLinks = h.app.state.gateway.cards
    stranger = links.issue("x", "printings", AESI)
    r = await h.http.get(stranger[len("https://mtg.test") :])
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    sub = links.open(meta["link"].rsplit("/", 1)[1])["sub"]
    bad = links.issue(sub, "printings", "not-an-oracle-id")
    r = await h.http.get(bad[len("https://mtg.test") :])
    assert r.status_code == 404 and r.json()["ok"] is False


async def test_resolve_cards_carries_the_picker_rows_in_meta(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    result = await call(
        h, token, "resolve_cards", {"cards": [{"name": "Sol Ring", "quantity": 2}, {"name": "Cultivatz"}]}
    )
    sc = result["structuredContent"]
    assert sc["ok"] and len(sc["cards"]) == 2
    rows = result["_meta"][CARD_META_KEY]["rows"]
    assert [r["input_name"] for r in rows] == ["Sol Ring", "Cultivatz"] and rows[0]["quantity"] == 2
    assert rows[0]["card"]["name"] == "Sol Ring" and set(rows[0]["card"]) <= {
        "name",
        "set",
        "set_name",
        "collector_number",
        "scryfall_id",
        "image_small",
        "finishes",
        "scryfall_uri",
    }
    assert rows[1]["status"] in ("fuzzy", "ambiguous", "not_found")


async def test_account_tools_say_where_the_account_page_is(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    who = structured(await call(h, token, "whoami"))
    assert who["account_page"] == "https://mtg.test/account"
    st = structured(await call(h, token, "account_status"))
    assert st["linked"] is False and st["account_page"] == "https://mtg.test/account" and st["approval_mode"]


# -- form questions ----------------------------------------------------------------------------------


class _Caps:
    def __init__(self, form: bool, url: bool = False):
        self.elicitation = SimpleNamespace(form={} if form else None, url={} if url else None)


class _Session:
    def __init__(self, answer):
        self.answer = answer
        self.asked: list[tuple[str, dict]] = []

    async def elicit(self, message, requested_schema, related_request_id=None):
        self.asked.append((message, requested_schema))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _ctx(form: bool, answer, url: bool = False):
    return SimpleNamespace(client_capabilities=_Caps(form, url), session=_Session(answer), request_id="r1")


async def test_form_questions_only_where_the_client_declares_forms() -> None:
    q = [{"key": "q0", "title": "Which card?", "options": ["Cultivate", "Cultivator's Caravan"]}]
    none = SimpleNamespace(client_capabilities=None, session=_Session(None))
    assert await ask_choices(none, "pick", q) is None
    url_only = _ctx(False, SimpleNamespace(action="accept", content={"q0": "Cultivate"}), url=True)
    assert await ask_choices(url_only, "pick", q) is None and not url_only.session.asked
    ctx = _ctx(True, SimpleNamespace(action="accept", content={"q0": "Cultivate"}))
    assert await ask_choices(ctx, "pick", q) == {"q0": "Cultivate"}
    message, schema = ctx.session.asked[0]
    assert message == "pick" and schema["properties"]["q0"]["enum"] == ["Cultivate", "Cultivator's Caravan"]
    # An answer outside the offered options, a decline, or a host error: nothing is taken.
    assert (
        await ask_choices(_ctx(True, SimpleNamespace(action="accept", content={"q0": "Sol Ring"})), "p", q)
        == {}
    )
    assert await ask_choices(_ctx(True, SimpleNamespace(action="decline", content=None)), "p", q) is None
    assert await ask_choices(_ctx(True, RuntimeError("no")), "p", q) is None
    # At most eight questions, twenty options each.
    many = [{"key": f"q{i}", "title": "?", "options": [str(n) for n in range(30)]} for i in range(12)]
    ctx = _ctx(True, SimpleNamespace(action="accept", content={}))
    assert await ask_choices(ctx, "p", many) == {}
    props = ctx.session.asked[0][1]["properties"]
    assert len(props) == 8 and len(props["q0"]["enum"]) == 20


async def test_resolve_cards_asks_a_form_for_ambiguous_names_where_it_can(stack: Stack, monkeypatch) -> None:
    """ "Cultivatz" ties between two names (as test_scan shows). Through the MCP endpoint the test
    client declares no elicitation, so nothing is asked and the tie stays ambiguous; with a client
    that answers the form, the picked name is resolved again and the row comes back exact."""
    h = stack.h
    stack.sf.autocomplete["cultivat"] = {
        "object": "catalog",
        "total_values": 2,
        "data": ["Cultivate", "Cultivatx"],
    }
    token = await mcp_token(h)
    out = structured(await call(h, token, "resolve_cards", {"cards": [{"name": "Cultivatz"}]}))
    assert out["cards"][0]["status"] == "ambiguous" and out["cards"][0]["suggestions"] == [
        "Cultivate",
        "Cultivatx",
    ]
    asked: list[tuple[str, list]] = []

    async def fake_ask(ctx, message, questions):
        asked.append((message, questions))
        return {"q0": "Cultivate"}

    monkeypatch.setattr("mtg_gateway.scan.tools.ask_choices", fake_ask)
    out = structured(await call(h, token, "resolve_cards", {"cards": [{"name": "Cultivatz", "quantity": 3}]}))
    assert (
        asked
        and asked[0][1][0]["options"] == ["Cultivate", "Cultivatx"]
        and "Cultivatz" in asked[0][1][0]["title"]
    )
    row = out["cards"][0]
    assert (
        row["card"]
        and row["card"]["name"] == "Cultivate"
        and row["quantity"] == 3
        and row["status"] == "exact"
    )
    # The row still says what was read, and that the user picked the name from the suggestions.
    assert (
        row["input"]["name"] == "Cultivatz" and "picked from the suggestions for 'Cultivatz'" in row["note"]
    )
    # A failed second pass keeps the first pass (its suggestions reach the assistant as before).
    service = h.app.state.gateway.scan

    async def boom(inputs, *, owner=None):
        from mtg_gateway.scan.service import ScanError

        raise ScanError("rate_limited", "busy")

    real = service.resolve
    monkeypatch.setattr(service, "resolve", real)
    calls = {"n": 0}

    async def second_fails(inputs, *, owner=None):
        calls["n"] += 1
        if calls["n"] == 2:
            return await boom(inputs, owner=owner)
        return await real(inputs, owner=owner)

    monkeypatch.setattr(service, "resolve", second_fails)
    out = structured(await call(h, token, "resolve_cards", {"cards": [{"name": "Cultivatz"}]}))
    assert out["ok"] is True and out["cards"][0]["status"] == "ambiguous" and calls["n"] == 2


def test_no_form_where_the_client_renders_cards() -> None:
    """A host that draws the picker card must not also get a form for the same names."""
    caps = _Caps(form=True)
    caps.extensions = {cards.UI_EXTENSION: {}}
    assert cards.client_elicits_forms(SimpleNamespace(client_capabilities=caps)) is False
    caps.extensions = {}
    assert cards.client_elicits_forms(SimpleNamespace(client_capabilities=caps)) is True


async def test_a_link_stops_working_for_a_member_who_left_the_group(stack: Stack, monkeypatch) -> None:
    from mtg_gateway.membership import Membership

    h = stack.h
    token = await linked_user(stack)
    link = (await call(h, token, "get_deck", {"deck_ref": "42"}))["_meta"][CARD_META_KEY]["link"]
    path = link[len("https://mtg.test") :]
    checker = h.app.state.gateway.membership
    assert checker is not None and (await h.http.get(path)).status_code == 200

    async def revoked(sub):
        return Membership.REVOKED

    monkeypatch.setattr(checker, "check", revoked)
    r = await h.http.get(path)
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
