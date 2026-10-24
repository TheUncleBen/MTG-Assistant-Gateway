"""The deck page's release-2 boxes (parity release 2, mockup screens/21-deck-page.js): Request
deck help, Report deck and Report card, the Registration PDF, Add a package, Lock personal
categories and the contest entry, each the member's own press on the page.

Every box is drawn hidden on the deck page (ui.box) and opened from the deck menu or the card menu
(static/deckextras.js); its form posts to a route here. An account action (a help request, a
report, a contest entry) is proposed exactly as an assistant would propose it (decks.propose_action)
and the member applies it on its review page, as every account action is applied; a package is
proposed as a deck change (decks.propose_package) and reviewed the same way. The registration PDF
is a read: Archidekt answers with the file's address, and the typed details are never stored.
Locking a card's categories is a hand change to the member's default categories
(account_lists.hand_change), applied at once as the My lists page applies its own. Without scripts
an old-style address (/decks/{id}/help, /report-to-archidekt, /registration, /package, /contest, /lock) draws
the deck page with that box open."""

from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import account_lists, actions, packages, registration, ui
from .archidekt import FORMAT_NAMES, Deck, DeckCard, format_label
from .busy import busy_response
from .deckpage import deck_title, folder_options_html
from .decks import DeckError, current_client, split_label
from .pages import BROWSER_CLIENT_ID, browser_session, login_redirect, read_limited
from .theme import esc, icon

EXTRA_BOXES = (
    "help",
    "delist",
    "report",
    "reportcard",
    "reportcomment",
    "pdf",
    "package",
    "lock",
    "contest",
    "withdraw",
)
# the member's press is on the review page: what the box's main button leads to
PUBLIC_LINE = (
    "{what} is public on Archidekt under your name. Your assistant can draft it, but it always waits for "
    "your press."
)
REPORT_LINE = "Your assistant can draft a report, but it always waits for your press."
HIST_LINE = "{what} is kept under History, and the result is read back to check."
HELP_INTRO = (
    "Posts the deck on Archidekt's deck help board for seven days; you can reopen it after. Needs 10 to "
    "200 words in each box, at least one deck tag, and a deck of 80 to 120 cards (Commander formats) or "
    "50 to 85 (others)."
)
PDF_INTRO = (
    "Archidekt fills the tournament deck registration sheet from this deck and your details below. "
    "Nothing here is stored by the gateway."
)
PDF_MAIN = "Main deck: every card whose first category counts in the deck. Sideboard: the Sideboard category."
PDF_SORTS = (("alpha", "Alphabet"), ("manaValue", "Mana value"), ("color", "Color"), ("type", "Type"))
PACKAGE_CATEGORIES = (
    ("package", "Keep the package's categories"),
    ("default", "Automatic (by card type)"),
    ("maybeboard", "Everything into Maybeboard"),
)
# the Add a package box's chips: whose packages the list shows (packages.WHOSE)
PACKAGE_WHOSE = (("mine", "Mine"), ("followed", "Followed"), ("all", "All packages"))
PACKAGE_NOTE = (
    "The cards go in as one change you review first, after a snapshot. Archidekt counts it as one "
    "import of the package."
)
PACKAGE_HIGH = "High when it adds more than 10 cards."
PACKAGE_ROWS = "More rows than your low-risk limit also wait for your press."
LOCK_NOTE = "Only names that are category templates are kept; others are left out."
CONTEST_WARN = (
    "The review shows the old description and the new one side by side; a snapshot of the deck is kept first."
)
WITHDRAW_TEXT = (
    "Your entry leaves the contest. You can enter again after withdrawing. The deck's description "
    "stays as it is now."
)
DELIST_TEXT = "It stays saved, so you can list it again later with the same words."
REPORT_PLACEHOLDER = "Say what the moderators should look at"
# the deck page's notices after a press here (companion.DECK_OK_MESSAGES carries them too)
OK_MESSAGES = {
    "locked": "Default categories for “{name}” saved on Archidekt and read back.",
}
_WORDS = re.compile(r"\S+")


def _words(text: str) -> int:
    return len(_WORDS.findall(text or ""))


def _pub(what: str) -> str:
    return f"<p class='muted small'>{icon('hand')} {esc(PUBLIC_LINE.format(what=what))}</p>"


def _hist(what: str) -> str:
    return f"<p class='muted small'>{icon('history')} {esc(HIST_LINE.format(what=what))}</p>"


def _err(error: str) -> str:
    return ui.notice(error, "error", role="alert", cls="boxnote") if error else ""


def _when(iso: str) -> str:
    """ "Oct 20" from an ISO time, as the page's other dates read (ui.when_text)."""
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return ""
    return ui.when_text(ts)


def _when_after(iso: str, days: int) -> str:
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() + days * 86400
    except ValueError:
        return ""
    return ui.when_text(ts)


def field(
    fid: str,
    name: str,
    label: str,
    value: str = "",
    *,
    placeholder: str = "",
    limit: int | None = None,
    required: bool = False,
    attrs: str = "",
) -> str:
    extra = (f" maxlength='{limit}'" if limit else "") + (" required" if required else "")
    ph = f" placeholder='{esc(placeholder)}'" if placeholder else ""
    return (
        f"<div class='field'><label for='{esc(fid)}'>{esc(label)}</label>"
        f"<input id='{esc(fid)}' type='text' name='{esc(name)}' value='{esc(value)}' autocomplete='off'"
        f"{extra}{ph}{(' ' + attrs) if attrs else ''}></div>"
    )


def words_field(fid: str, name: str, label: str, value: str) -> str:
    n = _words(value)
    bad = n and (n < 10 or n > 200)
    return (
        f"<div class='field'><label for='{esc(fid)}'>{esc(label)}</label>"
        f"<textarea id='{esc(fid)}' name='{esc(name)}' rows='4' data-words='{esc(fid)}-c'>{esc(value)}"
        "</textarea>"
        f"<div class='dx-under'><span></span><span class='small {'dx-bad' if bad else 'muted'}' "
        f"id='{esc(fid)}-c' data-words-count aria-live='polite'>{n} words (10 to 200)</span></div></div>"
    )


def switch(name: str, label: str, on: bool) -> str:
    return (
        f"<label class='switch dx-switch'><input type='checkbox' name='{esc(name)}' value='1'"
        f"{' checked' if on else ''}><span class='track' aria-hidden='true'></span>{esc(label)}</label>"
    )


def check_labels(deck: Deck) -> str:
    """The help form's two checks (deck tags, size) as labels: green when Archidekt's form would
    take the deck, red when not."""
    tags = ", ".join(deck.tags)
    size = sum(c.quantity for c in deck.main_cards)
    commander = (deck.format or "") in actions.HELP_COMMANDER_FORMATS
    low, high = actions.HELP_SIZE_COMMANDER if commander else actions.HELP_SIZE_OTHER
    size_ok = low <= size <= high
    return (
        "<div class='dx-why'>"
        + ui.label(
            f"Deck tags: {tags}" if tags else "No deck tags",
            "green" if tags else "red",
            "check" if tags else "alert",
        )
        + ui.label(
            f"Size: {size} cards" + ("" if size_ok else f" (needs {low} to {high})"),
            "green" if size_ok else "red",
            "check" if size_ok else "alert",
        )
        + "</div>"
    )


