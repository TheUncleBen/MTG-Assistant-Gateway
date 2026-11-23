"""The goldfish page's deck data (deckpage.goldfish_data): commanders apart, the sideboard and
maybeboard left out, and the JSON safe inside the page."""

from __future__ import annotations

import json

from mtg_gateway.archidekt import parse_deck
from mtg_gateway.deckpage import goldfish_data, goldfish_page_html

from .fake_archidekt import FakeArchidekt


def _deck(**change):
    ark = FakeArchidekt()
    ark.add_side_row(42, "Sol Ring")
    raw = ark.decks[42]
    raw.update(change)
    return raw


def test_commander_deck_keeps_the_commander_apart_and_the_sideboard_out() -> None:
    raw = _deck()
    deck = parse_deck(raw)
    data = goldfish_data(deck)
    assert [c["name"] for c in data["commanders"]] == ["Aesi, Tyrant of Gyre Strait"]
    names = [c["name"] for c in data["library"]]
    assert "Aesi, Tyrant of Gyre Strait" not in names
    main = sum(c.quantity for c in deck.cards if deck.in_deck(c))
    assert sum(c["qty"] for c in data["library"]) == main - 1  # the side Sol Ring is not counted
    assert any(c.get("land") for c in data["library"]) and all("qty" in c for c in data["library"])


def test_deck_without_a_commander_has_no_command_zone() -> None:
    raw = _deck()
    raw["categories"] = [c for c in raw["categories"] if not c.get("isPremier")]
    for row in raw["cards"]:
        if row["categories"] == ["Commander"]:
            row["categories"] = None
    deck = parse_deck(raw)
    data = goldfish_data(deck)
    assert data["commanders"] == []
    page = goldfish_page_html(deck)
    assert "id='gf-command'" not in page and "No commander" in page


def test_card_text_cannot_close_the_script_block() -> None:
    raw = _deck(name="</script><script>alert(1)</script>")
    raw["cards"][0]["card"]["oracleCard"]["name"] = "Evil </script> Card"
    page = goldfish_page_html(parse_deck(raw))
    blob = page.split("id='goldfish-data'>", 1)[1].split("</script>", 1)[0]
    assert "Evil \\u003c/script> Card" in blob
    assert json.loads(blob)["library"]
    assert "<script>alert" not in page


def test_goldfish_and_compare_use_the_sites_page_head_and_plain_section_headings() -> None:
    """One look for the deck's pages (docs/UI-STANDARD.md): the page title in ui.page_head's
    .ui-head, the purpose line as .ui-sub, the way back as the grey capsule, and section h2s with
    no size of their own (Goldfish had a 1.6rem title and 1.1rem zone headings)."""
    from mtg_gateway import deckpage

    page = goldfish_page_html(parse_deck(_deck()))
    assert (
        "<div class='ui-head'><h1>Goldfish</h1></div>"
        "<p class='ui-sub gf-intro ui-dots'><span class='gf-deck'>" in page
    )
    # the head sits outside the panel
    assert page.index("gf-intro") < page.index("class='panel gf-head'")
    # the way back is the site's one helper (grey capsule with the back icon, own line, above h1)
    back = "<p class='ui-back-line gf-crumbs'><a class='ui-cap ui-back' href='/decks/"
    assert back in page and page.index(back) < page.index("<h1>Goldfish</h1>")
    assert "← Back" not in page
    for rule in (".gf-zone h2{", ".gf-guide h2{"):
        assert "font-size" not in deckpage.GOLDFISH_CSS.split(rule)[1].split("}")[0], rule
    assert "font-size" not in deckpage.COMPARE_CSS.split(".cmp-h{")[1].split("}")[0]
    assert ".gf-head h1" not in deckpage.GOLDFISH_CSS


def test_compare_names_the_deck_once_escaped() -> None:
    """A deck title with & or < reads as typed in Compare's purpose line (page_head escapes; the
    name was escaped before it too, so "&" showed as "&amp;")."""
    from mtg_gateway.deckpage import compare_page_html

    raw = _deck(name="Tom & <Jerry>")
    page = compare_page_html(
        parse_deck(raw), other=None, other_name="", other_ref="", precons={}, paste="", result=None
    )
    assert "“Tom &amp; &lt;Jerry&gt;” and another deck" in page
    assert "&amp;amp;" not in page
    back = "<p class='ui-back-line cmp-back'><a class='ui-cap ui-back' href='/decks/"
    assert back in page and page.index(back) < page.index("<h1>Compare decks</h1>")
