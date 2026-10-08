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
    assert (
        opt.categories == ["Maybeboard"] and opt.zone == "main"
    )  # a category row: Archidekt decides the zone
    assert negate.zone == "side" and negate.board == "Sideboard" and negate.categories == []
    assert custom.categories == ["Custom"] and custom.zone == "side" and custom.board == "Sideboard"
    # rendering the parsed list keeps both finish marks
    rendered = to_text(parse_decklist(text))
    assert "1 Aesi, Tyrant of Gyre Strait (cmr) 365 *F*" in rendered and "2 Sol Ring (cmr) 1 *E*" in rendered
