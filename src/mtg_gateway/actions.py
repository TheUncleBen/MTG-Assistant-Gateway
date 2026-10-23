"""Archidekt account actions an assistant can propose (0.7.17): like or vote down a deck, bookmark
it, follow its owner, post, edit, delete or vote on a comment, delete a deck and create a
folder; 0.7.20 adds and removes a deck's collaborators. Each is a proposal of kind ``action``
with one review row, so it goes through the same approval flow as every other write, but
only the member's own press applies one: on the in-chat card (0.7.29) or on the signed-in
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
from .social import FOLLOWING_LOOKUP_PAGES

if TYPE_CHECKING:
    from .decks import DeckService

ACCOUNT_TARGET = "account"  # deck_id of a proposal that is about the account, not one deck
ACCOUNT_NAME = "Your Archidekt account"
# A comment edit that keeps formatting whose comment changed since its review: its
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
    # 0.7.43 (Archidekt parity release 2)
    "deck_help": "Deck help request",
    "report": "Report",
    "profile": "Profile and settings",
    "contest_enter": "Contest entry",
    "contest_withdraw": "Withdraw contest entry",
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


# A deck help request, as archidekt.com's own form checks it (read from its scripts, 2026-10-22):
# goals and issues of 10 to 200 words each, a deck with at least one tag, and a deck size of 80
# to 120 cards in a Commander-style format, else 50 to 85. It is listed for 7 days.
HELP_WORDS = (10, 200)
HELP_CHARS = 4000  # a cap on each box as well (security review 0.7.44): 200 words fit easily
HELP_SIZE_COMMANDER = (80, 120)
HELP_SIZE_OTHER = (50, 85)
# The Commander-style formats, by the gateway's format names (archidekt.FORMAT_IDS): the site's
# own list was not readable in its scripts, so this is the gateway's reading of "commander-type"
HELP_COMMANDER_FORMATS = frozenset(
    {
        "commander",
        "1v1",
        "duel",
        "brawl",
        "oathbreaker",
        "paupercommander",
        "historicbrawl",
        "predh",
        "competitivebrawl",
        "canlander",
    }
)
HELP_ACTIONS = ("post", "edit", "delist")
# What a report is about, and the type Archidekt's report box sends for it
REPORT_KINDS = {"deck": "Deck", "card": "Card", "custom_card": "Custom card", "comment": "Comment"}
REPORT_MESSAGE_MAX = 1000
CONTEST_SUMMARY_MAX = 5000  # the gateway's own limit: Archidekt's is unknown
CONTEST_ACTIONS = ("contest_enter", "contest_withdraw")


def contest_thread(value: Any) -> int:
    """A contest's thread id: the number, or a contest page's address ending in it."""
    import re

    m = re.search(r"(\d{1,12})/?$", str(value or "").strip())
    if not m or int(m.group(1)) <= 0:
        raise DeckError("invalid", "Give the contest's id or its page address.")
    return int(m.group(1))


async def contest_entry(decks: DeckService, sub: str, thread_id: int, deck_id: str) -> dict[str, Any] | None:
    """The member's entry of this deck in the contest, as Archidekt lists the entries now."""
    from . import profile

    uid = profile.user_id(decks, sub)
    rows = await decks._call_unslotted(sub, lambda token, *_: decks.client.contest_entries(token, thread_id))
    for e in rows:
        deck = e.get("deck") if isinstance(e, dict) else None
        owner = e.get("owner") if isinstance(e, dict) else None
        did = deck.get("id") if isinstance(deck, dict) else deck
        oid = owner.get("id") if isinstance(owner, dict) else owner
        if str(did) == str(deck_id) and str(oid) == str(uid):
            return e
    return None


def _words(text: str) -> int:
    return len(text.split())


def _help_text(value: Any, what: str) -> str:
    raw = str(value or "")
    if len(raw) > HELP_CHARS:  # before any cleaning: a 200-"word" box of megabytes is refused at once
        raise DeckError("invalid", f"Messages can be up to {HELP_CHARS:,} characters.")
    text = clean_text(raw)
    low, high = HELP_WORDS
    if not low <= _words(text) <= high:
        raise DeckError("invalid", f"{what} must be {low} to {high} words (it has {_words(text)})")
    return text