# -- the boxes -------------------------------------------------------------------------------------


def help_box_html(
    deck: Deck,
    csrf: str,
    help_req: dict[str, Any] | None,
    *,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
) -> str:
    did = esc(deck.id)
    v = values or {}
    listed = help_req is not None and help_req["active"]
    goals = v.get("goals", help_req["goals"] if help_req else "")
    issues = v.get("issues", help_req["issues"] if help_req else "")
    since = _when(help_req["created_at"]) if help_req else ""
    off = _when_after(help_req["created_at"], 7) if help_req else ""
    listed_line = (
        ui.notice(f"Listed since {esc(since)} · comes off the board {esc(off)}", "ok", role="status")
        if listed and since
        else ""
    )
    body = (
        f"<form id='dx-help-f' method='post' action='/decks/{did}/help' class='stack' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='help_action' value='{'edit' if listed else 'post'}'>"
        f"<p class='muted small'>{esc(HELP_INTRO)}</p>"
        + listed_line
        + words_field("dx-hg", "goals", "What I want the deck to do", goals)
        + words_field("dx-hi", "issues", "What isn't working", issues)
        + check_labels(deck)
        + _pub("A help request")
        + "</form>"
        + _err(error)
    )
    acts = [ui.box_button("Cancel", cancel=True)]
    if listed:
        acts = [
            f"<a class='btn btn-outline-danger dx-delist' href='/decks/{did}/delist' "
            f"{ui.box_open_attr('delist')}>"
            f"{icon('x')} Delist</a>",
            "<span class='dx-gap'></span>",
            ui.box_button("Close", cancel=True),
            ui.box_button("Save changes", kind="primary", form="dx-help-f", icon_name="check"),
        ]
    else:
        acts.append(ui.box_button("Post the request", kind="primary", form="dx-help-f", icon_name="send"))
    return ui.box(
        "help",
        "Your help request" if listed else "Request deck help",
        body,
        actions=acts,
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box dx-wide",
    )


def delist_box_html(deck: Deck, csrf: str, *, open: bool = False, error: str = "") -> str:
    did = esc(deck.id)
    body = (
        f"<form id='dx-delist-f' method='post' action='/decks/{did}/help'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<input type='hidden' name='help_action' value='delist'></form>"
        f"<p>{esc(DELIST_TEXT)}</p>" + _pub("A help request") + _err(error)
    )
    return ui.box(
        "delist",
        "Take the request off the board?",
        body,
        actions=[
            ui.box_button("Keep it listed", cancel=True),
            ui.box_button("Delist", kind="danger-solid", form="dx-delist-f", icon_name="x"),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box",
    )


def help_strip_html(deck: Deck, help_req: dict[str, Any] | None) -> str:
    """The strip above the banner while the deck is listed for help."""
    if help_req is None or not help_req["active"]:
        return ""
    did = esc(deck.id)
    since, off = _when(help_req["created_at"]), _when_after(help_req["created_at"], 7)
    when = (
        f"<span class='muted small'>Since {esc(since)} · comes off the board {esc(off)}</span>"
        if since
        else ""
    )
    return (
        "<div class='dx-strip' id='dx-help-strip'><span class='t'>"
        f"<b>{icon('hand')} Listed for deck help on Archidekt</b>{when}</span>"
        f"<a class='btn' href='/decks/{did}/help' {ui.box_open_attr('help')}>{icon('edit')} Edit or delist"
        "</a>"
        "</div>"
    )


def report_box_html(
    deck: Deck,
    csrf: str,
    *,
    card: bool = False,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
) -> str:
    did = esc(deck.id)
    v = values or {}
    box_id = "reportcard" if card else "report"
    name = v.get("card", "") if card else deck_title(deck)
    fid = "dx-rc" if card else "dx-rm"
    body = (
        f"<form id='{box_id}-f' method='post' action='/decks/{did}/report-to-archidekt' class='stack' "
        "novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='report_kind' value='{'card' if card else 'deck'}'>"
        + (f"<input type='hidden' name='card' value='{esc(name)}'>" if card else "")
        + f"<div class='field'><label for='{fid}'>What is wrong with it</label>"
        f"<textarea id='{fid}' name='message' rows='4' maxlength='{actions.REPORT_MESSAGE_MAX}' "
        f"placeholder='{esc(REPORT_PLACEHOLDER)}' required>{esc(v.get('message', ''))}</textarea></div>"
        + (
            "<p class='muted small'>A custom card is reported as a custom card, with its own link.</p>"
            if card
            else ""
        )
        + f"<p class='muted small'>{icon('hand')} {esc(REPORT_LINE)}</p>"
        + "</form>"
        + _err(error)
    )
    return ui.box(
        box_id,
        f"Report “{name}” to Archidekt's moderators",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Send report", kind="primary", form=f"{box_id}-f", icon_name="send"),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box",
    ).replace(
        "<div class='ui-box", "<div data-t='Report “{name}” to Archidekt&#39;s moderators' class='ui-box", 1
    )


def report_comment_box_html(
    csrf: str,
    *,
    action: str,
    close_href: str,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
) -> str:
    """Report comment… (the mockup's BOXES.r2ReportComment): on a deck's comment or a forum post.
    Drawn with no comment; the script fills whose comment it is, its id and the thread page it was
    shown on (static/social.js, the post's ⋮). ``action``: the deck page's report route or the
    forum thread's, both of which propose the report and send the member to its review page."""
    v = values or {}
    who = v.get("who", "")
    body = (
        f"<form id='reportcomment-f' method='post' action='{esc(action)}' class='stack' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<input type='hidden' name='report_kind' value='comment'>"
        f"<input type='hidden' name='comment_id' value='{esc(v.get('comment_id', ''))}'>"
        f"<input type='hidden' name='page' value='{esc(v.get('page', '1'))}'>"
        f"<input type='hidden' name='who' value='{esc(who)}'>"
        "<div class='field'><label for='dx-rcm'>What is wrong with it</label>"
        f"<textarea id='dx-rcm' name='message' rows='4' maxlength='{actions.REPORT_MESSAGE_MAX}' "
        f"placeholder='{esc(REPORT_PLACEHOLDER)}' required>{esc(v.get('message', ''))}</textarea></div>"
        f"<p class='muted small'>{icon('hand')} {esc(REPORT_LINE)}</p>"
        "</form>" + _err(error)
    )
    return ui.box(
        "reportcomment",
        f"Report {who}'s comment to Archidekt's moderators"
        if who
        else "Report this comment to Archidekt's moderators",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Send report", kind="primary", form="reportcomment-f", icon_name="send"),
        ],
        open=open,
        close_href=close_href,
        cls="dx-box",
    ).replace(
        "<div class='ui-box",
        "<div data-t='Report {name}&#39;s comment to Archidekt&#39;s moderators' class='ui-box",
        1,
    )


