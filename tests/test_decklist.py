import pytest

from mtg_gateway.decklist import DecklistError, parse_decklist, to_text


def test_formats() -> None:
    text = (
        "Commander\n1 Aesi, Tyrant of Gyre Strait (cmr) 365 *F*\n\n// Lands\n15 Forest\n15x Island\n"
        "1 Sol Ring (CMR) 436 [Ramp{top}]\nCultivate\nSB: 1 Negate\nSideboard:\n2 Counterspell\n# note\n"
    )
    cards = {c.name: c for c in parse_decklist(text)}
    assert cards["Aesi, Tyrant of Gyre Strait"].categories == ["Commander"]
    assert (
        cards["Aesi, Tyrant of Gyre Strait"].foil and cards["Aesi, Tyrant of Gyre Strait"].set_code == "cmr"
    )
    assert cards["Forest"].quantity == 15 and cards["Island"].quantity == 15
    assert cards["Forest"].categories == ["Lands"]
    assert cards["Sol Ring"].categories == ["Ramp"] and cards["Sol Ring"].collector_number == "436"
    assert cards["Cultivate"].quantity == 1
    assert cards["Negate"].zone == "side" and cards["Counterspell"].zone == "side"


def test_merge_and_round_trip() -> None:
    cards = parse_decklist("2 Forest\n3 Forest\n1 Sol Ring\n")
    assert [(c.name, c.quantity) for c in cards] == [("Forest", 5), ("Sol Ring", 1)]
    text = to_text(cards)
    assert text == "5 Forest\n1 Sol Ring\n"
    assert [(c.name, c.quantity) for c in parse_decklist(text)] == [("Forest", 5), ("Sol Ring", 1)]


def test_errors() -> None:
    with pytest.raises(DecklistError):
        parse_decklist("\n\n# only comments\n")
    with pytest.raises(DecklistError):
        parse_decklist("0 Sol Ring\n")


def test_mismatches_merge_case_duplicates():
    from mtg_gateway.decks import _mismatches

    # Two proposal entries that differ only in case are one card to Archidekt.
    assert _mismatches({"Sol Ring": 2}, {"Sol Ring": 1, "sol ring": 1}) == []
    assert _mismatches({"Sol Ring": 1}, {"Sol Ring": 1, "sol ring": 1}) == ["Sol Ring"]


def test_hostile_lines_are_rejected_quickly():
    import time

    import pytest

    from mtg_gateway.decklist import DecklistError

    # A 32 kB line of spaces used to take over ten seconds in the line pattern.
    hostile = "1 a" + " " * 32_000 + "x("
    t = time.perf_counter()
    with pytest.raises(DecklistError):
        parse_decklist(hostile)
    assert time.perf_counter() - t < 0.5
    # Many whitespace-heavy lines under the cap stay fast too.
    text = "\n".join("1 a" + " " * 290 + "x(" for _ in range(2000))
    t = time.perf_counter()
    with pytest.raises(DecklistError):
        parse_decklist(text)
    assert time.perf_counter() - t < 1.0
    with pytest.raises(DecklistError):
        parse_decklist("\n".join(["1 Sol Ring"] * 5001))
    # Ordinary spacing is still accepted.
    assert parse_decklist("1   Sol   Ring  (c21)   123")[0].name == "Sol Ring"


def test_archidekt_syntax_round_trips_through_the_parser() -> None:
    """Archidekt's own export syntax (verified from its import dialog and exporter) comes back
    whole: quantities, printing, both finishes, categories with their flags stripped, the label
    kept apart, section headers as zones, and a custom-card mark tolerated."""
    text = (
        "1x Aesi, Tyrant of Gyre Strait (cmr) 365 *F* [Commander{top}]\n"
        "2x Sol Ring (cmr) 1 *E* [Ramp,Artifact] ^Upgrade,#ff0000^\n"
        "1x Opt [Maybeboard{noDeck}{noPrice}]\n"
        "# Sideboard\n"
        "1 Negate\n"
        "1x My Own Card [Custom] #CustomCard\n"
    )
    cards = {c.name: c for c in parse_decklist(text)}
    aesi, sol, opt, negate, custom = (
        cards[n] for n in ("Aesi, Tyrant of Gyre Strait", "Sol Ring", "Opt", "Negate", "My Own Card")
    )
    assert aesi.categories == ["Commander"] and aesi.finish == "Foil" and aesi.foil and aesi.set_code == "cmr"
    assert aesi.collector_number == "365" and aesi.zone == "main"
    assert (
        sol.quantity == 2 and sol.finish == "Etched" and sol.foil and sol.categories == ["Ramp", "Artifact"]
    )
    assert sol.label == "Upgrade,#ff0000"
    # {noDeck} keeps the row out of the deck, as Archidekt does
    assert opt.categories == ["Maybeboard"] and opt.zone == "side" and opt.board == "Maybeboard"
    assert negate.zone == "side" and negate.board == "Sideboard" and negate.categories == []
    assert custom.categories == ["Custom"] and custom.zone == "side" and custom.board == "Sideboard"
    # rendering the parsed list keeps both finish marks
    rendered = to_text(parse_decklist(text))
    assert "1 Aesi, Tyrant of Gyre Strait (cmr) 365 *F*" in rendered and "2 Sol Ring (cmr) 1 *E*" in rendered


