# Live read-only fixtures

Real responses recorded from archidekt.com and api.scryfall.com on 2026-10-04
(about 21:20 to 21:22 UTC, and 21:55 UTC for the maybeboard deck). Each file is the response body exactly as received, except for the
owner placeholders described under "Decks with a maybeboard" and the synthetic fields described under "The deck". Use them to check parsers against what the providers actually
return, rather than against shapes built by hand.

## How they were captured

- GET requests only, no login, no cookies, no `Authorization` header.
- One request at a time with a one-second pause between requests.
- User-Agent: `MTG-Assistant-Gateway-fixture-capture/0.1 (+https://github.com/TheUncleBen/MTG-Assistant-Gateway; read-only)`
- Header `Accept: application/json`.

The Archidekt endpoint is the one Mystic Forge uses
(`ARCHIDEKT_API = "https://archidekt.com/api"` and `GET /decks/{id}/` in its
`server.py`, at commit `dd82cd1449645862a2e309ae70df2fb7c3a2a839`, the commit
pinned in `docker/mystic-forge/Dockerfile`).

| File | Request | HTTP status |
| --- | --- | --- |
| `archidekt_deck_sample.json` | `https://archidekt.com/api/decks/<id>/` | 200 |
| `archidekt_deck_365563.json` | `https://archidekt.com/api/decks/365563/` | 200 |
| `scryfall_named_aesi.json` | `/cards/named?exact=Aesi, Tyrant of Gyre Strait` | 200 |
| `scryfall_named_fuzzy_aesi.json` | `/cards/named?fuzzy=aesi tyrant` | 200 |
| `scryfall_card_aesi_cmr_365.json` | `/cards/d607b003-6b48-429c-a7fd-45b8dd1bb4f9` | 200 |
| `scryfall_search_aesi_prints.json` | `/cards/search?q=!"Aesi, Tyrant of Gyre Strait"&unique=prints` | 200 |
| `scryfall_rulings_aesi.json` | `/cards/d607b003-6b48-429c-a7fd-45b8dd1bb4f9/rulings` | 200 |
| `scryfall_named_murkfiend_liege.json` | `/cards/named?exact=Murkfiend Liege` (hybrid mana cost) | 200 |
| `scryfall_named_meloku.json` | `/cards/named?exact=Meloku the Clouded Mirror` | 200 |
| `scryfall_named_sol_ring.json` | `/cards/named?exact=Sol Ring` | 200 |
| `scryfall_named_not_found.json` | `/cards/named?exact=Definitely Not A Real Card Name` | 404 |

Scryfall paths are relative to `https://api.scryfall.com` and were sent URL-encoded.

## The deck

`archidekt_deck_sample.json` is a real response for a public Commander deck built from
the "Reap the Tides" Commander Legends precon, with the identifying fields replaced:
deck id, name, timestamps, owner (`sample-user`), folder, comment root and the
per-card record ids are synthetic. Every card record is otherwise exactly as
received. It is the same deck as the sample CSV export (`tests/fixtures/sample_deck.csv`;
Archidekt's CSV export with every column), so the two can be compared card for card.

Things in the live JSON worth knowing:

- 72 card entries, 100 cards in total (15 Forest, 15 Island).
- `cards[].categories` is `null` on 71 entries. Only the commander entry has a
  list (`["Commander"]`). The deck-level `categories` array has one entry,
  `Commander`. The CSV still shows a category on every row because the export
  fills in Archidekt's suggested category (`card.oracleCard.defaultCategory`,
  or `Land` for lands, which have no default).
- `cards[].modifier` is `Normal` or `Foil`; this is the CSV's `Finish` column.
- `cards[].card.uid` is the Scryfall printing id; this is the CSV's `Scryfall ID`.
- `card.oracleCard.manaCost` uses braces (`{2}{G/U}{G/U}{G/U}`); the CSV writes
  the same cost as `2G,UG,UG,U`.
- `cards[].label` is `""` on every entry; the CSV `Label` column writes `default` for it.
- The CSV `Price` column matches `card.prices.ck` (or `ckfoil` for the foil
  commander), not the TCGplayer price.
- Scryfall `named` lookups return the newest printing (Aesi comes back as
  `dsc` 210, not the deck's `cmr` 365); look a card up by id to get the deck's
  printing.

## Decks with a maybeboard

Another public Commander deck, recorded because it has a `Maybeboard`
category marked `"includedInDeck": false`, which the deck above does not:

| File | Deck | Entries | Copies in total | Copies in the deck |
| --- | --- | --- | --- | --- |
| `archidekt_deck_365563.json` | "Brago Blink" (the example deck in Mystic Forge's docs) | 109 | 117 | 103 |

"In the deck" counts every entry with no category or with at least one
category whose `includedInDeck` is not `false`. The deck has categories on
every card, and some cards sit in two or three categories. It belongs to
another Archidekt user, so its owner block was replaced with placeholders
after capture (`owner.id` 1, `owner.username` `example-owner`, a placeholder
avatar URL), and `parentFolder` and `commentRoot` were set to 1. Everything
else is as received, re-serialised compactly.

Prices, `viewCount`, `updatedAt`, EDHREC ranks and similar fields change over
time, so do not assert on their exact values. Refresh these files by repeating
the requests above; never record anything that needs a login or writes to a
deck.
