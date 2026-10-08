# Export and import: what survives in every direction

The gateway's website and Android app, the assistant's tools and archidekt.com each read and
write decklists. This page says which file goes where and what survives the trip. Every line is
labelled: **verified** means a test in this repository or a read of archidekt.com's own code
proves it; **reported** means the code says so but the step was not exercised on a device or on
the live site; **unknown** means we could not establish it.

## The formats

| Format | Written by | Read by |
| --- | --- | --- |
| **Archidekt import text** (`1x Name (set) 123 *F* [Category{top}] ^Label,#hex^`, one row per card, sorted by name) | gateway: deck page > More > Export ("Archidekt import text", Copy, Download Archidekt .txt); `get_deck` with `view` `export` (or `full`) as `archidekt_text`; archidekt.com: Export > Text with every option on | archidekt.com: deck > Import (paste, or "Upload exported Archidekt file", .txt up to 1 MB); gateway: New deck > paste a list, editor > Paste a list, `propose_new_deck(decklist_text)`, `parse_decklist`, `compare_decks` |
| **Plain decklist** (`1 Name`, commander first, no headers, mainboard only; sideboard as a second list) | gateway: Export ("Plain decklist", "Sideboard and maybeboard", Download plain .txt); `decklist_text` and `sideboard_text` in `get_deck`'s default `text` view (and `full`) | gateway (as above); Moxfield, MTGO, Arena and the assistant's research tools (outside this repository: unknown beyond "they take `N Name` lines") |
| **CSV** with Archidekt's column names (Quantity, Name, Finish, Category, Secondary categories, Edition code, Collector number, …) | gateway: Export > Download .csv; Collection > Export CSV; archidekt.com: Export > CSV with the columns you tick | gateway: New deck > CSV, `propose_new_deck(csv_text)`, `parse_deck_export`. **archidekt.com's deck import does not take CSV** (its CSV import is for the collection only; verified from its client code) |
| **JSON**, the gateway's own format (the full deck read: cards with name, quantity, categories, set, collector number and finish, plus statistics) | gateway: Export > .json; every `get_deck` answer; `/api/v1/decks/{id}` | gateway: New deck > "a gateway .json export" (pasted or from a file), `propose_new_deck(json_text)`. Only the six card fields are read back. **archidekt.com has no JSON import** (verified from its client code), so this format is for the gateway and for your assistant |
| **Arena text** (`1 Name (SET) 123` under Commander, Companion, Deck and Sideboard headings; the Maybeboard is left out, Arena has none) | gateway: Export > Arena .txt; archidekt.com: Export > Arena | MTG Arena's import (**reported**: the heading and line shape Arena's own export writes; not exercised in Arena). Export only: the gateway does not read it back |
| **MTGO .dek** (the `Deck`/`Cards` XML the MTGO client exports, with `Quantity`, `Sideboard` and `Name`; MTGO's catalogue ids `CatID` are not known to the gateway and are left out) | gateway: Export > MTGO .dek; archidekt.com: Export > MTGO | the MTGO client (**unknown**: whether MTGO accepts a .dek without `CatID` was not verified; archidekt.com's own .dek was not compared). Export only |
| **PDF** (a printable list: name, format, size, each category with its rows, printing and finish, several pages when needed) | gateway: Export > PDF (written by the gateway without a PDF library) | any PDF reader (**verified** with pdftotext and pypdf on the test deck). Export only |
| Deck Registration PDF, EDHREC article | archidekt.com only | their own programs; not written or read by the gateway |

## What survives each trip

