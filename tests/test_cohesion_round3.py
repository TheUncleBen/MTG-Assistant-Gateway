"""Review round 3 of the cohesion pass: what the independent bug hunt found on the branch."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit

from mtg_gateway import binder_hub, theme


def test_a_lists_menu_without_scripts_opens_the_box_for_that_list() -> None:
    """The Rename and Delete links of a list's menu are the no-script path: the page opens their
    box only for the list shown (View.box), so each link carries the list."""
    view = binder_hub.View({"list": "Trade binder"})
    menu = binder_hub.list_menu_html(view)
    hrefs = [html.unescape(h) for h in re.findall(r"<a href='([^']+)'", menu)]
    assert len(hrefs) == 2, menu
    for href, box in zip(hrefs, ("rename", "delete"), strict=True):
        q = parse_qs(urlsplit(href).query)
        assert q.get("box") == [box] and q.get("list") == ["Trade binder"], href
        opened = binder_hub.View({k: v[0] for k, v in q.items()})
        assert opened.box == box, href


def test_historys_group_row_keeps_only_this_deck_at_large_text() -> None:
    """At 200% text on a phone a group row's Open deck goes (the name opens the deck), but
    History's group row holds Only this deck, which the name does not do: it stays, under the name."""
    css = theme.CSS + theme.UI_CSS
    assert ".ui-table.ui-rows .tr.group:not(.hy-group) .act{display:none}" in css
    assert ".ui-table.ui-rows .tr.group .act{display:none}" not in css
    assert ".ui-table.ui-rows .tr.hy-group{display:flex;flex-wrap:wrap" in css
