"""Companion pages: a deck's settings (details, cover, tags, folder, collaborators, delete), and the
form behind My decks' folder boxes without scripts (POST /folders)."""

from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import modes, prefs, recheck, ui
from .actions import COLLABORATOR_NOTE, collaborator_by_hand, collaborator_target, profile_url
from .archidekt import (
    FORMAT_IDS,
    GAME_IDS,
    GAME_LABELS,
    ArchidektError,
    Deck,
    featured_scryfall_id,
    format_label,
    rich_text,
)
from .busy import busy_response
from .companion import DECK_CSP, FORMAT_CHOICES, INDENT, SETTINGS_OK_MESSAGES
from .deckpage import card_image, deck_title, delete_box_html
from .decks import (
    CATEGORY_FLAGS,
    CATEGORY_HAS_CARDS,
    MAX_CATEGORY_CHANGES,
    DeckError,
    category_change_rows,
    category_line,
    current_client,
    deck_categories,
    parse_category_changes,
    unused_category_changes,
)
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    browser_session,
    login_redirect,
)
from .theme import esc as _esc
from .theme import icon

NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

# The section of the settings page each ?ok= notice belongs to: the forms there send the member back
# to their own section (#cover, #tags ...), so the notice shows next to the form that was used.
OK_SECTIONS = {
    "categories": "categories",
    "category_created": "categories",
    "category_deleted": "categories",
    "categories_unused": "categories",
    "cover": "cover",
    "cover_auto": "cover",
    "tag_added": "tags",
    "tag_removed": "tags",
    "moved": "folder",
    "collaborator_added": "collaborators",
    "collaborator_removed": "collaborators",
}

SETTINGS_CSS = """
.ds-wrap{display:grid;grid-template-columns:minmax(0,52rem);justify-content:center;gap:24px}
.ds-nav{display:none}
@media (min-width:1100px){.ds-wrap{grid-template-columns:11rem minmax(0,52rem)}
  .ds-nav{display:block;position:sticky;top:12px;align-self:start;margin-top:118px}}
.ds-nav ol{list-style:none;margin:0;padding:0;border-left:2px solid var(--border-soft)}
.ds-nav a{display:flex;align-items:center;min-height:36px;padding:0 .75rem;color:var(--text-muted);
  text-decoration:none;font-weight:700;font-size:.92rem;margin-left:-2px;border-left:2px solid transparent}
.ds-nav a:hover,.ds-nav a:focus-visible{color:var(--text);border-left-color:var(--border)}
.ds-nav a.dz{color:var(--danger-text)}
.ds .back{display:inline-flex;align-items:center;min-height:44px;text-decoration:none;font-weight:700}
.ds .ui-card{margin:0 0 1rem}
.ds .ui-card h2{display:flex;align-items:center;gap:.5rem}
.ds .ui-card.danger{border-color:var(--danger-fill)}
.ds .ui-card.danger h2{color:var(--danger-text)}
.ds .ds-note{margin:0 0 .75rem}
.ds .ui-row .help{margin:.15rem 0 0}
.ds .ui-row label{font-weight:700}
.ds .ui-row.plain .lab{font-weight:400}
.ds .ui-row.plain .ctl{justify-self:end}
.ds .ui-row.wctl{grid-template-columns:minmax(0,1fr) minmax(0,26rem)}
.ds .ui-row.full{grid-template-columns:1fr}
.ds .ui-row.full .ctl{grid-column:1;grid-row:auto}
.ds .ui-row .ctl input,.ds .ui-row .ctl select:not(.msel-native),.ds .ui-row .ctl textarea{width:100%;
  margin:0}
.ds .ds-switch{justify-content:flex-end;margin:0}
.ds .acts{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;border-top:1px solid var(--border-soft);
  padding-top:.75rem;margin-top:.75rem}
.ds .acts .btn,.ds .acts button{margin:0;width:auto}
.ds .foot{margin:.5rem 0 0}
.ds ul.ds-list{list-style:none;margin:.25rem 0 .25rem;padding:0}
.ds ul.ds-list li{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .6rem;min-height:44px;
  border-top:1px solid var(--border-soft);padding:.25rem 0}
.ds ul.ds-list li:first-child{border-top:0}
.ds ul.ds-list li .nm,.ds ul.ds-list li .who{font-weight:700;flex:1 1 auto;min-width:0}
.ds .collablist .who{display:flex;flex-direction:column;gap:.1rem}
.ds .collablist .when{color:var(--text-muted);font-size:.86rem;font-weight:400}
.ds .collablist form{margin:0}
.ds .collablist form button{margin:0;min-height:44px;width:auto}
.ds .ds-flags{display:flex;flex-wrap:wrap;gap:.35rem .75rem}
.ds .ds-flag{display:inline-flex;align-items:center;min-height:28px;border-radius:999px;padding:0 .7rem;
  font-size:.85rem;border:1px solid var(--control-border);background:var(--surface-2);color:var(--text)}
/* editable rows (F7): the name in its box, three switches that say their word, one under the
   other on a phone; a row without a record stays read-only pills with its reason */
.ds .ds-cats li{align-items:flex-start;padding:.35rem 0}
.ds .ds-cats .nm{flex:1 1 14rem;min-width:0}
.ds .ds-cats .nm input{width:100%;min-height:44px;font-weight:700}
.ds .ds-cats .ds-sw,.ds form.catnew .ds-sw{display:flex;flex-wrap:wrap;gap:0 1rem;flex:0 1 auto}
.ds .ds-cats .switch.ds-flag,.ds form.catnew .switch.ds-flag{min-height:44px;margin:0;padding:0;border:0;
  border-radius:0;background:none;font-size:.95rem}
.ds .ds-cats .del{flex:0 0 auto;margin:0;min-height:44px;min-width:44px;width:auto;padding:0 .6rem}
.ds .ds-cats .cnt{flex:0 0 auto;color:var(--text-muted);font-size:.9rem;min-height:44px;
  display:inline-flex;align-items:center}
.ds form.catnew{margin-top:1rem;border-top:1px solid var(--border-soft);padding-top:.75rem}
.ds form.catnew h3{margin:0 0 .5rem;font-size:1rem}
.ds form.catnew .row{display:flex;flex-wrap:wrap;gap:.4rem .9rem;align-items:center}
.ds form.catnew .row input[type=text]{flex:1 1 14rem;min-height:44px;margin:0}
.ds form.catnew .row .btn{flex:0 0 auto;width:auto;margin:0}
.ds .confirmform label.mv{display:block;font-weight:700;margin:.75rem 0 .25rem}
.ds .ds-cats + .acts{border-top:0;padding-top:.5rem}
@media (max-width:599.98px){.ds .ds-cats .nm{flex-basis:100%}.ds .ds-cats .ds-sw{width:100%;gap:0 .75rem}
  .ds form.catnew .ds-sw{width:100%;gap:0 .75rem}}
.ds form.catnew .row .btn{max-width:100%;white-space:normal}
.ds .ds-cover{display:grid;grid-template-columns:9rem minmax(0,1fr);gap:1rem;align-items:center}
.ds .ds-cover .pv{width:100%;max-width:none;aspect-ratio:4/3;border-radius:var(--radius-panel);
  object-fit:cover;background:var(--surface-2);display:grid;place-items:center;text-align:center;
  padding:0;margin:0}
.ds .ds-cover .pv.none{padding:.5rem}
.ds .ds-cover .field{margin:0}
.ds form.addtag{display:block}
.ds .ds-inline{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
/* the box (or the themed select's button, or the suggest wrapper) takes the room, the button its
   own; at large text the button wraps under the box instead of leaving the window */
.ds .ds-inline > *{flex:1 1 12rem;min-width:0}
.ds .ds-inline > .btn,.ds .ds-inline > button{flex:0 0 auto;width:auto;margin:0;white-space:nowrap}
.ds .ds-inline .msel-btn,.ds .ui-row .ctl .msel-btn{max-width:100%}
.ds ul.taglist{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 .75rem;padding:0}
.ds .follownote{display:flex;flex-direction:column;align-items:flex-start;gap:.5rem;margin:.75rem 0 0}
.ds .follownote p{margin:0;font-weight:400}
.ds .follownote p.lead{font-weight:700}
.ds .follownote .btn{color:var(--text)}
@media (pointer:coarse){.ds .taglist button.mini{width:44px;height:44px}}
@media (max-width:599.98px){.ds .ui-row.wctl{grid-template-columns:1fr}
  .ds .ui-row.plain .ctl{justify-self:start}
  .ds .ds-cover{grid-template-columns:1fr}.ds .ds-cover .pv{max-width:12rem}
  .ds .ds-inline{flex-wrap:wrap}.ds .ds-inline > *{flex:1 1 100%}
  .ds .ds-inline > .btn,.ds .ds-inline > button{flex:1 1 100%;width:100%}
  .ds .acts .btn,.ds .acts button{flex:1 1 auto}}
"""


