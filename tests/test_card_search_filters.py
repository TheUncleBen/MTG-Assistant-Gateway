"""The card search's filters (cardquery.py): every field of Scryfall's advanced search builds the
search Scryfall's own form builds (read 2026-10-14 by submitting that form), unknown values are
dropped, the page address round-trips; and the live results API (/cards/api/search) pages, keeps
pages, draws the list view and pins the printing shown when the search is about printings."""

from __future__ import annotations

import pytest

from mtg_gateway import cardquery as cq

from .test_browse_collection import Stack, linked, stack_options  # noqa: F401 - fixture


def q(**params: object) -> str:
    pairs: list[tuple[str, str]] = []
    for k, v in params.items():
        for item in v if isinstance(v, list) else [v]:
            pairs.append((k, str(item)))
    return cq.scryfall_query(cq.parse(pairs))


# -- one test per field: the search it writes ---------------------------------------------------
def test_defaults_are_paper_only_and_name_order() -> None:
    f = cq.parse([])
    assert f.games == ["paper"] and f.order == "name" and f.dir == "auto" and not f.chosen()
    assert cq.scryfall_query(f) == "(game:paper)"


def test_name_goes_as_typed() -> None:
    assert q(q="lightning bolt") == "lightning bolt (game:paper)"
    assert q(q="fire (ice)") == "fire ice (game:paper)"
    assert "(" not in q(q="a(b)").split(" (game")[0]


def test_rules_text_one_term_per_word_and_phrases_stay_whole() -> None:
    assert q(o="deals 3") == "(oracle:deals oracle:3) (game:paper)"
    assert q(o="draw") == "oracle:draw (game:paper)"
    assert q(o='"enters tapped" ~') == '(oracle:"enters tapped" oracle:"~") (game:paper)'
    assert q(o="{T}: add") == '(oracle:"{T}:" oracle:add) (game:paper)'


def test_types_is_not_and_any() -> None:
    assert q(t=["instant", "-goblin"]) == "type:instant -type:goblin (game:paper)"
    assert q(t=["instant", "sorcery"], tany="1") == "(type:instant OR type:sorcery) (game:paper)"
    assert q(t=["time lord"]) == 'type:"time lord" (game:paper)'
    assert q(t="urza's,elf") == "type:urza's type:elf (game:paper)"  # the page without scripts
    assert q(t=["<script>"]) == "(game:paper)"  # not a type word: dropped


def test_colors_with_each_comparison() -> None:
    assert q(c=["R", "G"]) == "color>=RG (game:paper)"
    assert q(c=["U"], cmp="eq") == "color=U (game:paper)"
    assert q(c=["w", "b"], cmp="le") == "color<=WB (game:paper)"
    assert q(c=["C"], cmp="eq") == "color=C (game:paper)"
    assert q(c=["X"]) == "(game:paper)"


def test_commander_colors() -> None:
    assert q(id=["R", "G"]) == "commander:RG (game:paper)"
    assert q(id=["C"]) == "commander:C (game:paper)"


def test_mana_cost() -> None:
    assert q(m="{2}{G}{G}") == "mana={2}{G}{G} (game:paper)"
    assert q(m="{R/G} x") == "mana={R/G}x (game:paper)"


def test_mana_value_range_and_stat_rows() -> None:
    assert q(mv="2", mvx="4") == "cmc>=2 cmc<=4 (game:paper)"
    assert q(mv="3", mvx="3") == "cmc=3 (game:paper)"
    assert q(mvx="1") == "cmc<=1 (game:paper)"
    assert q(st=["pow", "tou"], sto=["ge", "lt"], stv=["5", "2"]) == "pow>=5 tou<2 (game:paper)"
    assert q(st="loy", sto="eq", stv="3") == "loy=3 (game:paper)"
    assert q(st="cmc", sto="ne", stv="0") == "cmc!=0 (game:paper)"
    assert q(st="pow", sto="ge", stv="") == "(game:paper)"  # an empty row sends nothing
    assert q(st="hax", sto="ge", stv="1") == "(game:paper)"
    assert q(st="pow", sto="ge", stv="1; drop") == "(game:paper)"
    rows = cq.parse([("st", "pow"), ("sto", "ge"), ("stv", "1")] * 9).stats
    assert len(rows) == cq.MAX_ROWS


