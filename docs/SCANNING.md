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
| `deferred` | not looked up yet because Scryfall asked the gateway to slow down; `retry_in` says how many seconds, and `complete` is false |
| `conflict` | the set and number are a different card from the name; `card` is what the name matched, `requested` the printing asked for. Left out of the decklist and the changes until you say which card you mean, and `complete` is false |

With no set given, a card keeps Scryfall's own default printing when that
one is already out (released today or earlier, UTC) and sold on paper, in
foil when foil was asked for. Scryfall's default is not always its newest
printing. When the default isn't out yet (a preview) or isn't sold on paper,
the card gets its newest printing that is, and the newer printings passed
over are listed in `newer_printings`. When foil was asked for and the printing has no foil,
the card comes back non-foil with `finish_unavailable` and a note naming
the finishes it does come in, and it is listed in `needs_review`.

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

- **Finding cards by their picture (from 0.7.47).** Once the gateway has
  learned every printing's picture ([below](#finding-cards-by-their-picture)),
  the camera works this way, in the browser and in the Android app alike:
  hold a card inside the frame and keep it still for a moment. The page
  finds the card's outline, straightens it and sends that picture (a JPEG of
  about 50 KB) to the gateway, which compares its artwork with every
  printing's. **Check each card** (the default) shows each card found to
  confirm: its printing, with the printings that share its artwork one tap
  away and **Other printings** for the rest, the finish (Non-foil or Foil),
  the quantity, **Add**, and **Not this card** with the next closest cards.
  **Add right away** adds the cards the gateway is sure of, with **Undo**,
  and shows the rest. The same card isn't sent again until it has left the
  frame. Printings that share one artwork (reprints) are told apart by the
  set and number at the bottom left of the card, read on the phone among
  those printings only; the **Set** box picks too, and when neither can,
  the card asks which one and **Add by name** keeps the card without a
  printing. **Detect now** looks at the frame at once, outline or not. Zoom
  chips (0.6×, 1×, 2× and so on, where the camera reports a zoom range) and
  two fingers on the viewfinder zoom; **Scan area** moves the frame (drag)
  and resizes it (its corner), kept on the device. When no card matches, the
  page tries the title, and failing that offers **Keep this picture for the
  admin**: the picture goes to Admin › Card scanner for 30 days. Until the
  gateway has learned the pictures, the camera reads titles as described
  next.