NOTHING_TO_SAVE = "Nothing to save: no name or switch was changed."
COUNTS_TITLE = modes.CATEGORY_CONFIRM_TITLES[modes.CATEGORY_COUNTS_CONFIRM]
DELETE_TITLE = modes.CATEGORY_CONFIRM_TITLES[modes.CATEGORY_DELETE_CONFIRM]


MAIN_FIELDS = (
    "name",
    "description",
    "deck_format",
    "edh_bracket",
    "private",
    "unlisted",
    "theorycrafted",
    "game",
)


def main_values(deck: Deck) -> dict[str, str]:
    """The Main settings form's fields as the page draws them for ``deck``: the baseline its hidden
    was_ fields carry, so a save sends only what the member changed and a page opened before a
    change made elsewhere never puts the old values back (security review 0.7.42). The description
    is carried as its digest, so the page does not hold it twice."""
    return {
        "name": deck.name,
        "description": _digest(rich_text(deck.description)),
        "deck_format": deck.format or "",
        "edh_bracket": str(deck.edh_bracket or ""),
        "private": "1" if deck.private else "",
        "unlisted": "1" if deck.unlisted else "",
        "theorycrafted": "1" if deck.theorycrafted else "",
        "game": next((k for k, i in GAME_IDS.items() if i == deck.game), "any"),
    }