def test_games() -> None:
    assert q(g=["paper", "arena"]) == "(game:paper or game:arena)"
    assert q(g=["mtgo"]) == "(game:mtgo)"
    assert q(g=["any"]) == ""  # every game: nothing to narrow
    assert q(g=["any", "paper"]) == "(game:paper)"  # the page's hidden "any" plus a tick


def test_formats_legal_restricted_banned() -> None:
    assert q(fs="legal", f="commander") == "(game:paper) legal:commander"
    got = q(fs=["banned", "restricted"], f=["modern", "vintage"])
    assert got == "(game:paper) banned:modern restricted:vintage"
    assert q(fs="legal", f="") == "(game:paper)"
    assert q(fs="legal", f="hearthstone") == "(game:paper)"


def test_sets_and_blocks() -> None:
    assert q(s="lea") == "(game:paper) set:lea"
    assert q(s=["lea", "LEB"]) == "(game:paper) (set:lea OR set:leb)"
    assert q(b="ice") == "(game:paper) block:ice"
    assert q(s="not a set!") == "(game:paper)"


def test_rarity() -> None:
    assert q(r=["c", "u"]) == "(game:paper) (rarity:c OR rarity:u)"
    assert q(r="m") == "(game:paper) rarity:m"
    assert q(r="z") == "(game:paper)"


def test_criteria_is_not_and_any() -> None:
    assert q(**{"is": ["foil", "-promo"]}) == "(game:paper) is:foil -is:promo"
    assert q(**{"is": ["foil", "promo"], "isany": "1"}) == "(game:paper) (is:foil OR is:promo)"
    assert q(**{"is": ["madeup"]}) == "(game:paper)"


def test_prices() -> None:
    assert q(pc="usd", po="lt", pv="5") == "(game:paper) usd<5"
    assert q(pc=["eur", "tix"], po=["ge", "le"], pv=["1.5", "10"]) == "(game:paper) eur>=1.5 tix<=10"
    assert q(pc="usd", po="ne", pv="5") == "(game:paper)"  # Scryfall's price form has no "not equal"
    assert q(pc="usd", po="lt", pv="-5") == "(game:paper)"


def test_artist_flavor_lore_language() -> None:
    assert q(a="Christopher Rush") == "(game:paper) (artist:Christopher artist:Rush)"
    assert q(ft="fire storm") == "(game:paper) (flavor:fire flavor:storm)"
    assert q(lore="Jaya") == "(game:paper) lore:Jaya"
    assert q(lang="ja") == "(game:paper) lang:ja"
    assert q(lang="any") == "(game:paper) lang:any"
    assert q(lang="xx") == "(game:paper)"


def test_display_options_and_syntax_box() -> None:
    assert q(prefer="oldest") == "(game:paper) prefer:oldest"
    assert q(prints="1") == "(game:paper) unique:prints"
    assert q(extras="1") == "(game:paper) include:extras"
    assert q(x="t:fish or t:bird") == "(game:paper) (t:fish or t:bird)"
    f = cq.parse([("order", "edhrec"), ("dir", "desc")])
    assert (f.order, f.dir) == ("edhrec", "desc")
    f = cq.parse([("order", "nope"), ("dir", "sideways")])
    assert (f.order, f.dir) == ("name", "auto")


def test_everything_together_matches_scryfalls_own_order() -> None:
    got = q(
        q="bolt",
        o="deals 3",
        t=["instant", "-goblin"],
        c="R",
        id=["R", "G"],
        m="{R}",
        st="cmc",
        sto="le",
        stv="2",
        fs="legal",
        f="commander",
        r=["c", "u"],
        pc="usd",
        po="lt",
        pv="5",
        a="Christopher Rush",
        ft="fire storm",
        lore="Jaya",
        lang="ja",
        prefer="oldest",
        **{"is": ["foil", "-promo"]},
    )
    # the same terms Scryfall's advanced form wrote for these choices (2026-10-14)
    for term in (
        "bolt",
        "(oracle:deals oracle:3)",
        "type:instant",
        "-type:goblin",
        "color>=R",
        "commander:RG",
        "(game:paper)",
        "mana={R}",
        "cmc<=2",
        "legal:commander",
        "(rarity:c OR rarity:u)",
        "is:foil",
        "-is:promo",
        "usd<5",
        "(flavor:fire flavor:storm)",
        "(artist:Christopher artist:Rush)",
        "lore:Jaya",
        "lang:ja",
        "prefer:oldest",
    ):
        assert term in got, (term, got)


