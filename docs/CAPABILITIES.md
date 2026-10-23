# Capability map

Everything a person can do with the gateway, by the path they take: asking an
AI assistant, or using the web pages and the Android app by hand. One table
per path, then how the two relate. Every feature appears exactly once as an
owned capability. An assistant reaches each job through one tool, with a few
exceptions that show the same thing another way (for example, the
`show_waiting_changes` card lists the waiting changes `list_my_proposals` also
lists).

The rule behind it: **one tool per job.** Where Mystic Forge has a tool that
does what a gateway tool does, the Mystic Forge one is hidden and an
assistant that calls it is told which tool owns the job
([one tool per job](../README.md#one-tool-per-job) in the README has the
short list; the tool reference in
[plugin/mtg-gateway/skills/mtg-gateway/reference/tools.md](../plugin/mtg-gateway/skills/mtg-gateway/reference/tools.md)
has every parameter).

## A. What the assistant can do (user to agent)

The assistant acts as the signed-in member. Reads need nothing more than the
connection; **every write is a proposal** (`propose_*`), and who applies it is
decided by the member's approval mode on their Account page, never by the
assistant:

| Mode | Low-risk proposal | High-risk proposal |
| --- | --- | --- |
| Manual, shown as "Ask me every time" (default) | the member presses Approve or Apply | the member presses |
| Semi-auto, shown as "Apply low-risk changes without asking" | the assistant applies it (`apply_proposal`) | the member presses |
| Full auto, shown as "Apply deck and collection changes without asking" | the assistant applies it | the assistant applies it, except a restore, a commander or companion change, a new deck, an edit of more than 20 rows (by default), making a deck public or unlisted, a category change that touches the commander or which cards count in the deck, and any edit of a description that has formatting, such as bold text, links or pictures: the member presses |

Low risk (`MTG_AUTO_APPLY_MAX_ROWS` sets the row limit, 5 here): A small edit to an existing deck or to your collection: at most 5 card rows, each a card added or removed, or a new quantity, category, finish, printing, color tag or custom mana value, no more than 4 copies a row, never a change to which cards are the commanders or to the categories of a row in the commander zone, and never fewer copies of a card that has a row in the commander zone and another row, both in the deck or both in the maybeboard or sideboard; a private copy of a deck (a clone), which changes nothing that exists; and a change to your Archidekt lists (category templates, default categories, color tags, pins or custom cards) of at most 5 entries, with no deletes.
High risk: Everything else: more rows than that, a commander change, a new
deck, restoring a snapshot, the deck's details (name, format, bracket, description, visibility, folder, tags and cover), and its categories (a new category, a new name, Premier, Not in deck or Not in price, or deleting one).
Always the member's own press: Actions on your Archidekt account (likes, votes, bookmarks, follows, comments, new folders, collaborator changes, deck help requests, reports to Archidekt's moderators, contest entries, and profile and email settings), deleting from your Archidekt lists and deleting a deck are never applied by an assistant: each one waits for your own press, in every mode, on the card in the chat or on its review page (deleting a deck, and an edit of a comment that has formatting: on the review page only). An edit of a description that has formatting is also approved on its review page only.
In every mode: a held change, a change waiting for another change first, one another app had a hand in, one that replaces another app's change, or one that another app's waiting change would replace waits for the member's press; its card and review page say why.
A change to one of your decks keeps a snapshot first. Deck and collection changes are read back to check them.

