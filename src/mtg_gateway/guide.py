"""The in-app guide: what a member can do on these pages, with or without an AI assistant.

End-user documentation only, in the member's language, arranged the Diátaxis way in four parts:
tutorials (learning, step by step), how-to guides (one task at a time), reference (the facts,
to look up) and explanation (how and why things work). Nothing about deploying or operating a
gateway lives here (that is the repository's docs). The page needs the same sign-in as every
other page and reads a few facts from the gateway (its name, whether deck writes are on, the
low-risk row limit, whether the backup copy on Archidekt is offered) so it describes this
gateway and not a generic one.

The page is one long document with a nested contents rail: ``static/guide.js`` adds the search
box, keeps the rail's current entry in step with the reader's position and shows the
"Back to top" button; without script the whole guide is simply visible and every link works.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response

from . import modes
from .pages import _csrf, browser_session, login_redirect
from .theme import display_name, icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .config import LiveSettings

_esc = html.escape

GUIDE_CSS = """
.guide{display:grid;grid-template-columns:minmax(0,1fr);gap:1rem;align-items:start}
.guide h2,.guide h3,.guide h4{overflow-wrap:anywhere}
.guide .gside{min-width:0}
.gsearch{margin:0 0 .75rem}
.gsearch label{display:block;font-weight:700;margin-bottom:.25rem}
.gsearch input[type=search]{width:100%}
.gsearch .status{min-height:1.2em;margin:.35rem 0 0}
.guide .gresults{list-style:none;margin:.35rem 0 0;padding:0}
.guide .gresults li{margin:0 0 .25rem}
.gresults a{display:block;padding:.4rem .6rem;border-radius:var(--radius);color:var(--text);
  text-decoration:none;border:1px solid var(--border-soft)}
.gresults a:hover,.gresults a:focus-visible{background:var(--surface-2);color:var(--text)}
.gresults .t{display:block;font-weight:700;overflow-wrap:anywhere}
.gresults .snip{display:block;color:var(--text-muted);font-size:.86rem;overflow-wrap:anywhere}
.gresults mark{background:var(--orange-tint);color:var(--text);border-radius:2px;padding:0 .1rem}
details.gnav{margin:0 0 .5rem}
details.gnav > summary{list-style:none;cursor:pointer;user-select:none;width:100%}
details.gnav > summary::-webkit-details-marker{display:none}
.gnav nav{margin-top:.5rem}
.gnav ul{list-style:none;margin:0;padding:0}
.gnav > nav > ul > li{margin:0 0 .35rem}
.gnav li ul{margin:.1rem 0 .25rem .75rem;border-left:2px solid var(--border-soft);padding-left:.35rem}
.gnav a{display:flex;align-items:center;gap:.5rem;padding:.4rem .6rem;border-radius:var(--radius);
  color:var(--text);text-decoration:none;min-height:2.2rem}
.gnav a.part{font-weight:800}
.gnav a.part svg{color:var(--orange)}
.gnav li ul a{font-size:.95rem;color:var(--text-muted)}
.gnav a:hover{background:var(--surface-2);color:var(--text)}
.gnav a[aria-current=true]{background:var(--orange-tint);color:var(--text);font-weight:700;
  box-shadow:inset 3px 0 0 var(--orange)}
.gnav a.part.in{color:var(--orange-text)}
.gmain{min-width:0}
.gpart{margin:0 0 1.5rem}
.gpart > header{margin:0 0 .75rem}
.gpart > header h2{display:flex;align-items:center;gap:.5rem;font-size:1.6rem;margin:0 0 .15rem}
.gpart > header h2 svg{color:var(--orange);width:28px;height:28px}
.gpart > header p{margin:0;color:var(--text-muted)}
.gsec{margin:0 0 1rem;scroll-margin-top:4.5rem}
.gpart,.guide h4{scroll-margin-top:4.5rem}
.gsec h3{display:flex;align-items:center;gap:.5rem;font-size:1.3rem;margin:0 0 .5rem}
.gsec h3 svg{color:var(--orange)}
.gsec h4{font-size:1.05rem;margin:1rem 0 .25rem;display:flex;align-items:center;gap:.4rem}
.guide .anchor{color:var(--text-muted);text-decoration:none;font-weight:400;opacity:0;margin-left:auto;
  padding:0 .35rem;border-radius:var(--radius);font-size:.9em}
.guide h2:hover .anchor,.guide h3:hover .anchor,.guide h4:hover .anchor,
.guide .anchor:focus-visible{opacity:1}
.guide .anchor:hover{color:var(--orange-text);background:var(--surface-2)}
.guide ul,.guide ol{padding-left:1.2rem}
.guide li{margin:.2rem 0}
.guide p,.guide li{overflow-wrap:anywhere}
.guide .tip{border-left:3px solid var(--orange);padding:.5rem .75rem;background:var(--orange-tint);
  border-radius:0 var(--radius) var(--radius) 0;margin:.75rem 0}
.guide .steps{counter-reset:step;list-style:none;padding:0}
.guide .steps li{counter-increment:step;display:flex;gap:.6rem;margin:.4rem 0}
.guide .steps li::before{content:counter(step);flex:0 0 1.6rem;height:1.6rem;border-radius:50%;
  background:var(--orange);color:var(--on-orange);font-weight:900;display:inline-flex;align-items:center;
  justify-content:center;font-size:.9rem}
.guide .tbl{overflow-x:auto;max-width:100%;container-type:inline-size}
.guide table{border-collapse:collapse;width:100%;font-size:.95rem}
.guide th,.guide td{text-align:left;vertical-align:top;padding:.4rem .5rem;overflow-wrap:break-word;
  border-bottom:1px solid var(--border-soft)}
.guide th{background:var(--surface-2);font-weight:700}
/* Where four columns would squeeze to a word a line (a phone, or large text: the query is in em, so
   it follows the text size), each row is a block and each cell sits under its column's name. The
   roles keep it a table for screen readers; the names come from data-label, with no spoken text. */
@container (max-width:40em){
  .guide table,.guide tbody,.guide tr,.guide td{display:block;width:auto}
  .guide thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);
    white-space:nowrap}
  .guide tbody tr{border-bottom:1px solid var(--border-soft);padding:.35rem 0}
  .guide tbody td{border:0;padding:.15rem .5rem}
  .guide td[data-label]::before{content:attr(data-label);content:attr(data-label) / "";display:block;
    font-size:.85em;font-weight:700;color:var(--text-muted)}
}
.guide code,.guide kbd{font:inherit;font-size:.9em;background:var(--surface-2);border-radius:3px;
  padding:.05rem .3rem}
.guide kbd{border:1px solid var(--border);font-weight:700;white-space:nowrap}
.guide .nomatch{margin:1rem 0}
.gtop{position:fixed;right:1rem;bottom:1.5rem;z-index:7;display:inline-flex;align-items:center;gap:.4rem;
  box-shadow:var(--shadow)}
