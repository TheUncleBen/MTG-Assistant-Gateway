"""Companion pages: a deck's settings (details, cover, tags, folder, collaborators, delete) and folders."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .actions import COLLABORATOR_NOTE, collaborator_by_hand, collaborator_target, profile_url
from .archidekt import FORMAT_IDS, ArchidektError, Deck, featured_scryfall_id, format_label, rich_text
from .busy import busy_response
from .companion import DECK_CSP, FORMAT_CHOICES, INDENT, LIST_OK_MESSAGES, SETTINGS_OK_MESSAGES
from .deckpage import (
    card_image,
)
from .decks import DeckError, current_client
from .folderview import visible_folders
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    browser_session,
    login_redirect,
)
from .theme import (
    esc as _esc,
)
from .theme import (
    icon,
    plural,
)

NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

# The section of the settings page each ?ok= notice belongs to: the forms there send the member back
# to their own section (#cover, #tags ...), so the notice shows next to the form that was used.
OK_SECTIONS = {
    "cover": "cover",
    "cover_auto": "cover",
    "tag_added": "tags",
    "tag_removed": "tags",
    "moved": "folder",
    "collaborator_added": "collaborators",
    "collaborator_removed": "collaborators",
}


def add_settings_routes(server: MCPServer, state: AppState, h: SimpleNamespace) -> None:
    """Deck settings, hand actions, delete and folders."""
    EXPIRED = h.EXPIRED
    apply_now = h.apply_now
    check_csrf = h.check_csrf
    decks = h.decks
    form = h.form
    link_prompt = h.link_prompt
    my_decks = h.my_decks
    page = h.page
    s = h.s

    # -- deck settings (details proposal) ---------------------------------------
    def settings_form(
        deck: Deck,
        csrf: str | None,
        values: dict[str, str] | None = None,
        error: str = "",
        *,
        error_in: str = "",
        folders: dict[str, Any] | None = None,
        collaborators: list[dict[str, Any]] | None = None,
        following: list[str] | None = None,
        follow_hint: str = "",
        typed_tag: str = "",
        ok: str = "",
    ) -> str:
        """The settings page. ``error`` shows at the top (the main settings form's own refusal) or,
        with ``error_in``, inside that section next to its form, as the ?ok= notices of the section
        forms do (OK_SECTIONS): those forms land on their own section, below the top of the page."""
        v = values or {}

        def note(section: str) -> str:
            if error and error_in == section:
                return f"<p class='notice error' id='{section}-msg' role='alert'>{_esc(error)}</p>"
            if not error and ok in SETTINGS_OK_MESSAGES and OK_SECTIONS.get(ok) == section:
                return f"<p class='notice ok' role='status'>{_esc(SETTINGS_OK_MESSAGES[ok])}</p>"
            return ""

        fmt_current = v.get("deck_format", deck.format or "")
        description = v.get("description", rich_text(deck.description))  # its text (Quill on Archidekt)

        def sel(flag: bool) -> str:
            return " selected" if flag else ""

        # A deck with no format (or one this list doesn't know) shows that, selected, rather than
        # the browser falling back to the first format; an empty value leaves the format alone.
        fmt_opts = (
            "" if fmt_current in FORMAT_CHOICES else "<option value='' selected>No format set</option>"
        ) + (
            "".join(
                f"<option value='{_esc(n)}'{sel(fmt_current == n)}>{_esc(format_label(n))}</option>"
                for n in FORMAT_CHOICES
            )
        )
        bracket_current = v.get("edh_bracket", str(deck.edh_bracket or ""))
        brackets = {
            "": "No bracket set",
            "1": "Exhibition (1)",
            "2": "Core (2)",
            "3": "Upgraded (3)",
            "4": "Optimized (4)",
            "5": "cEDH (5)",
        }
        bracket_opts = "".join(
            f"<option value='{k}'{' selected' if bracket_current == k else ''}>{_esc(label)}</option>"
            for k, label in brackets.items()
        )
        # a submitted form carries no key for an unchecked box, so with values a missing key is off
        private = v.get("private", "") if values else ("1" if deck.private else "")
        unlisted = v.get("unlisted", "") if values else ("1" if deck.unlisted else "")
        did = _esc(deck.id)
        cats = "".join(
            f"<li><span class='name'>{_esc(c.get('name'))}</span>"
            + ("<span class='badge warn'>premier</span>" if c.get("isPremier") else "")
            + ("" if c.get("includedInDeck", True) else "<span class='badge'>not in deck</span>")
            + ("" if c.get("includedInPrice", True) else "<span class='badge'>not in price</span>")
            + "</li>"
            for c in deck.categories
            if isinstance(c.get("name"), str)
        )
        return (
            (f"<p class='notice error'>{_esc(error)}</p>" if error and not error_in else "")
            + (
                f"<p class='notice ok'>{_esc(SETTINGS_OK_MESSAGES[ok])}</p>"
                if ok in SETTINGS_OK_MESSAGES and ok not in OK_SECTIONS
                else ""
            )
            + f"<form method='post' action='/decks/{did}/settings' class='panel settings'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<h2>Main settings</h2>"
            "<label for='name'>Deck name</label>"
            f"<input id='name' type='text' name='name' value='{_esc(v.get('name', deck.name))}' "
            "required maxlength='200'>"
            f"<div class='field'><label for='deck_format'>Format</label><select id='deck_format' "
            "name='deck_format'>"
            f"{fmt_opts}</select></div>"
            "<div class='field'><label for='edh_bracket'>Commander bracket</label>"
            f"<select id='edh_bracket' name='edh_bracket'>{bracket_opts}</select>"
            "<p class='muted small'>Your own bracket, for Commander decks only: changing the format "
            "clears it. It does not influence the estimated bracket on the deck page."
            "</p></div>"
            "<label for='description'>Description</label>"
            f"<textarea id='description' name='description' rows='10'>{_esc(description)}"
            "</textarea>"
            "<label class='switch'><input type='checkbox' name='private' value='1'"
            + (" checked" if private else "")
            + "><span class='track'></span>Private <span class='muted small'>(the deck is only "
            "visible to you)"
            "</span></label>"
            "<label class='switch'><input type='checkbox' name='unlisted' value='1'"
            + (" checked" if unlisted else "")
            + "><span class='track'></span>Unlisted <span class='muted small'>(viewable with a "
            "direct link, but "
            "not shown in lists)</span></label>"
            f"<div class='actions'><button class='primary'>{icon('check')} Save changes</button>"
            f"<a class='btn' href='/decks/{did}'>Cancel</a></div>"
            "<p class='muted small'>Saved to Archidekt when you press Save changes; a snapshot from just "
            "before is kept under History.</p></form>"
            "<section class='panel' id='categories'><h2>Categories</h2>"
            f"<ul class='plain plist'>{cats or '<li class=muted>No categories yet.</li>'}</ul>"
            "<p class='muted small'>Cards are moved between categories in the editor; a new category "
            "is created "
            "by typing its name there. Renaming, deleting and the premier / in-deck / in-price flags are not "
            "available through the gateway yet.</p></section>"
            + cover_section(deck, csrf, note("cover"))
            + tags_section(deck, csrf, note("tags"), typed_tag if error_in == "tags" else "")
            + folder_section(deck, csrf, folders, note("folder"))
            + collaborator_section(
                deck,
                csrf,
                collaborators,
                following,
                follow_hint if error_in == "collaborators" else "",
                note("collaborators"),
            )
            + "<section class='panel danger' id='delete'><h2>Delete this deck</h2>"
            "<p class='muted small'>Deletes the deck on Archidekt after you type its name. A snapshot is "
            "kept under History"
            + (
                ", and a copy in your backup folder on Archidekt"
                if state.live.get("archidekt_backups")
                else ""
            )
            + ".</p>"
            f"<a class='btn btn-danger' href='/decks/{did}/delete'>{icon('trash')} Delete deck…</a></section>"
        )

    def cover_section(deck: Deck, csrf: str | None, note: str = "") -> str:
        """Pick the deck's cover image from its cards (Archidekt's "deck image"), or let Archidekt pick."""
        did = _esc(deck.id)
        chosen = (featured_scryfall_id(deck.featured) or "").lower()
        current = next((c for c in deck.cards if c.scryfall_uid.lower() == chosen), None) if chosen else None
        preview = card_image(current, "art_crop") if current else None
        seen: set[str] = set()
        options = []
        for c in sorted(deck.cards, key=lambda c: ("Commander" not in c.categories, c.name.lower())):
            uid = c.scryfall_uid.lower()
            if not uid or uid in seen or not card_image(c):
                continue
            seen.add(uid)
            label = c.name + (" (commander)" if "Commander" in c.categories else "")
            options.append(
                f"<option value='{_esc(uid)}'{' selected' if uid == chosen else ''}>{_esc(label)}</option>"
            )
        return (
            "<section class='panel coverbox' id='cover'><h2>Cover image</h2>"
            f"<form method='post' action='/decks/{did}/cover#cover' class='coverform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            + (
                f"<img class='coverart' src='{_esc(preview)}' alt='Current cover: {_esc(current.name)}'>"
                if preview and current
                else "<div class='coverart none'><span class='muted small'>Archidekt picks the image"
                "</span></div>"
            )
            + "<div class='field grow'><label for='cover_card'>Card whose art fronts the deck</label>"
            "<span class='sel'><select id='cover_card' name='card'>"
            f"<option value=''{'' if chosen else ' selected'}>Automatic (Archidekt picks)</option>"
            + "".join(options)
            + "</select></span></div>"
            f"<button class='primary'>{icon('image')} Set cover</button></form>"
            + note
            + "<p class='muted small'>Shown on the deck page, in your deck lists and on Archidekt.</p>"
            "</section>"
        )

    def tags_section(deck: Deck, csrf: str | None, note: str = "", typed: str = "") -> str:
        did = _esc(deck.id)
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        items = "".join(
            f"<li><span class='pill'>{_esc(r.get('name'))}</span>"
            f"<form method='post' action='/decks/{did}/tags#tags' class='inline'>{csrf_in}"
            "<input type='hidden' name='action' value='remove'>"
            f"<input type='hidden' name='relation_id' value='{int(r['id'])}'>"
            f"<button class='mini' aria-label='Remove tag {_esc(r.get('name'))}' title='Remove tag'>"
            f"{icon('x')}</button>"
            "</form></li>"
            for r in deck.tag_relations
            if isinstance(r.get("id"), int) and r.get("name")
        )
        # The box suggests Archidekt's tags as the member types (static/suggest.js over
        # /decks/api/tags), less the deck's own; without scripts, or when the lookup fails, it is a
        # plain box and a name Archidekt lacks is refused with its closest tags.
        taken = [str(r["name"]) for r in deck.tag_relations if r.get("name")]
        bad = "id='tags-msg'" in note
        return (
            f"<section class='panel tagbox' id='tags'><h2>Deck tags</h2>"
            f"<ul class='plain taglist'>{items or '<li class=muted>No deck tags yet.</li>'}</ul>"
            f"<form method='post' action='/decks/{did}/tags#tags' class='addtag'>{csrf_in}"
            "<input type='hidden' name='action' value='add'>"
            "<div class='field grow'><label for='tagname'>Add a tag</label>"
            "<input id='tagname' type='text' name='name' maxlength='40' autocomplete='off' "
            "data-suggest='tags' data-suggest-src='/decks/api/tags?q=' data-suggest-delay='250' "
            f"data-suggest-exclude='{_esc(json.dumps(taken, ensure_ascii=False))}' "
            "data-suggest-none='No Archidekt tag by that name' placeholder='Type to find a tag' "
            f"value='{_esc(typed)}' "
            "aria-describedby='tags-help" + (" tags-msg' aria-invalid='true'" if bad else "'") + " required>"
            f"</div><button>{icon('tag')} Add tag</button></form>"
            + note
            + "<p class='muted small' id='tags-help'>Tags come from Archidekt's own tag list: type a few "
            "letters and pick one of its tags.</p></section>"
        )

    def folder_section(deck: Deck, csrf: str | None, folders: dict[str, Any] | None, note: str = "") -> str:
        did = _esc(deck.id)
        if not folders:
            return (
                "<section class='panel folderbox' id='folder'><h2>Folder</h2>"
                + note
                + "<p class='muted'>Your folders could not be read from Archidekt right now.</p></section>"
            )
        current = deck.parent_folder if deck.parent_folder is not None else folders["root_id"]
        # the backup folder is no place to move a deck to; a deck already in it keeps its row
        opts = "".join(
            f"<option value='{f['id']}'{' selected' if f['id'] == current else ''}>"
            f"{_esc(INDENT * f['depth'] + f['name'])}</option>"
            for f in folders["folders"]
            if not f.get("backup") or f["id"] == current
        )
        return (
            f"<section class='panel folderbox' id='folder'><h2>Folder</h2>"
            f"<form method='post' action='/decks/{did}/move#folder' class='moveform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<div class='field grow'><label for='folder_id'>This deck sits in</label>"
            f"<span class='sel'><select id='folder_id' name='folder_id'>{opts}</select></span></div>"
            f"<button>{icon('folder')} Move</button></form>"
            + note
            + "<p class='muted small'>Folders are created and renamed from <a href='/folders'>My decks "
            "› Folders</a>.</p></section>"
        )

    def collaborator_section(
        deck: Deck,
        csrf: str | None,
        rows: list[dict[str, Any]] | None,
        following: list[str] | None = None,
        follow_hint: str = "",
        note: str = "",
    ) -> str:
        """Archidekt's deck collaborators. Adding or removing one changes who can edit the deck, so
        each form asks once more (a confirmation page) before it is sent to Archidekt. These are the
        member's own presses; an assistant's proposal for the same thing waits on its review page.
        The add box is the site's own combobox (static/suggest.js, data-suggest="list") over the
        people the member follows (``following``, the only people Archidekt accepts) less the
        current collaborators: focusing it shows them all, typing narrows them; without scripts it is
        a plain box. ``follow_hint`` is a name just refused for not being followed: the refusal under
        the box offers that person's Archidekt profile, where the Follow button is. ``note`` is this
        section's other notice (a refusal or the ?ok= notice)."""
        did = _esc(deck.id)
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        if rows is None:
            listing = "<p class='muted'>The collaborators could not be read from Archidekt right now.</p>"
        else:
            items = "".join(
                f"<li><span class='who'><span class='name'>{_esc(r['username'])}</span>"
                + (f"<span class='when'>added by {_esc(r['added_by'])}</span>" if r["added_by"] else "")
                + "</span>"
                f"<form method='post' action='/decks/{did}/collaborators#collaborators'>{csrf_in}"
                "<input type='hidden' name='action' value='remove'>"
                f"<input type='hidden' name='username' value='{_esc(r['username'])}'>"
                f"<button aria-label='Remove {_esc(r['username'])}'>{icon('x')} Remove</button>"
                "</form></li>"
                for r in rows
            )
            empty = "<li class='none muted'>No collaborators yet. Only you can edit this deck.</li>"
            listing = f"<ul class='plain plist collablist'>{items or empty}</ul>"
        taken = {str(r["username"]).casefold() for r in rows or []}
        people = sorted((n for n in following or [] if n.casefold() not in taken), key=str.casefold)
        if follow_hint:
            name = _esc(follow_hint)
            note = (
                "<div class='notice error follownote' id='collaborators-msg' role='alert'>"
                f"<p class='lead'>{name} isn't someone you follow on Archidekt.</p>"
                "<p>Follow them on their Archidekt profile, then add them here.</p>"
                f"<a class='btn' href='{_esc(profile_url(follow_hint))}' target='_blank' "
                f"rel='noreferrer noopener'>{icon('external')} Open {name} on Archidekt</a></div>"
            )
        if following is not None and not following:
            hint = (
                "<p class='muted small'>You don't follow anyone on Archidekt yet. To find people, "
                "<a href='/search'>search decks</a>, open a deck, then its owner's name, and press "
                "Follow.</p>"
            )
        elif following and not people:
            hint = "<p class='muted small'>Everyone you follow on Archidekt is a collaborator already.</p>"
        else:
            hint = ""
        described = "collab-help" + (" collaborators-msg" if "id='collaborators-msg'" in note else "")
        picker = (
            "data-suggest='list' "
            f"data-options='{_esc(json.dumps(people, ensure_ascii=False))}' "
            "data-suggest-head='People you follow' "
            "data-suggest-none='Not among the people you follow' "
            "placeholder='Pick someone you follow' "
            if people
            else "placeholder='Archidekt username' "
        )
        return (
            "<section class='panel collabbox' id='collaborators'><h2>Collaborators</h2>"
            + listing
            + f"<form method='post' action='/decks/{did}/collaborators#collaborators' class='addtag'>"
            f"{csrf_in}<input type='hidden' name='action' value='add'>"
            "<div class='field grow'><label for='collabname'>Add someone you follow</label>"
            "<input id='collabname' type='text' name='username' maxlength='60' autocomplete='off' "
            "autocapitalize='none' spellcheck='false' "
            + picker
            + f"value='{_esc(follow_hint)}' aria-describedby='{described}'"
            + (" aria-invalid='true'" if "id='collaborators-msg'" in note else "")
            + " required>"
            + f"</div><button>{icon('plus')} Add</button></form>"
            + note
            + hint
            + "<p class='muted small' id='collab-help'>Only people you follow on Archidekt can be added; "
            "that is Archidekt's own rule. A collaborator can make any change to this deck that you can, "
            "except its main settings. You confirm each change once before it is sent to Archidekt.</p>"
            "</section>"
        )

    async def collaborators_or_none(sub: str, deck_id: str) -> list[dict[str, Any]] | None:
        social = state.social
        if social is None:
            return None
        try:
            return await social.collaborators(sub, deck_id, owned=True)  # the page just read it as own
        except (DeckError, ArchidektError):
            return None

    async def following_or_none(sub: str) -> list[str] | None:
        """The usernames the member follows on Archidekt (cached briefly), None when unreadable."""
        social = state.social
        if social is None:
            return None
        try:
            return list((await social.following_names(sub)).values())
        except (DeckError, ArchidektError):
            return None

    async def folders_or_none(sub: str) -> dict[str, Any] | None:
        try:
            return await decks.folders(sub)
        except DeckError:
            return None

    def settings_problem(
        request: Request, deck_id: str, exc: DeckError, sub: str, sid: str | None
    ) -> Response:
        if busy := busy_response(exc, request, page, sub=sub, sid=sid):
            return busy
        return page(
            "Cannot edit this deck",
            f"<div class='panel'><p>{_esc(exc)}</p>"
            f"<a class='btn' href='/decks/{_esc(deck_id)}'>Back</a></div>",
            sub=sub,
            sid=sid,
            status={"not_found": 404, "forbidden": 403}.get(exc.kind, 400),
        )

    @server.custom_route("/decks/{deck_id}/settings", methods=["GET"], include_in_schema=False)
    async def settings_page(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/settings")
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            return settings_problem(request, deck_id, exc, sub, sid)
        ok = request.query_params.get("ok") or ""
        return page(
            f"Deck settings: {deck.name}",
            settings_form(
                deck,
                _csrf(s, sid),
                folders=await folders_or_none(sub),
                collaborators=await collaborators_or_none(sub, deck.id),
                following=await following_or_none(sub),
                ok=ok,
            ),
            sub=sub,
            sid=sid,
            current="/decks",
            csp=DECK_CSP,
            deck_css=True,
        )

    async def hand_action(request: Request, deck_id: str, run: Any, *, ok: str, anchor: str) -> Response:
        """One of the settings page's own forms (cover, tags, folder, collaborators): the member's
        click is the approval. On success the settings page reopens with a notice; a refusal
        re-renders it with the message next to the form (``anchor``, its section, which the form's
        own address names too, so the browser shows that section) and the typed tag still in its
        box. ``run`` may answer with a page of its own instead (the collaborator form's
        confirmation)."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/settings")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Deck settings", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        try:
            code = await run(sub, data, sid) or ok
        except DeckError as exc:
            try:
                deck = await decks.get_own_deck(sub, deck_id)
            except DeckError as again:
                return settings_problem(request, deck_id, again, sub, sid)
            return page(
                f"Deck settings: {deck.name}",
                settings_form(
                    deck,
                    _csrf(s, sid),
                    error=str(exc),
                    error_in=anchor,
                    folders=await folders_or_none(sub),
                    collaborators=await collaborators_or_none(sub, deck.id),
                    following=await following_or_none(sub),
                    follow_hint=str(exc.extra.get("follow_username") or ""),
                    typed_tag=(data.get("name") or "")[:40] if data.get("action") == "add" else "",
                ),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                csp=DECK_CSP,
                deck_css=True,
            )
        if isinstance(code, Response):
            return code
        return RedirectResponse(f"/decks/{_esc(deck_id)}/settings?ok={code}#{anchor}", status_code=303)

    def confirm_form(
        action_url: str,
        csrf: str | None,
        fields: dict[str, str],
        question: str,
        note: str,
        yes: str,
        back: str,
    ) -> str:
        """The second step of a one-press account action on the member's own pages (0.7.24): the
        question, then a form that sends the same fields again with ``confirmed`` and the page's
        CSRF token, or Cancel back where they came from. Deleting a deck has its own typed-name form."""
        hidden = "".join(
            f"<input type='hidden' name='{_esc(k)}' value='{_esc(v)}'>" for k, v in fields.items()
        )
        return (
            f"<form method='post' action='{_esc(action_url)}' class='panel confirmform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>{hidden}"
            "<input type='hidden' name='confirmed' value='1'>"
            f"<h2>{_esc(question)}</h2>"
            + (f"<p class='muted small'>{_esc(note)}</p>" if note else "")
            + f"<div class='actions'><button class='primary'>{icon('check')} {_esc(yes)}</button>"
            f"<a class='btn' href='{_esc(back)}'>Cancel</a></div></form>"
        )

    @server.custom_route("/decks/{deck_id}/collaborators", methods=["POST"], include_in_schema=False)
    async def collaborators_post(request: Request) -> Response:
        """Add or remove a collaborator of the member's own deck: one press, then a confirmation,
        then it is sent to Archidekt (0.7.24; before, the form made a proposal to apply on its
        review page). The member's own click with their session and CSRF token, like the delete and
        new-folder forms; an assistant's propose_collaborator still needs the review page."""
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str], sid: str | None) -> str | Response:
            kind = {"add": "collaborator_add", "remove": "collaborator_remove"}.get(data.get("action") or "")
            if kind is None:
                raise DeckError("invalid", "Pick add or remove.")
            if data.get("confirmed") == "1":
                await collaborator_by_hand(decks, sub, deck_id, kind, data.get("username") or "")
                return "collaborator_added" if kind == "collaborator_add" else "collaborator_removed"
            deck = await decks.get_own_deck(sub, deck_id)
            _uid, name = await collaborator_target(decks, sub, deck, kind, data.get("username") or "")
            adding = kind == "collaborator_add"
            body = confirm_form(
                f"/decks/{deck.id}/collaborators#collaborators",
                _csrf(s, sid),
                {"action": data.get("action") or "", "username": name},
                f"Let {name} edit “{deck.name}” on Archidekt?"
                if adding
                else f"Stop {name} editing “{deck.name}” on Archidekt?",
                COLLABORATOR_NOTE if adding else "",
                f"Add {name}" if adding else f"Remove {name}",
                f"/decks/{deck.id}/settings#collaborators",
            )
            return page("Confirm collaborator", body, sub=sub, sid=sid, current="/decks", deck_css=True)

        return await hand_action(request, deck_id, run, ok="collaborators", anchor="collaborators")

    @server.custom_route("/decks/{deck_id}/cover", methods=["POST"], include_in_schema=False)
    async def cover_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str], _sid: str | None) -> str:
            uid = (data.get("card") or "").strip() or None
            done = await decks.set_cover(sub, deck_id, uid)
            if uid:
                state.db.save_deck_cover(done["deck_id"], uid.lower(), done["card_name"], owner_sub=sub)
            return "cover" if uid else "cover_auto"

        return await hand_action(request, deck_id, run, ok="cover", anchor="cover")

    @server.custom_route("/decks/{deck_id}/tags", methods=["POST"], include_in_schema=False)
    async def tags_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str], _sid: str | None) -> str:
            if data.get("action") == "remove":
                rid = data.get("relation_id") or ""
                if not re.fullmatch(r"[0-9]{1,12}", rid):
                    raise DeckError("invalid", "That tag could not be identified.")
                await decks.remove_tag(sub, deck_id, int(rid))
                return "tag_removed"
            await decks.add_tag(sub, deck_id, data.get("name") or "")
            return "tag_added"

        return await hand_action(request, deck_id, run, ok="tag_added", anchor="tags")

    @server.custom_route("/decks/api/tags", methods=["GET"], include_in_schema=False)
    async def tag_lookup(request: Request) -> Response:
        """The tag box's suggestions (static/suggest.js, data-suggest-src): Archidekt's tags for the
        typed text, ``{"ok": true, "names": [...]}``, for the signed-in member with a linked
        account; nothing is changed, so like the card-name lookup it is a plain GET (no CSRF). Any
        refusal (signed out, not linked, Archidekt busy) answers with an error status: the box then
        simply shows no list, and the form's own refusal still names close tags."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        try:
            names = await decks.tag_suggestions(sub, request.query_params.get("q", ""))
        except (DeckError, ArchidektError) as exc:
            status = {"not_linked": 409, "auth": 409, "rate_limited": 429}.get(exc.kind, 503)
            return JSONResponse({"ok": False, "error": exc.kind}, status, headers=NO_STORE)
        # Archidekt's own lookup may stop short of every match: never narrowed in the browser
        return JSONResponse({"ok": True, "names": names, "complete": False}, headers=NO_STORE)

    @server.custom_route("/decks/{deck_id}/move", methods=["POST"], include_in_schema=False)
    async def move_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str], _sid: str | None) -> str:
            fid = (data.get("folder_id") or "").strip()
            if not re.fullmatch(r"[0-9]{1,12}", fid):
                raise DeckError("invalid", "Pick a folder.")
            await decks.move_deck(sub, deck_id, int(fid))
            return "moved"

        return await hand_action(request, deck_id, run, ok="moved", anchor="folder")

    # -- delete a deck (hand action, typed-name confirmation) -----------------------------------
    def delete_form(deck: Deck, csrf: str | None, error: str = "") -> str:
        did = _esc(deck.id)
        n = sum(c.quantity for c in deck.cards if deck.in_deck(c))
        undo = (
            "A snapshot of the deck is kept under History"
            + (
                ", and a private copy is made in your backup folder on Archidekt first"
                if state.live.get("archidekt_backups")
                else ""
            )
            + ". Archidekt itself has no undo for a deleted deck."
        )
        return (
            (f"<p class='notice error'>{_esc(error)}</p>" if error else "")
            + f"<form method='post' action='/decks/{did}/delete' class='panel danger deleteform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            f"<h2>Delete “{_esc(deck.name)}”?</h2>"
            f"<p>This deletes the deck ({plural(n, 'card')}) from your Archidekt account. {undo}</p>"
            "<label for='typed'>Type the deck's name to confirm</label>"
            f"<input id='typed' type='text' name='name' autocomplete='off' required maxlength='200' "
            f"placeholder='{_esc(deck.name)}'>"
            f"<div class='actions'><button class='danger'>{icon('trash')} Delete this deck</button>"
            f"<a class='btn' href='/decks/{did}'>Keep it</a></div></form>"
        )

    @server.custom_route("/decks/{deck_id}/delete", methods=["GET"], include_in_schema=False)
    async def delete_page(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/delete")
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            return settings_problem(request, deck_id, exc, sub, sid)
        return page(
            f"Delete {deck.name}",
            delete_form(deck, _csrf(s, sid)),
            sub=sub,
            sid=sid,
            current="/decks",
            deck_css=True,
        )

    @server.custom_route("/decks/{deck_id}/delete", methods=["POST"], include_in_schema=False)
    async def delete_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/delete")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Delete deck", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        try:
            await decks.delete_deck(sub, deck_id, data.get("name") or "")
        except DeckError as exc:
            try:
                deck = await decks.get_own_deck(sub, deck_id)
            except DeckError as again:
                return settings_problem(request, deck_id, again, sub, sid)
            return page(
                f"Delete {deck.name}",
                delete_form(deck, _csrf(s, sid), str(exc)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                deck_css=True,
            )
        return RedirectResponse("/decks?ok=deleted", status_code=303)

    # -- folders (create, rename) ---------------------------------------------------------------
    def folders_form(folders: dict[str, Any] | None, csrf: str | None, error: str = "", ok: str = "") -> str:
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        if not folders:
            return (
                "<div class='panel'><p>Your folders could not be read from Archidekt right now.</p>"
                "<a class='btn' href='/decks'>Back to My decks</a></div>"
            )
        rows = visible_folders(folders)  # never the gateway's backup folder
        tree = "".join(
            f"<li style='--depth:{f['depth']}'><span class='name'>{icon('folder')} {_esc(f['name'])}</span>"
            + ("<span class='badge'>private</span>" if f["private"] else "")
            + (f"<a class='btn' href='/decks?folder={f['id']}'>Show decks</a>" if f["depth"] else "")
            + "</li>"
            for f in rows
        )
        parent_opts = "".join(
            f"<option value='{f['id']}'>{_esc(INDENT * f['depth'] + f['name'])}</option>" for f in rows
        )
        rename_opts = "".join(
            f"<option value='{f['id']}'>{_esc(INDENT * (f['depth'] - 1) + f['name'])}</option>"
            for f in rows
            if f["depth"]
        )
        return (
            (f"<p class='notice error'>{_esc(error)}</p>" if error else "")
            + (f"<p class='notice ok'>{_esc(LIST_OK_MESSAGES[ok])}</p>" if ok in LIST_OK_MESSAGES else "")
            + f"<section class='panel'><h2>Your folders</h2><ul class='plain plist foldertree'>{tree}</ul>"
            "<p class='muted small'>A deck is moved between folders from its settings page.</p></section>"
            f"<form method='post' action='/folders' class='panel folderform'>{csrf_in}"
            "<input type='hidden' name='action' value='create'><h2>New folder</h2>"
            "<div class='row'><div class='field grow'><label for='fname'>Name</label>"
            "<input id='fname' type='text' name='name' maxlength='100' required></div>"
            "<div class='field'><label for='fparent'>Inside</label><span class='sel'>"
            f"<select id='fparent' name='parent_id'>{parent_opts}</select></span></div></div>"
            f"<div class='actions'><button class='primary'>{icon('plus')} Create folder</button></div></form>"
            + (
                f"<form method='post' action='/folders' class='panel folderform'>{csrf_in}"
                "<input type='hidden' name='action' value='rename'><h2>Rename a folder</h2>"
                "<div class='row'><div class='field'><label for='rfolder'>Folder</label><span class='sel'>"
                f"<select id='rfolder' name='folder_id'>{rename_opts}</select></span></div>"
                "<div class='field grow'><label for='rname'>New name</label>"
                "<input id='rname' type='text' name='name' maxlength='100' required></div></div>"
                f"<div class='actions'><button>{icon('edit')} Rename</button></div></form>"
                if rename_opts
                else ""
            )
            + "<p class='muted small'>Folders live on Archidekt; deleting one is done there.</p>"
        )

    @server.custom_route("/folders", methods=["GET"], include_in_schema=False)
    async def folders_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/folders")
        _rows, problem, failure = await my_decks(sub)
        if failure is not None and (busy := busy_response(failure, request, page, sub=sub, sid=sid)):
            return busy
        if problem == "not_linked":
            return page("Folders", link_prompt(), sub=sub, sid=sid, current="/decks")
        ok = request.query_params.get("ok") or ""
        return page(
            "Folders",
            folders_form(await folders_or_none(sub), _csrf(s, sid), ok=ok),
            sub=sub,
            sid=sid,
            current="/decks",
            deck_css=True,
        )

    @server.custom_route("/folders", methods=["POST"], include_in_schema=False)
    async def folders_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/folders")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Folders", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        try:
            if data.get("action") == "rename":
                fid = (data.get("folder_id") or "").strip()
                if not re.fullmatch(r"[0-9]{1,12}", fid):
                    raise DeckError("invalid", "Pick a folder to rename.")
                await decks.rename_folder(sub, int(fid), data.get("name") or "")
                ok = "folder_renamed"
            else:
                pid = (data.get("parent_id") or "").strip()
                parent = int(pid) if re.fullmatch(r"[0-9]{1,12}", pid) else None
                if data.get("confirmed") != "1":  # one press, then this confirmation (0.7.24)
                    name, inside = await decks.check_new_folder(sub, data.get("name") or "", parent)
                    where = "your top level" if not inside["depth"] else f"“{inside['name']}”"
                    body = confirm_form(
                        "/folders",
                        _csrf(s, sid),
                        {"action": "create", "name": name, "parent_id": str(inside["id"])},
                        f"Create the folder “{name}” in {where} on Archidekt?",
                        "Folders live on Archidekt; deleting one is done there.",
                        "Create folder",
                        "/folders",
                    )
                    return page("Confirm new folder", body, sub=sub, sid=sid, current="/decks", deck_css=True)
                await decks.create_folder(sub, data.get("name") or "", parent)
                ok = "folder_created"
        except DeckError as exc:
            return page(
                "Folders",
                folders_form(await folders_or_none(sub), _csrf(s, sid), error=str(exc)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                deck_css=True,
            )
        return RedirectResponse(f"/folders?ok={ok}", status_code=303)

    @server.custom_route("/decks/{deck_id}/settings", methods=["POST"], include_in_schema=False)
    async def settings_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/settings")
        data = await form(request, limit=100_000)
        if not check_csrf(sid, data):
            return page("Deck settings", EXPIRED, sub=sub, sid=sid, status=403)
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            return page(
                "Deck settings", f"<p class='notice error'>{_esc(exc)}</p>", sub=sub, sid=sid, status=400
            )
        details: dict[str, Any] = {
            "name": data.get("name", "").strip(),
            "description": data.get("description", "").replace("\r\n", "\n"),
            "private": bool(data.get("private")),
            "unlisted": bool(data.get("unlisted")),
        }
        if data.get("deck_format"):
            details["deck_format"] = data["deck_format"]
        bracket = data.get("edh_bracket", "")
        details["edh_bracket"] = int(bracket) if re.fullmatch(r"[1-5]", bracket) else None
        # only a Commander deck has a bracket: archidekt.com's own settings form sends none for
        # another format, so a bracket left picked when the format moves off Commander goes
        fmt = details.get("deck_format") or deck.format
        if FORMAT_IDS.get(str(fmt or "")) != FORMAT_IDS["commander"]:
            details["edh_bracket"] = None
        current_client.set(BROWSER_CLIENT_ID)
        try:
            p = await decks.propose_deck_details(sub, deck.id, details)
        except DeckError as exc:
            return page(
                f"Deck settings: {deck.name}",
                settings_form(deck, _csrf(s, sid), data, str(exc)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                deck_css=True,
            )
        return await apply_now(sub, p["proposal_id"], ok="saved", deck_id=deck.id)
