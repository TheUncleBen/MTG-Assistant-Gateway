# Using the pages

What a member can do on the gateway's web pages and in the Android app, with
or without an AI assistant. The in-app **Guide** (`/guide`, in the account
menu) covers the same ground in its own words, step by step. Nothing here is
about running a gateway; that's [DEPLOY.md](DEPLOY.md) and friends.

## Start here

The gateway is a private companion to Archidekt. Everything you can do on
the Archidekt website with your own decks, you can do here on your phone or
computer, and an assistant such as Claude or ChatGPT can do it with you.
Your decks stay on Archidekt; the gateway never changes one without showing
you the exact change first.

1. **Link Archidekt** once on the Account page. The gateway then sees the
   decks of that account, private ones included. Read the note above the
   form first. It says exactly what linking gives the gateway, what is stored
   and never stored, what the person who runs the server and the site's
   admins can and cannot see or do, what is in logs and backups, and how
   long the link lasts (see [What linking gives the gateway](#what-linking-archidekt-gives-the-gateway)).
   It also says that Archidekt has no official way for other apps to reach
   decks, and that its terms restrict automated access, so Archidekt could
   limit or block an account used this way. You tick a box to say you have
   read it before linking.
2. **Open Decks** to browse them the way Archidekt shows them: text, stacks
   or grid, grouped and sorted how you like.
3. **Scan** a pile of cards with your phone, add them to a deck or to your
   collection, and **Search** public decks for ideas.
4. Optional: **connect an assistant** from the home page and ask it to
   research, simulate or edit for you. Every edit still comes back to you
   as a proposal.

On a phone the five tabs at the bottom are Home, Decks, Scan, Collection
and More; Search is the magnifier in the top bar, and More opens a sheet
with Proposals, History, Reports, Watchlist (when watchlists are on), Guide
and Account, and Admin for administrators. On a wider touch screen, such as an unfolded foldable or a
tablet, and in the Android app from 600 px, a rail down the left edge lists
every page, so it has no More. A desktop browser shows Home, Decks, Search,
Collection, Scan, Proposals and History in the top bar. The layout follows
the window as you fold, rotate or split the screen. The account menu is your
profile picture (or your initials) at the top right; a tap anywhere else
closes it. Large text works too: the
pages follow the phone's font size (the Android app included) and a
browser's text zoom up to 200%, wrapping rather than running off the screen.

## Home

Your newest decks with their cover art, a deck search box, and one tile per
section: Decks, Search, Precons, Scan, Collection, Proposals (how many wait for you), History, Guide. **Connect an AI
assistant** at the end holds the connector URL and the guided setup.

## Your decks

**My decks** lists the decks of your linked Archidekt account with their cover
art (the image you chose on Archidekt, else the commander), folder by folder.
Filter by name, colors or format, sort by last updated, created, name or
format in either direction, and show a grid or a list in three card sizes.
**New deck** creates one from a name, a pasted list, a CSV export, the
gateway's own .json export, a file you choose (.txt, .csv or .json) or a scan.
**New folder** makes an Archidekt folder.

Every deck, folder and card has a menu: right-click it, press and hold it on a
phone, or use its ⋮ button (Shift+F10 on a keyboard). A deck's menu opens,
renames, moves, exports, compares, clones or deletes it; a folder's menu makes
a folder inside it, renames, moves or deletes it. Deleting a folder deletes
everything inside it on Archidekt: the box lists how many decks, card packages
and folders that is and asks you to type the folder's name. The gateway first
makes a snapshot of every deck (and a copy in your backup folder when backups
are on) and stops if any of them fails; card packages get no snapshot or copy.
The backup folder can't be moved or deleted, and a folder holding it can't be
moved or deleted. The assistant can't move or delete folders.

One deck:

- **View as** Text, Table, Brewer, Stacks, Scroll or Grid; **Group by**
  Categories, Type, Mana value, Color, Rarity or Full deck (no grouping);
  **Sort by** name, mana value, price and more. The page
  updates as soon as you change a choice. Switching views, or coming back to
  the deck, within 20 seconds shows it as just read without asking Archidekt
  again; a change made on archidekt.com shows after that, or at once when
  you reload the page.
- **Filter** narrows the cards on the page as you type.
- Tap a card (or a stack on a phone, which fans it out) to read the whole
  card: the image, every face with its mana cost, type line, rules text,
  power and toughness or loyalty, flavour text, the printing, rarity,
  artist, price, salt score and the formats it is legal in; from there open
  it on Scryfall, mark it as owned, or move it to another category. The same
  viewer opens from a thumbnail in the editor and in the collection.
- On your own deck, **drag a card** onto another category: with a mouse, or
  press and hold on a touch screen and slide. The move goes to Archidekt
  when you let go, with a snapshot taken first, as a change from the card
  menu or **Select cards** does. Dragging a card into the commander
  stack first asks "This changes the deck's commander. Save
  anyway?".
- **Undo** and **Redo** (beside **Select cards**, and **Undo** on the
  message after each save) take back or redo any of those changes, each a
  save of its own; the editor's keys work too: Ctrl+Z, and Ctrl+Shift+Z or
  Ctrl+Y (Cmd on a Mac), except while you type in a field. A card's notes
  cannot come back: Archidekt gives the gateway no way to write them, so
  removing a card that has a note asks first.
- Drag a stack's handle to put the stacks in your own order. That order is
  kept with your account, so every device shows it (Archidekt has no stack order);
  **Reset stack order** puts the deck's own order back.
- **Quick add** types a card name and takes you to the editor with it
  filled in.
- **Playtest** opens the deck in Archidekt's own playtester (draw an
  opening hand, mulligan, play turns by hand) in a new tab, or in your
  phone's browser from the app. A private deck shows there only when that
  browser is signed in to Archidekt, so sign in on archidekt.com once in
  the browser you use. To play the deck by hand on the gateway itself, use
  **Goldfish**.
- **Play on EDHPlay** takes the deck to [EDHPlay](https://edhplay.com/), the
  free online table for Commander. The button opens a short box; its **Copy
  and Open EDHPlay.com** copies the deck in EDHPlay's import format and opens
  EDHPlay's My Decks (in the app, in your phone's browser). The list has the
  commander first (marked as the commander, partners too), the rest of the
  deck with each card's printing, then a **Sideboard** section with your
  companion and sideboard. EDHPlay's sideboard holds 15 cards, so anything
  past that is left out and the box names it. Your maybeboard, and anything
  else kept outside the deck, isn't sent (EDHPlay has no maybeboard), and
  EDHPlay doesn't take tokens from a list, so add those there by hand.
  EDHPlay's import never sets a companion, and a deck with no commander set
  arrives with none, so the box says when to set them on EDHPlay. On EDHPlay
  go to **My Decks**, choose **Add Deck** and open the new deck, then press
  **Import**, paste and press **Import & Replace** (the list sets the
  commander). The box shows these steps the first time and keeps them folded
  under **How to import** after that. EDHPlay has no way to receive a deck by
  link, so a later change on Archidekt is taken over the same way: press the
  button again.
- **Goldfish** plays the deck by hand on a gateway page, in your browser:
  the deck is shuffled and you get seven cards. Keep the hand or take a
  mulligan (London rules: the hand goes back, you draw seven again, and
  keeping puts one card on the bottom of the library for each mulligan; in a
  Commander deck the first mulligan is free). Then **Draw a card**, select a
  card to play it to the battlefield, the graveyard or exile, tap and untap
  it, or put it back; **Next turn** untaps everything and draws (the game is
  on the play, so turn 1 has no draw), and **New game** starts again. A
  Commander deck starts with its commander in the command zone; other decks
  have none. The page shows the library count and works with a mouse, a
  finger or the keyboard. Nothing is sent to Archidekt and nothing is kept.
- **Run full test** runs the statistics, the legality check and the
  solo test (300 games, no opponents) and opens the result, filed under
  History. It is the same run your assistant makes when you ask it for a
  deck report, so the numbers match whichever way it is started. A Commander deck is played
  from the command zone against 40 life. A deck without a commander and with
  at least 60 cards (Standard, Modern, Pioneer, Legacy, Vintage, Pauper and
  the like) is played as a 60-card game: seven-card London mulligan with no
  free one, 20 life, on the play, sideboard left out. A Commander-format
  deck without a card in its Commander category, or a smaller deck without
  one, gets no solo test and the report says why.
- When the gateway runs Forge, a Commander deck's report also has Forge
  games against three precons. They are picked for the bracket set on the
  deck in Archidekt, or, when none is set, for Archidekt's estimate from
  cards. Each precon's bracket comes from the game changers it holds. When there are not
  enough precons of that bracket, the nearest bracket fills in, and an older
  version of Forge without precon brackets plays the newest precons; the report
  names the opponents, their brackets and why they were picked. Forge plays
  4 games per report by default (your gateway's operator can change this)
  and shows each win rate with its 95% interval. Once they have finished,
  **Play more games** on the report page plays that many more against the
  same opponents and adds them to the results, up to 50 games a report
  (on a comparison, both decks get the same number, so the two stay even).
  You can also ask your assistant for more games, with the same rules. A
  game still going after four minutes (by default) stops and counts as a draw. Forge does not run 60-card
  decks (its opponents are Commander precons): their reports never start or
  queue a Forge run and say why. When the Forge engine is busy
  with other runs, a report waits in the queue and starts by itself. Unless you
  use the Compare page's Forge button (or your assistant asks for the same
  opponents), each deck meets precons picked for its own bracket (or
  Archidekt's estimate when none is set), so two decks' Forge
  win rates are not a like-for-like comparison; the paired goldfish A/B, which
  your assistant can run for the two decks, is.
- A report without Forge games (Forge not set up, a 60-card deck, a deck
  with no commander marked, Forge left out of the report, or too many of
  your reports already waiting for Forge) is labelled **Preflight only (no rules engine)**
  where the Forge games would be. A Forge run that stops early (a group of
  games failed, the run reached its time limit, the run was stopped, or the
  gateway lost track of it) shows the games it did play, marked **stopped
  early**, and the report is partial. Each report shows its status: queued,
  running, complete, partial (some parts did not run or did not finish) or
  failed.
- A deck's report trends on its History page compare a simulation number
  only across reports run the same way (Commander or 60-card game, same last
  turn), and the table of numbers shows each report's settings.
- **Compare with another deck…** (More menu) pits the deck against a
  preconstructed deck (pick one from Archidekt's list), any deck id or
  link, or a pasted list: what the build took out of the other deck, what
  it put in, changed counts, basic lands, and the statistics' differences.
  Tap a card name for the whole card. Your assistant makes the same
  comparison when you ask it to compare two decks.
- The **More** menu in the banner holds **Solo test only** (when the gateway
  runs Forge: the same run without the Forge games), Deck settings, Export
  deck, History and snapshots, **Delete deck**, Compare with another deck,
  Deck stats and Open on Archidekt. Deleting asks you to type the deck's name; a snapshot is
  kept under History and, when backups are on, a copy in your backup folder
  on Archidekt. Archidekt itself has no undo for a deleted deck.
- **Deck settings** holds the name, format, bracket, description and
  privacy, and also the **cover image** (any card of the deck, or Archidekt's
  automatic pick), the deck's **tags** (Archidekt's public deck tags) and the
  **folder** it sits in. Each saves to Archidekt at once; cover and tag
  changes take a snapshot first. **Categories** renames the deck's categories
  and turns Premier, Not in deck and Not in price on or off; Save categories
  keeps a snapshot first, asks once more when the change touches Premier or the
  commander, and refuses (sending nothing) if the deck changed since you
  opened the page; the panel then shows the deck as it is now, to make the
  change again.
- Below the cards: statistics (mana curve, colours, types, rarities,
  prices, legality), **Probability of draw** (the chance of at least or
  exactly N cards of a category, name, type, subtype or mana value in your
  first N cards, like Archidekt's Probability tab), **Deck checks** for
  every Archidekt format (deck size, the command zone and whether each card
  may lead it, partner and background pairing, colour identity, singleton or
  the four-copies limit, restricted cards, companion, sideboard size, cards
  banned or not legal in the format, the bracket you set against the
  estimate, uncategorised cards; a deck with a banned card is never reported
  fine), the bracket set on the deck, Archidekt's estimate from cards with
  Archidekt's notes (beside a bracket you set, labelled as Archidekt's
  estimate; alone when none is set) and the description.
- **Export** (More menu) shows the deck as Archidekt import text (every row
  with printing, finish, categories and labels; paste it into Archidekt's
  Import dialog or the gateway's New deck box), as a plain decklist and as
  the sideboard list, each with a **Copy** button, plus downloads as
  Archidekt .txt, plain .txt, .csv and .json (each imports back here or into
  Archidekt), and Arena .txt, MTGO .dek and PDF (one-way). See
  [EXPORT-IMPORT.md](EXPORT-IMPORT.md) for what survives in each direction.

The **editor** changes quantities, categories (type a new one to create it),
foil or printing, adds cards with autocompletion, removes cards, and undoes.
Maybeboard and sideboard rows are edited the same way (count and category;
they never count toward the deck), and **Add to** sends a new card to the
deck or to the maybeboard. **Paste a list** adds many cards at once, one per
line with a count in front (Archidekt's own export syntax is understood:
printing, `*F*` and `*E*` finishes, `[Category]`, `# Sideboard` headers);
names the gateway cannot match stay in the box.
Press **Save changes** and they go to Archidekt (a snapshot is taken
first, so History can undo); only a large removal or a change to the
commander zone asks you to confirm.

## Finding cards and decks

The **search box at the top of every page** finds both. Type part of a
name: the *Cards* group lists matching cards with their cost and type, and
the *Decks* group offers the deck search for what you typed and, when a
legendary creature matches, "Decks with commander …". Enter on plain text
searches decks. On a phone the box is behind the magnifier at the top.

Picking a card opens the **Cards** page (`/cards?q=`), which lists every
match with its picture; tap one to read it large, and **Add to deck** puts
it into any of your decks with the copies and category you choose. The add
is saved exactly like an edit made on the deck page (a snapshot first; the
same confirmation when a change is large; kept for review while writes are
off), so nothing skips your approval settings. Without a linked Archidekt
account the button is disabled and says why.

**Search** finds public decks on Archidekt the way the Archidekt site does:
by deck name, commander, format, colours or the person who built it,
ordered by newest, most viewed or largest. Archidekt matches a commander
by its full name only, so when part of a name ("Krenko") finds nothing, the
gateway looks it up among cards that can be commanders: one match shows
"Showing decks led by …", several give a "Did you mean" list to pick from.
Open any result to read it with the same views as your own decks; the
owner's name opens their profile with every public deck they have. From a public deck you can run the statistics,
export it, or clone it into your own account.

**Precons** (from Search or the home page) lists every preconstructed deck
Archidekt knows, newest set first, with a filter by set or deck name. Each
opens like any public deck, so you can clone it or compare it with your own
build.

## Scanning cards

**Scan** turns physical cards into a list with your phone camera. Text
recognition runs on the phone; the card is then matched on Scryfall through
the gateway.

1. **Camera.** Hold a card in the frame with its title in the dashed box
   and tap the shutter, or switch on **Auto** and show cards one after
   another: sure matches are added on their own (with Undo), doubtful ones
   wait for your tap.
2. **Check the list.** Fix a wrong match from the suggestions, pick the
   exact printing or foil, adjust quantities. Cards the camera could not
   read are kept with a picture of the title so you can type them.
3. **Choose what to do with it:** save the cards to your collection (on
   Archidekt), add them to one of your decks, start a new deck from them, or
   keep the scan for later (your assistant can pick it up too). A saved scan
   is a draft: it stays until you send its cards somewhere or delete it.

You can also type or paste names instead of using the camera, and earlier
scans are listed under **Sessions**. The details are in
[SCANNING.md](SCANNING.md).

## Collection

**Collection** is the list of cards you own. It is your Collection on
Archidekt, shown and edited here through the account you linked: nothing
about your cards is stored on the gateway, and what you add here appears on
archidekt.com at once. Scan a pile and save it, add cards by name, or mark a
card as owned from any deck page. Each card keeps its printing, finish,
condition and count.

- Filter by name, show it as a grid or a list, sort by newest or by set
  release, and page through it. Plus and minus change the count; the cross
  removes a card; the dots open the card's details: finish, condition,
  language and the price you paid, saved to Archidekt on **Save** (its tags
  are shown as Archidekt holds them).
- Cards you own show a **green dot** on every deck page, your own decks and
  public ones alike, with the number of copies Archidekt knows about.
- **Export CSV** downloads the whole collection. Archidekt's import does not
  read the column names: on archidekt.com you pick each column's type when
  you import (the page's **Importing the CSV on archidekt.com** lists them)
  and leave **Skip first line** on. Condition and Language are written in
  Archidekt's own codes: D for Damaged, and JP Japanese, KR Korean, SP
  Spanish, CS Chinese (Simplified) and CT Chinese (Traditional); the other codes
  are the same as the gateway's.
  **Import a list** adds cards from that CSV, from a CSV with Archidekt's
  column names or from a plain card list, pasted or chosen as a file, up to
  100 rows at a time. Afterwards the page says how many cards went in and
  names the lines it could not take as given: a language Archidekt's
  collection doesn't store (such as Phyrexian) is kept as English, a
  condition Archidekt's collection doesn't store (such as EX) is kept as
  Near Mint, copies of a card you already own in another language are added
  to that copy, and
  cards Archidekt cannot find are left out. When the text itself cannot be
  read, nothing is imported and the page says which line is wrong.
- A scan is a draft that stays as long as you like, up to a whole deck:
  fix misread cards, printings and quantities first, then send it to your
  collection or a deck, which removes the draft. Nothing expires by age.

Your assistant can read and add to the collection too (an addition is a
proposal you approve on the card in the chat or the review page, like a deck
edit), for questions like "which cards in
this deck do I not own yet?". After a collection change is applied, the
gateway reads the collection back to check every card landed. Adding takes about a second a card,
because each one is written to Archidekt.

## Likes, bookmarks, follows and comments

Every deck page has Archidekt's own social buttons: **Like** (with the
deck's score), **Bookmark**, **Follow** its owner, and a **Comments** panel
that shows the deck's thread and lets you reply or add a comment, and edit
or delete your own comments. A user page has the Follow button too. Each one asks you to confirm first, then
goes to Archidekt under your Archidekt name, exactly as if you had pressed
it on archidekt.com. Your assistant can suggest a like, bookmark, follow or
comment, but it never sends one: each waits for your own press, in every
approval mode, on the card in the chat or on its review page (an edit of a
comment that has formatting: on its review page only).

## Proposals and history

What you save in the app (the editor, a drag between categories, a new
deck, clone or settings) goes to Archidekt at once: pressing Save is your
approval, and a snapshot is taken first so History can undo it. Only a big
removal or a change to the commander zone asks you to confirm, and a restore
from History opens its review first. An **assistant's** edit, new deck, clone,
settings change, restore or collection change is first saved as a
**proposal**: the exact list of what would change. Approve it on the card
in the chat or on the review page (deleting a deck, and any edit of a
description or comment that has formatting: on the review page only), or
pick an approval mode on your Account page (Manual, shown as "Ask me every
time"; Semi-auto, shown as "Apply low-risk changes without asking", for small deck or collection edits and copies of a deck; Full auto, shown as "Apply deck and collection changes without asking") and the
assistant applies what that mode allows, with a snapshot first for a change to one of your decks. Adding, removing or swapping a deck's commander always waits for your press.

- **Proposals** lists what is waiting. Open one to read the diff and press
  **Apply** or **Reject**, or check several, on any decks, and press
  **Apply checked** or **Reject checked** to do them together.
- Before an edit is applied, the gateway checks the change still fits the
  deck as it is now, saves a **snapshot** and, with backups on (the operator's
  setting), puts a backup copy of the deck in an "MTG Gateway backups" folder
  on your Archidekt account, then reads the deck back to confirm. The deck
  page's own saves, Card search's Add to deck, and the deck editor's save with
  its backup box unticked make no backup copy.
- **Updated** and **Needs a look**: Archidekt is the master copy of your
  decks. When you open the app, and every 30 minutes while changes are
  waiting, the gateway checks your decks on Archidekt (it only reads). If a
  deck changed after a change for it was made, on archidekt.com, in another
  app or by another change applied here, the gateway works that change out
  again on the deck as it is now. If it still does exactly what you
  reviewed, it shows **Updated** and can be applied as before. If it would
  now do something else, it shows **Needs a look** with the reason, and
  Apply sends nothing: ask your assistant to update it, or reject it. The
  check never changes a deck, a snapshot or your undo history, and it waits
  while you or your assistant are working.
- **History** shows proposals, snapshots and deck reports over time.
  **Restore** puts a deck back exactly as a snapshot had it, cards and the
  deck's own name, description, format, bracket and privacy alike (another
  proposal you approve).
- **Reports** (under More) list your stored deck reports, newest first (the
  gateway keeps the newest 200), with what Forge has going for you now. Each holds
  the deck's statistics, a legality check, and the solo test, the Forge games
  or both, so you can follow how a deck develops.
- **Compare** has **Test both in Forge against the same opponents** when
  Forge is on and both decks have a commander: both decks play Forge games
  against the same three precons, picked for the higher of the two decks'
  brackets, so it doesn't matter which deck you compare from.
- The **Goldfish** page's **How to play** guide walks a first game step by
  step; you can hide it.

## Watchlist and prices

Watchlists are off unless the operator turns them on; until then the page,
the card viewer's price buttons and your assistant's watchlist abilities are
not there.

The **Watchlist** page (in the account menu, and under More on a phone)
keeps the price of cards and decks you choose, once a day:

- **Watch a card** by name, or one printing by its set code and number, in
  non-foil, foil or etched. A name alone follows the printing Scryfall gives
  for that name. The card viewer has the same: **Price history** shows the
  prices kept for that card, and **Watch this card** adds it.
- **Watch a deck** by its Archidekt link or number (your own or a public
  one). The gateway reads it again once a day, so its value follows the
  cards it holds.
- Each card and deck shows today's price, how much it moved over 1, 7 and
  30 days, a small chart of its price over time, and the same numbers as a
  table under **Price history table**.
- **Price alert**: a move of more than a percentage from one day to the
  next, or a price it rises above or falls below. Alerts reach you through
  notifications (below).

Prices come from Scryfall (TCGplayer in dollars, Cardmarket in euros) and
are checked once a day; the operator sets how many cards and decks you may
watch and how long prices are kept (a year unless they changed it). Your
assistant can do the same: watch a card or a deck, stop watching, list your
watchlist and read a card's price history. Watching changes nothing on
Archidekt, so it needs no proposal.

## Notifications and summaries

The **Notifications** card on your Account page can send you a short message
in the gateway's Android app when an assistant leaves changes waiting for
your press (one message per deck, such as "2 changes to Atraxa are waiting
for you"), a deck report finishes (Forge runs that waited in the queue
included), or a watched card crosses your price alert. There are no
notifications on iPhone. Open your Account page in the app, tick **Send me
notifications**, choose which of these you want, and save, then press
**Get notifications on this phone**. On Android 13 and later the app asks
you to allow notifications, and it asks once to run in the background.
Android then shows a quiet "Always connected" notification while the app
listens. **Send test** checks it, and
**Disconnect my phones** stops every phone you connected. Messages say only
what happened and link to the page here; they never list a deck's cards.

The summary is part of the **Notifications** card, so it is there only when
the gateway has a notification server set up (without one, the card only
says so). Choose **Every day** or **Every week
(Mondays)** and the card shows a plain summary of today or the last 7 days
(watched prices that moved and your watched decks' values when watchlists
are on, deck reports finished, and proposals waiting and applied), and it is
also sent to you as a notification when notifications are on. The gateway writes it with fixed sentences from its
own data. It does not write an AI summary itself: that would need a paid AI
model service, and the gateway uses only free ones.

For a summary in your assistant's own words, use the assistant you already
have. It can read the same summary, for a day or a week, with or without
notifications, and reading it changes nothing. Claude and ChatGPT can both
run a prompt on a schedule (check your app for scheduled tasks); a prompt
such as "Every Monday morning, get my weekly summary from my MTG gateway and
tell me in a few lines what changed and what needs me" does it.

## With an AI assistant

Connect Claude or ChatGPT from the home page ([CONNECT.md](CONNECT.md)).
You sign in with the same account as here, and the assistant acts as you:
it can read your decks but never change one without a proposal you approve.
Things to ask it: show my decks; open a deck and tell me its mana curve;
search for decks with a commander; look up a card, its rulings, prices,
combos; goldfish a deck; swap cards or build a new deck from a list (each a
proposal with a review link); read my card photos or use my last scan;
which cards in this deck do I own. In Claude on the web, desktop and
phones (and ChatGPT, as reported), the things you have to pick or press come
as cards in the chat: a proposal with Approve and Reject, the changes waiting
for you with Apply all, the printings of a card as pictures to tap, and the
cards read from your photos to keep or drop. Everything else, such as a deck
or its statistics, comes as the assistant's own words, which keeps a long chat
light. A tap on a picker is passed to the assistant as text; nothing changes
until a proposal you approve. The [assistant skill](SKILL.md) teaches
the house rules. The assistant can also propose moving a deck to another
folder, changing its tags or its cover (a settings change you approve). It
can also suggest deleting a deck or creating a folder, but only your own
press applies those; renaming, moving and deleting folders is yours alone.
[CAPABILITIES.md](CAPABILITIES.md) lists every tool the
assistant has, which one owns each job, everything you can do by hand, and
what only one of the two paths can do.

## The Android app

The [Android app](ANDROID.md) shows these same pages as a native-feeling
app with the phone camera built in. The app's own actions (Scan with the
app's camera, Reload, Open in browser, Change gateway) sit in the account
menu. On a foldable, the folded screen has the tabs at the bottom and the
unfolded screen moves them to a rail down the left edge. No Android phone? Add the site to your home screen from the
browser and it opens like an app, scanning included.

## Your account and privacy

The **Account** page shows who you are signed in as and your Archidekt
link. Unlinking removes the stored Archidekt session at once. **Sign out on
all my devices** ends every browser and Android app session; connected
assistants keep working until you disconnect them under **Connected apps**.
Only
you can see your decks, proposals, snapshots, scans and collection; another
member cannot reach them, and neither can their assistant. Nothing is
public or indexed. Your Archidekt sign-in is stored encrypted; no page,
admin tool, log or backup shows it, and backups leave it out, so after the
gateway is restored from a backup you sign in again and relink Archidekt.
The key that opens it is on the same server, so whoever runs the server
can use it to act as you on Archidekt until it expires. More in
[ONBOARDING.md](ONBOARDING.md#8-privacy).

## What linking Archidekt gives the gateway

This is the text shown above the link form on the Account page (and the
Android app, which shows the same page). The short summary is always open.
The full detail is folded, and the tick box stays disabled until you open it
(with JavaScript off, the detail shows open and the box works). You tick the
box to say you have read it before anything is sent to Archidekt, and once
you are linked it stays on the Account page under **What linking gives this
gateway and the person who runs it**. Your Account page also shows the date
your link stops working, and whether this gateway runs the hourly clean-up
mentioned at the end.

**Before you link your Archidekt account**

**In short**

- The gateway sends your Archidekt password to Archidekt once and never stores it. It keeps the sign-in session Archidekt returns, encrypted. Assume that session can do anything your Archidekt sign-in can.
- The person who runs this server holds the key, so they could use the session to act as you on Archidekt until it expires (about 40 days after you link), and they can read everything else the gateway keeps about you. Link only if you trust them.
- Admins can see your account details and activity log, but not your password or session, and nothing on the admin pages uses your link. An admin who also has access to the server can do what the person who runs it can.
- You can unlink at any time. Whether Archidekt itself ends the session when you unlink or change your password is not known.

**Full detail**

*How linking works*

- Your Archidekt username or email and password go from this server to Archidekt once, to sign you in. The gateway never stores your password.
- Archidekt returns a sign-in session, the same kind archidekt.com gets. We know of no limited version, so assume it can do whatever your Archidekt sign-in can. The gateway keeps it so it doesn't need your password again.

*What it uses the session for*

- Only when you, or an assistant you connected, ask for something. A big change you approved may finish in the background.
- Reading your decks (private ones included), folders, tags and collection.
- Reading, as you, other people's decks and comment threads you or your assistant look at and the list of people you follow, and looking up the cards in the changes you make.
- Making the deck, folder, tag and collection changes you make yourself, approve, or let your assistant make through your approval mode, including deleting a deck when you ask. It may first save a copy of the deck in a backup folder on your Archidekt account (the person who runs the server can turn that off).
- The likes, bookmarks, follows and comments you make yourself on these pages. No assistant can do those.

*What is stored*

- Encrypted: the session (an access token and the refresh token that renews it).
- Plain text: your Archidekt username and user number (if Archidekt sends no username, what you typed to sign in, which may be your email); when you linked and when the session was last renewed and used; activity-log entries for each link, unlink, renewal and failed attempt.
- Never stored: your password.
- After an unlink, your Archidekt username and user number stay until you or an admin delete your data. Activity-log entries, including the Archidekt username noted when you linked, are kept for a year, even after your data is deleted.

*What the person who runs this server can do*

- The key that opens the session is on the same server. Anyone with access to the server, including the person who runs it, can use the session to act as you on Archidekt until it expires: read, change or delete your decks and collection, or anything else your sign-in allows. If Archidekt lets a session change your email or password (not known), they could use that to keep your account. They cannot get your password from the session.
- They can read everything else the gateway keeps about you, unencrypted: your name, email, username, user ID and groups at the sign-in service, profile picture (if sent), sign-in and last-seen times, approval mode, Archidekt username and user number, proposed and applied deck and collection changes, deck snapshots, reports and covers, scans, connected apps, usage counts and the activity log. The sign-in service's tokens the gateway keeps to check your groups use the same key, so they can open those too.
- They control the code this server runs. This gateway's code never keeps your password, but changed code could.

*What admins on this site can do*

- On the admin pages they see your name, email, username, groups and user ID at the sign-in service, your Archidekt username, first sign-in and last seen, connected apps, how many app tokens and browser sessions you have open, whether and when you were disabled, and the activity log: what you did, when and with which app (links, unlinks, proposals, deck and collection changes, scans, likes, bookmarks, follows and comments, with deck, proposal and comment numbers).
- They can unlink your Archidekt account, disable and enable your account, sign you out everywhere and delete your data.
- They cannot see your password or session, cannot open your proposals, and have no button that uses your link. An admin who also has server access can do everything in the section above.

*Who else can use your link*

- Anyone who can sign in here as you: someone holding your browser session, an assistant you connected, or whoever runs the sign-in service, if they can sign in as you there.

*Logs and backups*

- Logs never contain your password or session. At the usual log level they hold no Archidekt addresses or usernames: the request log names the kind of page (deck pages and profile pages), not the deck or the person, and a few other messages may include a deck number. With debug logging on, they list the Archidekt addresses called, which include deck numbers and usernames.
- The gateway's nightly backups leave out the session and keep everything else above. The copy it saves just before it upgrades its database keeps the session, still encrypted, so the server can go back to the older version without anyone linking again; that copy is deleted after as many days as the nightly backups are kept. A copy of the server's disk made another way includes the session, still encrypted.

*How long it lasts and how to end it*

- The access token lasts about an hour and is renewed with the refresh token. The refresh token stops working about 40 days after you link (measured October 2026; Archidekt can change this). Then you link again. Your Account page shows your date.
- Unlink, Delete my data, or an admin's Unlink or Disable deletes the gateway's copy at once, also from the copies saved before database upgrades.
- If you're removed from this gateway's group, or your sign-in account is deactivated or deleted, you lose access at once. If the hourly clean-up is on (it needs an extra setting at the sign-in service), your stored session is usually deleted within about an hour, later while the sign-in service gives no clear answer. Without it, the session is deleted when an admin presses Disable or Unlink, when it expires, or (after a group removal) the next time you or one of your apps uses the gateway.
- Unlinking deletes only the gateway's copy. Whether the session also stops working at Archidekt, or ends when you change your Archidekt password, is not known, so a copy someone already took may keep working until it expires.

*Archidekt's terms*

Archidekt has no official way for other apps to read or change decks, so the gateway uses the same requests archidekt.com's own pages use. Archidekt's terms of service restrict automated access, so Archidekt could limit or block an account used this way.

The tick box reads: *I have read what linking gives this gateway and the person who runs it, and I want to link my account.*