def _digest(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest()[:32]


def posted_main_values(data: dict[str, str]) -> dict[str, str]:
    """``main_values`` for a posted Main settings form (an unchecked box sends no key)."""
    return {
        "name": data.get("name", "").strip(),
        "description": _digest(data.get("description", "")),
        "deck_format": data.get("deck_format", ""),
        "edh_bracket": data.get("edh_bracket", ""),
        "private": "1" if data.get("private") else "",
        "unlisted": "1" if data.get("unlisted") else "",
        "theorycrafted": "1" if data.get("theorycrafted") else "",
        "game": data.get("game") or "any",
    }


def category_keys(deck: Deck) -> list[tuple[str, dict[str, Any]]]:
    """Each category of the panel with the key its fields carry: its record id, or ``d1``, ``d2``...
    in the panel's order for one Archidekt sent without a record (its default Sideboard or
    Maybeboard until someone creates one; the change gives it its record)."""
    out, n = [], 0
    for c in deck_categories(deck):
        if c["record"]:
            out.append((str(int(c["id"])), c))
        else:
            n += 1
            out.append((f"d{n}", c))
    return out


def category_baseline_fields(c: dict[str, Any], key: str | None = None) -> str:
    """The hidden fields that carry what an editable category (``deck_categories``) was when the
    page was shown: the Save compares the deck with them before anything is proposed (M-1)."""
    cid = key or str(int(c["id"]))
    out = f"<input type='hidden' name='was_name_{cid}' value='{_esc(c['name'])}'>"
    for key, _label, _field, _inv in CATEGORY_FLAGS:
        out += f"<input type='hidden' name='was_{key}_{cid}' value='{'1' if c[key] else '0'}'>"
    return out


def stale_categories_error() -> DeckError:
    """The refusal of a Save whose page no longer matches the deck: the needs-a-look words, and
    ``fresh`` so the panel shows the deck as it is now instead of the stale fields."""
    return DeckError(
        "stale",
        f"Not saved: {recheck.reason_text(recheck.REASON_CHANGED)} Nothing was sent to Archidekt. "
        "The panel now shows the deck as it is: make your changes again, then save.",
        fresh=True,
    )


def category_changes_from_form(deck: Deck, data: dict[str, str]) -> list[dict[str, Any]]:
    """What the Categories panel's fields change, as ``decks.propose_categories`` takes them: one
    entry per editable category whose name or any switch differs from what the page showed (its
    hidden ``was_`` fields; a blank name is left as it is). A checkbox left off sends no field.
    The deck must still be what the page showed: a category changed since (elsewhere, by an
    assistant or on Archidekt), or a post without the page's baseline, is refused before
    anything is proposed, so a stale page never puts back a change made since (M-1)."""
    changes: list[dict[str, Any]] = []
    keys = category_keys(deck)
    for cid, c in keys:
        if f"name_{cid}" not in data:
            continue
        was_name = data.get(f"was_name_{cid}")
        was = {key: data.get(f"was_{key}_{cid}") for key, _l, _f, _i in CATEGORY_FLAGS}
        if was_name != c["name"] or any(was[k] != ("1" if c[k] else "0") for k in was):
            raise stale_categories_error()
        entry: dict[str, Any] = {"id": int(cid)} if c["record"] else {"category": c["name"]}
        new = " ".join((data.get(f"name_{cid}") or "").split())
        if new and new != was_name:
            entry["rename_to"] = new
        for key in was:
            on = data.get(f"{key}_{cid}") == "1"
            if on != (was[key] == "1"):
                entry[key] = on
        if len(entry) > 1:
            changes.append(entry)
    posted = {m.group(1) for k in data if (m := re.fullmatch(r"was_name_(d?\d+)", k))}
    if posted - {cid for cid, _c in keys}:
        raise stale_categories_error()  # a category the page showed is gone
    return changes


def add_settings_routes(server: MCPServer, state: AppState, h: SimpleNamespace) -> None:
    """Deck settings, hand actions, delete and folders."""
    EXPIRED = h.EXPIRED
    apply_now = h.apply_now
    check_csrf = h.check_csrf
    decks = h.decks
    form = h.form
    page = h.page
    s = h.s

    # -- deck settings (details proposal) ---------------------------------------
    def row(lab: str, help_html: str, ctl: str, cls: str = "") -> str:
        """One shared form row: label (and help) on the left, the control on the right."""
        help_part = f"<p class='help'>{help_html}</p>" if help_html else ""
        return (
            f"<div class='ui-row {cls}'><p class='lab'>{lab}</p>{help_part}<div class='ctl'>{ctl}</div></div>"
        )

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
        cat_values: dict[str, str] | None = None,
    ) -> str:
        """The settings page on the shared form rows (F7). ``error`` shows in the main panel (the main
        settings form's own refusal) or, with ``error_in``, inside that section next to its form, as
        the ?ok= notices of the section forms do (OK_SECTIONS): each panel carries its own strip."""
        v = values or {}

        def note(section: str) -> str:
            if error and error_in == section:
                return ui.notice(error, "error", role="alert", cls="ds-note", id=f"{section}-msg")
            if not error and ok in SETTINGS_OK_MESSAGES and OK_SECTIONS.get(ok) == section:
                return ui.notice(SETTINGS_OK_MESSAGES[ok], "ok", role="status", cls="ds-note")
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
        theorycrafted = v.get("theorycrafted", "") if values else ("1" if deck.theorycrafted else "")
        game_current = v.get("game") or next((k for k, i in GAME_IDS.items() if i == deck.game), "any")
        game_opts = "".join(
            f"<option value='{k}'{sel(game_current == k)}>{_esc(label)}</option>"
            for k, label in (("any", "Any"), *GAME_LABELS.items())
        )
        did = _esc(deck.id)
        title = deck_title(deck)
        # a form drawn again after a refusal keeps the baseline it was opened with
        baseline = {k: v.get(f"was_{k}", now) for k, now in main_values(deck).items()}

        def switch(name: str, on: str) -> str:
            return (
                f"<label class='switch ds-switch'><input id='{name}' type='checkbox' name='{name}' value='1'"
                + (" checked" if on else "")
                + "><span class='track'></span><span class='sr-only'>"
                + name.capitalize()
                + "</span></label>"
            )

        main_note = (
            ui.notice(error, "error", role="alert", cls="ds-note")
            if error and not error_in
            else (
                ui.notice(SETTINGS_OK_MESSAGES[ok], "ok", role="status", cls="ds-note")
                if ok in SETTINGS_OK_MESSAGES and ok not in OK_SECTIONS
                else ""
            )
        )
        sections = [
            ("main", "Main settings"),
            ("categories", "Categories"),
            ("cover", "Cover image"),
            ("tags", "Deck tags"),
            ("folder", "Folder"),
            ("collaborators", "Collaborators"),
            ("delete", "Delete this deck"),
        ]
        nav = (
            "<nav class='ds-nav' aria-label='Sections'><ol>"
            + "".join(
                f"<li><a href='#{'ds-' + k if k in ('main', 'delete') else k}'"
                f"{' class=dz' if k == 'delete' else ''}>{_esc(t)}</a></li>"
                for k, t in sections
            )
            + "</ol></nav>"
        )
        return (
            "<div class='ds-wrap'>"
            + nav
            + f"<div class='ds'><a class='back' href='/decks/{did}'>← {_esc(title)}</a>"
            + ui.page_head("Deck settings", f"“{title}”")
            + f"<form method='post' action='/decks/{did}/settings' class='ui-card settings' id='ds-main'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            + "".join(
                f"<input type='hidden' name='was_{k}' value='{_esc(was)}'>" for k, was in baseline.items()
            )
            + "<h2>Main settings</h2>"
            + main_note
            + row(
                "<label for='name'>Deck name</label>",
                "",
                f"<input id='name' type='text' name='name' value='{_esc(v.get('name', deck.name))}' "
                "required maxlength='200'>",
            )
            + row(
                "<label for='deck_format'>Format</label>",
                "",
                f"<select id='deck_format' name='deck_format'>{fmt_opts}</select>",
            )
            + row(
                "<label for='edh_bracket'>Commander bracket</label>",
                "Commander only. Changing the format clears it. It does not influence the estimated "
                "bracket on the deck page.",
                f"<select id='edh_bracket' name='edh_bracket'>{bracket_opts}</select>",
            )
            + "<div class='ui-row full'><p class='lab'><label for='description'>Description</label></p>"
            f"<div class='ctl'><textarea id='description' name='description' rows='8'>{_esc(description)}"
            "</textarea></div></div>"
            + row(
                "<label for='private'>Private</label>",
                "The deck is only visible to you.",
                switch("private", private),
            )
            + row(
                "<label for='unlisted'>Unlisted</label>",
                "Viewable with a direct link, but not shown in lists.",
                switch("unlisted", unlisted),
            )
            + row(
                "<label for='theorycrafted'>Theorycrafted</label>",
                "Not included by default in the In decks view on Archidekt.",
                # the marker tells a form from before this release (no switch) from one switched off
                "<input type='hidden' name='theorycrafted_shown' value='1'>"
                + switch("theorycrafted", theorycrafted),
            )
            + row(
                "<label for='game'>Game type</label>",
                "Where the deck is played: paper, MTGO or Arena.",
                f"<select id='game' name='game'>{game_opts}</select>",
            )
            + f"<div class='acts'><button class='btn btn-primary'>{icon('check')} Save changes</button>"
            f"<a class='btn' href='/decks/{did}'>Cancel</a></div>"
            "<p class='muted small foot'>Saved to Archidekt when you press Save changes; a snapshot from "
            "just before is kept under History.</p></form>"
            + categories_section(deck, csrf, note("categories"), cat_values)
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
            # (ds-delete: the Delete box the page holds is #delete)
            + "<section class='ui-card danger' id='ds-delete'><h2>Delete this deck</h2>"
            + row(
                f"{icon('history')} A snapshot is kept under History, and a copy in your backup folder on "
                "Archidekt when backups are on.",
                "",
                f"<a class='btn btn-outline-danger' href='/decks/{did}/delete' data-box-open='delete'>"
                f"{icon('trash')} Delete deck…</a>",
                "plain",
            )
            + "</section></div></div>"
            + delete_box_html(deck, csrf, backups=bool(state.live.get("archidekt_backups")))
        )

    def categories_section(
        deck: Deck, csrf: str | None, note: str = "", values: dict[str, str] | None = None
    ) -> str:
        """The deck's categories, as archidekt.com's deck settings has them (F7, Ben 21 Oct: option
        a; create and delete, Ben 22 Oct): each name in its box and three switches, Premier, Not in
        deck and Not in price; a plain Save categories (the main settings' Save changes stays the
        page's one orange button) and Undo changes, which puts the boxes back before saving. Each
        category with a record has a Delete button (an empty one is deleted; one with cards asks
        where its cards go first, as Archidekt only deletes an empty category), and under the list
        "New category" and, when there are any, "Delete unused categories". Anything that touches
        Premier, the Commander or a commander's category, or which cards count, asks first, and so
        does every delete (a box with scripts, static/menus.js; a confirmation page without), with
        the backend's own sentence. Archidekt's default Sideboard or Maybeboard without a record is
        edited like any other: the save gives it its record. ``values``: the fields of a refused
        save, kept in the boxes to fix."""
        did = _esc(deck.id)
        items = ""
        keys = category_keys(deck)
        names = [c["name"] for _k, c in keys]
        for cid, c in keys:
            name = _esc(c["name"])
            typed = values.get(f"name_{cid}") if values else None
            shown = _esc(typed) if typed is not None else name

            def on(key: str, c: dict[str, Any] = c, cid: str = cid) -> bool:
                return values.get(f"{key}_{cid}") == "1" if values else bool(c[key])

            switches = "".join(
                f"<label class='switch ds-flag'><input type='checkbox' name='{key}_{cid}' value='1'"
                + (" checked" if on(key) else "")
                + f"><span class='track'></span><span class='t'>{_esc(label)}</span></label>"
                for key, label, _f, _i in CATEGORY_FLAGS
            )
            commander = " data-commander='1'" if c["commander"] else ""
            n = int(c["cards"])
            count = f"<span class='cnt'>{n} card{'s' if n != 1 else ''}</span>"
            delete = ""
            if c["record"]:
                # the box adds the commander or counts sentence the gateway's own page adds
                also = (
                    modes.CATEGORY_COMMANDER_CONFIRM
                    if c["commander"] or c["premier"] or c["name"].casefold() == "commander"
                    else modes.CATEGORY_COUNTS_CONFIRM
                    if c["name"].casefold() in ("sideboard", "maybeboard")
                    else ""
                )
                delete = (
                    f"<button class='btn btn-outline-danger del' form='catdel' name='delete' value='{cid}' "
                    f"data-name='{name}' data-cards='{n}' data-also='{_esc(also)}' "
                    f"aria-label='Delete “{name}”' "
                    f"title='Delete “{name}”'>{icon('trash')}</button>"
                )
            items += (
                f"<li data-cat='{cid}' data-name='{name}'{commander}>"
                f"<span class='nm'><label class='sr-only' for='cat-{cid}-name'>Name of “{name}”</label>"
                f"<input id='cat-{cid}-name' type='text' name='name_{cid}' value='{shown}' maxlength='60' "
                "required>" + category_baseline_fields(c, cid) + "</span>"
                f"<span class='ds-sw' role='group' aria-label='Switches of “{name}”'>{switches}</span>"
                f"{count}{delete}</li>"
            )
        unused = unused_category_changes(deck)
        more = len(unused) > MAX_CATEGORY_CHANGES  # one press deletes the first 30, the rest next time
        unused = unused[:MAX_CATEGORY_CHANGES]
        new_switches = "".join(
            f"<label class='switch ds-flag'><input type='checkbox' name='{key}' value='1'>"
            f"<span class='track'></span><span class='t'>{_esc(label)}</span></label>"
            for key, label, _f, _i in CATEGORY_FLAGS
        )
        unused_names = ", ".join(f"“{u['category']}”" for u in unused)
        try:
            unused_wait = modes.category_wait(category_change_rows(deck, unused)[1]) if unused else None
        except DeckError:  # a damaged deck answer: the press is checked again, and refused there
            unused_wait = None
        unused_also = {
            modes.CATEGORY_COMMANDER_REASON: modes.CATEGORY_COMMANDER_CONFIRM,
            modes.CATEGORY_COUNTS_REASON: modes.CATEGORY_COUNTS_CONFIRM,
        }.get(unused_wait or "", "")
        return (
            "<section class='ui-card' id='categories' data-confirm-title='Change the commander category?' "
            f"data-confirm='{_esc(modes.CATEGORY_COMMANDER_CONFIRM)}' "
            f"data-counts-confirm-title='{_esc(COUNTS_TITLE)}' "
            f"data-counts-confirm='{_esc(modes.CATEGORY_COUNTS_CONFIRM)}' "
            f"data-delete-confirm-title='{_esc(DELETE_TITLE)}' "
            f"data-delete-confirm='{_esc(modes.CATEGORY_DELETE_CONFIRM)}' "
            f"data-names='{_esc(json.dumps(names))}'>"
            "<h2>Categories</h2>"
            + note
            + f"<form method='post' action='/decks/{did}/categories#categories' id='catform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            f"<ul class='plain ds-list ds-cats'>{items or '<li class=muted>No categories yet.</li>'}</ul>"
            + (
                f"<div class='acts'><button class='btn' name='action' value='save'>{icon('check')} "
                "Save categories</button><button type='reset' class='btn'>Undo changes</button>"
                + (
                    "<button class='btn btn-outline-danger' form='catunused' name='action' value='unused' "
                    f"data-names='{_esc(unused_names)}' data-also='{_esc(unused_also)}'>{icon('trash')} "
                    "Delete unused categories "
                    + (f"({len(unused)} at a time)" if more else f"({len(unused)})")
                    + "</button>"
                    if unused
                    else ""
                )
                + "</div>"
                if items
                else ""
            )
            + "</form>"
            # the row Delete buttons and Delete unused belong to these forms (``form`` attribute),
            # so a delete never sends the panel's typed names along
            + f"<form method='post' action='/decks/{did}/categories#categories' id='catdel'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<input type='hidden' name='action' value='delete'></form>"
            + f"<form method='post' action='/decks/{did}/categories#categories' id='catunused'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'></form>"
            + f"<form method='post' action='/decks/{did}/categories#categories' class='catnew' id='catnew'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<input type='hidden' name='action' value='create'>"
            "<h3><label for='newcat'>New category</label></h3><div class='row'>"
            "<input id='newcat' type='text' name='category' maxlength='60' required "
            "placeholder='Name, e.g. Removal'>"
            "<span class='ds-sw' role='group' aria-label='Switches of the new category'>"
            f"{new_switches}</span>"
            f"<button class='btn'>{icon('plus')} Create category</button></div></form>"
            + "<p class='muted small foot'>Moving single cards between categories is in the deck editor "
            "and on the deck page.</p></section>"
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
            "<section class='ui-card coverbox' id='cover'><h2>Cover image</h2>"
            + note
            + f"<form method='post' action='/decks/{did}/cover#cover' class='ds-cover'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            + (
                f"<img class='pv coverart' src='{_esc(preview)}' alt='Current cover: {_esc(current.name)}'>"
                if preview and current
                else "<div class='pv coverart none'><span class='muted small'>Archidekt picks the image"
                "</span></div>"
            )
            + "<div class='field'><label for='cover_card'>Card whose art fronts the deck</label>"
            "<div class='ds-inline'><select id='cover_card' name='card'>"
            f"<option value=''{'' if chosen else ' selected'}>Automatic (Archidekt picks)</option>"
            + "".join(options)
            + f"</select><button class='btn'>{icon('image')} Set cover</button></div></div></form>"
            "<p class='muted small foot'>Shown on the deck page, in your deck lists and on Archidekt.</p>"
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
            "<section class='ui-card tagbox' id='tags'><h2>Deck tags</h2>"
            + note
            + f"<ul class='plain taglist'>{items or '<li class=muted>No deck tags yet.</li>'}</ul>"
            f"<form method='post' action='/decks/{did}/tags#tags' class='addtag'>{csrf_in}"
            "<input type='hidden' name='action' value='add'>"
            + row(
                "<label for='tagname'>Add a tag</label>",
                "<span id='tags-help'>Tags come from Archidekt's own tag list: type a few letters and pick "
                "one of its tags.</span>",
                "<div class='ds-inline'>"
                "<input id='tagname' type='text' name='name' maxlength='40' autocomplete='off' "
                "data-suggest='tags' data-suggest-src='/decks/api/tags?q=' data-suggest-delay='250' "
                f"data-suggest-exclude='{_esc(json.dumps(taken, ensure_ascii=False))}' "
                "data-suggest-none='No Archidekt tag by that name' placeholder='Type to find a tag' "
                f"value='{_esc(typed)}' "
                "aria-describedby='tags-help"
                + (" tags-msg' aria-invalid='true'" if bad else "'")
                + f" required><button class='btn'>{icon('tag')} Add tag</button></div>",
                "wctl",
            )
            + "</form></section>"
        )

    def folder_section(deck: Deck, csrf: str | None, folders: dict[str, Any] | None, note: str = "") -> str:
        did = _esc(deck.id)
        if not folders:
            return (
                "<section class='ui-card folderbox' id='folder'><h2>Folder</h2>"
                + note
                + ui.notice(
                    "Your folders could not be read from Archidekt right now.", "error", cls="ds-note"
                )
                + "</section>"
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
            "<section class='ui-card folderbox' id='folder'><h2>Folder</h2>"
            + note
            + f"<form method='post' action='/decks/{did}/move#folder'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            + row(
                "<label for='folder_id'>This deck sits in</label>",
                "Manage folders on <a href='/decks'>My decks</a>.",
                f"<div class='ds-inline'><select id='folder_id' name='folder_id'>{opts}</select>"
                f"<button class='btn'>{icon('folder')} Move</button></div>",
                "wctl",
            )
            + "</form></section>"
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
        each form asks once more (a box with scripts, static/menus.js; a confirmation page without)
        before it is sent to Archidekt. These are the member's own presses; an assistant's proposal
        for the same thing waits on its review page. The add box is the site's own combobox
        (static/suggest.js, data-suggest="list") over the people the member follows
        (``following``, the only people Archidekt accepts) less the current collaborators: focusing
        it shows them all, typing narrows them; without scripts it is a plain box. ``follow_hint``
        is a name just refused for not being followed: the refusal under the row offers that
        person's Archidekt profile, where the Follow button is. ``note`` is this section's other
        notice (a refusal or the ?ok= notice)."""
        did = _esc(deck.id)
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        if rows is None:
            listing = ui.notice(
                "The collaborators could not be read from Archidekt right now.", "error", cls="ds-note"
            )
        else:
            items = "".join(
                f"<li><span class='who'><span class='name'>{_esc(r['username'])}</span>"
                + (f"<span class='when'>added by {_esc(r['added_by'])}</span>" if r["added_by"] else "")
                + "</span>"
                f"<form method='post' action='/decks/{did}/collaborators#collaborators'>{csrf_in}"
                "<input type='hidden' name='action' value='remove'>"
                f"<input type='hidden' name='username' value='{_esc(r['username'])}'>"
                f"<button class='btn btn-outline-danger' aria-label='Remove “{_esc(r['username'])}”'>"
                f"{icon('trash')} Remove</button>"
                "</form></li>"
                for r in rows
            )
            empty = "<li class='none muted'>No collaborators yet. Only you can edit this deck.</li>"
            listing = f"<ul class='plain ds-list collablist'>{items or empty}</ul>"
        taken = {str(r["username"]).casefold() for r in rows or []}
        people = sorted((n for n in following or [] if n.casefold() not in taken), key=str.casefold)
        if follow_hint:
            name = _esc(follow_hint)
            note = (
                "<div class='notice error follownote ds-note' id='collaborators-msg' role='alert'>"
                f"<p class='lead'>{name} isn't someone you follow on Archidekt.</p>"
                "<p>Follow them on their Archidekt profile, then add them here.</p>"
                f"<a class='btn' href='{_esc(profile_url(follow_hint))}' target='_blank' "
                f"rel='noreferrer noopener'>{icon('external')} Open {name} on Archidekt</a></div>"
            )
        if following is not None and not following:
            hint = (
                "You don't follow anyone on Archidekt yet. To find people, "
                "<a href='/search'>search decks</a>, open a deck, then its owner's name, and press Follow."
            )
        elif following and not people:
            hint = "Everyone you follow on Archidekt is a collaborator already."
        else:
            hint = (
                "Only people you follow on Archidekt. They can change anything in this deck except its "
                "main settings."
            )
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
        # the deck's name and the note for the box the forms ask in first (static/menus.js); the
        # confirmation page does the same without scripts
        return (
            "<section class='ui-card collabbox' id='collaborators' "
            f"data-deck-name='{_esc(deck_title(deck))}' "
            f"data-note='{_esc(COLLABORATOR_NOTE)}'><h2>Collaborators</h2>"
            + listing
            + f"<form method='post' action='/decks/{did}/collaborators#collaborators' class='addtag'>"
            f"{csrf_in}<input type='hidden' name='action' value='add'>"
            + row(
                "<label for='collabname'>Add someone you follow</label>",
                f"<span id='collab-help'>{hint}</span>",
                "<div class='ds-inline'>"
                "<input id='collabname' type='text' name='username' maxlength='60' autocomplete='off' "
                "autocapitalize='none' spellcheck='false' "
                + picker
                + f"value='{_esc(follow_hint)}' aria-describedby='{described}'"
                + (" aria-invalid='true'" if "id='collaborators-msg'" in note else "")
                + f" required><button class='btn'>{icon('plus')} Add</button></div>",
                "wctl",
            )
            + "</form>"
            + note
            + "</section>"
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
        return settings_response(
            settings_form(
                deck,
                _csrf(s, sid),
                folders=await folders_or_none(sub),
                collaborators=await collaborators_or_none(sub, deck.id),
                following=await following_or_none(sub),
                ok=ok,
            ),
            sub,
            sid,
        )

    def settings_response(body: str, sub: str, sid: str | None, status: int = 200) -> Response:
        return page(
            "Deck settings",
            body,
            sub=sub,
            sid=sid,
            status=status,
            current="/decks",
            csp=DECK_CSP,
            deck_css=True,
            heading=False,
            extra_css=SETTINGS_CSS,
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
            return settings_response(
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
                    cat_values=data
                    if anchor == "categories"
                    and (data.get("action") or "save") == "save"
                    and not exc.extra.get("fresh")
                    else None,
                ),
                sub,
                sid,
                400,
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
        *,
        danger: bool = False,
        extra: str = "",
        answer: str = "confirmed",
    ) -> str:
        """The second step of a one-press account action on the member's own pages (0.7.24): the
        question, then a form that sends the same fields again with ``confirmed`` and the page's
        CSRF token, or Cancel back where they came from. Deleting a deck has its own typed-name form.
        ``extra``: more of the form, already escaped (a category delete's "move its cards to");
        ``answer``: the field the yes sends (``move_ok`` for a pick that may need asking again)."""
        hidden = "".join(
            f"<input type='hidden' name='{_esc(k)}' value='{_esc(v)}'>" for k, v in fields.items()
        )
        return (
            f"<form method='post' action='{_esc(action_url)}' class='panel confirmform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>{hidden}"
            f"<input type='hidden' name='{_esc(answer)}' value='1'>"
            f"<h2>{_esc(question)}</h2>"
            + (f"<p class='muted small'>{_esc(note)}</p>" if note else "")
            + extra
            + "<div class='actions'>"
            + (
                f"<button class='btn btn-outline-danger'>{icon('trash')} {_esc(yes)}</button>"
                if danger
                else f"<button class='btn btn-primary'>{icon('check')} {_esc(yes)}</button>"
            )
            + f"<a class='btn' href='{_esc(back)}'>Cancel</a></div></form>"
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
                f"Let “{name}” edit “{deck_title(deck)}” on Archidekt?"
                if adding
                else f"Stop “{name}” editing “{deck_title(deck)}” on Archidekt?",
                COLLABORATOR_NOTE if adding else "",
                f"Add “{name}”" if adding else f"Remove “{name}”",
                f"/decks/{deck.id}/settings#collaborators",
                danger=not adding,
            )
            return page("Confirm collaborator", body, sub=sub, sid=sid, current="/decks", deck_css=True)

        return await hand_action(request, deck_id, run, ok="collaborators", anchor="collaborators")

    @server.custom_route("/decks/{deck_id}/categories", methods=["POST"], include_in_schema=False)
    async def categories_post(request: Request) -> Response:
        """Save categories: the member's own press on their deck's settings page. The posted names
        and switches are compared with the deck as it is now; what differs is checked first
        (decks.category_change_rows, which refuses before anything is proposed), asks once more
        when it touches the commander or which cards count (modes.hand_edit_confirm; the box with
        scripts sends ``confirmed`` itself), then is proposed and applied at once with its snapshot."""
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str], sid: str | None) -> str | Response:
            deck = await decks.get_own_deck(sub, deck_id)
            action = data.get("action") or "save"
            unused = action == "unused"
            move_field = ""
            ok, yes = "categories", "Save categories"
            if action == "create":
                changes: list[dict[str, Any]] = [
                    {
                        "action": "create",
                        "category": " ".join((data.get("category") or "").split()),
                        **{key: True for key, _l, _f, _i in CATEGORY_FLAGS if data.get(key) == "1"},
                    }
                ]
                ok, yes = "category_created", "Create category"
            elif action == "delete":
                key = data.get("delete") or ""
                if not re.fullmatch(r"[0-9]{1,12}", key):
                    raise DeckError("invalid", "That category could not be identified.")
                cat = next((c for c in deck_categories(deck) if c["id"] == int(key)), None)
                if cat is None:
                    raise stale_categories_error()
                move = " ".join((data.get("move_cards_to") or "").split())
                changes = [{"action": "delete", "id": int(key), **({"move_cards_to": move} if move else {})}]
                ok, yes = "category_deleted", f"Delete “{cat['name']}”"
                if cat["cards"] and not move:
                    # Archidekt only deletes an empty category: the member picks where its cards go
                    n = int(cat["cards"])
                    targets = "".join(
                        f"<option value='{_esc(c['name'])}'>{_esc(c['name'])}</option>"
                        for c in deck_categories(deck)
                        if c["name"] != cat["name"]
                    )
                    has_cards = CATEGORY_HAS_CARDS.format(
                        name=cat["name"], n=n, s="s" if n != 1 else "", them="them" if n != 1 else "it"
                    )
                    move_field = (
                        f"<p>{_esc(has_cards)}</p>"
                        "<label class='mv' for='move_cards_to'>Move its cards to</label>"
                        f"<select id='move_cards_to' name='move_cards_to' required>{targets}</select>"
                    )
            elif unused:
                changes = []
                ok, yes = "categories_unused", "Delete unused categories"
            else:
                changes = category_changes_from_form(deck, data)
                if not changes:
                    raise DeckError("invalid", NOTHING_TO_SAVE)
            if move_field:
                rows: list[dict[str, Any]] = [{"kind": "deck_category", "delete": True}]
                why: str | None = modes.CATEGORY_DELETE_CONFIRM
            else:
                wanted = parse_category_changes(changes) if changes else []
                if unused:
                    wanted += unused_category_changes(deck, MAX_CATEGORY_CHANGES)  # the rest next time
                    if not wanted:
                        raise DeckError("invalid", "The deck has no unused categories; nothing would change.")
                _plan, rows = category_change_rows(deck, wanted)
                why = modes.hand_edit_confirm("categories", rows)
            # picking where a deleted category's cards go (on its page or in the box, ``move_ok``) is
            # a yes to the delete only: a pick that makes them commanders or changes which cards
            # count is asked once more, with that sentence (S-01, review 0.7.42)
            said_yes = data.get("confirmed") == "1" or (
                data.get("move_ok") == "1" and why == modes.CATEGORY_DELETE_CONFIRM
            )
            if why and not said_yes:
                drop = ("csrf", "confirmed", "move_ok") + (("move_cards_to",) if move_field else ())
                fields = {k: v for k, v in data.items() if k not in drop}
                if action != "save":
                    fields["action"] = action
                note, title = why, modes.CATEGORY_CONFIRM_TITLES[why]
                if action in ("delete", "unused"):
                    # a delete is asked as a delete, with the commander or counts sentence after
                    title = modes.CATEGORY_CONFIRM_TITLES[modes.CATEGORY_DELETE_CONFIRM]
                    note = modes.CATEGORY_DELETE_CONFIRM + (
                        "" if why == modes.CATEGORY_DELETE_CONFIRM else " " + why
                    )
                    if not move_field:
                        note += " " + " ".join(category_line(r).split(" Archidekt tabs")[0] for r in rows)
                body = confirm_form(
                    f"/decks/{deck.id}/categories#categories",
                    _csrf(s, sid),
                    fields,
                    title,
                    note,
                    yes,
                    f"/decks/{deck.id}/settings#categories",
                    danger=action in ("delete", "unused"),
                    extra=move_field,
                    answer="move_ok" if move_field else "confirmed",
                )
                return page("Confirm categories", body, sub=sub, sid=sid, current="/decks", deck_css=True)
            p = await decks.propose_categories(sub, deck.id, changes, delete_unused=unused)
            done = await apply_now(sub, p["proposal_id"], ok="categories", deck_id=deck.id)
            if done.headers.get("location") == f"/decks/{deck.id}?ok=categories":
                return RedirectResponse(f"/decks/{deck.id}/settings?ok={ok}#categories", status_code=303)
            return done  # Archidekt refused or writes are off: the review page says why

        return await hand_action(request, deck_id, run, ok="categories", anchor="categories")

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
        if not prefs.from_this_site(request):  # it asks Archidekt on the member's own allowance
            return JSONResponse({"ok": False, "error": "cross_site"}, 403, headers=NO_STORE)
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

    # -- delete a deck (hand action, typed-name box on the deck page) --------------------------
    @server.custom_route("/decks/{deck_id}/delete", methods=["GET"], include_in_schema=False)
    async def delete_page(request: Request) -> Response:
        """The old Delete page's address: the deck page with its Delete box open (M4)."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/delete")
        return await h.deck_page_response(request, sub, sid, deck_id, box="delete")

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
            # the deck page again, the box open with the refusal (a wrong name, Archidekt busy ...)
            return await h.deck_page_response(
                request,
                sub,
                sid,
                deck_id,
                box="delete",
                box_error=str(exc),
                status={"not_found": 404, "forbidden": 403}.get(exc.kind, 400),
            )
        return RedirectResponse("/decks?ok=deleted", status_code=303)

    # -- folders: the old Folders page's address, and its form for a browser without scripts ----
    @server.custom_route("/folders", methods=["GET"], include_in_schema=False)
    async def folders_page(request: Request) -> Response:
        """The Folders page is gone: folders are made, renamed, moved and deleted from My decks'
        boxes (static/menus.js on companion_folders.py's routes). Its old address goes there."""
        return RedirectResponse("/decks", status_code=303)

    def folders_problem(message: str) -> str:
        return (
            f"<p class='notice error'>{_esc(message)}</p>"
            "<p><a class='btn' href='/decks'>Back to My decks</a></p>"
        )

    @server.custom_route("/folders", methods=["POST"], include_in_schema=False)
    async def folders_post(request: Request) -> Response:
        """A new folder (one press, then a confirmation page) or a rename, as a plain form: the
        same checks as the boxes' JSON routes, and My decks with the notice when it is done. The
        boxes themselves use companion_folders.py; this stays for a browser without scripts."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
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
                        "",
                        "Create folder",
                        "/decks",
                    )
                    return page("Confirm new folder", body, sub=sub, sid=sid, current="/decks", deck_css=True)
                await decks.create_folder(sub, data.get("name") or "", parent)
                ok = "folder_created"
        except DeckError as exc:
            return page("Folders", folders_problem(str(exc)), sub=sub, sid=sid, status=400, current="/decks")
        return RedirectResponse(f"/decks?ok={ok}", status_code=303)

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
        if data.get("theorycrafted_shown"):
            details["theorycrafted"] = bool(data.get("theorycrafted"))
        if data.get("game"):
            details["game"] = data["game"]
        if data.get("deck_format"):
            details["deck_format"] = data["deck_format"]
        bracket = data.get("edh_bracket", "")
        details["edh_bracket"] = int(bracket) if re.fullmatch(r"[1-5]", bracket) else None
        if "was_name" in data:
            # only what the member changed on the page is sent: a field left as the page drew it
            # keeps the deck's current value, even when that changed elsewhere since
            posted = posted_main_values(data)
            for k in MAIN_FIELDS:
                if k in details and f"was_{k}" in data and posted[k] == data[f"was_{k}"]:
                    del details[k]
        # only a Commander deck has a bracket: archidekt.com's own settings form sends none for
        # another format, so a bracket left picked when the format moves off Commander goes
        fmt = details.get("deck_format") or deck.format
        if FORMAT_IDS.get(str(fmt or "")) != FORMAT_IDS["commander"] and (
            "edh_bracket" in details or deck.edh_bracket is not None
        ):
            details["edh_bracket"] = None
        if not details:
            return settings_response(
                settings_form(deck, _csrf(s, sid), data, "Nothing to save: nothing on the form was changed."),
                sub,
                sid,
                400,
            )
        current_client.set(BROWSER_CLIENT_ID)
        try:
            p = await decks.propose_deck_details(sub, deck.id, details)
        except DeckError as exc:
            return settings_response(settings_form(deck, _csrf(s, sid), data, str(exc)), sub, sid, 400)
        return await apply_now(sub, p["proposal_id"], ok="saved", deck_id=deck.id)
