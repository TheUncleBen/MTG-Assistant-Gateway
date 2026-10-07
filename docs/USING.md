# Using the pages

What a member can do on the gateway's web pages and in the Android app, with
or without an AI assistant. The same text is the in-app **Guide** (`/guide`,
in the account menu), so the app and this file stay in step. Nothing here is
about running a gateway; that's [DEPLOY.md](DEPLOY.md) and friends.

## Start here

The gateway is a private companion to Archidekt. Everything you can do on
the Archidekt website with your own decks, you can do here on your phone or
computer, and an assistant such as Claude or ChatGPT can do it with you.
Your decks stay on Archidekt; the gateway never changes one without showing
you the exact change first.

1. **Link Archidekt** once on the Account page. The gateway then sees the
   decks of that account, private ones included.
2. **Open Decks** to browse them the way Archidekt shows them: text, stacks
   or grid, grouped and sorted how you like.
3. **Scan** a pile of cards with your phone, add them to a deck or to your
   collection, and **Search** public decks for ideas.
4. Optional: **connect an assistant** from the home page and ask it to
   research, simulate or edit for you. Every edit still comes back to you
   as a proposal.

On a phone the five tabs at the bottom are Decks, Search, Scan, Collection
and More (More opens the home page with every section). On a wider touch
screen, such as an unfolded foldable or a tablet, and in the Android app
from 600 px, the same five sit in a rail down the left edge; a desktop
browser shows them in the top bar. The layout follows the window as you
fold, rotate or split the screen. The account menu is the person icon at
the top right; a tap anywhere else closes it.

## Home

Your newest decks with their cover art, a deck search box, and one tile per
section: Decks, Search, Scan, Collection, Proposals (how many wait for you), History, Guide. **Connect an AI
assistant** at the end holds the connector URL and the guided setup.

## Your decks

**Decks** lists the decks of your linked Archidekt account with their cover
art. Filter by name or folder, grid or list, sort by last updated, created,
name or format. **New deck** creates one from a name, a pasted list, a CSV
export or a scan.

One deck:

- **View as** text, stacks or grid; **Group by** category, type, mana value,
  colour or none; **Sort by** name, mana value, price and more. The page
  updates as soon as you change a choice.
- **Filter** narrows the cards on the page as you type.
- Tap a card (or a stack on a phone, which fans it out) to see it large,
  open it on Scryfall, mark it as owned, or move it to another category.
- On your own deck, **drag a card** onto another category: with a mouse, or
  press and hold on a touch screen and slide. The moves are collected into
  one proposal you review.
- **Quick add** types a card name and takes you to the editor with it
  filled in.
- The **More** menu in the banner holds Edit deck, Deck settings, Clone, Run
  report, Export (text, JSON or CSV) and the deck's page on Archidekt.
- Below the cards: statistics (mana curve, colours, types, prices,
  legality, bracket estimate) and the description.

The **editor** changes quantities, categories (type a new one to create it),
foil or printing, adds cards with autocompletion, removes cards, and undoes.
Everything becomes one proposal when you press Review.

## Finding decks

**Search** finds public decks on Archidekt the way the Archidekt site does:
by deck name, commander, format, colours or the person who built it,
ordered by newest, most viewed or largest. Open any result to read it with
the same views as your own decks; the owner's name opens their profile with
every public deck they have. From a public deck you can run the statistics,
export it, or clone it into your own account (a proposal, like every
change).

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
   is an inbox: once its cards have gone somewhere it disappears.

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
  removes a card.
- Cards you own show a **green dot** on every deck page, your own decks and
  public ones alike, with the number of copies Archidekt knows about.
- **Export CSV** downloads the whole collection in the column layout
  Archidekt's own import reads.
- A scan is an inbox: once you save its cards to the collection or a deck,
  the scan is done with and disappears. Scans nobody touched for a month go
  too.

Your assistant can read and add to the collection too (`list_collection`,
`add_to_collection`, `remove_from_collection`), for questions like "which
cards in this deck do I not own yet?". Adding takes about a second a card,
because each one is written to Archidekt.

## Likes, bookmarks, follows and comments

Every deck page has Archidekt's own social buttons: **Like** (with the
deck's score), **Bookmark**, **Follow** its owner, and a **Comments** panel
that shows the deck's thread and lets you reply or add a comment. A user
page has the Follow button too. Each one asks you to confirm first, then
goes to Archidekt under your Archidekt name, exactly as if you had pressed
it on archidekt.com. These buttons are yours alone: your assistant has no
tool for any of them and cannot like, follow or comment for you.

## Proposals and history

No deck is ever changed directly. Every edit, new deck, clone, settings
change or restore is first saved as a **proposal**: the exact list of what
would change. Approving happens on the review page in your browser unless
the operator allows approving in chat.

- **Proposals** lists what is waiting. Open one to read the diff and press
  **Apply** or **Reject**.
- Before an edit is applied, the gateway checks the deck has not changed in
  the meantime, saves a **snapshot** and puts a backup copy of the deck in
  an "MTG Gateway backups" folder on your Archidekt account, then reads the
  deck back to confirm.
- **History** shows proposals, snapshots and deck reports over time.
  **Restore** puts a deck back exactly as a snapshot had it (another
  proposal you approve).
- **Reports** store a deck's statistics, a legality check and a goldfish
  simulation so you can follow how it develops.

## With an AI assistant

Connect Claude or ChatGPT from the home page ([CONNECT.md](CONNECT.md)).
You sign in with the same account as here, and the assistant acts as you:
it can read your decks but never change one without a proposal you approve.
Things to ask it: show my decks; open a deck and tell me its mana curve;
search for decks with a commander; look up a card, its rulings, prices,
combos; goldfish a deck; swap cards or build a new deck from a list (each a
proposal with a review link); read my card photos or use my last scan;
which cards in this deck do I own. The [assistant skill](SKILL.md) teaches
the house rules.

## The Android app

The [Android app](ANDROID.md) shows these same pages as a native-feeling
app with the phone camera built in. The app's own actions (scan with the
phone camera, reload, open in browser, change gateway) sit in the account
menu. On a foldable, the unfolded screen uses the same app layout as the
folded one. No Android phone? Add the site to your home screen from the
browser and it opens like an app, scanning included.

## Your account and privacy

The **Account** page shows who you are signed in as and your Archidekt
link. Unlinking removes the stored Archidekt session at once. **Sign out on
all my devices** ends every session and assistant connection you have. Only
you can see your decks, proposals, snapshots, scans and collection; another
member cannot reach them, and neither can their assistant. Nothing is
public or indexed. More in [ONBOARDING.md](ONBOARDING.md#8-privacy).
