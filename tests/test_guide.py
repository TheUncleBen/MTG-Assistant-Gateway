"""The Guide (guide.py), one page a topic since decision C8: four Diátaxis parts, every topic
under exactly one of them, a contents page and a contents rail whose every link lands on a
topic page, the links inside the sentences re-pointed to the pages they name, a search over
the topics, the navigation described from the navigation itself, and the facts that went stale
in earlier versions (Playtest in a new tab, two sign-out buttons, the backup tick box, the card
menu keys, the search bar syntax, the 90-second list cache)."""

from __future__ import annotations

import html
import re

import pytest

from mtg_gateway import guide, theme

PART_IDS = ("tutorials", "howto", "reference", "explanation")


def _page(**kw) -> str:
    args = {"site": "Gateway", "writes_enabled": True, "has_collection": True}
    args.update(kw)
    return guide.guide_body(**args)


def _guide(**kw) -> guide.Guide:
    args = {"site": "Gateway", "writes_enabled": True, "has_collection": True}
    args.update(kw)
    return guide.build(**args)


def _ids(page: str) -> list[str]:
    return re.findall(r" id='([^']+)'", page)


def test_every_topic_sits_under_exactly_one_part_and_every_inner_link_lands() -> None:
    g = _guide()
    assert [p[0] for p in g.parts] == list(PART_IDS)
    topics = g.topics
    assert len(topics) >= 25 and len({t[0] for t in topics}) == len(topics)
    declared = [sid for _p, _i, _t, _l, secs in guide.PARTS for sid, _ic, _lb in secs]
    assert [t[0] for t in topics] == declared
    ids = _ids(_page())
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
    for sid, _ic, _label, _pid in topics:
        body = guide.topic_body(g, sid)
        # every link inside the sentences is a topic page, a heading on one, or this page's own
        for target in re.findall(r"href='#([a-z0-9-]+)'", body):
            assert target == "top" or f"id='{target}'" in body, (sid, target)
        for href in re.findall(r"href='/guide/([a-z0-9-]+)(?:#([a-z0-9-]+))?'", body):
            assert href[0] in g.bodies, (sid, href)
            if href[1]:
                assert f"id='{href[1]}'" in g.bodies[href[0]], (sid, href)
        assert "<nav class='gturn'" in body and "gcrumbs" in body
    # the rail nests every topic under its part, the current one marked
    nav = guide.contents_nav(g, "how-restore")
    assert re.findall(r"href='/guide/([a-z0-9-]+)'", nav) == declared
    assert "<a href='/guide/how-restore' aria-current=page>Restore a snapshot</a>" in nav


def test_the_contents_page_lists_every_topic_once_and_keeps_the_old_anchors() -> None:
    g = _guide()
    body = guide.contents_body(g, anchors_json="{}")
    assert "<h1>Guide</h1>" in body and "<form class='gsearch' method='get' action='/guide/search'" in body
    assert (
        re.findall(r"<ul class='gtoc'>", body)
        and len(re.findall(r"<section class='panel gpart' id='", body)) == 4
    )
    listed = re.findall(r"<li><a href='/guide/([a-z0-9-]+)'>", body)
    assert listed == [t[0] for t in g.topics]
    assert "id='guide-anchors'" in body
    # a heading inside a topic is found from the old one-page address
    assert g.owner_of("how-export-deck") == "how-export" and g.owner_of("ref-keys") == "ref-keys"
    assert g.owner_of("nothing-here") is None
    # the first and the last topic have one neighbour each
    first = guide.topic_body(g, g.topics[0][0])
    assert "rel='prev'" not in first and "rel='next'" in first
    last = guide.topic_body(g, g.topics[-1][0])
    assert "rel='next'" not in last and "rel='prev'" in last


def test_the_search_finds_topics_and_marks_the_match() -> None:
    g = _guide()
    hits = guide.search(g, "F10")
    assert hits and hits[0]["title"] == "Keyboard shortcuts" and hits[0]["href"] == "/guide/ref-keys"
    assert "F10" in hits[0]["snippet"] and hits[0]["part"] == "Reference"
    assert guide.search(g, "") == [] and guide.search(g, "zzzz-nothing") == []
    assert guide.search(g, "restore")[0]["title"] == "Restore a snapshot"  # the title match first
    out = guide.results_html(hits, "F10")
    assert "<mark>F10</mark>" in out and "class='where'>Reference</span>" in out
    assert "Nothing in the guide matches" in guide.results_html([], "zzz")