def pdf_box_html(
    deck: Deck,
    csrf: str,
    *,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
    pdf_url: str = "",
) -> str:
    did = esc(deck.id)
    v = values or {}
    today = datetime.now().strftime("%m/%d/%Y")
    sort = v.get("sort", "alpha")
    opts = "".join(
        f"<option value='{k}'{' selected' if sort == k else ''}>{label}</option>" for k, label in PDF_SORTS
    )
    done = (
        "<div class='notice ok dx-pdf-done' role='status'>Archidekt made the PDF. "
        f"<a href='{esc(pdf_url)}' target='_blank' rel='noreferrer noopener'>{icon('external')} Open the PDF"
        "</a></div>"
        if pdf_url
        else "<div class='notice ok dx-pdf-done' role='status' hidden></div>"
    )
    body = (
        f"<form id='dx-pdf-f' method='post' action='/decks/{did}/registration' class='stack' data-fetches>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<p class='muted small'>{esc(PDF_INTRO)}</p>"
        "<div class='dx-two'>"
        + field("dx-pf", "first_name", "First name", v.get("first_name", ""), limit=registration.TEXT_MAX)
        + field("dx-pl", "last_name", "Last name", v.get("last_name", ""), limit=registration.TEXT_MAX)
        + "</div><div class='dx-two'>"
        + field(
            "dx-pd",
            "dci_number",
            "DCI number",
            v.get("dci_number", ""),
            placeholder="Digits only",
            limit=registration.DCI_MAX,
            attrs="inputmode='numeric'",
        )
        + field("dx-pe", "event", "Event", v.get("event", ""), limit=registration.TEXT_MAX)
        + "</div><div class='dx-two'>"
        + field("dx-pc", "location", "Location", v.get("location", ""), limit=registration.TEXT_MAX)
        + field("dx-pt", "date", "Date", v.get("date", today), limit=registration.DATE_MAX)
        + "</div><div class='dx-two'>"
        + field(
            "dx-pn",
            "deck_name",
            "Deck name",
            v.get("deck_name", deck_title(deck)[: registration.TEXT_MAX]),
            limit=registration.TEXT_MAX,
        )
        + field(
            "dx-pg",
            "designer",
            "Deck designer",
            v.get("designer", (deck.owner or "")[: registration.TEXT_MAX]),
            limit=registration.TEXT_MAX,
        )
        + "</div>"
        f"<div class='field'><label for='dx-ps'>Sort the list by</label><span class='sel'>"
        f"<select id='dx-ps' name='sort'>{opts}</select></span></div>"
        f"<p class='muted small'>{esc(PDF_MAIN)} {ui.label('No change on Archidekt', 'green', 'shield')}</p>"
        "</form>" + done + _err(error)
    )
    return ui.box(
        "pdf",
        "Deck registration sheet (PDF)",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Make the PDF", kind="primary", form="dx-pdf-f", icon_name="download"),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box dx-wide",
    )


def color_tag_options(
    deck: Deck, color_tags: list[dict[str, Any]] | None = None, *, chosen: str = ""
) -> tuple[str, str]:
    """The Color tag select's options and the chosen tag's colour: None, the tags this deck's cards
    already carry (no extra read), then the member's own color tags (``color_tags``, read only when
    the box opens), each name once, each value "Name,#rrggbb". A chosen tag the list does not
    hold (one the script added, or typed) stays as an option, so a refusal keeps the choice."""
    seen: dict[str, str] = {}
    for card in deck.cards:
        name, colour = split_label(card.label)
        if name and name.casefold() not in seen:
            seen[name.casefold()] = f"{name},{colour}" if colour else name
    for t in color_tags or []:
        name, colour = split_label(f"{t.get('name') or ''},{t.get('color') or ''}")
        if name and name.casefold() not in seen:
            seen[name.casefold()] = f"{name},{colour}" if colour else name
    want_name, want_colour = split_label(chosen)
    want = (f"{want_name},{want_colour}" if want_colour else want_name) if want_name else ""
    if want and want_name.casefold() not in seen:
        seen[want_name.casefold()] = want
    out = [f"<option value=''{' selected' if not want else ''}>None</option>"]
    for value in seen.values():
        name = value.rsplit(",", 1)[0]
        out.append(f"<option value='{esc(value)}'{' selected' if value == want else ''}>{esc(name)}</option>")
    return "".join(out), (want_colour if want else "")


def package_box_html(
    deck: Deck,
    csrf: str,
    *,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
    color_tags: list[dict[str, Any]] | None = None,
) -> str:
    did = esc(deck.id)
    v = values or {}
    cat = v.get("categories", "package")
    opts = "".join(
        f"<option value='{k}'{' selected' if cat == k else ''}>{label}</option>"
        for k, label in PACKAGE_CATEGORIES
    )
    tag_opts, tag_colour = color_tag_options(deck, color_tags, chosen=v.get("color_tag", ""))
    swatch = (
        f"<span class='tagdot dx-tagdot' style='background:{tag_colour}' aria-hidden='true'></span>"
        if tag_colour
        else "<span class='tagdot dx-tagdot' aria-hidden='true' hidden></span>"
    )
    # a box drawn with only a package named in its address (Add to a deck… on My decks) is as
    # fresh as an empty one: the switches keep their defaults
    fresh = not any(k in v for k in ("categories", "skip_existing", "skip_outside_identity", "singleton"))
    chips = "".join(
        f"<button type='button' class='ui-chip' aria-pressed='{'true' if key == 'mine' else 'false'}' "
        f"data-whose='{key}'>{label}</button>"
        for key, label in PACKAGE_WHOSE
    )
    fmt_opts = "<option value=''>Any format</option>" + "".join(
        f"<option value='{esc(n)}'>{esc(format_label(n))}</option>" for n in PACKAGE_FORMATS
    )
    budget_opts = "<option value=''>Any budget</option>" + "".join(
        f"<option value='{val}'>{label}</option>" for val, label in BUDGET_CHOICES if val
    )
    body = (
        f"<form id='dx-pkg-f' method='post' action='/decks/{did}/package' class='stack' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<div class='dx-mine' data-src='/decks/api/packages'>"
        f"<div class='ui-chips' role='group' aria-label='Which packages'>{chips}</div>"
        "<div class='dx-pkf'>"
        "<div class='field'><label for='dx-pkq'>Package name</label>"
        "<input id='dx-pkq' type='search' placeholder='Package name' autocomplete='off' maxlength='100'>"
        "</div>"
        "<div class='field'><label for='dx-pkfmt'>Intended format</label><span class='sel'>"
        f"<select id='dx-pkfmt'>{fmt_opts}</select></span></div>"
        "<div class='field'><label for='dx-pkbud'>Budget</label><span class='sel'>"
        f"<select id='dx-pkbud'>{budget_opts}</select></span></div></div>"
        "<span class='lbl' id='dx-pk-lbl'>Your packages</span>"
        "<div class='tree' role='radiogroup' aria-labelledby='dx-pk-lbl'>"
        "<p class='muted small dx-mine-note'>Your packages are listed here when the box opens.</p>"
        "</div>"
        "<div class='dx-more' hidden><button type='button' class='btn'>Load more</button></div>"
        "</div>"
        + field(
            "dx-pk",
            "package",
            "Package link or id",
            v.get("package", ""),
            placeholder="https://archidekt.com/decks/…",
            required=True,
        )
        + "<div class='dx-two'>"
        f"<div class='field'><label for='dx-pcat'>Categories</label><span class='sel'>"
        f"<select id='dx-pcat' name='categories'>{opts}</select></span></div>"
        f"<div class='field'><label for='dx-ptag'>Color tag</label><span class='dx-tagsel'>{swatch}"
        "<span class='sel'><select id='dx-ptag' name='color_tag' data-src='/decks/api/color_tags'>"
        f"{tag_opts}</select></span></span></div></div>"
        "<div class='dx-switches'>"
        + switch("skip_existing", "Skip cards already in the deck", fresh or v.get("skip_existing") == "1")
        + switch(
            "skip_outside_identity",
            "Skip cards outside the commander's colors",
            fresh or v.get("skip_outside_identity") == "1",
        )
        + switch("singleton", "One copy of each (singleton)", fresh or v.get("singleton") == "1")
        + "</div>"
        f"<p class='muted small'>{icon('history')} {esc(PACKAGE_NOTE)} "
        f"{ui.label(PACKAGE_HIGH, 'orange', 'triangle')} {esc(PACKAGE_ROWS)}</p>"
        "</form>" + _err(error)
    )
    return ui.box(
        "package",
        f"Add a package to “{deck_title(deck)}”",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Review the cards", kind="primary", form="dx-pkg-f", icon_name="plus"),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box dx-wide",
    )