def test_every_known_list_value_is_accepted() -> None:
    for code, _ in cq.CRITERIA:
        assert f"is:{code}" in q(**{"is": code})
    for code, _ in cq.FORMATS:
        assert f"legal:{code}" in q(fs="legal", f=code)
    for code, _ in cq.LANGUAGES:
        if code:
            assert f"lang:{code}" in q(lang=code)
    for code, _ in cq.PREFER:
        if code:
            assert f"prefer:{code}" in q(prefer=code)
    assert len({c for c, _ in cq.CRITERIA}) == len(cq.CRITERIA)


def test_the_address_round_trips() -> None:
    pairs = [
        ("q", "sol"),
        ("o", "draw"),
        ("t", "-goblin"),
        ("tany", "1"),
        ("c", "R"),
        ("cmp", "eq"),
        ("id", "G"),
        ("mv", "1"),
        ("mvx", "3"),
        ("st", "pow"),
        ("sto", "ge"),
        ("stv", "2"),
        ("g", "arena"),
        ("fs", "banned"),
        ("f", "modern"),
        ("s", "lea"),
        ("b", "ice"),
        ("r", "m"),
        ("is", "foil"),
        ("isany", "1"),
        ("pc", "eur"),
        ("po", "lt"),
        ("pv", "3"),
        ("a", "Rush"),
        ("ft", "fire"),
        ("lore", "Jaya"),
        ("lang", "de"),
        ("x", "kw:flying"),
        ("prefer", "newest"),
        ("order", "cmc"),
        ("dir", "desc"),
        ("prints", "1"),
        ("extras", "1"),
    ]
    f = cq.parse(pairs)
    again = cq.parse(f.to_params())
    assert again == f
    assert cq.scryfall_query(again) == cq.scryfall_query(f)
    assert cq.parse(cq.parse([]).to_params()) == cq.parse([])


def test_printings_pin_only_when_the_search_is_about_printings() -> None:
    assert not cq.parse([("q", "sol")]).shows_printings()
    for extra in (("prints", "1"), ("prefer", "oldest"), ("lang", "ja")):
        assert cq.parse([("q", "sol"), extra]).shows_printings()


@pytest.mark.parametrize("raw", ["", "  ", "x" * 500])
def test_text_is_trimmed_and_capped(raw: str) -> None:
    f = cq.parse([("q", raw)])
    assert len(f.name) <= cq.MAX_TEXT and f.name == f.name.strip()


# -- the live results API -----------------------------------------------------------------------
async def test_the_api_needs_a_sign_in(stack: Stack) -> None:
    r = await stack.h.http.get("/cards/api/search?q=sol")
    assert r.status_code == 401
    r = await stack.h.http.get("/cards/api/options")
    assert r.status_code == 401


async def test_the_api_answers_the_page_html_and_pages_on(stack: Stack) -> None:
    stack.sf.page_size = 2
    b = await linked(stack)
    try:
        r = await b.http.get("/cards/api/search", params={"t": "land"})
        d = r.json()
        assert r.status_code == 200 and d["ok"], d
        assert d["page"] == 1 and d["count"] == 2 and d["has_more"] is True and d["total"] >= 3
        assert d["html"].startswith("<ul class='cardgrid' id='cardgrid' data-view='grid' data-linked>")
        assert d["query"] == "type:land (game:paper)"
        assert r.headers["cache-control"] == "no-store"
        r = await b.http.get("/cards/api/search", params={"t": "land", "page": "2"})
        d2 = r.json()
        assert d2["ok"] and d2["page"] == 2 and not d2["html"].startswith("<ul")  # rows only, to append
        assert "data-card=" in d2["html"]
        # nothing chosen: no request to Scryfall, an empty answer the page shows its help for
        n = len(stack.sf.searches)
        d3 = (await b.http.get("/cards/api/search")).json()
        assert d3["ok"] and d3["empty"] and len(stack.sf.searches) == n
    finally:
        await b.aclose()


