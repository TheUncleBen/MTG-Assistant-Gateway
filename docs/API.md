# JSON API and companion pages

The gateway has three front doors over one service layer: the MCP tools (for Claude and ChatGPT),
the companion pages (for a browser or the Android app), and this JSON API under `/api/v1`. All
three read and write through the same code, so a deck looks the same and a write follows the same
proposal → review → apply path whichever door it came through.

## Authentication

Every `/api/v1` call needs one of:

- **A gateway bearer token**: `Authorization: Bearer <token>` issued by the gateway's own OAuth
  flow (the same token an MCP client holds). The caller must be in `MTG_REQUIRED_GROUP` (unless the
  gateway runs with `MTG_ALLOW_ANY_IDP_USER=true` and no group). A bearer token is its own proof against cross-site requests, so no CSRF header is needed.
- **The browser session cookie** (`__Host-mtg_session`, or plain `mtg_session` on an `http://localhost` development URL; set by signing in at `/login`). Writes (`POST`,
  `DELETE`) then also need the `X-CSRF-Token` header. The token is the `csrf` hidden field every
  signed-in page carries (the sign-out form) and the `csrf` value in the editor's config block.

A bearer token whose scopes are all read-only (`mtg.read` or a bare `read`, for an app that asked
for `scope=mtg.read`) may call every `GET` route but none of the writes: they answer `403` with
`"error": "insufficient_scope"`. The MCP tools that change something refuse such a token the same
way: every `propose_*` tool (`propose_collection_changes` included), `apply_proposal`,
`confirm_proposal`, `reject_proposal`, `amend_proposal`, `withdraw_proposal`, `combine_proposals`,
`confirm_waiting`, `run_deck_report`, `play_more_games`, `remove_card_model`, `save_scan_session`,
`watch_card`, `watch_deck` and `unwatch`. A token
with the default scope `mtg`, with no scope, or with any scope that is not read-only (for example
`read write`) has full access, as before.

A missing or invalid credential answers `401` with `{"ok": false, "error": "unauthenticated",
"login": "/login"}`. Someone no longer in `MTG_REQUIRED_GROUP` gets the same `401`: before a request is served the gateway
asks the identity provider whether the caller is still a member (cached for `MTG_MEMBERSHIP_CHECK_TTL`
seconds) and revokes a removed member's tokens and browser sessions. When the identity provider can't be
reached, the request gets `503` with `"error": "idp_unavailable"` instead, and nothing is revoked.

Responses are JSON objects with `"ok": true` on success. Failures carry `ok: false`, an `error`
code and a human `message`:

| error | status | meaning |
|---|---|---|
| `invalid` | 400 | bad input |
| `not_found` | 404 | not yours, or does not exist |
| `forbidden` | 403 | the deck belongs to someone else |
| `writes_disabled` | 403 | `MTG_WRITES_ENABLED` is off |
| `browser_required` | 403 | the member's approval mode wants their own press for this proposal (review page or in-chat card); nothing was sent (`review_url` is included) |
| `browser_pending` | 409 | MCP only: the client opened the review page for the user (URL elicitation) but the proposal is still pending |
| `invalid_approval` | 403 | MCP only: `confirm_proposal` was called without the card's one-time code for this proposal, app and member; audited as `approval_refused` |
| `now_held` | 409 | MCP only: Approve on the card reached a change held for a reason that came up after the card showed it; nothing was sent, and the reply carries the hold (`status`, `status_reason`) so the card shows it |
| `in_chat_disabled` | 403 | MCP only: `MTG_APPLY_IN_CHAT` is off, so the card cannot apply; use the review page |
| `insufficient_scope` | 403 | the bearer token is read-only (`mtg.read`) and the route writes |
| `other_account` | 409 | a new-deck proposal was made for another Archidekt account than the one linked now |
| `other_client` | 403 | the proposal was made by another connected app (or, for reject, in the browser); use the review page (`review_url` is included) |
| `not_linked` | 409 | no Archidekt account linked yet; send the user to `/account` |
| `changed` | 409 | the proposal changed since the page showed it (the `version` sent is out of date); `review_url` and the current version are included |
| `not_pending` / `already_applied` / `stale` | 409 | the proposal cannot be applied in its current state |
| `rate_limited` | 429 | Archidekt asked the gateway to slow down, the member already has three Archidekt actions running or waiting, the member's Archidekt budget (`MTG_ARCHIDEKT_CALLS_PER_10_MIN`) is used up, the member already has two research calls running when a report is asked for, or too many pending proposals (100 per member) |
| `auth` | 409 | Archidekt asked for a sign-in on a deck read the gateway made without one: with no account linked, or after the member's session could not read the deck. A stored session Archidekt no longer accepts is otherwise `not_linked`. Linking an account happens only on the Account page, not through this API, and a username and password Archidekt refuses are reported on that page |
| `backup_failed` | 502 | the Archidekt backup copy could not be made before the change; nothing was changed and the proposal is still pending, so it can be applied again |
| `verify_mismatch` | 502 | Archidekt accepted the change, but reading it back shows it doesn't match; the message names what differs and, for a change to an existing deck, the snapshot kept from before it |
| `contract` | 502 | Archidekt refused the request with an unexpected error, or answered with something the gateway can't read |
| `unavailable` | 503 | Archidekt or Mystic Forge is unreachable |
| `idp_unavailable` | 503 | the identity provider can't be reached to confirm the caller is still a member; nothing was revoked, try again shortly (`Retry-After: 30`) |