def test_the_navigation_is_described_from_the_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(theme, "WATCHLISTS", True)
    text = guide.nav_description(html_out=False)
    for label in ("Home", "Decks", "Binder", "Assistant", "More", "Cards", "Wants", "Watchlist", "Proposals"):
        assert label in text, label
    for _g, links in theme.more_groups():
        for _h, label, _i in links:
            assert label in text, label
    assert "Forum and Alerts" in text  # the rail's extra places
    # a change to More changes the words: nothing is written by hand
    monkeypatch.setattr(theme, "MORE_GROUPS", [("Find", [("/zzz", "Zebra crossings", "search")])])
    assert "Zebra crossings" in guide.nav_description(html_out=False)
    assert "Precons" not in guide.nav_description(html_out=False)
    page = _page()
    assert page.count("<p class='tip'>On a phone the tabs at the bottom are") == 1
    assert "Watchlist" not in guide.nav_description(html_out=False) if not theme.WATCHLISTS else True


def test_docs_describe_the_navigation_in_the_same_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/USING.md carries the generated paragraph between its markers: when the navigation
    changes, this says so, and the paragraph is the one to paste."""
    from pathlib import Path

    monkeypatch.setattr(theme, "WATCHLISTS", True)  # the docs describe a gateway with watchlists on
    doc = Path(__file__).resolve().parents[1] / "docs" / "USING.md"
    text = doc.read_text()
    block = text.split("<!-- nav:start -->")[1].split("<!-- nav:end -->")[0]
    want = guide.nav_description(html_out=False)
    assert " ".join(block.split()) == want, f"docs/USING.md's navigation paragraph should read:\n{want}"


def test_a_topic_page_names_no_search_markup_twice_and_no_inline_script() -> None:
    g = _guide()
    page = guide.topic_body(g, "ref-keys")
    assert page.count("<label for='guide-q'>Search the guide</label>") == 1
    assert re.search(r"<input id='guide-q' type='search'[^>]*aria-describedby='guide-status'", page)
    assert "<p id='guide-status' class='status muted small' role='status' aria-live='polite'>" in page
    assert "<script" not in page and not re.search(r"\son[a-z]+='", page)
    assert "<details class='gnav' open>" in page and "aria-label='Guide contents'" in page
    assert "class='btn gtop hide' href='#top'" in page and "id='top'" in page


def test_collection_sections_follow_the_gateway() -> None:
    with_it, without = _guide(has_collection=True), _guide(has_collection=False)
    assert "how-collection" in with_it.bodies and "href='/guide/how-collection'" in guide.contents_nav(
        with_it
    )
    assert "how-collection" not in without.bodies
    assert "href='/guide/how-collection'" not in guide.contents_nav(without)
    assert "id='how-collection'" not in guide.guide_body(site="G", writes_enabled=True, has_collection=False)


def test_facts_match_the_current_pages() -> None:
    page = _page(max_rows=7, archidekt_backups=True)
    for fact in (
        "in a new tab",  # Playtest
        "Sign out on all my devices",  # two buttons on the Account page
        "Also keep a backup copy on Archidekt",  # the per-save tick box
        "keep the gateway snapshot only",  # quick edits from the deck page
        "<kbd>Shift</kbd>+<kbd>F10</kbd>",  # the card menu
        "opens the focused card in the viewer",  # Enter on a card
        "3 sol ring",  # the editor's search bar
        "90 seconds",  # the deck list cache
        "Markdown",  # the report exports
        "placeholder",  # Home and Decks while the list loads
        "Everything, Changes, Snapshots or Tests",  # the History chips (owner decision C7)
        "the search finds a deck by name, and the filters narrow by deck and when",
        "7 card rows",  # the gateway's own low-risk limit
    ):
        assert fact in page, fact
    assert "framed on a gateway page" not in page
    off = _page(writes_enabled=False, archidekt_backups=False)
    assert "switched off on this gateway" in off and "no tick box is offered" in off


def test_hand_play_names_the_goldfish_page_too() -> None:
    """Consolidation bug 5: the guide still called Archidekt's playtester the one place to play a
    deck by hand, though the gateway's own Goldfish page does that since 0.7.23."""
    page = _page()
    assert "one place to draw and play" not in page
    assert "two places to draw and play a deck by hand: the gateway's own <strong>Goldfish</strong>" in page


def test_every_assistant_tool_and_the_new_pages_are_in_the_guide(tmp_path, monkeypatch) -> None:
    """0.7.26: the reference names every gateway tool (the watch tools only when watchlists are on),
    and the 0.7.23 to 0.7.25 pages and features: Goldfish, Folders, Play on EDHPlay, out of date."""
    from mtg_gateway import app as app_mod

    from .conftest import make_settings

    servers = []
    build = app_mod.build_mcp_server
    monkeypatch.setattr(
        app_mod, "build_mcp_server", lambda state: servers.append(build(state)) or servers[-1]
    )
    app_mod.create_app(make_settings(tmp_path, watchlists_enabled=True))
    tools = [t.name for t in servers[-1]._tool_manager.list_tools()]
    assert "watch_card" in tools and len(tools) > 30
    on, off = _page(watchlists=True), _page()
    missing = [t for t in tools if f"<code>{t}</code>" not in on]
    assert not missing, missing
    assert "<code>watch_card</code>" not in off and "<code>/watchlist</code>" not in off
    assert "<code>/watchlist</code>" in on
    for fact in (
        "<code>/decks/&lt;id&gt;/goldfish</code>",
        "<code>/decks</code>",
        "Play on EDHPlay",
        "<strong>Needs a look</strong>",  # 0.7.29: the re-check replaced "out of date"
    ):
        assert fact in off, fact


