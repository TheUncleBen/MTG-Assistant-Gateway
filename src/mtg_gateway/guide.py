"""The in-app guide: what a member can do on these pages, with or without an AI assistant.

End-user documentation only, in the member's language: decks, search, scanning, the collection,
proposals and history, the assistant and the Android app. Nothing about deploying or operating a
gateway lives here (that is the repository's docs). The page needs the same sign-in as every
other page and reads a few facts from the gateway (its name, whether deck writes are on, whether
proposals can be applied from chat) so it describes this gateway and not a generic one.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import Response

from .pages import _csrf, browser_session, login_redirect
from .theme import icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

_esc = html.escape

GUIDE_CSS = """
.guide{display:grid;grid-template-columns:14rem minmax(0,1fr);gap:1.5rem;align-items:start}
.guide nav.toc{position:sticky;top:4rem;display:flex;flex-direction:column;gap:.15rem}
.guide nav.toc a{display:flex;align-items:center;gap:.5rem;padding:.45rem .6rem;border-radius:var(--radius);
  color:var(--text);text-decoration:none;font-weight:700}
.guide nav.toc a:hover{background:var(--surface-2)}
.guide nav.toc a svg{color:var(--orange)}
.guide section.panel h2{display:flex;align-items:center;gap:.5rem;font-size:1.35rem;margin-bottom:.5rem}
.guide section.panel h2 svg{color:var(--orange)}
.guide section.panel h3{font-size:1.05rem;margin:1rem 0 .25rem}
.guide ul{padding-left:1.2rem}
.guide li{margin:.2rem 0}
.guide .tip{border-left:3px solid var(--orange);padding:.5rem .75rem;background:var(--orange-tint);
  border-radius:0 var(--radius) var(--radius) 0;margin:.75rem 0}
.guide .steps{counter-reset:step;list-style:none;padding:0}
.guide .steps li{counter-increment:step;display:flex;gap:.6rem;margin:.4rem 0}
.guide .steps li::before{content:counter(step);flex:0 0 1.6rem;height:1.6rem;border-radius:50%;
  background:var(--orange);color:#fff;font-weight:900;display:inline-flex;align-items:center;
  justify-content:center;font-size:.9rem}
