# Scanning physical cards

The gateway can turn a pile of physical cards into a decklist, no
third-party scanner app needed. There are two ways to do it, and both end up
in the same place: a *scan session* your assistant can read with
`get_scan_session`.

- **Ask your assistant** ([section 1](#1-ask-your-assistant-any-app-no-setup)):
  snap photos in Claude or ChatGPT. Nothing to set up.
- **Use the `/scan` page** ([section 2](#2-the-scan-page)): point your phone
  camera at cards one after another. Faster for a big stack.

The fine detail on how matching works, the privacy side and the operator
settings follow after that.

## 1. Ask your assistant (any app, no setup)

Take photos of your cards inside Claude or ChatGPT. The assistant reads the
names off the photo and calls `resolve_cards` with them (plus the set code
and collector number if it can read them). The gateway looks each one up on
Scryfall and tells the assistant how sure it is:

| Status | Means |
|---|---|
| `exact` | the name matched a card exactly |
| `printing` | matched by set code and collector number |
| `fuzzy` | the name was corrected (OCR noise, typos); the assistant should check with you |
| `ambiguous` | more than one card fits, or the closest one isn't a convincing match; `suggestions` lists the candidates |
| `not_found` | nothing matched; `suggestions` may still help |
| `deferred` | not looked up yet because Scryfall asked the gateway to wait; `retry_in` says how many seconds, and `complete` is false |

The answer also includes a ready-made decklist for `propose_new_deck` and a
list of `add` changes for `propose_deck_changes`, so the assistant can go
straight to building a new deck or adding the cards to one of yours. To keep
the result for later, it can call `save_scan_session`.

## 2. The /scan page

Open `https://mtg.example.com/scan` on your phone and sign in like any other
gateway page. You can add it to your home screen on Android (Chrome, Samsung
Internet) and iOS (Safari: Share → Add to Home Screen), and then it opens
like an app.

What's on it:

- **Camera.** Hold a card in the card-shaped frame with its title inside the
  dashed box and tap the shutter. The page finds the card's edges and
  straightens it out first, so a card held at an angle reads as if you'd
  shot it straight on. Then it reads the title *on the phone* and checks it
  against Scryfall through the gateway. Tap **Add**, or pick from the
  suggestions. If there's a lot of glare on the title you'll get a "tilt it
  away from the light" hint.
- **Continuous scan.** Instead of tapping for every card, flip the **Auto**
  switch and it reads frame after frame. A card it's **certain** about is added on its own, with a
  short "added" message and an **Undo**. Certain means two things agree: the
  title and the collector line, or a title no other card's name starts with
  ("Sol Ring" counts, "Mountain" waits because of Mountain Goat), read
  clearly with no glare. A card it's **unsure** about waits for your tap.
  Cards it **couldn't read** go to an unidentified list, with a picture of
  the title, so you can type them in later. The same card isn't added twice
  until it's left the frame, so a card sitting under the camera only counts
  once. Continuous mode needs to see the card's outline, so use a plain,
  contrasting background; the page tells you if it's not seeing anything.
- **Torch, zoom and brightness.** A Torch button turns the phone's flash
  on, and Zoom and Brightness sliders appear, each only when the camera
  reports it. Chrome on Android usually offers all three; iOS Safari has no
  torch and offers zoom from Safari 17 (going by the browsers' published
  capabilities, not tested on every phone).
- **Artwork check.** When the card was straightened, the page also makes a
  tiny fingerprint of the artwork (128 bytes; the picture itself never
  leaves the phone) and the gateway compares it with every printing of that
  card. That's how it picks the right printing when only the name was read.
  A set and collector number read off the card always win; the artwork can
  only back them up or add a "check the card" note. The first time a card
  name is scanned, the gateway "learns" its printings in the background
  (about a second for a typical card, 15 seconds for Sol Ring's 147
  printings) and says so. After that it's instant. Reprints with identical
  art can't be told apart this way; that's what the collector number is for.
- **Printing picker.** Lists every printing of a card, newest first, with
  finishes and art, so you can pick the exact one by hand.
- **Photo.** Uses your camera app or gallery instead, for browsers that
  can't stream video. It does the same edge-finding over the whole picture.
- **Type.** Card name with autocomplete, or paste a whole decklist.
- **List.** Quantities, cards still unresolved, **Save to gateway** and
  **Copy decklist**.
- **Sessions.** Your earlier scans. Open one to keep going, or delete it.

Once you've saved, tell the assistant: *"get my scan session &lt;name&gt;"*.
From there it can create a deck (`propose_new_deck`), add the cards to one
of your decks (`propose_deck_changes`), or just talk about them.

Scan sessions belong to you, live in the gateway's database (so they're in
the nightly backup), and are capped at 500 cards and 100 sessions per person
(the oldest drop off). Scanning never sends anything to Archidekt.

## How matching works

This is the detail behind `resolve_cards` and the page.

**Misreads.** When Scryfall can't place a name at all, the gateway retries
with common scan misreads swapped (`0`/`o`, `1`/`l`/`i`, `rn`/`m`, `5`/`s`),
then autocompletes shorter and shorter chunks of the name. A card is only
accepted if it still looks like what was read, and two equally close names
come back as `ambiguous` rather than a guess. Those extra lookups are capped
at 40 per call, so a list full of unreadable names can't eat Scryfall's rate
limit for everyone else.

**Limits per call.** One call takes up to 250 names, and each account runs
one call at a time. A second call while the first is still running is
turned away (HTTP 429, `busy`) rather than queued. The `/scan` page queues
its own lookups and retries a busy answer by itself.

**Scryfall's rate limit.** Scryfall rate-limits clients, and we couldn't
find its current limits published in a form we could verify. A live run on
2026-10-04 got HTTP 429 with `Retry-After: 60` at about 5 single-card
lookups a second. So the gateway spaces single-card lookups (`named`,
`autocomplete`, printings) `MTG_SCRYFALL_LOOKUP_INTERVAL` seconds apart
(default 0.5; the batched collection call stays at 0.1). If Scryfall still
says 429, the gateway keeps the cards it already resolved and returns the
rest as `deferred` instead of failing the whole call. Each call also has a
time budget of about 40 seconds, so a long list of unreadable names gets an
answer before proxies and clients time out, with the leftovers `deferred`
and `retry_in` 0. Resolve the deferred names again after `retry_in` seconds;
cards already resolved come from the gateway's cache and cost nothing.

**Printings.** Each card can also carry `set` and `collector_number` (as
printed; leading zeros are fine), `foil` (true or false), `lang` (two
letters) and an optional `art_hash` for the artwork check. When the set and
number point at a printing with a different name, the title is checked on
its own:

- same card: you get that printing (`printing`, "title read as ...");
- a different card matched exactly or confidently: the title wins over the
  number (a misread digit is the more likely mistake) and the note says so;
- anything less: `ambiguous`, with both cards as suggestions.

A long title cut short still counts as agreeing when the fragment completes
to exactly one card name and that's the printed card (one Scryfall
autocomplete lookup, checked against all of Scryfall's completions with
double-faced duplicates folded together).

**What the changes include.** When every copy of a name is one agreed
printing (title and collector line agreed, or picked by hand), each `add`
change also names `set_code` and `collector_number`, and `foil` when the
finish is known. An unknown finish is left out rather than sent as "not
foil". A printing that only exists in one finish (CMR 365 Aesi is foil only)
sets `foil` by itself and beats a misread star.

`card_printings(oracle_id | name)` lists every printing of a card, newest
first, with finishes and art images (`has_more` when a card has more than
the 175 shown). The page uses the same list at `/scan/api/prints`, cached as
summaries.

**On the page.** Before reading, the page looks for the card's four edges
and straightens the card (perspective correction in `geometry.js`, no
libraries). When it can't find a convincing outline it reads the dashed box
as is. The Photo button searches the whole picture on a working copy no
bigger than 2400 px on its long side, and the straightening only samples the
card's own box (at most 2048 px), so a 50 MP photo is never copied whole.
Titles are read with Tesseract.js (WebAssembly). In continuous mode only
frames with a card outline are read, a title read below
`MTG_SCAN_MIN_OCR_CONFIDENCE` goes straight to the unidentified list without
a lookup, and a frame that reads the same as the last one reuses its answer,
which keeps server traffic down. Three frames in a row without an outline
count as the card having left. For anyone building on the page, its hooks
live on `window.__scan` (`startContinuous()`, `stopContinuous()`,
`undoLastAdd()`, `setTorch(on)`, `state.unidentified`, and the
`scan:auto-added` event).

## Privacy and network

- Photos never leave the phone. Only the recognised text goes to the
  gateway. The gateway asks Scryfall (`api.scryfall.com`) for card data with
  the headers Scryfall's API guidelines ask for, at most ten requests a
  second across the whole gateway, and caches results for a day.
- Card pictures on the page load straight from Scryfall's image host
  (`cards.scryfall.io`).
- The text recognition engine and its English model (about 8 MB) are served
  by the gateway itself under `/scan/static/vendor/` and cached by a service
  worker after the first visit. No CDN is involved. The page's Content
  Security Policy only allows scripts and workers from the gateway itself.

## Operator notes

- The Docker image downloads the text recognition files at build time with
  `scripts/fetch_ocr_assets.py` (pinned versions, SHA-256 checked). Running
  from a checkout? Run that script once. Without the files the page still
  works for typing and pasting, and says so.
- Scryfall is called from the gateway container, so if you restrict
  outbound traffic, allow `api.scryfall.com` alongside `archidekt.com`, and
  `cards.scryfall.io` for the artwork check (everything else keeps working
  without it).
- Nothing new to deploy: no extra container, no new secrets. Text
  recognition runs on the phone. The artwork check keeps 768 bytes per
  printing in the database (table `scan_art_hashes`, part of the backup) and
  decodes one small JPEG at a time. Building a 250-printing gallery added
  about 3 MB to the gateway's memory in our test, well inside the 256 MB
  cap. Pillow adds about 6 MB (compressed) to the image.
- Learned galleries don't grow forever. The most recently scanned
  `MTG_SCAN_ART_MAX_GALLERIES` cards are kept, and a card not scanned for
  `MTG_SCAN_ART_GALLERY_IDLE_DAYS` is forgotten (it's just learned again
  next time). Each learned printing takes roughly 2 KB (768 bytes of hashes
  plus the stored card fields; estimated, not measured on a live gallery),
  so 500 cards with a handful of printings each is around 10 MB, and the
  250-printing cap keeps any one card to about half a megabyte. Pruning runs
  at startup and after every gallery build. At most 8 cards are learned at
  once; others get picked up on their next scan.

These settings are read by the gateway but aren't in the example stack file,
so add any you want to change under the gateway's `environment:`:

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_SCAN_FUZZY_MIN_SIMILARITY` | `0.65` | Lowest similarity at which a corrected name is accepted at all |
| `MTG_SCAN_FUZZY_CONFIDENT_SIMILARITY` | `0.8` | Similarity accepted without a second lookup; also what "confidently" means above |
| `MTG_SCAN_AMBIGUITY_MARGIN` | `0.05` | How close two candidates can be before the answer is `ambiguous` |
| `MTG_SCAN_AUTO_ADD_CONFIDENCE` | `80` | Reading confidence a continuous-scan card needs to be added without a tap |
| `MTG_SCAN_FOIL_STAR_INK_RATIO` | `0.09` | Threshold for spotting the foil star on the collector line |
| `MTG_SCAN_GLARE_RATIO` | `0.08` | Share of blown-out pixels in the title that triggers the glare hint |
| `MTG_SCAN_MIN_OCR_CONFIDENCE` | `50` | Continuous scan only: below this a title goes to the unidentified list without a lookup |
| `MTG_SCAN_ART_ENABLED` | `true` | Turns the artwork check on or off |
| `MTG_SCAN_ART_MAX_DISTANCE` | `380` | Largest difference (of 1024 bits) that still counts as an artwork match |
| `MTG_SCAN_ART_MIN_MARGIN` | `40` | Bits the best match must beat the next different artwork by. Raise to 60 for fewer wrong picks, at the cost of a few more "unsure" results |
| `MTG_SCAN_ART_MAX_PRINTINGS` | `250` | How many of a card's newest printings are learned (basic lands have hundreds) |
| `MTG_SCAN_ART_IMAGE_INTERVAL` | `0.1` | Seconds between image fetches from `cards.scryfall.io` |
| `MTG_SCAN_ART_IMAGES_PER_HOUR` | `1500` | Cap on image fetches per hour (about a deck's worth of new cards). One person can use at most a quarter of it, and start learning at most `MTG_SCAN_ART_MAX_GALLERIES`/20 new cards an hour; past that their cards answer busy while other people's carry on. Past the whole cap, learning pauses until the hour rolls over and the affected cards say so |
| `MTG_SCAN_ART_MAX_GALLERIES` | `500` | Cards whose learned printings are kept |
| `MTG_SCAN_ART_GALLERY_IDLE_DAYS` | `180` | Days without a scan before a card's learned printings are dropped |

`MTG_SCRYFALL_LOOKUP_INTERVAL` (default `0.5`) is in the example stack file
already; see [DEPLOY.md](DEPLOY.md#environment-reference).

## Known limits (as of 2026-10-04)

- It reads the printed English title only. Foreign-language cards, heavily
  foiled or glossy cards under glare, and art-covered "showcase" titles read
  poorly. Use the suggestions, or type it.
- A scan identifies the card name. The printing shown is Scryfall's default
  unless the set and collector number were read or typed, or the artwork
  check picked one. The artwork check has only been measured on synthetic
  photos (99% right with the card straightened, under 1% wrong automatic
  picks). Real phone results, especially with foil glare, aren't verified
  yet.
- Browsers without `getUserMedia` (some in-app browsers) fall back to the
  Photo button. In an iOS home-screen web app the camera is reported to work
  on current iOS versions, but the project hasn't verified that yet.