Request bodies are JSON objects of at most 2 MB.

## Endpoints

| Method and path | What it returns or does |
|---|---|
| `GET /api/v1/me` | The signed-in user: `sub`, `name`, `preferred_username`, `email`, `groups`, `is_admin`, `account` (Archidekt link status), `via` (`api` or `browser`) |
| `GET /api/v1/decks?q=&format=&folder=` | The linked account's decks (`decks`, `count`), filtered by name substring, format name or folder |
| `GET /api/v1/decks/{id}?cards=0` | One deck (own or public) with cards, categories, format, description, bracket, commanders and `stats`; `outside_categories` lists the categories whose rows sit outside the deck (an add to the deck under one is refused; Card search adds such a card to the maybeboard or sideboard instead); `cards=0` leaves the card list out |
| `GET /api/v1/decks/{id}/stats` | `deck` brief plus `stats` (curve, pips, types, lands, prices, salt, bracket estimate) |
| `GET /api/decks/mine` | Browser session only: the signed-in member's deck list as server-rendered HTML for the Home and My decks pages while a cold list loads (`no-store`; backup copies left out) |
| `POST /api/v1/decks/{id}/edit` | Browser session only (CSRF header): the deck page's own save. `{"changes": [...], "confirmed": false}` proposes, applies with a snapshot and answers the touched rows (with every category and the read-only notes), fresh checks and the re-rendered Legality chip and Deck checks panel; a change that asks first (a big removal, a commander change, or fewer copies of a card that has a row in the commander zone and another row, both in the deck or both in the maybeboard or sideboard) answers `needs_confirm` with `why`, `proposal_id` and `version`, and `{"proposal_id": ..., "version": n}` applies that version (without a version `400 invalid`, "Send the version of the proposal the page showed."; a version that is not a whole number `400 invalid` too; one changed since `409 changed`, with nothing sent, and the page says so; a cancelled confirmation rejects it; only a pending edit of this deck made in the member's browser session is accepted, never an assistant's or another app's). `{"refresh": true, "names": [...]}` reads the deck again, writing nothing |
| `GET /api/v1/decks/{id}/history` | Everything stored about one deck: `proposals`, `snapshots`, `reports` and a `series` of report metrics over time |
| `GET /api/v1/compare?a=&b=` | Differences between two decks; each side is a deck id or link, or a snapshot id |
| `GET /api/v1/proposals?state=` | The user's proposals, optionally filtered by state |
| `POST /api/v1/proposals` | Create a proposal (see kinds below). Answers `201` with the proposal, its `diff` and `review_url`. The browser session may add `apply: true` and `archidekt_backup: false` (skip the extra backup copy on Archidekt for that save; the gateway's own snapshot is always kept) |
| `GET /api/v1/proposals/{pid}` | One proposal with `state`, `diff`, `changes`, `snapshot_id`, `result`, `next_step` |
| `POST /api/v1/proposals/{pid}/apply` | Apply it. A change to one of the member's existing decks gets a snapshot, an Archidekt backup copy (with backups on), a re-check, the write and a check by reading it back; a new deck, a clone or a collection change is written and then read back. A bearer caller applies only what the member's approval mode allows (`manual`: nothing, `browser_required`; `semi`: low-risk proposals; `auto`: everything except what Full auto still leaves to the member, listed in OPERATIONS.md under Approval modes), judged inside the apply. The browser session must send `{"version": n}`, the version of the proposal the page showed: without it `400 invalid` ("Send the version of the proposal the page showed."), with one that isn't a whole number `400 invalid` ("version must be a whole number"), and with an older one `409 changed` with `review_url` and the current version. A bearer caller may send the version too and gets the same `409 changed` when it is out of date, but needn't. Another member's proposal is `404 not_found`, checked before a missing or out-of-date version. The in-chat card uses the `confirm_proposal` MCP tool instead, not this route. The deck editor's own save that asked first may also send `archidekt_backup: false` (browser session only), and then gets no backup copy. The field is honoured only for the member's own saves: an assistant's proposal keeps its copy. A member's own save that skips the copy (from the deck page, Card search, or the deck editor with its box unticked) keeps that choice when it waits and is applied later, unless an app amended it in the meantime. An Archidekt account action (a proposal of kind `action`) is never applied here, with a bearer token or the browser session: `403 browser_required` with its `review_url` and `risk` (only the member's own press applies one, with Apply on the review page or Approve on the in-chat card; a deck deletion, and an edit of a comment that has formatting, only on the review page) |
| `POST /api/v1/proposals/{pid}/reject` | Mark a pending proposal rejected. A bearer caller can reject only proposals its own app made; others answer `other_client` |
| `GET /api/v1/snapshots?deck_id=` | Deck snapshots taken before applies (and on demand) |
| `GET /api/v1/snapshots/{sid}` | One snapshot with the full deck as it was |
| `GET /api/v1/reports?deck_id=` | Stored deck reports, newest first, with their trend `metrics` |
| `POST /api/v1/reports` | Run a report: `{"deck_id": "...", "simulate": true, "games": 300}`. Stats always; validation and goldfish simulation when Mystic Forge is configured. `"forge": false` leaves the Forge games out (default: as `simulate`); a 60-card deck, or one with no commander marked, never runs Forge. `"same_opponents_as": "<report id>"` seats another of your reports' Forge opponents; `"compare_with": "<deck id>"` picks the precons for the higher of this deck's and that deck's brackets (use it on the first of two reports, then `same_opponents_as` on the second; with `"forge": false`, no Forge on the gateway, or a deck with no commander, the report is refused; the other deck is read first, and if it can't be read no report is made). Give `compare_with` or `same_opponents_as`, not both. An unchanged deck within ten minutes returns the existing report with `reused: true`. A report has `status` (`queued`, `running`, `complete`, `partial`, `failed`), `engine_label` and its run `settings` |
| `GET /api/v1/reports/{rid}` | One report with `stats`, `goldfish`, `validation` |
| `GET /api/v1/reports/{rid}/forge` | A report's Forge run alone while it plays: its state, games done of games asked for, the report's status. Reads the gateway only |
| `POST /api/v1/reports/{rid}/forge/more` | Play more games, as the report page's button and the `play_more_games` tool do, with the same rules: once the report's Forge games have finished, the games-per-report number again against the same opponents, up to 50 Forge games a report; it queues like any report and starts nothing when 2 of your reports already wait (on a comparison, which needs room for 2, when any already waits); on a comparison the other deck's report gets the same number. `202` with the report as it now is (`forge.more_note` when the games did not start); a refusal is `400` with `message` and `reason` (`queue`, `full`, `ended`, `none`, `noforge`, `pairwait`, `paircant`, `pairqueue`, `setup`), and a report that is not yours is `404` with `reason` `notfound`. Never asks Archidekt; a read-only (`mtg.read`) token gets `403` `insufficient_scope` |
| `DELETE /api/v1/reports/{rid}` | Delete a report. With a bearer token only a report that same app ran; the browser session may delete any of the member's reports |
| `GET /api/v1/activity?limit=50` | The user's own audit trail |