@media (max-width:900px){
  .guide{grid-template-columns:1fr}
  .guide nav.toc{position:static;flex-direction:row;flex-wrap:wrap;gap:.35rem;margin-bottom:.5rem}
  .guide nav.toc a{background:var(--surface);border:1px solid var(--border);font-size:.9rem;
    padding:.35rem .6rem}
}
"""

SECTIONS = [
    ("start", "home", "Start here"),
    ("decks", "decks", "Your decks"),
    ("search", "search", "Finding decks"),
    ("scan", "camera", "Scanning cards"),
    ("collection", "collection", "Your collection"),
    ("proposals", "proposals", "Proposals and history"),
    ("assistant", "report", "With an AI assistant"),
    ("app", "download", "The Android app"),
    ("privacy", "account", "Your account and privacy"),
]


def guide_body(*, site: str, writes_enabled: bool, has_collection: bool) -> str:
    toc = (
        "<nav class='toc' aria-label='Guide sections'>"
        + "".join(f"<a href='#{sid}'>{icon(ic)}{_esc(label)}</a>" for sid, ic, label in SECTIONS)
        + "</nav>"
    )

    def sec(sid: str, body: str) -> str:
        ic, label = next((i, n) for s, i, n in SECTIONS if s == sid)
        return f"<section class='panel' id='{sid}'><h2>{icon(ic)}{_esc(label)}</h2>{body}</section>"

    writes = (
        "Edits are switched on here: what you save in the app goes to Archidekt at once, and an "
        "approved proposal from your assistant is applied for you."
        if writes_enabled
        else "Edits are switched off on this gateway for now: your saves and your assistant's proposals "
        "are kept as proposals and applied once the person running it turns writes on."
    )
    chat_apply = (
        "Approve on the card the assistant shows in the chat, or open the review page and press Apply. "
        "On your Account page you can pick an approval mode: Manual (every change asks you), Semi-auto "
        "(the assistant applies small, low-risk edits itself and asks about the rest) or Full auto. "
        "Every change keeps a snapshot either way."
    )
    start = (
        f"<p>{_esc(site)} is a private companion to <strong>Archidekt</strong>. Everything you can do on "
        "the Archidekt website with your own decks, you can do here on your phone or computer, and an AI "
        "assistant such as Claude or ChatGPT can do it with you. Your decks stay on Archidekt; what you do "
        "here is saved there, and an assistant never changes one without a proposal you approve.</p>"
        "<ol class='steps'>"
        "<li><span><strong>Link Archidekt</strong> once on the <a href='/account'>Account</a> page. The "
        "site then sees the decks of that account, private ones included.</span></li>"
        "<li><span><strong>Open <a href='/decks'>Decks</a></strong> to browse them the way Archidekt shows "
        "them: text, stacks or grid, grouped and sorted how you like.</span></li>"
        "<li><span><strong>Scan</strong> a pile of cards with your phone, add them to a deck or to your "
        "collection, and <strong>search</strong> public decks for ideas.</span></li>"
        "<li><span>Optional: <strong>connect an assistant</strong> from the home page and ask it to "
        "research, "
        "simulate or edit for you. An assistant's edit comes back to you as a proposal.</span></li></ol>"
        "<p class='tip'>On a phone the tabs at the bottom are Decks, Scan, Collection, Proposals and More; "
        "Search is the magnifier at the top. More opens Home, History, Guide and Account (and Admin). "
        "Unfolded or on a tablet the tabs move to a rail down the left edge, with Search among them. "
        "Your account menu is your picture at the top right, and a tap anywhere else closes it.</p>"
    )
    decks = (
        "<p><a href='/decks'>Decks</a> lists the decks of your linked Archidekt account with their cover "
        "art. Filter by name or folder, switch between grid and list, and sort by last updated, created, "
        "name or format. <strong>New deck</strong> creates one from a name, a pasted list, a CSV export or "
        "a scan.</p>"
        "<h3>One deck</h3><ul>"
        "<li><strong>View as</strong> text, stacks or grid; <strong>Group by</strong> category, type, mana "
        "value, colour or none; <strong>Sort by</strong> name, mana value, price and more. The page updates "
        "as soon as you change a choice.</li>"
        "<li><strong>Filter</strong> narrows the cards on the page as you type.</li>"
        "<li>Tap a card (or a stack on a phone, which fans it out) to read the whole card: the image, "
        "every face with its mana cost, type line, rules text, power and toughness or loyalty, flavour "
        "text, printing, rarity, artist, price and legal formats; then open it on Scryfall, mark it as "
        "owned, or move it to another category. Thumbnails in the editor and the collection open the same "
        "viewer.</li>"
        "<li>On your own deck, <strong>drag a card</strong> onto another category: with a mouse, or press "
        "and hold on a touch screen and slide, then press <strong>Save moves</strong>: they go to Archidekt "
        "at once.</li>"
        "<li><strong>Quick add</strong> types a card name and takes you to the editor with it filled in.</li>"
        "<li><strong>Playtest</strong> shows the deck in Archidekt's own playtester (opening hand, "
        "mulligans, turns played by hand) on a gateway page, in the app as on the web. A private deck "
        "shows only when this browser is signed in to Archidekt; the page keeps an Open on Archidekt "
        "button for that case.</li>"
        "<li><strong>Run simulation</strong> runs the statistics, the validation and the goldfish "
        "simulation (300 games), opens the result and files it under History: the same run the "
        "assistant makes, so the numbers match whichever way it is started.</li>"
        "<li><strong>Compare with another deck…</strong> (More menu) pits the deck against a "
        "preconstructed deck from Archidekt's list, any deck link or a pasted list: cards taken out, "
        "put in, changed counts and the statistics' differences. The assistant's compare_decks makes "
        "the same comparison.</li>"
        "<li>The <strong>More</strong> menu in the banner holds Deck settings, Export (text, JSON or CSV), "
        "History, Compare, Deck stats, the deck's page on Archidekt and <strong>Delete deck</strong>, which "
        "asks you to type the deck's name and keeps a snapshot (and the Archidekt backup copy) first.</li>"
        "<li><strong>Deck settings</strong> also sets the deck's <strong>cover image</strong>, its "
        "<strong>tags</strong> and the <strong>folder</strong> it sits in; <strong>Folders</strong> on the "
        "Decks page creates and renames folders.</li>"
        "<li>Below the cards: statistics (mana curve, colours, types, rarities, prices, legality), "
        "<strong>Probability of draw</strong> (at least or exactly N cards of a category, name, type or "
        "mana value in your first N cards), <strong>Deck checks</strong> (deck size for the format, "
        "commander zone, colour identity, singleton or four-copies limit, sideboard size, uncategorised "
        "cards), the bracket estimate and the description.</li>"
        "<li><strong>Export</strong> (More menu) gives the deck as Archidekt import text with every row's "
        "printing, finish, categories and labels (paste it into Archidekt's Import dialog or the New deck "
        "page), as a plain decklist and as the sideboard list, each with a Copy button, plus downloads as "
        "Archidekt .txt, plain .txt, .csv and .json; the app saves downloads to your Downloads "
        "folder.</li></ul>"
        "<h3>The editor</h3><p>Change quantities, categories (type a new one to create it), foil or "
        "printing, add cards with autocompletion (to the deck or the maybeboard), edit maybeboard and "
        "sideboard rows, paste a whole list, remove cards, and undo. <strong>Save changes</strong> "
        "sends the whole session to Archidekt in one go, after a snapshot of the deck as it was; only a big "
        "removal (many cards at once) asks you to confirm first.</p>"
        f"<p class='tip'>{_esc(writes)}</p>"
    )
    search = (
        "<p><a href='/search'>Search</a> finds public decks on Archidekt the way the Archidekt site does: by "
        "deck name, commander, format, colours or the person who built it, ordered by newest, most viewed "
        "or largest. Open any result to read it with the same views as your own decks; the owner's name "
        "opens their profile with every public deck they have.</p>"
        "<p>From a public deck you can run the statistics, export it, like, bookmark or comment on it "
        "(your own comments can be edited and deleted), follow its owner, or clone it into your own "
        "account. <a href='/precons'>Precons</a> lists every preconstructed deck Archidekt knows, by set.</p>"
    )
    scan = (
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
    )
    collection = (
        "<p><a href='/collection'>Collection</a> is the list of cards you own. It is your Collection on "
        "Archidekt, shown and edited here through the account you linked: nothing about your cards is "
        "stored on this gateway, and what you add here appears on archidekt.com at once. Scan a pile and "
        "save it, or add cards by name; each card keeps its printing, finish, condition and count.</p><ul>"
        "<li>Filter by name, show it as a grid or a list, sort by newest or by set release, and page "
        "through it. Plus and minus change the count; the cross removes a card; the dots open its details "
        "(finish, condition, language, price paid), saved to Archidekt on Save.</li>"
        "<li>Cards you own show a <strong>green dot</strong> on every deck page, your own decks and public "
        "ones alike, with the number of copies Archidekt knows about.</li>"
        "<li><strong>Export CSV</strong> downloads the whole collection in the column layout Archidekt's "
        "own import reads.</li>"
        "<li>A scan is a draft that stays here as long as you like, up to a whole deck: fix misread cards, "
        "printings and quantities first, then send it to your collection or a deck, which removes the "
        "draft. Nothing expires; you delete what you do not want.</li></ul>"
        "<p class='tip'>Your assistant can read the collection too, for questions like “which cards in "
        "this deck do I not own yet?”, and propose additions or removals that you approve like a deck "
        "edit. It cannot like, follow or comment for you: those buttons only work when you press them "
        "yourself.</p>"
    )
    proposals = (
        "<p>Your own saves in the app go to Archidekt at once: pressing Save is your approval. An "
        "<strong>assistant's</strong> edit, new deck, clone, settings change, restore or collection change "
        "is first saved as a <strong>proposal</strong>: the exact list of what would change. "
        f"{_esc(chat_apply)}</p><ul>"
        "<li><a href='/proposals'>Proposals</a> lists what is waiting. Open one to read the diff and press "
        "<strong>Apply</strong> or <strong>Reject</strong>. Your own saves appear here too, already "
        "applied, so every change has a record.</li>"
        "<li>Before an edit is applied, the gateway checks the deck has not changed in the meantime, saves a "
        "<strong>snapshot</strong> and puts a backup copy of the deck in an “MTG Gateway backups” "
        "folder on your Archidekt account, then reads the deck back to confirm.</li>"
        "<li><a href='/history'>History</a> shows proposals, snapshots and deck reports over time. "
        "<strong>Restore</strong> puts a deck back exactly as a snapshot had it, after you confirm.</li>"
        "<li><strong>Reports</strong> store a deck's statistics, a legality check and a goldfish simulation "
        "so you can follow how it develops.</li></ul>"
    )
    assistant = (
        "<p>Connect Claude or ChatGPT from the home page (<strong>Connect an AI assistant</strong>) on the "
        "web, desktop or phone app you use. You sign in with the same account as here, and the assistant "
        "acts as you: it can read your decks but never change one without a proposal you approve.</p>"
        "<p>Things you can ask it:</p><ul>"
        "<li>“Show me my decks”, “open my Krenko deck and tell me its mana curve”.</li>"
        "<li>“Search for Atraxa decks under bracket 3”, “what does this deck run that mine "
        "doesn't?”.</li>"
        "<li>“Look up this card”, rulings, prices, combos, EDHREC data, the Comprehensive Rules.</li>"
        "<li>“Goldfish this deck 500 times” (it reports the seed, the settings and what the "
        "simulation leaves out).</li>"
        "<li>“Swap these three cards”, “build a new deck from this list”, “undo "
        "yesterday's change”: each comes back as a proposal with a review link.</li>"
        "<li>“Here are photos of my cards”: it reads the names and matches them; or "
        "“use my last scan” to pick up a scan from the Scan page.</li>"
        "<li>“Which cards in this deck do I own?” using your collection.</li></ul>"
        "<p>Every job has exactly one tool, so the assistant never guesses between two ways of doing "
        "the same thing. Some things are yours alone: deleting a deck, its cover, tags and folders, "
        "likes, comments and follows, linking Archidekt and choosing your approval mode have no "
        "assistant tool at all. The full map of what the assistant can do, what you do by hand and "
        "what both reach is in the gateway's documentation (CAPABILITIES).</p>"
        "<p class='tip'>The <a href='/skill'>assistant skill</a> teaches your assistant the house rules: "
        "look things up instead of guessing, show the diff before changing anything, never ask for a "
        "password. Install it once per assistant.</p>"
    )
    app = (
        "<p>The <a href='/app'>Android app</a> shows these same pages as a native-feeling app with the phone "
        "camera built in. Sign in once, then it opens on your home page. The app's own actions (scan with "
        "the phone camera, reload, open in browser, change gateway) sit in the account menu at the top "
        "right. Links to Archidekt or Scryfall open in your browser; gateway links shared from other apps "
        "open in the app.</p>"
        "<p>On a foldable, the folded screen has the tabs at the bottom and the unfolded screen moves them "
        "to a rail down the left edge, like other Android apps; the layout follows the window, so split "
        "screen and pop-up windows work too. While scanning, half-folding the phone puts the camera on "
        "the top half and the list on the bottom.</p>"
        "<p>No Android phone? Add this site to your home screen from the browser (Chrome and Samsung "
        "Internet on Android, Safari on iPhone: Share, then Add to Home Screen) and it opens like an app, "
        "scanning included.</p>"
    )
    privacy = (
        "<p>The <a href='/account'>Account</a> page shows who you are signed in as and your Archidekt "
        "link. Unlinking removes the stored Archidekt session at once. <strong>Sign out on all my "
        "devices</strong> ends every browser and Android app session; connected assistants keep working "
        "until you disconnect them under <strong>Connected apps</strong>.</p><ul>"
        "<li>Only you can see your decks, proposals, snapshots, scans and collection; another member "
        "cannot reach them, and neither can their assistant.</li>"
        "<li>Your assistant's access can be withdrawn at any time from the Account page (the "
        "<strong>Connected apps</strong> list) and ends on its own after a week unless you sign in "
        "again.</li>"
        "<li>Nothing here is public or indexed. Card data and images come from Scryfall; decks live on "
        "Archidekt.</li>"
        "<li>Light, Dark or System theme is in the account menu and applies in the app too.</li></ul>"
    )
    panels = (
        sec("start", start)
        + sec("decks", decks)
        + sec("search", search)
        + sec("scan", scan)
        + (sec("collection", collection) if has_collection else "")
        + sec("proposals", proposals)
        + sec("assistant", assistant)
        + sec("app", app)
        + sec("privacy", privacy)
    )
    return f"<div class='guide'>{toc}<div>{panels}</div></div>"


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
            ),
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            wide=True,
            current="/guide",
            head_extra=f"<style>{GUIDE_CSS}</style>",
        )
