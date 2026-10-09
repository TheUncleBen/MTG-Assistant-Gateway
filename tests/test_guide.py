"""The Guide page's structure (guide.py): four Diátaxis parts, every section under exactly one of
them, a contents rail whose every link lands on an id that exists, stable heading ids with anchor
links, a labelled search box with a status line, no inline script, and the facts that went stale
in earlier versions (Playtest in a new tab, two sign-out buttons, the backup tick box, the card
menu keys, the search bar syntax, the 90-second list cache)."""

from __future__ import annotations

import re

from mtg_gateway import guide

PART_IDS = ("tutorials", "howto", "reference", "explanation")


def _page(**kw) -> str:
    args = {"site": "Gateway", "writes_enabled": True, "has_collection": True}
    args.update(kw)
    return guide.guide_body(**args)


def _ids(page: str) -> list[str]:
    return re.findall(r" id='([^']+)'", page)


def test_every_link_in_the_rail_and_every_anchor_lands_on_an_id() -> None:
    page = _page()
    ids = _ids(page)
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1]
    nav = page[page.index("<nav aria-label='Guide contents'>") : page.index("</nav>")]
    targets = re.findall(r"href='#([^']+)'", nav)
    assert targets, "the rail has no links"
    missing = [t for t in targets if t not in ids]
    assert not missing, missing
    anchors = re.findall(r"<a class='anchor' href='#([^']+)'", page)
    assert anchors and all(a in ids for a in anchors)
    # every heading in the body carries an id and its own anchor link
    for level in ("h2", "h3", "h4"):
        for m in re.finditer(rf"<{level}\b([^>]*)>(.*?)</{level}>", page):
            assert "id='" in m.group(1), m.group(0)[:80]
            assert "class='anchor'" in m.group(2), m.group(0)[:80]


def test_every_section_sits_under_exactly_one_of_the_four_parts() -> None:
    page = _page()
    parts = list(re.finditer(r"<section class='gpart' id='([a-z]+)'", page))
    assert [m.group(1) for m in parts] == list(PART_IDS)
    bounds = [
        (m.start(), parts[i + 1].start() if i + 1 < len(parts) else len(page)) for i, m in enumerate(parts)
    ]
    sections = list(re.finditer(r"<section class='panel gsec' id='([a-z0-9-]+)'", page))
    assert len(sections) >= 25
    for m in sections:
        owners = [pid for (lo, hi), pid in zip(bounds, PART_IDS, strict=True) if lo <= m.start() < hi]
        assert len(owners) == 1, (m.group(1), owners)
    # the rail nests the same sections under the same parts, in the same order
    nav = page[page.index("<nav aria-label='Guide contents'>") : page.index("</nav>")]
    for pid, _ic, _title, _lead, listed in guide.PARTS:
        li = nav[nav.index(f"href='#{pid}'") :]
        li = li[: li.index("</ul></li>")]
        expected = [sid for sid, _i, _l in listed]
        assert re.findall(r"<li><a href='#([^']+)'", li) == expected, pid
    declared = [sid for _p, _i, _t, _l, secs in guide.PARTS for sid, _ic, _lb in secs]
    assert [m.group(1) for m in sections] == declared


def test_search_box_status_line_and_no_inline_script() -> None:
    page = _page()
    assert "<label for='guide-q'>Search the guide</label>" in page
    assert re.search(r"<input id='guide-q' type='search'[^>]*aria-describedby='guide-status'", page)
    assert "<p id='guide-status' class='status muted small' role='status' aria-live='polite'>" in page
    assert "<script" not in page and not re.search(r"\son[a-z]+='", page)
    assert "<details class='gnav' open>" in page and "aria-label='Guide contents'" in page
    assert "class='btn gtop hide' href='#top'" in page and "id='top'" in page
    # without script the search box is hidden and every section is simply on the page
    assert "<div class='gsearch' hidden>" in page
    assert "hidden" not in re.search(r"<section class='panel gsec'[^>]*>", page).group(0)


def test_collection_sections_follow_the_gateway() -> None:
    with_it = _page(has_collection=True)
    without = _page(has_collection=False)
    assert "id='how-collection'" in with_it and "href='#how-collection'" in with_it
    assert "id='how-collection'" not in without and "href='#how-collection'" not in without


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
        "filter bar",  # History filters
        "7 card rows",  # the gateway's own low-risk limit
    ):
        assert fact in page, fact
    assert "framed on a gateway page" not in page
    off = _page(writes_enabled=False, archidekt_backups=False)
    assert "switched off on this gateway" in off and "no tick box is offered" in off
