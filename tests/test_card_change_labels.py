"""The chat's proposal card writes its change labels (Add, Remove, Change) in sentence case, as the
gateway's pages do on every width (0.7.46 polish N5): no uppercase transform on .act."""

from __future__ import annotations

import re
from pathlib import Path

CARD = Path(__file__).parents[1] / "src" / "mtg_gateway" / "static" / "cards" / "proposal-card.html"


def test_the_proposal_cards_change_labels_are_sentence_case() -> None:
    rule = re.search(r"(?m)^\.act\{([^}]*)\}", CARD.read_text(encoding="utf-8"))
    assert rule is not None
    assert "uppercase" not in rule.group(1) and "letter-spacing" not in rule.group(1), rule.group(1)
