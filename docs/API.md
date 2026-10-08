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
`"error": "insufficient_scope"`. The MCP tools that change something (`propose_*`, `apply_proposal`,
`confirm_proposal`, `reject_proposal`, `run_deck_report`, `save_scan_session`) refuse such a token
the same way. A token
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
| `browser_required` | 403 | this gateway applies proposals only on the review page or the in-chat card (`review_url` is included) |
| `browser_pending` | 409 | MCP only: the client opened the review page for the user (URL elicitation) but the proposal is still pending |
| `invalid_approval` | 403 | MCP only: `confirm_proposal` was called without the card's one-time code for this proposal, app and member; audited as `approval_refused` |
| `in_chat_disabled` | 403 | MCP only: `MTG_APPLY_IN_CHAT` is off, so the card cannot apply; use the review page |
| `insufficient_scope` | 403 | the bearer token is read-only (`mtg.read`) and the route writes |
| `other_account` | 409 | a new-deck proposal was made for another Archidekt account than the one linked now |
| `other_client` | 403 | the proposal was made by another connected app (or, for reject, in the browser); use the review page (`review_url` is included) |
| `not_linked` | 409 | no Archidekt account linked yet; send the user to `/account` |
| `not_pending` / `already_applied` / `stale` | 409 | the proposal cannot be applied in its current state |
| `rate_limited` | 429 | the Archidekt pacer is busy, the member's Archidekt budget (`MTG_ARCHIDEKT_CALLS_PER_10_MIN`) is used up, too many failed link attempts, or too many pending proposals (100 per member, 30 per app) |
| `unavailable` | 503 | Archidekt or Mystic Forge is unreachable |
| `idp_unavailable` | 503 | the identity provider can't be reached to confirm the caller is still a member; nothing was revoked, try again shortly (`Retry-After: 30`) |

Request bodies are JSON objects of at most 2 MB.

## Endpoints

| Method and path | What it returns or does |
|---|---|
| `GET /api/v1/me` | The signed-in user: `sub`, `name`, `preferred_username`, `email`, `groups`, `is_admin`, `account` (Archidekt link status), `via` (`api` or `browser`) |
| `GET /api/v1/decks?q=&format=&folder=` | The linked account's decks (`decks`, `count`), filtered by name substring, format name or folder |
| `GET /api/v1/decks/{id}?cards=0` | One deck (own or public) with cards, categories, format, description, bracket, commanders and `stats`; `cards=0` leaves the card list out |
| `GET /api/v1/decks/{id}/stats` | `deck` brief plus `stats` (curve, pips, types, lands, prices, salt, bracket estimate) |
| `GET /api/v1/decks/{id}/history` | Everything stored about one deck: `proposals`, `snapshots`, `reports` and a `series` of report metrics over time |
| `GET /api/v1/compare?a=&b=` | Differences between two decks; each side is a deck id or link, or a snapshot id |
| `GET /api/v1/proposals?state=` | The user's proposals, optionally filtered by state |
| `POST /api/v1/proposals` | Create a proposal (see kinds below). Answers `201` with the proposal, its `diff` and `review_url` |
| `GET /api/v1/proposals/{pid}` | One proposal with `state`, `diff`, `changes`, `snapshot_id`, `result`, `next_step` |
| `POST /api/v1/proposals/{pid}/apply` | Apply it: snapshot, Archidekt backup copy, write, re-read verification. A bearer caller applies only what the member's approval mode allows (`manual`: nothing, `browser_required`; `semi`: low-risk proposals; `auto`: everything), judged inside the apply; the in-chat card uses the `confirm_proposal` MCP tool instead, not this route |
| `POST /api/v1/proposals/{pid}/reject` | Mark a pending proposal rejected. A bearer caller can reject only proposals its own app made; others answer `other_client` |
| `GET /api/v1/snapshots?deck_id=` | Deck snapshots taken before applies (and on demand) |
| `GET /api/v1/snapshots/{sid}` | One snapshot with the full deck as it was |
| `GET /api/v1/reports?deck_id=` | Stored deck reports, newest first, with their trend `metrics` |
| `POST /api/v1/reports` | Run a report: `{"deck_id": "...", "simulate": true, "games": 300}`. Stats always; validation and goldfish simulation when Mystic Forge is configured. An unchanged deck within ten minutes returns the existing report with `reused: true` |
| `GET /api/v1/reports/{rid}` | One report with `stats`, `goldfish`, `validation` |
| `DELETE /api/v1/reports/{rid}` | Delete a report. With a bearer token only a report that same app ran; the browser session may delete any of the member's reports |
| `GET /api/v1/activity?limit=50` | The user's own audit trail |

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

