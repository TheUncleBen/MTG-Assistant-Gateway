"""One test for "this text is a whole number the gateway can turn into an int".

``str.isdigit()`` says yes to characters ``int()`` then refuses ("²", "①"), and to digit runs longer
than Python converts, so every place that reads a number out of a request, a form or an upstream
answer asks here instead.
"""

from __future__ import annotations

MAX_DIGITS = 4000  # Python refuses to convert more than 4300


def is_digits(text: object, *, most: int = MAX_DIGITS) -> bool:
    """True when ``text`` (str, or the bytes of a header) is 1 to ``most`` plain digits 0-9."""
    return isinstance(text, (str, bytes)) and 0 < len(text) <= most and text.isascii() and text.isdigit()