The cards an AI app shows in the chat load their bulky data from `GET /cards/data/{token}`, outside
`/api/v1` and outside the session: the token (a ten-minute Fernet token the gateway hands to the
card in a tool result's `_meta`) names the member, the kind (`deck`, `snapshot`, `printings`) and the
id, and the answer is that member's own data as JSON (`Access-Control-Allow-Origin: *`, no cookies,
`Cache-Control: private, no-store`). An expired, forged or used-up token (twenty fetches) answers 404 `expired`; a member who
was disabled or removed from the group since, 403 `forbidden`; a deck, snapshot or card that no longer
exists or is not that member's, 404 `not_found`; the member's Archidekt budget used up, 429
`rate_limited`; Archidekt or Scryfall unreachable, 503 `unavailable` (503 `not_linked` when the link
needs an Archidekt account that was unlinked since). Error messages are the gateway's own fixed words. The route exists only with
`MTG_APPLY_IN_CHAT` on. It is not a public API: the token is the only credential and it cannot be
requested.

The collection is the member's own Archidekt Collection, read and written through their linked
session; the gateway stores none of it. Its small JSON API serves the pages and the Android app,
under the browser session only (cookie plus `X-CSRF-Token` on writes; a bearer token uses the
`*_collection` tools instead): `POST /collection/api/add` with `{"items": [{"name" | "scryfall_id"
| "card": {...}, "quantity", "finish" | "foil", "condition"}], "source": "scan" | "manual",
"scan_session"?}` answers `added` and `skipped` (one or two Archidekt calls per card, paced about a
second apart; at most 100 cards a call; a `scan_session` given with `source: "scan"` is deleted
once saved); `POST /collection/api/rows/{id}` with `{"quantity": n}` (0 removes) or `finish` /
`condition`; `DELETE /collection/api/rows/{id}`; `GET /collection/api/summary` (`rows`, and
`cards` when the collection fits one page). Ids are Archidekt's record ids; a record that is not
in the member's own collection answers `404`, and a member without a linked account `409`
`not_linked`.