Archidekt's social actions are browser-only too, under `/social/api`, and have no tool or `/api/v1`
counterpart on purpose: `POST /social/api/decks/{id}/vote` `{"vote": "up" | "down" | "none"}`
(a like is an up-vote on the deck's thread; answers the new `vote` and `points`), `POST
/social/api/decks/{id}/bookmark` `{"on": bool}`, `GET` and `POST /social/api/users/{id}/follow`
(`{"on": bool}`; the GET answers `following` and `self`), `GET /social/api/decks/{id}/comments?page=`
(the thread as nested `comments`) and `POST /social/api/decks/{id}/comments` `{"text", "parent"?}`
(`parent` must be a comment of that deck's thread). All need the session cookie and the CSRF token
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
  `{"action": "add", "card_name", "quantity", "category"?, "set_code"?, "collector_number"?, "finish"?}`,
  `{"action": "remove", "card_name"}`, `{"action": "set_quantity", "card_name", "quantity"}`,
  `{"action": "set_category", "card_name", "category"}`, `{"action": "set_commander", "card_name"}`,
  `{"action": "set_finish", "card_name", "finish"}` (normal, foil or etched, for the copies already in
  the deck) or `{"action": "set_printing", "card_name", "set_code", "collector_number", "finish"?}`
  (swap every copy for that printing, keeping quantity and categories).
  At most 40 changes; one card takes one kind of change (count, category or printing) per proposal.
- `new_deck`: `{"name", "format"?, "cards"? | "decklist_text"? | "csv_text"?, "private"?}`.
- `restore`: `{"snapshot_id"}` puts a deck back exactly as a snapshot recorded it.
- `details`: `{"deck_id", "details": {"name"?, "description"?, "deck_format"?, "edh_bracket"?, "private"?, "unlisted"?}}`.
- `clone`: `{"deck_id", "name"?}` copies one of your decks into a new private deck ("Copy of - …" by
  default), the way Archidekt's Clone deck button does.

Nothing changes on Archidekt until the proposal is applied, and every apply takes a snapshot first.

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
| `/decks?q=&folder=&view=grid|list&order=` | My decks: filter by name or Archidekt folder, grid or list, order by updated / created / name / format, "New deck", "Folders" |
| `/folders` (GET, POST `action=create|rename`) | The member's Archidekt folders: create one (`name`, `parent_id`) or rename one (`folder_id`, `name`); verified by re-reading the folder tree |
| `/precons?q=` | Archidekt's preconstructed decks grouped by set (cached an hour), filtered by set or deck name |
| `/decks/open?ref=` | Redirects an Archidekt link or id to its deck page |
| `/decks/new?scan_session=` | New deck form (name, format, private, pasted list or CSV) that makes a `new_deck` proposal; a scan session prefills the list |
| `/decks/{id}?view=&group=&sort=&q=` | One deck: banner (art, legality, bracket, size, price, tags), toolbar (Quick add, View as text / stacks / grid, Group by, Sort by, local filter), cards, deck stats, description; owners get Edit deck, Clone deck and Deck settings |
| `/decks/{id}/edit?scan_session=&add=` | The editor: quantities, categories (new ones by typing), finish, printing (picker over Scryfall), additions with autocomplete and Undo become one proposal; a scan session or a Quick add name prefills it |
| `/decks/{id}/settings` | Deck settings (name, format, bracket, description, private, unlisted) as a `details` proposal, plus the hand actions below |
| `/decks/{id}/cover` (POST `card`) | Set the cover image to a card of the deck (its Scryfall id) or back to Archidekt's automatic pick (empty); snapshot first, verified by re-reading |
| `/decks/{id}/tags` (POST `action=add name=` or `action=remove relation_id=`) | Add an Archidekt deck tag (reused if it exists, else created) or remove one; snapshot first, verified |
| `/decks/{id}/move` (POST `folder_id`) | Move the deck into one of the member's folders; verified |
| `/decks/{id}/delete` (GET, POST `name`) | Delete the deck after its exact name is typed; a gateway snapshot and, with backups on, an Archidekt backup copy are made first, and the deletion is verified by reading the deck back. Owners only; never a tool |
| `/decks/{id}/clone` (POST) | A `clone` proposal for the deck |
| `/decks/{id}/report` (POST) | Run a deck report and open it |
| `/decks/{id}/export`, `.txt`, `.json`, `.csv` | Export as text, JSON or an Archidekt-style CSV |
| `/history?deck_id=` | Proposals, snapshots and reports over time, with restore |
| `/history/reports/{rid}` | One report |
| `/activity` | My activity |
| `/proposals`, `/proposals/{pid}` | Review and Apply, Reject |
| `/scan` | The card scanner (see SCANNING.md) |
| `/account`, `/login`, `/logout`, `/signed-out` | Account, Archidekt link, sign-in and sign-out |
| `/account/avatar` | The signed-in member's picture (the provider's, else Gravatar, else initials) for the account menu |
| `/app`, `/app/mtg-assistant-gateway.apk` | The Android app page and download ([ANDROID.md](ANDROID.md)) |
| `/admin`, `/admin/users`, `/admin/activity`, `/admin/metrics` | Admin page (members of `MTG_ADMIN_GROUP`) |
| `/app.webmanifest`, `/sw.js`, `/static/{file}` | App shell |
| `/.well-known/assetlinks.json` | Android App Links statement from `MTG_ANDROID_ASSETLINKS` (empty list when unset) |

The pages keep the strict CSP (`default-src 'none'`); the deck page and editor allow card images
from `cards.scryfall.io` and `connect-src 'self'`, and the deck, deck list and editor pages allow
one script each from `/static`. Every control works without script.