def lock_box_html(
    deck: Deck,
    csrf: str,
    *,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
    templates: list[str] | None = None,
) -> str:
    """Lock personal categories. Drawn with no card; the script fills the card's name and its
    categories (data-t and data-text carry the sentence's shape), checking them against the
    member's templates (/decks/api/templates). Without scripts the /lock?card= address draws it
    with the card and the templates read."""
    did = esc(deck.id)
    v = values or {}
    name = v.get("card", "")
    cats = [c for c in (v.get("categories") or "").split("\n") if c] if v else []
    kept = [c for c in cats if templates is None or c in templates] if cats else []
    left = [c for c in cats if c not in kept]
    if name and templates is not None and not kept:
        sentence = (
            f"None of this card's categories ({esc(', '.join(cats)) or 'none'}) is one of your category "
            "templates, so there is nothing to lock. Make a template first on "
            "<a href='/lists/templates'>My lists</a>."
        )
    else:
        sentence = (
            f"From now on, when you add “<span data-lock-card>{esc(name)}</span>” to any deck, it goes into "
            f"<b data-cats>{esc(', '.join(kept))}</b> (its categories in this deck). Saved to My lists › "
            "Default categories."
        )
    left_line = (
        f"<p class='muted small' data-left>Left out, not a template: {esc(', '.join(left))}.</p>"
        if left
        else "<p class='muted small' data-left hidden></p>"
    )
    body = (
        f"<form id='dx-lock-f' method='post' action='/decks/{did}/lock' data-fetches>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='card' value='{esc(name)}'></form>"
        f"<p data-sentence>{sentence}</p>"
        + left_line
        + f"<p class='muted small'>{esc(LOCK_NOTE)}</p>"
        + _hist("A copy of your default categories from just before")
        + _err(error)
    )
    return ui.box(
        "lock",
        f"Lock personal categories for “{name}”",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Lock", kind="primary", form="dx-lock-f", icon_name="lock", disabled=bool(name and not kept)
            ),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box",
    ).replace("<div class='ui-box", "<div data-t='Lock personal categories for “{name}”' class='ui-box", 1)


def contest_box_html(
    deck: Deck,
    csrf: str,
    *,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
) -> str:
    did = esc(deck.id)
    v = values or {}
    summary = v.get("summary", "")
    body = (
        f"<form id='dx-contest-f' method='post' action='/decks/{did}/contest' class='stack' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<input type='hidden' name='entry_action' value='enter'>"
        + field(
            "dx-ec",
            "contest",
            "Contest link or id",
            v.get("contest", ""),
            placeholder="https://archidekt.com/contest/discussion/…",
            required=True,
        )
        + "<div class='field'><label for='dx-es'>Summary of the deck (required)</label>"
        f"<textarea id='dx-es' name='summary' rows='4' maxlength='{actions.CONTEST_SUMMARY_MAX}' "
        f"data-count='dx-es-c' required>{esc(summary)}</textarea>"
        "<div class='dx-under'><span class='muted small'>Shown on the contest page.</span>"
        f"<span class='muted small' id='dx-es-c' data-counter>{len(summary)} / "
        f"{actions.CONTEST_SUMMARY_MAX}</span></div></div>"
        + ui.notice(
            f"<b>Archidekt replaces the deck's description with this summary.</b> {esc(CONTEST_WARN)}",
            "warn",
            html=True,
        )
        + _pub("A contest entry")
        + "</form>"
        + _err(error)
    )
    return ui.box(
        "contest",
        f"Enter “{deck_title(deck)}” in a contest",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Review the entry", kind="primary", form="dx-contest-f", icon_name="send"),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box dx-wide",
    )


def withdraw_box_html(
    deck: Deck,
    csrf: str,
    *,
    open: bool = False,
    error: str = "",
    values: dict[str, str] | None = None,
) -> str:
    did = esc(deck.id)
    v = values or {}
    body = (
        f"<form id='dx-withdraw-f' method='post' action='/decks/{did}/contest' class='stack' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        "<input type='hidden' name='entry_action' value='withdraw'>"
        + field(
            "dx-wc",
            "contest",
            "Contest link or id",
            v.get("contest", ""),
            placeholder="https://archidekt.com/contest/discussion/…",
            required=True,
        )
        + f"<p>{esc(WITHDRAW_TEXT)}</p>"
        + _pub("Withdrawing an entry")
        + "</form>"
        + _err(error)
    )
    return ui.box(
        "withdraw",
        f"Withdraw “{deck_title(deck)}” from a contest?",
        body,
        actions=[
            ui.box_button("Keep my entry", cancel=True),
            ui.box_button("Withdraw", kind="danger-solid", form="dx-withdraw-f", icon_name="x"),
        ],
        open=open,
        close_href=f"/decks/{did}",
        cls="dx-box",
    )


def boxes_html(
    deck: Deck,
    *,
    own: bool,
    csrf: str | None,
    open_box: str = "",
    box_error: str = "",
    box_values: dict[str, str] | None = None,
    templates: list[str] | None = None,
    color_tags: list[dict[str, Any]] | None = None,
) -> str:
    """Every release-2 box the deck page holds, hidden; ``open_box`` names the one drawn open.
    ``templates`` and ``color_tags`` are the member's lists, read only for the Lock box and the
    Add a package box drawn open from their own addresses (the scripts read them as a box opens)."""
    if not csrf:
        return ""
    help_req = actions.deck_help_of(deck)

    def st(name: str) -> dict[str, Any]:
        on = open_box == name
        return {"open": on, "error": box_error if on else "", "values": box_values if on else None}

    out = report_box_html(deck, csrf, **st("report"))
    out += report_box_html(deck, csrf, card=True, **st("reportcard"))
    out += report_comment_box_html(
        csrf,
        action=f"/decks/{esc(deck.id)}/report-to-archidekt",
        close_href=f"/decks/{esc(deck.id)}",
        **st("reportcomment"),
    )
    pdf = st("pdf")
    out += pdf_box_html(
        deck, csrf, pdf_url=(box_values or {}).get("pdf_url", "") if pdf["open"] else "", **pdf
    )
    if own:
        out += help_box_html(deck, csrf, help_req, **st("help"))
        out += delist_box_html(
            deck, csrf, open=open_box == "delist", error=box_error if open_box == "delist" else ""
        )
        out += package_box_html(deck, csrf, color_tags=color_tags, **st("package"))
        out += lock_box_html(deck, csrf, templates=templates, **st("lock"))
        out += contest_box_html(deck, csrf, **st("contest"))
        out += withdraw_box_html(deck, csrf, **st("withdraw"))
    return out


