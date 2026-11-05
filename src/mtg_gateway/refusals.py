"""One wording for each refusal that the website and the assistant share: the same
situation reads the same on the deck page, the review page, the cards and in the assistant's
answers."""

from __future__ import annotations

WRITES_OFF = "Archidekt writes are switched off on this gateway, so nothing was sent to Archidekt."
# the same, where the change stays waiting (the review page, an apply), and several (a selection)
WRITES_OFF_KEPT = WRITES_OFF + " The change stays waiting and can be applied once writes are switched on."
WRITES_OFF_KEPT_MANY = (
    WRITES_OFF + " The changes stay waiting and can be applied once writes are switched on."
)
# the start of every line that says so without a refusal (the deck page, the cards)
WRITES_OFF_STATE = "Archidekt writes are switched off on this gateway"
FOLLOW_SELF = "You can't follow yourself on Archidekt."


def sentence(text: object) -> str:
    """A refusal's words (or an exception's) as a whole sentence: a capital first letter and one
    full stop at the end, never two (0.7.46 sweep B-3)."""
    words = str(text).strip()
    if not words:
        return words
    words = (
        words[0].upper() + words[1:] if words[0].islower() and "_" not in words.split(" ", 1)[0] else words
    )
    return words if words[-1] in ".!?…" else words + "."


# The Archidekt link, one wording for each case (whole-application audit, row 12): no link at all,
# a link Archidekt no longer accepts (only relinking helps, never waiting), and a link stored
# without its Archidekt user ID. ``public_url`` names the Account page where the reader can't click.
def _account(public_url: str) -> str:
    return f"at {public_url.rstrip('/')}/account" if public_url else "on the Account page"


def not_linked(public_url: str = "") -> str:
    return f"No Archidekt account is linked. Link one {_account(public_url)}."


def relink(public_url: str = "") -> str:
    return (
        "Archidekt no longer accepts the stored session. Relink your Archidekt account "
        f"{_account(public_url)}."
    )


def no_user_id(public_url: str = "") -> str:
    return f"The linked Archidekt account is missing its user ID. Relink it {_account(public_url)}."


NOT_OWN_COMMENT = "Only your own comments can be changed or deleted."
OWN_COMMENT_VOTE = "You can't vote on your own comment."
EMPTY_COMMENT = "The comment is empty: write it first."
ARCHIVED_COMMENT = "That comment is archived on Archidekt, so it can't be voted on."


def comment_too_long(most: int) -> str:
    return f"A comment can be at most {most} characters."
