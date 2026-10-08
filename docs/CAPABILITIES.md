# Capability map

Everything a person can do with the gateway, by the path they take: asking an
AI assistant, or using the web pages and the Android app by hand. One table
per path, then how the two relate. Every feature appears exactly once as an
owned capability; nothing is reachable two ways by the assistant.

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
| Manual (default) | the member presses Approve or Apply | the member presses |
| Semi-auto | the assistant applies it (`apply_proposal`) | the member presses |
| Full-auto | the assistant applies it | the assistant applies it |

Low risk: up to 5 card rows, at most 4 copies moved per row, only add,
remove, quantity, category, finish or printing changes, no commander change;
cloning a deck. High risk: anything over those limits, a commander change, a
new deck, a snapshot restore, deck details. Every applied proposal keeps a
snapshot first and is read back to verify.

| Capability | The one tool | Writes? | Notes |
| --- | --- | --- | --- |
| Who is signed in; is Archidekt linked, are writes on | `whoami`, `account_status` | no | linking itself happens only in the browser (Account page) |
| List my decks (private included), filter by name, format, folder | `list_my_decks` | no | |
| Search public decks (name, commander, partial commander names, format, colours, owner), and one user's public decks (`owner`, newest first) | `search_decks` | no | hides Mystic Forge `archidekt_user_decks`; the web profile page `/users/<name>` is the hand path |
| Read any deck, the member's own private ones included: cards (type line, power/toughness, loyalty), zones, stats, plain list, Archidekt import text, picked with `view`; rules text, faces, flavour and artist on request | `get_deck` | no | hides Mystic Forge `archidekt_deck`, `archidekt_export`; `owner` says whose deck it is |
| Read a pasted list or an Archidekt CSV export | `parse_decklist`, `parse_deck_export` | no | |
| Statistics, legality, structural checks, bracket estimate | `deck_stats` | no | hides `validate_archidekt_deck` |
| Diff two decks, snapshots or lists (precon upgrades included), with an optional paired goldfish A/B | `compare_decks` | no | hides `precon_diff`, `goldfish_ab`; the deck page's Compare view is the hand path |
| Goldfish simulation of one deck or pasted list, stored with its stats and validation (a pasted list is returned, not stored) | `run_deck_report` (+ `list_deck_reports`, `get_deck_report`) | stores a report | hides `goldfish_run`; takes the simulator's options; the deck page's Run simulation is the hand path |
| Draw odds; what the engine models for a deck | `goldfish_odds`, `goldfish_annotate` (Mystic Forge) | no | annotations feed `run_deck_report`; the deck page's Probability of draw is the hand path to the same odds |
| Validate a pasted list that is not a deck yet | `validate_decklist` (Mystic Forge) | no | an Archidekt deck's checks are in `deck_stats` |
| Card lookups, rulings, prices, rules, EDHREC, combos, precon lists | the Mystic Forge research tools | no | the full list is in the tool reference |
| Turn names or card photos into exact printings | `resolve_cards`, `card_printings` | no | |
| Scan sessions (the Scan page's drafts) | `list_scan_sessions`, `get_scan_session`, `save_scan_session` | saves a draft | drafts stay in the gateway until used |
| Edit a deck's cards (main or maybeboard) | `propose_deck_changes` | proposal | |
| Create a deck from cards, a list, a CSV, the gateway's JSON or a scan | `propose_new_deck` | proposal (high) | |
| Clone a deck | `propose_clone_deck` | proposal (low) | |
| Change a deck's name, description, format, bracket, privacy; move it to a folder, add or remove tags, set its cover | `propose_deck_details` | proposal (high) | applied with the same verified calls as the deck settings page |
| Undo: snapshots and restore (cards and the deck's name, description, format, bracket, privacy) | `list_snapshots`, `get_snapshot`, `propose_restore_snapshot` | proposal (high) | |
| My collection: read; add or remove cards | `list_collection`, `propose_collection_changes` | proposal | in-chat card like a deck proposal; read back after applying |
| Proposals: list, read, approve with the card's code, reject, apply | `list_my_proposals`, `get_proposal`, `confirm_proposal`, `reject_proposal`, `apply_proposal` | apply writes | `apply_proposal` succeeds only when the mode allows |

**No assistant tool exists for:** deleting a deck, creating or renaming
folders, liking, bookmarking, following, commenting, editing a comment,
linking or unlinking Archidekt, approval modes, admin. Those are hand actions (below) by
design: either social (a person speaks for themselves) or destructive.

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
| One deck | views (text, stacks, grid), grouping, sorting, filter, the whole card on tap (image, every face's mana cost, type line, rules text, power/toughness or loyalty, flavour, printing, rarity, artist, price, salt, legal formats; Scryfall, mark owned, move category), drag cards between categories and Save moves, Quick add, Playtest (Archidekt's own playtester framed on a gateway page, web and app alike, with an Open on Archidekt fallback), Run simulation (the same statistics, validation and 300-game goldfish run as `run_deck_report`, opened and filed under History), statistics with Probability of draw and Deck checks, description, comments (post, edit and delete your own), like, bookmark, follow the owner; More: Settings, Compare with another deck (a precon from Archidekt's list, any deck or a pasted list; the same comparison as `compare_decks`), Export (Archidekt import text, plain text, sideboard, each with Copy; downloads as Archidekt .txt, plain .txt, .csv, .json, and one-way Arena .txt, MTGO .dek, PDF; see [EXPORT-IMPORT.md](EXPORT-IMPORT.md)), Open on Archidekt, Delete deck |
| Editor | quantities, categories, finish, printing, add cards (deck or maybeboard), maybeboard and sideboard rows, paste a list (Archidekt's syntax: printing, `*F*`/`*E*`, `[Category]`, `# Sideboard`), the whole card from a thumbnail, remove, undo, Save changes |
| Deck settings | name, format, bracket, description, private, unlisted; cover image; tags; folder; Delete this deck |
| Search | public decks by name, commander, format, colours, owner; a user's profile page; Precons by set |
| Scan | phone camera or photos to a draft; set lock, foil, printing picker; save as a new deck, into a deck, or to the collection |
| Collection | the Archidekt Collection: filter, sort, grid or list, add, quantity, per-row details (finish, condition, language, price paid), the whole card from a thumbnail, remove, CSV export, import from a CSV or a card list (pasted or a file) |
| Proposals | review page with Approve, Apply, Reject; the in-chat card opens the same proposal |
| History | proposals, snapshots (Restore), reports (open one in full) |
| Account | link or unlink Archidekt, approval mode, hand-edit confirmation, theme, sign out, delete my data |
| Admin (admin group only) | members (disable, revoke, unlink, delete data), activity, metrics; System card with the newest backup and backup copy and the research service's state |
| Guide | this map in the user's words, section by section |

## C. How the two paths relate

- **Shared backend, one set of rules.** A hand save and an applied proposal
  travel the same path: a proposal record, a snapshot, the Archidekt write,
  a read-back, an audit row. The review page and the in-chat card open the
  same proposal. Reports started from a deck's More menu and from
  `run_deck_report` are the same reports on the same History page.
- **Only the assistant does:** research lookups (Scryfall, EDHREC, combos,
  rules), the paired goldfish A/B with the simulator's options, reading card
  photos into names, proposing changes it worked out itself. The goldfish
  simulation of one deck and the comparison of two are both paths: Run
  simulation and Compare on the deck page for people, `run_deck_report` and
  `compare_decks` for the assistant, same engine and settings.
- **Only a person does:** link Archidekt and set approval modes, approve a
  high-risk proposal in manual or semi-auto mode, delete a deck, create and
  rename folders, like, bookmark, follow, comment, scan with the camera,
  administer members.
- **Both reach:** reading decks and collections, statistics, creating and
  editing decks, deck settings (folder, tags and cover included), cloning,
  restoring snapshots, collection changes, scan drafts. The person's path applies at once; the assistant's path is a
  proposal under the member's mode.

Nothing was orphaned by hiding the Mystic Forge duplicates: each hidden tool's
inputs and outputs are carried by its owner (0.7.2 added the simulator's
options, the paired A/B, rules text and Archidekt import text on a deck read,
structural checks in the statistics, and the precon-style summary in the
comparison so that this holds; 0.7.3 folded the separate own-deck reader into
`get_deck` and the separate profile tool into `search_decks`).