NEW_PACKAGE_INTRO = (
    "A package is a short list of cards others can add to a deck in one go. It is made in your Archidekt "
    "account like a deck; add its cards afterwards, as for a new deck."
)
NEW_PACKAGE_NAME = "Give the package a name."
NEW_PACKAGE_KEYWORDS = "Give it at least one keyword."
BUDGET_CHOICES = (("", "Not set"), ("1", "$"), ("2", "$$"), ("3", "$$$"), ("4", "$$$$"))
PACKAGE_FORMATS = sorted({n for n in FORMAT_NAMES.values()}, key=lambda n: format_label(n).lower())


async def read_form(request: Request, lists: tuple[str, ...] = (), limit: int = 16_384) -> dict[str, str]:
    """The posted form as the companion's h.form reads it (the first value per name), plus, for
    each name in ``lists`` (tick lists: one value per tick), ``<name>_list``: every value joined
    by newlines."""
    from urllib.parse import parse_qs

    raw = await read_limited(request, limit)
    if raw is None:
        return {}
    multi = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    data = {k: v[0] for k, v in multi.items()}
    for key in lists:
        data[f"{key}_list"] = "\n".join(x for x in multi.get(key, []) if x)
    return data


def budget_html(budget: str, *, labelled_by: str = "") -> str:
    """Budget as one row of choices (packages.BUDGETS): the Package panel of Deck settings and the
    New package box. ``budget``: "" (not set) or "1" to "4"."""
    budgets = "".join(
        f"<label><input type='radio' name='budget' value='{val}'{' checked' if budget == val else ''}>"
        f"{label}</label>"
        for val, label in BUDGET_CHOICES
    )
    lab = f"aria-labelledby='{esc(labelled_by)}'" if labelled_by else "aria-label='Budget'"
    return f"<span class='budget' role='radiogroup' {lab}>{budgets}</span>"


def formats_html(formats: str, *, labelled_by: str = "") -> str:
    """Intended formats as pills, one checkbox each. ``formats``: the picked format names joined by
    newlines (the shape the posted ``format_list`` has)."""
    picked = {f for f in formats.split("\n") if f}
    fmts = "".join(
        f"<label><input type='checkbox' name='format' value='{esc(n)}'{' checked' if n in picked else ''}>"
        f"{esc(format_label(n))}</label>"
        for n in PACKAGE_FORMATS
    )
    lab = f"aria-labelledby='{esc(labelled_by)}'" if labelled_by else "aria-label='Intended formats'"
    return f"<div class='fmts' role='group' {lab}>{fmts}</div>"


def new_package_box_html(
    csrf: str,
    values: dict[str, str] | None = None,
    *,
    error: str = "",
    open: bool = False,
    folders: dict[str, Any] | None = None,
    folder: str = "",
) -> str:
    """The New package box on My decks (mockup screens/22-packages.js): Name and Keywords (both
    required, as Archidekt requires), Budget, Folder, Intended formats and Private. Create package
    is the member's own press: decks.propose_new_deck(package=...) with no cards, applied at once
    as New deck is. ``values`` are the typed values kept after a refusal (``error`` is its strip).
    ``folders``: the member's folder tree for the Folder field (None: the top level alone);
    ``folder``: the one picked to begin with (the folder My decks has open)."""
    v = values or {}
    name = v.get("name", "")
    private = v.get("private", "" if "name" in v else "1")
    folder_opts = folder_options_html(folders, v.get("folder_id", folder))
    body = (
        f"<form id='npf' method='post' action='/decks/new-package' novalidate>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<p class='muted small'>{esc(NEW_PACKAGE_INTRO)}</p>"
        "<div class='dx-two'>"
        "<div class='field'><label for='np-name'>Name (required)</label>"
        f"<input id='np-name' type='text' name='name' value='{esc(name)}' placeholder='e.g. Golgari staples' "
        "required maxlength='120' autocomplete='off'>"  # counted by static/newpackage.js, as New deck's name
        "<div class='dx-under'><span></span>"
        f"<span class='muted small' id='np-name-c' aria-live='polite'>{len(name)}/120</span></div></div>"
        "<div class='field'><label for='np-kw'>Keywords (required)</label>"
        f"<input id='np-kw' type='text' name='keywords' value='{esc(v.get('keywords', ''))}' "
        f"placeholder='shocklands, fetchlands' required maxlength='{packages.KEYWORDS_MAX}' "
        "autocomplete='off'><div class='dx-under'><span class='muted small'>Words people search packages "
        "by, separated by commas.</span></div></div></div>"
        + "<div class='dx-two'><div class='field'><span class='lbl' id='np-bl'>Budget</span>"
        + budget_html(v.get("budget", ""), labelled_by="np-bl")
        + "</div><div class='field'><label for='np-folder'>Folder</label><span class='sel'>"
        f"<select id='np-folder' name='folder_id'>{folder_opts}</select></span></div></div>"
        "<div class='field'><span class='lbl' id='np-fl'>Intended formats</span>"
        + formats_html(v.get("formats", ""), labelled_by="np-fl")
        + "</div>"
        + switch("private", "Private", bool(private))
        + "</form>"
        + (ui.notice(error, "error", role="alert", cls="boxnote") if error else "")
    )
    return ui.box(
        "newpkg",
        "New package",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Create package",
                kind="primary",
                form="npf",
                icon_name="plus",
                attrs="data-busy-label='Creating…'",
            ),
        ],
        wide=True,
        open=open,
        close_href="/decks",
        cls="dx-box dx-wide np-box",
    )


PACKAGE_FIELDS_CSS = """
/* a package's Budget (one row of choices) and Intended formats (pills): the Package panel of Deck
   settings and the New package box */
.budget{display:inline-flex;flex-wrap:wrap}
.budget label{display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;
  padding:0 .6rem;border:1px solid var(--control-border);margin:0 0 0 -1px;font-weight:700;cursor:pointer;
  background:var(--surface);position:relative}
.budget label:first-child{margin-left:0;border-radius:var(--radius) 0 0 var(--radius)}
.budget label:last-child{border-radius:0 var(--radius) var(--radius) 0}
.budget input{position:absolute;opacity:0;width:0;height:0;margin:0}
.budget label:has(input:checked){background:var(--orange-tint);color:var(--orange-text);
  border-color:var(--orange-text);z-index:1}
.budget label:has(input:focus-visible){outline:2px solid var(--focus);outline-offset:2px}
.fmts{display:flex;flex-wrap:wrap;gap:.35rem}
.fmts label{display:inline-flex;align-items:center;gap:.35rem;min-height:44px;
  border:1px solid var(--control-border);border-radius:999px;padding:0 .7rem;cursor:pointer;
  font-weight:400;margin:0}
.fmts input{width:18px;height:18px;min-height:0;margin:0;accent-color:var(--orange-ctl)}
/* a box's or a settings row's inputs are otherwise full width and its labels block: these keep
   their own shape */
.mtgbox .body .field .fmts input[type=checkbox],.ds .ui-row .ctl .fmts input{width:18px;height:18px}
.mtgbox .body .field .budget input[type=radio],.ds .ui-row .ctl .budget input{width:0;height:0}
.mtgbox .body .field .fmts label,.dx-box .body .field .fmts label{display:inline-flex;margin:0;
  font-weight:400}
.mtgbox .body .field .budget label,.dx-box .body .field .budget label{display:inline-flex;margin:0 0 0 -1px}
.mtgbox .body .field .budget label:first-child,.dx-box .body .field .budget label:first-child{margin-left:0}
.np-box .body .field .lbl{display:block;font-weight:700;margin:0 0 .25rem}
.np-box .body .field + .field{margin-top:.75rem}
.np-box .body .dx-switch{margin-top:.75rem;min-height:44px}
"""