- **Camera (reading titles).** Hold a card in the card-shaped frame with its title inside the
  dashed box and tap the shutter. Filling the frame holds the phone about
  15 cm (6 in) from the card, in the browser and in the Android app alike: a
  card there fills about 40% of the picture's width, which reads well, and
  the main lens of some phones (Samsung's Ultra models among them) cannot
  focus much closer. The page finds the card's edges and
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
  once. A card is only added on its own when the page has found its
  outline. When it finds none for four frames in a row (a dark mat, a
  black-bordered card), it reads the dashed box as the shutter does and
  offers what it read for your tap, never adding it on its own; it doesn't
  offer the same card again until the box reads something else. A plain,
  contrasting background still works best. A card from another set than the
  collector line or the **Set** box asked for (a set code misread, such as
  "FERC" for FRC) waits for your tap too, since the printing and the foil
  star came from that line.
- **Running count.** While you scan, the viewfinder's top corner shows how
  many cards the scan holds ("12 cards"); tap it to open the list. Once the
  gateway prices scanned cards, the scan's value will show next to the
  count.
- **In the Android app.** From gateway 0.7.47 the app shows this page as
  it is, with its own live camera. Against an older gateway, the orange
  shutter and the viewfinder open the phone's own camera panel, with its
  torch brightness, zoom and exposure, and **Auto** opens it scanning on its
  own (see [ANDROID.md](ANDROID.md#3-scanning-with-the-phone-camera)).
- **Torch, zoom and brightness.** A Torch button turns the phone's flash
  on, zoom chips appear over the viewfinder and a Brightness slider under
  it, each only when the camera reports it. Chrome on Android usually offers all three; iOS Safari has no
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
- **Photo.** Scans a picture you choose from your gallery or files (on every
  phone and in the app), for a shot taken earlier or a browser that can't
  stream video. It does the same edge-finding over the whole picture,
  so the card doesn't have to fill the photo. HEIC pictures (the "high
  efficiency" setting of some phone cameras) can't be opened by the browser;
  the page says so, and JPEG or PNG works.
- **Type.** Card name with instant suggestions as you type, or paste a whole decklist.
- **List.** Quantities, cards still unresolved, **Save scan** and
  **Copy decklist**, then **What next?** with the three places the cards
  can go: **Save to binder** (your binder, see
  [USING.md](USING.md#binder); the scan is removed once saved), **Add to deck** (pick one of your decks;
  the deck editor opens with the cards filled in and you review the change
  before it is applied) and **New deck from these cards** (the new-deck form
  opens with the list as its decklist). The first two save the scan on the
  way if it isn't yet.
- **Sessions.** Your earlier scans. Open one to keep going, or delete it.

The first visit shows a short "How scanning works" card; on later visits it
is folded to its title, so the camera stays on the first screen. **Got it**
hides it, and the **How it works** button under the tabs brings it back.

A card lying on its side is found too: the page reads its name both ways up.
A black-bordered card is hard to find on a dark table; lay it on something
light.

Once you've saved, you can also tell the assistant: *"get my scan session
&lt;name&gt;"*. From there it can create a deck (`propose_new_deck`), add
the cards to one of your decks (`propose_deck_changes`), add them to your binder (`propose_collection_changes` with `scan_session`), or just talk about
them.

Scan sessions belong to you and live in the gateway's database (so they're
in the nightly backup) for as long as you keep them: they are drafts, up to
500 cards each and 500 drafts per person, and never expire by age (only past that
ceiling does the oldest go). Saving a scan's cards to a deck or your binder removes the draft. Scanning itself never sends anything to
Archidekt.

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
find its current limits published in a form we could check. A live run on
2026-10-04 got HTTP 429 with `Retry-After: 60` at about 5 single-card
lookups a second. So the gateway spaces single-card lookups (`named`,
`autocomplete`, printings) 0.5 seconds apart by default (**Time between
Scryfall lookups** on the Settings page; the batched collection call stays at 0.1). If Scryfall still
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
- a different card matched exactly or confidently: `conflict`. The card the
  title matched is shown (a misread digit is the more likely mistake) next to
  the printing asked for, but it isn't used until you pick one;
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
sets `foil` by itself and beats a misread star. The assistant's tools
(`resolve_cards`, `get_scan_session`, `save_scan_session`) return those
changes in the one card spelling the tools share: `name`, `set_code`,
`collector_number`, `quantity` and `finish` (`foil` or `nonfoil`), without
image links.

`card_printings(oracle_id | name)` lists the printings of a card, newest
first, with finishes; `set_code`, `finish` and `limit` (default 25) narrow
it, `matching` and `truncated` say how many matched and whether more did
than came back, and `has_more` says Scryfall has more than the 175 newest
printings. The page uses the same list at `/scan/api/prints`, cached as
summaries, with the art images the assistant's answer leaves out. In an app that shows cards, the
user sees every printing's picture on a card in the chat and taps the one
they mean, which reaches the assistant as text; the assistant's own answer
also carries `sets`, one row per set, and a `note`.

**On the page.** Before reading, the page looks for the card's four edges
and straightens the card (perspective correction in `geometry.js`, no
libraries). Around the dashed box first; failing that, over the whole
picture for a card down to about a quarter of the picture's width (a card
held further back than the box, a photo where the card fills only part of
the frame). An edge must be straight to within about half a percent of the
picture, so wood grain or a busy playmat isn't taken for a card. When it
can't find a convincing outline it reads the dashed box as is, and a failed
read says whether the card's edges or its name were the problem. The Photo
button works on a copy no
bigger than 2400 px on its long side, and the straightening only samples the
card's own box (at most 2048 px), so a 50 MP photo is never copied whole.
Titles are read with Tesseract.js (WebAssembly). In continuous mode a frame
with a card outline is read; without one, every fourth frame reads the dashed
box and can only offer a card for a tap. A title read below
**Lowest reading confidence looked up** goes straight to the unidentified list without
a lookup (read from the dashed box without an outline, it is let go: it may be the
table), and a frame that reads the same as the last one reuses its answer,
which keeps server traffic down. Three frames in a row without an outline
count as the card having left. For anyone building on the page, its hooks
live on `window.__scan` (`startContinuous()`, `stopContinuous()`,
`undoLastAdd()`, `noOutlineFrame(picture, box)`, `scanFile(file)`,
`setTorch(on)`, `state.unidentified`, and the `scan:auto-added` and
`scan:unsure` events).

## Privacy and network

- When the gateway finds cards by their picture, the page sends the
  straightened card it sees (a JPEG of about 50 KB, the card only, cut out of
  the frame) to the gateway, which compares it in memory and doesn't keep
  it. A picture is kept only when you press **Keep this picture for the
  admin**: then the gateway's admins can see it, with your name, under
  Admin › Card scanner for 30 days (it is deleted with your data too).
  Nothing is sent to Scryfall or anyone else. Reading titles, photos never
  leave the phone; only the recognised text goes to the gateway. The gateway asks Scryfall (`api.scryfall.com`) for card data with
  the headers Scryfall's API guidelines ask for, at most ten requests a
  second across the whole gateway, and caches results for a day.
- Card pictures on the page load straight from Scryfall's image host
  (`cards.scryfall.io`).
- The text recognition engine and its English model (about 8 MB) are served
  by the gateway itself under `/scan/static/vendor/` and cached by a service
  worker after the first visit. No CDN is involved. The page's Content
  Security Policy only allows scripts and workers from the gateway itself.

## Finding cards by their picture

The gateway keeps a summary of every paper printing's artwork, used to find
a card from the picture the page sends (`scan/picture.py`).

- **How it's built.** A few minutes after a start, and then hourly, the
  gateway goes through the daily printings copy (below) and fetches
  Scryfall's `small` picture (146 × 204) of each printing it hasn't seen,
  from `cards.scryfall.io`, about 8 a second for the whole gateway, four at a
  time, with its own User-Agent. A "slow down" answer pauses every fetch for
  five minutes; a picture that fails is tried again a day later. Each
  picture's art box goes through Google's MediaPipe image model
  (`mobilenet_v3_small.tflite`, run by the LiteRT runtime), and its 1,024
  numbers are kept in `<MTG_DATA_DIR>/scan-pictures.sqlite` (about 1.5 KB a
  printing, about 150 MB for 100,000). The pictures themselves aren't kept.
  The first round fetches about 100,000 pictures (roughly 1.5 GB, several
  hours); later rounds only new printings.
- **When it's used.** Only once a round has learned at least 95% of the
  copy's printings; until then the page reads titles. Admin › Card scanner
  shows how far it has got. It needs the printings copy (**Keep a local copy
  of card printings**) and the artwork check (`MTG_SCAN_ART_ENABLED`); with
  either off it is off.
- **The search.** The art box of the sent picture goes through the same
  model; a short summary of every row (128 numbers, kept in memory, about
  27 MB for 100,000 printings) finds the 200 closest, and those are compared
  in full. A card is **sure** when the best match beats the closest other
  card by 0.06; below a similarity of 0.45 nothing matches. A card that
  isn't sure is also tried upside down. In our test (8,474 printings of 16
  recent sets, 300 made-up phone photos built from card pictures) it found
  the right card 298 times out of 300, and every one of the 286 it called
  sure was right. Made-up photos aren't real ones, the whole catalogue is about twelve times larger,
  and foil cards weren't tested, so the numbers will move once measured on
  real phones.
- **Memory.** About 27 MB for the summary and about 20 MB for the model
  once the first scan loads it (measured on an x86 machine).
- **Missed pictures.** At most 500 kept, 50 a day per member, each deleted
  after 30 days or with the member's data.

## Operator notes

- The Docker image downloads the text recognition files at build time with
  `scripts/fetch_ocr_assets.py` (pinned versions, SHA-256 checked). Running
  from a checkout? Run that script once. Without the files the page still
  works for typing and pasting, and says so.
- Scryfall is called from the gateway container, so if you restrict
  outbound traffic, allow `api.scryfall.com` alongside `archidekt.com`, and
  `cards.scryfall.io` for the artwork check (everything else keeps working
  without it), and `data.scryfall.io` for the daily printings download below.
- **Once a day the gateway downloads Scryfall's printings file** (Scryfall's bulk data,
  `default_cards`, about 80 MB) and keeps a trimmed copy of it in the data folder,
  `<MTG_DATA_DIR>/scryfall-printings.sqlite`, about 55 MB. While a new copy is built (about
  35 seconds on an x86 machine, longer on a Raspberry Pi) the folder holds the download and the
  new copy beside the old one (up to about 172 MB in all), and the build adds about 17 MB to the gateway's memory. The copy is used only
  when Scryfall can't be reached, is slowing the gateway down or has a problem answering: the
  printing picker and `card_printings` then answer from it and say how old it is ("As of 3 Nov
  2026 · Scryfall can't be reached right now", or "From the gateway's own copy · Scryfall can't
  be reached right now" when the copy's day can't be read); `card_printings` adds "Prices are from that day, not
  today." When Scryfall's list of card names can't be loaded, the copy's names stand in for it
  until a later try loads Scryfall's own, so a card name the gateway doesn't know costs one
  Scryfall lookup instead of a walk through many (the log says "using the N names of the local
  printings copy meanwhile"). It is not part of the backup: Scryfall gives it again. Deleting it is
  safe; the next daily download makes a new one. To do without it, turn off **Keep a local copy
  of card printings** on the Settings page (General, no stack variable): the copy and anything
  it left are deleted at once, no more is downloaded, and printings answers fail while Scryfall
  is down, as before 0.7.46. On again, the next hourly check downloads a new copy.
- Nothing new to deploy: no extra container, no new secrets. Text
  recognition runs on the phone. The artwork check keeps 768 bytes per
  printing in the database (table `scan_art_hashes`, part of the backup) and
  decodes one small JPEG at a time. Building a 250-printing gallery added
  about 3 MB to the gateway's memory in our test, well inside the 256 MB
  cap. Pillow adds about 6 MB (compressed) to the image.
- Learned galleries don't grow forever. The most recently scanned
  500 cards are kept (**Cards whose printings are kept**), and a card not
  scanned for 180 days (**Forget a card's printings after**) is forgotten (it's just learned again
  next time). Each learned printing takes roughly 2 KB (768 bytes of hashes
  plus the stored card fields; estimated, not measured on a live gallery),
  so 500 cards with a handful of printings each is around 10 MB, and the
  250-printing cap keeps any one card to about half a megabyte. Pruning runs
  at startup and after every gallery build. At most 8 cards are learned at
  once; others get picked up on their next scan.

These settings are on the gateway's Settings page, in the folded **Card
scanning (advanced)** section. A change applies after the gateway restarts.
Only the artwork check's on/off switch can also be set in your stack:
`MTG_SCAN_ART_ENABLED=false` turns it off and locks the row.

| Settings page row | Default | What it does |
| --- | --- | --- |
| Lowest name similarity accepted | `0.65` | Lowest similarity at which a corrected name is accepted at all. Range 0.3 to 1 |
| Name similarity accepted at once | `0.8` | Similarity accepted without a second lookup; also what "confidently" means above. Range 0.3 to 1 |
| Closest two names may be | `0.05` | How close two candidates can be before the answer is `ambiguous`. Range 0 to 0.5 |
| Reading confidence to add without a tap | `80` | Reading confidence a continuous-scan card needs to be added without a tap. Range 0 to 100 |
| Foil star threshold | `0.09` | Threshold for spotting the foil star on the collector line. Range 0.01 to 0.9 |
| Glare threshold | `0.08` | Share of blown-out pixels in the title that triggers the glare hint. Range 0.005 to 0.9 |
| Lowest reading confidence looked up | `50` | Continuous scan only: below this a title goes to the unidentified list without a lookup. Range 0 to 100 |
| Artwork check | on | Turns the artwork check on or off (`MTG_SCAN_ART_ENABLED` in the stack) |
| Largest artwork difference | `380` | Largest difference (of 1024 bits) that still counts as an artwork match. Range 0 to 1024 |
| Artwork lead over the next match | `40` | Bits the best match must beat the next different artwork by. Raise to 60 for fewer wrong picks, at the cost of a few more "unsure" results. Range 0 to 1024 |
| Printings learned per card | `250` | How many of a card's newest printings are learned (basic lands have hundreds). Range 1 to 2000 |
| Time between image downloads | `0.1` | Seconds between image fetches from `cards.scryfall.io`. Range 0.1 to 5 |
| Image downloads per hour | `1500` | Cap on image fetches per hour (about a deck's worth of new cards). One person can use at most a quarter of it, and start learning at most a twentieth of **Cards whose printings are kept** as new cards an hour; past that their cards answer busy while other people's carry on. Past the whole cap, learning pauses until the hour rolls over and the affected cards say so. Range 1 to 1500 |
| Cards whose printings are kept | `500` | Cards whose learned printings are kept. Range 1 to 100000 |
| Forget a card's printings after | `180` | Days without a scan before a card's learned printings are dropped. Range 1 to 3650 |

**Time between Scryfall lookups** (default `0.5` seconds) is in the
Settings page's **Archidekt and Scryfall pacing** section.

## Measuring the scanner

Text recognition of the title (with the collector line) is the scanner's main
signal; the artwork fingerprint only picks the printing once the name is known.
Whether the artwork should become the main signal, as some scanner apps do, is
decided on measurements of real phone photos, not on synthetic ones.
`scripts/measure_scanner.py` runs both signals on a folder of photos with the
page's and the gateway's own code and writes a report.

**Taking the photos.** About 50, one card each, taken the way you'd scan:

- With the phone's normal camera app, the card about half the picture's width,
  held or lying a little at an angle, in the light you'd really scan in (a lamp,
  a window, the ceiling light).
- A mix, roughly: 10 plain cards, 10 foils, 10 in sleeves (glossy ones too), 5
  black-bordered cards on a dark mat, 5 not in English, 5 showcase, borderless
  or full-art cards, 5 old-frame cards. The report breaks the results down by
  these groups, so the mix matters more than the exact count.
- JPEG or PNG. Some phones save in HEIC ("high efficiency"); turn that off in
  the camera's settings, or share the pictures as JPEG. A HEIC photo is counted
  as the failure it is in a browser.
- Copy them into one folder on a computer with a checkout of this project
  (`pip install -e ".[dev]"`, then `playwright install chromium` and
  `python scripts/fetch_ocr_assets.py`).

**Labelling them.** Run

    python scripts/measure_scanner.py PHOTOS --template

which writes `PHOTOS/labels.csv` with one row per photo. Fill in each card's
`name`. Where you can read them, add `set` and `number` (the set code and
collector number at the card's bottom left, for the printing), `foil` (yes or
no) and `tags`: words for the groups above, separated by `;` (for example
`foil; sleeved`). A row without a name is left out.

**Running it.**

    python scripts/measure_scanner.py PHOTOS [--pool-extra NAMES.txt] [--json ROWS.json]

writes `PHOTOS/scanner-report.md`: for each signal, how often it found the right
card (and printing and foil where labelled), how often Auto would have added a
card without a tap and how many of those were wrong, the same by tag, and one
line per photo. The artwork is matched among every printing of the labelled
cards; `--pool-extra` adds look-alike cards (one name a line) to make that
harder, since a real scanner would choose among every card printed. The photos
never leave the computer: the script asks Scryfall for card data and card
pictures (spaced as the gateway spaces them) and keeps what it learns in
`PHOTOS/.scanner-measure.sqlite`, so a second run is quick.

The rows that decide the question are **Right card** for each signal and, above
all, **Of those, wrong**: wrong cards that would go on the list without a tap.

## Known limits (as of 2026-10-04)

- It reads the printed English title only. Foreign-language cards, heavily
  foiled or glossy cards under glare, and art-covered "showcase" titles read
  poorly. Use the suggestions, or type it.
- A scan identifies the card name. The printing shown is the newest one
  already out on paper, unless the set and collector number were read or
  typed, or the artwork check picked one. The artwork check has only been measured on synthetic
  photos (99% right with the card straightened, under 1% wrong automatic
  picks). Real phone results, especially with foil glare, aren't measured
  yet; [Measuring the scanner](#measuring-the-scanner) is how.
- Browsers without `getUserMedia` (some in-app browsers) fall back to the
  Photo button. In an iOS home-screen web app the camera is reported to work
  on current iOS versions, but the project hasn't tested that yet.