.gtop.hide{display:none}
.gtop svg{transform:rotate(180deg)}
@media (max-width:599px){.gtop{bottom:calc(4.5rem + env(safe-area-inset-bottom))}}
@media (min-width:900px){
  .guide{grid-template-columns:16rem minmax(0,1fr);gap:1.5rem}
  .guide .gside{position:sticky;top:4rem;max-height:calc(100vh - 5rem);overflow:auto;padding-right:.25rem}
  details.gnav > summary{display:none}
  details.gnav:not([open]) > nav{display:block}
}
"""

# The four parts: id, icon, title and lead, then the sections (id, icon, label) in reading order.
# A section whose id ends up without a body (the collection on a gateway without one) is dropped
# from the rail and the page together, so the two never disagree.
PARTS: list[tuple[str, str, str, str, list[tuple[str, str, str]]]] = [
    (
        "tutorials",
        "play",
        "Tutorials",
        "Learn the site by doing: the first time through, one step at a time.",
        [
            ("tut-start", "home", "Start here"),
            ("tut-link", "link", "Link Archidekt"),
            ("tut-connect", "report", "Connect your assistant"),
            ("tut-first-edit", "edit", "Your first deck edit"),
            ("tut-first-sim", "stats", "Your first solo test"),
        ],
    ),
    (
        "howto",
        "check",
        "How-to guides",
        "One task at a time, for when you already know your way around.",
        [
            ("how-new-deck", "plus", "Create a deck"),
            ("how-add-cards", "edit", "Add or change cards"),
            ("how-organise", "folder", "Settings, folders, tags and deleting"),
            ("how-approve", "proposals", "Approve a proposal"),
            ("how-restore", "undo", "Restore a snapshot"),
            ("how-compare", "swap", "Compare with a precon"),
            ("how-export", "download", "Export and import"),
            ("how-search", "search", "Find public decks and precons"),
            ("how-scan", "camera", "Scan a photo of your cards"),
            ("how-collection", "collection", "Use your collection"),
            ("how-playtest", "play", "Playtest a deck"),
            ("how-layout", "sun", "Change the theme or layout"),
            ("how-app", "download", "Use the Android app"),
            ("how-signout", "x", "Sign out everywhere"),
        ],
    ),
    (
        "reference",
        "list",
        "Reference",
        "The facts, laid out to look things up.",
        [
            ("ref-assistant", "report", "What the assistant can do, by tool"),
            ("ref-pages", "layers", "Pages and addresses"),
            ("ref-approval", "proposals", "Approval modes and risk levels"),
            ("ref-keys", "menu", "Keyboard shortcuts"),
            ("ref-syntax", "edit", "The editor's search bar and list syntax"),
            ("ref-formats", "download", "Export formats"),
        ],
    ),
    (
        "explanation",
        "guide",
        "Explanation",
        "How things work behind the pages, and why they work that way.",
        [
            ("exp-approvals", "proposals", "Approvals, snapshots and backups"),
            ("exp-fresh", "refresh", "How lists stay fresh"),
            ("exp-assistant", "report", "How the assistant works with you"),
            ("exp-privacy", "account", "Privacy and what is stored"),
            ("exp-playtest", "play", "Why Playtest opens Archidekt in a new tab"),
        ],
    ),
]


def _anchor(target: str, label: str) -> str:
    return f"<a class='anchor' href='#{target}' aria-label='Link to “{_esc(label)}”'>#</a>"


def _h4(hid: str, label: str) -> str:
    return f"<h4 id='{hid}'>{_esc(label)}{_anchor(hid, label)}</h4>"


def _stackable(page: str) -> str:
    """Each reference table's cells carry their column's name (``data-label``) for the stacked layout
    on narrow screens or large text, and the table its roles, which ``display:block`` would drop."""

    def one(m: re.Match[str]) -> str:
        head, body = m.group(1), m.group(2)
        names = [html.unescape(re.sub(r"<[^>]+>", "", h)) for h in re.findall(r"<th>(.*?)</th>", head, re.S)]
        head = head.replace("<tr>", "<tr role='row'>").replace("<th>", "<th role='columnheader'>")

        def row(r: re.Match[str]) -> str:
            cells = iter(names)
            return (
                "<tr role='row'>"
                + re.sub(
                    r"<td>", lambda _c: f"<td role='cell' data-label='{_esc(next(cells, ''))}'>", r.group(1)
                )
                + "</tr>"
            )

        body = re.sub(r"<tr>(.*?)</tr>", row, body, flags=re.S)
        return (
            f"<table role='table'><thead role='rowgroup'>{head}</thead>"
            f"<tbody role='rowgroup'>{body}</tbody></table>"
        )

    return re.sub(r"<table><thead>(.*?)</thead><tbody>(.*?)</tbody></table>", one, page, flags=re.S)


def _nav(parts: list[tuple[str, str, str, str, list[tuple[str, str, str]]]]) -> str:
    items = []
    for pid, ic, title, _lead, sections in parts:
        subs = "".join(f"<li><a href='#{sid}'>{_esc(label)}</a></li>" for sid, _i, label in sections)
        items.append(f"<li><a class='part' href='#{pid}'>{icon(ic)}{_esc(title)}</a><ul>{subs}</ul></li>")
    return (
        f"<details class='gnav' open><summary class='btn'>{icon('menu')} Contents</summary>"
        f"<nav aria-label='Guide contents'><ul>{''.join(items)}</ul></nav></details>"
    )


def guide_body(
    *,
    site: str,
    writes_enabled: bool,
    has_collection: bool,
    max_rows: int = modes.DEFAULT_MAX_ROWS,
    big_rows: int = modes.DEFAULT_BIG_EDIT_ROWS,
    archidekt_backups: bool = True,
    backup_folder: str = "MTG Gateway backups",
    watchlists: bool = False,
    live: LiveSettings | None = None,
) -> str:
    if live is not None:  # the approval rows as set now (live settings), over the start-up values
        max_rows = int(live.get("auto_apply_max_rows"))
        big_rows = int(live.get("auto_big_edit_rows"))
    folder = _esc(backup_folder)
    writes = (
        "Edits are switched on here: what you save in the app goes to Archidekt at once, and an "
        "approved proposal from your assistant is applied for you."
        if writes_enabled
        else "Archidekt writes are switched off on this gateway for now: what you save in the app and "
        "what your assistant proposes stay waiting, and can be applied once the person who runs the "
        "gateway switches writes on."
    )
    box_ticked = bool(live.get("archidekt_backup_box_default")) if live is not None else False
    backup_box = (
        (
            "The save bar has a tick box, <strong>Also keep a backup copy on Archidekt</strong>, ticked "
            "by default on this gateway: untick it to skip that extra copy for that one save."
            if box_ticked
            else "The save bar has a tick box, <strong>Also keep a backup copy on Archidekt</strong>, "
            "unticked by default on this gateway: tick it to also keep that extra copy for that one save."
        )
        if archidekt_backups
        else "The backup copy on Archidekt is switched off on this gateway, so no tick box is offered."
    )
    backup_assistant = "Applies made by your assistant always make the copy. " if archidekt_backups else ""
    backup_delete = "both the snapshot and the backup copy" if archidekt_backups else "the snapshot"
    # -- Tutorials --------------------------------------------------------------------------------
    tut_start = (
        f"<p>{_esc(site)} is a private companion to <strong>Archidekt</strong>. Everything you can do on "
        "the Archidekt website with your own decks, you can do here on your phone or computer, and an AI "
        "assistant such as Claude or ChatGPT can do it with you. Your decks stay on Archidekt; what you do "
        "here is saved there, and an assistant never changes one without a proposal you approve.</p>"
        "<p>The first time through, take the tutorials in order:</p>"
        "<ol class='steps'>"
        "<li><span><a href='#tut-link'><strong>Link Archidekt</strong></a> once on the Account page, so "
        "the site sees the decks of that account, private ones included.</span></li>"
        "<li><span><strong>Open <a href='/decks'>Decks</a></strong> to browse them the way Archidekt shows "
        "them: text, stacks or grid, grouped and sorted how you like; then make "
        "<a href='#tut-first-edit'>your first edit</a>.</span></li>"
        "<li><span><strong>Scan</strong> a pile of cards with your phone, add them to a deck or to your "
        "collection, and <strong>search</strong> public decks for ideas (the how-to guides cover "
        "each).</span></li>"
        "<li><span>Optional: <a href='#tut-connect'><strong>connect an assistant</strong></a> from the home "
        "page and ask it to research, simulate or edit for you. An assistant's edit comes back to you as a "
        "proposal.</span></li></ol>"
        "<p class='tip'>On a phone the tabs at the bottom are Home, Decks, Scan, Collection and More; "
        "the magnifier at the top opens the search box for cards and decks. More opens Proposals, History, "
        "Guide and Account (and Admin). "
        "Unfolded or on a tablet the tabs move to a rail down the left edge with every page on it, Search "
        "among them, and no More. "
        "Your account menu is your picture at the top right, and a tap anywhere else closes it.</p>"
    )
    tut_link = (
        "<p>Linking gives this site your Archidekt decks, your collection and the right to save for you. "
        "It takes a minute and you do it once.</p>"
        "<ol class='steps'>"
        "<li><span>Open the <a href='/account'>Account</a> page and find <strong>Archidekt</strong>.</span>"
        "</li>"
        "<li><span>Read the note there first: Archidekt has no official way for other apps to reach decks, "
        "so the site uses the same requests archidekt.com's own pages use, and Archidekt's terms restrict "
        "automated access, so it could limit or block an account used this way. You tick a box to say you "
        "accept that.</span></li>"
        "<li><span>Type your Archidekt login and password and press <strong>Link</strong>. The password is "
        "used once to sign in; the site keeps Archidekt's session, encrypted, and never shows it "
        "again.</span></li>"
        "<li><span>Open <a href='/'>Home</a> or <a href='/decks'>Decks</a>: your decks are there with "
        "their cover art, private ones included.</span></li></ol>"
        "<p>Unlinking on the same page removes the stored Archidekt session at once. The Account page "
        "spells out exactly what linking gives this site, its admins and the person who runs it "
        "(<a href='/account#archidekt-disclosure'>What linking gives this gateway</a>); "
        "<a href='#exp-privacy'>Privacy and what is stored</a> has the short version.</p>"
    )
    tut_connect = (
        "<p>Connect Claude or ChatGPT from the home page (<strong>Connect an AI assistant</strong>) on the "
        "web, desktop or phone app you use. You sign in with the same account as here, and the assistant "
        "acts as you: it can read your decks but never change one without a proposal you approve.</p>"
        "<ol class='steps'>"
        "<li><span>On <a href='/'>Home</a>, open <strong>Connect an AI assistant</strong> and follow the "
        "steps for your assistant; it ends with a sign-in to this site in your browser.</span></li>"
        "<li><span>Install the <a href='/install?for=skill'>assistant skill</a> once per assistant. "
        "It teaches your "
        "assistant the house rules: look things up instead of guessing, show the diff before changing "
        "anything, never ask for a password.</span></li>"
        "<li><span>Ask it something that only reads: “Show me my decks”, “open my Krenko deck and tell me "
        "its mana curve”.</span></li>"
        "<li><span>Ask for a change: “Swap these three cards”. It comes back as a <strong>proposal</strong> "
        "with a review link and, in Claude on the web, desktop and phones (and ChatGPT, as its maker "
        "reports), as a card in the chat with Approve and Reject. <a href='#how-approve'>Approve a "
        "proposal</a> has the details.</span></li></ol>"
        "<p>Things you can ask it:</p><ul>"
        "<li>“Search for Atraxa decks under bracket 3”, “what does this deck run that mine "
        "doesn't?”.</li>"
        "<li>“Look up this card”, rulings, prices, combos, EDHREC data, the Comprehensive Rules.</li>"
        "<li>“Goldfish this deck 500 times” (it reports the seed, the settings and what the "
        "simulation leaves out).</li>"
        "<li>“Build a new deck from this list”, “undo yesterday's change”: each comes back as a proposal "
        "with a review link.</li>"
        "<li>“Here are photos of my cards”: it reads the names and matches them; or “use my last scan” to "
        "pick up a scan from the Scan page.</li>"
        "<li>“Which cards in this deck do I own?” using your collection.</li></ul>"
        "<p>Your assistant's access can be withdrawn at any time from the Account page (the "
        "<strong>Connected apps</strong> list) and ends on its own after a while (a week, unless "
        "this gateway is set otherwise) unless you sign in "
        "again.</p>"
    )
    tut_first_edit = (
        "<p>Add one card to a deck of your own and save it, the way every edit works.</p>"
        "<ol class='steps'>"
        "<li><span>Open <a href='/decks'>Decks</a> and tap a deck, then <strong>Edit deck</strong> in its "
        "banner.</span></li>"
        "<li><span>In <strong>Add a card</strong>, the chips above the box say where the card goes: "
        "<strong>Auto</strong> (the gateway picks the category from the card's type), one of the deck's "
        "categories, or the maybeboard. Leave Auto on.</span></li>"
        "<li><span>Type a name. Suggestions appear as you type, with the card's picture, mana cost and "
        "type line; Down and Up move, <kbd>Enter</kbd> adds the highlighted card and keeps the cursor in "
        "the box for the next one. “3 sol ring” adds three. On a wide screen the printings of the "
        "highlighted card show as pictures beside the list, with the set used last on this deck "
        "pre-selected: a click adds that printing.</span></li>"
        "<li><span>The new row shows under <strong>Pending changes</strong>, with Undo. Nothing has gone "
        "to Archidekt yet.</span></li>"
        f"<li><span>Press <strong>Save changes</strong>. {backup_box} The whole session goes to Archidekt "
        "in one go, after a snapshot of the deck as it was; only a big removal (many cards at once) asks "
        "you to confirm first.</span></li>"
        "<li><span>Back on the deck page the card is there. Under <a href='/history'>History</a> the "
        "snapshot taken just before the change is filed, with <strong>Restore (review first)</strong> if "
        "you ever want the deck back as it was.</span></li></ol>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    tut_first_sim = (
        "<p>The solo test tells you how the deck plays on its own: how fast the commander lands, how "
        "often the mana flows, how much damage it would deal to an opponent who does nothing. A deck "
        "without a commander "
        "and with 60 or more cards plays a 60-card game instead (20 life, on the play).</p>"
        "<ol class='steps'>"
        "<li><span>Open one of your decks and press <strong>Run full test</strong>. The button says the "
        "solo test takes up to a minute and shows “Testing…” while it does.</span></li>"
        "<li><span>The report opens: the deck, its commander, the games run and when; the headline numbers "
        "as tiles with their 95% intervals (the turn the commander lands, the chance of 40 damage or a "
        "lethal board by the last turn, mulligans, land drops); four small charts (milestones reached by "
        "turn, the turn the commander was cast, mana available and damage per turn); in plain words what "
        "the solo test could not model, with each group's share of the deck; and the validation verdict "
        "as a badge with the problems listed. The raw output stays, folded away at the end.</span></li>"
        "<li><span>Keep it: the buttons download the report as <strong>Markdown</strong> or as a "
        "self-contained <strong>HTML page</strong> (light and dark, no scripts) and <strong>Copy as "
        "Markdown</strong> puts it on the clipboard.</span></li>"
        "<li><span>Find it again under <a href='/history'>History</a> (the Reports chip). A report "
        "reused because the deck had not changed says so.</span></li></ol>"
        "<p>It is the same run the assistant makes for “goldfish this deck”: the statistics, the "
        "validation and the solo test (300 games), so the numbers match whichever way it is "
        "started.</p>"
    )

    # -- How-to guides ----------------------------------------------------------------------------
    how_new_deck = (
        "<p><a href='/decks'>Decks</a> lists the decks of your linked Archidekt account with their cover "
        "art. Filter by name or folder, switch between grid and list, and sort by last updated, created, "
        "name or format.</p><ul>"
        "<li><strong>New deck</strong> creates one from a name, a pasted list, a CSV export, the gateway's "
        "own .json export, a file you choose (.txt, .csv or .json) or a scan.</li>"
        "<li>From a public deck, <strong>Clone</strong> copies it into your own account.</li>"
        "<li>From the Scan page, <strong>start a new deck</strong> from the cards you scanned.</li>"
        "<li>Ask your assistant: “build a new deck from this list” comes back as a proposal.</li></ul>"
    )
    how_add_cards = (
        f"{_h4('how-add-editor', 'In the editor')}"
        "<p>Change quantities, categories (type a new one to create it), foil or printing, add cards with "
        "autocompletion (to the deck or the maybeboard), edit maybeboard and sideboard rows, paste a whole "
        "list, remove cards, and undo. The <strong>Add a card</strong> bar is one search box: chips pick "
        "where a card goes (Auto, a category, the maybeboard), <kbd>Enter</kbd> adds the highlighted card "
        "and keeps the cursor in the box, “3 sol ring” adds three, and on wide screens the printings of "
        "the highlighted card show as pictures beside the list, the set used last on this deck "
        "pre-selected. Enter pressed while the list is still catching up takes the first answer when it "
        "arrives. <strong>Save changes</strong> sends the whole session to Archidekt in one go, after a "
        f"snapshot of the deck as it was; only a big removal (many cards at once) asks you to confirm "
        f"first. {backup_box}</p>"
        f"{_h4('how-add-page', 'Straight from the deck page')}"
        "<ul>"
        "<li>On your own deck a right-click on a card (a press and hold on a phone, <kbd>Shift</kbd>+"
        "<kbd>F10</kbd> or the Menu key on a focused card) opens the site's own card menu: open the card, "
        "one more or one fewer copy, move it to a category or the maybeboard, remove it, or jump to the "
        "editor. The card viewer has the same quantity buttons and Remove. Each change is saved at once as "
        "one proposal with its snapshot (the same path as the editor, with the same “are you sure” for a "
        "big removal), and the page redraws in place with a toast offering <strong>Undo</strong>. These "
        "quick edits keep the gateway snapshot only, without the backup copy on Archidekt. On someone "
        "else's deck the menu offers Open card and Open on Scryfall.</li>"
        "<li><strong>Drag a card</strong> onto another category: with a mouse, or press and hold on a "
        "touch screen and slide. The move is saved to Archidekt when you let go, like a change from the "
        "menu.</li>"
        "<li><strong>Undo</strong> and <strong>Redo</strong> (beside Select cards, and the toast's Undo) "
        "take back or redo any change saved on the page, with the editor's keys: Ctrl+Z, and Ctrl+Shift+Z "
        "or Ctrl+Y. Removing a card that has notes asks first: Archidekt gives the gateway no way to "
        "write notes, so they do not come back. Your own stack order is kept with your account.</li>"
        "<li><strong>Quick add</strong> types a card name and takes you to the editor with it filled "
        "in.</li></ul>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    how_organise = (
        "<ul>"
        "<li>The <strong>More</strong> menu in a deck's banner holds Deck settings, Export (text, JSON or "
        "CSV), History, Compare, Deck stats, the deck's page on Archidekt and <strong>Delete deck</strong>, "
        f"which asks you to type the deck's name and keeps {backup_delete} first.</li>"
        "<li><strong>Deck settings</strong> changes the name, format, bracket, description and whether the "
        "deck is private or unlisted, and also sets the deck's <strong>cover image</strong>, its "
        "<strong>tags</strong> and the <strong>folder</strong> it sits in.</li>"
        "<li><strong>+ New folder</strong> on My decks makes a folder; a folder tile's menu (its ⋮, a "
        "right-click, or press and hold on a phone) renames, moves or deletes it.</li>"
        "<li>Your assistant can propose moving a deck to another folder, changing its tags or its cover "
        "(a settings change you approve, like any other). It can also propose deleting a deck or "
        "creating a folder, but only your own press applies either one (a deletion on its review page "
        "only). Renaming folders has no assistant tool.</li></ul>"
    )
    how_approve = (
        "<p>Your own saves in the app go to Archidekt at once: pressing Save is your approval. An "
        "<strong>assistant's</strong> edit, new deck, clone, settings change, restore or collection change "
        "is first saved as a <strong>proposal</strong>: the exact list of what would change.</p>"
        "<ol class='steps'>"
        "<li><span>Approve on the card the assistant shows in the chat (Reject can carry a reason), or "
        "open <a href='/proposals'>Proposals</a>, which lists what is waiting by deck, oldest first. "
        "Open one to read the diff and press <strong>Apply</strong> or <strong>Reject</strong>, or check "
        "several and press <strong>Apply</strong>: the gateway shows the order first, then applies them "
        "one deck at a time. A deck's own page shows its waiting changes at the top, and asking the "
        "assistant what is waiting shows them as a card with <strong>Apply all</strong>.</span></li>"
        "<li><span>Before the edit is applied, the gateway checks it still fits the deck, saves a "
        + (
            f"snapshot and puts a backup copy of the deck in a “{folder}” folder on your Archidekt account"
            if archidekt_backups
            else "snapshot"
        )
        + ", then reads the deck back to confirm.</span></li>"
        "<li><span>After an apply, <strong>Undo</strong> puts the deck back as it was just before; it "
        "says first if it would also take back later changes.</span></li>"
        "<li><span>Your own saves appear under Proposals too, already applied, so every change has a "
        "record.</span></li></ol>"
        "<p><strong>When the deck changes first:</strong> Archidekt is the master copy of your decks. When "
        "you open the app, and regularly while proposals are waiting (every 30 minutes unless this "
        "gateway is set otherwise), the gateway reads your decks on Archidekt; it only reads. If a deck "
        "changed after a proposal for it was made, on archidekt.com, in another app or by another "
        "change applied here, the gateway works the proposal out again on the deck as it is now. When "
        "it still makes exactly the same change it shows <strong>Updated</strong> and can be applied as "
        "before; when it no longer fits (a card it removes is gone, a count moved) it shows "
        "<strong>Needs a look</strong> with the reason, and Apply is refused with nothing sent until the "
        "assistant changes it or you reject it. A change that depends on one that was rejected, withdrawn, "
        "failed or expired shows <strong>Held</strong>, and so does one that would replace a change "
        "already applied. The check never changes a deck, a snapshot or your undo "
        "history.</p>"
        "<p>On your Account page you can pick an approval mode: Manual, shown as "
        f"<strong>{_esc(modes.MODE_LABELS['manual'])}</strong> (every change asks you), Semi-auto, shown "
        f"as <strong>{_esc(modes.MODE_LABELS['semi'])}</strong> (the assistant applies low-risk changes "
        "itself, such as a small edit or a private copy of a deck, and asks about the rest) or Full auto, "
        f"shown as <strong>{_esc(modes.MODE_LABELS['auto'])}</strong> (it "
        "also applies bigger deck and collection edits and changes to a deck's details, except a "
        f"restore, a commander change, a new deck, an edit of more than {int(big_rows)} rows, making a "
        "deck public or unlisted, and any edit of a description that has formatting, such as bold text, "
        "links or pictures). "
        "Actions on your Archidekt account (likes, follows, comments and the like) and deleting a deck "
        "always wait for your press, and so does a held change, a change waiting for another change "
        "first, one another app had a hand in, one that replaces another app's change, or one that "
        "another app's waiting change would replace. "
        "When a change waits for your press, its card and "
        "review page say why. "
        "Every deck change keeps a snapshot either way; <a href='#ref-approval'>Approval modes and risk "
        "levels</a> has the exact rules.</p>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    how_restore = (
        "<ol class='steps'>"
        "<li><span>Open <a href='/history'>History</a>: snapshots, restores and deck reports, newest "
        "first and grouped by day (or by deck, with <strong>Group by</strong>). The chips pick "
        "Everything, Snapshots, Restores or Reports; the search finds a deck by name, and the filters "
        "narrow by deck and when "
        "(today, the last 7, 30 or 90 days or this year, counted in UTC days), and <strong>Older</strong> "
        "pages through the rest 25 at a time. Changes your assistants proposed, applied or not, are on "
        "<a href='/proposals?show=all'>Proposals</a>.</span></li>"
        "<li><span>A snapshot row links to the change it was taken before and has <strong>Restore (review "
        "first)</strong> (on a phone, under <strong>Details</strong>). Press it to see what would "
        "change.</span></li>"
        "<li><span>Confirm. <strong>Restore</strong> puts the deck back exactly as the snapshot had it, "
        "cards and the deck's own name, description, format, bracket and privacy alike.</span></li></ol>"
        "<p>Or ask your assistant to “undo yesterday's change”: the restore comes to you as a proposal, "
        "and a restore always waits for your press, whatever approval mode you chose.</p>"
    )
    how_compare = (
        "<p><strong>Compare with another deck…</strong> (More menu on a deck) pits the deck against a "
        "preconstructed deck from Archidekt's list, any deck link or a pasted list: cards taken out, "
        "put in, changed counts and the statistics' differences. The precon box suggests as you type. "
        "<a href='/precons'>Precons</a> lists every preconstructed deck Archidekt knows, by set.</p>"
        "<p>The assistant's compare_decks makes the same comparison (“what does this deck run that mine "
        "doesn't?”), and can add a paired goldfish A/B.</p>"
    )
    how_export = (
        f"{_h4('how-export-deck', 'A deck')}"
        "<p><strong>Export</strong> (More menu) gives the deck as Archidekt import text with every row's "
        "printing, finish, categories and labels (paste it into Archidekt's Import dialog or the New deck "
        "page), as a plain decklist and as the sideboard list, each with a Copy button, plus downloads as "
        "Archidekt .txt, plain .txt, .csv and .json (each imports back here or into Archidekt) and Arena "
        ".txt, MTGO .dek and PDF (one-way); the app saves downloads to your Downloads folder. "
        "<strong>New deck</strong> on the Decks page imports a pasted list, a CSV export, the gateway's "
        ".json or a chosen file.</p>"
        f"{_h4('how-export-collection', 'The collection')}"
        "<p><strong>Export CSV</strong> downloads the whole collection in the column layout Archidekt's "
        "own import reads. <strong>Import a list</strong> adds cards from that CSV, from a CSV with "
        "Archidekt's column names or from a plain card list, pasted or chosen as a file, up to 100 rows "
        "at a time.</p>"
        f"{_h4('how-export-report', 'A report')}"
        "<p>A deck report downloads as Markdown or as a self-contained HTML page, and <strong>Copy as "
        "Markdown</strong> puts it on the clipboard. <a href='#ref-formats'>Export formats</a> lists "
        "every format in one place.</p>"
    )
    how_search = (
        "<p>The search box at the top of every page finds cards and decks at once. Type part of a name: "
        "the <strong>Cards</strong> group lists matching cards with their cost and type (pick one to open "
        "it on the <a href='/cards'>Cards</a> page, where you can read it large and <strong>add it to any "
        "of your decks</strong>, choosing the copies and category; the add is saved like an edit on the "
        "deck page, with its snapshot), and the <strong>Decks</strong> group offers the deck search for "
        "what you typed and, when a legendary creature matches, “Decks with commander …”. Enter on plain "
        "text searches decks. On a phone the box is behind the magnifier at the top.</p>"
        "<p><a href='/search'>Search</a> finds public decks on Archidekt the way the Archidekt site does: by "
        "deck name, commander, format, colors or the person who built it, ordered by newest, most viewed "
        "or largest. Open any result to read it with the same views as your own decks; the owner's name "
        "opens their profile with every public deck they have. Archidekt matches a commander by its full "
        "name only; type part of one (“Krenko”) and the search looks it up for you: one match shows "
        "“Showing decks led by …”, several give a “Did you mean” list to pick from.</p>"
        "<p>From a public deck you can run the statistics, export it, like, bookmark or comment on it "
        "(your own comments can be edited and deleted), follow its owner, or clone it into your own "
        "account. <a href='/precons'>Precons</a> lists every preconstructed deck Archidekt knows, by set.</p>"
    )
    how_scan = (
        "<p><a href='/scan'>Scan</a> turns physical cards into a list with your phone camera. Text "
        "recognition runs on the phone; the card is then matched on Scryfall through the gateway.</p>"
        "<ol class='steps'>"
        "<li><span><strong>Camera.</strong> Hold a card in the frame with its title in the dashed box and "
        "tap the shutter, or switch on <strong>Auto</strong> and show cards one after another: sure "
        "matches are added on their own (with Undo), doubtful ones wait for your tap.</span></li>"
        "<li><span><strong>Check the list.</strong> Fix a wrong match from the suggestions, pick the exact "
        "printing or foil, adjust quantities. Cards the camera could not read are kept with a picture of "
        "the title so you can type them.</span></li>"
        "<li><span><strong>Choose what to do with it:</strong> save the cards to your "
        "<a href='/collection'>collection</a>, add them to one of your decks, start a new deck from them, "
        "or save the scan for later (your assistant can pick it up too).</span></li></ol>"
        "<p>You can also type or paste names instead of using the camera, and earlier scans are listed "
        "under <strong>Sessions</strong>. On the Android app the <strong>Scan with the app's camera</strong> "
        "button in the account menu uses the app's own camera panel.</p>"
        "<p>A scan is a draft that stays here as long as you like, up to a whole deck: fix misread cards, "
        "printings and quantities first, then send it to your collection or a deck, which removes the "
        "draft. Nothing expires; you delete what you do not want.</p>"
    )
    how_collection = (
        "<p><a href='/collection'>Collection</a> is the list of cards you own. It is your Collection on "
        "Archidekt, shown and edited here through the account you linked: nothing about your cards is "
        "stored on this gateway, and what you add here appears on archidekt.com at once. Scan a pile and "
        "save it, or add cards by name; each card keeps its printing, finish, condition and count.</p><ul>"
        "<li>Filter by name, show it as a grid or a list, sort by newest or by set release, and page "
        "through it. Plus and minus change the count; the cross removes a card; the dots open its details "
        "(finish, condition, language, price paid), saved to Archidekt on Save.</li>"
        "<li>Cards you own show a <strong>green dot</strong> on every deck page, your own decks and public "
        "ones alike, with the number of copies Archidekt knows about.</li>"
        "<li><strong>Export CSV</strong> and <strong>Import a list</strong> are described under "
        "<a href='#how-export'>Export and import</a>.</li></ul>"
        "<p class='tip'>Your assistant can read the collection too, for questions like “which cards in "
        "this deck do I not own yet?”, and propose additions or removals that you approve like a deck "
        "edit. It can also propose a like, follow or comment, but each one waits for your own press on "
        "its review page, whatever your approval mode.</p>"
    )
    how_playtest = (
        "<p><strong>Playtest</strong> in a deck's banner opens the deck in Archidekt's own playtester "
        "(opening hand, mulligans, turns played by hand) in a new tab, or in the phone's browser from the "
        "app. A private deck shows there when that browser is signed in to Archidekt, so sign in on "
        "archidekt.com once in the browser you use. <a href='#exp-playtest'>Why Playtest opens Archidekt "
        "in a new tab</a> explains.</p>"
        "<p><strong>Goldfish</strong>, next to it, plays the deck by hand here on the gateway: seven cards "
        "with London mulligans, then draw, play, tap and next turn, all in your browser. A Commander deck "
        "starts with its commander in the command zone.</p>"
        "<p><strong>Play on EDHPlay</strong> takes the deck to EDHPlay, the free online table for "
        "Commander. A short box asks first; its <strong>Copy and Open EDHPlay.com</strong> copies the "
        "deck in EDHPlay's import format and opens EDHPlay. The import steps show in that box the "
        "first time and stay folded under <strong>How to import</strong> after that. The list has "
        "the commander first, then the rest of the deck with "
        "each card's printing, then a Sideboard section with your companion and sideboard. EDHPlay's "
        "sideboard holds 15 cards, so anything past that is left out and a note names it; your "
        "maybeboard is not sent, and EDHPlay does not take tokens from a list, so add those there by "
        "hand. On EDHPlay go to <strong>My Decks</strong>, choose <strong>Add Deck</strong> and open the "
        "new deck, then press <strong>Import</strong>: paste the list and press <strong>Import &amp; "
        "Replace</strong>. EDHPlay cannot receive a deck by link, "
        "so after a change on Archidekt press the button again.</p>"
    )
    how_layout = (
        "<ul>"
        "<li><strong>Light, Dark or System theme</strong> is in the account menu (your picture at the top "
        "right) and applies in the app too.</li>"
        "<li><strong>Desktop layout</strong>, in the same menu and remembered per device, shows the "
        "computer layout on a phone or in the app, like a browser's Desktop site switch; <strong>Fit the "
        "screen</strong> goes back to the adaptive layout.</li>"
        "<li>No Android phone? Add this site to your home screen from the browser (Chrome and Samsung "
        "Internet on Android, Safari on iPhone: Share, then Add to Home Screen) and it opens like an app, "
        "scanning included.</li></ul>"
    )
    how_app = (
        "<p>The <a href='/install?for=android'>Android app</a> shows these same pages as a "
        "native-feeling app with the phone "
        "camera built in. Sign in once, then it opens on your home page. The app's own actions (Scan with "
        "the app's camera, Reload, Open in browser, Change gateway) sit in the account menu at the top "
        "right. Links to Archidekt or Scryfall open in your browser; gateway links shared from other apps "
        "open in the app.</p>"
        "<p>On a foldable, the folded screen has the tabs at the bottom and the unfolded screen moves them "
        "to a rail down the left edge, like other Android apps; the layout follows the window, so split "
        "screen and pop-up windows work too. While scanning, half-folding the phone puts the camera on "
        "the top half and the list on the bottom.</p>"
    )
    how_signout = (
        "<p>The <a href='/account'>Account</a> page shows who you are signed in as and has two buttons: "
        "<strong>Sign out</strong> ends this device's session, <strong>Sign out on all my devices</strong> "
        "ends every browser and Android app session. Both act on the first press (the button says "
        "“Signing out…” and locks at once). The account menu's Sign out does the same for this "
        "device.</p><ul>"
        "<li>Connected assistants keep working until you disconnect them under <strong>Connected "
        "apps</strong> on the same page; disconnecting one rejects its pending proposals.</li>"
        "<li><strong>Unlink Archidekt</strong> removes the stored Archidekt session at once.</li>"
        "<li><strong>Delete my data</strong> removes everything this gateway keeps for your account "
        "(proposals, snapshots, reports, scan sessions, your Archidekt link, connected apps and your "
        "sign-in); your decks on Archidekt are not touched.</li></ul>"
    )

    # -- Reference --------------------------------------------------------------------------------
    cap_rows = [
        (
            "Who is signed in; is Archidekt linked, are writes on",
            "<code>whoami</code>, <code>account_status</code>",
            "no",
            "linking itself happens only in the browser (Account page)",
        ),
        (
            "List my decks (private included), filter by name, format, folder",
            "<code>list_my_decks</code>",
            "no",
            "",
        ),
        (
            "Search public decks (name, commander, partial commander names, format, colors, owner), and "
            "one user's public decks (<code>owner</code>, newest first)",
            "<code>search_decks</code>",
            "no",
            "hides Mystic Forge <code>archidekt_user_decks</code>; the profile page /users/&lt;name&gt; "
            "is the hand path",
        ),
        (
            "Read any deck, your own private ones included: cards (type line, power/toughness, loyalty), "
            "zones, stats, plain list, Archidekt import text, picked with <code>view</code>; rules text, "
            "faces, flavour and artist on request",
            "<code>get_deck</code>",
            "no",
            "hides Mystic Forge <code>archidekt_deck</code>, <code>archidekt_export</code>; "
            "<code>owner</code> says whose deck it is",
        ),
        (
            "Read a pasted list or an Archidekt CSV export",
            "<code>parse_decklist</code>, <code>parse_deck_export</code>",
            "no",
            "",
        ),
        (
            "Statistics, legality, structural checks, bracket estimate",
            "<code>deck_stats</code>",
            "no",
            "hides <code>validate_archidekt_deck</code>",
        ),
        (
            "Diff two decks, snapshots or lists (precon upgrades included), with an optional paired "
            "goldfish A/B",
            "<code>compare_decks</code>",
            "no",
            "hides <code>precon_diff</code>, <code>goldfish_ab</code>; the deck page's Compare view is "
            "the hand path",
        ),
        (
            "Solo test (300 games, no opponents) of one deck or pasted list, stored with its stats and "
            "validation (a pasted list is returned, not stored)",
            "<code>run_deck_report</code> (+ <code>list_deck_reports</code>, <code>get_deck_report</code>, "
            "<code>remove_card_model</code>)",
            "stores a report",
            "hides <code>goldfish_run</code>; takes the simulator's options; the deck page's Run "
            "full test is the hand path",
        ),
        (
            "Play more Forge games for a report whose games have finished, against the same "
            "opponents (on a comparison, both decks get the same number)",
            "<code>play_more_games</code>",
            "starts Forge games",
            "the report page's Play more games button is the hand path, with the same rules: up to 50 "
            "games a report, and nothing starts when the member's Forge queue is full",
        ),
        (
            "Draw odds; what the engine models for a deck",
            "<code>goldfish_odds</code>, <code>goldfish_annotate</code> (Mystic Forge)",
            "no",
            "annotations feed <code>run_deck_report</code>; the deck page's Probability of draw is the "
            "hand path to the same odds",
        ),
        (
            "Validate a pasted list that is not a deck yet",
            "<code>validate_decklist</code> (Mystic Forge)",
            "no",
            "an Archidekt deck's checks are in <code>deck_stats</code>",
        ),
        (
            "Card lookups, rulings, prices, rules, EDHREC, combos, precon lists",
            "the Mystic Forge research tools",
            "no",
            "the full list is in the assistant skill's tool reference",
        ),
        (
            "Turn names or card photos into exact printings",
            "<code>resolve_cards</code>, <code>card_printings</code>",
            "no",
            "the picker card lets you keep, drop or pick among suggestions; the printings card shows "
            "every printing's picture to tap (a form question in an app without cards)",
        ),
        (
            "Scan sessions (the Scan page's drafts)",
            "<code>list_scan_sessions</code>, <code>get_scan_session</code>, <code>save_scan_session</code>",
            "saves a draft",
            "drafts stay in the gateway until used",
        ),
        ("Edit a deck's cards (main or maybeboard)", "<code>propose_deck_changes</code>", "proposal", ""),
        (
            "Create a deck from cards, a list, a CSV, the gateway's JSON or a scan",
            "<code>propose_new_deck</code>",
            "proposal (high)",
            "",
        ),
        ("Clone a deck", "<code>propose_clone_deck</code>", "proposal (low)", ""),
        (
            "Change a deck's name, description, format, bracket, privacy; move it to a folder, add or "
            "remove tags, set its cover",
            "<code>propose_deck_details</code>",
            "proposal (high)",
            "applied the same way as on the deck settings page",
        ),
        (
            "Undo: snapshots and restore (cards and the deck's name, description, format, bracket, privacy)",
            "<code>list_snapshots</code>, <code>get_snapshot</code>, <code>propose_restore_snapshot</code>",
            "proposal (high)",
            "",
        ),
        (
            "My collection: read; add or remove cards",
            "<code>list_collection</code>, <code>propose_collection_changes</code>",
            "proposal",
            "in-chat card like a deck proposal; read back after applying",
        ),
        (
            "Read a deck's comments, and the collaborators of your own deck",
            "<code>get_deck_comments</code>, <code>get_deck_collaborators</code>",
            "no",
            "",
        ),
        (
            "Act on your Archidekt account: like, vote, bookmark, follow the owner; post, edit, delete or "
            "vote on comments; a new folder; add or remove a collaborator; delete a deck",
            "<code>propose_deck_social</code>, <code>propose_comment</code>, "
            "<code>propose_create_folder</code>, <code>propose_collaborator</code>, "
            "<code>propose_delete_deck</code>",
            "proposal (your press)",
            "only you apply these, on the review page, in every approval mode",
        ),
        (
            "A plain summary of the last day or week",
            "<code>get_summary</code>",
            "no",
            "fixed sentences from the gateway's own data, with or without notifications",
        ),
        (
            "Proposals: list, read, approve with the card's code, reject (with a reason on the card), "
            "apply; show the waiting ones as a card with Apply all; tidy them (amend, withdraw, combine); "
            "check a planned deck edit first, with no card",
            "<code>list_my_proposals</code>, <code>get_proposal</code>, <code>confirm_proposal</code>, "
            "<code>reject_proposal</code>, <code>apply_proposal</code>, <code>show_waiting_changes</code>, "
            "<code>confirm_waiting</code> and <code>get_apply_result</code> (the card's buttons), "
            "<code>amend_proposal</code>, <code>withdraw_proposal</code>, <code>combine_proposals</code>, "
            "<code>check_deck_changes</code>",
            "apply writes",
            "<code>apply_proposal</code> succeeds only when the mode allows; one that would now do "
            "something other than what was reviewed is not sent and waits as Needs a look",
        ),
    ]
    if watchlists:
        cap_rows.append(
            (
                "Watch card prices and deck values",
                "<code>watch_card</code>, <code>watch_deck</code>, <code>unwatch</code>, "
                "<code>list_watchlist</code>, <code>card_price_history</code>",
                "the gateway's own data",
                "changes nothing on Archidekt, so no proposal; the Watchlist page is the hand path",
            )
        )
    ref_assistant = (
        "<p>The assistant acts as the signed-in member. Reads need nothing more than the connection; "
        "<strong>every write is a proposal</strong>, and who applies it is decided by your approval mode "
        "on the Account page, never by the assistant. Every job has exactly one tool, so the assistant "
        "never guesses between two ways of doing the same thing: where the research service (Mystic "
        "Forge) has a tool that does what a gateway tool does, the research one is hidden and an "
        "assistant that calls it is told which tool owns the job.</p>"
        "<div class='tbl'><table><thead><tr><th>Capability</th><th>The one tool</th><th>Writes?</th>"
        "<th>Notes</th></tr></thead><tbody>"
        + "".join(f"<tr><td>{c}</td><td>{t}</td><td>{w}</td><td>{n}</td></tr>" for c, t, w, n in cap_rows)
        + "</tbody></table></div>"
        f"<p><strong>Your own approval, every time:</strong> {_esc(modes.MEMBER_ONLY_TEXT)} An "
        "assistant can propose them, and no setting lets an assistant apply them. "
        "<strong>No assistant tool exists for:</strong> renaming "
        "folders, linking or unlinking Archidekt, approval modes, admin.</p>"
        "<p><strong>Only the assistant does:</strong> research lookups (Scryfall, EDHREC, combos, rules), "
        "the paired goldfish A/B with the simulator's options, reading card photos into names, proposing "
        "changes it worked out itself. <strong>Both reach:</strong> reading decks and collections, "
        "statistics, creating and editing decks, deck settings (folder, tags and cover included), "
        "cloning, restoring snapshots, collection changes, scan drafts; your path applies at once, the "
        "assistant's is a proposal under your mode. The solo test of one deck and the "
        "comparison of two are both paths: Run full test and Compare on the deck page for people, "
        "<code>run_deck_report</code> and <code>compare_decks</code> for the assistant, same engine and "
        "settings.</p>"
    )
    page_rows = [
        (
            "Home",
            "<code>/</code>",
            "newest decks with covers, search, tiles to every section, Connect an AI assistant",
        ),
        (
            "Decks",
            "<code>/decks</code>",
            "the linked account's decks (grid or list, filter, sort), New deck (<code>/decks/new</code>), "
            "Folders",
        ),
        (
            "One deck",
            "<code>/decks/&lt;id&gt;</code>",
            "views, grouping, sorting, filter, the whole card on tap, the card menu, drag to move, Undo and "
            "Redo, "
            "Quick add, Playtest, Goldfish, Play on EDHPlay, Run full test, statistics with Probability "
            "of draw and Deck "
            "checks, "
            "description, comments, like, bookmark, follow; More: Settings, Compare, Export, History, "
            "Open on Archidekt, Delete deck",
        ),
        (
            "Editor",
            "<code>/decks/&lt;id&gt;/edit</code>",
            "quantities, categories, finish, printing, add cards, maybeboard and sideboard rows, paste a "
            "list, remove, undo, Save changes with the backup tick box",
        ),
        (
            "Deck settings",
            "<code>/decks/&lt;id&gt;/settings</code>",
            "name, format, bracket, description, private, unlisted; cover image; tags; folder; Delete",
        ),
        (
            "Compare",
            "<code>/decks/&lt;id&gt;/compare</code>",
            "a precon, any deck or a pasted list against this deck",
        ),
        ("Export", "<code>/decks/&lt;id&gt;/export</code>", "import text, plain list, sideboard, downloads"),
        (
            "Goldfish",
            "<code>/decks/&lt;id&gt;/goldfish</code>",
            "play the deck by hand in your browser: opening hand, mulligans, draw, play, next turn",
        ),
        (
            "Folders",
            "<code>/decks</code>",
            "your Archidekt folders on My decks: a new folder, rename, move or delete one",
        ),
        ("Search", "<code>/search</code>", "public decks by name, commander, format, colors, owner"),
        ("Cards", "<code>/cards</code>", "cards by name, with picture, cost and type; add one to a deck"),
        (
            "A member of Archidekt",
            "<code>/users/&lt;name&gt;</code>",
            "their profile and every public deck they have",
        ),
        ("Precons", "<code>/precons</code>", "every preconstructed deck Archidekt knows, by set"),
        (
            "Scan",
            "<code>/scan</code>",
            "camera or photos to a draft; Sessions; save to a deck or the collection",
        ),
        (
            "Collection",
            "<code>/collection</code>",
            "the Archidekt Collection: filter, sort, grid or list, add, quantity, details, remove, CSV "
            "export and import",
        ),
        (
            "Proposals",
            "<code>/proposals</code>",
            "what waits for you, grouped by deck, to apply or reject one at a time or several at once; "
            "every change proposed, newest first, under All",
        ),
        (
            "History",
            "<code>/history</code>",
            "snapshots (Restore), restores and reports, newest first, with filters; a report at "
            "<code>/history/reports/&lt;id&gt;</code> with its Markdown and HTML exports",
        ),
        ("My activity", "<code>/activity</code>", "what your account and its connected apps did"),
    ]
    if watchlists:
        page_rows.append(
            ("Watchlist", "<code>/watchlist</code>", "the card prices and deck values you watch, once a day")
        )
    page_rows += [
        (
            "Account",
            "<code>/account</code>",
            "link or unlink Archidekt, approval mode, Connected apps, theme, sign out (two buttons), "
            "Delete my data",
        ),
        (
            "Assistant skill",
            "<code>/install?for=skill</code>",
            "the house rules to install in your assistant",
        ),
        ("Android app", "<code>/install?for=android</code>", "download and set-up"),
        ("Guide", "<code>/guide</code>", "this page"),
        ("Admin (admin group only)", "<code>/admin</code>", "members, activity, metrics, the System card"),
    ]
    ref_pages = (
        "<p>Every page needs the same sign-in. Addresses are relative to this gateway.</p>"
        "<div class='tbl'><table><thead><tr><th>Page</th><th>Address</th><th>What is there</th></tr></thead>"
        "<tbody>"
        + "".join(f"<tr><td>{_esc(p)}</td><td>{a}</td><td>{w}</td></tr>" for p, a, w in page_rows)
        + "</tbody></table></div>"
        "<p>On a phone the tabs at the bottom are Home, Decks, Scan, Collection and More (Proposals, "
        "History, Reports, Watchlist when it is on, Guide, Account and Admin); Search is the magnifier "
        "at the top. From 600 px on a touch screen every page is a tab on a rail down the left edge, "
        "with no More; a wider browser has the links in the top bar, Home first.</p>"
    )
    # in every mode; since 0.7.29 the card in the chat too, but never for a deck deletion
    on_review_page = (
        "<td>you press Approve on the card or Apply on its review page (deleting a deck, and an edit of a "
        "comment that has formatting: on its review page only)</td>"
    )
    ref_approval = (
        "<p>Your approval mode on the <a href='/account'>Account</a> page says how much your assistant "
        "may change on Archidekt without asking you first. It is your own setting and applies to nobody "
        "else; the gateway may cap the choices.</p>"
        "<div class='tbl'><table><thead><tr><th>Mode</th><th>Low-risk proposal</th><th>High-risk proposal"
        "</th><th>Account action or deck deletion</th></tr></thead><tbody>"
        f"<tr><td><strong>Manual</strong> (default): {_esc(modes.MODE_LABELS['manual'])}</td>"
        "<td>you press Approve or Apply</td><td>you press</td>"
        f"{on_review_page}</tr>"
        f"<tr><td><strong>Semi-auto</strong>: {_esc(modes.MODE_LABELS['semi'])}</td>"
        "<td>the assistant applies it</td><td>you press</td>"
        f"{on_review_page}</tr>"
        f"<tr><td><strong>Full auto</strong>: {_esc(modes.MODE_LABELS['auto'])}</td>"
        "<td>the assistant applies it</td><td>the assistant applies it, except a restore, a commander "
        f"change, a new deck, an edit of more than {big_rows} rows, making a deck public or unlisted, "
        "and any edit of a description that has formatting, such as bold text, links or pictures: you "
        "press</td>"
        f"{on_review_page}</tr>"
        "</tbody></table></div>"
        f"<p><strong>Low risk:</strong> {_esc(modes.low_risk_text(max_rows))} "
        f"<strong>High risk:</strong> {_esc(modes.HIGH_RISK_TEXT)} "
        f"<strong>Always your own press:</strong> {_esc(modes.MEMBER_ONLY_TEXT)} "
        "<strong>In every mode:</strong> a held change, a change waiting for another change first, one "
        "another app had a hand in, one that replaces another app's change, or one that another app's "
        "waiting change would replace waits for your press; its card and review page say why. "
        "A change to one of your decks keeps a snapshot first, and deck and collection changes are read "
        "back to check them.</p>"
        f"<p class='tip'>{_esc(modes.AUTO_WARNING)}</p>"
        f"{_h4('ref-approval-hand', 'Your own saves')}"
        "<p>A hand save needs no approval: pressing Save is it. It still keeps a snapshot, and asks "
        "“are you sure” only for a restore, a commander change, or a removal of "
        f"{modes.HAND_EDIT_CONFIRM_ROWS} or more different cards or more than "
        f"{modes.HAND_EDIT_CONFIRM_COPIES} copies in one go; deleting a deck asks for its name, and "
        "adding or removing a collaborator or creating a folder asks once more before it is sent.</p>"
    )
    key_rows = [
        ("Deck page", "<kbd>Tab</kbd>", "moves through the cards; a focused card has an outline"),
        ("Deck page", "<kbd>Enter</kbd> or <kbd>Space</kbd>", "opens the focused card in the viewer"),
        (
            "Deck page",
            "<kbd>Shift</kbd>+<kbd>F10</kbd> or the Menu key",
            "opens the card menu for the focused card (your own deck: quantity, move, remove, editor; "
            "another's: Open card, Open on Scryfall)",
        ),
        ("Card menu", "<kbd>↑</kbd> <kbd>↓</kbd>, <kbd>Enter</kbd>, <kbd>Esc</kbd>", "move, choose, close"),
        (
            "Card viewer",
            "<kbd>Esc</kbd>",
            "closes it (so does the phone's Back button); focus stays inside while it is open",
        ),
        (
            "Any card-name box",
            "<kbd>↓</kbd> <kbd>↑</kbd>, <kbd>Enter</kbd>, <kbd>Esc</kbd>, <kbd>Tab</kbd>",
            "move through the suggestions, pick one, close the list, leave the box",
        ),
        (
            "Editor, Add a card",
            "<kbd>Enter</kbd>",
            "adds the highlighted card (or the first answer when the list is still catching up) and keeps "
            "the cursor in the box",
        ),
        (
            "Any dropdown",
            "<kbd>Enter</kbd>, <kbd>Space</kbd> or <kbd>↓</kbd>; then <kbd>↑</kbd> <kbd>↓</kbd>, letters, "
            "<kbd>Enter</kbd>, <kbd>Esc</kbd>",
            "opens the site's own list; move, jump to a choice by its first letters, pick, close",
        ),
        ("Guide", "<kbd>Esc</kbd> in the search box", "clears the search and shows the whole guide again"),
        ("Menus and dialogs", "<kbd>Esc</kbd>", "closes the open menu, sheet or dialog"),
    ]
    ref_keys = (
        "<div class='tbl'><table><thead><tr><th>Where</th><th>Keys</th><th>What happens</th></tr></thead>"
        "<tbody>"
        + "".join(f"<tr><td>{_esc(w)}</td><td>{k}</td><td>{d}</td></tr>" for w, k, d in key_rows)
        + "</tbody></table></div>"
        "<p>With a mouse, a right-click on a card opens the same card menu; on a touch screen, press and "
        "hold.</p>"
    )
    ref_syntax = (
        f"{_h4('ref-syntax-bar', 'The search bar')}"
        "<ul>"
        "<li><code>sol ring</code>: suggestions as you type; <kbd>Enter</kbd> adds the highlighted one.</li>"
        "<li><code>3 sol ring</code> or <code>3x sol ring</code>: adds three copies (up to 99).</li>"
        "<li>The <strong>chips</strong> above the box choose where the card goes: <strong>Auto</strong> "
        "(the category follows the card's type), one of the deck's categories, or the maybeboard. The "
        "chosen chip stays on for the next card.</li>"
        "<li>On a wide screen the <strong>printings</strong> of the highlighted card show as pictures "
        "beside the list, the set used last on this deck pre-selected; a click adds that printing.</li>"
        "</ul>"
        f"{_h4('ref-syntax-list', 'A pasted list')}"
        "<p>The editor's paste box, New deck and the assistant read the shapes people paste from "
        "Archidekt, Moxfield, MTGO and Arena: one card per line as <code>1 Sol Ring</code> or "
        "<code>1x Sol Ring</code>; a printing as the set code in parentheses and the collector number, as "
        "<code>1 Sol Ring (cmr) 436</code>; <code>*F*</code> for foil and <code>*E*</code> for etched; a "
        "category in brackets, "
        "as <code>[Ramp]</code>; a line that is only <code>Sideboard</code>, <code>Maybeboard</code>, "
        "<code>Commander</code> or <code>// Lands</code> starts that section and becomes the category of the "
        "rows under it; <code>SB:</code> in front of a row is MTGO's sideboard mark. Lines starting with "
        "<code>#</code> and blank lines are ignored, and a name without a count adds one copy. A CSV export "
        "from Archidekt and the gateway's own .json are read as they are.</p>"
    )
    ref_formats = (
        "<div class='tbl'><table><thead><tr><th>What</th><th>Formats</th><th>Comes back in?</th></tr></thead>"
        "<tbody>"
        "<tr><td>A deck (Export in the More menu)</td><td>Archidekt import text, plain decklist and the "
        "sideboard list, each with Copy; downloads as Archidekt .txt, plain .txt, .csv and .json</td>"
        "<td>yes: New deck here, or Archidekt's Import dialog</td></tr>"
        "<tr><td>A deck, one-way</td><td>Arena .txt, MTGO .dek, PDF</td><td>no</td></tr>"
        "<tr><td>The collection</td><td>.csv in the column layout Archidekt's own import reads</td>"
        "<td>yes: Import a list here, or Archidekt</td></tr>"
        "<tr><td>A deck report</td><td>Markdown (.md), a self-contained HTML page (light and dark, no "
        "scripts), Copy as Markdown</td><td>no (reports are read, not imported)</td></tr>"
        "</tbody></table></div>"
        "<p>The Android app saves downloads to your Downloads folder.</p>"
    )

    # -- Explanation ------------------------------------------------------------------------------
    exp_approvals = (
        "<p>A hand save and an applied proposal travel the same path: a proposal record, a snapshot when "
        "it changes the cards or details of one of your decks, the Archidekt write, a read-back, an audit "
        "row. That is why every change, yours or the assistant's, has a record under Proposals and "
        "History, and why such a change to one of your decks can be undone by restoring its snapshot "
        "(which does not put back the deck's folder, tags or cover).</p>"
        f"{_h4('exp-approvals-proposal', 'Proposals')}"
        "<p>An assistant never writes to Archidekt directly. It proposes: the exact list of rows that "
        "would change, stored on the gateway with a review page and, in a chat that shows cards, a card "
        "with Approve and Reject. Whether the assistant may then apply the proposal itself depends only on "
        "your approval mode and the proposal's risk level (<a href='#ref-approval'>the reference</a> has "
        "the rules), except that a held change, a change waiting for another change first, one another "
        "app had a hand in, one that replaces another app's change, or one that another app's waiting "
        "change would replace always waits for your press; your own saves count as approved by "
        "the press. Before anything is applied the "
        "gateway checks the change still fits the deck as it is now, and after the write it reads the deck "
        "back to confirm what landed.</p>"
        f"{_h4('exp-approvals-snapshot', 'Snapshots and the backup copy')}"
        "<p>Every apply to one of your decks takes a <strong>snapshot</strong> first: the deck as it "
        "was, cards and the deck's own name, description, format, bracket and privacy, kept on the "
        "gateway and listed under History "
        "with <strong>Restore</strong>. The gateway can also put a <strong>backup copy</strong> of the "
        f"whole deck in a “{folder}” folder on your Archidekt account, so you have a copy "
        "even where this gateway is not. "
        f"{backup_box} {backup_assistant}Quick edits from the deck "
        "page (the card menu and the viewer's quantity buttons) keep the gateway snapshot only, so a "
        "one-card change does not fill your Archidekt folder with copies; deleting a deck keeps "
        f"{backup_delete} first.</p>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    exp_fresh = (
        "<p>Archidekt answers slowly and asks every app to go easy on it, so the gateway keeps what it "
        "read for a short while instead of asking again on every page.</p><ul>"
        "<li>Your <strong>deck list</strong> is kept in memory for 90 seconds after Archidekt answered and "
        "served from there by Home, Decks, the deck tools and the assistant; for a quarter of an hour more "
        "a slightly older list is shown while one refresh runs in the background.</li>"
        "<li>Every change the gateway sends to Archidekt for you (a new deck, an applied proposal, a "
        "deleted deck, a folder, a tag, a cover) <strong>drops the list first</strong>, so the next view "
        "is live again. A change made on archidekt.com itself shows up when the 90 seconds are up.</li>"
        "<li>The collection's unfiltered first page is kept for a minute per sort and dropped by every "
        "collection add, change or removal.</li>"
        "<li>When the list is cold and Archidekt has not answered within a second and a half, Home and "
        "Decks open at once with a gray <strong>placeholder</strong> that the page fills as soon as the "
        "list arrives; without script a reload shows the list.</li>"
        "<li>Card names suggest instantly because the gateway downloads Scryfall's card-name catalog once "
        "a day and answers from memory; until it has loaded (the first minute after a start) suggestions "
        "come from Scryfall directly.</li></ul>"
    )
    exp_assistant = (
        "<p>In Claude on the web, desktop and phones (and ChatGPT, as its maker reports), the things "
        "you have to pick or press come as <strong>cards in the chat</strong>: a proposal with Approve "
        "and Reject (Reject can carry a reason), the changes waiting for you with <strong>Apply "
        "all</strong>, the printings of a card as pictures to tap, and the cards read from your photos "
        "to keep or drop. Everything else, such as a deck, its statistics, a comparison, a list of "
        "decks or your account's setup, comes as the assistant's own words, which keeps a long chat "
        "light. A tap on a picker is passed to the assistant as text and it carries on from there; "
        "nothing changes until a proposal you approve. Only the Approve, Reject and Apply all buttons "
        "change anything, and each uses the card's one-time code. Once a proposal you approved on "
        "its card, or the changes you sent with Apply all, are applied or under way, the card posts "
        "a short message, &ldquo;Approved, go on&rdquo;, in the "
        "chat so the assistant carries on, where the app lets a card do that. Claude Code and older "
        "apps show the text instead and ask about unsure names with a short form.</p>"
        "<p>Every job has exactly one tool, so the assistant never guesses between two ways of doing "
        "the same thing. It can propose moving a deck to another folder, changing its tags or its cover "
        "(a settings change you approve, like any other). Some changes always wait for you: an assistant "
        "can propose deleting a deck, creating a folder, a like, a comment or a follow, but each one "
        "waits for your own press every time. Renaming folders, linking Archidekt and choosing your "
        "approval mode have no assistant tool at all. "
        "<a href='#ref-assistant'>What the assistant can do, by tool</a> is the full map.</p>"
        "<p class='tip'>The <a href='/install?for=skill'>assistant skill</a> teaches your assistant "
        "the house rules: "
        "look things up instead of guessing, show the diff before changing anything, never ask for a "
        "password. Install it once per assistant.</p>"
    )
    exp_privacy = (
        "<p>The gateway keeps as little as it can: who you are (from your sign-in), your Archidekt link, "
        "your proposals, snapshots, reports and scan drafts, your approval mode and theme, and an "
        "activity log of what your account and its apps did. Decks live on Archidekt and your collection "
        "is Archidekt's; card data and images come from Scryfall.</p><ul>"
        "<li>Only you can see your decks, proposals, snapshots, scans and collection; another member "
        "cannot reach them, and neither can their assistant.</li>"
        "<li>Your Archidekt sign-in is stored encrypted. No page, admin tool, log or backup shows it, and "
        "backups leave it out, so after the site is restored from a backup you sign in again and relink "
        "Archidekt. The key that opens it is on the same server, so whoever runs the server can use it "
        "to act as you on Archidekt until it expires. The Account page spells out exactly what linking "
        "gives this site, its admins and the person who runs it "
        "(<a href='/account#archidekt-disclosure'>What linking gives this gateway</a>).</li>"
        "<li>Nothing about the cards in your collection is stored on this gateway: it is read from and "
        "written to Archidekt through your link.</li>"
        "<li>Your assistant's access can be withdrawn at any time from the Account page (the "
        "<strong>Connected apps</strong> list) and ends on its own after a while (a week, unless "
        "this gateway is set otherwise) unless you sign in "
        "again.</li>"
        "<li>Nothing here is public or indexed. Links to Archidekt and Scryfall carry no referrer.</li>"
        "<li><strong>Delete my data</strong> on the Account page removes everything above, your sign-in "
        "included; your decks on Archidekt are not touched.</li></ul>"
    )
    exp_playtest = (
        "<p>There are two places to draw and play a deck by hand: the gateway's own <strong>Goldfish"
        "</strong> page, which plays the deck in your browser, and Archidekt's playtester, which only "
        "shows a private deck to a browser signed in to Archidekt. The gateway used to frame "
        "Archidekt's playtester on a page of its own, but a frame cannot carry your Archidekt sign-in, "
        "so private decks came up empty and the "
        "page was a step for nothing. <strong>Playtest</strong> therefore opens the playtester in its own "
        "tab, where your Archidekt sign-in lives; the Android app hands it to the phone's browser for the "
        "same reason. Old links to the framed page are sent on to Archidekt.</p>"
    )

    bodies: dict[str, str] = {
        "tut-start": tut_start,
        "tut-link": tut_link,
        "tut-connect": tut_connect,
        "tut-first-edit": tut_first_edit,
        "tut-first-sim": tut_first_sim,
        "how-new-deck": how_new_deck,
        "how-add-cards": how_add_cards,
        "how-organise": how_organise,
        "how-approve": how_approve,
        "how-restore": how_restore,
        "how-compare": how_compare,
        "how-export": how_export,
        "how-search": how_search,
        "how-scan": how_scan,
        "how-collection": how_collection if has_collection else "",
        "how-playtest": how_playtest,
        "how-layout": how_layout,
        "how-app": how_app,
        "how-signout": how_signout,
        "ref-assistant": ref_assistant,
        "ref-pages": ref_pages,
        "ref-approval": ref_approval,
        "ref-keys": ref_keys,
        "ref-syntax": ref_syntax,
        "ref-formats": ref_formats,
        "exp-approvals": exp_approvals,
        "exp-fresh": exp_fresh,
        "exp-assistant": exp_assistant,
        "exp-privacy": exp_privacy,
        "exp-playtest": exp_playtest,
    }
    parts = [
        (pid, ic, title, lead, [s for s in sections if bodies.get(s[0])])
        for pid, ic, title, lead, sections in PARTS
    ]

    def section(sid: str, ic: str, label: str) -> str:
        return (
            f"<section class='panel gsec' id='{sid}' aria-labelledby='{sid}-h' data-title='{_esc(label)}'>"
            f"<h3 id='{sid}-h'>{icon(ic)}{_esc(label)}{_anchor(sid, label)}</h3>{bodies[sid]}</section>"
        )

    main = _stackable(
        "".join(
            f"<section class='gpart' id='{pid}' aria-labelledby='{pid}-h'><header>"
            f"<h2 id='{pid}-h'>{icon(ic)}{_esc(title)}{_anchor(pid, title)}</h2><p>{_esc(lead)}</p></header>"
            + "".join(section(*s) for s in sections)
            + "</section>"
            for pid, ic, title, lead, sections in parts
        )
    )
    search = (
        "<div class='gsearch' hidden><label for='guide-q'>Search the guide</label>"
        "<input id='guide-q' type='search' placeholder='A word or two, such as “snapshot” or “F10”' "
        "autocomplete='off' spellcheck='false' aria-describedby='guide-status'>"
        "<p id='guide-status' class='status muted small' role='status' aria-live='polite'></p>"
        "<ul id='guide-results' class='gresults' aria-label='Matching sections' hidden></ul></div>"
    )
    return (
        f"<div class='guide' id='top'><div class='gside'>{search}{_nav(parts)}</div>"
        f"<div class='gmain'>{main}"
        "<p class='nomatch muted' hidden>Nothing in the guide matches that. Clear the search to see "
        "everything again.</p></div></div>"
        f"<a class='btn gtop hide' href='#top'>{icon('chev')} Back to top</a>"
    )


def add_guide_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings

    @server.custom_route("/guide", methods=["GET"], include_in_schema=False)
    async def guide(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if sub is None:
            return login_redirect("/guide")
        user = state.db.get_user(sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        return render(
            "Guide",
            guide_body(
                site=s.server_name,
                live=state.decks.live,
                writes_enabled=bool(s.writes_enabled),
                has_collection=getattr(state, "collection", None) is not None,
                max_rows=int(getattr(s, "auto_apply_max_rows", modes.DEFAULT_MAX_ROWS)),
                archidekt_backups=bool(state.live.get("archidekt_backups")),
                backup_folder=s.archidekt_backup_folder,
                watchlists=bool(s.watchlists_enabled),
            ),
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            user=display_name(user),
            wide=True,
            current="/guide",
            head_extra=f"<style>{GUIDE_CSS}</style><script src='/static/guide.js' defer></script>",
        )
