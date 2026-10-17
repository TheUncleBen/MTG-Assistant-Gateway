"""One wording for each refusal that the website and the assistant share (Q141 item 7A): the same
situation reads the same on the deck page, the review page, the cards and in the assistant's
answers. Wording drafts for the wording card (WORDING-4); what each refusal does is unchanged."""

from __future__ import annotations

WRITES_OFF = "Archidekt writes are switched off on this gateway, so nothing was sent to Archidekt."
# the same, where a proposal stays waiting (the review page, an apply)
WRITES_OFF_KEPT = WRITES_OFF + " The proposal is kept, so it can be applied once they are switched on."
# the start of every line that says so without a refusal (the deck page, the cards)
WRITES_OFF_STATE = "Archidekt writes are switched off on this gateway"
FOLLOW_SELF = "You can't follow yourself on Archidekt."
NOT_OWN_COMMENT = "Only your own comments can be changed or deleted."
OWN_COMMENT_VOTE = "You can't vote on your own comment."
EMPTY_COMMENT = "The comment is empty: write it first."