def _help_checks(deck: Any) -> None:
    """archidekt.com's own checks before a help request is posted."""
    if not deck.tags:
        raise DeckError("invalid", "Archidekt asks for at least one deck tag before a help request.")
    low, high = HELP_SIZE_COMMANDER if (deck.format or "") in HELP_COMMANDER_FORMATS else HELP_SIZE_OTHER
    size = sum(c.quantity for c in deck.main_cards)
    if not low <= size <= high:
        raise DeckError(
            "invalid",
            f"Archidekt asks for a deck of {low} to {high} cards for a help request (this one has {size}).",
        )


def deck_help_of(deck: Any) -> dict[str, Any] | None:
    """The deck's current help request as the deck JSON carries it (``deckHelp``), or None."""
    found = (deck.raw or {}).get("deckHelp") if isinstance(getattr(deck, "raw", None), dict) else None
    if not isinstance(found, dict) or not isinstance(found.get("id"), int):
        return None
    from .archidekt import rich_text

    return {
        "id": found["id"],
        "goals": rich_text(found.get("goals") or ""),
        "issues": rich_text(found.get("issues") or ""),
        "active": found.get("active") is not False,
        "created_at": str(found.get("createdAt") or ""),
    }


def report_url(kind: str, deck_id: str | None, comment_id: int | None) -> str:
    """The page a report names, as archidekt.com's report box sends it (the page it was opened
    on): the deck's page for a deck, card or custom card in it, the comment's thread for a
    comment."""
    if kind == "comment":
        return f"https://archidekt.com/forum/thread/{int(comment_id or 0)}"
    return f"https://archidekt.com/decks/{deck_id}"


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
        raise DeckError("invalid", refusals.EMPTY_COMMENT)
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
        found = await social.find_followed(sub, want)
        if found is None:
            if social.following_cut(sub):
                # the list goes on past the pages read: they may be followed further down
                missing = (
                    f"{want} isn't in the first {FOLLOWING_LOOKUP_PAGES} pages of the people you follow "
                    "on Archidekt, which is as far as the gateway reads, so they can't be added here. "
                    "Collaborators are added from the people you follow, as on Archidekt's own "
                    "settings page: if you don't follow them yet, follow them on Archidekt first "
                )
            else:
                missing = (
                    f"{want} is not among the people you follow on Archidekt. Collaborators are added "
                    "from the people you follow, as on Archidekt's own settings page: follow them on "
                    "Archidekt first "
                )
            raise DeckError(
                "not_found",
                f"{missing}({profile_url(want)}), then add them. Someone you followed on "
                "archidekt.com very recently may not be in the list the gateway last read.",
                follow_username=want,
                follow_url=profile_url(want),
            )
        user_id, name = found
        if any(c["user_id"] == user_id for c in current):
            raise DeckError("invalid", f"{name} is already a collaborator on this deck")
        return user_id, name
    found = next((c for c in current if c["username"].casefold() == want.casefold()), None)
    if found is None:
        names = ", ".join(c["username"] for c in current) or "none"
        raise DeckError("not_found", f"{want} is not a collaborator on this deck (collaborators: {names})")
    return found["user_id"], found["username"]