async def test_a_page_already_read_is_not_asked_again(stack: Stack) -> None:
    b = await linked(stack)
    try:
        await b.http.get("/cards/api/search", params={"q": "sol"})
        n = len(stack.sf.searches)
        await b.http.get("/cards/api/search", params={"q": "sol"})
        await b.http.get("/cards", params={"q": "sol"})
        assert len(stack.sf.searches) == n
        await b.http.get("/cards/api/search", params={"q": "sol", "order": "cmc"})  # another order: asked
        assert len(stack.sf.searches) == n + 1
        assert stack.sf.searches[-1]["order"] == "cmc" and stack.sf.searches[-1]["dir"] == "auto"
    finally:
        await b.aclose()


async def test_the_list_view_is_drawn_and_remembered(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/cards/api/search", params={"q": "sol", "view": "list"})
        d = r.json()
        assert d["html"].startswith("<ul class='cardlist' id='cardgrid' data-view='list' data-linked>")
        assert "class='nmb' data-open>Sol Ring</button>" in d["html"]
        assert "mtg_view_cards=list" in r.headers.get("set-cookie", "")
        r = await b.http.get("/cards", params={"q": "sol"})
        assert "class='cardlist'" in r.text  # the remembered view
        assert "value='list' form='cs-form' checked" in r.text
    finally:
        await b.aclose()


async def test_printing_searches_pin_the_printing_on_add(stack: Stack) -> None:
    b = await linked(stack)
    try:
        d = (await b.http.get("/cards/api/search", params={"q": "sol ring", "prints": "1"})).json()
        assert "data-printing" in d["html"].split(">")[0]
        assert d["html"].count("data-add='Sol Ring'") == 3  # each printing its own tile
        assert "data-set-code=" in d["html"] and "data-number=" in d["html"]
        d = (await b.http.get("/cards/api/search", params={"q": "sol ring"})).json()
        assert "data-set-code=" not in d["html"]  # one card: the default printing goes in
    finally:
        await b.aclose()


async def test_a_bad_search_is_a_400_with_scryfalls_reason(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/cards/api/search", params={"x": "bad:thing"})
        assert r.status_code == 400 and "unknown keyword bad" in r.json()["message"]
        stack.sf.fail_with = 503
        r = await b.http.get("/cards/api/search", params={"q": "other"})
        assert r.status_code == 503 and "not answering" in r.json()["message"]
    finally:
        stack.sf.fail_with = None
        await b.aclose()


async def test_the_options_list_sets_blocks_types_and_criteria_once_a_day(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/cards/api/options")
        d = r.json()
        assert d["ok"] and not d["partial"], d
        assert {s["code"] for s in d["sets"]} == {"c16", "ice", "csp"}
        assert d["blocks"] == [{"code": "ice", "name": "Ice Age"}]
        groups = {g["label"]: g["values"] for g in d["types"]}
        assert groups["Creature types"] == ["Goblin", "Time Lord", "Elemental"]
        assert {"code": "foil", "name": "Foil"} in d["criteria"]
        assert r.headers["cache-control"] == "private, max-age=3600"
        n = len(stack.sf.requests)
        await b.http.get("/cards/api/options")
        assert len(stack.sf.requests) == n  # kept a day
    finally:
        await b.aclose()


async def test_the_page_keeps_every_filter_in_its_controls(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get(
            "/cards",
            params=[
                ("q", "sol"),
                ("t", "-goblin"),
                ("c", "R"),
                ("s", "lea"),
                ("lang", "de"),
                ("prints", "1"),
                ("order", "cmc"),
                ("st", "pow"),
                ("sto", "ge"),
                ("stv", "4"),
            ],
        )
        t = r.text
        assert r.status_code == 200
        assert "<li class='not' data-v='goblin'>" in t and "name='t' value='-goblin'" in t
        assert "name='c' value='R' checked" in t
        assert "data-v='lea'" in t and "name='s' value='lea'" in t
        assert "<option value='de' selected>German</option>" in t
        assert "name='prints' value='1' checked" in t
        assert "<option value='cmc' selected>Mana value</option>" in t
        assert "name='stv' value='4'" in t
        assert "<details class='cs-more' open>" in t  # a field under More filters is in use
    finally:
        await b.aclose()