def test_archidekt_nodeck_flag_keeps_the_row_out_of_the_deck() -> None:
    """Archidekt's own export writes a maybeboard row as ``[Maybeboard{noDeck}{noPrice}]``: the row
    stays out of the deck (zone side, counted apart), keeps its category and says it is unpriced."""
    cards = parse_decklist(
        "1 Sol Ring\n2 Sol Ring\n1 Island [Commander{top}]\n1 Arcane Signet [Maybeboard{noDeck}{noPrice}]"
    )
    by_name = {c.name: c for c in cards}
    signet = by_name["Arcane Signet"]
    assert signet.zone == "side" and signet.board == "Maybeboard" and signet.categories == ["Maybeboard"]
    assert signet.no_price and not by_name["Sol Ring"].no_price
    assert sum(c.quantity for c in cards if c.zone == "main") == 4
    assert "Arcane Signet" not in to_text(cards)
    assert to_text(cards, zone="side") == "1 Arcane Signet [Maybeboard]\n"
    # the flag belongs to the category: a line naming that category without the flag is outside
    # too, and only the first (primary) category decides, as on Archidekt
    text = "1 Opt [Considering{noDeck}]\n1 Negate [Considering]\n1 Ponder [Draw,Considering{noDeck}]"
    by_name = {c.name: c for c in parse_decklist(text)}
    assert by_name["Opt"].zone == "side" and by_name["Negate"].zone == "side"
    assert by_name["Ponder"].zone == "main"


def test_cmdr_marker_names_the_commander() -> None:
    cards = parse_decklist("1 Aesi, Tyrant of Gyre Strait *CMDR*\n1 Sol Ring\n1 Arcane Signet\n97 Island")
    aesi = cards[0]
    assert aesi.name == "Aesi, Tyrant of Gyre Strait" and aesi.categories == ["Commander"]
    assert to_text(cards).splitlines()[0] == "1 Aesi, Tyrant of Gyre Strait [Commander]"
    # after a printing and finish, or before categories, it still marks the commander
    foil = parse_decklist("1 Aesi, Tyrant of Gyre Strait (cmr) 365 *F* *CMDR* [Lands]")[0]
    assert foil.categories == ["Commander", "Lands"] and foil.finish == "Foil" and foil.set_code == "cmr"


@pytest.mark.parametrize(
    "line", ["-1 Island", "1.5 Island", "1,5x Island", "SB: -2 Negate", "1000x Island", "1000 x Island"]
)
def test_signed_or_fractional_quantity_is_refused(line: str) -> None:
    with pytest.raises(DecklistError, match="bad quantity on line"):
        parse_decklist(f"1 Sol Ring\n{line}\n")
    # a card whose name starts with a sign or a year is still a name
    assert parse_decklist("1 +2 Mace")[0].name == "+2 Mace"
    assert [(c.quantity, c.name) for c in parse_decklist("1996 World Champion")] == [
        (1, "1996 World Champion")
    ]


# The research tools' own output, as edhrec_average_deck and precon_decklist print it (markdown
# headings, a bold total, a set line): it must read back with its commander.
EDHREC_AVERAGE = """# Average Deck for Krenko, Mob Boss

## Commander
1 Krenko, Mob Boss

## Creature
1 Goblin Matron
1 Skirk Prospector

## Artifact
1 Sol Ring

Total: 4 cards
"""
PRECON = """# Multiverse Reforged
Set: FRC | Type: Commander Deck

## Commander
1 Krenko, Mob Boss

## Main Deck (2 cards)
1 Sol Ring
1 Goblin Matron

## Sideboard (1 cards)
1 Negate

**Total: 3 cards**
"""


def test_markdown_headings_are_sections() -> None:
    unread: list[str] = []
    cards = {c.name: c for c in parse_decklist(EDHREC_AVERAGE, unread)}
    assert cards["Krenko, Mob Boss"].categories == ["Commander"]
    assert cards["Goblin Matron"].categories == ["Creature"]
    assert cards["Sol Ring"].categories == ["Artifact"]
    # nothing read is dropped without a word: the title and the footer are listed
    assert unread == ["# Average Deck for Krenko, Mob Boss", "Total: 4 cards"]
    assert to_text(list(cards.values())).startswith("1 Krenko, Mob Boss [Commander]\n")


def test_precon_output_reads_back() -> None:
    unread: list[str] = []
    cards = {c.name: c for c in parse_decklist(PRECON, unread)}
    assert set(cards) == {"Krenko, Mob Boss", "Sol Ring", "Goblin Matron", "Negate"}
    assert cards["Krenko, Mob Boss"].categories == ["Commander"]
    assert cards["Sol Ring"].categories == [] and cards["Sol Ring"].zone == "main"
    assert cards["Negate"].zone == "side"
    assert unread == ["# Multiverse Reforged", "Set: FRC | Type: Commander Deck", "**Total: 3 cards**"]


def test_comment_lines_are_listed_as_unread() -> None:
    unread: list[str] = []
    parse_decklist("# my notes\n// made by hand\n1 Sol Ring\n// Lands\n1 Forest\n", unread)
    assert unread == ["# my notes", "// made by hand"]