class _Live:
    """Live settings as the Settings page holds them: the given values, else each default."""

    def __init__(self, **values: object) -> None:
        self.values = values

    def get(self, key: str) -> object:
        from mtg_gateway.config import LiveSettings

        return self.values.get(key, LiveSettings.spec(key).default)


def test_the_backup_texts_follow_the_backup_settings() -> None:
    """The save bar box, the approve step and Delete deck say what this gateway does: the box
    ticked or unticked by default, and no backup copy at all with backups off."""
    folder = "MTG Gateway Backups"
    copy_step = (
        f"saves a snapshot and puts a backup copy of the deck in a “{folder}” folder on your "
        "Archidekt account"
    )
    ticked = _page(live=_Live(archidekt_backup_box_default=True))
    assert "ticked by default on this gateway: untick it to skip that extra copy for that one save." in ticked
    assert "unticked by default" not in ticked and copy_step in ticked
    # a delete keeps the snapshot only, backups on or off (assistant tool review, 2026-10-28)
    assert "keeps a snapshot (no backup copy on Archidekt) first." in ticked
    unticked = _page(live=_Live(archidekt_backup_box_default=False))
    assert (
        "unticked by default on this gateway: tick it to also keep that extra copy for that one save."
        in unticked
    )
    assert copy_step in unticked and "keeps a snapshot (no backup copy on Archidekt) first." in unticked
    off = _page(
        archidekt_backups=False, live=_Live(archidekt_backups=False, archidekt_backup_box_default=True)
    )
    assert "The backup copy on Archidekt is switched off on this gateway, so no tick box is offered." in off
    assert "by default on this gateway" not in off and copy_step not in off and "backup copy first" not in off
    assert "checks it still fits the deck, saves a snapshot, then reads the deck back to confirm." in off
    assert "keeps a snapshot (no backup copy on Archidekt) first." in off


# -- 2026-10-21 overhaul (Ben's answers of 18:11Z): plain asks, the one closed tool-names fold ----
def test_member_text_names_no_tool_the_abilities_are_plain_asks() -> None:
    from mtg_gateway import tool_labels

    page = _page(watchlists=True)
    fold_at = page.index("id='adv-tools'")
    before = page[:fold_at]
    named = [raw for raw in tool_labels.TOOLS if f"<code>{raw}</code>" in before or f">{raw}<" in before]
    assert not named, named
    assert "by tool" not in page
    assert "<h3>What the assistant can do</h3>" in page
    table = page[page.index("id='ref-assistant'") : fold_at]
    assert "<th role='columnheader'>Ask your assistant</th><th role='columnheader'>What happens</th>" in table
    for ask in ("Read my deck", "Compare two decks", "Run a test report", "Watch this card's price"):
        assert f"“{html.escape(ask)}”" in table, ask
    assert "A change you approve first (a proposal)" in table and "only your own press applies" in table
    assert "Reads; nothing changes" in table
    # the asks follow the shared words, so a renamed tool cannot drift from the setup pages
    assert tool_labels.tool_ask("get_deck") == "Read my deck"


def test_the_tool_names_are_the_last_reference_topic_with_each_ask_beside_its_name() -> None:
    from mtg_gateway import tool_labels

    g = _guide(watchlists=True)
    ref = next(p for p in g.parts if p[0] == "reference")
    assert ref[4][-1] == ("ref-tools", "list", "Advanced: tool names")
    body = g.bodies["ref-tools"]
    assert (
        "<p>Your assistant picks these for you. They are listed here in case you need the exact name.</p>"
        in body
    )
    for raw, words in tool_labels.TOOLS.items():
        assert f"<td role='cell' data-label='Ask'>“{html.escape(words.ask)}”</td>" in body, raw
        assert f"<td role='cell' data-label='Tool name'><code>{raw}</code></td>" in body, raw
    # the watch asks go with watchlists off, in the table and in the topic
    off = _page()
    assert "watch_card" not in off and "Watch this card's price" not in off
    # no heading inside the topic: the search and the rail index the topics only
    assert not re.search(r"<h[2-4]", body)