| From | To | Quantities | Printing (set, collector number) | Finish (foil, etched) | Categories | Commander | Sideboard and maybeboard | Labels (colour tags) | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gateway Archidekt text | archidekt.com Import | yes | yes | yes (`*F*`, `*E*`) | yes, with `{top}` `{noDeck}` `{noPrice}` | yes (`{top}`) | yes (their category rows) | yes (`^Label,#hex^`) | **reported**: the text is the shape archidekt.com's own exporter writes and its import dialog is built to read back; a live import was not exercised (deck writes from the test sessions are blocked) |
| gateway Archidekt text | gateway (New deck, editor paste, `propose_new_deck`) | yes | yes | yes | yes (flags stripped) | yes, when the category is named Commander or carries `{top}`-free "Commander" | yes: `Sideboard` and `Maybeboard` rows keep their category | dropped (the gateway has no colour tags) | **verified** (`tests/test_parity_hand_actions.py`, `tests/test_decks_and_proxy.py`) |
| archidekt.com Text export (all options on) | gateway | yes | yes | yes | yes | yes | yes, with `# Sideboard` or Generic section headers, or `[Sideboard]` / `[Maybeboard]` categories | dropped | **verified** against the syntax shown in archidekt.com's import dialog and written by its exporter; a renamed premier category (not "Commander") imports as a plain category |
| archidekt.com Text export, Primary-category headers | gateway | yes | yes | yes | yes (the header becomes the category) | yes | yes | dropped | **verified** (`tests/test_decklist.py`) |
| archidekt.com CSV export | gateway (`csv_text`) | yes | yes | yes (Finish column, Foil and Etched) | yes (Category and Secondary categories) | yes (Commander category) | yes (their categories) | dropped | **verified** on a 72-row live export (`tests/fixtures/sample_deck.csv`) |
| gateway CSV | gateway | yes | yes | yes | yes | yes | yes | n/a | **verified** (`tests/test_csv_export.py`) |
| gateway JSON | gateway (New deck, `propose_new_deck(json_text)`) | yes | yes | yes (etched too) | yes | yes | yes | n/a | **verified**: the export → import → compare test covers .json beside the plain .txt, the Archidekt text and the .csv (`tests/test_companion.py::test_export_import_round_trip_keeps_every_card_finish_and_commander`) |
| gateway Arena .txt, MTGO .dek, PDF | anything | export only | | | | | | | **verified** that each file names every row with its count and keeps the commander and sideboard apart (`tests/test_companion.py::test_export_only_formats_arena_mtgo_and_pdf`); what Arena and MTGO do with them is **reported** (Arena) or **unknown** (MTGO) above |
| gateway CSV | archidekt.com deck import | not accepted | | | | | | | **verified**: no CSV deck import on archidekt.com; use the Archidekt import text instead |
| gateway plain decklist | gateway | yes | yes | foil only (`*F*`) | yes | yes (first line, Commander category) | second list imports under `Sideboard` | n/a | **verified** |
| gateway plain decklist | archidekt.com Import | yes | yes | foil | yes | yes | with a `# Sideboard` header you add | n/a | **reported** (same syntax family) |

Quantities, printings, finishes, categories, the commander and the sideboard therefore survive
in every direction the two sites can read. The one field that does not round-trip through the
gateway is Archidekt's colour label (`^Label,#hex^`): the gateway writes it when exporting a deck
it read from Archidekt, and drops it when importing a list, because the gateway stores no labels.

## From a file

The **New deck** page has a **From a file** picker beside the text box: choose a `.txt`, `.csv` or
`.json` file and the page reads it in the browser (`static/filepick.js`, FileReader) into the box
and sets "The text above is" from the extension, so the form posts plain text and the gateway
handles no uploads. **Verified** that the page carries the picker and that each kind posts into
the same import; the picker itself is browser behaviour, exercised in the headless browser used
for the screenshots, not on a phone. The Collection page's **Import a list** has the same picker.

## Collection import

**Collection > Import a list** adds cards to your Archidekt Collection from pasted or uploaded
text: the gateway's own **Export CSV** (so the collection round-trips, same printings and
finishes, copies topped up), a CSV with Archidekt's collection column names (`Quantity`, `Name`,
`Finish` or `Foil`, `Condition`, `Edition Code` or `Set Code`, `Collector Number`, `Scryfall ID`;
headers are matched by name in any order and extra columns are ignored), or a plain card list
(`2 Sol Ring (CMR) 436 *F*`). At most 100 rows per import, because Archidekt is written one card
at a time about a second apart; split a bigger file and import the rest after. **Verified**
against the test double (`tests/test_browse_collection.py::test_collection_import_from_csv_and_plain_list`).
Whether archidekt.com's own collection CSV export carries exactly these headers is **reported**
from its collection import page, not verified against a live file.

## From the phone app

The Android app shows the same Export page. **Copy** puts the list on the clipboard
(**reported**: the app's WebView gives the page the standard clipboard API; the page falls back
to selecting the text when the clipboard is refused, **verified** in `static/export.js`). The
**Download** buttons save the file to the phone's Downloads folder (**reported**: the app's
download listener fetches gateway downloads with the signed-in session and hands them to the
system download manager, `android/.../MainActivity.kt`; not exercised on a device in this
round). From Downloads or the clipboard the list goes into archidekt.com's Import dialog in the
phone browser, or back into the gateway's New deck page.

## What archidekt.com itself offers

Read from the site on 2026-10-07: Import takes pasted text, an uploaded Archidekt `.txt` and
(behind a feature flag) a deck URL; Export writes Text (with options for `x`, set code, collector
number, foil mark, categories, colour tags and section headers), CSV with chosen columns, Arena,
MTGO `.dek`, a Deck Registration PDF and an EDHREC article. The gateway covers the text and CSV
formats both ways and its own JSON both ways; it writes Arena text, an MTGO `.dek` and a plain
PDF (export only); the Deck Registration PDF and the EDHREC article stay Archidekt's.