Archidekt's social actions for the browser are under `/social/api` (an assistant proposes the same
through `propose_deck_social` and `propose_comment`; there is no `/api/v1` counterpart): `POST /social/api/decks/{id}/vote` `{"vote": "up" | "down" | "none"}`
(a like is an up-vote on the deck's thread; answers the new `vote` and `points`), `POST
/social/api/decks/{id}/bookmark` `{"on": bool}`, `GET` and `POST /social/api/users/{id}/follow`
(`{"on": bool}`; the GET answers `following` and `self`), `GET /social/api/decks/{id}/comments?page=`
(the thread as nested `comments`) and `POST /social/api/decks/{id}/comments` `{"text", "parent"?}`
(`parent` must be a comment of that deck's thread), and `POST
/social/api/decks/{id}/comments/{comment_id}/vote` `{"vote": "up" | "down" | "none"}` on someone
else's comment (answers `vote` and the comment's new `points`). All need the session cookie and the CSRF token
and run under the member's own Archidekt session.

Admin callers (members of `MTG_ADMIN_GROUP`) also have `GET /api/v1/admin/overview`,
`GET /api/v1/admin/users`, `GET /api/v1/admin/metrics` and
`POST /api/v1/admin/users/{sub}` with `{"action": "disable" | "enable" | "revoke" | "unlink" | "delete_data"}`.
`delete_data` also needs `"confirm": true` and is refused for your own account.
The admin endpoints accept only the browser session cookie (with `X-CSRF-Token` on `POST`), not
a bearer token. They answer `401` without a browser session, and `404` to signed-in non-admins
(and to everyone while `MTG_ADMIN_GROUP` is unset).

### Proposal kinds

`POST /api/v1/proposals` takes a `kind`:

- `edit` (default): `{"deck_id": "...", "changes": [...]}`. Each change is one of
  `{"action": "add", "card_name", "quantity", "category"?, "categories"?, "set_code"?, "collector_number"?, "finish"?}`
  (`categories`: every category the new row goes in, the first one being `category`; the deck page's
  Undo of a removal sends it),
  `{"action": "remove", "card_name"}`, `{"action": "set_quantity", "card_name", "quantity"}`,
  `{"action": "set_category", "card_name", "category", "categories"?}` (`categories` as for add: the
  row goes under exactly those; the deck page's Undo of a move sends it), `{"action": "set_commander", "card_name"}`,
  `{"action": "set_finish", "card_name", "finish"}` (normal, foil or etched, for the copies already in
  the deck) or `{"action": "set_printing", "card_name", "set_code", "collector_number", "finish"?}`
  (swap every copy for that printing, keeping quantity and categories), or
  `{"action": "set_label", "card_name", "label", "color"?, "zone"?}` (Archidekt's colour tag on every
  row of the card; `color` is `#rrggbb`, an empty `label` takes the tag off), or
  `{"action": "set_mana_value", "card_name", "mana_value", "zone"?}` (Archidekt's custom mana value,
  0 to 15, on every row of the card; `null` takes it off).
  At most 40 changes; one card takes one kind of change (count, category or printing) per proposal.
- `new_deck`: `{"name", "format"?, "cards"? | "decklist_text"? | "csv_text"?, "private"?}`.
- `restore`: `{"snapshot_id"}` puts a deck back exactly as a snapshot recorded it.
- `details`: `{"deck_id", "details": {"name"?, "description"?, "deck_format"?, "edh_bracket"?, "private"?, "unlisted"?, "folder"?, "add_tags"?, "remove_tags"?, "cover"?}}`.
  `folder` is an existing folder's name (`""` or `"top"` for the top level), the tags are lists of
  names, `cover` is the name of a card in the deck.
- `clone`: `{"deck_id", "name"?}` copies one of your decks into a new private deck ("Copy of - …" by
  default), the way Archidekt's Clone deck button does.

Nothing changes on Archidekt until the proposal is applied, and an apply to one of the member's existing decks takes a snapshot first.

## Companion pages

Server-rendered pages behind the identity provider sign-in (and `MTG_REQUIRED_GROUP`), laid out the way
Archidekt lays out its own (top bar, bottom tab bar on phones, deck banner, toolbar, text / stacks /
grid views). Light, Dark or System is a per-browser choice in the account menu (the `mtg_theme`
cookie, set by `POST /theme`); internal links never open a new tab.

| Path | Page |
|---|---|
| `/` | Home: newest decks with covers, a deck search box, one tile per section (with the count of pending proposals), the assistant connection details |
| `/search?name=&commander=&owner=&format=&colors=&order=&page=` | Public deck search on Archidekt (name substring, commander, owner, format, colour identity; order `-updatedAt`, `-createdAt`, `-viewCount`, `-size`, `edhBracket`; Archidekt pages of 60) |
| `/users/{username}` | One Archidekt user's public decks |
| `/collection?q=&sort=added|edition&view=grid|list&page=` | My collection: the member's Archidekt Collection, filtered by name, grid or list, plus and minus, remove, add by name, a details menu per row (finish, condition, language, price paid; `action=details`); `/collection/export.csv` downloads it |
| `/guide` | The in-app guide (end-user documentation) |
| `/cards?q=` | Card search by name (picture, mana cost, type), each card in the card viewer, and an add to one of the member's decks; `/cards/api/text` and `/cards/api/rulings` feed the viewer |
| `/watchlist` (GET, POST) | The member's card watchlist and price history; only when `MTG_WATCHLISTS_ENABLED=true` |
| `/install`, `/install.md` | The setup hub: connecting Claude and ChatGPT, the skill, the plugins and the Android app (`?for=` opens one section) |
| `/skill`, `/skill/mtg-gateway.zip` | `/skill` redirects to `/install?for=skill`; the ZIP is the end-user skill |
| `/plugin/marketplace.json`, `/plugin/{name}.zip`, `/plugin/{name}-chatgpt.zip` | The Claude plugin marketplace and plugin, and the ChatGPT plugin ZIP ([PLUGIN.md](PLUGIN.md)) |
| `/decks?q=&folder=&view=grid|list&order=` | My decks: filter by name or Archidekt folder, grid or list, order by updated / created / name / format, "New deck", "Folders" |
| `/folders` (GET, POST `action=create|rename`) | The member's Archidekt folders: create one (`name`, `parent_id`) or rename one (`folder_id`, `name`); checked by reading the folder tree |
| `/precons?q=` | Archidekt's preconstructed decks grouped by set (cached an hour), filtered by set or deck name |
| `/decks/open?ref=` | Redirects an Archidekt link or id to its deck page |
| `/decks/new?scan_session=` | New deck form (name, format, private, pasted list or CSV) that makes a `new_deck` proposal; a scan session prefills the list |
| `/decks/{id}?view=&group=&sort=&q=` | One deck: banner (art, legality, bracket, size, price, tags), toolbar (Quick add, View as text / stacks / grid, Group by, Sort by, local filter), cards, deck stats, description; owners get Edit deck, Clone deck and Deck settings |
| `/decks/{id}/edit?scan_session=&add=` | The editor: quantities, categories (new ones by typing), finish, printing (picker over Scryfall), additions with instant suggestions (picture, mana cost, type) and Undo become one proposal; a scan session or a Quick add name prefills it |
| `/decks/{id}/settings` | Deck settings (name, format, bracket, description, private, unlisted) as a `details` proposal, plus the hand actions below |
| `/decks/{id}/cover` (POST `card`) | Set the cover image to a card of the deck (its Scryfall id) or back to Archidekt's automatic pick (empty); snapshot first, checked by reading it back |
| `/decks/{id}/tags` (POST `action=add name=` or `action=remove relation_id=`) | Add an Archidekt deck tag (reused if it exists, else created) or remove one; snapshot first, checked by reading it back |
| `/decks/{id}/move` (POST `folder_id`) | Move the deck into one of the member's folders; checked by reading it back |
| `/decks/{id}/delete` (GET, POST `name`) | Delete the deck after its exact name is typed; a gateway snapshot and, with backups on, an Archidekt backup copy are made first, and the deletion is checked by reading the deck back. Owners only; never a tool |
| `/decks/{id}/clone` (POST) | A `clone` proposal for the deck |
| `/decks/{id}/report` (POST) | Run a deck report and open it |
| `/decks/{id}/export`, `export.archidekt.txt`, `export.txt`, `export.csv`, `export.json`, `export.arena.txt`, `export.dek`, `export.pdf` | The Export page and its downloads: Archidekt import text, plain text, an Archidekt-style CSV, JSON, and the one-way Arena, MTGO and PDF files ([EXPORT-IMPORT.md](EXPORT-IMPORT.md)) |
| `/decks/{id}/goldfish` | Goldfish: play the deck by hand in the browser; nothing is sent or kept |
| `/decks/{id}/compare` | Compare the deck with a precon, another deck or a pasted list |
| `/decks/{id}/collaborators` (POST) | Add or remove a deck collaborator by hand, after a confirmation page |
| `/history?deck_id=` | Proposals, snapshots and reports over time, with restore; `type=`, `state=`, `q=`, `when=` (today, 7d, 30d, 90d, year: UTC days) and `group=` (day or deck) narrow and arrange the page, `offset=` pages it |
| `POST /history/restore` | Browser session (CSRF): makes a restore proposal for `snapshot_id` and opens its review page |
| `/history/reports/{rid}` | One report, designed: headline tiles, trends, the research service's text; `/history/reports/{rid}/export.md` and `.html` download it, and Copy puts the Markdown on the clipboard |
| `POST /layout` | Browser session (CSRF): the "Desktop layout" / "Fit the screen" switch, remembered per device in a cookie |
| `/activity` | My activity |
| `/proposals`, `/proposals/{pid}` | Review and Apply, Reject |
| `/scan` | The card scanner (see SCANNING.md) |
| `/account`, `/login`, `/logout`, `/signed-out`, `/data-deleted` | Account, Archidekt link, sign-in and sign-out, and the page shown after a member deletes their data |
| `POST /login/app` | The Android app finishing a sign-in made in the phone's browser: a one-time code and the app's secret ([ANDROID.md](ANDROID.md#13-privacy-and-security-notes)) |
| `/account/avatar` | The signed-in member's picture (the provider's, else Gravatar, else initials) for the account menu |
| `/app`, `/app/mtg-assistant-gateway.apk`, `/app/latest.json` | `/app` redirects to `/install?for=android`; the APK download and the version file the app checks for updates ([ANDROID.md](ANDROID.md)) |
| `/admin`, `/admin/users`, `/admin/activity`, `/admin/metrics` | Admin page (members of `MTG_ADMIN_GROUP`) |
| `/app.webmanifest`, `/sw.js`, `/static/{file}` | App shell |
| `/.well-known/assetlinks.json` | Android App Links statement from `MTG_ANDROID_ASSETLINKS` (empty list when unset) |

The pages keep the strict CSP (`default-src 'none'`); the deck page and editor allow card images
from `cards.scryfall.io` and `connect-src 'self'`, and the deck, deck list and editor pages allow
one script each from `/static`. Every control works without script.
