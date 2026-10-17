"""Archidekt account actions an assistant can propose (0.7.17): like or vote down a deck, bookmark
it, follow its owner, post, edit, delete or vote on a comment, delete a deck and create a
folder; 0.7.20 adds and removes a deck's collaborators. Each is a proposal of kind ``action``
with one review row, so it goes through the same approval flow as every other write, but (R-142)
only the member's own press applies one: on the in-chat card (0.7.29, Q8) or on the signed-in
review page (a deck deletion: the review page only), never the assistant, whatever the approval
mode (modes.py). The work itself is the code the gateway's own pages already use (the deck page's social
buttons in social.py, the delete and folder pages in decks.py), so a proposal does exactly what
the matching button does, with the same checks again at apply time."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from . import formatting, refusals
from .archidekt import VOTE_DOWN, VOTE_NONE, VOTE_UP, drops_formatting, words_only_note
from .decklist import clean_text
from .decks import DeckError, _clean_deck_id, _row, needs_look_error
from .social import FOLLOWING_PAGES

if TYPE_CHECKING:
    from .decks import DeckService

ACCOUNT_TARGET = "account"  # deck_id of a proposal that is about the account, not one deck
ACCOUNT_NAME = "Your Archidekt account"
# A comment edit that keeps formatting (Q137) whose comment changed since its review: its
# "needs a look" reason, in words (recheck.reason_text shows any other text as it is)
COMMENT_CHANGED = (
    "The comment changed on Archidekt since this was reviewed, so it would not look as reviewed."
)

# What each action is called on the review page and the in-chat card.
LABELS = {
    "deck_vote": "Like",
    "deck_bookmark": "Bookmark",
    "follow": "Follow",
    "comment_post": "Comment",
    "comment_edit": "Edit comment",
    "comment_delete": "Delete comment",
    "comment_vote": "Comment vote",
    "delete_deck": "Delete deck",
    "create_folder": "New folder",
    "collaborator_add": "Add collaborator",
    "collaborator_remove": "Remove collaborator",
}
ACTIONS = frozenset(LABELS)
DESTRUCTIVE = frozenset({"delete_deck"})  # never applied by an assistant on its own
CANNOT_UNDO = frozenset({"delete_deck", "comment_delete"})
VOTES = {"up": VOTE_UP, "down": VOTE_DOWN, "none": VOTE_NONE}
COLLABORATOR_ACTIONS = frozenset({"collaborator_add", "collaborator_remove"})
# Archidekt's own settings page says this of collaborators (2026-10-10 bundle).
COLLABORATOR_NOTE = (
    "A collaborator can make any change to the deck that you can, except its main settings. "
    "Archidekt's own settings page offers only people you follow, and so does the gateway."
)


def risk_of(rows: list[dict[str, Any]] | None) -> tuple[str, str]:
    """The approval tier of an action proposal (modes.risk_of hands ``action`` proposals here)."""
    action = str((rows or [{}])[0].get("action") or "")
    if action in DESTRUCTIVE:
        return "destructive", "deletes a deck; an assistant never applies this by itself"
    if action == "create_folder":
        return "consent", "creates a folder on your Archidekt account; you approve each one yourself"
    if action in COLLABORATOR_ACTIONS:
        return "consent", "changes who can edit your deck on Archidekt; you approve each one yourself"
    return "consent", "acts publicly on Archidekt under your name; you approve each one yourself"


def _social(decks: DeckService) -> Any:
    social = decks.social
    if social is None:
        raise DeckError("unavailable", "Archidekt's social actions are not loaded on this gateway.")
    return social


def _comment_text(text: Any) -> str:
    from .social import MAX_COMMENT, comment_text

    clean = comment_text(str(text or ""))
    if not clean:
        raise DeckError("invalid", refusals.EMPTY_COMMENT)  # Q141 item 7A
    if len(clean) > MAX_COMMENT:
        raise DeckError("invalid", refusals.comment_too_long(MAX_COMMENT))
    return clean


def _vote(value: Any) -> int:
    if str(value) not in VOTES:
        raise DeckError("invalid", "vote must be up, down or none")
    return VOTES[str(value)]


def _owner(deck: Any) -> str:
    return clean_text(str(getattr(deck, "owner", "") or "")) or "its owner"


async def _thread_comment(
    decks: DeckService, sub: str, deck_id: str, comment_id: Any, *, keep_doc: bool = False, page: int = 1
) -> dict[str, Any]:
    if not isinstance(comment_id, int) or isinstance(comment_id, bool) or comment_id <= 0:
        raise DeckError("invalid", "comment_id must be a comment's number (get_deck_comments lists them)")
    found, thread = await _social(decks).find_comment(sub, deck_id, comment_id, page, keep_doc=keep_doc)
    return {**found, "_me": thread.get("me"), "_page": thread["page"]}


def _page(value: Any) -> int:
    """The page of the comment thread a comment was read from (get_deck_comments' page)."""
    if value is None:
        return 1
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 1000:
        raise DeckError("invalid", "page must be the thread page get_deck_comments read (1 or more)")
    return value


def profile_url(username: str) -> str:
    """The person's profile on archidekt.com (/u/<username>, as the site links it), where the
    Follow button is."""
    return "https://archidekt.com/u/" + quote(username, safe="")


async def collaborator_target(
    decks: DeckService, sub: str, deck: Any, action: str, username: Any
) -> tuple[int, str]:
    """(user id, username) of the person a collaborator add or remove on the member's own ``deck``
    is about, checked against Archidekt as it is now: someone the member follows who is not a
    collaborator yet (add), or one of the deck's collaborators (remove)."""
    social = _social(decks)
    want = clean_text(str(username or "")).lstrip("@")
    if not want:
        raise DeckError("invalid", "username is required: the Archidekt username of the person")
    current = await social.collaborators(sub, deck.id, owned=True)
    if action == "collaborator_add":
        people = await social.following_names(sub)
        hits = [i for i, n in people.items() if n.casefold() == want.casefold()]
        if not hits:  # followed a moment ago, after the short-lived list was read
            people = await social.following_names(sub, fresh=True)
            hits = [i for i, n in people.items() if n.casefold() == want.casefold()]
        if not hits:
            raise DeckError(
                "not_found",
                f"{want} is not among the people you follow on Archidekt (the gateway reads the "
                f"first {FOLLOWING_PAGES} pages of that list). Collaborators are added from the "
                "people you follow, as on Archidekt's own settings page: follow them on Archidekt "
                f"first ({profile_url(want)}), then add them.",
                follow_username=want,
                follow_url=profile_url(want),
            )
        user_id, name = hits[0], people[hits[0]]
        if any(c["user_id"] == user_id for c in current):
            raise DeckError("invalid", f"{name} is already a collaborator on this deck")
        return user_id, name
    found = next((c for c in current if c["username"].casefold() == want.casefold()), None)
    if found is None:
        names = ", ".join(c["username"] for c in current) or "none"
        raise DeckError("not_found", f"{want} is not a collaborator on this deck (collaborators: {names})")
    return found["user_id"], found["username"]


async def follow_target(decks: DeckService, sub: str, username: Any) -> tuple[int, str]:
    """(user id, username) of the person to follow by name (Q141 item 6A): one Archidekt read,
    through their public decks, which is the only lookup the gateway has, so someone with no
    public decks is not found."""
    want = clean_text(str(username or "")).lstrip("@")
    if not want:
        raise DeckError("invalid", "Give the person's Archidekt username.")
    found = await decks.user_profile(sub, want)
    name = clean_text(str((found or {}).get("username") or ""))
    if not found or not str(found.get("id") or "").isdigit() or name.casefold() != want.casefold():
        # wording draft (WORDING-6): one text for follow and unfollow
        raise DeckError(
            "not_found",
            f"No Archidekt user called {want} with public decks was found. Check the spelling. The "
            "gateway finds people only through their public decks, so someone with none can't be "
            f"found from here: go to their page on Archidekt instead ({profile_url(want)}).",
            follow_url=profile_url(want),
        )
    return int(found["id"]), name


async def collaborator_by_hand(
    decks: DeckService, sub: str, deck_id: str, action: str, username: Any
) -> dict[str, Any]:
    """The member's own confirmed press on the deck settings page's Collaborators form (0.7.24):
    applied at once, like the page's delete and new-folder forms, through the same code an
    approved proposal runs. Never an assistant's path: the route takes only the signed-in browser
    session with the page's CSRF token, and the deck must be the member's own."""
    if action not in COLLABORATOR_ACTIONS:
        raise DeckError("invalid", "Pick add or remove.")
    # Before 0.7.24 this form made a proposal, which the writes switch held back; keep it so.
    if not decks.settings.writes_enabled:
        raise DeckError("writes_disabled", refusals.WRITES_OFF)  # Q141 item 7A
    social = _social(decks)
    social._me(sub)
    deck = await decks.get_own_deck(sub, _clean_deck_id(str(deck_id or "")))
    user_id, name = await collaborator_target(decks, sub, deck, action, username)
    if action == "collaborator_add":
        result = await social.add_collaborator(sub, deck.id, user_id)
    else:
        result = await social.remove_collaborator(sub, deck.id, user_id)
    return {**result, "user_id": user_id, "username": name}


async def propose(decks: DeckService, sub: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Check the action against Archidekt as it is now, store it as a pending proposal with one
    review row saying exactly what will happen, and return the proposal."""
    if action not in ACTIONS:
        raise DeckError("invalid", f"unknown action {action!r}")
    social = _social(decks) if action not in ("delete_deck", "create_folder") else None
    if social is not None:
        social._me(sub)  # a linked account first: every social action is sent under it
    decks._room_for_proposal(sub)
    changes: dict[str, Any] = {"action": action}
    row: dict[str, Any] = {"action": action, "label": LABELS[action]}
    deck = None
    # Q141 item 6A: follow or unfollow someone by username, not through one of their decks
    by_name = action == "follow" and params.get("username") is not None and not params.get("deck_id")
    if action != "create_folder" and not by_name:
        deck_id = _clean_deck_id(str(params.get("deck_id") or ""))
        deck = (
            await decks.get_own_deck(sub, deck_id)
            if action == "delete_deck" or action in COLLABORATOR_ACTIONS
            else await decks.get_any_deck(sub, deck_id)
        )
        changes["deck_id"] = deck.id
        if social is not None:
            social.note_root(sub, deck)
        # the deck's name is on the card's title and the page's Deck line; it is left out of this
        # sentence so a name written to read like more of it cannot change what the sentence says
        where = f"by {_owner(deck)}"
    if action == "deck_vote":
        vote = _vote(params.get("vote"))
        changes["vote"] = vote
        row["text"] = {
            VOTE_UP: f"Like the deck {where} on Archidekt",
            VOTE_DOWN: f"Vote down the deck {where} on Archidekt",
            VOTE_NONE: f"Take back your vote on the deck {where}",
        }[vote]
        if deck is not None and deck.user_vote == vote:
            row["text"] += " (already the case; applying changes nothing)"
    elif action == "deck_bookmark":
        on = params.get("on") is not False
        changes["on"] = on
        row["text"] = ("Bookmark" if on else "Remove your bookmark from") + f" the deck {where}"
        if deck is not None and deck.bookmarked == on:
            row["text"] += " (already the case; applying changes nothing)"
    elif action == "follow":
        on = params.get("on") is not False
        if by_name:
            owner_id, who = await follow_target(decks, sub, params.get("username"))
        else:
            owner_id = int(deck.owner_id) if deck and str(deck.owner_id or "").isdigit() else None
            who = _owner(deck)
        if owner_id is None:
            raise DeckError(
                "unavailable", "Archidekt does not say who owns this deck, so there is no one to follow"
            )
        state = await social.follow_state(sub, owner_id)
        if state["self"]:
            raise DeckError("invalid", refusals.FOLLOW_SELF)  # Q141 item 7A
        changes.update(user_id=owner_id, on=on)
        if by_name:
            changes["username"] = who
        row["text"] = ("Follow " if on else "Stop following ") + f"{who} on Archidekt"
        if state.get("unknown"):
            # Q141 item 4A (wording draft, WORDING-4)
            row["text"] += " (the gateway could not check whether you already follow them)"
        elif state["following"] == on:
            row["text"] += " (already the case; applying changes nothing)"
    elif action == "comment_post":
        text = _comment_text(params.get("text"))
        if deck.comment_root is None:
            raise DeckError("unavailable", "Archidekt reports no comment thread for this deck")
        parent = params.get("reply_to")
        if parent is not None:
            found = await _thread_comment(decks, sub, deck.id, parent, page=_page(params.get("page")))
            changes["page"] = found["_page"]
            row["text"] = f"Reply publicly to {found['owner']['username'] or 'a comment'} on the deck {where}"
        else:
            row["text"] = f"Post a public comment on the deck {where}"
        changes.update(text=text, parent=parent)
        row["after_text"] = text
    elif action in ("comment_edit", "comment_delete", "comment_vote"):
        found = await _thread_comment(
            decks, sub, deck.id, params.get("comment_id"), keep_doc=True, page=_page(params.get("page"))
        )
        own = found["_me"] is not None and found["owner"]["id"] == found["_me"]
        changes["comment_id"] = found["id"]
        changes["page"] = found["_page"]
        row["before_text"] = found["text"]
        if action == "comment_vote":
            if own:
                raise DeckError("invalid", refusals.OWN_COMMENT_VOTE)  # Q141 item 7A
            if found.get("archived"):
                raise DeckError("invalid", refusals.ARCHIVED_COMMENT)
            vote = _vote(params.get("vote"))
            changes["vote"] = vote
            row["text"] = {
                VOTE_UP: "Vote up",
                VOTE_DOWN: "Vote down",
                VOTE_NONE: "Take back your vote on",
            }[vote] + f" {found['owner']['username'] or 'a'}'s comment on the deck {where}"
        else:
            if not own:
                raise DeckError("forbidden", refusals.NOT_OWN_COMMENT)  # Q141 item 7A
            if action == "comment_edit":
                text = _comment_text(params.get("text"))
                changes["text"] = text
                row["after_text"] = text
                # Q137: its bold text, links and pictures stay where their words did not change;
                # the review page draws the result, and the edit is approved there
                kept = formatting.keep_formatting(found.get("_doc"), text)
                if kept is not None:
                    row["after_doc"], row["dropped_embeds"] = kept
                row["text"] = f"Change your comment on the deck {where}"
            else:
                row["text"] = f"Delete your comment on the deck {where}"
    elif action == "delete_deck":
        cards = sum(c.quantity for c in deck.cards)
        changes["name"] = deck.name
        row["text"] = f"Delete your deck '{deck.name}' ({cards} cards) from your Archidekt account"
        row["note"] = (
            "Archidekt itself has no undo for a deleted deck. The gateway keeps a snapshot first"
            + (" and a backup copy on Archidekt" if decks.live.get("archidekt_backups") else "")
            + "."
        )
    elif action == "create_folder":
        name = decks._folder_name(params.get("name"))
        info = await decks.folders(sub)
        inside = clean_text(str(params.get("inside") or ""))
        if inside:
            shown = [f for f in info["folders"] if f["depth"] and not f.get("backup")]  # Q21
            hits = [f for f in shown if f["name"].casefold() == inside.casefold()]
            if len(hits) != 1:
                names = ", ".join(f"'{f['name']}'" for f in shown) or "none"
                raise DeckError(
                    "not_found" if not hits else "invalid",
                    f"inside must name exactly one of your folders (yours: {names})",
                )
            parent = hits[0]
        else:
            parent = info["folders"][0]
        # refused now, not only at apply, so the assistant can pick another name (B8 a)
        decks._not_the_backup_name(name, parent["depth"] + 1)
        siblings = [f["name"].casefold() for f in info["folders"] if f["parent"] == parent["id"]]
        if name.casefold() in siblings:
            raise DeckError("invalid", f"There is already a folder called '{name}' there.")
        changes.update(name=name, parent=parent["id"])
        where_to = "your top level" if not parent["depth"] else f"'{parent['name']}'"
        row["text"] = f"Create the folder '{name}' in {where_to}"
    elif action in COLLABORATOR_ACTIONS:
        user_id, name = await collaborator_target(decks, sub, deck, action, params.get("username"))
        if action == "collaborator_add":
            row["text"] = f"Let {name} edit your deck on Archidekt as a collaborator"
            row["note"] = COLLABORATOR_NOTE
        else:
            row["text"] = f"Stop {name} editing your deck on Archidekt"
        changes.update(user_id=user_id, username=name)
    if action in CANNOT_UNDO:
        row["cannot_undo"] = True
    if action in DESTRUCTIVE:
        row["destructive"] = True
    pid = secrets.token_urlsafe(12)
    decks._save_proposal(
        {
            "id": pid,
            "owner_sub": sub,
            "kind": "action",
            "deck_id": deck.id if deck else ACCOUNT_TARGET,
            "deck_name": deck.name if deck else ACCOUNT_NAME,
            "baseline_fingerprint": deck.fingerprint() if action == "delete_deck" else "",
            "changes": changes,
        },
        [_row("action", **row)],
    )
    decks._audit("proposal_created", sub=sub, detail={"proposal_id": pid, "kind": "action", "action": action})
    out = decks.describe(sub, pid)
    # Q90: a comment given as a Quill document is posted as its words alone; the assistant (the
    # only caller) is told when links, formatting or embeds were lost, so it does not claim them
    if action in ("comment_post", "comment_edit") and drops_formatting(params.get("text")):
        out["note"] = words_only_note("comment")
    return out


async def apply(
    decks: DeckService, sub: str, row: dict[str, Any], progress: dict[str, Any]
) -> dict[str, Any]:
    """Do one stored action, through the same code the gateway's own pages use, which checks it
    again against Archidekt as it is now."""
    c = row.get("changes") or {}
    action = c.get("action")
    deck_id = str(c.get("deck_id") or "")
    result: dict[str, Any]
    if action == "deck_vote":
        result = await _social(decks).vote(sub, deck_id, int(c["vote"]))
    elif action == "deck_bookmark":
        result = await _social(decks).bookmark(sub, deck_id, bool(c["on"]))
    elif action == "follow":
        result = await _social(decks).follow(sub, int(c["user_id"]), bool(c["on"]))
    elif action == "comment_post":
        out = await _social(decks).comment(
            sub, deck_id, str(c["text"]), c.get("parent"), int(c.get("page") or 1)
        )
        result = {"comment_id": out["comment"]["id"], "parent": out["parent"]}
    elif action == "comment_edit":
        # Q137: the comment's formatting is kept as the review showed it (after_doc), or not sent
        reviewed = next((r for r in row.get("rows") or [] if r.get("kind") == "action"), {})
        try:
            out = await _social(decks).edit_comment(
                sub,
                deck_id,
                int(c["comment_id"]),
                str(c["text"]),
                expect_doc=str(reviewed.get("after_doc") or ""),
                page=int(c.get("page") or 1),
            )
        except DeckError as exc:
            if exc.kind == "stale":
                raise needs_look_error(COMMENT_CHANGED) from exc
            raise
        result = {"comment_id": out["comment"]["id"], "verified": True}
    elif action == "comment_delete":
        out = await _social(decks).delete_comment(sub, deck_id, int(c["comment_id"]), int(c.get("page") or 1))
        result = {"deleted": out["deleted"], "verified": True}
    elif action == "comment_vote":
        result = await _social(decks).vote_comment(
            sub, deck_id, int(c["comment_id"]), int(c["vote"]), int(c.get("page") or 1)
        )
    elif action == "delete_deck":
        deck = await decks.get_own_deck(sub, deck_id)
        if deck.fingerprint() != row["baseline_fingerprint"]:
            raise needs_look_error(None)  # a deck deletion stays strict (K1): any change stops it
        try:
            # progress: the snapshot and the backup copy are recorded as every apply's are (review
            # L1), so a stop that changed nothing deletes the copy or queues its delete (Q77)
            result = {
                **(await decks.delete_deck(sub, deck_id, deck.name, progress=progress)),
                "verified": True,
            }
        except DeckError as exc:
            if exc.kind == "backup_failed":  # nothing was deleted: the member can try again or reject
                decks.db.finish_proposal(
                    row["id"], state="pending", result={"error": "backup_failed", "detail": str(exc)}
                )
            raise
    elif action == "collaborator_add":
        result = await _social(decks).add_collaborator(sub, deck_id, int(c["user_id"]))
    elif action == "collaborator_remove":
        result = await _social(decks).remove_collaborator(sub, deck_id, int(c["user_id"]))
    elif action == "create_folder":
        folder = await decks.create_folder(sub, str(c["name"]), int(c["parent"]))
        result = {"folder_id": folder["id"], "name": folder["name"], "verified": True}
    else:
        raise DeckError("invalid", "This proposal names an action this gateway does not know.")
    decks.db.finish_proposal(row["id"], state="applied", snapshot_id=result.get("snapshot_id"), result=result)
    return decks.describe(sub, row["id"])