| Capability | The one tool | Writes? | Notes |
| --- | --- | --- | --- |
| Who is signed in; is Archidekt linked, are writes on | `whoami`, `account_status` | no | linking itself happens only in the browser (Account page) |
| List my decks (private included), filter by name, format, folder | `list_my_decks` | no | |
| Search public decks (name, commander, partial commander names, format, colours, owner), and one user's public decks (`owner`, newest first) | `search_decks` | no | hides Mystic Forge `archidekt_user_decks`; the web profile page `/users/<name>` is the hand path |
| Read any deck, the member's own private ones included: cards (type line, power/toughness, loyalty), zones, stats, plain list, Archidekt import text, picked with `view`; rules text, faces, flavour and artist on request | `get_deck` | no | hides Mystic Forge `archidekt_deck`, `archidekt_export`; `owner` says whose deck it is |
| Read a pasted list or an Archidekt CSV export | `parse_decklist`, `parse_deck_export` | no | |
| Statistics, legality, structural checks, bracket estimate; curve by type or category, land-only colour sources, pips outside the commander's identity, subtype and keyword counts (0.7.24: one legality engine, the deck page's, for every legality figure; the curve, average, grouping, sorting and draw odds use a card's custom mana value when the deck sets one; whether archidekt.com does the same in its own curve is not confirmed) | `deck_stats` | no | hides `validate_archidekt_deck` |
| Diff two decks, snapshots or lists (precon upgrades included), with an optional paired goldfish A/B | `compare_decks` | no | hides `precon_diff`, `goldfish_ab`; the A/B takes two Commander decks or two 60-card decks; the deck page's Compare view is the hand path |
| Solo test (300 games, no opponents) of one deck or pasted list, stored with its stats and validation (a pasted list is returned, not stored); trends across a deck's reports | `run_deck_report` (+ `list_deck_reports` with its `trend`, `get_deck_report`) | stores a report | hides `goldfish_run`; takes the simulator's options; a Commander deck plays from the command zone at 40 life, a deck without a commander and with 60 or more cards (Standard, Modern, Pioneer, Legacy, Vintage, Pauper...) plays a 60-card game (0.7.23: 20 life, London mulligan with no free one, on the play, sideboard left out); Forge games (Commander decks) seat precons of the deck's bracket and say why (0.7.23); the deck page's Run full test is the hand path, its History page draws the trends |
| Play more Forge games for a report whose games have finished, against the same opponents (on a comparison, both decks get the same number) | `play_more_games` | starts Forge games | the report page's Play more games button is the hand path, with the same rules: up to 50 games a report, and nothing starts when the member's Forge queue is full |
| Draw odds; what the engine models for a deck | `goldfish_odds`, `goldfish_annotate` (Mystic Forge) | no | annotations feed `run_deck_report`; the deck page's Probability of draw is the hand path to the same odds |
| Validate a pasted list that is not a deck yet | `validate_decklist` (Mystic Forge) | no | an Archidekt deck's checks are in `deck_stats` |
| Card lookups, rulings, prices, rules, EDHREC, combos, precon lists | the Mystic Forge research tools | no | the full list is in the tool reference |
| Turn names or card photos into exact printings | `resolve_cards`, `card_printings` | no | the picker card lets the person keep, drop or pick among suggestions; the printings card shows every printing's picture to tap (a form question in an app without cards) |
| Scan sessions (the Scan page's drafts) | `list_scan_sessions`, `get_scan_session`, `save_scan_session` | saves a draft | drafts stay in the gateway until used |
| Edit a deck's cards (main or maybeboard): counts, categories, commander, finish, printing, colour tags, custom mana value, Companion, show back face | `propose_deck_changes` | proposal (a Companion change waits for the member's press in every mode) | per-card notes are returned by `get_deck` but cannot be changed (see D) |
| Create a deck from cards, a list, a CSV, the gateway's JSON or a scan | `propose_new_deck` | proposal (high) | |
| Clone a deck | `propose_clone_deck` | proposal (low) | |
| Change a deck's name, description, format, bracket, privacy, Theorycrafted and Game type; move it to a folder, add or remove tags, set its cover | `propose_deck_details` | proposal (high) | applied with the same checked calls as the deck settings page |
| Create, delete and rename a deck's categories; switch Premier, Not in deck and Not in price; delete unused categories | `propose_deck_categories` | proposal (high) | the same change as the deck settings page's Categories panel; one that touches the commander, Not in deck, or a Sideboard or Maybeboard rename or delete waits for the member's press in every mode (and asks to confirm on the settings page too, as every delete does); Archidekt only deletes an empty category, so a category with cards names where they go and they move first, and the gateway sends a delete only after a fresh read shows it empty; Archidekt's default Sideboard or Maybeboard without a record gets one when changed; a backup copy and a snapshot first, read back after, Restore makes deleted categories again; tabs already open on Archidekt keep the old categories until reloaded |
| Undo: snapshots and restore (cards with their Companion and show back face, the deck's categories with their names and flags, deleted ones made again, and the deck's name, description, format, bracket, privacy, Theorycrafted and Game type) | `list_snapshots`, `get_snapshot`, `propose_restore_snapshot` | proposal (high) | |
| My collection: read; add or remove cards | `list_collection`, `propose_collection_changes` | proposal | in-chat card like a deck proposal; read back after applying |
| Like, vote down, bookmark a deck; follow its owner | `propose_deck_social` | proposal (consent) | the same calls as the deck page's buttons; never applied by the assistant in any mode |
| Read a deck's comments; post, reply, edit or delete your own, vote on others' | `get_deck_comments`, `propose_comment` | proposal (consent) | the full comment text is on the review card; never applied by the assistant in any mode |
| Delete one of your decks | `propose_delete_deck` | proposal (destructive) | never applied by the assistant in any mode; snapshot first, and a backup copy with backups on |
| Create a folder | `propose_create_folder` | proposal (consent) | never applied by the assistant in any mode; renaming a folder stays a hand action |
| See, add or remove a deck's collaborators (people who may edit it) | `get_deck_collaborators`, `propose_collaborator`; deck settings page | proposal (consent) | changes who can edit the deck, so it is an account action: the member approves the assistant's proposal on the card in the chat or on its review page, and it is never applied by the assistant in any mode (the member's own form on the settings page applies after one confirmation). Only people you follow can be added, as on Archidekt |
| Ask Archidekt's community for help with one of your decks on its deck help board (post, edit, delist) | `propose_deck_help` | proposal (consent) | public, under your name; Archidekt asks for a deck tag and a deck size that fits the format; listed for 7 days; never applied by the assistant in any mode |
| Report a deck, a card or custom card in it, or a comment on it to Archidekt's moderators | `propose_report` | proposal (consent) | your username goes with it and it can't be taken back; never applied by the assistant in any mode |
| Enter one of your decks in an Archidekt contest, or withdraw it | `propose_contest_entry` | proposal (consent) | public; as on Archidekt, entering replaces the deck's description with your summary, after a snapshot; never applied by the assistant in any mode |
| Your Archidekt profile and settings: bio, username, email and notification switches, new-device default settings, collection public or private; read your notifications | `get_my_profile`, `propose_profile_change`, `get_my_notifications` | proposal (consent) for changes | one change a proposal; never applied by the assistant in any mode; a profile picture is uploaded only on the Account page, by you; Archidekt has no way to mark notifications read |
| Your account-wide Archidekt lists: category templates, default categories, color tags, pinned cards, custom cards | `get_my_lists`, `propose_list_change` | proposal (low when small, with no deletes) | a delete always waits for your press; a custom card's picture is uploaded only on the My lists page, by you; the list as it was just before is kept with the change |
| Add one of your category templates to a deck as a new category | `propose_deck_categories` (`from_template`) | proposal (high) | the new category gets the template's switches |
| Card packages: make one, change its keywords, budget and formats, list yours, add one to a deck | `propose_new_deck` (`package`), `propose_deck_details` (`package`), `list_my_packages`, `propose_add_package` | proposals | adding a package makes ordinary card adds, each reviewed, and counts one import of the package on Archidekt when applied |
| A deck's tournament registration sheet (PDF) | `get_registration_pdf` | no | Archidekt fills the sheet; your name and DCI number go to Archidekt only and are not kept by the gateway |
| Watch cards (a name, or one printing and finish) and decks; their daily prices and values over time; price alerts | `watch_card`, `watch_deck`, `unwatch`, `list_watchlist`, `card_price_history` | the member's own watchlist (no proposal) | changes only the member's gateway data, never Archidekt; prices from Scryfall once a day; the Watchlist page is the hand path; only when the operator sets `MTG_WATCHLISTS_ENABLED=true` (off by default) |
| A plain summary of the last day or week: watched prices and deck values (when watchlists are on), reports finished, proposals waiting and applied | `get_summary` | no | written by the gateway without AI; an assistant's scheduled task can turn it into its own words ([USING.md](USING.md#notifications-and-summaries)) |
| Proposals: list, read, approve with the card's code, reject (with a reason on the card), apply | `list_my_proposals`, `get_proposal`, `confirm_proposal`, `reject_proposal`, `apply_proposal` | apply writes | `apply_proposal` succeeds only when the mode allows |
| Check a planned deck edit before proposing it: problems it would be refused for that can be found without the deck, the deck's waiting proposals it shares cards with, and whether to amend one of its own instead | `check_deck_changes` | read | no card in the chat, nothing saved, Archidekt not asked (a card name may be looked up on Scryfall); anything that needs the deck itself, such as a card missing from it or a deck that isn't the member's, is found only when the proposal is made |
| Change a waiting proposal in place, take back one of its own, or combine several of its own waiting card edits on one deck into one | `amend_proposal`, `withdraw_proposal`, `combine_proposals` | proposal | changes nothing on Archidekt; a proposal amended by another app than the one that made it always waits for the member's press; a withdrawn one stays visible; combined ones come back if the combined one is rejected |
| Show every waiting change (one deck or all), approve or reject each of this app's own, and apply several of them together | `show_waiting_changes` (+ `confirm_waiting` and `get_apply_result`, called only by the card) | Apply all writes | the waiting-changes card; `confirm_waiting` needs the card's one-time code, which the assistant never has; another app's changes are listed with their review link; the Proposals page is the hand path |
| Remove a saved card description (a card model the solo test kept) | `remove_card_model` | the member's own gateway data (no proposal) | changes nothing on Archidekt and leaves stored reports as they are |

**No assistant tool exists for:** renaming folders, linking or unlinking
Archidekt, approval modes, notification and summary settings, admin. Social actions and deleting a deck have
tools since 0.7.17, but only as proposals, and the person applies each one
with their own press, in every approval mode, on the card in the chat or on
its review page (deleting a deck, and an edit of a comment that has formatting: review page only): no mode,
setting or batch lets an assistant apply a like, vote, bookmark, follow,
comment, new folder, collaborator change or deck deletion. The gateway enforces this where proposals
are applied, not only in the pages.

## B. What a person does by hand (web pages and the Android app)

The Android app shows the same pages in a native shell with the phone camera;
everything here applies to both. Hand saves go to Archidekt at once (the
member's own action is the approval), each after a snapshot; only a restore,
a commander change or a big removal asks for an extra confirmation, and
deleting a deck asks for its name.

| Page | Actions |
| --- | --- |
| Home | newest decks with covers, search, tiles to every section |
| Decks | the linked account's decks (grid or list, filter, sort), New deck (name, pasted list, CSV, the gateway's .json, a chosen file, scan), Folders (create, rename) |
| One deck | views (text, table, brewer, stacks, scroll, grid), grouping, sorting, filter, your own stack order (drag a stack's handle or use the arrow keys; kept with your account, so every device shows it; Archidekt has no stack order; Reset puts the deck's order back), Select cards on your own deck to move, refinish, colour-tag or remove several in one save with one Undo (Color tag… and Mana value… are in the card menu too), the whole card on tap (image, every face's mana cost, type line, rules text, power/toughness or loyalty, flavour, printing, rarity, artist, price, salt, legal formats, custom mana value, the card's notes (read-only); Scryfall, mark owned, move category), drag cards between categories (0.7.24: saved to Archidekt on the drop, like the card menu and Select cards; every one of those saves has Undo and Redo: the buttons, the toast's Undo, and the editor's keys Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y; the Undo of a removal puts back every category, and removing a card with notes asks first, as notes cannot be written back), Quick add, Playtest (Archidekt's own playtester in a new tab, or the phone's browser from the app, where the person's Archidekt sign-in lives), Goldfish (0.7.23: play the deck by hand in the browser: shuffle, an opening hand of seven with London mulligans, draw, play to the battlefield, graveyard or exile, tap and untap, next turn; a Commander deck starts with its commander in the command zone; nothing is sent or kept), Play on EDHPlay (0.7.25: copies the whole deck in EDHPlay's import format, commander marked, companion and sideboard up to EDHPlay's 15 copies, and opens edhplay.com; the maybeboard and tokens are not sent and a note names any cut; see [EXPORT-IMPORT.md](EXPORT-IMPORT.md)), Run full test (0.7.30, was Run simulation: the same statistics, validation and 300-game solo test as `run_deck_report`, opened and filed under History), statistics with Probability of draw and Deck checks (curve by type or category, colours as bars or a pie, lands only, pips outside the commander's identity, subtypes and keywords; a click shows only those cards), description, comments (post, edit and delete your own), like, bookmark, follow the owner; More: Solo test only (when Forge is on: the same run without the Forge games), Settings, Compare with another deck (a precon from Archidekt's list, any deck or a pasted list; the same comparison as `compare_decks`), Export (Archidekt import text, plain text, sideboard, each with Copy; downloads as Archidekt .txt, plain .txt, .csv, .json, and one-way Arena .txt, MTGO .dek, PDF; see [EXPORT-IMPORT.md](EXPORT-IMPORT.md)), Open on Archidekt, Delete deck |
| Editor | quantities, categories, finish, printing, add cards (deck or maybeboard), maybeboard and sideboard rows, paste a list (Archidekt's syntax: printing, `*F*`/`*E*`, `[Category]`, `# Sideboard`), the whole card from a thumbnail, remove, undo, Save changes |
| Deck settings | name, format, bracket, description, private, unlisted; cover image; tags; folder; collaborators (add or remove, then confirm once; 0.7.24); Delete this deck (type its name) |
| Search | public decks by name, commander, format, colours, owner; a user's profile page; Precons by set |
| Scan | phone camera or photos to a draft; set lock, foil, printing picker; save as a new deck, into a deck, or to the collection |
| Collection | the Archidekt Collection: filter, sort, grid or list, add, quantity, per-row details (finish, condition, language, price paid), the whole card from a thumbnail, remove, CSV export, import from a CSV or a card list (pasted or a file) |
| Cards | every card matching the top bar's search, with its picture; the whole card on tap; Add to deck (copies and category), saved like an edit on the deck page |
| Proposals | review page with Approve, Apply, Reject; check several and Apply checked or Reject checked; the in-chat card opens the same proposal |
| History | proposals, snapshots (Restore), reports (open one in full) |
| Reports | every stored deck report, newest first (the newest 200 are kept), filter by deck, and what Forge is playing for you now |
| My activity | everything done in your name on this gateway: sign-ins, which assistant proposed and applied what, links and unlinks, and anything an administrator did to your account |
| Watchlist | watch cards (name, or set and number, finish) and decks; today's price and the change over 1, 7 and 30 days; a chart and a table of each price over time; price alerts; remove. The card viewer's Price history and Watch this card reach the same. Only when watchlists are on (off by default) |
| Account | who you are signed in as, whether Archidekt writes are on, what your sign-in shares, Get the assistant skill, Get the Android app (in the app: the App card with Check for app updates and Open a copied link), Sign out, Sign out on all my devices, approval mode, Connected apps (Disconnect), notifications in the Android app when the gateway has a notification server (on or off, which events, Get notifications on this phone, Disconnect my phones, Send test, and the summary: Off, Every day or Every week, with the latest one shown in the card), link or unlink Archidekt, Forget my view choices, delete my data; the site theme and layout are in the account menu |
| Admin (admin group only) | members (disable, revoke, unlink, delete data), activity, metrics; System card with the newest backup and backup copy and the research service's state |
| Guide | tutorials, how-to guides, reference (this map in the user's words) and explanation, with a contents rail and a search box |

## C. How the two paths relate

The assistant's path has **cards in the chat** wherever the person has to
pick or press (MCP Apps: Claude on the web, desktop and phones; ChatGPT as
reported): the proposal (Approve, Reject with a reason), the waiting changes
(Apply all), the printings of a card and the cards read from photos or a list.
Every pick on a card reaches the assistant as plain text, and the
assistant then proposes as usual; the only cards that act by themselves are the
proposal card, whose Approve and Reject (with an optional reason) both use its
one-time code, and the waiting-changes card (`show_waiting_changes`), which
lists every waiting change, approves or rejects each of the assistant's own,
and applies several in order after one confirmation with **Apply all**
(`confirm_waiting`, with the card's one-time code). Once a proposal approved on
its card, or a batch sent with Apply all, is applied or under way, the card
posts "Approved, go on" as the user's message where the app allows it, so the
assistant carries on; a row's own Approve on the waiting-changes card posts
nothing. Since 0.7.33 every other
answer (a deck, its statistics, a comparison, a deck list, a scan, the account)
is text alone.
An app without cards (Claude Code, older clients) gets the same text results,
and a form question for ambiguous names.

**How an assistant gets connected.** Every app adds the same connector (the
gateway's `/mcp` address); the guided setup at `/install` has the steps. Two
files bundle the connector with the MTG skill, both built for each gateway
and downloadable without signing in: the Claude plugin
(`/plugin/<name>.zip`, uploaded on claude.ai or installed in Claude Code) and
the ChatGPT plugin (`/plugin/<name>-chatgpt.zip`, Agent Plugins format).
OpenAI's help pages say workspace owners and admins on ChatGPT Business and
Enterprise upload the ChatGPT plugin in the admin console (reported, not yet
tested); individual Plus and Pro accounts have no plugin upload (checked on a
Pro account, 2026-10-11) and add the connector by hand, which gives the same
tools.

- **Shared backend, one set of rules.** A hand save and an applied proposal
  travel the same path: a proposal record, a snapshot, the Archidekt write,
  a read-back, an audit row. The review page and the in-chat card open the
  same proposal. Reports started from a deck's Run full test button (or
  Solo test only in its More menu) and from `run_deck_report` are the same
  reports on the same History page.
- **Only the assistant does:** research lookups (Scryfall, EDHREC, combos,
  rules), the paired goldfish A/B with the simulator's options, reading a card
  photo shared in the chat into names (the Scan page reads its own camera
  photos), proposing changes it worked out itself.
- **Only a person plays:** the deck page's Goldfish, a game played by hand in
  the browser. It is a playtable, not a tool result, so the assistant has no
  counterpart; its simulated games are `run_deck_report`. The solo test of
  one deck and the comparison of two are both paths: Run full test and
  Compare on the deck page for people, `run_deck_report` and
  `compare_decks` for the assistant, same engine and settings.
- **Only a person does:** link Archidekt and set approval modes, turn
  notifications and summaries on or off, approve a
  high-risk proposal in manual or semi-auto mode, approve a like, vote,
  bookmark, follow, comment, new folder, collaborator change or deck deletion (an assistant may
  propose these; the person approves each one on the card in the chat or on
  its review page, in every mode, and a deck deletion or an edit of a comment
  that has formatting only on its review page), rename folders, scan with the camera, administer members.
- **Both reach:** reading decks and collections, statistics, creating and
  editing decks, deck settings (folder, tags and cover included), cloning,
  restoring snapshots, collection changes, scan drafts. The person's path applies at once; the assistant's path is a
  proposal under the member's mode. Watching cards and decks and reading
  their prices and the summary are the same on both paths and need no
  proposal (they change nothing on Archidekt).

Nothing was orphaned by hiding the Mystic Forge duplicates: each hidden tool's
inputs and outputs are carried by its owner (0.7.2 added the simulator's
options, the paired A/B, rules text and Archidekt import text on a deck read,
structural checks in the statistics, and the precon-style summary in the
comparison so that this holds; 0.7.3 folded the separate own-deck reader into
`get_deck` and the separate profile tool into `search_decks`).

## Cards Forge cannot play

Forge, the game engine (on by default), plays 99.71% of the cards legal in Commander and 99.60% of the
cards legal in at least one format (Forge 2.0.15, measured against Scryfall on 2026-10-21; every
Forge image build measures it again and stops below 99.6% and 99.5%). What it lacks:

- **Unfinity sticker and Attraction cards** (94 cards): Forge does not support stickers or Attractions.
- **Arena-only Alchemy cards** (39 cards, none legal in Commander): Forge does not have the newest
  Alchemy sets.

A report names every card of the deck that Forge could not play, and the games run without it.

## D. Parked

Not built, and listed here so nothing is dropped silently. Each may still come
later; none is promised.

- **Moxfield import by link** (parked 2026-10-10, 0.7.19). Moxfield has no public API, and requests
  to it were refused from the build environment, so a link import could not be built or
  checked. A Moxfield export pasted as text imports today (New deck, the deck editor's paste
  box, or the assistant's `parse_decklist`).
- **Per-card notes can be read, not written** (0.7.20). Notes show in the card viewer and in
  `get_deck`. Archidekt's own deck editor no longer saves (or shows) per-card deck notes: its save
  request (`modifyCards/v2`) carries quantity, finish, custom mana value, companion, flip and colour
  tag but no notes, and no other deck route writes them (site code re-checked 2026-10-11, 0.7.23).
  Note text exists on Archidekt only for pinned cards and inside the playtester, so the gateway
  does not guess a route.
- **AI-written summaries by the gateway itself** (0.7.23). The gateway has no AI model and the
  project uses only free services, so it writes the summary with fixed sentences; a member's own
  assistant can write it in its words on a schedule (`get_summary`). Watchlists, price history and
  ntfy notifications shipped in 0.7.23.