CSS = """
/* the release-2 boxes (deck_extras.py): a wider box for forms with two columns, the strip above
   the banner while the deck is listed for help */
.mtgbox.dx-wide{width:min(100%,44rem)}
.dx-box .body .field label{display:block;font-weight:700;margin:0 0 .25rem}
.dx-box .body .field input[type=text],.dx-box .body .field select,.dx-box .body .field textarea{width:100%}
.dx-box .body textarea{min-height:5rem;font-family:inherit;font-size:.95rem;line-height:1.4}
.dx-box .body .dx-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));
  gap:.5rem .75rem}
.dx-box .body .dx-two .field + .field{margin-top:0}
.dx-box .body .dx-two + .dx-two{margin-top:.75rem}  /* a row's help line clear of the next row's labels */
.mtgbox .body .dx-two + .field,.mtgbox .body form > p + .dx-two,
.mtgbox .body form > p + .field{margin-top:.75rem}
.dx-under{display:flex;justify-content:space-between;gap:.5rem;margin-top:.2rem}
.dx-bad{color:var(--danger-text);font-weight:700}
.dx-why{display:flex;flex-wrap:wrap;gap:.3rem .5rem;align-items:center}
.dx-box .body .dx-switches{display:grid}
.dx-box .body .dx-switch{min-height:44px;margin:0;border-top:1px solid var(--border-soft);font-weight:400}
.dx-box .body .dx-switch:first-child{border-top:0}
.dx-box .body .dx-mine .lbl{display:block;font-weight:700;margin:0 0 .25rem}
.dx-box .body .dx-mine .tree label span{display:block;min-width:0}
.dx-box .body .dx-mine .tree label .s{color:var(--text-muted);font-size:.86rem}
.dx-box .body .dx-mine .tree p{margin:.3rem .5rem}
/* the Add a package box's chips (Mine, Followed, All packages), its name, format and budget
   filters in one row (one under another on a phone), the list (its own scroll past 18 rem)
   and Load more */
.dx-box .body .dx-mine .ui-chips{margin:0 0 .6rem}
.dx-box .body .dx-mine .ui-chip{margin:0;font:inherit;font-weight:700;font-size:.9rem;line-height:1.2}
.dx-box .body .dx-pkf{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) minmax(0,1fr);
  gap:.5rem .75rem;margin:0 0 .75rem}
.dx-box .body .dx-pkf .field{margin:0;min-width:0}
.dx-box .body .dx-pkf .field input[type=search]{width:100%;min-height:44px}
.dx-box .body .dx-pkf .msel-btn{max-width:100%}
@media (max-width:599.98px){ .dx-box .body .dx-pkf{grid-template-columns:minmax(0,1fr)} }
.dx-box .body .dx-mine .tree{max-height:18rem;overflow:auto}
.dx-box .body .dx-mine .tree label{min-height:44px}
.dx-box .body .dx-more{margin:.5rem 0 0}
.dx-box .body .dx-more[hidden]{display:none}
.dx-box .body .dx-more .btn{margin:0;min-height:44px}
.dx-box .body .dx-more .btn[aria-busy=true]{cursor:progress;opacity:.7}
.dx-box .body .dx-mine + .field{margin-top:.75rem}
/* the Color tag select with the chosen tag's colour beside it */
.dx-box .body .dx-tagsel{display:flex;align-items:center;gap:.5rem}
.dx-box .body .dx-tagsel .sel{flex:1 1 auto;min-width:0}
.dx-box .body .dx-tagsel .dx-tagdot{width:16px;height:16px;border:1px solid var(--border)}
.dx-box .body .dx-tagsel .dx-tagdot[hidden]{display:none}
.dx-box .body .notice{margin:0}
.dx-box .acts .dx-gap{flex:1 1 auto}
.dx-box .acts .dx-delist{margin:0}
.dx-box .body .ui-lbl{vertical-align:middle}
.dx-box .boxnote{margin-top:.75rem}
.dx-pdf-done a{display:inline-flex;align-items:center;gap:.3rem;min-height:44px;font-weight:700}
.dx-pdf-done a svg.i{width:16px;height:16px}
.dx-strip{border:1px solid var(--card-border);border-left:4px solid var(--orange);
  border-radius:var(--radius-panel);background:var(--surface);padding:.6rem .9rem;margin:0 0 12px;
  display:flex;flex-wrap:wrap;gap:.4rem .9rem;align-items:center}
.dx-strip .t{flex:1 1 16rem;min-width:0}
.dx-strip .t b{display:flex;align-items:center;gap:.4rem}
.dx-strip .t b svg.i{width:18px;height:18px;flex:none}
.dx-strip .t .muted{display:block}
.dx-strip .btn{margin:0}
@media (max-width:599.98px){
  .dx-box .acts .dx-gap{display:none}
  .dx-box .acts .dx-delist{flex:1 1 100%}
  .dx-strip .btn{flex:1 1 100%}
}
"""


# -- routes --------------------------------------------------------------------------------------------


