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

A missing or invalid credential answers `401` with `{"ok": false, "error": "unauthenticated",
"login": "/login"}`. A bearer caller outside the group gets `403` with `"error": "forbidden"`; a browser session outside the group counts as signed out (`401`).

Responses are JSON objects with `"ok": true` on success. Failures carry `ok: false`, an `error`
code and a human `message`:

| error | status | meaning |
|---|---|---|
| `invalid` | 400 | bad input |
| `not_found` | 404 | not yours, or does not exist |
| `forbidden` | 403 | the deck belongs to someone else |
| `writes_disabled` | 403 | `MTG_WRITES_ENABLED` is off |
| `browser_required` | 403 | this gateway applies proposals only on the review page (`review_url` is included) |
| `other_client` | 403 | the proposal was made by another connected app (or, for reject, in the browser); use the review page (`review_url` is included) |
| `not_linked` | 409 | no Archidekt account linked yet; send the user to `/account` |
| `not_pending` / `already_applied` / `stale` | 409 | the proposal cannot be applied in its current state |
| `apply_too_soon` | 425 | the proposal was created moments ago; apply needs a short pause |
| `rate_limited` | 429 | the Archidekt pacer is busy |
| `unavailable` | 503 | Archidekt or Mystic Forge is unreachable |

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
| `POST /api/v1/proposals/{pid}/apply` | Apply it: snapshot, Archidekt backup copy, write, re-read verification. Bearer callers get `browser_required` when `MTG_APPLY_VIA_MCP` is off |
| `POST /api/v1/proposals/{pid}/reject` | Mark a pending proposal rejected. A bearer caller can reject only proposals its own app made; others answer `other_client` |
| `GET /api/v1/snapshots?deck_id=` | Deck snapshots taken before applies (and on demand) |
| `GET /api/v1/snapshots/{sid}` | One snapshot with the full deck as it was |
| `GET /api/v1/reports?deck_id=` | Stored deck reports, newest first, with their trend `metrics` |
| `POST /api/v1/reports` | Run a report: `{"deck_id": "...", "simulate": true, "games": 300}`. Stats always; validation and goldfish simulation when Mystic Forge is configured. An unchanged deck within ten minutes returns the existing report with `reused: true` |
| `GET /api/v1/reports/{rid}` | One report with `stats`, `goldfish`, `validation` |
| `DELETE /api/v1/reports/{rid}` | Delete a report |
| `GET /api/v1/activity?limit=50` | The user's own audit trail |

Admin callers (members of `MTG_ADMIN_GROUP`) also have `GET /api/v1/admin/overview`,
`GET /api/v1/admin/users`, `GET /api/v1/admin/metrics` and
`POST /api/v1/admin/users/{sub}` with `{"action": "disable" | "enable" | "revoke" | "unlink"}`.
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

Server-rendered pages behind the Authentik sign-in (and `MTG_REQUIRED_GROUP`), laid out the way
Archidekt lays out its own (top bar, bottom tab bar on phones, deck banner, toolbar, text / stacks /
grid views). Light, Dark or System is a per-browser choice in the account menu (the `mtg_theme`
cookie, set by `POST /theme`); internal links never open a new tab.

| Path | Page |
|---|---|
| `/decks?q=&folder=&view=grid|list&order=` | My decks: filter by name or Archidekt folder, grid or list, order by updated / created / name / format, "New deck" |
| `/decks/open?ref=` | Redirects an Archidekt link or id to its deck page |
| `/decks/new` | New deck form (name, format, private, pasted list or CSV) that makes a `new_deck` proposal |
| `/decks/{id}?view=&group=&sort=&q=` | One deck: banner (art, legality, bracket, size, price, tags), toolbar (Quick add, View as text / stacks / grid, Group by, Sort by, local filter), cards, deck stats, description; owners get Edit deck, Clone deck and Deck settings |
| `/decks/{id}/edit?scan_session=&add=` | The editor: quantities, categories (new ones by typing), finish, printing (picker over Scryfall), additions with autocomplete and Undo become one proposal; a scan session or a Quick add name prefills it |
| `/decks/{id}/settings` | Deck settings (name, format, bracket, description, private, unlisted) as a `details` proposal |
| `/decks/{id}/clone` (POST) | A `clone` proposal for the deck |
| `/decks/{id}/report` (POST) | Run a deck report and open it |
| `/decks/{id}/export`, `.txt`, `.json`, `.csv` | Export as text, JSON or an Archidekt-style CSV |
| `/history?deck_id=` | Proposals, snapshots and reports over time, with restore |
| `/history/reports/{rid}` | One report |
| `/activity` | My activity |
| `/proposals`, `/proposals/{pid}` | Review and Apply, Reject |
| `/scan` | The card scanner (see SCANNING.md) |
| `/account`, `/login`, `/logout`, `/signed-out` | Account, Archidekt link, sign-in and sign-out |
| `/app`, `/app/mtg-assistant-gateway.apk` | The Android app page and download ([ANDROID.md](ANDROID.md)) |
| `/admin`, `/admin/users`, `/admin/activity`, `/admin/metrics` | Admin page (members of `MTG_ADMIN_GROUP`) |
| `/app.webmanifest`, `/sw.js`, `/static/{file}` | App shell |
| `/.well-known/assetlinks.json` | Android App Links statement from `MTG_ANDROID_ASSETLINKS` (empty list when unset) |

The pages keep the strict CSP (`default-src 'none'`); the deck page and editor allow card images
from `cards.scryfall.io` and `connect-src 'self'`, and the deck, deck list and editor pages allow
one script each from `/static`. Every control works without script.