async def follow_target(decks: DeckService, sub: str, username: Any) -> tuple[int, str]:
    """(user id, username) of the person to follow by name: one Archidekt read,
    through their public decks, which is the only lookup the gateway has, so someone with no
    public decks is not found."""
    want = clean_text(str(username or "")).lstrip("@")
    if not want:
        raise DeckError("invalid", "Give the person's Archidekt username.")
    found = await decks.user_profile(sub, want)
    name = clean_text(str((found or {}).get("username") or ""))
    if not found or not str(found.get("id") or "").isdigit() or name.casefold() != want.casefold():
        # one text for follow and unfollow
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
        raise DeckError("writes_disabled", refusals.WRITES_OFF)
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
    social = (
        _social(decks)
        if action not in ("delete_deck", "create_folder", "deck_help", "report", "profile", *CONTEST_ACTIONS)
        else None
    )
    if social is not None:
        social._me(sub)  # a linked account first: every social action is sent under it
    decks._room_for_proposal(sub)
    changes: dict[str, Any] = {"action": action}
    row: dict[str, Any] = {"action": action, "label": LABELS[action]}
    deck = None
    # Follow or unfollow someone by username, not through one of their decks
    by_name = action == "follow" and params.get("username") is not None and not params.get("deck_id")
    if action not in ("create_folder", "profile") and not by_name:
        deck_id = _clean_deck_id(str(params.get("deck_id") or ""))
        deck = (
            await decks.get_own_deck(sub, deck_id)
            if action in ("delete_deck", "deck_help", *CONTEST_ACTIONS) or action in COLLABORATOR_ACTIONS
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
        state = await social.follow_state(sub, owner_id, lookup=True)
        if state["self"]:
            raise DeckError("invalid", refusals.FOLLOW_SELF)
        changes.update(user_id=owner_id, on=on)
        if by_name:
            changes["username"] = who
        row["text"] = ("Follow " if on else "Stop following ") + f"{who} on Archidekt"
        if state.get("unknown"):
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
                raise DeckError("invalid", refusals.OWN_COMMENT_VOTE)
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
                raise DeckError("forbidden", refusals.NOT_OWN_COMMENT)
            if action == "comment_edit":
                text = _comment_text(params.get("text"))
                changes["text"] = text
                row["after_text"] = text
                # Its bold text, links and pictures stay where their words did not change;
                # the review page draws the result, and the edit is approved there
                kept = formatting.keep_formatting(found.get("_doc"), text)
                if kept is not None:
                    row["after_doc"], row["dropped_embeds"] = kept
                elif text and formatting.is_formatted(found.get("_doc")):
                    # as for descriptions: a comment rewrite that keeps none of its formatting says what goes
                    row["formatting_lost"] = True
                    row["dropped_embeds"] = formatting.embeds(found.get("_doc"))
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
            shown = [f for f in info["folders"] if f["depth"] and not f.get("backup")]
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
        # refused now, not only at apply, so the assistant can pick another name
        decks._not_the_backup_name(name, parent["depth"] + 1)
        siblings = [f["name"].casefold() for f in info["folders"] if f["parent"] == parent["id"]]
        if name.casefold() in siblings:
            raise DeckError("invalid", f"There is already a folder called '{name}' there.")
        changes.update(name=name, parent=parent["id"])
        where_to = "your top level" if not parent["depth"] else f"'{parent['name']}'"
        row["text"] = f"Create the folder '{name}' in {where_to}"
    elif action == "deck_help":
        what = str(params.get("help_action") or "post")
        if what not in HELP_ACTIONS:
            raise DeckError("invalid", "help_action must be post, edit or delist")
        current = deck_help_of(deck)
        if what == "post":
            if current is not None and current["active"]:
                raise DeckError("invalid", "This deck already has a help request listed: edit or delist it.")
            _help_checks(deck)
            goals, issues = (
                _help_text(params.get("goals"), "goals"),
                _help_text(params.get("issues"), "issues"),
            )
            changes.update(help_action=what, goals=goals, issues=issues)
            if current is not None:  # a delisted or expired one is opened again
                changes["help_id"] = current["id"]
            row["text"] = f"Ask for help with your deck publicly on Archidekt's deck help board {where}"
            row["after_text"] = f"Goals: {goals}\n\nIssues: {issues}"
            row["note"] = "Listed for 7 days, under your name."
        else:
            if current is None:
                raise DeckError("not_found", "This deck has no help request.")
            if what == "edit":
                goals, issues = (
                    _help_text(params.get("goals"), "goals"),
                    _help_text(params.get("issues"), "issues"),
                )
                row["text"] = f"Change your public help request for your deck {where}"
                row["before_text"] = f"Goals: {current['goals']}\n\nIssues: {current['issues']}"
                row["after_text"] = f"Goals: {goals}\n\nIssues: {issues}"
            else:
                if not current["active"]:
                    raise DeckError("invalid", "This deck's help request is not listed.")
                goals, issues = current["goals"], current["issues"]
                row["text"] = f"Take down your help request for your deck {where}"
            changes.update(help_action=what, help_id=current["id"], goals=goals, issues=issues)
    elif action == "report":
        kind = str(params.get("report_kind") or "")
        if kind not in REPORT_KINDS:
            raise DeckError("invalid", "report_kind must be deck, card, custom_card or comment")
        message = clean_text(str(params.get("message") or ""))
        if not message:
            raise DeckError("invalid", "Say what is wrong: a report needs a message.")
        if len(message) > REPORT_MESSAGE_MAX:
            raise DeckError("invalid", f"A report's message must be at most {REPORT_MESSAGE_MAX} characters.")
        about = ""
        comment_id = None
        if kind in ("card", "custom_card"):
            card = clean_text(str(params.get("card") or ""))
            hit = next((c for c in deck.cards if c.name.casefold() == card.casefold()), None)
            if hit is None:
                raise DeckError("not_found", f"“{card}” is not in this deck.")
            about = f"the card “{hit.name}” in the deck {where}"
            # Archidekt's own box sends the page's address only: the card is named in the message
            message = f"{hit.name}: {message}"
        elif kind == "comment":
            found = await _thread_comment(
                decks, sub, deck.id, params.get("comment_id"), page=_page(params.get("page"))
            )
            comment_id = found["id"]
            changes["page"] = found["_page"]
            about = f"{found['owner']['username'] or 'a'}'s comment on the deck {where}"
            row["before_text"] = found["text"]
        else:
            about = f"the deck {where}"
        url = report_url(kind, deck.id, comment_id)
        changes.update(report_kind=kind, message=message, url=url, comment_id=comment_id)
        row["text"] = f"Report {about} to Archidekt's moderators"
        row["after_text"] = message
        row["note"] = "Archidekt's moderators see your username with the report. It can't be taken back."
        row["cannot_undo"] = True
    elif action in CONTEST_ACTIONS:
        from .archidekt import rich_text

        thread = contest_thread(params.get("contest"))
        async with decks.archidekt_slot(sub):
            entry = await contest_entry(decks, sub, thread, deck.id)
        changes["thread_id"] = thread
        if action == "contest_enter":
            if entry is not None:
                raise DeckError("invalid", "This deck is entered in that contest already.")
            summary = summary_text(params.get("summary"))
            changes["summary"] = summary
            row["text"] = f"Enter your deck {where} in the contest (thread {thread}) on Archidekt, publicly"
            row["before_text"] = rich_text(deck.description) or "(empty)"
            row["after_text"] = summary
            if formatting.is_formatted(deck.description):
                # plain words replace links, bold and pictures: approved on the review page only, as
                # a description edit that loses its formatting is (security review 0.7.44)
                row["formatting_lost"] = True
                row["dropped_embeds"] = formatting.embeds(deck.description)
            row["note"] = (
                "Archidekt's contest entry replaces the deck's description with your summary. The gateway "
                "keeps a snapshot first, so History can put the old description back."
            )
        else:
            if entry is None:
                raise DeckError("not_found", "This deck has no entry in that contest.")
            changes["entry_id"] = int(entry["id"])
            row["text"] = f"Withdraw your deck {where} from the contest (thread {thread}) on Archidekt"
            row["note"] = "Archidekt says you can enter again after withdrawing."
    elif action == "profile":
        from . import profile

        field = params.get("field")
        body, words = profile.plan(field, params, await profile.read(decks, sub))
        if words.pop("same"):
            raise DeckError(
                "invalid", "Your Archidekt account is already set that way; nothing would change."
            )
        changes.update(field=field, body=body)
        row.update(words)
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
    # A comment given as a Quill document is posted as its words alone; the assistant (the
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
        # The comment's formatting is kept as the review showed it (after_doc), or not sent
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
            raise needs_look_error(None)  # a deck deletion stays strict: any change stops it
        try:
            # progress: the snapshot and the backup copy are recorded as every apply's are,
            # so a stop that changed nothing deletes the copy or queues its delete
            result = {
                **(
                    await decks.delete_deck(sub, deck_id, deck.name, progress=progress, proposal_id=row["id"])
                ),
                "verified": True,
            }
        except DeckError as exc:
            if exc.kind == "backup_failed":  # nothing was deleted: the member can try again or reject
                decks.db.finish_proposal(
                    row["id"], state="pending", result={"error": "backup_failed", "detail": str(exc)}
                )
            raise
    elif action == "deck_help":
        result = await _apply_help(decks, sub, deck_id, c)
    elif action == "report":
        decks._writes_switch()
        await decks._call_unslotted(
            sub,
            lambda token, *_: decks.client.report(
                token, str(c["url"]), REPORT_KINDS[str(c["report_kind"])], str(c["message"])
            ),
        )
        result = {"reported": True, "verified": True}  # Archidekt keeps no list of reports to read back
    elif action == "profile":
        from . import profile

        result = await profile.send(decks, sub, dict(c["body"]))
    elif action == "contest_enter":
        result = await _apply_contest_enter(decks, sub, deck_id, c, row["id"])
    elif action == "contest_withdraw":
        decks._writes_switch()
        thread = int(c["thread_id"])
        entry = await contest_entry(decks, sub, thread, deck_id)
        if entry is None or int(entry["id"]) != int(c["entry_id"]):
            raise needs_look_error("The contest entry changed on Archidekt since this was reviewed.")
        await decks._call_unslotted(
            sub, lambda token, *_: decks.client.contest_withdraw(token, int(c["entry_id"]))
        )
        result = {"verified": await contest_entry(decks, sub, thread, deck_id) is None}
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


async def _apply_help(decks: DeckService, sub: str, deck_id: str, c: dict[str, Any]) -> dict[str, Any]:
    """Post, edit or delist a deck help request, then read the deck back for it."""
    decks._writes_switch()
    deck = await decks.get_own_deck(sub, deck_id)
    current = deck_help_of(deck)
    what = str(c["help_action"])
    goals, issues = str(c["goals"]), str(c["issues"])
    client = decks.client
    if what == "post" and current is None:
        _help_checks(deck)
        await decks._call_unslotted(
            sub, lambda token, *_: client.create_deck_help(token, deck_id, goals, issues)
        )
    else:
        if current is None or (c.get("help_id") is not None and current["id"] != c["help_id"]):
            raise needs_look_error("The deck's help request changed on Archidekt since this was reviewed.")
        if what == "post":
            _help_checks(deck)
        await decks._call_unslotted(
            sub,
            lambda token, *_: client.update_deck_help(
                token, current["id"], goals, issues, active=what != "delist"
            ),
        )
    back = deck_help_of(await decks.get_deck(sub, deck_id))
    want_active = what != "delist"
    ok = (
        back is not None
        and back["active"] == want_active
        and (not want_active or (back["goals"].strip() == goals and back["issues"].strip() == issues))
    )
    return {"help_action": what, "verified": ok}


def summary_text(value: Any) -> str:
    """A contest summary as plain lines (a Quill document reduced to its words), required."""
    from .archidekt import plain_text

    lines = plain_text(str(value or "")).strip().splitlines()
    text = "\n".join(clean_text(line) for line in lines).strip()
    if not text:
        raise DeckError("invalid", "A contest entry needs a summary.")
    if len(text) > CONTEST_SUMMARY_MAX:
        raise DeckError("invalid", f"A contest summary is at most {CONTEST_SUMMARY_MAX} characters here.")
    return text


async def _apply_contest_enter(
    decks: DeckService, sub: str, deck_id: str, c: dict[str, Any], proposal_id: str
) -> dict[str, Any]:
    """As the site enters a contest: the deck's description becomes the summary, then the entry is
    sent. A snapshot of the deck is kept first. (update_deck writes the summary as the plain
    Quill document the gateway's description edits send.)"""
    from . import profile
    from .archidekt import rich_text

    decks._writes_switch()
    thread = int(c["thread_id"])
    deck = await decks.get_own_deck(sub, deck_id)
    if await contest_entry(decks, sub, thread, deck.id) is not None:
        raise needs_look_error("This deck was entered in that contest on Archidekt since this was reviewed.")
    snapshot_id = decks._hand_snapshot(sub, deck, proposal_id)
    summary = str(c["summary"])
    await decks._call_unslotted(
        sub, lambda token, *_: decks.client.update_deck(token, deck.id, {"description": summary})
    )
    uid = profile.user_id(decks, sub)
    await decks._call_unslotted(
        sub, lambda token, *_: decks.client.contest_submit(token, uid, thread, deck.id)
    )
    back = await decks.get_deck(sub, deck.id)
    entered = await contest_entry(decks, sub, thread, deck.id) is not None
    return {
        "snapshot_id": snapshot_id,
        "verified": entered and rich_text(back.description).strip() == summary.strip(),
    }