def add_deck_extras_routes(server: Any, state: Any, h: SimpleNamespace) -> None:
    decks = h.decks
    page = h.page
    check_csrf = h.check_csrf
    form = h.form

    def wants_json(request: Request) -> bool:
        return "application/json" in (request.headers.get("accept") or "")

    def expired(request: Request, sub: str, sid: str | None) -> Response:
        if wants_json(request):
            return JSONResponse(
                {"ok": False, "message": "This form expired. Reload and try again."}, status_code=403
            )
        return page("Deck", h.EXPIRED, sub=sub, sid=sid, status=403)

    async def back_with(
        request: Request,
        sub: str,
        sid: str | None,
        deck_id: str,
        box: str,
        exc: DeckError,
        values: dict[str, str],
    ) -> Response:
        """A refusal: the box drawn open again with what was typed and the refusal's words."""
        if wants_json(request):
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        if busy := busy_response(exc, request, page, sub=sub, sid=sid):
            return busy
        return await h.deck_page_response(
            request, sub, sid, deck_id, box=box, box_error=str(exc), box_values=values, status=400
        )

    def to_review(request: Request, p: dict[str, Any]) -> Response:
        """The member's press is on the proposal's review page."""
        url = f"/proposals/{p['proposal_id']}"
        if wants_json(request):
            return JSONResponse({"ok": True, "proposal_id": p["proposal_id"], "review_url": url})
        return RedirectResponse(url, status_code=303)

    async def member(request: Request, deck_id: str) -> tuple[str, str | None, dict[str, str]] | Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}")
        data = await form(request, limit=64_000)
        if not check_csrf(sid, data):
            return expired(request, sub, sid)
        current_client.set(BROWSER_CLIENT_ID)
        return sub, sid, data

    # -- the old-style addresses: the page with the box open ---------------------------------------
    for name in ("help", "delist", "registration", "package", "contest", "withdraw", "lock"):
        box_name = {"registration": "pdf"}.get(name, name)

        def make(box_name: str = box_name, name: str = name) -> Any:
            async def opened(request: Request) -> Response:
                deck_id = request.path_params["deck_id"]
                sub, sid = browser_session(state, request)
                if not sub:
                    return login_redirect(f"/decks/{deck_id}/{name}")
                values: dict[str, str] = {}
                templates = None
                color_tags = None
                if box_name == "package" and request.query_params.get("package"):
                    # Add to a deck… on a package tile (My decks) names the package
                    values = {"package": request.query_params["package"][:200]}
                if box_name == "package":
                    # the Color tag select's member tags, for the page without scripts (with
                    # them, the box reads /decks/api/color_tags as it opens)
                    try:
                        color_tags = (await account_lists.read(decks, sub, "color_tags"))["items"]
                    except DeckError:
                        color_tags = None  # the box still opens with the deck's own tags
                if box_name == "lock":
                    card = (request.query_params.get("card") or "")[:200]
                    try:
                        deck = await decks.get_own_deck(sub, deck_id)
                        hit = next((c for c in deck.cards if c.name.casefold() == card.casefold()), None)
                        templates = [
                            t["name"] for t in (await account_lists.read(decks, sub, "templates"))["items"]
                        ]
                    except DeckError as exc:
                        return h.deck_problem(request, exc, sub=sub, sid=sid)
                    values = {
                        "card": hit.name if hit else card,
                        "categories": "\n".join(hit.categories) if hit else "",
                    }
                return await h.deck_page_response(
                    request,
                    sub,
                    sid,
                    deck_id,
                    box=box_name,
                    box_values=values,
                    templates=templates,
                    color_tags=color_tags,
                )

            return opened

        server.custom_route(f"/decks/{{deck_id}}/{name}", methods=["GET"], include_in_schema=False)(make())

    # (/decks/{id}/report is the deck's analysis report: this one names where it goes)
    @server.custom_route("/decks/{deck_id}/report-to-archidekt", methods=["GET"], include_in_schema=False)
    async def report_opened(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/report-to-archidekt")
        card = (request.query_params.get("card") or "")[:200]
        return await h.deck_page_response(
            request, sub, sid, deck_id, box="reportcard" if card else "report", box_values={"card": card}
        )

    # -- the presses --------------------------------------------------------------------------------

    @server.custom_route("/decks/{deck_id}/help", methods=["POST"], include_in_schema=False)
    async def help_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        got = await member(request, deck_id)
        if isinstance(got, Response):
            return got
        sub, sid, data = got
        what = data.get("help_action") or "post"
        values = {k: data.get(k, "") for k in ("goals", "issues")}
        try:
            p = await decks.propose_action(sub, "deck_help", deck_id=deck_id, help_action=what, **values)
        except DeckError as exc:
            return await back_with(
                request, sub, sid, deck_id, "delist" if what == "delist" else "help", exc, values
            )
        return to_review(request, p)

    @server.custom_route("/decks/{deck_id}/report-to-archidekt", methods=["POST"], include_in_schema=False)
    async def report_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        got = await member(request, deck_id)
        if isinstance(got, Response):
            return got
        sub, sid, data = got
        kind = data.get("report_kind") if data.get("report_kind") in ("card", "comment") else "deck"
        values = {
            "message": data.get("message", ""),
            "card": data.get("card", "")[:200],
            "comment_id": data.get("comment_id", "")[:20],
            "page": data.get("page", "")[:6],
            "who": data.get("who", "")[:150],
        }
        box = {"card": "reportcard", "comment": "reportcomment"}.get(kind, "report")
        params: dict[str, Any] = {"deck_id": deck_id, "report_kind": kind, "message": values["message"]}
        if kind == "card":
            params["card"] = values["card"]
            try:
                deck = await decks.get_any_deck(sub, deck_id)
                hit = next((c for c in deck.cards if c.name.casefold() == values["card"].casefold()), None)
                # a custom card: one marked so in the cards, or one of Deck.custom_cards
                if (hit is not None and hit.custom) or any(
                    c.name.casefold() == values["card"].casefold() for c in deck.custom_cards
                ):
                    params["report_kind"] = "custom_card"
            except DeckError as exc:
                return await back_with(request, sub, sid, deck_id, "reportcard", exc, values)
        elif kind == "comment":
            params["comment_id"] = int(values["comment_id"]) if values["comment_id"].isdigit() else None
            params["page"] = int(values["page"]) if values["page"].isdigit() else 1
        try:
            p = await decks.propose_action(sub, "report", **params)
        except DeckError as exc:
            return await back_with(request, sub, sid, deck_id, box, exc, values)
        return to_review(request, p)

    @server.custom_route("/decks/{deck_id}/registration", methods=["POST"], include_in_schema=False)
    async def registration_post(request: Request) -> Response:
        """The registration sheet: a read answered with the PDF's address. The details go to
        Archidekt and nowhere else."""
        deck_id = request.path_params["deck_id"]
        got = await member(request, deck_id)
        if isinstance(got, Response):
            return got
        sub, sid, data = got
        keys = ("first_name", "last_name", "dci_number", "event", "location", "date", "deck_name", "designer")
        details = {k: data.get(k, "") for k in keys}
        sort = data.get("sort") or "alpha"
        values = {**details, "sort": sort}
        try:
            out = await registration.make(decks, sub, deck_id, details, sort)
        except DeckError as exc:
            return await back_with(request, sub, sid, deck_id, "pdf", exc, values)
        if wants_json(request):
            return JSONResponse(
                {
                    "ok": True,
                    "pdf_url": out["pdf_url"],
                    "main_deck": out["main_deck"],
                    "sideboard": out["sideboard"],
                }
            )
        return await h.deck_page_response(
            request, sub, sid, deck_id, box="pdf", box_values={**values, "pdf_url": out["pdf_url"]}
        )

    @server.custom_route("/decks/{deck_id}/package", methods=["POST"], include_in_schema=False)
    async def package_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        got = await member(request, deck_id)
        if isinstance(got, Response):
            return got
        sub, sid, data = got
        values = {
            k: data.get(k, "")
            for k in (
                "package",
                "categories",
                "skip_existing",
                "skip_outside_identity",
                "singleton",
                "color_tag",
            )
        }
        try:
            ref = values["package"].strip()
            if not ref:
                raise DeckError("invalid", "Pick one of your packages, or paste a package link or id.")
            cat = values["categories"] if values["categories"] in packages.CATEGORY_CHOICES else "package"
            p = await decks.propose_package(
                sub,
                deck_id,
                ref,
                categories=cat,
                skip_existing=values["skip_existing"] == "1",
                skip_outside_identity=values["skip_outside_identity"] == "1",
                singleton=values["singleton"] == "1",
                # the box's choice is "Name,#rrggbb" (the deck's tags and the member's color tags)
                color_tag=values["color_tag"].rsplit(",", 1)[0] if values["color_tag"] else None,
                color=values["color_tag"].rsplit(",", 1)[1] if "," in values["color_tag"] else None,
            )
        except DeckError as exc:
            return await back_with(request, sub, sid, deck_id, "package", exc, values)
        return to_review(request, p)

    @server.custom_route("/decks/{deck_id}/contest", methods=["POST"], include_in_schema=False)
    async def contest_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        got = await member(request, deck_id)
        if isinstance(got, Response):
            return got
        sub, sid, data = got
        withdraw = data.get("entry_action") == "withdraw"
        values = {"contest": data.get("contest", "")[:300], "summary": data.get("summary", "")}
        try:
            if withdraw:
                p = await decks.propose_action(
                    sub, "contest_withdraw", deck_id=deck_id, contest=values["contest"]
                )
            else:
                p = await decks.propose_action(
                    sub,
                    "contest_enter",
                    deck_id=deck_id,
                    contest=values["contest"],
                    summary=values["summary"],
                )
        except DeckError as exc:
            return await back_with(
                request, sub, sid, deck_id, "withdraw" if withdraw else "contest", exc, values
            )
        return to_review(request, p)

    @server.custom_route("/decks/{deck_id}/lock", methods=["POST"], include_in_schema=False)
    async def lock_post(request: Request) -> Response:
        """Lock personal categories: the card's categories in this deck, kept to the member's
        templates, saved as its default categories (account_lists.hand_change, at once)."""
        deck_id = request.path_params["deck_id"]
        got = await member(request, deck_id)
        if isinstance(got, Response):
            return got
        sub, sid, data = got
        card = (data.get("card") or "").strip()[:200]
        values = {"card": card, "categories": ""}
        try:
            deck = await decks.get_own_deck(sub, deck_id)
            hit = next((c for c in deck.cards if c.name.casefold() == card.casefold()), None)
            if hit is None:
                raise DeckError("not_found", f"“{card}” is not in this deck.")
            if hit.custom:
                raise DeckError(
                    "invalid", "Not for custom cards (Archidekt doesn't keep default categories for them)."
                )
            values = {"card": hit.name, "categories": "\n".join(hit.categories)}
            names = [t["name"] for t in (await account_lists.read(decks, sub, "templates"))["items"]]
            kept = [c for c in hit.categories if c in names]
            if not kept:
                raise DeckError(
                    "invalid",
                    f"None of this card's categories ({', '.join(hit.categories) or 'none'}) is one of your "
                    "category templates, so there is nothing to lock. Make a template first on My lists.",
                )
            out = await account_lists.hand_change(
                decks, sub, "default_categories", {"op": "set", "card": hit.name, "categories": kept}
            )
        except DeckError as exc:
            return await back_with(request, sub, sid, deck_id, "lock", exc, values)
        result = out.get("result") if isinstance(out.get("result"), dict) else {}
        text = OK_MESSAGES["locked"].format(name=hit.name)
        if wants_json(request):
            return JSONResponse(
                {"ok": True, "notice": text, "verified": result.get("verified"), "kept": kept}
            )
        return RedirectResponse(
            f"/decks/{deck.id}?ok=locked&{urlencode({'n': hit.name[:200]})}", status_code=303
        )

    # -- the lists a box reads when it opens ---------------------------------------------------------

    @server.custom_route("/decks/api/templates", methods=["GET"], include_in_schema=False)
    async def templates_json(request: Request) -> Response:
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated"}, 401, headers={"Cache-Control": "no-store"}
            )
        try:
            got = await account_lists.read(decks, sub, "templates")
        except DeckError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)}, 400, headers={"Cache-Control": "no-store"}
            )
        return JSONResponse({"ok": True, "templates": got["items"]}, headers={"Cache-Control": "no-store"})

    @server.custom_route("/decks/api/color_tags", methods=["GET"], include_in_schema=False)
    async def color_tags_json(request: Request) -> Response:
        """The member's color tags for the Add a package box's Color tag select, read as the box
        opens (never on a deck page load)."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated"}, 401, headers={"Cache-Control": "no-store"}
            )
        try:
            got = await account_lists.read(decks, sub, "color_tags")
        except DeckError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)}, 400, headers={"Cache-Control": "no-store"}
            )
        tags = [{"name": t["name"], "color": t["color"]} for t in got["items"] if t["name"]]
        return JSONResponse({"ok": True, "tags": tags}, headers={"Cache-Control": "no-store"})

    @server.custom_route("/decks/api/packages", methods=["GET"], include_in_schema=False)
    async def packages_json(request: Request) -> Response:
        """Card packages for the Add a package box, read when it opens and when its chips or
        filters change: ``whose`` (mine, followed, all), ``q`` (name), ``format``, ``budget``,
        ``page``, as packages.listing takes them."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated"}, 401, headers={"Cache-Control": "no-store"}
            )
        q = request.query_params
        try:
            budget = int(q["budget"]) if q.get("budget") else None
            page = int(q.get("page") or 1)
        except ValueError:
            return JSONResponse(
                {"ok": False, "message": "budget and page are numbers"},
                400,
                headers={"Cache-Control": "no-store"},
            )
        try:
            got = await packages.listing(
                decks,
                sub,
                whose=q.get("whose") or "mine",
                name_contains=q.get("q"),
                intended_format=q.get("format") or None,
                budget=budget,
                page=page,
            )
        except DeckError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)}, 400, headers={"Cache-Control": "no-store"}
            )
        rows = [
            {
                "id": r["id"],
                "name": r["name"],
                "line": package_line(r["owner"], r["size"], r["block"]),
                # for a package's tile on My decks: "$$" ("" for any) and "Commander, Modern"
                "budget": packages.BUDGETS.get(r["block"].get("budget"), "any").replace("any", ""),
                "formats": package_formats(r["block"]),
            }
            for r in got["rows"]
        ]
        return JSONResponse(
            {"ok": True, "packages": rows, "more": got["more"]}, headers={"Cache-Control": "no-store"}
        )


def package_formats(block: dict[str, Any]) -> str:
    """A package's intended formats as 'Commander, Modern' ("" when any)."""
    return ", ".join(
        format_label(FORMAT_NAMES.get(f)) for f in block.get("intendedFormats") or [] if f in FORMAT_NAMES
    )


def package_line(owner: str, size: Any, block: dict[str, Any]) -> str:
    """'alice · 14 cards · $$ · Commander' under a package's name."""
    parts = [owner] if owner else []
    if isinstance(size, int):
        parts.append(f"{size} card{'s' if size != 1 else ''}")
    budget = packages.BUDGETS.get(block.get("budget"), "any")
    parts.append(budget if budget != "any" else "any budget")
    parts.append(package_formats(block) or "any format")
    return " · ".join(parts)


def card_attr(card: DeckCard) -> str:
    """The card row's marker for the card menu (static/deck.js): a custom card gets no Lock."""
    return " data-custom='1'" if card.custom else ""


def notice_html(code: str, name: str) -> str:
    text = OK_MESSAGES.get(code)
    return ui.notice(text.format(name=name), "ok", role="status") if text else ""
