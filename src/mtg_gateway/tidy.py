"""Numbers in a tool result rounded to what an assistant can use (assistant tool review AT-34): a
win rate of 0.32691781564606526 is sent as 0.327. Three significant digits, never fewer than two
decimals, so a price (12.35) or a count (40.0) reads as it did; whole numbers are left alone."""

from __future__ import annotations

import math
import re
from typing import Any

# A float literal with five or more decimals, standing alone (not part of a version or an id).
_LONG_FLOAT = re.compile(r"(?<![\w.])-?\d+\.\d{5,}(?:[eE][-+]?\d+)?(?![\w.])")


def tidy_float(value: float) -> float:
    """``value`` at three significant digits, with at least two decimals."""
    if not math.isfinite(value) or value == 0:
        return value
    decimals = max(2, 2 - math.floor(math.log10(abs(value))))
    return round(value, decimals)


def _long(value: float) -> bool:
    text = repr(value)
    return "." in text and len(text.split(".", 1)[1].split("e", 1)[0]) >= 5


def tidy_value(value: Any) -> Any:
    """``value`` with each long float rounded, in dicts and lists at any depth, and in strings
    (a Metrics block inside a report's text)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return tidy_float(value) if _long(value) else value
    if isinstance(value, str):
        return tidy_text(value)
    if isinstance(value, dict):
        return {k: tidy_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [tidy_value(v) for v in value]
    return value


def tidy_text(text: str) -> str:
    """``text`` with each long float literal in it rounded."""
    if "." not in text:
        return text
    return _LONG_FLOAT.sub(lambda m: repr(tidy_float(float(m.group(0)))), text)


__all__ = ["tidy_float", "tidy_text", "tidy_value"]
