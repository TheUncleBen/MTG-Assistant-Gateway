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
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response

from . import modes
from .pages import _csrf, browser_session, login_redirect
from .theme import icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

_esc = html.escape

GUIDE_CSS = """
.guide{display:grid;grid-template-columns:minmax(0,1fr);gap:1rem;align-items:start}
.guide .gside{min-width:0}
.gsearch{margin:0 0 .75rem}
.gsearch label{display:block;font-weight:700;margin-bottom:.25rem}
.gsearch input[type=search]{width:100%}
.gsearch .status{min-height:1.2em;margin:.35rem 0 0}
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
.guide .tbl{overflow-x:auto;max-width:100%}
.guide table{border-collapse:collapse;width:100%;font-size:.95rem}
.guide th,.guide td{text-align:left;vertical-align:top;padding:.4rem .5rem;overflow-wrap:anywhere;
  border-bottom:1px solid var(--border-soft)}
.guide th{background:var(--surface-2);font-weight:700}
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
            ("tut-first-sim", "stats", "Your first simulation"),
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
    archidekt_backups: bool = True,
    backup_folder: str = "MTG Gateway backups",
) -> str:
    folder = _esc(backup_folder)
    writes = (
        "Edits are switched on here: what you save in the app goes to Archidekt at once, and an "
        "approved proposal from your assistant is applied for you."
        if writes_enabled
        else "Edits are switched off on this gateway for now: your saves and your assistant's proposals "
        "are kept as proposals and applied once the person running it turns writes on."
    )
    backup_box = (
        "The save bar has a tick box, <strong>Also keep a backup copy on Archidekt</strong>, on by "
        "default: unticking it skips only that extra copy for that one save."
        if archidekt_backups
        else "The backup copy on Archidekt is switched off on this gateway, so no tick box is offered."
    )
    rows = f"{max_rows} card row{'s' if max_rows != 1 else ''}"

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
        "<p class='tip'>On a phone the tabs at the bottom are Decks, Scan, Collection, Proposals and More; "
        "Search is the magnifier at the top. More opens Home, History, Guide and Account (and Admin). "
        "Unfolded or on a tablet the tabs move to a rail down the left edge, with Search among them. "
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
        "<li><span>Install the <a href='/skill'>assistant skill</a> once per assistant. It teaches your "
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
        "change and its snapshot are filed, with <strong>Restore</strong> if you ever want the deck back "
        "as it was.</span></li></ol>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    tut_first_sim = (
        "<p>A simulation tells you how the deck plays on its own: how fast the commander lands, how "
        "often the mana flows, how much damage goldfishing deals.</p>"
        "<ol class='steps'>"
        "<li><span>Open one of your decks and press <strong>Run simulation</strong>. The button says the "
        "run takes up to a minute and shows “Simulating…” while it does.</span></li>"
        "<li><span>The report opens: the deck, its commander, the games run and when; the headline numbers "
        "as tiles with their 95% intervals (the turn the commander lands, the chance of 40 damage or a "
        "lethal board by the last turn, mulligans, land drops); four small charts (milestones reached by "
        "turn, the turn the commander was cast, mana available and damage per turn); in plain words what "
        "the simulation could not model, with each group's share of the deck; and the validation verdict "
        "as a badge with the problems listed. The raw output stays, folded away at the end.</span></li>"
        "<li><span>Keep it: the buttons download the report as <strong>Markdown</strong> or as a "
        "self-contained <strong>HTML page</strong> (light and dark, no scripts) and <strong>Copy as "
        "Markdown</strong> puts it on the clipboard.</span></li>"
        "<li><span>Find it again under <a href='/history'>History</a> (filter by type: Reports). A report "
        "reused because the deck had not changed says so.</span></li></ol>"
        "<p>It is the same run the assistant makes for “goldfish this deck”: the statistics, the "
        "validation and the goldfish simulation (300 games), so the numbers match whichever way it is "
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
        "touch screen and slide, then press <strong>Save moves</strong>: they go to Archidekt at once; "
        "<strong>Undo all</strong> puts the cards back without reloading.</li>"
        "<li><strong>Quick add</strong> types a card name and takes you to the editor with it filled "
        "in.</li></ul>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    how_organise = (
        "<ul>"
        "<li>The <strong>More</strong> menu in a deck's banner holds Deck settings, Export (text, JSON or "
        "CSV), History, Compare, Deck stats, the deck's page on Archidekt and <strong>Delete deck</strong>, "
        "which asks you to type the deck's name and keeps a snapshot (and the Archidekt backup copy) "
        "first.</li>"
        "<li><strong>Deck settings</strong> changes the name, format, bracket, description and whether the "
        "deck is private or unlisted, and also sets the deck's <strong>cover image</strong>, its "
        "<strong>tags</strong> and the <strong>folder</strong> it sits in.</li>"
        "<li><strong>Folders</strong> on the Decks page creates and renames folders.</li>"
        "<li>Your assistant can propose moving a deck to another folder, changing its tags or its cover "
        "(a settings change you approve, like any other), but deleting a deck and creating or renaming "
        "folders are yours alone: they have no assistant tool at all.</li></ul>"
    )
    how_approve = (
        "<p>Your own saves in the app go to Archidekt at once: pressing Save is your approval. An "
        "<strong>assistant's</strong> edit, new deck, clone, settings change, restore or collection change "
        "is first saved as a <strong>proposal</strong>: the exact list of what would change.</p>"
        "<ol class='steps'>"
        "<li><span>Approve on the card the assistant shows in the chat (Reject can carry a reason), or "
        "open <a href='/proposals'>Proposals</a>, which lists what is waiting, open one to read the diff "
        "and press <strong>Apply</strong> or <strong>Reject</strong>.</span></li>"
        "<li><span>Before the edit is applied, the gateway checks the deck has not changed in the "
        f"meantime, saves a snapshot and puts a backup copy of the deck in a “{folder}” folder "
        "on your Archidekt account, then reads the deck back to confirm.</span></li>"
        "<li><span>Your own saves appear under Proposals too, already applied, so every change has a "
        "record.</span></li></ol>"
        "<p>On your Account page you can pick an approval mode: Manual (every change asks you), Semi-auto "
        "(the assistant applies small, low-risk edits itself and asks about the rest) or Full auto. "
        "Every change keeps a snapshot either way; <a href='#ref-approval'>Approval modes and risk "
        "levels</a> has the exact rules.</p>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    how_restore = (
        "<ol class='steps'>"
        "<li><span>Open <a href='/history'>History</a>. Entries are grouped by day (or by deck, with "
        "<strong>Group by</strong>); the filter bar narrows them by deck, type (Changes, Snapshots, "
        "Reports), state, when (today, the last 7, 30 or 90 days or this year, counted in UTC days) and "
        "a search over deck names and change text, and <strong>Older</strong> pages through the rest 25 "
        "at a time.</span></li>"
        "<li><span>A snapshot row links to the change it was taken before and has <strong>Restore (review "
        "first)</strong>. Press it to see what would change.</span></li>"
        "<li><span>Confirm. <strong>Restore</strong> puts the deck back exactly as the snapshot had it, "
        "cards and the deck's own name, description, format, bracket and privacy alike.</span></li></ol>"
        "<p>Or ask your assistant to “undo yesterday's change”: a restore is a proposal like any other, "
        "always high risk, so it waits for your press unless you chose Full auto.</p>"
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
        "<p><a href='/search'>Search</a> finds public decks on Archidekt the way the Archidekt site does: by "
        "deck name, commander, format, colours or the person who built it, ordered by newest, most viewed "
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
        "under <strong>Sessions</strong>. On the Android app the <strong>Scan with phone camera</strong> "
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
        "edit. It cannot like, follow or comment for you: those buttons only work when you press them "
        "yourself.</p>"
    )
    how_playtest = (
        "<p><strong>Playtest</strong> in a deck's banner opens the deck in Archidekt's own playtester "
        "(opening hand, mulligans, turns played by hand) in a new tab, or in the phone's browser from the "
        "app. A private deck shows there when that browser is signed in to Archidekt, so sign in on "
        "archidekt.com once in the browser you use. <a href='#exp-playtest'>Why Playtest opens Archidekt "
        "in a new tab</a> explains.</p>"
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
        "<p>The <a href='/app'>Android app</a> shows these same pages as a native-feeling app with the phone "
        "camera built in. Sign in once, then it opens on your home page. The app's own actions (scan with "
        "the phone camera, reload, open in browser, change gateway) sit in the account menu at the top "
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
            "linking itself happens only in the browser (Account page); the account card in the chat "
            "shows what is missing and opens that page",
        ),
        (
            "List my decks (private included), filter by name, format, folder",
            "<code>list_my_decks</code>",
            "no",
            "",
        ),
        (
            "Search public decks (name, commander, partial commander names, format, colours, owner), and "
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
            "the deck card in the chat shows the deck by category with pictures and rules text; hides "
            "Mystic Forge <code>archidekt_deck</code>, <code>archidekt_export</code>; <code>owner</code> "
            "says whose deck it is",
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
            "Goldfish simulation of one deck or pasted list, stored with its stats and validation (a "
            "pasted list is returned, not stored)",
            "<code>run_deck_report</code> (+ <code>list_deck_reports</code>, <code>get_deck_report</code>)",
            "stores a report",
            "hides <code>goldfish_run</code>; takes the simulator's options; the deck page's Run "
            "simulation is the hand path",
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
            "applied with the same verified calls as the deck settings page",
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
            "Proposals: list, read, approve with the card's code, reject (with a reason on the card), apply",
            "<code>list_my_proposals</code>, <code>get_proposal</code>, <code>confirm_proposal</code>, "
            "<code>reject_proposal</code>, <code>apply_proposal</code>",
            "apply writes",
            "<code>apply_proposal</code> succeeds only when the mode allows",
        ),
    ]
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
        "<p><strong>No assistant tool exists for:</strong> deleting a deck, creating or renaming folders, "
        "liking, bookmarking, following, commenting, editing a comment, linking or unlinking Archidekt, "
        "approval modes, admin. Those are hand actions by design: either social (a person speaks for "
        "themselves) or destructive.</p>"
        "<p><strong>Only the assistant does:</strong> research lookups (Scryfall, EDHREC, combos, rules), "
        "the paired goldfish A/B with the simulator's options, reading card photos into names, proposing "
        "changes it worked out itself. <strong>Both reach:</strong> reading decks and collections, "
        "statistics, creating and editing decks, deck settings (folder, tags and cover included), "
        "cloning, restoring snapshots, collection changes, scan drafts; your path applies at once, the "
        "assistant's is a proposal under your mode. The goldfish simulation of one deck and the "
        "comparison of two are both paths: Run simulation and Compare on the deck page for people, "
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
            "views, grouping, sorting, filter, the whole card on tap, the card menu, drag and Save moves, "
            "Quick add, Playtest, Run simulation, statistics with Probability of draw and Deck checks, "
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
        ("Search", "<code>/search</code>", "public decks by name, commander, format, colours, owner"),
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
            "what is waiting; the review page with Approve, Apply, Reject",
        ),
        (
            "History",
            "<code>/history</code>",
            "changes, snapshots (Restore) and reports as a timeline with a filter bar; a report at "
            "<code>/history/reports/&lt;id&gt;</code> with its Markdown and HTML exports",
        ),
        ("My activity", "<code>/activity</code>", "what your account and its connected apps did"),
        (
            "Account",
            "<code>/account</code>",
            "link or unlink Archidekt, approval mode, Connected apps, theme, sign out (two buttons), "
            "Delete my data",
        ),
        ("Assistant skill", "<code>/skill</code>", "the house rules to install in your assistant"),
        ("Android app", "<code>/app</code>", "download and set-up"),
        ("Guide", "<code>/guide</code>", "this page"),
        ("Admin (admin group only)", "<code>/admin</code>", "members, activity, metrics, the System card"),
    ]
    ref_pages = (
        "<p>Every page needs the same sign-in. Addresses are relative to this gateway.</p>"
        "<div class='tbl'><table><thead><tr><th>Page</th><th>Address</th><th>What is there</th></tr></thead>"
        "<tbody>"
        + "".join(f"<tr><td>{_esc(p)}</td><td>{a}</td><td>{w}</td></tr>" for p, a, w in page_rows)
        + "</tbody></table></div>"
        "<p>On a phone the tabs at the bottom are Decks, Scan, Collection, Proposals and More (Home, "
        "History, Guide, Account and Admin); Search is the magnifier at the top. From 600 px on a touch "
        "screen the tabs are a rail down the left edge; a wider browser has the links in the top bar.</p>"
    )
    ref_approval = (
        "<p>Your approval mode on the <a href='/account'>Account</a> page says how much your assistant "
        "may change on Archidekt without asking you first. It is your own setting and applies to nobody "
        "else; the gateway may cap the choices.</p>"
        "<div class='tbl'><table><thead><tr><th>Mode</th><th>Low-risk proposal</th><th>High-risk proposal"
        "</th></tr></thead><tbody>"
        f"<tr><td><strong>Manual</strong> (default): {_esc(modes.MODE_LABELS['manual'])}</td>"
        "<td>you press Approve or Apply</td><td>you press</td></tr>"
        f"<tr><td><strong>Semi-auto</strong>: {_esc(modes.MODE_LABELS['semi'])}</td>"
        "<td>the assistant applies it</td><td>you press</td></tr>"
        f"<tr><td><strong>Full auto</strong>: {_esc(modes.MODE_LABELS['auto'])}</td>"
        "<td>the assistant applies it</td><td>the assistant applies it</td></tr>"
        "</tbody></table></div>"
        f"<p><strong>Low risk:</strong> an edit to an existing deck of at most {rows}, each adding, "
        f"removing or moving no more than {modes.MAX_COPIES_PER_ROW} copies, with only add, remove, "
        "quantity, category, finish or printing changes and no commander change; cloning a deck. "
        "<strong>High risk:</strong> anything over those limits, a commander change, a new deck, a "
        "snapshot restore, deck details (name, description, format, bracket, privacy, folder, tags, "
        "cover). Every applied proposal keeps a snapshot first and is read back to verify.</p>"
        f"<p class='tip'>{_esc(modes.AUTO_WARNING)}</p>"
        f"{_h4('ref-approval-hand', 'Your own saves')}"
        "<p>A hand save needs no approval: pressing Save is it. It still keeps a snapshot, and asks "
        "“are you sure” only for a restore, a commander change, or a removal of "
        f"{modes.HAND_EDIT_CONFIRM_ROWS} or more different cards or more than "
        f"{modes.HAND_EDIT_CONFIRM_COPIES} copies in one go; deleting a deck asks for its name.</p>"
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
        "<p>A hand save and an applied proposal travel the same path: a proposal record, a snapshot, the "
        "Archidekt write, a read-back, an audit row. That is why every change, yours or the assistant's, "
        "has a record under Proposals and History, and why every one of them can be undone.</p>"
        f"{_h4('exp-approvals-proposal', 'Proposals')}"
        "<p>An assistant never writes to Archidekt directly. It proposes: the exact list of rows that "
        "would change, stored on the gateway with a review page and, in a chat that shows cards, a card "
        "with Approve and Reject. Whether the assistant may then apply the proposal itself depends only on "
        "your approval mode and the proposal's risk level (<a href='#ref-approval'>the reference</a> has "
        "the rules); your own saves count as approved by the press. Before anything is applied the "
        "gateway checks the deck has not changed in the meantime, and after the write it reads the deck "
        "back to confirm what landed.</p>"
        f"{_h4('exp-approvals-snapshot', 'Snapshots and the backup copy')}"
        "<p>Every apply takes a <strong>snapshot</strong> first: the deck as it was, cards and the deck's "
        "own name, description, format, bracket and privacy, kept on the gateway and listed under History "
        "with <strong>Restore</strong>. The gateway can also put a <strong>backup copy</strong> of the "
        f"whole deck in a “{folder}” folder on your Archidekt account, so you have a copy "
        "even where this gateway is not. "
        f"{backup_box} Applies made by your assistant always make the copy. Quick edits from the deck "
        "page (the card menu and the viewer's quantity buttons) keep the gateway snapshot only, so a "
        "one-card change does not fill your Archidekt folder with copies; deleting a deck keeps both the "
        "snapshot and the backup copy first.</p>"
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
        "Decks open at once with a grey <strong>placeholder</strong> that the page fills as soon as the "
        "list arrives; without script a reload shows the list.</li>"
        "<li>Card names suggest instantly because the gateway downloads Scryfall's card-name catalog once "
        "a day and answers from memory; until it has loaded (the first minute after a start) suggestions "
        "come from Scryfall directly.</li></ul>"
    )
    exp_assistant = (
        "<p>In Claude on the web, desktop and phones (and ChatGPT, as its maker reports), the things "
        "you have to pick or press come as <strong>cards in the chat</strong>: a proposal with Approve "
        "and Reject (Reject can carry a reason), the printings of a card as pictures to tap, the cards "
        "read from your photos to keep or drop, a deck by category with pictures and rules text, and "
        "your account's setup. A tap is passed to the assistant as text and it carries on from there; "
        "nothing changes until a proposal you approve. The only card that acts by itself is the proposal "
        "card, whose Approve and Reject both use its one-time code. Claude Code and older apps show the "
        "text instead and ask about unsure names with a short form.</p>"
        "<p>Every job has exactly one tool, so the assistant never guesses between two ways of doing "
        "the same thing. It can propose moving a deck to another folder, changing its tags or its cover "
        "(a settings change you approve, like any other). Some things are yours alone: deleting a deck, "
        "creating or renaming folders, likes, comments and follows, linking Archidekt and choosing your "
        "approval mode have no assistant tool at all. <a href='#ref-assistant'>What the assistant can "
        "do, by tool</a> is the full map.</p>"
        "<p class='tip'>The <a href='/skill'>assistant skill</a> teaches your assistant the house rules: "
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
        "<p>Archidekt's playtester is the one place to draw and play a deck by hand, and it only shows a "
        "private deck to a browser signed in to Archidekt. The gateway used to frame it on a page of its "
        "own, but a frame cannot carry your Archidekt sign-in, so private decks came up empty and the "
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

    main = "".join(
        f"<section class='gpart' id='{pid}' aria-labelledby='{pid}-h'><header>"
        f"<h2 id='{pid}-h'>{icon(ic)}{_esc(title)}{_anchor(pid, title)}</h2><p>{_esc(lead)}</p></header>"
        + "".join(section(*s) for s in sections)
        + "</section>"
        for pid, ic, title, lead, sections in parts
    )
    search = (
        "<div class='gsearch' hidden><label for='guide-q'>Search the guide</label>"
        "<input id='guide-q' type='search' placeholder='A word or two, such as “snapshot” or “F10”' "
        "autocomplete='off' spellcheck='false' aria-describedby='guide-status'>"
        "<p id='guide-status' class='status muted small' role='status' aria-live='polite'></p></div>"
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
                writes_enabled=bool(s.writes_enabled),
                has_collection=getattr(state, "collection", None) is not None,
                max_rows=int(getattr(s, "auto_apply_max_rows", modes.DEFAULT_MAX_ROWS)),
                archidekt_backups=bool(getattr(s, "archidekt_backups", True)),
                backup_folder=s.archidekt_backup_folder,
            ),
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            wide=True,
            scripts=True,
            current="/guide",
            head_extra=f"<style>{GUIDE_CSS}</style><script src='/static/guide.js' defer></script>",
        )
