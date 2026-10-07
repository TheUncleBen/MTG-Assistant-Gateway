# MTG Assistant Gateway tool catalogue

Every tool the gateway exposes, grouped by purpose. Your client also shows
each tool's full input schema; when the schema and this page disagree, follow
the schema.

## Gateway tools

These run inside the gateway, as the signed-in user. Write-type results are
JSON with `"ok": true` or `"ok": false` plus `error` and `message`.

| Tool | Arguments | What it does |
| --- | --- | --- |
| `whoami` | none | The signed-in identity: `name`, `preferred_username`, `email`, `groups`, plus the token's client and expiry and the `gateway_version`. |
| `account_status` | none | `linked`, `archidekt_username`, `linked_at`, `last_used_at`, `writes_enabled`, `account_page` (the URL where the user links Archidekt). |
| `list_my_decks` | `name_contains`, `deck_format`, `folder` (all optional) | Up to 50 decks owned by the linked Archidekt account, most recently updated first, each with its `url`, plus `count`. `name_contains` keeps decks whose name contains the text (case-insensitive), `deck_format` keeps one format name (`commander`, `modern`...), `folder` keeps one folder name exactly. Filters apply after the read, so they never widen it. |
| `get_my_deck` | `deck_id` | One deck owned by the linked account (private decks included); refused with `forbidden` for anyone else's deck. `deck_id` may be the number or the full Archidekt deck URL. Returns the deck fields listed under "Deck results" below. |
| `get_deck` | `deck_ref` | Any public or unlisted Archidekt deck by id or URL, without a linked account; falls back to the user's linked account for their own private decks. Same deck fields as `get_my_deck`. |
| `parse_decklist` | `text` | Parses pasted decklist text (`1 Sol Ring`, `1x Sol Ring (cmr) 436 [Ramp]`, section headers, `SB:` lines; up to 200 kB) into `cards`, `card_count`, `sideboard_count`, a normalised `decklist_text` and a `sideboard_text`. Contacts no service. |
| `parse_deck_export` | `csv_text` | Parses the text of an Archidekt CSV export (any column selection, up to 2 MB) into cards with quantity, name, set, collector number, categories, mana cost, mana value, types, price and owned flag, plus `card_count` (deck proper), `side_count` (rows in the Maybeboard or Sideboard category), `distinct`, `categories`, `decklist_text` and `sideboard_text`. Does not contact Archidekt. |
| `propose_deck_changes` | `deck_id`, `changes`, `scan_session` (optional) | Step 1 of an edit, for decks the linked account owns (`forbidden` otherwise). Stores a proposal against the deck's current state and returns `proposal_id`, `kind`, `diff`, `state`, `review_url`, `writes_enabled`, `next_step`. `scan_session` (a scan session id or name) appends every resolved card of that session as `add` changes, after any `changes` given. Changes nothing on Archidekt. |
| `propose_new_deck` | `name`, `deck_format`, `private`, and one of `cards`, `decklist_text`, `csv_text`, `scan_session` | Step 1 of creating a deck in the linked account. `scan_session` (id or name) uses that scan session's `decklist_text` as the source. Same return fields as `propose_deck_changes`. Creates nothing on Archidekt. |
| `list_my_proposals` | none | This user's recent proposals with their states and review links. |
| `get_proposal` | `proposal_id` | One proposal: diff, state, result, snapshot id, review link. |
| `reject_proposal` | `proposal_id` | Closes one of the user's own `pending` proposals without applying it; nothing is sent to Archidekt. Only a proposal this app made can be rejected here; any other answers `other_client` with the `review_url`, where the user can reject it. Returns the proposal as `get_proposal` would, now `rejected`. A proposal that is no longer pending answers `not_pending` with its current state. Only the user's own message can ask for this. |
| `list_snapshots` | none | The snapshots kept of this user's decks (one before every applied edit), newest first: `snapshot_id`, `deck_id`, `deck_name`, `proposal_id`, `taken_at`, `card_count`, and `backup_deck_id` / `backup_url` of the readable backup copy kept in the user's Archidekt backup folder (null when the gateway runs without backups). |
| `get_snapshot` | `snapshot_id` | One snapshot in full: the deck fields of `get_deck` as the snapshot recorded them, plus `snapshot_id`, `proposal_id`, `taken_at` and `backup_url`. Contacts nothing; the snapshot is read from the gateway's database. Its id (`snap_...`) is also accepted by `deck_stats` and `compare_decks`. |
| `propose_restore_snapshot` | `snapshot_id` | Step 1 of an undo: a proposal (`kind` `restore`) that puts every deck row back as the snapshot recorded it: printing, foil or etched finish, quantity and categories (so commander, sideboard and maybeboard too). Deck name, description, format and custom-category settings are not touched. Same return fields as `propose_deck_changes`; the user confirms, then `apply_proposal` applies it like any other proposal. Changes nothing on Archidekt. |
| `propose_deck_details` | `deck_id`, `details` | Step 1 of changing a deck's own settings rather than its cards, for decks the linked account owns. `details` is an object with any of `name` (1 to 200 characters), `description` (plain text, up to 20000 characters), `deck_format` (the same names as `propose_new_deck`), `edh_bracket` (1 to 5, or `null` to clear it), `private` and `unlisted` (booleans). Fields the deck already has that way are dropped; a proposal that would change nothing is refused with `invalid`. Returns the same fields as `propose_deck_changes` with `kind` `details` and a before/after diff (`name: "Old" -> "New"`, `format: commander -> modern`, `description: (changed, 120 chars)`; the description text itself is never printed). The user confirms, then `apply_proposal` applies it like any other proposal: snapshot and backup first, one update call, then the deck is re-read and every field checked. Changes nothing on Archidekt. |
| `propose_clone_deck` | `deck_id`, `name` (optional) | Step 1 of copying one of the linked account's decks into a new private deck, as Archidekt's Clone deck button does; `name` defaults to `Copy of - <deck name>`. Returns the same fields as `propose_deck_changes` with `kind` `clone`. After the user confirms, `apply_proposal` makes the copy (every card, quantity, category and finish) and returns the new deck's `deck_id` and `deck_url`. Creates nothing on Archidekt by itself. |
| `apply_proposal` | `proposal_id` | Step 2: **writes to Archidekt.** Refused while writes are disabled. By default it is also refused with `browser_required` plus the `review_url`, because the gateway applies only from the browser review page; the owner can allow it with `MTG_APPLY_VIA_MCP`. A proposal another app (or the browser) made answers `other_client` with the `review_url`. When allowed, a proposal younger than `MTG_APPLY_MIN_AGE_SECONDS` (default 15) is refused with `apply_too_soon` and `retry_after_seconds`; nothing is sent. Otherwise: For an edit it re-checks the deck is unchanged, snapshots it, applies, re-reads and verifies. For a new deck it creates the deck, adds the cards, re-reads and verifies, and returns `deck_id`, `deck_url` and `printing_notes` (a line per listed printing Archidekt did not have, which was added by name instead; tell the user) in `result`. A pinned printing on an edit that Archidekt does not have, or that is another card, refuses the whole proposal with `not_found`; nothing is changed. |
| `deck_stats` | `deck_ref` | Statistics for one deck (the deck proper only): `card_count`, `distinct`, `land_count`, `nonland_count`, `average_mana_value`, `mana_curve` (buckets `0` to `6` and `7+`, nonlands), `colour_pips` against `mana_sources`, `type_counts`, `rarity_counts`, `price_total` and `priced_cards`, `format` with `legality_problems` and `legality_unknown`, `game_changers`, `tutors`, `extra_turns`, `mass_land_denial`, `salt_total`, `commanders`, `colour_identity`, `archidekt_bracket` (the bracket set on the deck, if any) and `bracket_estimate` (`bracket` 2 to 4 or null, `kind: "estimate"`, `basis`). The estimate comes only from Archidekt's own card flags: mass land denial or two or more extra-turn cards means 4, more than three game changers means 4, one to three game changers or a two-card combo means 3, otherwise 2; bracket 5 is never estimated. It is an estimate, not an official bracket; say so. Prices, salt and the flags are Archidekt's figures, not checked by the gateway. `deck_ref` is an Archidekt id or URL (read like `get_deck`) or a snapshot id. Returns `deck` (brief) and `stats`. Reads Archidekt only; no Mystic Forge call. |
| `compare_decks` | `a`, `b` | Cards `added`, `removed` and `changed` (with `before` and `after` counts) going from `a` to `b`. Each side is an Archidekt deck id or URL, a snapshot id (`snap_...`) or pasted decklist text (one card per line; main zone only). When both sides are decks or snapshots, also `stats_delta` (b minus a) for the numeric statistics of `deck_stats` and the `a` and `b` deck briefs. Reads Archidekt for deck references; contacts nothing else. |
| `run_deck_report` | `deck_ref`, `games` (default 300, 10 to 2000), `simulate` (default true) | Reads the deck, computes `deck_stats` and, when the research service is configured, runs `validate_decklist` and (with `simulate`) a `goldfish_run` of `games` games, then stores the whole thing as a report for the user. Returns the report as `get_deck_report` does. A research call that fails is recorded in the report (`ok: false`), not raised. A second run of an unchanged deck within ten minutes returns the existing report with `reused: true`. The gateway keeps the newest 200 reports per user. Reports also appear on the gateway's History page. Changes nothing on Archidekt. |
| `list_deck_reports` | `deck_id` (optional) | The user's stored reports, newest first (up to 20): `report_id`, `deck_id`, `deck_name`, `deck_url`, `taken_at`, `metrics` (`card_count`, `average_mana_value`, `land_count`, `price_total`, `salt_total`), `has_goldfish`, `has_validation`, `bracket_estimate`. Contacts nothing. |
| `get_deck_report` | `report_id` | One report in full: the summary fields of `list_deck_reports` plus `stats` (as `deck_stats`), `goldfish` and `validation` (each `null` when not run, otherwise `tool`, `ok`, `data` and/or `text` as the research service answered). Contacts nothing. |
| `resolve_cards` | `cards` or `text` | Turns card names (from the user's photos, or a pasted list; `cards` items are `{name, set?, collector_number?, quantity?}` or plain lines) into exact Scryfall cards. Each result has `status`: `exact`, `printing` (matched by set and number), `fuzzy` (name corrected; confirm with the user), `ambiguous` or `not_found` (with `suggestions`). Returns `cards`, `needs_review`, `decklist_text` (for `propose_new_deck`) and `changes` (adds for `propose_deck_changes`). Contacts Scryfall only. |
| `card_printings` | `oracle_id` or `name` | Every printing of one card from Scryfall, newest first (`has_more` when there are more than the 175 shown): `set`, `collector_number`, `released_at`, `finishes` (a printing with `["foil"]` only is always foil), `image_art`. For picking an exact printing with the user. Contacts Scryfall only. |
| `save_scan_session` | `name`, and `cards` or `text` | Resolves like `resolve_cards` and stores the result as a named scan session for the signed-in user. |
| `list_scan_sessions` | none | The user's scan sessions (from the gateway's `/scan` phone page or `save_scan_session`), newest first, with `card_count` and `unresolved`. |
| `search_decks` | `name`, `commander`, `owner`, `format`, `colors`, `order_by`, `page` (all optional) | Public decks on Archidekt, as the site's own deck search finds them: `name` is a substring of the deck name, `commander` the commander's name, `owner` an exact Archidekt username, `format` a format name, `colors` letters from WUBRG (colour identity within them), `order_by` one of `-updatedAt`, `-createdAt`, `-viewCount` (default), `-size`, `edhBracket`; 60 decks a page, `has_more` says whether to ask for the next `page`. Each deck has `id`, `name`, `owner`, the format name, `size`, `bracket`, `views`, `updated_at`, `url` and `gateway_url` (the deck in the gateway's own pages). Private decks never appear. Anonymous read of Archidekt. |
| `archidekt_user` | `username` | One Archidekt user's public profile: `username`, `user_id`, `avatar`, `deck_count`, their newest `decks` (as `search_decks` lists them) and `has_more`. |
| `list_collection` | `query`, `set`, `sort`, `limit`, `offset` (all optional) | The cards the user owns, kept on the gateway (not Archidekt's Collection): `cards` (each with `id`, `name`, `set`, `collector_number`, `finish`, `condition`, `quantity`, the Scryfall id, `image_small`, mana and type data), `count` and `totals` (`cards`, `distinct`, `rows`, `sets`). `query` matches name, type or set; `sort` is `added`, `name`, `set`, `quantity` or `price`. Contacts nothing. |
| `add_to_collection` | `cards`, or `text`, or `scan_session` | Adds cards to the user's collection. `cards` items are `{name, set?, collector_number?, quantity?, finish? ("nonfoil", "foil", "etched") or foil?, condition?}` or the Scryfall id plus `name` for an exact printing; `text` is a pasted list; `scan_session` (id or name) adds every recognised card of that scan. Names are resolved on Scryfall like `resolve_cards`; a name nothing matches is returned in `skipped`, the rest in `added` with the new `totals`. Only the user's own message can ask for this. |
| `remove_from_collection` | `id` or `name`, `quantity` (optional) | Takes `quantity` copies (all, when omitted) of one collection row (`id`) or of every row with that card name out of the collection. Returns `removed`, `row` (what is left, or null) and `totals`. Only the user's own message can ask for this. |
| `get_scan_session` | `session` (id or name) | One scan session: `items` (each with `quantity`, `name`, `status`, `card` or `null`), `decklist_text` and `changes`. Items without a `card` were not recognised. |

### Deck results

`get_deck` and `get_my_deck` return `id`, `name`, `owner`, `updated_at`,
`url`, `card_count` (the deck proper), `side_count` (maybeboard and
sideboard: cards whose only categories are excluded from the deck),
`categories`, `decklist_text` (the deck proper, commander first, no
section headers), `sideboard_text`, and `cards`, each with `name`, `quantity`,
`categories`, `set`, `collector_number`, `finish` and `in_deck`.

### `changes` format for `propose_deck_changes`

A list of 1 to 40 objects:

| Field | Required | Notes |
| --- | --- | --- |
| `action` | yes | `add`, `remove`, `set_quantity`, `set_category`, `set_commander`, `set_finish` or `set_printing` |
| `card_name` | yes | Exact card name. Matching against the deck is case-insensitive. |
| `quantity` | for `set_quantity` | Integer 0 to 99. `add` defaults to 1. `remove` without a quantity removes every copy. |
| `category` | for `set_category` | For `add`: the category a card new to the deck is filed under (cards already in the deck keep their categories). For `set_category`: the one category every deck-proper row of that card is moved to (maybeboard and sideboard rows are left alone) (up to 60 characters). |
| `set_code`, `collector_number` | no, together | For `add`: pin the exact printing (as `resolve_cards` and `get_deck` report them). The printing must exist on Archidekt and be this card, or `apply_proposal` refuses the proposal with `not_found` and changes nothing. A pinned add goes in as its own deck row unless the deck already has that printing and finish. |
| `finish` | no | For `add`: `normal`, `foil` or `etched` (`foil: true` also works). A finish the printing does not come in falls back to what Archidekt offers. For `set_finish` (required) and `set_printing` (optional): the finish every copy already in the deck gets. |

`set_category` and `set_commander` take only `card_name` (plus `category` for
`set_category`): no quantity or printing. The card must already be in the deck.
`set_category` moves every deck-proper row of the card to exactly that category; maybeboard and sideboard rows are left alone. Moving a card into a category the deck does not count (Maybeboard, Sideboard) takes it out of the deck proper: the diff line ends in "(leaves the deck, -N)" and the review page counts it as removed.
`set_commander` files the card under `Commander` and takes `Commander` off every
other card, so the commanders become exactly the cards named by the proposal's
`set_commander` changes (give two for partners). A card may have one
`set_category` or `set_commander` per proposal, and not also an `add`, `remove`
or `set_quantity`.

`set_finish` (`card_name`, `finish`) changes the finish of every deck-proper copy of
a card already in the deck; `set_printing` (`card_name`, `set_code`,
`collector_number`, optional `finish`) swaps every copy for that printing, keeping
quantity and categories, and the printing must exist and be that card (else
`not_found`, nothing changes). A card takes one of `set_finish` or `set_printing`
per proposal, and not also a count or category change. Diff lines read
`Sol Ring: finish Normal -> Foil` and `Sol Ring: printing CMR 472 -> SLD 1074`.

Diff lines read `+1 Card` (added), `-1 Card` (removed), `4 -> 6 Card`
(quantity changed), `Sol Ring: category Ramp -> Artifacts` and
`Commander: Old Name -> New Name`; an add with a pinned printing or finish shows it, as in
`+1 Sol Ring (SLD 1074, Etched)`. Counts compare and change only the deck proper
(maybeboard and sideboard rows are left alone); `set_category` and
`set_commander` touch every deck-proper row of the named card.

### `propose_new_deck` arguments

| Argument | Notes |
| --- | --- |
| `name` | Required, up to 120 characters. |
| `deck_format` | `commander` (default; `edh` also accepted), `standard`, `modern`, `legacy`, `vintage`, `pauper`, `pioneer`, `brawl`, `historic` or `oathbreaker`. |
| `private` | Default `true`. |
| `cards` | List of `{card_name, quantity, category?, set_code?, collector_number?, foil?}`, quantity 1 to 99. A set code and collector number pick that printing when Archidekt has it, else the card's default printing. |
| `decklist_text` | Pasted list; sideboard lines are left out. |
| `csv_text` | An Archidekt CSV export. |
| `scan_session` | The id or name of one of the user's scan sessions; its `decklist_text` becomes the list. |

Give exactly one of `cards`, `decklist_text`, `csv_text` or `scan_session`
(the scan session's `decklist_text` is used). At most 300 rows
and 400 cards. The account must be linked before the proposal is stored.

### Proposal states

`pending` (waiting for approval), `applying` (in progress), `applied`
(done and verified), `failed` (nothing further will happen; see `result`),
`rejected` (the user pressed Reject on the review page or asked for
`reject_proposal`; nothing was sent to Archidekt), `expired` (older than 24 hours without being applied).

## Research tools (Mystic Forge, read only)

These are relayed to the gateway's internal [Mystic Forge](https://github.com/Kautiontape/mystic-forge)
service (version 1.3.2 with small compatibility patches). Their arguments go inside one object named `params`, for example
`{"params": {"name": "Sol Ring"}}`. Main arguments are listed; see each
tool's schema for the rest.

### Cards and prices (Scryfall)

| Tool | Main arguments | Use |
| --- | --- | --- |
| `scryfall_named` | `name`, `set_code` | One card by name (exact, then fuzzy). |
| `scryfall_search` | `query`, `order`, `page` | Search with Scryfall query syntax. |
| `scryfall_random` | `query` | A random card, optionally filtered. |
| `scryfall_card_text` | `cards` | Exact oracle text for many cards in one call. |
| `scryfall_rulings` | `name` | Official rulings for a card. |
| `scryfall_price` | `name`, `set_code`, `collector_number`, `finish` | Prices for a card or one printing. |
| `scryfall_price_list` | `decklist` | Prices a whole list and totals it. |

### Commander data (EDHREC) and combos (Commander Spellbook)

| Tool | Main arguments | Use |
| --- | --- | --- |
| `edhrec_commander` | `name` | Top recommendations for a commander. |
| `edhrec_average_deck` | `name` | The average decklist for a commander. |
| `edhrec_combos` | `name` | Popular combo lines for a commander. |
| `edhrec_recommendations` | `commanders`, `cards` | Suggestions given a commander and the current list. |
| `edhrec_top_cards` | `period`, `color` | Most popular cards over a period. |
| `edhrec_salt` | `limit` | The most disliked cards. |
| `edhrec_precon_upgrade` | `precon`, `commander` | How a precon is commonly upgraded. |
| `spellbook_card_combos` | `card`, `color_identity` | Every combo using one card. |
| `spellbook_combos` | `cards`, `color_identity` | Combos involving several cards together. |

### Rules

| Tool | Main arguments | Use |
| --- | --- | --- |
| `rules_get` | `ref` | A Comprehensive Rules entry by number or keyword. |
| `rules_search` | `query` | Full-text search of the rules and glossary. |

### Precons

| Tool | Main arguments | Use |
| --- | --- | --- |
| `precon_search` | `query`, `commander_only` | Find a preconstructed deck. |
| `precon_decklist` | `file_name` | Its official list. |
| `precon_export` | `file_name` | The list in Archidekt import format. |
| `precon_diff` | `file_name`, `deck` or `decklist` | Exact cuts and adds between a precon and an upgraded deck. |

### Archidekt public decks and formatting

| Tool | Main arguments | Use |
| --- | --- | --- |
| `archidekt_deck` | `deck` (id or URL), `include_text` | Read any public deck. Lists a card once per category, so its total can be too high for multi-category cards; count with `get_deck` instead. |
| `archidekt_user_decks` | `username` | A user's public decks. |
| `archidekt_export` | `deck` | A public deck in Archidekt import format. |
| `format_archidekt` | `cards`, `include_set_codes` | Turns a card list into Archidekt import text. Use it whenever you output a list for import. |

### Validation

| Tool | Main arguments | Use |
| --- | --- | --- |
| `validate_decklist` | `decklist`, `commander` | Card names, deck size and colour identity for pasted text. |
| `validate_archidekt_deck` | `deck` | The same plus category structure, for a public Archidekt deck. |

### Simulation (goldfish)

| Tool | Main arguments | Use |
| --- | --- | --- |
| `goldfish_odds` | `deck_size`, `draws`, `copies`, `min_successes` | Exact draw odds; no simulation. |
| `goldfish_annotate` | `deck` | What the engine models for this deck and which cards it cannot. Run first. |
| `goldfish_run` | `deck`, `n`, `seed`, `until_turn`, `mulligan`, `annotations`, `combos` | Simulate `n` games (default 1000, seed 42, 10 turns) with confidence intervals and an honesty report. |
| `goldfish_ab` | `deck_a`, `deck_b`, `n`, `seed`, `until_turn` | Paired comparison of two decks under identical seeds. |

`deck` arguments accept an Archidekt id or URL (public decks only) or
decklist text with one `1 Card Name` per line, commander first or marked with
a trailing ` *CMDR*`.

The gateway refuses, before forwarding, a goldfish `n` above 2000, an
`until_turn` above 30, `goldfish_odds` sizes above 1000, and any text argument
over 200 kB. The `archidekt_*` tools (and a `deck` given as an Archidekt id or
URL) count against the user's Archidekt budget; past it they answer that the
budget is used up for a few minutes.

## Not available on this gateway

Hidden on purpose until the gateway can record who owns them:
`goldfish_report`, `goldfish_start`, `goldfish_step`, `goldfish_state`,
every `watchlist_*` tool and `price_history`. The gateway's own stored
reports (`run_deck_report`) cover the saved-report case. There is no tool to
delete a deck.
