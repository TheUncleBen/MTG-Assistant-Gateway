# Changelog

Notable changes for people who run or use the gateway. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) as described in
[docs/VERSIONS.md](docs/VERSIONS.md): every update to `main` is a new version
with its own image (`1.2.3`), git tag (`v1.2.3`) and read-only branch
(`release/1.2.3`); `latest` is always the newest.

## [0.7.45] - 2026-10-24

Shorter stack and example variables files with every feature on by default, the tuning settings on
the Settings page only, a new Settings row for the Archidekt backup folder, the Claude logo beside
Claude's connection, an assistant reading the same Forge seed the report page shows, Forge's
load problems leaving out Java's own warnings, a solo test shorter than five turns no longer talking
about turn 5, the research service having the current rules from the moment it starts, signing in
to the Android app coming back to the app by itself, and faster pages and deck deletes. The
navigation is simpler too: four places (Home, Decks, Binder, Assistant) and More, a settings-style
Account page behind your picture, and a tidier deck page.

### Upgrade notes
- **Start from the new stack file and example variables file**, as in
  [docs/UPGRADING.md](docs/UPGRADING.md), and copy your values in: the address and sign-in lines,
  the nodes, folders, `PUID`, `PGID`, the network, `MTG_TAG`, and any security or
  identity-provider line you changed. The new files name the exact research service, Forge and
  ntfy images and their limits, so there is nothing to change for those.
- **These are now only on the Settings page** (`/admin/settings`). A line left in your variables
  for one of them does nothing, so set any you had changed again on the page after the update:
  `MTG_SERVER_NAME`, `MTG_LOG_LEVEL`, `MTG_APPROVAL_MODE_DEFAULT`, `MTG_APPROVAL_MODE_MAX`,
  `MTG_AUTO_APPLY_MAX_ROWS`, `MTG_AUTO_BIG_EDIT_ROWS`, `MTG_PROPOSAL_EXPIRY_DAYS`,
  `MTG_ARCHIDEKT_CALLS_PER_10_MIN`, `MTG_ARCHIDEKT_MIN_INTERVAL`, `MTG_ARCHIDEKT_MAX_PER_MINUTE`,
  `MTG_ARCHIDEKT_RETRIES`, `MTG_ARCHIDEKT_BACKOFF_BASE`, `MTG_ARCHIDEKT_CACHE_SECONDS`,
  `MTG_ARCHIDEKT_CARD_CACHE_SECONDS`, `MTG_ARCHIDEKT_BACKUP_COPIES`,
  `MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT`, `MTG_ARCHIDEKT_BACKUP_FOLDER`, `MTG_SNAPSHOTS_PER_DECK`,
  `MTG_BACKUP_HOUR_UTC`, `MTG_BACKUP_KEEP_DAYS`, `MTG_FORGE_GAMES`, `MTG_FORGE_PARALLEL_GAMES`,
  `MTG_WATCH_MAX_CARDS`, `MTG_WATCH_MAX_DECKS`, `MTG_PRICE_HISTORY_DAYS`, `MTG_PRICE_HOUR_UTC`,
  `MTG_SCRYFALL_LOOKUP_INTERVAL` and every `MTG_SCAN_*` setting except `MTG_SCAN_ART_ENABLED`.
- **Removed:** `MTG_CHATGPT_NAME` (ChatGPT now shows the gateway name), `MTG_AUTHENTIK_API_URL`
  (the clean-up uses the issuer's address), `MTG_ALLOWED_HOSTS` (worked out from the public URL),
  `MTG_LISTEN_HOST`, `MTG_LISTEN_PORT`, `MTG_PLUGIN_DIR`, `MTG_APP_DIR`,
  `MTG_ANDROID_ASSETLINKS`, `MTG_ANDROID_PACKAGE`, `MTG_IMAGE`, `MF_IMAGE`, `MF_TAG`, `MF_NODE`
  (the research service may run on any node), `MF_MEMORY_LIMIT`, `FORGE_IMAGE`, `FORGE_TAG`,
  `FORGE_XMX`, `FORGE_MAX_METASPACE`, `FORGE_OOM_SCORE_ADJ`, `FORGE_GAME_SECONDS`,
  `FORGE_MAX_GAMES`, `FORGE_MAX_PARALLEL` (Forge works out games at once from its memory and CPU
  limits), `NTFY_IMAGE`, `NTFY_MEMORY_LIMIT`, `COMPOSE_PROFILES`,
  `COMPOSE_PROJECT_NAME`, `CADDYFILE` and `MTG_SECRETS_DIR`. With Compose, the Caddyfile stays at
  `deploy/proxy/caddy/Caddyfile` and the secret files in `./secrets/`. `MTG_FORGE_URL` is now a
  fixed line in the stack files.
- **Every feature is on unless you turn it off.** Deck edits now reach Archidekt unless
  `MTG_WRITES_ENABLED=false`, and watchlists are on unless `MTG_WATCHLISTS_ENABLED=false`.
  `MTG_ARCHIDEKT_BACKUPS`, `MTG_APPLY_IN_CHAT`, `MTG_SCAN_ART_ENABLED`, `MTG_UPDATE_CHECK` and
  `MTG_DECK_SYNC_MINUTES` work the same way: empty means on and changeable on the Settings page,
  and `false` (or `0` for the deck check) turns it off and locks its row.
- **Forge follows `FORGE_REPLICAS` alone** (`1` on, `0` off), with Compose too. If you had turned
  Forge off with `COMPOSE_PROFILES`, set `FORGE_REPLICAS=0`, or Forge comes back on.
- **Approval rules set in your variables.** If you capped what members may choose with
  `MTG_APPROVAL_MODE_MAX`, or changed `MTG_APPROVAL_MODE_DEFAULT`, `MTG_AUTO_APPLY_MAX_ROWS` or
  `MTG_AUTO_BIG_EDIT_ROWS`, set the same values on the Settings page right after the update and
  before members use their assistants: until then the defaults apply (members may choose any mode),
  and deck edits now reach Archidekt by default.
- **Backup folder.** The default name is now "MTG Gateway Backups". A member who already has an
  "MTG Gateway backups" folder on Archidekt keeps using it (the name is matched whatever its
  capitals); new members get "MTG Gateway Backups". The new Settings row "Backup folder on
  Archidekt" changes the name.
- No database change, so this update saves no before-upgrade copy. Going back to 0.7.44 needs its
  stack file and example variables file, with your values copied back in.
- No new secrets. The two `MTG_ANDROID_*` lines are gone because the gateway now takes the Android
  app's package ID and signing certificate from the app it ships.
- The Android app's automatic sign-in return needs this version of the app as well as of the
  gateway: the app offers the update itself (or download it from the gateway's Android app page).
- **Account details moved one step in.** Your picture now opens the Account page; the cards that
  were on it (approval mode, Archidekt, notifications, connected apps, your data) are under
  **Account details** (`/account/details`). Old `/account` links with a setting or a `#` section
  still open Account details, so bookmarks and notification links keep working.

### Changed
- **Simpler navigation.** The top bar, the phone's tabs and the Fold's rail show the same four
  places, Home, Decks, Binder (your collection) and Assistant, plus More with Search, Precons, Scan,
  Watchlist and My lists in two groups. The rail no longer scrolls. Assistant shows how many
  proposals are waiting, and a switch at its top moves between Proposals, History and Reports. The
  search box fills the top bar's free space, and on the rail the bell is an Alerts tab.
- **A new Account page** behind your picture, laid out like a phone's settings: Your account
  (Account details, My activity, Forum), Look (Theme and Layout, chosen in place), Assistants and
  apps (Guide, Assistant skill, the Android app, Admin for admins) and, in the Android app, This
  app (Scan with the app's camera, Reload, Open a copied link, Change gateway, Check for app updates
  with the installed version). Sign out is one button at its foot.
- **Decks page:** a My Decks and Packages switch at the top replaces the Show filter (My Decks lists
  your decks, Packages your packages), with New
  deck, New folder and New package beside it; the sort, view and size controls fit one row on an
  unfolded Fold.
- **Deck page:** Edit deck, Clone deck, Test and play (Goldfish, the full test and solo test, and
  playing on Archidekt or EDHPlay) and More. More is grouped (This deck, Look into it, Share and
  print, Community) and adds Deck reports. **Share** replaces Copy link: in the Android app it opens
  the phone's share sheet (with Open on Archidekt, Copy gateway link and Copy deck list on Android
  14 and later); in a browser it offers the gateway link, the Archidekt link and the deck list. The
  deck's waiting proposals are a **Proposals** button beside Like, Bookmark and Comments, with a
  count, instead of a strip on the page.
- **The Android app** no longer has Open in browser, and "Open in a new tab" shows on the website
  only.
 The example variables files and stack files list only what a
  deployment needs: about 90 lines instead of 350 for the variables, 290 instead of 460 for the
  stack.
- **ChatGPT's name** is the gateway name, cut at a word to 80 characters (ChatGPT refuses a longer
  one), in the plugin file and the setup steps alike. The "Name in ChatGPT" setting and its warnings
  are gone.
- **"Backup folder on Archidekt"** is a new row on the Settings page; a new name applies to the next
  backup copy.
- **Faster pages.** Pages go out compressed, so a long deck page is about a sixth of the size
  over the network. The gateway renews its Authentik token in the background before it runs out,
  so a page no longer waits for that renewal. It still asks Authentik whether you are allowed in
  before each page, exactly as before. Deleting a deck from its Delete box now waits for four
  Archidekt calls instead of six: the box reads the deck and finds the backup folder while you
  type the name. The backup copy is still Archidekt's own copy of the deck as it is when you press
  Delete.
- **Android sign-in returns to the app by itself.** After signing in, the browser no longer stops
  on a "Signed in" page waiting for you to tap **Open the MTG Assistant Gateway app**. Where your
  browser supports it (Chrome's Auth Tab), it closes and you are back in the app, signed in. The
  browser hands the sign-in back only to the app the gateway ships, checked against the app's
  signing certificate, which the gateway now publishes at `/.well-known/assetlinks.json` with no
  setting. Other browsers, or an app the gateway didn't ship, still get the page with the button.
- **What the quick solo test leaves out**, on a report's page, is laid out like the account
  settings lists. A summary card shows the bar and the three counts; the two left-out counts lead
  to their groups. Each group's explanation is folded under **Details** after its count. The cards
  are rows, in two columns on a wide screen, and the shortcuts are a list. **Copy a request for
  your assistant** and **My card models** are two even buttons under the cards played without their
  effect. "Left out on purpose" is now "Needs opponents", here, in the downloaded report and in the
  request for your assistant. The two solo tests are named "Quick solo test" and "Full-rules solo
  test by Forge".
- **Shorter lists** in what the quick solo test leaves out: a list of more than eight shows its
  first six and **Show all**, and the cards that need opponents sit in their categories
  (Counterspells, Removal and other interaction, Board wipes and so on), each opening on a tap.
- **Every Details and Show more fold** is a grey capsule with a chevron that turns when it opens,
  instead of blue or grey text: on the report page, the Account page (Approval mode, What your
  sign-in shares, the Archidekt link details), the collection's CSV help, card search's More
  filters, the new deck and compare pages, a contest's entries, the History page's numbers, a
  proposal's technical details and diff, a watched card's price table and the admin update steps.
  The Show all and Show more buttons (a precon set's decks, the goldfish game's steps, a deck's
  comments and a proposal card's changes) are the same capsule.
- **A commander** among the cards the quick solo test lists says "commander" instead of "drawn 0%":
  it starts in the command zone, so it is never drawn.
- **Android install and update steps** say that on a Samsung Galaxy phone Auto Blocker must be off
  to install the app or its updates, and that letting Play Protect scan the app or not are both
  fine. The release notes say the same beside the app's files.
- **Fewer obvious lines.** The Archidekt profile switches and default settings no longer say that
  each one saves when you change it.

### Fixed
- **The Claude logo** beside a Claude connection: Claude registers sign-in addresses on both
  claude.ai and claude.com, and only claude.ai was recognised, so it got the plain icon.
- **Forge seeds in Claude and ChatGPT.** The seed the gateway picks for a report's Forge games and
  its Forge solo test could be too large for the apps to show exactly, so an assistant read a
  rounded number that Forge never used. The gateway now picks seeds every app shows in full. Seeds
  you give yourself are used as they are.
- **Forge's load problems** no longer list the Java runtime's own start-up warnings ("Skipping
  jdk/proxy1/…: Unsupported location"), which an assistant could read as cards Forge could not load.
- **Short solo tests.** A solo test played to turn 3 said "made every land drop through T5" and
  gave the commander's cast "by T4, T5, T6". The text and the research service's own report now
  name the last turn the run played.
- **Rules right after a restart.** For a short while after the research service started, the rules
  tools answered from the older copy of the Comprehensive Rules built into its image, so two answers
  a minute apart could name different dates. It now starts fetching the current rules as soon as it starts.
- **Two-face cards in card search.** A card's second face (for example Swords to Plowshares on
  Emeritus of Truce) read like a separate search result; it is now headed "Other face of …".
- **Which printing a card gets** when no set is given is now described as it works, in the
  assistant's tool description and [docs/SCANNING.md](docs/SCANNING.md): Scryfall's own default
  printing when it is out on paper, which is not always the newest one, else the newest that is.
- **A pasted list's name.** A test run or deck check of a pasted list named it "pasted list", which
  an assistant could show as the deck's name; it now reads "Unnamed deck", like any deck without a name.
- **A Forge game with no result.** When Forge ended a game without printing its result, the
  report said "Games played: 4 of 5" and nothing about the missing game. It now names the game,
  says the results count only the games that came back, and keeps the end of Forge's log.
- **Cards the solo test lists.** Cards that only act on opponents or their creatures (votes,
  returning every creature to its owner's hand, a creature dealing damage equal to its power to
  another, Auras that shut a creature down, regeneration) are now listed under "Needs
  opponents", not with the cards it played without their effect. A creature whose ability exiled a
  creature and then did more (for example Displacer Kitten) counted as played with its effect
  while that ability never played; it is now listed as played without its effect.
- **More card text in the solo test.** It now reads landcycling and the other options it never
  uses (miracle, convoke, undaunted and similar: the full cost is paid), a graveyard shuffled back
  into the library, a land put onto the battlefield from your hand, and an equip cost for one
  creature type. Each shortcut is named in the report.
- **Validation wording.** The deck check said "All 82 unique cards verified on Scryfall"; it now
  says "found on Scryfall" in new answers, and on the pages of reports saved before.

## [0.7.44] - 2026-10-24

A review of the screens 0.7.42 and 0.7.43 added, brought to the same standard as the rest of the
app, and Show back face now shows the card's back picture, as Archidekt does.

### Upgrade notes
- None beyond the [update steps](docs/UPGRADING.md). There is no database change, so this update
  saves no before-upgrade copy, and going back to 0.7.43 needs only the `MTG_TAG` change.
- No new settings or secrets: your variables file works as it is.

### Changed
- **Show back face shows the back.** With the switch on, the deck page draws the card's back
  picture in every view, and the card viewer opens on the back with a Show front button. Cards
  with no back picture of their own (split cards, for example) keep the front.
- **Category boxes say what they do.** Delete unused categories names each category it deletes
  and counts them on its button; New category asks before it makes a Premier category or one
  that doesn't count in the deck; deleting a category is titled with its name, its cancel button
  reads Keep it, and after you pick where its cards move, the box says when that changes the
  commander or the deck's card count.
- **Deck settings' categories** line up as one grid: name, card count and Delete on the first
  line, the switches under them, on every screen size. Delete unused categories sits on its own
  line under Save categories.
- **Pages that work without scripts** ask you to pick where a category's cards move (no category
  is picked for you), list Cancel first, and show the final delete button in solid red. The
  remove-collaborator confirmation follows the same order.
- **New deck's preview** shows Theorycrafted and the game type.
- **Theorycrafted's help** reads "Left out of Archidekt’s “In decks” view unless you choose to
  show it."
- Card menu rows without an icon keep the icon's space, so their words line up.
- **Removing many copies of a custom card** asks in a box ending on a red button, instead of in a
  message at the foot of the page; a question left open when you leave the page is turned down.
- **Enter a deck in a contest** starts on "Pick a deck…" and lists only decks, not packages.
- **The forum** lists threads in two lines on a phone, shows a thread's section in its trail once
  the section's list has been read, and no longer repeats the section names above the table. A
  vote button shows an up arrow, Post reply has its icon, and a deck comment's ⋮ sits at the
  right edge.
- **My lists** counts every tab on the first visit and keeps the current tab in view; a custom card
  whose picture can't be shown is drawn with its name and type.
- **Account's Archidekt settings** show each switch's words once, and Price sources lines up with
  its two lists. Some of the page's wording is clearer.
- A custom card's note reads "Custom card, made on Archidekt: not on Scryfall, and not counted in
  the deck's price here."

### Fixed
- Card menu rows, the quantity buttons and the menu's other buttons were smaller than a finger on
  phones and tablets; they are now 44 pixels tall.
- Turning Companion on or off from the card menu didn't add or remove the Companion mark on the
  card until the page was opened again. The mark now follows the switch at once, Undo included,
  and in the list views it reads "Companion".
- The top bar's search box drew its magnifier over "Search cards and decks" on pages shown
  without scripts, and for a moment before scripts ran on every page.
- On a card tile without a picture, the green owned dot and the finish badge sat on the type line
  and the quantity corner could touch the name; the text now keeps clear of them at every card
  size.
- The card viewer left a void between its text and its buttons; the New deck box's Format field
  sat lower than Deck name and its preview tile floated mid-column; on a phone the third category
  switch sat on a line of its own; the no-script category delete pages mixed small grey print with
  body text and ran their sentences together in one paragraph.
- The × of the "Saved" toast on the deck page could wrap onto a line of its own (at 810 pixels,
  for example). Every toast's × now stays in the top right corner, finger-sized on touch, with
  the toast's text and buttons kept clear of it.
- A long deck page opened slowly on phones: the top bar measured its room and wrote it in turns,
  so the phone laid out the whole page several times as it loaded. It now measures once and
  writes once.
- In Change printing, with the keyboard on Use this printing, a card sliding under a resting mouse
  pointer redrew the preview and dropped the keyboard's place onto the page behind the box. The
  preview now stays put while the keyboard is in it.
- In every drop-down list (Group by, Sort by, Price sources, the folder pickers, My decks' Show
  filter), the current choice was too faint to read; it now has full contrast and an orange mark.
- The Archidekt notifications switch on the Account page showed the opposite of the setting.
- Holding a finger on a card or a post opened its menu, and lifting the finger could close it
  again; the menu now stays open.
- On a phone, choosing a menu item that opens another page or sends a form (Remove… on Card
  models, Apply or Reject on a waiting change, Clone, Run full test, Revoke tokens and sessions)
  could leave you on the same page, or bring you back to it a moment after the new page opened.
  The menu now finishes closing before the page changes.
- On touch screens, the Approval mode folds, the "Has a back face" switch, and the card viewer's
  close, − and + buttons are now 44 pixels, finger-sized.
- The Add template box said "Add 1 category" with nothing ticked; it now says "Add categories".
- The New package box, Package settings' Budget, the notification rows and a custom card's list
  row now line up with the rest of the app.

## [0.7.43] - 2026-10-23

More of what you can do on Archidekt is now in the gateway too: your account-wide lists, card
packages, your Archidekt profile and settings, the notification bell, deck help requests, reports to
Archidekt's moderators, contest entries, the deck registration sheet, Archidekt's forum and your
custom cards in decks. Your assistant can propose each of them; anything public or about your
account waits for your own press.

### Upgrade notes
- None beyond the [update steps](docs/UPGRADING.md). There is no database change, so this update
  saves no before-upgrade copy, and going back to 0.7.42 needs only the `MTG_TAG` change.
- No new settings or secrets: your variables file works as it is.
- **Not tried on Archidekt itself yet.** Everything new in this release follows how Archidekt's own
  site works and was tested with the gateway only. None of it has been used on Archidekt yet, so try
  each part on a test deck or with a small change first: My lists, card packages (with a color
  tag), your profile and settings, the bell, deck help, reports, contest entries, the registration
  sheet, the forum, and custom cards in decks with their pictures.

### Added
- **My lists.** One page for Archidekt's account-wide lists: category templates, default
  categories, color tags, pinned cards and custom cards, including a custom card's picture (checked,
  then redrawn as a 500×700 PNG before it is sent). Your assistant can read them and propose
  changes; a small change with no delete is low risk, and a delete always waits for your press.
- **Categories from your templates.** Deck settings has Add template, and a template's menu on
  My lists has Add to a deck…; your assistant can do the same.
- **Card packages.** New package on My decks (name, keywords, budget, intended formats, private,
  folder); a package's keywords, budget and formats in its Deck settings; Add a package in the deck
  menu, choosing from your packages, the ones you follow or everyone's, by name, format and budget,
  with Archidekt's options (keep the package's categories, let Archidekt file them, or put them in
  the Maybeboard; skip cards already in the deck or outside the commander's colors; one copy of
  each). The cards are added as one change you review first, and Archidekt counts one import of
  the package when it is applied. Adding more than 10 cards from a package is high risk. My decks
  has a Show filter (decks and packages, decks only, packages only), and a package's menu has Add
  to a deck….
- **A folder for a new deck.** New deck and New package can put the deck straight into one of your
  folders, and so can your assistant.
- **Your Archidekt profile and settings** on the Account page: bio, username, profile picture,
  e-mail and notification switches, and new device default settings. Each change is applied only
  by your own press. Password, e-mail address, Patreon and deleting your account stay on Archidekt,
  with a link to it. If Archidekt takes more than 3 seconds to send them, the page shows
  without them and says so, and the next load has them.
- **Collection visibility.** My collection says whether it is private or public on Archidekt, and
  can change it; making it public asks once first.
- **The notification bell.** Archidekt's notifications in the top bar and on their own page, with
  a link to what each one is about.
- **Deck help requests.** Request deck help in the deck menu posts the deck on Archidekt's deck
  help board, and lets you edit or delist it.
- **Reports to Archidekt's moderators** for a deck, a card or a comment, from the deck and card
  menus and a comment's menu.
- **Contest entries.** Enter in a contest and Withdraw from a contest in the deck menu. Entering
  replaces the deck's description with your summary, after a snapshot, and the review shows the
  old and new description side by side.
- **Registration PDF.** Archidekt fills the tournament deck registration sheet from a deck and the
  details you type; nothing you type is kept by the gateway.
- **Lock personal categories** in the card menu saves a card's template categories as its default
  categories.
- **Archidekt's forum.** A Forum page with Archidekt's sections, each section's threads and a
  thread's posts, and a reply box; your assistant can read the forum and propose a reply. A reply
  is public under your name and waits for your own press. Starting a new thread stays on Archidekt,
  which asks you to prove you're human first. The section and thread lists are read the way
  Archidekt's own forum pages load them, so if Archidekt changes those pages the gateway says so,
  and a thread still opens from its link or number. A post's menu has Reply, Copy link, Vote up,
  Vote down, Edit and Delete for your own, and Report comment…; the reply box has bold, italic
  and link buttons and shows your reply once more before posting it; a contest thread lets you
  enter one of your decks or withdraw it.
- **Custom cards in your decks.** Your custom cards show in your decks with their pictures, and you
  can put one in a deck (from My lists), change its count, category, color tag or mana value, or
  take it out, as on Archidekt; your assistant can propose the same. Taking custom cards out of a
  deck always waits for your own press, because restoring a snapshot doesn't put them back. My
  lists shows each custom card's picture too.
- **A color tag when adding a package.** Add a package can put one of your color tags on every card
  it adds, in the same change.
- **More device default settings:** Group by, Sort by and the two price sources, as on Archidekt's
  account settings.

### Changed
- **Approval mode** on the Account page is one line per mode, each with a fold that explains it.
- **Full auto** is now shown as “Apply changes without asking” everywhere, so each approval mode
  fits one line on a phone.
- **Deck comments** work like forum replies: the vote count and Reply sit under each comment, and
  Vote up, Vote down, Edit… and Delete… are in the comment's ⋮ menu.
- **The deck page's banner** text is lighter, and its shade deeper in the light theme, so the
  deck's details and dates are easier to read.

### Fixed
- **Search suggestions** stay open when you close a card's menu from them with Escape. On a slow
  phone they could close and not come back until you typed again.

## [0.7.42] - 2026-10-22

Everything you can do with a deck's categories and settings on Archidekt, you can now do in the
gateway too: create and delete categories, Theorycrafted, Game type, and a card's Companion and
show back face switches.

### Upgrade notes
- None beyond the [update steps](docs/UPGRADING.md). There is no database change, so this update
  saves no before-upgrade copy, and going back to 0.7.41 needs only the `MTG_TAG` change.
- No new settings or secrets: your variables file works as it is.

### Added
- **Create and delete categories.** Deck settings has a New category form with the three
  switches, a Delete button on each category, and Delete unused categories; your assistant can
  propose the same. Archidekt only deletes an empty category, so deleting one that still has cards
  asks which category its cards move to, moves them first, and deletes it only after a fresh read
  of the deck just before that delete shows it empty. On Deck settings every delete asks you to
  confirm. Delete unused categories deletes up to 30 at a time; as it reads the deck again
  before each delete, 30 categories take 60 Archidekt calls, roughly one to two minutes at the
  default pace of 30 calls a minute. Deleting Sideboard or Maybeboard,
  a Premier category or the Commander category is high risk and never applied by an assistant on
  its own. Restore under History makes deleted categories again and puts their cards back, and
  deletes a category added since the snapshot once its cards are gone (only the ones its review
  listed).
- **Theorycrafted and Game type** (Any, Paper, MTGO, Arena) in Deck settings and for your
  assistant, with snapshot, read-back and Restore like the other settings.
- **Companion and Show back face** in the card menu on your deck page (Show back face on two-faced
  cards), and for your assistant. A Companion change from an assistant always waits for your
  press, because it changes what the deck is legal with; so does removing the companion card or
  cutting its copies. Restore puts both back.

### Changed
- **Sideboard and Maybeboard without a record.** Archidekt's default Sideboard or Maybeboard can
  have no record of its own until someone changes it; Deck settings showed it read-only. It is now
  edited like any other category: the save gives it its record first.
- **A new deck keeps a category's switches.** A pasted list's category marked not in deck or not
  in price is made with those switches before any card goes in, instead of as a plain category
  with a warning. A list can mark up to 30 such categories, each 1 to 60 characters.
- **The Guide's ask for categories** reads "Add, delete or rename a deck category".
- **New deck** has Theorycrafted and Game type too, for you and your assistant.

### Fixed
- New deck's Empty tab refused to create the deck ("give exactly one of cards, decklist_text,
  csv_text or json_text"); it now creates an empty deck, as archidekt.com does.
- Deck settings' Main settings saves only what you changed on the page. Before, saving from a page
  opened before a change made elsewhere (by your assistant, or on Archidekt) put back the old
  Private, Unlisted, name, description, format or bracket.
- On Deck settings, Skip to content and the Main settings link in the section menu could land on
  the wrong place: the page and its Main settings form had the same name.

## [0.7.41] - 2026-10-22

### Upgrade notes
- None beyond the [update steps](docs/UPGRADING.md). There is no database change, so this update
  saves no before-upgrade copy, and going back to 0.7.40 needs only the `MTG_TAG` change.
- No new settings or secrets: your variables file works as it is.
- **The Android app changes:** install the new APK (the app offers the update by itself). Its
  buttons follow the site's look, Torch and Auto say "On" or "Off", and its error messages are
  plain sentences without codes.

### Added
- **Category rename and switches.** Deck settings lets you rename a deck's categories and turn
  Premier, Not in deck and Not in price on or off, and your assistant can propose the same. Every
  such change keeps a snapshot (and a backup copy when backups are on), checks that the deck has
  not changed since, writes one category at a time and reads the deck back; Restore puts the names
  and switches back. A change that touches the commander's category, Premier, Not in deck, or a
  rename to or from Sideboard or Maybeboard is always high risk, is never applied by an assistant
  on its own, and asks you to confirm on your own Deck settings page too. A Deck settings page
  left open while the deck changed elsewhere refuses to save instead of undoing that change, and a
  waiting card edit that names a renamed category waits as Needs a look. Creating and deleting
  categories stays on Archidekt.
- **My card models:** the card descriptions the simulator keeps for you, listed from the report
  and Goldfish pages, each with Remove.
- **Clone into a folder:** pick the folder when you clone a deck.
- **The Forge solo test on the page:** the report page shows the Forge solo test's result, and the
  Goldfish page shows the deck's newest one.
- **Official Claude and ChatGPT logos** beside the names of connected apps, shown only for an app
  the gateway can tell really comes from Anthropic or OpenAI; any other app keeps the plain icon.
- **Card size control** (S / M / L) everywhere card pictures show, remembered on your account.

### Changed
- **Every page follows one standard.** One-action pages became pop-up boxes on the page you were
  on: New deck, the admin dashboards, Collection, Precons, your profile, setup, and the sign-in
  and account pages. Lists, notices, buttons and app names look and read the same everywhere.
- **Faster.** Menus, boxes and tabs answer at once, long lists and the editor fill in steps, and
  the Proposals list reads what it needs in one go.
- **The Guide speaks in plain asks** ("Rename a category in my deck"); the tool names moved to a
  closed "Advanced: tool names" section at the end.

### Fixed
- A cloned one-card deck's change line said "1 cards".
- A deck with a category named like the start of an HTML comment (such as `<!--<script>`) showed
  a broken deck page: the Probability of draw calculator and the rest of the page below it did not
  load.

## [0.7.40] - 2026-10-21

When Forge is on, every report that simulates now also has Forge play the deck alone by the full
rules, beside the solo test's quick estimate: Forge plays each card it supports exactly as its
rules text says, and the report shows how often and on which turn the deck won. Forge is now on in the
example files for new installs.

### Upgrade notes
- Update as usual ([docs/UPGRADING.md](docs/UPGRADING.md)): the newest stack file and example
  variables file, with your values in it. The Forge image is new (`2.0.15-mag6`), and the newest
  stack file already names it.
- **Forge is on in the example files.** If you copy the new example file, Forge runs unless you
  turn it off ([Forge simulations](docs/DEPLOY.md#forge-simulations-on-by-default) in DEPLOY.md
  says how). Forge is limited to about 1.5 GB of memory and 2 CPU cores, and uses them only while
  it plays; [docs/HARDWARE.md](docs/HARDWARE.md) says what machine it needs.
- **Portainer or Swarm: `FORGE_NODE` is now required,** even with Forge off; set it to the node
  that runs Forge (any node name will do with Forge off). The stack won't deploy without it.
- To keep Forge off on a stack, set both `FORGE_REPLICAS=0` and an empty `MTG_FORGE_URL=`. With
  Compose: put a `#` in front of `COMPOSE_PROFILES=forge` and set an empty `MTG_FORGE_URL=`.
  Turning Forge off without emptying `MTG_FORGE_URL` leaves the gateway asking a Forge that isn't
  there; emptying it alone leaves Forge running unused.
- **If you keep your own variables file** instead of taking the new example: a file made from an
  earlier example file already has `FORGE_REPLICAS` and `MTG_FORGE_URL` lines, so Forge stays as
  you set it (on or off). On a stack you still need to add `FORGE_NODE` (above). A variables list
  with no `MTG_FORGE_URL` line at all now asks for Forge: add `MTG_FORGE_URL=` to keep it off.
- The reports table gains two columns, added in place, so this update saves no before-upgrade
  copy, and going back to 0.7.39 needs no restore.
- The Android app doesn't change.

### Added
- **The Forge solo test.** With Forge on, a report that simulates also has Forge play the deck
  alone against opponents who do nothing (60-card decks too), counted up to turn 10, and shows how
  often and on which turn it won, in the report your assistant reads and in the report's Markdown
  download. It starts after the report's games against precons, so it never holds them up. Forge
  shares about four and a half minutes of play between the games (Java starting up and any wait
  for Forge come on top, so it often takes longer), and games the time limit stopped before turn
  10 or a win are left out of the numbers, and the report says how many. Each member has one at a
  time (two for reports that share opponents); a report made while another is still playing gets
  none, and says so, as does one made while other members' Forge games wait their turn, which
  go first. A report now counts as finished, and you are notified, only once its Forge
  solo test has ended too. Running the report again later plays one that was skipped or failed.
  Deleting the report stops it. An older Forge image skips it and the report says so.

### Changed
- **Forge is on by default** in the example stack, Compose and variables files.

### Fixed
- **Delete my data** (and an administrator's delete of a member's data) now also stops that
  member's unfinished Forge games, as deleting one report does.

## [0.7.39] - 2026-10-21

Fixes from a security review of 0.7.31 to 0.7.37, and small fixes to card search and the
Proposals page.

### Upgrade notes
- None beyond the [update steps](docs/UPGRADING.md). Coming from 0.7.37 or 0.7.38 there is no
  database change, so this update saves no before-upgrade copy, and going back needs only the
  `MTG_TAG` change. Coming from 0.7.36 or older, the database is upgraded as before.
- At its first start the gateway deletes the copies in `before-upgrade` (set-aside copies too)
  that are older than `MTG_BACKUP_KEEP_DAYS` (14 days unless you changed it), the newest included,
  and brings the rest in line with the database (below).
- Going back by itself works only within `MTG_BACKUP_KEEP_DAYS` of an upgrade; raise it before
  you update to keep the way back open longer.
- The Android app doesn't change.

### Security
- The copies saved before database upgrades, and the set-aside copies made when going back, are
  kept no longer than the nightly backups (`MTG_BACKUP_KEEP_DAYS`), the newest included. An
  unlink, a disable, a removal from the gateway's group, **Disconnect my phones** and **Delete my
  data** are applied to every one of them at once, as the “Before you link” text says, and so is a
  relink's replaced session; a copy that can't be changed is deleted. At every start the copies
  are also brought in line with the database, so what ended under an older version is ended in
  them too: deleted members are forgotten (in a set-aside copy too, where members who joined after
  the upgrade stay), members who are gone or disabled keep no sign-in tokens, and Archidekt
  sessions and phone keys the database no longer holds are blanked.
- When you go back from a later version to 0.7.39 or newer, each member keeps the Archidekt link
  and the phone connection they have now: an Archidekt account they unlinked or replaced since the
  upgrade, and phones they disconnected, don't come back.
- Going forward again with a set-aside copy put back by hand: if the copy was made by 0.7.39 or
  newer, the newer version ends every sign-in at its first start on it. docs/UPGRADING.md says
  what to do with a copy made by 0.7.37 or 0.7.38, and what to check on the Users page afterwards.
- The My decks folder and deck boxes, deck search's Load more, tag suggestions, deck downloads,
  the JSON API used with a browser sign-in and the other signed-in reads that ask Archidekt (or
  Scryfall, for card search) now answer only requests from the gateway's own pages: a link on
  another site can't make them spend a member's allowance.
- Nightly backups no longer keep members' phone keys (their notification topics). After a
  restore, members who use notifications press **Save notifications** in the app again.
- The gateway remembers the folder it puts backup copies in, so the backup folder renamed on
  Archidekt (or a folder it was moved into) can't be deleted like any other folder, even with no
  copy left inside. Deleting a folder is also refused when it holds backup copies the gateway
  made.
- The before-upgrade folder is made owner-only even when it already existed with looser
  permissions.
- The “Before you link” text and the member guide say what the copy saved before a database
  upgrade keeps, and for how long.

### Changed
- Card search fills the screen and a few rows more, then loads more cards only from the Load more
  button, never by scrolling.

### Fixed
- Card search on a computer: the cards Load more adds no longer move the Add to deck buttons
  already on screen, and they get their ⋮ menu button too. Every card tile is now the same
  height: the mana cost keeps one line and the type line two (the card viewer shows all of a long
  one).
- Proposals: a proposal whose risk can't be judged shows "—" instead of High, also while it
  waits (it still waits for your own press).
- The 0.7.35, 0.7.36, 0.7.37 and 0.7.38 sections of this changelog carry their release dates.

## [0.7.38] - 2026-10-21

The solo test plays far more of a deck's cards with their effect. It now reads the common card
text it used to skip (drawing, ramp, tokens, damage, anthems, cost reducers and more), so across
Forge's 85 recent Commander precons, about a third of a deck's cards played with their effect
before and about half do now. Removal, board wipes and counterspells, which have nothing to hit in a solo test, are set
aside and said so, instead of counting against the deck.

### Upgrade notes
- Update as usual ([docs/UPGRADING.md](docs/UPGRADING.md)): the newest stack file and example
  variables file, with your values in it. The research service and Forge images are new
  (`1.3.2-mag5` and `2.0.15-mag5`), and the newest stack file already names them.
- No database change, so this update saves no before-upgrade copy.
- The Android app doesn't change.

### Added
- **[docs/HARDWARE.md](docs/HARDWARE.md):** the machine you need, with and without Forge, and how
  long tests took on the machines we timed.
- **Cards Forge cannot play**, now listed in [docs/CAPABILITIES.md](docs/CAPABILITIES.md): the
  Unfinity sticker and Attraction cards, and Alchemy cards that are not Commander-legal.

### Changed
- **The solo test reads more card text.** Cards that draw, gain life, make Treasure or
  creature tokens, deal damage or drain opponents, add +1/+1 counters, fetch lands or cards, make
  extra mana or extra combats, pump your creatures, give haste, let you play more lands, lower
  costs or double tokens now do that in the solo test, from when they enter, when you cast
  spells, at upkeep, in combat, or from an ability with a plain cost. Prowess, double strike and
  defender count too, and the monarch's extra card is drawn at your upkeep. Cards with hybrid or Phyrexian mana costs are cast.
- A permanent whose text the solo test can't fully read is still cast, and does the parts it
  can read, unless the part it can't read limits the rest (such as "only once each turn"): then
  it is cast without its effect. The report names what it left out. Before, any card with removal or a counterspell
  in its text was never cast at all, even a creature.
- Removal, board wipes and counterspells are listed as set aside on purpose, not as cards the
  solo test can't play.
- Forge's build check now also measures the cards legal in Commander, and fails below 99.6% of
  them. Today it plays 99.71% of Commander-legal cards and 99.60% of all cards legal anywhere.
- Forge games run a little faster (about 5% on average over 6 paired test games on one x86
  machine; not timed on a Pi).

## [0.7.37] - 2026-10-21

Updating is now a fresh install on top of your data: take the newest stack file and example
variables file, put your settings in, deploy. There is no backup to take first: before a new
version upgrades the database, the gateway saves a copy of it, and going back to an older
version puts that copy back by itself.

### Upgrade notes
- Update the new way ([docs/UPGRADING.md](docs/UPGRADING.md)): the newest
  `deploy/portainer-stack.yml` (or `docker-compose.yml`) unchanged, the newest example variables
  file with your values in it.
- **One new Docker secret, `mtg_authentik_api_token`, if you don't have it yet.** The stack file
  and `docker-compose.yml` now always attach it, so you no longer remove the `# ` from its lines.
  If you don't use the removed-member clean-up, this secret holds the word `off`: on Swarm,
  command 5 under Option B in [docs/DEPLOY.md](docs/DEPLOY.md#4-docker-secrets-once) step 4 makes
  it (the commands there now skip secrets you already have), or add it under **Secrets** in
  Portainer; with Compose, run the new `make-secrets.sh` again. If you
  already use the clean-up, your secret stays as it is.
- No database change, so this update saves no before-upgrade copy, and going back to 0.7.36 needs
  only the `MTG_TAG` change (with 0.7.36's stack file, whose Authentik lines are commented out).
- The Android app doesn't change.

### Added
- **A copy before every database upgrade.** When a new version has to upgrade the database, it
  first saves a full copy in `before-upgrade` inside the backup folder, checks it, and keeps the
  newest 3. Without a good copy (a full disk, say) it doesn't upgrade or start, and its log says
  why. The copy keeps members' Archidekt links, still encrypted, so going back needs nobody to
  relink; it is never copied to the second backup folder.
- **Going back by itself.** From this version on, an older version started on a database a newer
  one upgraded puts back its before-upgrade copy, after saving the newer database beside it so
  nothing is lost, and everyone signs in once more. The admin page says when this happened.
- The admin page's System card names the newest before-upgrade copy and where it is.
- Going back keeps what must not come undone: members disabled, Archidekt sessions deleted and
  members' "Delete my data" since the upgrade stay so.

### Changed
- A database upgrade happens whole or not at all: one that fails or is cut off leaves the
  database as it was, and the older version still starts on it. A start that keeps failing saves
  its before-upgrade copy once, not on every try.
- The `mtg_authentik_api_token` secret holding the word `off` turns the removed-member clean-up
  off with no warning.
- docs/UPGRADING.md, docs/DEPLOY.md and docs/DEPLOY-COMPOSE.md describe an update as the install
  steps again; the extra steps 0.7.29 to 0.7.33 needed are kept at the end of UPGRADING.md.

## [0.7.36] - 2026-10-20

Decks, folders and cards get a menu on every page (right-click, press and hold, or the ⋮
button), and the actions that had a page of their own open as a box instead. Folders can be moved
and deleted from My decks. Card search, deck search and My decks share one search panel, and the
pages fit phones, an open Fold, tablets and computers the same way.

### Upgrade notes
- From 0.7.35: no extra steps. Update as usual ([docs/UPGRADING.md](docs/UPGRADING.md)).
- No database or stack change.
- The Android app changes too (its camera screen), so it is rebuilt with this release.
- Deleting a folder is a new kind of Archidekt write: it deletes everything inside the folder on
  Archidekt in one step. The gateway saves every deck first and asks for the folder's name.

### Added
- **Menus** on decks, folders and cards everywhere they show: right-click, press and hold on a
  phone, the ⋮ button, or Shift+F10 on a keyboard. Rows on Proposals, History, Reports, the
  Watchlist, the Collection and the Scan list have one too.
- **Move** and **Delete** a folder from its menu on My decks. A delete lists how many decks, card
  packages and folders are inside and asks you to type the folder's name; every deck gets a
  snapshot (and a copy in your backup folder when backups are on) before anything is deleted, and
  the delete stops if one of them fails. Card packages get no snapshot or copy. The backup folder
  is never moved or deleted, and a folder holding it can't be moved or deleted. The assistant can't move or
  delete folders.
- My decks filters by colors and format, sorts in either direction and shows the number of decks.
- Deck search opens an Archidekt deck from a link or deck ID typed in its name box, and shows more
  decks with **Load more decks**.

### Changed
- Renaming, moving, exporting, comparing and deleting a deck, and making or renaming a folder, open
  as a box over the page. The old page addresses still work.
- The Folders page is gone: its address opens My decks, where folders are made, renamed, moved and
  deleted.
- Card search, deck search and My decks share one search panel: the name box, the filters, and a
  bar at its foot with the count, sort, direction, grid or list, card size and Clear filters. On a
  phone the filters open in a sheet.
- Every filter where you can pick several (rarity, for example) is a drop-down with checkboxes.
- Card search's Commander colors can match within, exactly or including the colors you pick.
  Deck search and My decks match decks including, exactly or at most the colors you pick, and deck
  search sorts in either direction.
- An open Fold and tablets get the side rail, in the app and in a touch browser up to 1279 px
  wide; a computer browser keeps the top bar.
- The Scan page is one column at every width, with its options on one row and a round capture
  button. The app's camera screen has the same round button.

### Fixed
- A saved scan could write its list into another tab after you switched tabs.

## [0.7.35] - 2026-10-20

The gateway's pages follow one look: the same status and risk words everywhere, tables that turn
into two-line rows on phones, a row of widgets at the top of Proposals, History, Reports and the
Activity pages, and searchable, paged lists. Proposals lists every kept proposal and, on a wide
screen, opens one beside the list; History keeps snapshots, restores and deck reports. A press and hold on a card in the deck
editor no longer leaves the page stuck.

### Upgrade notes
- From 0.7.34: no extra steps. Update as usual ([docs/UPGRADING.md](docs/UPGRADING.md)).
- The database moves to schema 15, which only adds one column (each closed proposal's risk). The
  upgrade is automatic. Going back to 0.7.34 needs the backup taken before the upgrade, as for
  every schema step.
- The Android app changes too (its launcher shortcuts), so it is rebuilt with this release.

### Changed
- **Proposals** lists every proposal the gateway keeps, waiting and past, 25 at a time, with no
  limit of 20. Search by deck or card, and filter by state, risk and app. Waiting ones are grouped
  by deck, and you can apply or reject several at once. Widgets at the top show what is waiting,
  the changes that always need your press and the last apply you can undo. A proposal opens beside
  the list on a wide screen, or on its own page.
- **History** shows snapshots, restores and deck reports, grouped by day or by deck, with a search,
  a deck filter and a time filter. Proposals are no longer listed there.
- Status and risk use the same words on every page and on the chat cards: Waiting, Updated, Needs a
  look, Held, Applying, Applied, Failed, Rejected, Expired, Withdrawn and Replaced; risk is Low,
  High, Your press or Delete. Each label has a word, a colour and an icon.
- Dates show as "2 h ago" up to a week, then as "Oct 12", in your own time zone. The full date
  and time is in the tooltip.
- **Reports**, a report, the **Watchlist**, **Home**, **Settings**, the admin **Activity** page, **My
  activity** and **Account** follow the same layout. On Reports, picking a filter shows the
  matching reports at once; there is no Show button.
- **Card search** has a Watch button on each card, and a card's menu (right-click or press and
  hold) can open it, watch it, show its art variants or add it to a deck. The deck editor's card
  search has the same Watch button and a card menu of its own (no Add to deck, since a row already
  adds to this deck), and the top bar's card suggestions have the menu.
- The top bar shows **Reports**, and **Watchlist** while watchlists are on, with **More** holding
  the rest on narrower screens. The app's launcher shortcuts add Reports, and Watchlist while watchlists are on.
- Settings that your stack sets show "Set in your stack", and a setting you lowered says what it
  was before.
- Activity names every kind of event in plain words.
- Account has **Send it now**, which sends your daily or weekly summary to your phones when you
  press it.

### Fixed
- A press and hold on a card in the deck editor, on Android, no longer leaves the card faded and
  the page refusing taps. A pen press works like a finger.
- The Scan page's capture button stays round in the Android app.

## [0.7.34] - 2026-10-19

Approval cards in the chat keep up with their proposal, and the assistants are told to check a
deck edit before they make a card for it.

### Upgrade notes
- From 0.7.33: no extra steps. Update as usual ([docs/UPGRADING.md](docs/UPGRADING.md)).

### Added
- `check_deck_changes`: an assistant can check a planned deck edit before it proposes it, with no
  card in the chat, nothing saved and no request to Archidekt (a card name may be looked up on
  Scryfall). It finds what a proposal would be refused for that can be found without the deck, an
  exact repeat of a change already waiting, and the assistant's own waiting change on that deck
  that it should amend instead of making a new one. Anything that needs the deck itself, such as a
  card that isn't in it, is still found only when the proposal is made.

### Changed
- A proposal card already in the chat follows its proposal. It reads the proposal again when it
  comes on screen, a few times while it stays there, and when you press Approve. When the
  assistant changed the proposal, the card shows the new version with a short line saying so.
  Where the app passes the card the new version's approval code, its Approve approves that
  version; otherwise the card offers the review page. When the change was updated after the card
  last read it and you press Approve, the card shows the new version and asks you to press again.
  When the assistant withdrew it, the card says so. A change replaced by another comes back when
  its replacement is rejected or withdrawn, and its card then shows it waiting again.
- The assistants' guides and the deck tools' descriptions tell them that, in an app that shows
  cards, every proposal, amend or combine puts a card in the chat, a refused one too, and to check
  a deck edit first.

### Fixed
- A proposal card drawn before the proposal was amended answered every press with "There is a
  newer version of this change than this card shows", so only a newer card or the review page
  could approve it. It now shows the new version.

## [0.7.33] - 2026-10-19

Fewer cards in the chat: only the changes waiting for you and the two pickers still show one. After
you approve a proposal on its card or press Apply all, the card can now post a short message so
the assistant carries on, where the app allows it. A card added to the maybeboard or sideboard keeps the printing you picked,
Forge can play up to 16 games at once on a machine big enough, and the gateway is gentler on
Archidekt by default.

### Upgrade notes
- From 0.7.32: the stack file and the Compose file name a new Forge image, `2.0.15-mag4`; take
  the new files as the update steps describe ([docs/UPGRADING.md](docs/UPGRADING.md#0733)).
  You need that image only to play more than 4 Forge games at once. With the older image, a
  higher setting plays at most 4 at once.
- The gateway now sends at most 30 Archidekt requests a minute by default, down from 40. To keep
  40, set "Archidekt requests per minute" on the Settings page, or `MTG_ARCHIDEKT_MAX_PER_MINUTE=40`
  in your variables.
- If you turned on the optional `mtg_authentik_api_token` lines, turn them on again in the new
  file ([docs/UPGRADING.md](docs/UPGRADING.md#0733)).

### Changed
- Only the changes waiting for you (a proposal with Approve and Reject, and the waiting changes
  with Apply all) and the two pickers (a card's printings, and the cards read from your photos)
  still come as cards in the chat. Your account, your deck list, a deck, a restore point, a deck's
  statistics, a comparison of two decks, a deck search and a scan's results now come as the
  assistant's own words, which keeps a long chat light. The gateway still serves the cards already
  in older chats, so an app that shows them again can still draw them.
- The assistants' guides ask them to finish their review before proposing: one proposal with every
  change, amended while it is still waiting, instead of several in a row.
- After you approve a proposal on its card, or press Apply all on a card, and the change is applied
  or under way, the card posts a short message, "Approved, go on", in the chat so the assistant
  carries on at once, where the app lets a card do that. Elsewhere the assistant carries on when
  you next write, as before. A row's own Approve on the waiting changes card posts nothing, so
  approving several rows one by one doesn't start a reply for each.
- A card added to the maybeboard or sideboard keeps the printing you picked, from Card search, the
  deck editor or an assistant, and the editor keeps its Foil box there too. Before, Card search and
  the deck editor left the printing out, so a new row got one Archidekt picked, and an assistant's
  proposal with one was refused. Undo of a removal on the deck page puts a maybeboard or sideboard
  row back in its printing too.
- "Forge games played at once" (`MTG_FORGE_PARALLEL_GAMES`) and `FORGE_MAX_PARALLEL` now go up to
  16, for a machine with the CPUs and memory for them (about 2 CPUs and 1.2 GB a game). Left at
  their defaults, a report still asks for 4 at once and Forge plays as many of those as its
  container's CPUs and memory fit (1 inside the default limits), so nothing changes until you raise
  "Forge games played at once" above 4 and give Forge room for more games (`FORGE_MAX_PARALLEL`, or
  its CPU and memory limits).
- "Archidekt requests per minute" defaults to 30 instead of 40. The Settings page still allows 1 to
  40.
- Busy, limit and error messages no longer promise a time, such as "try again in a minute" or "in
  a few minutes"; they say to try again later, because the gateway can't know when Archidekt,
  Scryfall or the sign-in service will have room again. Some messages still name a wait the gateway
  does know, such as the seconds Archidekt or Scryfall asks for, or the minute between two
  notification tests.

### Fixed
- On the waiting changes card, a row applied with its own Approve said "The deck on Archidekt
  matches this change." whatever it was, such as a new folder or a collection change. It now says
  what the change did.
- The member guide said deleting a deck, creating a folder, likes, comments and follows have no
  assistant tool, and that the card in the chat only opens their review page. An assistant can
  propose each of them, and only your own press applies them, on the card or on the review page (a
  deck deletion, and an edit of a comment that has formatting, on the review page only).
- In the deck editor, adding a card that already has a row there, with a picked printing or Foil
  ticked, only grew that row's count, while the status line named the picked printing. It now says
  the copies already listed keep their own printing and finish.
- When Archidekt asked the gateway to wait, the "Archidekt is busy" page always sent
  `Retry-After: 60`. It now sends how long the gateway is pausing its Archidekt requests.
- Adding the same new card to the deck and to the maybeboard or sideboard in one change, without a
  picked printing, could give one of the two rows the other's finish (foil or etched).

## [0.7.32] - 2026-10-19

The example settings files now list every variable the stack files read, each marked required or
optional, and the stack files now pass every setting on the Settings page to the gateway. This
version also fixes what the 0.7.31 test round found in the assistants' deck tools, Card search, the
in-chat cards, deck reports and the Settings and Account pages.

### Upgrade notes
- From 0.7.31: the stack file and the Compose file changed; take the new ones as the update
  steps describe ([docs/UPGRADING.md](docs/UPGRADING.md#0732)). They now pass 27 more variables
  themselves: the 21 settings under Fixed below, `MTG_ANDROID_PACKAGE`, `MTG_ANDROID_ASSETLINKS`,
  `MTG_PLUGIN_DIR`, `MTG_APP_DIR`, `FORGE_MAX_METASPACE` and `FORGE_OOM_SCORE_ADJ` (the Compose
  file also passes `MTG_ARCHIDEKT_BASE`). If you added a line for any of them under
  `environment:` in your old file, don't copy that line into the new one. Put its value in your
  variables (Portainer's, `mtg.env` or `.env`) instead. Any of the 21 settings your variables
  already set now reaches the gateway and locks that setting on the Settings page. Remove it from
  your variables to manage the setting on the page. Because these values now reach the gateway,
  one outside its range or not a number stops it from starting, so check them before you deploy;
  the update steps list each range.
- If you turned on the optional `mtg_authentik_api_token` lines, turn them on again in the new
  file ([docs/UPGRADING.md](docs/UPGRADING.md#0732)).

### Changed
- `deploy/stack.env.example` and `deploy/compose/.env.example` list every variable their stack
  file reads (the Compose example also lists the proxy overrides' variables), each with the value
  used when it isn't set (`MTG_WRITES_ENABLED=true` is the one exception, and its note says so).
  Each note says whether the variable is required or optional, and for an
  optional one, whether it is on the Settings page when the file doesn't set it. Setting one of
  those in the file locks it there. Newly listed, all commented out: `MTG_NTFY_URL`,
  `MTG_NTFY_TOKEN_FILE`, the 21 settings under Fixed, `MTG_ANDROID_PACKAGE`, `MTG_ANDROID_ASSETLINKS`,
  `MTG_PLUGIN_DIR`, `MTG_APP_DIR`, `FORGE_MAX_METASPACE` and `FORGE_OOM_SCORE_ADJ`. The stack
  files now pass the last six too, so they can be set from your variables without editing the
  stack file. The Compose file now passes `MTG_ARCHIDEKT_BASE` as the stack file does, and the
  Compose example lists it. In the Swarm example, `FORGE_XMX`, `FORGE_MEMORY_LIMIT`, `FORGE_CPUS`,
  `FORGE_GAME_SECONDS` and `FORGE_MAX_PARALLEL` are now commented out, as in the Compose example;
  the stack file uses the same values, so nothing changes.
- The Forge image notes in the examples and the environment reference now say that the stack file
  names one exact Forge version, which changes only when you deploy a newer stack file, and that
  setting `FORGE_TAG` pins Forge on purpose. `FORGE_XMX` is described as each Forge game's Java
  memory.
- The update steps in [docs/UPGRADING.md](docs/UPGRADING.md) and the removed-member clean-up steps
  in [docs/IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md) now say that a new stack file has the optional
  `mtg_authentik_api_token` lines commented out again, so the edit that turns them on has to be
  made again on each such update. They also say that the Repository build method can't keep
  that edit.
- When the Authentik API token file named in `MTG_AUTHENTIK_API_TOKEN_FILE` isn't there at all,
  the admin page's removed-member clean-up line now names the likely cause: the secret isn't
  attached to the gateway because its three lines in the stack file are still commented out.
- The notifications section of [docs/DEPLOY.md](docs/DEPLOY.md) no longer names ntfy's per-visitor
  limits as if they were settings. It says which values there are fixed and that
  `NTFY_MEMORY_LIMIT` is the one limit you can set.
- The deck report's "Copy a request for your assistant" now also lists, as context, the cards the
  solo test left out on purpose, the ones it played that never mattered and the shortcuts it took,
  and asks where any of them would play differently in a real game.
- `list_my_proposals` and `list_snapshots` page through everything (`limit` up to 100, `offset`,
  `has_more`, `next_offset`) instead of stopping at the newest 20. `list_my_proposals` takes
  `deck_id: "collection"` for collection changes, and `list_snapshots` can keep one deck's.
- `list_my_proposals` with `oldest_first` (and without `pending_only`) now starts from your oldest
  proposal and pages forward from there. Before, it gave your newest 20, shown oldest first.
- `get_proposal` says where an applied proposal was applied (`applied_via`).
- An applied account action, collection change or deck deletion says what it did ("The folder is
  created on Archidekt.") instead of "The deck on Archidekt matches this proposal."
- Collection results give each row's copies now, before, and what the proposal added or removed.
- A deck edit that adds a name no Magic card has is refused when it is proposed (when the gateway can
  check the name), instead of failing when it is applied.
- The in-chat cards no longer draw a border of their own inside the app's frame, which looked like a
  card within a card on ChatGPT.
- The assistants' guides say that, apart from linking Archidekt, they never need you to sign in to
  archidekt.com or the gateway for them: they read their changes back with their own tools.
- The Settings page's help for "Forge games per report" and "Forge games played at once" says which
  is the total and which is how many play side by side.
- The Account page's summary choice is labelled "How often", and outside the app the notifications
  hint gives the steps in order: open the Account page in the app, tick Send me notifications and
  press Save notifications.
- Archidekt requests made for your own pages in the browser now take their turn before waiting
  requests from assistants and the deck sync. A fourth Archidekt action for your account while three
  are running or waiting now waits up to 20 seconds for a turn instead of being refused at once. The
  gap between requests, the per-minute limit and each member's 10-minute allowance stay the same.
- While Archidekt can't be asked (it asked the gateway to slow down, it can't be reached, or your
  account's Archidekt actions or allowance leave nothing free), My decks and the home page (in the
  browser and the app) show your deck list as it was last read in the past day (not after a
  restart), with a notice saying how old it is, instead of the "Archidekt is busy" page. When it
  knows why, the "Archidekt is busy" page now says so.

### Fixed
- 21 settings on the Settings page couldn't be set from your variables alone. The stack files
  didn't pass them to the gateway, so a value set only in your variables did nothing until you
  also added a line for it to the stack file. They are the approvals' big-edit rows and proposal expiry
  (`MTG_AUTO_BIG_EDIT_ROWS`, `MTG_PROPOSAL_EXPIRY_DAYS`), the backup copies kept, the backup
  box's default and the restore points per deck (`MTG_ARCHIDEKT_BACKUP_COPIES`,
  `MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT`, `MTG_SNAPSHOTS_PER_DECK`), Forge games at once
  (`MTG_FORGE_PARALLEL_GAMES`) and the 15 card scanning settings (`MTG_SCAN_*`). Both stack files
  now pass them, empty unless your variables set them.
- A new deck made from a list or a CSV lost each card's colour label, and the check after creating
  it still said the deck matched. Labels are now kept, and the check compares them.
- Card search's set and type lists could take half a minute to show, and asked again and again
  while Scryfall asked the gateway to wait. They are now read in the background as the page opens and
  kept for a day, so they show at once.
- `list_my_decks` gave no folder name and no cover for decks that have them, so filtering by folder
  found nothing. A deck in the backup folder could also show in My decks for the same reason. When
  the folder names can't be read from Archidekt, `list_my_decks` now says so.
- Looking up someone you follow read only the first 5 pages of the list, so a person further down
  was "not followed" when adding a collaborator, and a follow couldn't say whether you already followed
  them. Both now read on until they find the person, up to 20 pages. A lookup repeated within two
  minutes, or made at the same time, doesn't read the same pages again, so someone you followed on
  archidekt.com in those two minutes is found once they are up.
- The summary counted a deck report as finished while its Forge games were still running.
- A 60-card deck's report showed a Commander check asking for 100 cards, and a run shorter than five
  turns showed a turn-five land-drop rate.
- A card named without a printing could get a printing from a set not released yet.
- `list_my_proposals` with `pending_only` left the commanders of a commander change out of each
  proposal's `cards`. A change made before this version lists them once the gateway next reads its
  deck (opening the deck's page, or a new proposal or an apply on that deck).
- `search_decks` answered `count: -1` for a search that matched nothing; it now answers null, as
  when Archidekt gives no count.
- A deck deletion's restore point didn't name its proposal.
- `edhrec_average_deck`'s `limit` was described as capping the cards; the average deck is always
  whole, and the description now says so.
- When a decimal setting that isn't a time, such as `MTG_SCAN_GLARE_RATIO`, wasn't a number, the
  gateway's start-up error said it must be "a number of seconds". It now says "a number".

## [0.7.31] - 2026-10-18

A documentation release: the example settings files are reorganised, and the guides are checked
against the code and corrected. Apart from some wording in the Android app, the in-app Guide, the
Account page and the admin page, and the example reverse-proxy files, nothing changes in how the
gateway or the app works.

### Upgrade notes
- From 0.7.30: none; update as usual ([docs/UPGRADING.md](docs/UPGRADING.md)). Optional: if your
  Swarm stack's variables (in Portainer, or in `mtg.env` without it) set `FORGE_IMAGE` and
  `FORGE_TAG`, you can delete those two, so Forge follows the stack file's own version on later
  updates.
- From 0.7.26 or earlier: follow the 0.7.30 steps in [docs/UPGRADING.md](docs/UPGRADING.md#0730),
  which include the 0.7.29 steps. They moved there from DEPLOY.md and DEPLOY-COMPOSE.md. On the
  way, the members' notification steps were corrected, and the note about waiting for the Forge
  image to be published was removed.

### Added
- New [docs/UPGRADING.md](docs/UPGRADING.md): what to do before every update, the update steps
  for Portainer, Swarm and Docker Compose, and the extra steps some versions need. The 0.7.30 and
  0.7.29 steps moved there from the deployment guides, which now describe only the current
  version. From now on each version's notes have an **Upgrade notes** section after their summary.

### Changed
- `deploy/stack.env.example` and `deploy/compose/.env.example` are grouped in a new order that
  reads top to bottom: images, server settings, placement (with Compose, where it listens) and
  storage, standard container settings, sign-in, notifications and watchlists, deck edits, Forge,
  sessions and tokens, AI clients, network trust, and the lines to leave as they are. The Compose
  example then ends with the optional proxy overrides. The values are the same. The Compose
  file's example gains a Forge section (off by default), and the Swarm example's Forge image and
  version lines are commented out, so a copied file follows the stack file's own Forge version.
  Both examples now also list, commented out, the other settings the stack files read, such as
  the sign-in scopes, the ntfy image and memory, and Forge's limit on games per request. Only the
  ntfy address and token file stay out, because the stack sets them itself. If you copied an
  earlier example, your file keeps working as it is.
- The images are public, and the guides now say so: no registry login, GitHub token or
  `--with-registry-auth` is needed to pull them. Step 5 of the Swarm guide is now "Images".
- The example reverse-proxy files: the nginx and Caddy examples limit request bodies to 4 MB, as
  the guides say. The Caddy example compresses only the static files, as
  [docs/REVERSE-PROXY.md](docs/REVERSE-PROXY.md) advises for the pages. The Traefik override keeps
  ntfy and Forge out of Traefik too, not only Mystic Forge.

### Fixed
- Several places in the deployment guides, the operator plugin's deploy skill, the member guides,
  the API and plugin guides, the architecture and operations guides and the third-party notices no
  longer matched the gateway and are corrected. Among them: the deploy skill now hands over the
  command that creates the `mtg_ntfy_token` secret and checks the ntfy service. The guides now say
  that `/healthz` also reports "degraded" when Forge is on and doesn't answer, that backups don't
  need the Fernet key, and which tools are marked as changing something. They also describe the
  Android app's permissions and notification steps, the phone tabs and the admin Card models page
  as they are. The third-party notices now list the gateway's Python packages and their licences.
- In the Android app, the messages about a share or a copied link that the app doesn't open, the
  one about turning the camera back on, and the app-update messages are shorter, so on most phones
  they fit in the two lines that Android 12 and later show. When a share has no link in it, the
  message now points to **Open a copied link** on the Account page's App card instead of the
  account menu, which no longer has it.

## [0.7.30] - 2026-10-18

This release also includes every change listed under 0.7.27, 0.7.28 and 0.7.29 in CHANGELOG.md. Those versions were not released on their own.

The simulator release: a Reports page under More, Forge games of two decks against the same
opponents, laid-out Forge results, a guide on the Goldfish page and quicker Forge runs. Forge stays
off by default. The database moves to schema 14, which only adds one table. Members can now get
their notifications in the gateway's own Android app, from a notification server that runs in the
stack.

### Added
- **Reports page** under More: your stored deck reports, newest first (the newest 200), with their status and what ran. It also shows what Forge has going for you now and what waits, with the expected times. A deck filter narrows the list, and each report links to its deck's trend in History. Reports stay in History too.
- **Same opponents in compares:**
  - The Compare page's **Test both in Forge against the same opponents** seats both decks at the same three precons, picked for the higher of the two decks' brackets (the bracket set on each deck, or Archidekt's estimate when none is set), so it doesn't matter which deck you compare from. Assistants and the app API do the same with `compare_with` on the first report and `same_opponents_as` on the second.
- **Saved card descriptions:** each member keeps their own (up to 200), they play only in that member's solo tests and goldfish A/B comparisons, and the member or an admin removes them.
- **Goldfish page guide:** "How to play" walks a first game step by step, and you can hide it (remembered for your account). Phones show the current step, with **Show all steps** to open all six.
- **Forge results laid out:**
  - Tiles: games played, your deck's win rate with its 95% interval, median game time and length.
  - A table of the decks at the table, with how each lost.
  - How the games ended, and each game.
  - Phones get a stacked layout.
- **Live Forge card:** while the games play, the report page asks a small status route (`GET /api/v1/reports/{rid}/forge`) instead of reloading the page.
- `options.forge_seed`: the Forge games' own seed.
- **Play more games** on a report whose Forge games have finished: plays more games against the same opponents and adds them to the results, so the win rates and their 95% intervals count every game (up to 50 games a report). On a comparison both decks get the same number, so the two stay even. The button is on the report page, and assistants and apps that use the JSON API can ask for it too, with the same rules (`play_more_games`, `POST /api/v1/reports/{rid}/forge/more`).
- `MTG_FORGE_PARALLEL_GAMES` (also on the admin Settings page, default 4): how many of a report's Forge games the gateway asks Forge to play at once. `FORGE_MAX_PARALLEL` (Forge service): the most games Forge plays at once for all runs together; left empty (the default), Forge works it out from its container's CPUs and memory, which is 1 inside the default limits. With more than one place, a Compare's second deck can start before the first deck's run has ended.
- **Forge on more than one machine** (optional): the gateway can use several copies of the Forge service, for example on a Pi and a PC. Each run plays on one copy, the one with the most free places, and stays there. One copy is still the usual setup. So far this is tested only with a stand-in, not on real machines.
- `scripts/measure_recognition.py` and a one-off CI step: for the precons and test decks, the share of cards played with their effect and the share known (played with their effect or left out on purpose).
- The Goldfish page has the card size switch too, and its cards follow it.
- **Notifications in the Android app.** Members can get their notifications in the gateway's own Android app instead of a separate notification app. There is no Google push service and no iPhone version. On the Account page in the app, **Get notifications on this phone** asks once to let the app run in the background, and on Android 13 and later it first asks to allow notifications. Android then shows a quiet "Always connected" notification while the app listens.
- **Disconnect my phones** on the Account page stops every connected phone.
- The stack runs its own ntfy server (`mtg-assistant-ntfy`; `ntfy` with Docker Compose) on a private network with no published port. Phones reach it through the gateway's own address.

### Changed
- **Names:**
  - The 300 automatic games are the **solo test**.
  - The deck page's buttons read **Run full test** and **Solo test only**.
  - The report's sections read "Solo test (N games, no opponents)" and "Forge games (against precons)".
  - "Goldfish" on the website names the page where you play a hand; the automatic games are the solo test (the paired A/B of two decks keeps its "goldfish A/B" name).
- **Bracket:**
  - A bracket set on the deck is the deck's bracket.
  - The estimate follows Archidekt's own Estimated Bracket rules and is labelled "Archidekt's estimate from cards". The deck banner, the Reports page and the chat card show it only when no bracket is set; the Deck checks row names it only when the checks find a mismatch; the deck page's statistics box shows it beside a set bracket, labelled as Archidekt's estimate with Archidekt's notes, and never flags it; a report's statistics show the set bracket, or the estimate when none is set, with the estimate's basis line under them.
- **Played without their effect:**
  - Reports call the simulator's "unrecognized" cards "Played without their effect" (not "Not recognized"), count copies, and offer "Copy a request for your assistant".
- **Forge runs:**
  - Each game of a run has its own seed, and a run's games play at once as far as Forge has room. The seed is kept for the record only: every report with Forge games and a recorded seed says Forge does not always replay the same games from the same seed, so compare runs by their win rates and 95% intervals.
  - A report plays 4 Forge games by default (`MTG_FORGE_GAMES`, was 10); **Play more games** adds more.
  - A game still going after four minutes stops and counts as a draw (`FORGE_GAME_SECONDS`, default 240, was 600). A stack variables file copied from the old example still sets `FORGE_TAG=2.0.15-mag2`, `FORGE_GAME_SECONDS=600` and `MTG_FORGE_GAMES=10`: DEPLOY.md's "Updating to 0.7.30" (now in [docs/UPGRADING.md](docs/UPGRADING.md#0730)) says what to change.
- **Forge memory:** where Docker can't cap memory (Raspberry Pi OS by default), Forge guards its own memory from inside the container, with nothing to change on the machine: `FORGE_MEMORY_LIMIT` sizes the games at once, each Java has a ceiling on its class data, and the kernel stops a Forge game first if the machine runs out of memory (`FORGE_MAX_METASPACE`, `FORGE_OOM_SCORE_ADJ`). This is not a hard limit as Docker's is.
- **Forge speed:** each Forge game's result shows on the report within a few seconds of the game ending. Forge reads a card's rules only when a game needs that card, so each game starts faster. With more than one Forge place, a Compare's second deck can start before the first deck's run has ended.
- **Forge image:** 2.0.15-mag3. Build pins are by digest (Dockerfile frontend and base, buildkit, binfmt).
- **Report wording:** "Out of scope" reads "Left out on purpose", and "Modeled but never mattered" reads "Played with their effect but never mattered". The report adds a second coverage figure, the cards known to the solo test (played with their effect or left out on purpose).

- The Notifications card no longer shows a server or topic, and members no longer choose a topic. When the gateway first starts after the update, every member who had a topic gets a new random one.
- **Check for app updates** says at once that it is checking.
- The deck sync now spends the member's Archidekt actions (120 per 10 minutes by default, `MTG_ARCHIDEKT_CALLS_PER_10_MIN`): each read of the deck list or of a deck uses one. A check stops while fewer than a quarter of them are left and tries again at the next check, so the member's own actions keep their room. The sync also checks a waiting change on someone else's deck, such as a copy of a public deck: it reads that deck at most once every 30 minutes, and leaves one that is gone or private alone for 6 hours.
- In Full auto, a change that moves a card into or out of the commander zone, removes the commander or adds a card to the commander zone waits for the member's press, as other commander changes do.
- The Android app's menu entry **Scan with phone camera** is now **Scan with the app's camera**, and the launcher shortcut **Scan cards** is now **Scan page**.
- The build images (Dockerfile, the Mystic Forge Dockerfile and the BuildKit and binfmt images in CI) are pinned by digest, and so are pip and wheel. The image installs only the wheels it built.
- Adding a card to the commander zone is now refused when the card is already in the deck but not in the commander zone, or when it is added to the maybeboard or sideboard with a first category that is not outside the deck. Before, the card was added without its commander-zone category.

### Fixed
- In Semi-auto, an assistant's change that moved a card into or out of the commander zone, removed the commander or added a card to the commander zone applied without the member's press. It now waits for the press, as other commander changes do, also when the deck's commander zone has another name. On the deck page, such a move now asks before it saves.
- A pasted or Archidekt list's copies are counted in the report's shares (they were counted once per name).
- The bracket estimate now follows archidekt.com's own rule for combos and extra turns; it followed an older, reversed rule.
- The Android app's colour strips under the status and navigation bars kept their full colour when Android faded the window.
- Restoring a snapshot left each card's color tag and custom mana value, and the order of its categories (the first one decides whether the card counts in the deck), as they were, and still reported the deck restored, or said it already matched the snapshot. A restore now puts them back and checks them.
- Adding a card to the deck under Maybeboard, Sideboard or another category the deck does not count wrote the card outside the deck, then reported that the deck did not match the change. The deck editor and Card search's Add to deck now add such a card to the maybeboard or sideboard. Any other such add, such as one from an assistant, is now refused before anything is written, and the message says to add the card to the maybeboard.
- A card added to the maybeboard or sideboard under Sideboard was filed under the deck's maybeboard category (usually Maybeboard) unless the deck marked Sideboard as outside the deck. It now goes under Sideboard.
- When the gateway's first try to reach the research service after a start failed, a deck report asked for in the next minute came back without its solo test, and some research calls from an assistant were sent in a form the research service refused. A report's solo test is now sent in the form the research service takes. For an assistant's research call whose form the gateway does not know yet, the gateway first asks the research service again; if it still does not answer, the call is refused before anything is sent, with a message to try again in a minute.
- The deck editor's Save anyway, after the editor asked first, made a backup copy on Archidekt even with its backup box unticked.
- A save from the deck page or Card search, or from the deck editor with its backup box unticked, that was left waiting (its question unanswered, or writes switched off) and applied later from its review page made a backup copy on Archidekt anyway.

### Removed
- The old `/decks/{id}/playtest` address. The deck page's Playtest opens Archidekt's own playtester directly.
- The sign-in fallback for Android apps older than 0.7.12.

### Card models (schema 14, for the simulator work)
- Database schema 14 adds one table, `card_models` (the simulator's card models). The upgrade is automatic and only adds the table; going back to an earlier version needs the backup taken before the upgrade, as for every schema step.
- Each member keeps their own card models (the saved card descriptions above), one per card, used only in their own solo tests and goldfish A/B comparisons. Each records the simulator version it was checked with.
- Admin > Card models lists every member's models, most recently changed first, with who added each (admins only) and Remove for any of them (audited as `card_model_removed`). "Delete my data" removes a member's models.

### Deployment
- **Create the `mtg_ntfy_token` secret before updating** (DEPLOY.md step 4, or `./make-secrets.sh` for Compose). Delete any `MTG_NTFY_URL` or `MTG_NTFY_TOKEN_FILE` value from the stack's environment. An ntfy server you ran for the gateway, and its proxy host, can go.
- **Members need app 0.7.30 to get notifications. Members on an app older than 0.7.12 must update the app first.**
- Members who used the ntfy app connect again in the gateway's Android app; there are no notifications on iPhone. Their old ntfy subscriptions get nothing more.

## [0.7.29] - not released on its own, included in 0.7.30

Changes that wait can be amended, combined and approved in the chat, account actions are approved
on the card, an admin Settings page manages most of the gateway's tuning settings, view choices
follow your account, and the gateway deletes its older backup copies past a set number per deck
(copies from earlier versions stay). The database moves to schema 13 in one upgrade step. The
Android app is unchanged.

### Added
- **Approve account actions on the card in the chat.** These are likes, bookmarks, follows,
  comments, folders and collaborators.
  - Each needs the member's own press, in every mode; an assistant never applies one.
  - Deleting a deck stays on its review page.
- **Full auto still waits for some changes.** Restores, new decks, commander changes, and deck or
  collection edits of more rows than Full auto applies by itself (20 by default) wait for a press.
  Smaller collection edits apply by themselves in Full auto.
- **A second step waits for its first.** An assistant never applies a
  change while a change it depends on is not applied, in any mode.
- **Another app's change needs the member's press.** A change that replaces a change another app
  made waits for a press, and the other app's change stays waiting until that press applies the
  new one; only then is it marked replaced. An assistant can't combine another app's change or a
  held one. A change that another app's waiting change would replace waits for a press too.
- **Flexible proposals.**
  - Amend a waiting change: it keeps its number, gets a new version, the gateway writes what
    changed, and its expiry starts again. Withdraw, combine and replace are also new.
  - Earlier versions are kept, up to 50; when they are full, the assistant starts a new linked
    change.
  - "There is a newer version of this change": an apply of an older version sends nothing.
  - Each change is compared again with the deck as it is now: Updated / Needs a look / Held,
    with the reason.
- **The assistant board.** Each change on a deck has a number, and `list_my_proposals` lines
  carry its status, version and who changed it.
- **The waiting changes card** (`show_waiting_changes`). It approves or rejects each change,
  and "Apply all N" applies the same app's changes, oldest first, after a confirmation
  (`confirm_waiting`, `get_apply_result`).
  - The proposal card names the other waiting changes ("1 from ChatGPT also waits", Review all).
- **Pages.** "Waiting changes" strip on the deck page; check several on the Proposals page and Apply
  them in order; Undo of a batch from its result page.
- **Keep formatting.**
  - When the description or a comment is edited, the bold text, links and pictures already there
    stay where their words did not change, and new words are plain.
  - Such a change is approved on its review page, which draws how it will look.
  - An edit that removes all of a description's or a comment's formatting is approved on its
    review page too, which says how many pictures or card links go.
- **Comments on every page of a deck's thread.** The website has "Show more comments";
  votes, replies, edits and deletes work on any page; the assistant reads and acts by page.
- **More approval and social changes.** A change another app rewrote is approved on its own, never
  in Apply all. Full auto waits for making a deck public or unlisted and for any edit of a
  description that has formatting, such as bold text, links or pictures. A follow the gateway could
  not confirm offers Follow and Unfollow. A deck down vote shows "Voted down" and can be taken
  back. The assistant can follow or unfollow someone by username, with the member's press. One
  wording per refusal. Reject on the lists asks first.
- An admin Settings page (Admin > Settings). Settings are grouped (approvals, deck sync, backups, simulation, watchlists and prices, general, Archidekt and Scryfall pacing; card scanning folded under "Advanced"). Each shows its range and where its value comes from. Each section saves on its own, and one refused field saves nothing in that section. A setting set in the stack is shown where it would be changed, greyed out with its value, and can't be changed until the stack changes. Turning backup copies off, or lowering copies or restore points per deck, needs a confirm tick. "Back to default" keeps the section's other unsaved values in the form and names them. Settings marked "After a restart" take effect when the gateway restarts. Every change is in the activity log, written as "Setting: before → after". The page warns when the name ChatGPT gets is over 80 characters.
- When your stack sets any of the Settings page's settings, a box at the top of the page lists them, each with its variable and a link to its row.
- Card size S / M / L next to "View as" on the deck page, My decks, the collection and card search, and in the printing picker. M is the size every page had before.
- "Forget my view choices" on the Account page. A device not used since can't bring them back.

### Changed
- Database schema 13, one upgrade step. It adds:
  - a store for admin settings and one for each account's remembered choices;
  - versions, statuses, board numbers and links for waiting changes;
  - the Archidekt account number on new waiting changes, snapshots and queued backup deletes;
  - a deck name, card count and owner stored with each snapshot, so lists no longer read every stored deck;
  - queued deletes of unused backup copies moved into a table of their own;
  - an index for the expiry sweep.
  An older image refuses a schema 13 database. Take a backup before upgrading.
- Each setting the admin Settings page manages has an allowed range. A value set in your stack always wins and is shown locked. On the page, Archidekt and Scryfall pacing can only be made gentler than the gateway's defaults.
- New deployment settings, each also on the Settings page: `MTG_PROPOSAL_EXPIRY_DAYS` (7) and `MTG_AUTO_BIG_EDIT_ROWS` (20).
- A restore point of a deck that had no name is now listed in History by the deck's number, and assistants get an empty name instead. Before, both showed "None".
- **Live settings.** Approval settings are read live (approval mode default and limit, auto rows,
  big-edit rows, expiry), so a change on the Settings page applies without a restart.
- **The proposal card uses the shared card template.**
- **The history page** shows the bracket set on the deck, and the estimate only when none is set.
- **A comment vote costs 2 Archidekt calls instead of 3.**
- **Social actions and comments.**
  - Editing a comment on the website asks first.
  - The card quotes the comment an action is about.
  - Errors say what happened.
  - With writes switched off, the social buttons and the comment thread are read-only.
  - Long names wrap on phones.
- **Assistant wording.** Tool descriptions and the guide describe the card or review-page
  approval, Full auto's exceptions, Apply all and Undo.
- View choices follow your account to every device: "View as", card size, stack orders, the stats panel's choices and the dismissed EDHPlay steps and scan intro. A device's earlier choices are copied to the account once. Light or dark mode, the last deck in "Add to deck", the last set per deck in the printing picker and a scan in progress stay on each device.
- Your account keeps your newest 150 stack orders; one more forgets the oldest.
- Backup copies on Archidekt: the newest 10 per deck are kept (the Settings page can change this) and older ones are deleted from the backup folder, one a minute, only while writes are on, and only a copy still in the backup folder as the gateway made it. Copies made before 0.7.29 are never deleted and don't count toward that number; remove them by hand on Archidekt if you want them gone. A copy you changed or moved out of the backup folder on Archidekt is never deleted. A copy the gateway can't delete (for example after relinking another Archidekt account) is left alone and no longer counted or retried.
- Edits within 15 minutes of a deck's last backup copy, or with the deck unchanged since it, share that copy instead of making another. Each edit still keeps its own restore point.
- A restore point past the newest 25 is kept while it records a backup copy the gateway may still delete.
- The record of how each backup copy was made is kept in the database, so a restart no longer loses it. "Delete my data" removes these records at once.
- The watchlist limits, backups on or off, the editor's backup box and restore points per deck take effect at once when changed on the Settings page. The watchlist limit message says how many to remove ("You watch 12 decks; this gateway lets each member watch at most 10. Remove 3 to add another.").
- The admin page's ChatGPT name warning points to the Settings page, or to `MTG_CHATGPT_NAME` when your stack sets it.
- The hourly clean-up deletes in short batches (at most 500 rows per step), so it never holds the database for long.
- The admin Users and Activity pages read every member's apps in a fixed number of queries.

### Fixed
- **Comments on a long thread.** Changing or deleting a comment you had just
  posted failed on a thread of 3 or more pages.
- **History page.** It showed "bracket about N" although a bracket was set.
- **Deck list read.** A page that shared the deck sync's failed read of the deck list showed the
  error without trying again.
- **Comment reply.** A reply's failed thread read was reported as "not in this deck's thread".
- **Comment count.** It counted replies.
- **A new comment.** It could show vote buttons instead of Edit and Delete.
- **Refusal text.** A refusal with no stored reason had a double space.
- **Chat cards at a larger text size.** Card text now follows your text size setting.
- **The deck page at 200% text on a narrow phone.** It no longer scrolls sideways, with or without
  scripts: the mana curve, the deck checks, the View as, Group by and Sort by boxes and the Description
  and Comments headings fit their panels. On a phone, Card search keeps its name for screen readers,
  and "No mana data" is no longer dimmed below 4.5:1.
- The deck page's Stacks view scrolled sideways by a few pixels on a 360 px phone (a stack's Options button sat outside the window).
- A setting set to only spaces in the stack is treated as unset (before, a number setting stopped the gateway from starting).
- With large text and a mouse, the top bar's links wrap onto more rows instead of running past the edge of the window, and the bar doesn't cover the link or button you move to with the Tab key.

### Deployment
- The stack and Compose files pass the 24 settings the Settings page manages that they used to set (each with its default) as empty unless your environment sets them, so the page can change them. The example env files list every setting the stack passes, commented out with the default. A value you set in your environment still wins and is shown locked.
- **Upgrade note for existing deployments (DEPLOY.md and DEPLOY-COMPOSE.md "Updating to 0.7.29 from an earlier version", now in [docs/UPGRADING.md](docs/UPGRADING.md#0729-included-in-0730)):** a stack or Compose file from before 0.7.29 gives 24 of the page's 47 settings a value of its own, so they show locked. Before replacing the file, note your own edits to it (the uncommented Authentik and ntfy secret lines, lines added under `environment:`) and redo them in the new one. Portainer: paste the new stack file if it was pasted into the Web editor before (the Repository method brings it). Without Portainer: `git checkout deploy/portainer-stack.yml`, then `git pull`. Compose: download the new docker-compose.yml (in a clone, `git checkout docker-compose.yml` then `git pull`). Optionally delete the variables you want to manage on the page (an older Portainer variables file sets 19, an older .env 17). Any variable kept stays locked. Then update with re-pull (if `MTG_TAG` names a version, change it to `0.7.30` first; without Portainer: from a new shell in your copy of the repository, load `mtg.env` and run `docker stack deploy` again).
- New variables: `MTG_ARCHIDEKT_BACKUP_COPIES` (10), `MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT` (false), `MTG_SNAPSHOTS_PER_DECK` (25). DEPLOY.md's environment table describes them; OPERATIONS.md "The Settings page" describes the page.

## [0.7.28] - not released on its own, included in 0.7.30

Card search with every Scryfall filter, My decks by folder, a printing picker on the deck page and
in the editor, a clearer collection import, and leftover fixes. The database stays at schema 12,
so going back to 0.7.27 is a plain image swap. The Android app is unchanged.

### Added
- Card search (`/cards`) has every field of Scryfall's advanced search, paper cards by default:
  the main filters on top, the rest under More filters, a full-screen panel on phones. Results
  update as you change a filter, the address keeps the search, grid or list view, Scryfall's sorts,
  and the next cards load as you scroll.
- My decks shows your Archidekt folders: open a folder into its own view, go back with the
  breadcrumb, and filter by name across every folder (each match says which folder it is in).
  The gateway's backup folder never shows, here or on the Folders page. The Folders button is now
  Manage folders.
- Change printing… in the deck page's card menu (right-click, or press and hold on a phone) opens
  a printing picker: search by set name or code, filter by finish, frame and border, sort newest,
  oldest or cheapest, and see the whole card before you pick. Digital-only and preview printings
  are hidden until you ask for them, and only English printings are listed. The change saves at
  once with Undo; when the new printing isn't made in the card's finish, the picker says so first
  and the card takes a finish that printing has.
- The deck editor uses the same picker: Change printing on a card's row, and All printings… next
  to the small printings when adding a card.
- The Collection page explains how to import its Export CSV on archidekt.com: the column type to
  pick for each column, and what each condition and language code means.
- After a collection import the page says how many cards went in and names each line it could not
  take as given: a language Archidekt's collection doesn't store (kept as English), a condition
  Archidekt's collection doesn't store, such as EX (kept as Near Mint), a copy added to a card you
  already have in another language, and cards not found. A refused import says why and which line.
- The collection import reads a Language column, in the gateway's codes, Archidekt's codes or
  the language's English name. Assistants can give a language when they add to the collection.
  A condition may be written out (Near Mint, Lightly Played, Moderately Played, Heavily Played,
  Damaged) as well as given as its code.
- `MTG_CHATGPT_NAME` (optional) gives the ChatGPT connector its own name: the name in the ChatGPT
  plugin file and the name the ChatGPT install steps tell people to type. Empty keeps
  `MTG_SERVER_NAME`. Per OpenAI's plugin docs, ChatGPT refuses a plugin file whose name is over
  80 characters, so the gateway warns about a longer name in the log, on the admin page and on
  the install page's plugin-file step, and still starts. A `MTG_SERVER_NAME` over 120 characters
  is shortened in the plugin file, where OpenAI's plugin check allows at most 120 characters for
  the developer and author names, and the log and the admin page say so.

### Changed
- The collection CSV export writes condition and language in Archidekt's own codes (D for
  Damaged; JP, KR, SP, CS, CT for Japanese, Korean, Spanish and Chinese), so archidekt.com reads
  them. The gateway's own import reads both.
- Set as commander keeps the card's other categories, with Commander first, as archidekt.com's
  "Set Commander as primary" does. A commander that is replaced and had no other category gets
  its default category, as archidekt.com does.
- ChatGPT: the plugin's short description is "MTG decks and research", the server instructions
  put the safety rules first, and the install page says that ChatGPT shows the name and
  description you type and may not show the gateway's icon for a connector added by hand.
- The ChatGPT install step no longer calls 10 KB ChatGPT's limit for the icon.
- Prices over a thousand read with a separator ($3,500.00) in the printing picker, the card
  viewer and the editor. The card viewer, the deck page and the editor write a printing as
  SET #number.
- The admin page's clean-up row says each state in plain words.
- CI also checks that the code compiles and imports on Python 3.11, the oldest version the
  package allows, and a release waits for that check.

### Fixed
- Changing a companion or a double-faced card set to show its back (its printing, category,
  quantity, label or mana value) no longer clears that setting on Archidekt, and the deck page's
  Undo of its removal puts the setting back.
- Card-name suggestions answer faster while typing; a card Scryfall doesn't know is remembered.
- The assistant's printing list respects its limit for the set list too.
- Undo of a move on the deck page files a card back under every category it had.
- In the stacks view a card can be dragged onto the stack directly below it.
- The deck editor's save bar sits under the top bar instead of over it.
- Home, Goldfish and Compare load only the scripts they use, and the deck page's card filter is
  quicker on big decks.
- Every guarded form needs its render stamp, so a form can't be sent before it was shown.
- A collection import with a cell longer than 131,072 characters, or a quantity thousands of
  digits long, is refused with its line number instead of failing with an error page. A CSV whose
  name column is called Card is read as a CSV.
- A card search too long to send is refused with "This search is too long. Remove some filters or
  words." instead of being cut, which could drop its last filters (paper only among them).
- After Set as commander, the check that Archidekt saved the change also looks at the card's first
  category, so a commander Archidekt didn't put first is reported.
- A folder can't be renamed or made with the backup folder's name through the gateway. Such a
  folder was taken for the backup folder and hidden from My decks.
- A long server name shortens in the top bar instead of pushing the search box and the account
  button out of a narrow window.

## [0.7.27] - not released on its own, included in 0.7.30

Bug fixes and small fixes from the owner's checks of 0.7.26, a check of every Archidekt write
call against archidekt.com's own site code, and the fixes from an independent review. The database
stays at schema 12, so going back to 0.7.26 is a plain image swap. The Android app changes too
(it reloads by itself when the phone is back online), so it is rebuilt with this release.

### Fixed
- A long apply (a new 100-card deck, a big edit) no longer fails halfway when Archidekt asks the
  gateway to slow down: it waits out the pause Archidekt asks for and carries on by itself, up to
  6 waits and 5 minutes of waiting for the whole apply. If Archidekt asks for a longer wait than is
  left, the apply stops at once with a plain message saying what had changed; the assistant and
  the review page give the same account, and a deck or tag already written is never reported as
  nothing changed. The review page says when it is waiting. An apply's waits no longer pause the gateway for everyone. Outside an apply
  nothing changed: a 429 still pauses every call and is not retried. A wait Archidekt gives as a
  date is now read as that date instead of a fixed 30 seconds.
- When Archidekt refuses a request (HTTP 403), the message now says so, with Archidekt's own
  reason, instead of "relink your account" or "this deck belongs to another Archidekt account".
  Likes, bookmarks, follows and Collection edits no longer report a refusal as a sign-in problem.
- Deck tags come from Archidekt's own tag list: archidekt.com never creates a new tag, so the
  gateway no longer tries to. A name Archidekt doesn't have is refused when the proposal is made,
  with close matches.
- Write requests now match what archidekt.com itself sends (checked against its site code, not yet
  tried live): liking a deck uses the deck's own vote, taking back a down vote is sent as a down
  vote, the cover uses Archidekt's own image address, a new tag goes after the deck's other tags,
  and moving a deck to a folder sends the same request as Archidekt's folder page.
- Deck descriptions and comments written in the gateway are sent in Archidekt's rich-text format,
  so they show on archidekt.com. Text from an assistant or a gateway form is always sent as plain
  text, so a hidden link or formatting can never go out unseen; restoring a snapshot keeps
  Archidekt's formatting. Descriptions and comments written on Archidekt show as readable
  text in the gateway instead of raw code, and saving the settings without touching the
  description no longer replaces Archidekt's formatting.
- When an assistant sends a description or comment in Archidekt's rich-text format and its links,
  formatting or embeds are removed, the gateway now tells the assistant so in its reply, so it
  doesn't tell you a link was added. This includes applying a proposal made before this release.
- Collection condition and language are saved as Archidekt expects and shown as codes (for
  example "MP" instead of "3"). Phyrexian, which Archidekt does not have, is no longer offered. A
  new record is English and Near Mint when nothing is picked, as on archidekt.com, and editing a
  record never fails on a stored value the gateway doesn't know.
- When an apply fails before it changed the deck, the backup copy it had just made on Archidekt is
  deleted again. When part of the change landed (a tag, cover or folder change included), the copy
  is kept as the way back. Only that apply's own copy is ever deleted. The gateway's own snapshot
  is always kept.
- History's deck filter lists your decks by their current Archidekt names. Decks that are no
  longer on Archidekt are listed under "No longer on Archidekt", below the others. Nothing is put
  there when Archidekt's deck list can't be fully trusted (owner details missing, a list cut short,
  changing while it was read, a deck seen twice or fewer decks than Archidekt counts), and a report
  on someone else's deck, or a deck of an Archidekt account you were linked to before, is never put
  there.
- A deck whose owner Archidekt doesn't name is no longer said to belong to "None". Ownership is
  decided by Archidekt account id whenever both ids are known.
- My decks says when your deck list couldn't be read from Archidekt, with Try again, instead of
  showing an empty list.
- The deck page's card menu no longer freezes while Archidekt saves. Mana value, Quantity, Move to
  and Remove from deck show the change at once and save in the background; quick Quantity presses
  are sent as one save. If a save fails, the card goes back to what Archidekt has and a note says
  why, also when several edits to the same card fail, and says when a later move or mana value for
  that card was not sent.
- Grouped by Mana value, a card whose mana value you change moves to its new stack at once, and the
  Table view's MV column shows the new value. The card menu's Move to list no longer shows all the
  time, and undoing the removal of a card with no category no longer brings it back under a blank
  heading.
- Play on EDHPlay no longer makes the first card of a deck with no commander set into the commander
  on EDHPlay. Lands come first among the candidates, a list with only a sideboard no longer starts
  with a blank line, and a companion filed in a main-deck category goes to the sideboard.
- The "You're offline" page loads the page again by itself once the connection is back. When the
  browser knows it is offline the page shows at once; otherwise a page load keeps trying for about
  5 seconds first, so a short drop or a gateway restart no longer shows it at all. A page that
  keeps failing while the gateway answers is reloaded by itself at most 3 times, then waits for
  Try again.
- A tag named twice in one proposal is added once, a deck already liked isn't liked again, and a
  new tag after tags starting with Z still sorts last.
- Picking a card in the deck editor before its details had loaded no longer leaves the new row
  without its picture, mana cost and type.
- A card picture that fails to load keeps the deck editor's rows in shape (and is not read out
  again where the row already names the card), and a card suggestion no longer shows a gap inside
  the typed part of its name.
- Restoring a snapshot of a deck in another format than Commander is no longer refused because of
  a bracket stored with it.
- Try again on My decks reads the deck list from Archidekt again instead of showing the same
  partly read list.
- After an Approve whose apply failed, the chat card shows Failed with the reason once and no
  Approve or Reject buttons, and the review page shows the failure once instead of twice.
- A Collection change that stopped partway now says how many of its changes reached Archidekt,
  instead of saying nothing was changed.
- The Home page's My decks panel and the assistant's deck list say when the deck list couldn't be
  read from Archidekt, as My decks does.
- The deck card in ChatGPT no longer says "No connection to the gateway": the deck now comes with
  the answer, so the card can show it without reaching the gateway (not yet tried in ChatGPT).
- Proposal cards and review pages are easier to read: the footer names the app that proposed,
  tag changes say which tags are added and removed, a cover change names the cover it replaces,
  rows without a picture leave no gap, rows are shorter on phones (a long change such as a move to
  another board wraps beside its label instead of over it), the locked Apply and Reject
  buttons are readable in dark mode, and a chat app that cannot show the card gets a plain summary
  instead of raw data.
- Deck settings forms (cover, tags, folder, collaborators) show their result next to the form you
  used, and on phones each box keeps its button on the same row.
- The Guide's search lists the matching sections right under the box, with a short snippet and a
  link to each. Enter goes to the first match, and on phones the contents drawer folds away.
- Easier to use by touch and keyboard: the EDHPlay box's close button and "How to import", and the
  collaborator list's rows, are finger-sized; the collaborator list has a name for screen readers
  and its box shows its whole hint on a Fold's cover screen; the Guide's search results can be
  walked with the arrow keys; the chat cards' locked buttons and the deck card's mana curve
  numbers are readable in light and dark.

### Changed
- When Archidekt's waits stop an apply before anything changed on Archidekt, the proposal stays
  pending instead of failing, so you can approve it again in a few minutes without asking for a new
  proposal. It is checked against the deck again when applied, with a new snapshot and backup.
  Nothing applies it again by itself. The chat card keeps Pending with working buttons, the review
  page shows the one message with its Apply button, and History marks the snapshot of the stopped
  try as taken before an apply that changed nothing. If any part of the change had reached
  Archidekt, the proposal still fails as before. That includes a copy of a deck already made (the
  message gives the copy's number, so it is never copied twice) and a collaborator, comment or
  folder change already sent.
- When the backup copy an apply made can't be deleted right away because Archidekt asked the
  gateway to wait (or answered with a server or network error), the gateway deletes it later by
  itself: one copy at a time, never while Archidekt's pause runs, first once the pause is over,
  then waiting 10, 20 and 40 minutes between tries, and giving up after 4 failed tries (the copy
  then stays in the backup folder, as before). The messages say so until it is done. Waiting
  deletes are kept in the database, so a restart doesn't lose them. Before it deletes a copy, the
  gateway reads it once and keeps it if it was moved out of the backup folder, renamed, given
  another description or changed since it was made, or if it isn't owned by the Archidekt account
  you're linked to. Linking an Archidekt account again cancels your waiting
  deletes, and those copies stay.
- Only a Commander deck has a bracket, as on archidekt.com: moving a deck to another format clears
  its bracket, and a bracket for another format is refused.
- A hand-set mana value is a whole number from 0 to 15, the range archidekt.com offers.
- Every card change sends the card's colour tag, as archidekt.com does.
- The Android app reloads a page that failed to load as soon as the phone's connection comes back.
  For this it now asks for the network state (`ACCESS_NETWORK_STATE`), granted at install without
  a prompt.
- Home is always in the navigation: left of Decks in the top bar and the phone's tab bar, and first
  in the side rail on an unfolded foldable, a tablet and the Android app from 600 px wide. The rail
  lists every page and has no More menu. On phones, Proposals moves into More.
- Play on EDHPlay opens a short box on every press. Its "Copy and Open EDHPlay.com" copies the deck
  and opens EDHPlay's My Decks. The import steps (My Decks, Add Deck, open the deck, Import, paste,
  Import & Replace) show the first time on a device and stay folded under "How to import" after
  that. The box says when to choose the commander or set the companion on EDHPlay, and gets the
  deck list when it opens instead of with every deck page. A failed load shows only "The deck list
  couldn't be loaded." with Try again; when you're signed out it says to reload the page, without
  Try again. The first-use tip is gone.
- The deck editor's "Also keep a backup copy on Archidekt" box is unticked by default.
- With `MTG_WRITES_ENABLED` false, likes, bookmarks, follows, comments, collaborators and
  Collection edits made by hand are refused too, and nothing is sent to Archidekt.
- Adding a collaborator opens a list of the people you follow on Archidekt, which narrows as you
  type. Someone you don't follow gets one message under the box with a button to open their
  Archidekt profile.
- The "Add a tag" box suggests Archidekt's own tags as you type.
- The setup page (`/install`) and the Guide give plain instructions without research notes or test
  dates, and say where a detail is untested or may vary by plan.

## [0.7.26] - 2026-10-12

Clean-up release before the next deployment: old, unused and half-removed code is gone, the two
largest functions are split into modules, and the image is smaller. A few small visible fixes ride
along. No new images besides the gateway's; the database stays at schema 12.

### Changed
- With `MTG_WRITES_ENABLED` false, setting a deck's cover, adding or removing its tags, moving it
  to a folder and renaming a folder by hand are refused, so every change on the deck settings page
  and the Folders page obeys the switch (deck delete and new folder already did); nothing is sent.
  Likes, bookmarks, follows, comments and collection edits made by hand are unchanged.
- The in-app Guide covers Play on EDHPlay, out-of-date proposals, the Goldfish, Folders and
  Watchlist pages, and names every assistant tool.
- The gateway's links to the assistant skill and the Android app open their `/install` sections
  directly; `/skill` and `/app` still redirect there.
- Copy on the Export and report pages copies inside the press first, so it also works where the
  Android app's WebView refuses the async clipboard (tested in Chromium, not in the real app).
- The gateway image is about 75 MB smaller: dependency wheels are installed from a build mount
  instead of staying in a layer, and a code change no longer rebuilds them or downloads the scan
  assets again. The scan page's scripts and styles ship pre-compressed.
- The Forge refresher finds unfinished runs through an index instead of reading every stored
  report (created on start-up; no schema change).
- The deck pages, the History page and the MCP tools are split out of two very large functions
  into their own modules, moved unchanged; the routes and the tool list are the same.
- Shared page helpers (HTML escaping, form-token checks, the no-store header) have one copy.
- The public docs are brought in line with 0.7.25: the API list, Playtest, watchlists, EDHPlay
  export and the `MTG_FORGE_URL` / `MTG_FORGE_GAMES` settings.

### Removed
- Dead code, unused parameters and unused styles found by the 0.7.26 audit, and the unused
  `/static/gateway-icon-32.png`.

## [0.7.25] - 2026-10-11

Deck sync and online play. Archidekt is the master copy of every deck: the gateway now checks it on
its own, read-only, and marks a waiting proposal out of date as soon as its deck changes elsewhere.
EDHPlay is the online table, one press away. No new images; the database stays at schema 12.

### Added
- Automatic deck sync (`MTG_DECK_SYNC_MINUTES`, 30 by default; `0` turns it off). Opening the app
  (any signed-in page, at most once in 5 minutes) checks the member's decks on Archidekt, and every
  30 minutes the gateway checks members who have proposals waiting. A check reads the member's deck
  list (one request per 50 decks); a deck is read only when it has a waiting proposal and Archidekt
  says it changed (at most five per check). The sync never writes to Archidekt, waits until no
  member's Archidekt work is running, leaves a deck alone while it is being applied or restored,
  uses none of the members' own limits, never retries, sends nothing while Archidekt is failing or
  asking us to slow down, and never unlinks an account. Only the member's own decks are checked.
- A waiting proposal whose deck changed after it was made (on archidekt.com, in another app, or by
  another change applied here) is marked **out of date** at once: on its review page, in the
  Proposals list, and as `out_of_date` / `out_of_date_since` in `get_proposal` and
  `list_my_proposals`, whose `next_step` says Apply would be refused. Apply itself is unchanged: it
  re-reads the deck and refuses a changed one, marked or not.
- "Play on EDHPlay" on the deck page copies the deck in EDHPlay's import format and opens
  edhplay.com in a new tab, or the phone's browser in the app: the commander (or partners) first,
  marked the way EDHPlay's import recognises, every card with its printing, then a Sideboard
  section with the deck's companion and sideboard. EDHPlay's sideboard holds 15 cards and refuses
  the whole import past that, so the list stops at 15 and a one-line note names every card left out. The
  maybeboard and other cards kept outside the deck are not sent (EDHPlay has no maybeboard), and
  neither are tokens (EDHPlay's import drops them). The first press on a device shows a short tip
  on these limits, kept until it is closed. Checked with live imports on an EDHPlay test account: commander, partners,
  printings and double-faced cards come through, and the marked commander wins over one already
  picked under Create Deck.

### Changed
- `MTG_WRITES_ENABLED=false` now also blocks deleting a deck and creating a folder by hand (the deck
  settings page and the Folders page), as it already blocked every proposal.

## [0.7.24] - 2026-10-11

Consolidation: one owner per job instead of several copies, the bugs a review of 0.7.14 to 0.7.23
found, and the simulation fixes. No new images: the research service stays `1.3.2-mag4` and Forge
`2.0.15-mag2` (from 0.7.23). The database stays at schema 12. **Watchlists and prices are now off
unless you set `MTG_WATCHLISTS_ENABLED=true`.**

### Added
- Reports can leave Forge out (`forge: false` on `run_deck_report` and `POST /api/v1/reports`, and
  "Quick simulation (no Forge)" under More on the deck page when Forge is set up) for a quick
  goldfish-only run. A report without Forge games is labelled "Preflight only (no rules engine)".
- Every report has an overall status (queued, running, complete, partial, failed) in
  `get_deck_report`, `list_deck_reports` and the report page, which keeps it current while Forge runs.
- Undo and Redo on the deck page for every change saved there (card menu, card viewer, Select cards
  and dragging), with the editor's keys: Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y (Cmd on a Mac).
- One setup page at `/install` for Claude, Claude Code, ChatGPT, Codex, the assistant skill and the
  Android app; its page and its Markdown (`/install.md`) come from one step list. `/skill` and `/app`
  lead to their sections; every download keeps its address.
- `/app/latest.json` (same sign-in as the APK): the Android app now checks its own gateway for
  updates instead of GitHub.

### Changed
- Watchlists and prices are an operator setting, `MTG_WATCHLISTS_ENABLED`, off by default. While off,
  the Watchlist page, its links, the card viewer's price buttons, the five watch tools and the daily
  price check are absent; stored watches are kept.
- Summaries are part of the Notifications card on the Account page and appear only when ntfy is set
  up. `get_summary` stays, and leaves prices out while watchlists are off.
- The deck page has one save model: dragging a card to another category saves to Archidekt as soon
  as you let go, like the card menu and Select cards. The "Save moves" bar is gone.
- On a deck's settings page, adding or removing a collaborator is one press and a confirmation, like
  creating a folder (which now asks once too); deleting a deck keeps its typed-name check.
  Assistants' account-action proposals still wait for your press on the review page, in every mode.
- The approval-mode wording on the Account page, the guide and the docs comes from one place and
  agrees everywhere: color tags, custom mana value and collection edits are low risk, and
  collaborators are among the account actions that always wait for you.
- Report trends record each report's run settings and never mix 40-life and 20-life numbers;
  History shows where the settings changed.
- When the Forge engine is busy, a report's games wait in the queue and start by themselves instead
  of failing. A busy research call says when to retry (`retry_after`).
- The ChatGPT plugin file is labelled "Business/Enterprise admins only"; Plus and Pro accounts use
  the connector. The web account menu no longer has "Open a copied link" and "Check for app
  updates"; they stay in the app's own menu and on the Account page's App card.
- Deck values say where their prices come from: Archidekt on the deck page, Scryfall on the
  watchlist. Run simulation's tooltip says "300 simulated games"; the stack order is labelled
  "this device only".
- One legality check, one row builder for the Table, Text and Brewer views, one type and category
  helper (Type grouping now puts a card with none of the eight main types, such as a Scheme or a
  Plane, under "Other", as the automatic category already did), and one archive builder for the plugin and skill ZIPs (byte-for-byte the same files).
- The REST apply route no longer has its own account-action check; the shared apply refuses it with
  the same 403 `browser_required`, now with `review_url` and `risk`.

### Fixed
- A connection made read-only could create like, comment, folder, collaborator and deck-delete
  proposals (it could never apply them); these tools now need full access like every proposal.
- A report for a 60-card deck no longer starts or queues a Forge run that was bound to fail.
- `compare_decks` no longer suggests that two Forge runs with the same seed compare decks like for
  like: each deck meets precons of its own bracket, and the goldfish A/B is the like-for-like test.
- `run_deck_report` accepts one of your own snapshot ids, as it said it did.
- Legality everywhere (`deck_stats`, the report's stats strip, the deck page's edit answers, pasted
  lists) matches the deck page's Archidekt-style check: one restricted card is not illegal, and the
  sideboard counts where the format counts it.
- A card's custom mana value counts in the curve, average, Mana value grouping and sorting, the
  Table view and Probability of draw.
- Undo after removing a card puts it back under every category it had; removing a card with notes
  warns first that the note cannot come back.
- Reload reads someone else's public deck (or any deck with no account linked) from Archidekt again
  instead of a copy up to a minute old.
- A direct `validate_decklist` reads double-faced cards by their front face. Reports no longer say a
  card "underperforms" from goldfish games, and show how much of a Commander deck was modeled.
- Refusing an assistant's deck-deletion apply reports it as destructive, not consent; dead
  deck-delete branches were removed from the in-chat card. The guide names the Goldfish page for
  playing by hand.

## [0.7.23] - 2026-10-11

Everything left over, skipped or parked after 0.7.22 that can be built, plus faster pages.
**When you update, also update the research service image to `1.3.2-mag4` and the Forge image to
`2.0.15-mag2`** (the deploy files already name them). Still not possible, with the reason in
[docs/CAPABILITIES.md](docs/CAPABILITIES.md): writing per-card notes (Archidekt's own editor has no
way to save them), Moxfield import by link (parked), Forge's Unfinity stickers, Attractions and
Alchemy cards (Forge itself), and AI-written summaries by the gateway itself (it would need a paid
model; your assistant can write them from `get_summary`).

### Added
- Watchlists: watch cards (by name, optionally a printing and finish) and whole decks. The gateway
  keeps daily USD/EUR prices on a new Watchlist page and in the card viewer ("Price history",
  "Watch this card"), deck value over time, and optional alerts on a % move or a price line. MCP
  tools `watch_card`, `watch_deck`, `unwatch`, `list_watchlist`, `card_price_history`.
- Optional ntfy notifications (`MTG_NTFY_URL`, `MTG_NTFY_TOKEN_FILE`, both off by default):
  members opt in on the Account page with an unguessable topic (a chosen one starts with `mtg-`)
  and can press Send test (once a minute). `MTG_NTFY_URL` may not hold a user name or password; the
  token goes in its secret. Events: a
  proposal waiting for approval, a finished deck report (also queued Forge runs), price alerts,
  summaries. DEPLOY.md covers a self-hosted ntfy with access control.
- Plain daily or weekly summaries (written by the gateway from its own data, not AI), opt-in on the
  Account page, sent by ntfy when set up and readable with the new `get_summary` tool, so a Claude
  or ChatGPT scheduled task can turn it into an AI-written summary.
- Settings `MTG_WATCH_MAX_CARDS` (200), `MTG_WATCH_MAX_DECKS` (10), `MTG_PRICE_HISTORY_DAYS`
  (365), `MTG_PRICE_HOUR_UTC` (6).
- Goldfish runs for 60-card decks: a deck without a commander and with 60 or more cards plays a
  60-card game (7-card London mulligan with no free one, 20 life, on the play, sideboard left
  out); `compare_decks` can compare two 60-card decks.
- A Goldfish page from the deck page: play the deck by hand in the browser (mulligans, draw,
  battlefield, graveyard or exile, tap, next turn, new game). A Commander deck starts with its
  commander in the command zone. Nothing is sent or kept.
- Forge seats precons of your deck's bracket (the bracket set on Archidekt, else the gateway's
  estimate), then the nearest bracket, then the newest precons; the report names the opponents and
  why. Needs the Forge image `2.0.15-mag2`.
- A ChatGPT plugin file for each gateway at `/plugin/<name>-chatgpt.zip`: the connector and the MTG
  skill in one file, with your gateway's name, address and icon, in the format OpenAI's own plugin
  loader reads. The guided setup's ChatGPT section links it. Per OpenAI's help pages only workspace
  owners and admins on ChatGPT Business and Enterprise can upload a plugin; individual Plus and Pro
  accounts have no upload and keep adding the connector.
- REVERSE-PROXY.md: "Page speed through NPM", with checks for HTTP/2 and compression (pages stay
  uncompressed on purpose, because they carry a form token).

### Changed
- Pages move faster, above all in the Android app: the site's styles and scripts are downloaded
  once per release and kept, compressed, instead of with every page (pages are 70-85% smaller);
  switching a deck's views, or coming back to it within 20 seconds, no longer waits for Archidekt
  (Reload always shows the latest); the offline helper starts loading a page at once instead of
  waking up first; tapping a link shows a thin bar at the top straight away.
- Database schema 12 (watchlist, price, notification and job tables); the upgrade is automatic.
- The daily price job asks Scryfall only about watched cards, in batches of 75 on the shared
  pacing, and sends nothing when nobody watches anything.
- A Commander-format deck without a commander, a list of 99 to 101 cards with no set format (or
  Custom) and no commander marked, or a deck under 60 cards, is refused by the simulation with the
  reason. Trend labels say "Lethal damage".
- Dragging a stack or card near the top or bottom of the deck page scrolls at the same speed on any
  screen (about 1,200 px a second at the very edge, slower further in). Before, a 120 Hz phone
  scrolled about twice as fast and a busy phone about half as fast.
- US spelling in what people see: "Color tag…", "Colors", "recognized", "gray", and in the
  assistant's tool descriptions. Field names in tool results are unchanged.
- Full auto approval mode is described as applying deck and collection changes without asking:
  account actions and deck deletion always wait for your press on the review page, in every
  mode. The approval-mode docs match the Account page's own help.
- The docs and setup page say ChatGPT on the web (Pro) was checked against the gateway with every
  tool, and that the companion and admin pages were checked on a real deployment.
- Card notes stay read-only: a fresh check of Archidekt's site found no way to save a deck card's
  notes (CAPABILITIES.md says what was checked).
- Research service image `1.3.2-mag4` (patches 0007 and 0008); Forge image `2.0.15-mag2`.

### Fixed
- A 60-card list was simulated as a 59-card Commander deck with its first card as the commander,
  or refused.
- Deck reports and comparisons say how many copies of each unrecognized card were left out of the
  simulation ("skipped: 2 Bera, 1 Hamer").
- `scryfall_named` without a set shows the newest printing already out on paper, like the
  gateway's other lookups, and says which printing it is.
- An odds question the research service can't answer (for example more draws than cards in the
  deck) comes back as an error with the reason, not as an answer.
- A backup folder whose name differs only in capitals ("MTG Gateway Backups") is reused instead of
  a second one being made beside it.
- Undo after removing a card on the deck page brings back its printing, finish, color tag and
  custom mana value, not just the card.
- With large text (a phone's font size, or 200% text zoom), the main pages no longer scroll
  sideways or push buttons and menus off the screen, on phones and a foldable's inner screen.
- The Android app follows the phone's font size setting.
- The test suite runs fully offline; a test that tries to reach the internet fails.

### Security
- The ntfy token is read only from a Docker secret file and is never logged. Notifications carry
  minimal detail and a link, never deck contents.
- Page policies also allow stylesheets from the gateway itself (`style-src 'self'`); nothing else
  is loosened.

## [0.7.22] - 2026-10-11

Fixes from the owner's 0.7.21 checklist, an adversarial QA run from ChatGPT and the live test of
0.7.21. **Update the research service image to `1.3.2-mag3` when you update the gateway** (the
deploy files already name it).

### Added
- A deck report made while your previous Forge run is still playing now waits in a queue (up to
  two) and starts by itself when the run ahead finishes, also after a gateway restart. The report
  page and `get_deck_report` show its place, the report it waits for and the expected time (from
  the measured game time on your server).
- `get_deck_report` takes `view: "forge"` for a small progress answer, and every report carries
  `forge_status`.
- `get_deck` card rows without a category say which category archidekt.com shows them under
  (`shown_under`).
- Guided setup for ChatGPT: Copy buttons for the connector name (your `MTG_SERVER_NAME`),
  description and URL, and a download button for a connector icon small enough for ChatGPT (under
  10 KB) at `/static/gateway-icon-connector.png`, which needs no sign-in. The connector's MCP icon
  list starts with this small PNG.
- The skill page's ChatGPT instructions have a Copy button.
- Android app: inside the app, the Account page has an App card with the installed app version,
  Check for app updates and Open a copied link. It replaces "Get the Android app" there.
- Android app: the app looks for an update each time it starts and right after you sign in (at
  most once an hour), as well as when you come back to it after 12 hours.
- Android app: Open a copied link opens a copied link to your gateway or an archidekt.com deck
  link.
- TROUBLESHOOTING: what "bind source path does not exist" means on Swarm, and the fix for an NPM
  502 when the proxy isn't on the gateway's network.

### Changed
- Deck legality follows archidekt.com's own checks for every format, and the deck page's Legality
  chip and panel list every failing rule with its cards: deck size per format, sideboard rows not
  counted in the deck, copies counted by name across printings and the sideboard, legendary
  commander and matching partner kinds, Pauper EDH commanders, Duel Commander's
  banned-as-commander list, Canadian Highlander points, companion conditions, and the bracket set
  on the deck.
- Mana sources count only the commander's colours (plus colourless) in Commander formats.
- Every Forge run records the seed it used.
- `archidekt_bracket` is described as the bracket set on the deck, and the estimate as a floor
  counting only game changers, mass land denial, extra turns, tutors and two-card combos.
- When no set is given, card lookups (`resolve_cards`, `save_scan_session`, the price list,
  `format_archidekt` with set codes) use the newest printing that is already released and sold on
  paper, not Scryfall's newest printing, which can be an unreleased preview. Skipped newer
  printings are listed in `newer_printings`.
- `card_printings` lists released paper printings first and marks previews (`unreleased`) and
  printings not sold on paper (`paper: false`); adds `matching_total`, `returned_count` and
  `sets_scope`.
- Commander card searches (`scryfall_search` with a colour identity, `commander:`,
  `f:commander` or `is:commander`) leave out cards that exist only on Arena or MTGO. Add a
  `game:` term to choose otherwise.
- Semi-automatic approval mode says exactly what it applies without asking: small deck or
  collection edits (up to the row limit, at most 4 copies a row, no commander change) and private
  copies of a deck (clones). A new deck from a list, restores, deck details, bigger edits and
  account actions still wait for your press.
- Every tool refusal carries an `error_code` (`NOT_FOUND`, `BUSY`, `RATE_LIMITED`,
  `UNAVAILABLE`, `INVALID_ARGUMENT`, `PERMISSION_DENIED`, `FAILED_PRECONDITION`, …) and
  `retryable`, plus `retry_after` when known (collection tools and deck reads for research tools
  included), in the structured result and in its JSON text. Existing `error` values are unchanged.
- `parse_deck_export` also reads `Count`, `Set` and `Categories` columns, and returns
  `unknown_columns` naming any column it did not read.
- The Mystic Forge image tag is `1.3.2-mag3`; deploy defaults and docs are updated.
- Every guided-setup page (Claude, Claude Code, ChatGPT, Codex, the skill page and the Android app
  page) is numbered steps with one action each; the plain-text `install.md` matches.
- The admin page's "Removed-member clean-up" row says what to do next in every state, including
  each reason a check failed. IDP-AUTHENTIK.md section 12 is one start-to-finish procedure with a
  table of what the row says and how to fix it.
- CONNECT.md explains why Claude shows a generic tile for custom connectors (developer reports;
  nothing a server can set today). DEPLOY.md says the two Forge URLs are alternatives, not two
  steps.
- Android app: long-pressing the app icon shows My decks, Scan cards and Proposals from install.
  They open that page on your saved gateway (or the address screen before setup).
- Deck page: the stack handle has a 44 px finger target, and dragging a stack in the Text and
  Brewer views no longer causes extra full-page relayouts.

### Fixed
- The in-chat proposal card no longer shows Approve and Reject for a proposal that was already
  rejected, applied or expired: it checks the proposal when shown and says what happened and who
  closed it. A press on a closed proposal shows its state instead of a bare refusal.
- A refusal to apply or reject a closed proposal includes its current state, when it closed and
  who closed it. `assistant_may_apply` is false once a proposal is no longer pending.
- A proposal rejected by the assistant's own `reject_proposal` is no longer described as
  "Rejected by the user"; the result and the review page say whether the assistant, the card, the
  review page, the API or an app disconnect closed it.
- A second report no longer ends up with no Forge games because an earlier run was still going.
- `validate_decklist` no longer says PASSED for a list with duplicate cards: a Gateway checks
  section flags them (also in deck reports).
- Stored reports no longer keep the simulation text twice (about half the size).
- A set and collector number that are a different card from the name (Island with LTC #284) come
  back as `conflict`: listed for review, kept out of the decklist and the changes, unticked and
  labelled on the card picker and the scan page; `save_scan_session` stores them as unresolved.
- `format_archidekt` no longer marks a card foil (`*F*`) on a printing without foil: it picks a
  printing that has the finish, or warns which finishes exist. `resolve_cards` no longer claims
  foil there either.
- `scryfall_price` says when a printing exists but not in the requested finish, instead of "No
  results found".
- In the price list, a line naming a printing no longer stands in for a line of the same card that
  names none.
- Archidekt's own `{noDeck}` and `{noPrice}` category flags (as in
  `1x Arcane Signet [Maybeboard{noDeck}{noPrice}]`) are kept: such a card is read as a maybeboard
  card, not counted in the deck, and a new-deck proposal lists it apart. A new deck gets only
  category names, so a custom category flagged `{noDeck}` is made as a plain category: the proposal
  counts its cards in the deck and warns about it.
- In a new-deck proposal, sideboard and maybeboard rows from a pasted list, a CSV or the gateway's
  JSON no longer count toward the deck's card total or its deck-size warning.
- A trailing `*CMDR*` on a decklist line marks the commander in `parse_decklist`, `deck_stats`,
  `run_deck_report` and `compare_decks`, as `goldfish_annotate` already did.
- Lines such as `-1 Island`, `1.5 Island` or `1000x Island` are refused with the line quoted,
  instead of being read as a card called "-1 Island".
- `parse_deck_export`'s `decklist_text` keeps each card's categories (for example `[Ramp]`) and
  keeps etched rows etched.
- `goldfish_annotate` works on your own private Archidekt decks: the gateway reads the deck with
  your linked account, as `get_deck` does. When a deck can't be found, the message says it may be
  someone else's private deck or needs your account linked.
- Simulation text no longer says "the stored copy is under History" for pasted lists that were
  never stored.
- Research service image 1.3.2-mag3 (patches 0004 and 0005): `edhrec_precon_upgrade` shows real
  percentages again and names the precon's own commander apart from the commanders of upgraded
  builds; `edhrec_salt` shows salt scores; `edhrec_top_cards` shows section headings, says the
  limit is per section and that the period is ignored when a colour is given;
  `edhrec_recommendations` only suggests cutting cards you gave it; `spellbook_card_combos` no
  longer says "None total"; `precon_decklist` and `precon_export` merge split basic land lines;
  goldfish tools find double-faced cards given by their full name, and annotate says how many
  copies were skipped; the honesty report no longer lists played lands and mana rocks as
  low-impact; land drops per turn are named as such (new `avg_land_drops_by_turn`;
  `avg_lands_by_turn` kept).
- Deck page: dragging a stack's handle no longer reshuffles the page under the pointer. A bar
  shows where the stack will land, its name follows the pointer, and the stack moves once you let
  go; Escape puts it back.
- Deck page: holding a stack or a card near the top or bottom of the window scrolls the page, so
  stacks and categories off screen can be reached by mouse or finger.
- Deck page on Android: holding a card no longer sometimes opens the card menu by itself, which
  used to scroll the page instead of moving the card.
- Android app: when a share can't be opened, the message says where the link went. A Discord
  message link gets its own explanation (share or copy the archidekt.com link itself). Shares that
  put the link in HTML text or the title now open too.

## [0.7.21] - 2026-10-10

### Security
- R-142 is now enforced in one place: the gateway's shared apply refuses every Archidekt account
  action (like, vote, bookmark, follow, comment, new folder, collaborator change, deck deletion)
  unless the call comes from the review page's own Apply form, whatever page, API or tool it came
  through. Before, each route had to refuse it on its own. Nothing changes for people: Apply on the
  review page works as before.

## [0.7.20] - 2026-10-10

The rest of the Archidekt deck features that could be built from Archidekt's own site code.

### Added
- Custom mana value: set or clear a card's mana value override from the deck page's card menu
  (Mana value…), or ask the assistant (`propose_deck_changes` with `set_mana_value`). It is a
  low-risk change, like a colour tag.
- Per-card notes show in the card viewer and in `get_deck`, read-only.
- The Brewer view on the deck page: each card's picture, name and cost, type line and rules text.
- Deck collaborators: listed on the deck settings page and by `get_deck_collaborators`. Adding or
  removing one (`propose_collaborator`, or the settings page form) changes who can edit the deck,
  so it is an account action: it opens a review page and only your Apply there changes Archidekt,
  in every approval mode. Only people you follow can be added, as on Archidekt.
- More statistics controls: mana curve by type or category, colours as bars or a pie, land-only
  colour sources, pips outside the commander's identity, subtype and keyword counts, and a click
  on any of them shows only those cards. `deck_stats` returns the same numbers.
- Report trends on a deck's History page (sparklines and a table across its stored reports), and
  as `trend` in `list_deck_reports` for that deck.
- More in-chat cards (where the owner has the in-chat card on): a comparison card for
  `compare_decks`, a statistics card for `deck_stats`, a deck list card for `list_my_decks` and
  `search_decks`, and a scan session card for `get_scan_session`. The tool results are unchanged.

### Not built
- Writing per-card notes: no Archidekt route for it was found, so the gateway does not guess one.

## [0.7.19] - 2026-10-10

### Security
- R-142: an Archidekt account action (like, vote, bookmark, follow, comment, new folder, deck
  deletion) is now approved only on the signed-in review page. Its in-chat card no longer has
  Approve and Reject buttons; it shows "Approve on the review page", which opens that page. The
  gateway refuses any other way of applying one, including the in-chat card's own call, so a chat
  app that let the model press the card still could not act for you. Other proposals keep their
  in-chat Approve card.

### Parked
- Moxfield import by link: Moxfield has no public API and requests to it were refused from
  the build environment, so it is not built. A Moxfield export pasted as text imports today.

## [0.7.18] - 2026-10-10

More of the Archidekt deck page: new views, your own stack order, editing several cards at once and
colour tags.

0.7.17 was not released on its own (its build stopped on the test fixed below), so its changes
first ship in this version's image.

### Added
- Deck page: Table and Scroll views next to the existing ones.
- Stacks can be dragged into your own order. The order is kept in this browser for each deck and
  grouping, because Archidekt keeps no stack order of its own.
- Select several cards to move them to another category, change their finish or remove them in
  one save, with Undo.
- Colour tags: set, change or clear a card's coloured tag from the deck page, shown as a dot on
  the card. The assistant can propose tags too (`set_label` in `propose_deck_changes`); a tag
  change is low risk, so it follows your approval mode like other small edits.

### Fixed
- Card search's Add to deck: a second read of the same deck's categories no longer resets the
  category you just picked.

### Changed
- The in-chat card for deleting a deck shows Reject as a plain button next to the red Delete.

### Not built
- Moxfield import by URL: Moxfield's deck API could not be reached or checked from here, so it
  is not built. A Moxfield export pasted as text already imports.

## 0.7.17 - 2026-10-10

Assistant tools for actions on your Archidekt account. Each one is only a proposal: you apply it
yourself, every time, in every approval mode (R-142).

### Added
- `get_deck_comments` reads a deck's comment thread.
- `propose_deck_social` (like, vote down, bookmark, follow the deck's owner, and their undo),
  `propose_comment` (post, reply, edit or delete your own comment, vote on someone else's),
  `propose_create_folder` and `propose_delete_deck`. The review card and page show exactly what
  will happen, with the full comment text; deleting a deck is marked destructive, with a red
  "Delete this deck on Archidekt" button and the warning that Archidekt has no undo.

### Security
- R-142: a like, vote, bookmark, follow, comment, new folder or deck deletion is never applied by
  an assistant, whatever the approval mode. The gateway refuses the assistant's
  `apply_proposal` for them where proposals are applied (a "consent" risk tier no mode allows,
  plus a separate refusal for these proposals); only the member's own press on the in-chat card
  or the review page applies them. The account page, guide and skill say so.

## [0.7.16] - 2026-10-10

The first of the Archidekt features flagged in the gap review and never built.

### Added
- Card viewer: a Rulings button lists the card's official rulings (from Scryfall, kept a day), a
  double-faced card shows its back with Show back / Show front, and EDHREC and TCGplayer links
  open in a new tab (the TCGplayer link is the product page Scryfall names for the card).
- Deck comments: Up and Down votes on other members' comments, asked first like the deck's Like,
  sent to Archidekt under your linked session. Your own and archived comments take no votes.
- Deck editor: Redo, with Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y (Cmd on a Mac) for Undo and Redo. The
  keys are left alone while you type in a field or a dialog is open.

### Changed
- On a phone the editor's save bar keeps one row: Undo and Redo show as arrows (screen readers
  still hear their names), so the bar and its questions stay inside the screen at 320 px.
- Opening a card on the card search page reads its text once instead of twice.

## [0.7.15] - 2026-10-10

Small fixes to the Forge simulation from an independent gap review of 0.7.14.

### Fixed
- Deleting a deck report now stops its unfinished Forge games (queued or running). Before, the
  games kept the engine busy, and deleting a report freed the "one unfinished Forge run per member"
  limit while its games still ran.
  The same goes for old reports removed past the 200-per-member limit.
- The report page's Forge section updates by itself while the games are queued or running (every
  15 seconds while the page is visible), instead of asking to reload the page.

### Added
- The admin page's System card shows whether the Forge engine answers, and `/healthz` reports
  `degraded` when `MTG_FORGE_URL` is set and Forge does not answer (still status only, still
  HTTP 200, as for Mystic Forge).
- README status table: a row for the Forge simulation and what is not yet measured.

## [0.7.14] - 2026-10-10

### Added

- **Forge simulations (optional, off by default).** A new internal service runs
  Forge, an established open-source rules engine (GPL-3.0, unmodified), so a
  deck report can play real games against Commander precons: opponents,
  combat, the stack and the graveyard. A run happens in the background and
  the report's `forge` section fills in when it ends. Every card Forge cannot
  play is named in the report, never dropped silently. Turn it on with
  `FORGE_REPLICAS=1`, `FORGE_NODE` and `MTG_FORGE_URL` ([DEPLOY.md](docs/DEPLOY.md));
  nothing changes for an install that leaves it off.
- **Card coverage: 99.69%.** Forge 2.0.15 can play 32,793 of the 32,895 cards
  that are legal in at least one format. Every image build measures this
  again and fails below 99.5%. Forge doesn't have the other 102 yet: mostly
  Unfinity sticker and Attraction cards, plus a few Arena-only Alchemy cards.
  Reports name them.
- **Report details.** A report shows the Forge games, each deck's win rate
  with a 95% range, and the cards Forge could not play.
  - A game that hits the time limit (`FORGE_GAME_SECONDS`, default 10
    minutes) counts as a draw.
  - A seed makes a run repeatable.
  - To compare two decks, report each one with the same seed.
  - Each member can have one Forge run going at a time.
- **Forge updates.** A weekly check opens an issue when Forge publishes a new
  release, so the image follows Forge without anyone maintaining card data.

### Known gaps

- Opponents are the three newest Commander precons Forge can fully play.
  They aren't chosen by power bracket yet.
- There is no test over a large set of real decks, and no scenario check of
  how individual cards behave in Forge, yet.
- Speed and memory were measured on a GitHub arm64 runner, not on a
  Raspberry Pi: a four-player game took 21 to 39 s and peaked at about
  790 MiB. Pi numbers come from the owner's first deployment.

## [0.7.13] - 2026-10-10

A polish pass over every page and the Android app, from an inventory of each
control, icon and message.

### Changed

- **Website.** Text and control edges meet WCAG AA in both themes (darker
  link, orange and green text on light pages, lighter red on dark ones, a 3:1
  border on every field and checked control); orange fills always carry dark
  text, including the scanner's step numbers, the list badge and the profile's
  Follow button. Every page starts with a skip link, names the signed-in member
  in the account menu, marks the current theme and layout button as pressed,
  and carries description and preview metadata (and `noindex`); `/robots.txt`
  keeps crawlers out. The browser's own bar follows the light or dark top bar.
  Tick boxes and radios are drawn in the site's colors, and icon-only buttons
  have a tooltip. Times are shown in your own time zone with the UTC time in
  the tooltip, and words follow their numbers ("1 card", "2 cards"). Pages
  show "an assistant" instead of an app's internal id. Page text, tool text
  and the in-chat cards use American spelling ("color", "recognized").
- **Editor and deck page.** "New category…" is an inline field instead of the
  browser's prompt box; every quantity, category and finish control names its
  card for screen readers; a card's ⋮ menu and "Edit cards in" links open the
  editor on that card or category. The text view flows in columns on wide
  screens, the grid view's cards are smaller, and the phone banner's buttons
  sit two to a row.
- **Collection, scanner and other pages.** The collection's Add and Import
  form folds above the list. The scanner asks "New scan?" and "Delete?" in its
  own bar instead of the browser's box, shows a camera icon on the shutter and
  sits clear of the phone tab bar. Card images that fail to load show the
  card's name; Copy keeps its icon; 404 and error pages keep the signed-in
  top bar and menus. The web app has maskable home-screen icons, shortcuts, a
  short name and a themed offline page (served with `X-MTG-Offline: 1`).
- **Android app.** A gateway that never loads can be changed from the error
  screen, and Back from "Change gateway" returns to the app. Status and
  navigation bars stay readable on Android 15 and later; the camera screen
  keeps its dark colors in the light theme; the Android 13 themed icon is an
  outline. Buttons carry dark text on orange, show pressed and disabled
  states and are at least 48 dp tall. TalkBack reads the sliders' names and
  announces status and errors. The setup screen's Go key continues. A
  dark-mode switch while the app is open re-themes it at the next page, so
  the page in use is never reloaded. The app shows its offline message when
  the connection is down. The home screen label is "MTG Gateway" (the full name
  stays in Settings), and a long press on the icon offers My decks, Scan cards
  and Proposals.

### Fixed

- The folder filter matches folder names longer than 80 characters.
- Comments say when posting or loading fails, and show that they are loading.
- "Est cost" shows a dash, with a tooltip, while no card has a price; report prices show two
  decimals; the probability-of-draw table follows its form after the first
  change and counts automatic categories; a card with two types counts once
  for each.
- The compare page's deck box and list use the site's field style.

## [0.7.12] - 2026-10-10

The 0.7.11 code with a release build that finishes. 0.7.11 stays a tag
without an image or Android app: its release build stopped after signing the
app, because the build script could not read the signing certificate's
SHA-256 from apksigner's output.

### Fixed

- **Android release build.** The certificate's SHA-256 is taken from the
  keystore the app was signed with, and the build checks that apksigner
  reports the same digest for the APK, whatever wording apksigner uses.
  Pull requests that change `android/` now also build a release signed with
  a throwaway key, so this path is tested before it reaches `main`.
- The app and docs say "before 0.7.12" for the one-time reinstall, since
  0.7.12 is the first release with a signed app.

## [0.7.11] - 2026-10-10

Fixes from the owner's test round on 0.7.10, a search box in the top bar,
the first signed Android app, and less personal data for assistants.

### Added

- **Search from the top bar.** Every signed-in page has a search box (behind
  the magnifier on phones). It suggests cards as you type and offers deck
  searches by name or commander. The new card page (`/cards?q=`) shows
  matching cards, opens them in the card viewer with their rules text, and
  adds them to one of your decks through the usual approval path. The deck
  search form is tidier.
- **Admin page shows the removed-member clean-up.** It says whether the
  clean-up is off (and why), hasn't run yet, last ran fine, or last failed
  (with the reason, for example a group the token can't see). Each successful
  run also writes one log line.
- **Signed Android app.** When the repository's `android-release`
  environment has its signing secrets, the release carries the signed APK
  and bundle, and the release notes show the signing certificate's SHA-256
  ([ANDROID.md](docs/ANDROID.md)).
- **The Android app updates itself.** It looks for a newer version in the
  project's GitHub releases when it opens (at most twice a day) and from
  **Check for app updates** in its menu. **Update** downloads the release's
  APK, checks that it is this app signed with the same key, and installs it
  over the one you have, keeping your settings and sign-in. Android asks once
  to allow installs from the app; from Android 12 later updates may need no
  tap. An app from before 0.7.11 was signed with a test key and has to be
  uninstalled once, then installed from the gateway's `/app` page
  ([ANDROID.md](docs/ANDROID.md#updates)).
- **Admins hear about new gateway versions.** Twice a day the gateway asks
  GitHub for the newest release. When it is newer, admins see a notice on
  every page, and the admin overview shows the update steps for Portainer,
  Swarm and docker compose. The gateway never updates itself.
  `MTG_UPDATE_CHECK=false` turns the check off; nothing else changes for
  existing deployments.
- **The connector has its own icon, description and website.** The gateway
  tells apps that connect to it (MCP `serverInfo`) a description, the
  project's website and its icon, served from the gateway itself. Only
  signed-in apps see these. The same icon is the browser tab icon and is
  served at `/favicon.ico` and `/apple-touch-icon.png`, which apps that draw
  a connector's icon from its site look for. Before, those addresses
  answered 404. The home-screen icon of the website matches the Android app.

### Changed

- **Assistants learn only what they need about you.** `whoami` now answers
  with your display name, the gateway version and your account page, and
  nothing else: no email, no groups (your sign-in service sends every group
  you are in, including ones for other services), no IDs, no sign-in scopes
  or token details. `account_status` no longer says when Archidekt was
  linked or last used. You can see everything your sign-in service shares
  with the gateway under **What your sign-in shares** on the Account page;
  the operator guides now point there instead of `whoami` for checking
  groups and email.
- **The setup command is `/setup-mtg-gateway`.** The plugin's setup skill is
  renamed from `setup` (`/<plugin>:setup-mtg-gateway` in Claude Code). It
  keeps that name under an owner's own `MTG_SERVER_NAME`. Install the
  plugin again from `/install` to get it.
- The plugin names its author (`TheUncleBen`); under an owner's own name it
  still names the owner and links nowhere.

### Fixed

- **Deck legality counts every format rule.** A Commander deck with 101 cards
  no longer shows the green Legality chip. The chip, the stats panel and the
  edit API now use the deck checks, deck size included.
- **History matches what is still on Archidekt.** A backup copy deleted on
  Archidekt leaves the "Backup copies on Archidekt" panel. Restore uses the
  gateway's own snapshot, so it still works. A deck that no longer exists
  offers no Restore, and restoring it fails with a clear message.
- **Close editor asks in the page.** With unsaved edits, leaving the editor
  (Close editor, a link or a menu form) asks in the save bar: Keep editing,
  Discard changes or Save changes. The browser's "Leave site?" box is kept
  only for closing the tab or reloading.
- **Views are remembered.** The deck page's View as (text, stacks, grid) and
  the grid or list view of My decks and the collection stay as you left them.
- **A dropped connection no longer shows "You're offline" at once.** A page
  load that fails at the network level is tried twice more first.
- **The install steps for assistants are served as plain text**
  (`/install.md`), since some assistants' web readers refuse
  `text/markdown`.
- **Sharing a link into the Android app** works from apps such as Discord
  that send the link inside message text. Archidekt deck links open the
  gateway's deck page; other links get a clear message.
- **Add to home screen works on every page.** The pages' security policy
  now allows the gateway's own web app manifest (browsers refused it
  everywhere except the scanner), and the scanner names only its own.
- **Deck settings no longer change a deck's format by themselves.** A deck
  with no format shows "No format set"; before, the list showed its first
  format and saving any other setting applied it.
- Short menu items stay on one line; the editor's save bar no longer covers
  the footer links at exactly 600 px wide; a menu-history browser test no
  longer depends on timing.
- Docs: the CI, test and release descriptions match what the workflows do.

## [0.7.10] - 2026-10-09

The 0.7.9 code with a release build that cannot be stopped by Docker Hub's
rate limit. 0.7.9 itself is a tag without an image: its release build failed
twice while fetching the base image.

### Fixed

- **Release builds no longer depend on Docker Hub's anonymous pull limit.**
  The base images and the Dockerfile frontend of the gateway and of Mystic
  Forge come from Google's public mirror of Docker Hub (`mirror.gcr.io`, the
  same images and digests). As a fallback,
  the image jobs sign in to Docker Hub when the repository secrets
  `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` exist, and skip the sign-in
  otherwise ([DEPLOY.md](docs/DEPLOY.md)).

## [0.7.9] - 2026-10-09

Everything the owner's test round raised, in one update: the held work
(the shorter Archidekt disclosure, the Android app's sign-in through the
phone's browser, the faster and themed pages, the owner's own name on the
public files) and the fixes below. Nothing to change in the stack or the
settings unless you want the new optional `MTG_ANDROID_PACKAGE`; the
gateway makes one extra outbound request a day to Scryfall (the card-name
catalog). Back up the database before you update, as always.

### Changed

- **Connecting from outside:** CONNECT.md now starts with a check from
  mobile data (the AI service reaches the gateway from the internet, not
  from your network), and TROUBLESHOOTING.md has a row for "couldn't reach
  this address" with the proxy, access-list, certificate and router checks.
- **The request log names the route, not the request.** Each request's log
  line now reads `GET /decks/{deck_id} 200 41ms …` instead of the path
  itself, so deck numbers, Archidekt usernames, signed-link tokens and member
  IDs stay out of the gateway's log at the default level, as the Archidekt
  link disclosure says. A request no page answers is logged as its first
  path segment only.
- **Top bar in a narrow window with a mouse** (600 to 800 px, an open Fold's
  browser or a half-width desktop window): the brand's word steps aside so
  the section links end before the account button and the page needs no
  sideways scroll.
- **Android app: sign-in opens in your phone's browser**, where passkeys
  (Bitwarden and the like) and password managers work, and comes back to
  the app signed in. The gateway's sign-in page inside the app hands off to
  the browser; the browser hands back a one-time code that only the app can
  redeem (`POST /login/app`, new). The browser itself is not signed in to
  the gateway afterwards, and after a sign-out the next sign-in asks for
  credentials again. Older apps keep the in-app sign-in. New optional
  `MTG_ANDROID_PACKAGE`, only if you build the app with another package ID.
  Install the new app over the old one; it needs this gateway version for
  the browser sign-in and signs in as before on an older one.
- **Linking Archidekt:** the disclosure above the link form is shorter and
  plainer. A four-line summary now comes first and is always open. The full
  detail keeps every fact it had, reworded with less repetition, and is
  folded: the tick box stays disabled until the member opens it. With
  JavaScript off the detail shows open and the box works, and the server
  still refuses a link without the tick. The list of what the person who runs
  the server can read now also names the member's user ID at the sign-in
  service. Pressing Link before opening the detail sends nothing and points
  at the detail. After a failed link the form comes back with the detail open.
  docs/USING.md carries the same text.
- **The Guide is a handbook in four parts.** Tutorials take a new member
  through linking Archidekt, connecting an assistant, a first deck edit and a
  first simulation step by step; how-to guides give one recipe per task (add
  cards, approve a proposal, restore a snapshot, compare with a precon, export
  and import, scan, the collection, playtest, theme and layout, sign out
  everywhere); the reference lists what the assistant can do tool by tool,
  every page and its address, the approval modes and risk levels, the
  keyboard shortcuts (the deck page's card menu on Shift+F10, Enter to open a
  card) and the editor's search bar syntax ("3 sol ring", the chips); the
  explanation covers how approvals, snapshots and the per-save backup copy
  work, how lists stay fresh, privacy, and why Playtest opens Archidekt in a
  new tab. A contents rail (a drawer on phones) follows the reader and marks
  the section in view, every heading has its own link, a search box filters
  the sections and the rail as you type (Escape clears), and "Back to top"
  appears once you have scrolled. Text that had gone stale (Playtest in a
  frame, one sign-out button) is corrected.
- **Adding cards is one search bar.** In the deck editor, chips above the
  box say where a card goes (Auto, a category, the maybeboard), Enter
  adds the highlighted card and keeps the cursor in the box, "3 sol ring"
  adds three, and on wide screens the printings of the highlighted card
  show as pictures beside the list: the set used last on this deck is
  pre-selected, and a click adds that printing. Enter pressed while the
  list is still catching up takes the first answer when it arrives.
- **Playtest opens Archidekt's playtester in its own tab** (in the app: the
  phone's browser), where your Archidekt sign-in lives, so private decks
  play too. The gateway no longer frames it; old links are sent on.
- **"Desktop layout" switch** in the account menu, remembered per device:
  a phone or the app shows the computer layout, like a browser's Desktop
  site switch. "Fit the screen" goes back to the adaptive layout.
- **Sign-out acts on the first click.** The button says "Signing out…"
  and locks at once, both account-page buttons post straight away
  (no confirmation detour for "all my devices"); the cache clearing is
  gone too (see "Sign-out took seconds" under Fixed).
- **A deck report is a page to read, with exports.** "Run simulation" used
  to dump the research service's raw text. The report page now opens with
  the deck, its commander, the games run and when; shows the headline
  numbers as tiles with their 95% intervals (turn the commander lands,
  chance of 40 damage or a lethal board by the last turn, mulligans, land
  drops); draws four small charts (milestones reached by turn, the turn the
  commander was cast, mana available and damage per turn) in the site's
  colours without any script; explains in plain words what the simulation
  could not model (cards it does not recognise, removal and other
  interaction it cannot value, cards that never mattered, and the shortcuts
  it takes) with each group's share of the deck; and gives the validation
  verdict as a badge with the problems listed. Double-faced cards show their
  front face. The raw output stays, folded away at the end. The page has no
  assistant tool names on it. Buttons download the report as Markdown or as
  a self-contained HTML page (no scripts, light and dark) and copy the
  Markdown to the clipboard. The deck page's button says the run takes up
  to a minute and shows "Simulating…" while it does; a report reused
  because the deck had not changed says so.
- **History is a timeline, not a log.** Entries are grouped by day under
  headings that stay in view while scrolling; each row shows what it is
  (edit, new deck, restore, snapshot, report) with an icon, the deck as a
  link, a one-line summary of the change, who made it (you or the
  assistant), the time, and its details folded away. Snapshot rows link to
  the change they were taken before and keep their "Restore (review first)"
  button. A filter bar narrows the page by deck, type, state and a search
  over deck names and change text, a "When" preset (Today, Last 7, 30 or 90
  days, This year: UTC calendar days, as every time on the site, chosen from
  a themed list rather than a native date picker) and "Older" pages through
  the rest 25 at a time. "Group by" switches the timeline from days to
  decks: each deck a section headed by its name, linked to the deck and to
  its own history, decks in the order of their newest entry and each one's
  rows newest first, with the filters, folded details, Restore and paging
  unchanged. Every filter and the grouping sit in the address bar, so a
  view can be bookmarked. The "Backup copies on Archidekt" panel no longer
  touches the "Older" button. The per-deck trend tiles are styled like the
  rest of the site.

- **Home, My decks and the collection open at once.** With an Archidekt
  account linked, the member's deck list is kept in memory for a minute and a
  half after Archidekt answered and served from there by Home, My decks, the
  deck tools and the JSON API; for a quarter of an hour more a slightly older
  list is shown while one refresh runs in the background. Every change the
  gateway sends to Archidekt for that member (a new deck, an applied proposal,
  a deleted deck, a folder, a tag, a cover) drops the list first, so the next
  view is live again. The listing itself is one Archidekt request (by the
  account's id, where before it was one by name and one by id, each waiting its
  turn in the shared queue), following up to four pages of fifty decks. The
  collection's unfiltered first page is kept for a minute per sort and dropped
  by every collection add, change or removal. When the list is cold and
  Archidekt has not answered within a second and a half, Home and My decks go
  out at once with a grey placeholder that the page fills from
  `/api/decks/mine` (same markup, rendered by the gateway; no change without
  script, where a reload shows the list). Every response now carries a
  `Server-Timing` header (total, Archidekt and sign-in-provider time) and the
  log has one line per request (method, path, status, milliseconds; no query
  string or member id). The link's "last used" time is written at most once a
  minute instead of on every Archidekt call.
- **Edit a deck straight from its page.** On your own deck a right-click on a
  card (a press and hold on a phone, Shift+F10 or the Menu key on a focused
  card) opens the site's own card menu in place of the browser's: open the
  card, one more or one fewer copy, move it to a category or the maybeboard,
  remove it, or jump to the editor. The card viewer has the same quantity
  buttons and Remove. Each change is saved at once as one proposal with its
  snapshot (the same path as the editor, with the same "are you sure" for a
  big removal), and the page redraws in place: the card's count and the
  stack totals, the Size line, the Legality chip, the Legality and Deck
  checks panels, all from a fresh read of the deck, with a toast offering
  Undo. Dragging cards between categories now saves correctly (its
  request named the wrong field, so nothing was moved) and Undo all puts
  the cards back without reloading. On someone else's deck the menu offers
  Open card and Open on Scryfall.
- **Backup copies stay out of your deck lists.** The copies the gateway
  makes on Archidekt before a change (in the backup folder, or named
  "… (backup date UTC)") no longer appear on Home, My decks, the assistant's
  deck list or the deck API. History shows them in a "Backup copies on
  Archidekt" panel, each linked to Archidekt and to the deck it copied; a
  deck's own history lists only its copies. My decks says how many are kept
  out and where to find them.
- **Typed card names suggest instantly.** The gateway downloads Scryfall's
  card-name catalog once a day (about 700 KB) and answers suggestions from
  memory, so a name list appears within about a hundred milliseconds of a
  pause in typing instead of after a paced Scryfall round trip per
  keystroke. Until the catalog has loaded (the first minute after a start,
  or while Scryfall is unreachable) suggestions come from Scryfall as
  before.
- **Suggestion lists are the site's own.** Every card-name box (deck search,
  home, Quick add, the deck editor, the collection) and the compare page's
  precon box show a themed, keyboard- and screen-reader-accessible list
  instead of the browser's built-in datalist. Down and Up move, Enter
  picks, Escape closes; the match is underlined.
- **Deck editor, adding a card.** Suggestions show the card's picture, mana
  cost and type line; Enter adds the card and keeps the box focused for the
  next one; a new row shows its picture and mana cost. The add form's
  controls share one row on desktop and fold onto two or three rows on a
  phone (no stray full-width button). Categories sit side by side on wide
  screens. The printing picker opens as a dialog over the page.
- **Card viewer.** Mana costs and the symbols in rules text ({T}, {B}, hybrid,
  Phyrexian, snow, energy, numbers) are drawn as the gateway's own glyphs on
  coloured discs, in the viewer and in every card row. The viewer is a
  proper dialog: a blurred, darkened page behind it, a header with the name
  and cost, the type line, rules and flavour text, a facts table (printing,
  artist, price, salt, EDHREC rank) and legality chips, an actions row, a
  focus trap, Escape and the phone's Back button to close, a bottom sheet
  on phones; it never scrolls sideways.
- **Forms and buttons.** A form's buttons sit together in one row at one
  height (the Search decks page's Search and Clear buttons no longer sit
  mid-form at different sizes; the Account page's buttons are one row). On
  phones they stack full width.
- **Your own name hides this project on your gateway's public files.** With
  `MTG_SERVER_NAME` set, the plugin's short name follows it (`Deck Helper`
  becomes `deck-helper`): the install pages, `/plugin/marketplace.json` and
  the plugin download (now `/plugin/<name>.zip`) use it for the plugin, its
  connector and its skills, and the download no longer carries this project's
  name or a link to its repository. Anyone who installed the plugin under the
  old name installs it again from `/install`. Without `MTG_SERVER_NAME`
  nothing changes.
- **`/healthz` answers only `{"status": ...}`.** Anyone can ask it, and the
  version and the research service's name told a stranger which project the
  gateway runs. The System card on the admin page still shows both.
- **Dropdown lists are the site's own.** Every dropdown (the filters on
  Decks, Search, Collection and History, the deck page's views and odds,
  the editor's category and finish per row, deck settings, New deck,
  folders, the profile) opens the gateway's themed list instead of the
  browser's pop-up, on the website and in the Android app alike. It works
  from the keyboard (Enter, Space or an arrow opens it; arrows, Home, End
  and the first letters move; Enter picks; Escape closes) and on phones
  and touch screens it is a bottom sheet with finger-sized rows. The
  underlying form controls are unchanged, so filters still apply as soon as
  a choice is made, and a page with many dropdowns (the editor) builds a
  list only when it opens.
- The file pickers on Collection (Import a list) and New deck are themed
  controls (a Choose a file button and the chosen name), the editor's row
  category no longer clips its text, and the editor's two Maybeboard chips
  (the category and the zone) are told apart by their names.

### Added

- **The backup copy on Archidekt is a tick box per save.** The deck
  editor's save bar has "Also keep a backup copy on Archidekt", on by
  default; unticking it skips only that extra copy for that save. The
  gateway's own snapshot (Restore under History) is always kept, and the
  assistant's applies always make the copy. `POST /api/v1/proposals`
  takes `archidekt_backup: false` for the same choice (browser session,
  with `apply: true`). Quick edits from the deck page's card menu keep the
  gateway snapshot only.
- `POST /api/v1/decks/{id}/edit` (browser session only): the deck page's own
  save. Runs the proposals path exactly (propose, hand-edit confirmation,
  apply with a snapshot), reads the deck again from Archidekt and answers
  with the touched rows, the freshly computed checks and the re-rendered
  Legality chip and Deck checks panel; a read that still lags the write is
  retried once and marked `stale`, and `{refresh: true, names}` reads again.
- `GET /scan/api/peek?names=a|b|c` (browser session only): mana cost, type
  line, small picture and default printing for up to twenty exact card
  names, from one batched, cached Scryfall lookup; the suggestion lists use
  it to decorate their rows.

### Fixed

- **Touch dropdowns:** the tap that closes a dropdown's bottom sheet no
  longer also lands on what is under the finger (a link, a remove button).
  The printings card in the chat picks a finish with the card's own chips
  instead of the browser's list. A card's Details menu in the collection
  grid opens inside the window instead of 50 px off its left edge; any
  dropdown panel near a window edge is shifted back inside, whichever
  edge it hangs from (the deck page's More menu stuck out on the right
  at 720 px). On touch
  screens History's Details, a report's folded sections and the deck's
  text rows are 40 px tall. The review page's third choice is "Not now",
  and the editor shows one Maybeboard chip (the zone's), not two.
- **No sideways scrolling:** a deck with a long name ("Copy of - Sample
  Commander Deck", or longer) no longer widens My decks, a profile's
  decks or the precons grid past the window; the name ellipsises in its
  tile. A collection card's controls row (minus, count, plus, Details,
  remove) fits inside its card instead of touching the next card. The
  review page's three choices sit in one row only from 800 px wide and
  stack below that, so "Not now" never sits alone on a second line.
  Inside a collection row's Details menu the Finish, Condition and
  Language pickers and the Save button keep their borders and colour.
  A browser test now lays out every page at 18 widths from 320 to 1400
  px, with a mouse and on a touch screen, with long deck, card and
  member names, a full collection, history and a pending proposal, and
  fails on any sideways scroll, clipped or overlapping control, or
  dropdown panel outside the window. Its data is hostile: a 40-character
  unbroken username and deck name, long folder and tag names.
- **Phone menus and Back:** one history entry stands for "a menu is
  open", with one owner in the page script: opening a second menu
  straight from the first, or right after closing it, keeps that entry,
  so Back closes the menu and never leaves the page (gate R4-1), and
  never goes back twice. Three browser tests tap real menus on a phone
  and press Back.
- **Long names in controls:** the Follow button on a member's profile
  and on a deck page, the editor's category chips and the follow
  confirmation shrink and ellipsise inside their row, with the whole
  name in the control's title, instead of widening the page (a
  40-character username made a profile 163 px too wide at 320 px). In
  the My decks list view on a phone the tags sit under the row instead
  of squeezing the deck name to 44 px, and a collection card's picture
  fills its tile (a card with no image no longer collapses to a dot).
  The follow question on a profile grows with its text, so a long name
  wraps and its Follow and No buttons stay inside the chip instead of
  dropping under the Archidekt profile link (gate R5-1); the profile
  header puts the social row under a long name rather than squeezing
  either, a profile is found for usernames over 60 characters, the
  deck page's follow question wraps a long name too, and a long
  unbroken category name wraps in the editor's category heading and
  in the deck page's stats tables (gate R5-2, R5-3).
- **Touch screens:** the small controls a mouse never minds are at least
  40 px tall on a phone or tablet: the editor's add-to chips, the deck page's
  category button and odds form, folded Details, footer links, the brand
  mark, History and Proposals list links, report crumbs and Search's popular
  chips. The proposal review's three choices sit at one height, the deck
  banner's Comments link matches its Like and Bookmark buttons, and an
  admin's top bar keeps the Admin link on every page (it was missing on
  Proposals, Account, the sign-out, Assistant skill and Android app pages).
- **A used-up Archidekt budget no longer looks like a missing deck.** When a
  member's Archidekt budget (`MTG_ARCHIDEKT_CALLS_PER_10_MIN`) is spent, the
  deck, grid, export, compare, editor, settings, delete, folders, My decks,
  collection, search, precon and profile pages all answered differently
  ("Deck not found", "Cannot edit this deck", 200, 400, 404, 502 or 503).
  They now share one page, *Archidekt is busy*, with HTTP 429, a
  `Retry-After` header from the budget, `Cache-Control: no-store` and a
  *Try again* link to the same address; an unreachable Archidekt gets the
  same treatment as *Archidekt can't be reached right now* (503). Deck
  exports answer the same status as plain text or JSON. "Deck not found"
  (404) is kept for a deck that is really not there. The JSON API already
  answered 429 and is unchanged.
- **Deck page save:** the one-click save's follow-up (the "Save anyway"
  after a confirmation question) now answers only the question the page
  itself asked. A pending proposal an assistant made is refused there with
  a pointer to its review page, so it keeps its Approve step and its backup
  copy. An already-answered question cannot be answered twice.
- **Account menu on the light theme:** its links were white on the light
  menu (and bold, at the bar's height); they are the menu's own colour and
  weight again.
- **Simulations lost double-faced cards.** Archidekt names a transforming or
  modal card with both faces (`Enduring Angel // Angelic Enforcer`); the
  research service looks names up through Scryfall's collection endpoint,
  which knows only the front face, so such cards were dropped as "not
  recognized by Scryfall" and the deck ran short. The gateway now sends front
  faces to the simulator and the validator. Your own lists and exports keep
  both faces.
- **The commander was listed as an unrecognised card.** The simulation's
  honesty report filed the commander under "unrecognized, drawn 0%" because
  it is cast from the command zone. It is now set aside with one sentence
  saying its abilities beyond combat are not modelled, in the numbers and in
  the text. A result no longer points at `goldfish_report`, a tool the
  gateway hides.
- **Pauper Commander leaders in another printing.** The format lets any
  creature ever printed at uncommon lead, whichever printing the deck uses.
  A deck's data names only the chosen printing's rarity, so a rare or mythic
  printing of such a card used to fail the deck check; it is now reported
  as "not verified" with the reason, and the deck passes.
- **No profile picture, and no way to tell why.** The Authentik guide makes
  selecting the uploaded-picture mapping on the provider an explicit second
  step (creating the mapping alone sends nothing, and with an uploaded
  picture first in Avatars no Gravatar is sent either), the troubleshooting
  table says so, and with `MTG_LOG_LEVEL=DEBUG` the gateway logs which
  picture claims arrived, by kind only (never the address or the image).
- **Nginx Proxy Manager snippets that took the site offline.** DEPLOY.md step
  7 now puts the two rate-limit pieces in their two places (`limit_req_zone`
  only in `/data/nginx/custom/http_top.conf`, never in a proxy host's
  Advanced tab) and says what the wrong place does; the troubleshooting
  table has the symptom and the way back. The optional `location
  /cards/data/` block from 0.7.4 is withdrawn: the link's token lives ten
  minutes and returns only the member's own data, so it is harmless in a log.
- **Sign-out took seconds.** The sign-out response (and *Delete my data*)
  asked the browser to clear its whole HTTP cache as well as the site's
  storage; clearing the cache is the likely slow part in Chrome (inferred, not
  measured). Every gateway page is already sent with
  `Cache-Control: no-store`, so only the storage is cleared now.
- **Release notes carry the app files' checksums.** A release that ships the
  Android app lists the SHA-256 of the APK and AAB next to the signing
  certificate, so a download can be checked against the notes.
- **A deck's history lost older entries.** The page filtered the account's
  newest 20 proposals and snapshots down to one deck, so a deck's own
  history page (and the deck's API listing) stopped short whenever other
  decks had been busier. The deck, type, state, search and page are now
  applied in the database query.
- **Trend tiles and sparklines on the history and report pages were
  unstyled.** Their styles lived in the deck page's stylesheet only; they
  are now part of the site theme.

## [0.7.8] - 2026-10-08

Wording fixes from the acceptance check of 0.7.7, and a narrower Authentik
permission for the optional clean-up. No code behaviour changes; nothing to
change in the stack or the settings. The only secret to replace is the
optional clean-up's token, and only if you set it up with 0.7.7's steps
(see below).

### Changed

- **Linking Archidekt:** the disclosure now also says that the session is
  used to read, as the member, other people's decks and comment threads
  they look at and the list of people they follow, and to look up the
  cards in their changes; that deck,
  folder and tag changes, deck deletes and backup copies on the member's
  Archidekt account go through it; everything else the person who runs the
  server can read (name, email, username and groups at the sign-in service,
  profile picture, sign-in times, approval mode, collection changes, deck reports and covers, connected
  apps, usage counts) and that the sign-in service's tokens kept for the
  group check are encrypted with the same key; and everything admins see
  (username and user ID at the sign-in service, open token and session counts, the
  disabled date) and that they can also enable an account.
- **Removed-member clean-up setup** ([IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md#12-optional-removed-member-clean-up)):
  give the token **Can view Group** on the two gateway groups only (an
  object permission), checked on Authentik 2026.8.3 to be enough; with it
  the token sees no other group. The docs now say what the token can read
  (those groups' members' usernames, names and emails) and that to cut it
  off you delete the service account (deactivating lasts only until it is
  reactivated), because it can make itself new tokens. If you set it up
  with 0.7.7's steps (a global permission), delete that service account
  and follow the new steps with a new account, token and secret.

## [0.7.7] - 2026-10-08

Members now see exactly what linking their Archidekt account gives the
gateway and the person who runs it, and the stored Archidekt session is
handled more strictly. Nothing has to change in the stack, the settings or
the secrets. One optional addition: with Authentik, an API token that may
only view groups turns on an hourly clean-up of removed members' stored
Archidekt sessions (an optional secret, `mtg_authentik_api_token`, and
`MTG_AUTHENTIK_API_TOKEN_FILE`; three commented lines in the stack file).

### Added

- **Removed-member clean-up (optional, Authentik only):** once an hour the
  gateway asks Authentik who is in `MTG_REQUIRED_GROUP` and
  `MTG_ADMIN_GROUP` and deletes the stored Archidekt session of every linked
  member who is in neither or is deactivated, with an `archidekt_link_swept`
  audit row. It deletes nothing when Authentik's answer can't be trusted
  (unreachable, an error, empty, partial, split into pages, malformed,
  naming nobody the gateway knows, or removing more than three links and
  more than a quarter of them in one round) and waits longer before the next try.
  Off unless `MTG_AUTHENTIK_API_TOKEN_FILE` names a readable token file; an
  unreadable one leaves it off with one warning. Setup:
  [IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md#12-optional-removed-member-clean-up).
  New settings `MTG_AUTHENTIK_API_TOKEN_FILE` and `MTG_AUTHENTIK_API_URL`.

### Changed

- **Linking Archidekt:** the one-line trust note above the link form is
  replaced by a full, fixed disclosure: what linking does and what the
  gateway uses the session for; what is stored and never stored; what the
  person who runs the server can see and do (with the server and its key
  they can open the session and act as the member on Archidekt until it
  expires, and they control the code); what the site's admins can and
  cannot see or do; logs and backups; how long the session lasts and how to
  end it, including what is not known. Members tick a box to say they have
  read it, and it stays on the Account page after linking. No setting turns
  it off (the old switch was a constant in the code, not a stack setting).
- **Account page:** shows the date the stored Archidekt session stops
  working, read from the member's own refresh token (about 40 days after
  linking). `account_status` returns it as `link_expires_at`.
- **Wording:** the unlink message and the docs no longer say that Archidekt
  keeps accepting an unlinked session or that changing the Archidekt
  password ends it; neither is known.

### Security

- The stored Archidekt session is sealed to its member inside the
  encryption, so a session copied into another member's row opens nothing.
  A session stored by 0.7.6 or earlier carries no owner, so the first start
  seals it to whichever member's row holds it then. Only that first start
  does: after it, an unsealed session (copied in from an old disk image, or
  written by 0.7.6 after a rollback) is deleted and that member links again.
- Every hour the gateway deletes stored sessions that have expired, instead
  of keeping them until the member next uses the gateway.
- Relinking replaces the old session at once on disk (the write-ahead log is
  flushed, as for an unlink) and restarts the link's dates.
- A new test checks that no password or Archidekt token reaches the logs at
  any level while linking, using, refreshing and unlinking.
- The Archidekt client keeps no cookies, so a cookie Archidekt sets during
  one member's sign-in is never sent with anyone else's request.
- `httpcore` is never logged below WARNING (its debug lines carry response
  headers); `httpx` request lines only at DEBUG.
- A link that finishes after the account was disabled, removed from the
  group or deleted stores nothing.
- Backups are cleaned in memory before anything is written, so no backup
  file ever holds a stored session, even for a moment.
- Links made on the Account page are logged as made in the browser.
- Operator docs: when you remove someone from the group, also press
  **Disable**; otherwise, without the clean-up above, their stored
  Archidekt session is only deleted when they next use the gateway or when
  it expires. The same goes for someone deactivated or deleted at the
  identity provider, whose session the docs used to say stays until an
  admin deletes their data.

## [0.7.6] - 2026-10-08

One small security fix from the acceptance check of 0.7.5. Nothing to change
in the stack, the settings or the secrets.

### Security

- **In-chat card links:** a card link that had used up its 20 fetches could
  be fetched up to 20 more times in the last second of its 10 minutes,
  because its fetch count restarted 10 minutes after the first fetch instead
  of when the link was issued. The count is never restarted now, and it is kept for
  exactly as long as the link opens. The link only ever showed the member's
  own data.

## [0.7.5] - 2026-10-08

Small fixes from the acceptance check of 0.7.4: text that was too faint to
read comfortably, a badge that broke in two on phones, and a few wording
slips. Nothing to change in the stack, the settings or the secrets.

### Fixed

- **Dark theme, Delete-deck page:** the heading used the red meant for button
  backgrounds (2.55:1 on the dark panel); it now uses the red meant for text.
  A comment's Delete link had the same colour and is fixed too.
- **Light theme, secondary text** (deck facts such as "Deck size" and
  "Lands", Compare captions, code samples) was 3.58:1 on grey panels; it is
  darker now and passes 4.5:1. The chosen theme in the account menu
  ("Light theme") was 4.01:1 and now passes too.
- **In-chat cards:** small muted text (counts, group headers, years, the
  account card's "ready" pill) measured 3.9 to 4.5:1. The cards now take the
  app's secondary text colour, and their own defaults pass 4.5:1 on every
  card background. The "ready" pill keeps its green.
- **Approve card:** the "LOW RISK" badge no longer breaks across two lines on
  a narrow card; it moves under the state badge whole. A long change on a
  narrow card ("Main → Maybeboard (leaves the deck, −1)") no longer squeezes
  the card's name to one letter a line; it wraps under the name instead.
- **Proposal review page:** it showed the note written for the assistant
  ("do not call apply_proposal…"). It now says what happens next in words
  for the person reading it.
- **Card data links:** a used-up link could work again if more than 8192 other
  links were fetched within its 10 minutes. A link whose counter had to be
  dropped is now refused; links issued after that work as usual. When the
  counters fill up, the member holding the most loses their own oldest
  counters, so one member fetching thousands of links can't use up anyone
  else's.
- **Deck stats for Pauper Commander:** `legality_problems` no longer lists an
  uncommon commander as not legal (the deck checks already passed it); a
  banned commander is still listed.
- Wording: "Archidekt actions" in the README, the operations guide and the
  "still running" refusal; the shutdown comment now says 200 s; the
  capabilities page says the proposal card's Reject acts too, not only
  Approve.

## [0.7.4] - 2026-10-08

Interactive cards in the chat wherever a person has to pick or press, built
on the open MCP Apps standard the Approve card already used (Claude on the
web, desktop and phones; ChatGPT as reported by OpenAI, to be confirmed).
An app without cards (Claude Code, older clients) gets the same text as
before.

### Added

- **Printings card** on `card_printings`: every printing with its picture
  in a scrolling grid, a set filter, a finish filter, Expand to full screen
  where the app offers it. Tapping a printing tells the assistant the set and
  collector number as text; only then does it propose. The model's own copy
  of the result is small (the sets and the newest printings), so a card with
  hundreds of printings no longer fills the chat.
- **Picker card** on `resolve_cards`: the recognised
  cards with pictures and status, a tick per row to leave one out, suggestion
  tiles for ambiguous or unknown names, **Use these cards** sends the kept
  rows back to the assistant as text. In an app that offers form questions
  but no cards (Claude Code), the assistant asks about ambiguous names with a
  form instead.
- **Deck card** on `get_deck` and `get_snapshot`: the deck by
  category with the commander, colours and mana curve, list or picture view,
  a filter, tap a card for its picture and rules text, links to Archidekt and
  to the deck in the app. The assistant's text is unchanged.
- **Account card** on `whoami` and `account_status`: what is set up and what
  is not, with a button to the Account page (where Archidekt is linked).
- **Reject with a reason** on the Approve card: Reject asks why (optional);
  the reason goes back to the assistant with the outcome.
- Cards fetch bulky data (a deck's rows, a card's printings) through a signed
  link to `GET /cards/data/{token}` that is tied to the member and expires
  after ten minutes and twenty fetches; the link is handed to the card only,
  never to the model, and a replayed link is answered from a cache instead
  of reaching Archidekt or Scryfall again. Pictures and rules text come
  straight from Scryfall.

### Changed

- `card_printings` adds `sets` (one row per set: code, name, count, newest
  release, finishes) and a `note` beside its `cards` rows, so the assistant
  can name the sets without listing every printing; in an app without the
  card it narrows with `set_code` as before.
- `whoami` carries `account_page`; `MTG_APPLY_IN_CHAT=false` now removes
  every card and the data link endpoint, not only the Approve card.
- A gateway restart gives a background apply 90 seconds to finish (was 5)
  before recording it as interrupted, after the usual 100 seconds for running
  requests; the stack's `stop_grace_period` rises from 120 to 200 seconds so
  Docker allows both (an optional stack change; nothing else to set).

### Fixed

- Pauper Commander: a commander on the format's banned list is reported as
  banned (it was only checked for rarity).
- The rate-limit message says "Archidekt actions", which is what the
  per-member budget counts.
- `/healthz` reports Mystic Forge `ok` within seconds of it starting: a
  failed probe is now held for five seconds, not thirty.

### Security

- A database backup is written inside a fresh owner-only folder under the backup directory and
  moved into place once complete, so nothing else can swap a link in while SQLite writes it.
  Work folders a crashed backup left behind are removed by the next backup.
- Card lookups are cached per member: an answer fetched with one member's
  Archidekt sign-in is reused for that member only, never served to another.
- A card data link's replay budget cannot be reset by issuing many other
  links.

### Documentation

- CONNECT, ONBOARDING, USING and the in-app Guide: what each card does and
  that a pick is always relayed to the assistant as text. API: the data link
  endpoint. OPERATIONS and ARCHITECTURE: the signed links.

## [0.7.3] - 2026-10-08

The assistant's tools made harder to misuse, Archidekt sign-ins kept away from
everyone but their owner, and the gateway made lighter on Archidekt.

### Upgrade notes

- Optional new stack variables (defaults shown; nothing to set to keep them):
  `MTG_ARCHIDEKT_MIN_INTERVAL=1.0`, `MTG_ARCHIDEKT_MAX_PER_MINUTE=40`,
  `MTG_ARCHIDEKT_RETRIES=2`, `MTG_ARCHIDEKT_BACKOFF_BASE=1.0`,
  `MTG_ARCHIDEKT_CACHE_SECONDS=60`, `MTG_ARCHIDEKT_CARD_CACHE_SECONDS=3600`, and
  `MTG_BACKUP_COPY_DIR` (empty: no second copy). The stack and compose files carry them, plus one
  new mount for the copy folder; paste the new file to pick them up.
- Backups made from now on leave out Archidekt sessions and identity-provider tokens. After a
  restore, people sign in again and relink Archidekt.
- `get_my_deck` and `archidekt_user` are gone: `get_deck` reads your own decks too (check
  `owner`) and `search_decks` takes `owner`. Old Mystic Forge `params` calls still work.

### Added

- A large apply no longer outlives the assistant app's wait: after 20 seconds it answers
  `applying` with `progress` (cards looked up, changes sent) and carries on in the background.
  `get_proposal`, the review page (refreshing itself) and the in-chat card show the progress;
  applying again meanwhile only reports it. A restart records an unfinished apply as
  `interrupted`.
- `get_deck` and `get_snapshot` take `view` (`summary`, `text`, `cards`, `export`, `full`) so a
  read costs only what it needs.
- `search_decks` resolves a partial commander name (Scryfall, commanders only): one match is
  searched straight away (`commander_matched`), several come back as `commander_suggestions`. The
  web Search page does the same ("Showing decks led by…", "Did you mean"). `owner` lists one
  user's public decks; `limit` trims the page.
- `card_printings` filters by `set_code` and `finish`.
- `propose_deck_details` also moves the deck to a folder, adds or takes off tags and sets the cover
  (always high risk).
- `propose_collection_changes` gets the in-chat Approve card, and an applied change is read back
  from Archidekt (`verified`, `mismatches`).
- `compare_decks` takes `a_list` / `b_list` for pasted lists; `deck_stats` and `parse_decklist`
  return `unread_lines`; `list_my_proposals` rows carry a `summary` and `closed_at`;
  `propose_new_deck` warns on a deck too small for its format.
- `/healthz` reports `mystic_forge` (`ok`, `down`, `not_configured`); with it down the status is
  `degraded` but still HTTP 200, so the container isn't restarted. The admin page shows it and the Android
  setup screen accepts it.
- `MTG_BACKUP_COPY_DIR`: every backup is also copied to a second folder (another disk or shared
  storage) and pruned the same way; failures show on the admin page.
- Linking Archidekt shows a plain note on Archidekt's terms and on who runs the gateway, and needs
  a tick to go ahead.
- Gentle on Archidekt: at most `MTG_ARCHIDEKT_MAX_PER_MINUTE` requests a minute for everyone
  (extra requests wait their turn); reads that time out or get a 5xx are retried with jittered,
  doubling waits (writes and 429s never); card lookups and anonymous deck and search reads are
  briefly cached and cleared by any write; signed-in reads are never cached. The admin page shows
  the limits and counts. Requests keep the gateway's own User-Agent; nothing is disguised.

### Changed

- Every tool schema is typed, flat and self-contained (no `$ref`), with one card spelling (`name`,
  `set_code`, `collector_number`, `quantity`, `finish`); older spellings are still read. Wrong
  arguments get a plain "arguments do not fit" refusal. The tool list is smaller.
- Mystic Forge tools are listed with their own fields instead of a `params` wrapper, their texts
  name the gateway tool that owns a hidden job, and their upstream failures are flagged as errors.
- Every result with `ok: false` is marked `isError`.
- The hidden `goldfish_ab` points to `compare_decks`.

### Fixed

- `compare_decks` refuses a reference it can't read instead of treating it as a one-card list.
- A simulation that was asked for and didn't run (`run_deck_report`, `compare_decks` with
  `simulate`) now fails the result (`ok: false`, `simulation_failed`, with the reason) instead of
  reporting success with a failed part inside.
- A card whose name a scan guessed from a misread name is flagged on every proposal that adds it
  (`guessed_from`, `guessed_names`), on the review page and on the in-chat card.
- Pasted lists sum duplicate lines and report what they couldn't read.
- Collection applies in auto mode failed (no Archidekt session in context).
- A deck-details proposal shows the new description.

### Security

- Nobody but the member can see their Archidekt sign-in: the password is used once and never
  stored, only an encrypted session is kept, and no page, log, export or backup shows it.
- Backups blank Archidekt sessions and identity-provider tokens; disabling a member deletes their
  Archidekt session; SQLite `secure_delete` is on and the write-ahead log is flushed after an
  unlink or deletion; secrets no longer appear in the settings' printed form.

## [0.7.2] - 2026-10-07

The rest of what Archidekt's own pages offer for a member's decks, as hand
actions in the app and on the web (none of them is an assistant tool), and
one owner per capability among the assistant's tools.

### Added

- Delete a deck from its More menu: type the deck's name, a snapshot is kept (and the Archidekt
  backup copy when backups are on), the deletion is verified by reading the deck back.
- Deck settings: the cover image (any card of the deck, or Archidekt's automatic pick), the deck's
  tags (Archidekt's public tags, reused or created) and the folder it sits in. A Folders page under
  My decks creates and renames folders. Deck lists and the banner show the cover chosen on Archidekt.
- Editor: maybeboard and sideboard rows are edited like deck rows (count, category) and new cards
  can be added to the maybeboard; a list can be pasted into an existing deck. Proposals take a
  `zone` of `main` or `side` per change; the review shows side rows as such.
- Comments: edit and delete your own, on the deck page.
- Collection: a details menu per row sets finish, condition, language and price paid.
- Precons page: every preconstructed deck Archidekt lists, by set, with a filter; linked from Search
  and the home page.
- `get_deck` and `get_my_deck`: `include_text` adds each card's rules text; every deck read carries
  `archidekt_text`, the deck in Archidekt's import syntax (finishes, categories with their flags,
  labels) so it pastes back as the same deck.
- `deck_stats`: structural `checks` (deck size for the format, commander zone and whether each card
  may command, colour identity violations, singleton violations, uncategorised rows).
- `compare_decks`: names matched by front face and case, a `summary` with cut and added
  percentages and the basic-land changes apart, and `simulate: true` for a paired goldfish A/B of
  the two sides (same seeds game for game, deltas with confidence intervals), with its options.
- `run_deck_report`: `options` passes the simulator's knobs through (annotations, combos, seed,
  until_turn, opponents, mulligan rules).
- docs/CAPABILITIES.md: the capability map, by path (assistant, pages and app) and how they relate.
- The whole card, everywhere: tapping a card on the deck page, a thumbnail in the editor or in the
  collection opens one shared viewer with the image, every face's name, mana cost, type line, rules
  text, power and toughness or loyalty, flavour text, printing, rarity, artist, price, salt score,
  EDHREC rank and the formats the card is legal in. Deck reads carry `type_line`, `power`,
  `toughness` and `loyalty` per card, and `include_text` adds `oracle_text`, `faces`,
  `flavor_text` and `artist`.
- Export page: the deck as Archidekt import text (every row with printing, finish, categories with
  their flags and labels), the plain decklist and the sideboard list, each with a Copy button, and
  a new "Download Archidekt .txt" (`/decks/{id}/export.archidekt.txt`). docs/EXPORT-IMPORT.md says
  what survives in every direction between archidekt.com, the pages, the app and the assistant.
- Import keeps more: `*E*` is the etched finish (it used to be read as foil), `# Sideboard` and
  `// Sideboard` headers and `SB:` rows land in Archidekt's Sideboard (or Maybeboard) category
  instead of being dropped, `^Label^` colour tags are read as labels rather than categories, and
  `#CustomCard` marks are tolerated. `propose_new_deck` rows take `finish` (normal, foil, etched).
- Deck page: **Playtest** shows Archidekt's own playtester on a gateway page (`/decks/{id}/playtest`
  frames archidekt.com's playtester; the app shows it in place), with an Open on Archidekt button
  as the fallback for private decks; **Run simulation** in the banner runs the same statistics,
  validation and 300-game goldfish run as `run_deck_report` and opens the report; **Compare with
  another deck…** (`/decks/{id}/compare`) pits the deck against a preconstructed deck from
  Archidekt's list, any deck link or a pasted list, with the same cards taken out, put in, changed
  counts, basic lands and statistics' differences `compare_decks` reports, each card opening the
  card viewer.
- Clone deck on every deck you can read (public decks and precons included), as on archidekt.com;
  `propose_clone_deck` takes any readable deck. The copy lands in your own account.
- `run_deck_report` and `deck_stats` take a pasted decklist as `deck_ref` as well as a deck: the
  list gets the same validation and simulation (0.7.1's hidden `goldfish_run` could simulate a
  pasted list; its owner keeps that) and comes back with `stored: false`, not filed under History.
- Deck page statistics: **Probability of draw** (at least or exactly N cards of a category, name,
  type, subtype or mana value in the first N cards, the hypergeometric odds Archidekt's Probability
  tab shows) and **Deck checks** (the structural checks `deck_stats` returns, in words).
- `deck_stats` checks for constructed formats: at least 60 cards, at most four copies of a card
  (basic lands and "any number" cards aside), a sideboard of up to 15 (`copy_limit_violations`,
  `sideboard`); the commander-style formats keep their exact size and singleton checks.
- Archidekt's full format list (`deck_format`): every slug Archidekt uses, shown with Archidekt's
  own labels (Standard Brawl, Brawl, Pauper EDH, Duel Commander, Canadian Highlander, PreDH...).
- Deck checks for every Archidekt format (`deck_stats` `checks`, the deck page's Deck checks): card
  legality for the deck's format from Archidekt's own per-card data (`banned`, `not_legal`,
  restricted cards over one copy), so a deck with a banned card is never reported fine; the command
  zone knows Partner, Partner with, Friends forever, Choose a Background and Doctor's companion
  pairs, Oathbreaker's planeswalker plus signature spell, Tiny Leaders' mana value cap and Pauper
  Commander's uncommon leader, and formats without a commander (Canadian Highlander, Gladiator);
  companion rows; the bracket set on the deck against the estimate.
- Undo restores the deck's own details too: a snapshot restore puts back the name, description,
  format, bracket, private and unlisted settings the snapshot recorded (`result.restored_details`),
  besides every card row.
- Export: the gateway's .json is an import format as well (New deck > "a gateway .json export",
  `propose_new_deck(json_text)`), covered by the export → import → compare test with the plain
  .txt, the Archidekt text and the .csv; new one-way downloads Arena .txt, MTGO .dek and a
  printable PDF (written without a PDF library). docs/EXPORT-IMPORT.md lists each with its label.
- Import from a file: the New deck page and the Collection page take a .txt, .csv or .json file
  (read in the browser into the text box; the gateway handles no uploads).
- Collection: **Import a list** adds cards from the page's own Export CSV (a round trip), a CSV
  with Archidekt's collection column names or a plain card list, up to 100 rows at a time.
- The Android app page on a gateway that ships no app file is a plain page saying so and what to
  do instead, not a "not found" error.

### Fixed
- Importing a list into a new deck: an etched row was created as foil (the finish was read but
  not sent); two printings of the same card collapsed into one row; a maybeboard or sideboard
  row made the read-back verification fail. A plain .txt export dropped the commander's category,
  so re-importing it gave a deck with no commander. An export → import → compare test now covers
  the plain .txt, the Archidekt text and the .csv.
- Reports: `has_validation` is true only when the validation passed, and a report whose simulation
  or validation failed is never reused in place of a fresh run.
- Editor: "Keep editing" on the high-risk confirmation now rejects the proposal made for the check
  instead of leaving it pending.
- Readability: the Delete deck item in the dark More menu, the active tab or rail label in the
  light theme and the headings inside menus ("App", "Site theme") meet WCAG AA contrast (4.5:1 or
  better); the phone deck banner no longer runs the owner's avatar under the top bar; the deck
  list's Order by control and buttons line up on folding screens. A test now resolves the colour
  tokens the way each theme does, so a light value left in the dark set cannot come back.
- Compare: "taken out" and "put in" count cards and give each as a share of the deck it left or
  joined (a 5-card list upgraded into a 67-card deck reads 97%, not 1300%); basic lands are listed
  once, under Basic lands, so the list counts match the tiles.
- New deck: when a card or printing in the list does not exist on Archidekt, the review page says
  that (and the result names the card) instead of "No such proposal for your account". The
  read-back after a new deck is created compares printings and finishes too, not only names and
  counts, so a lost foil or a swapped printing fails the verification.
- The card viewer adds a history entry while it is open, so Back (the Android button, or the
  browser's) closes the card instead of leaving the deck page.
- The Install page shows the member's navigation (tab bar, rail, account menu) like every other
  signed-in page; in the Android app it had none.
- Deck checks: Pauper Commander accepts a non-legendary uncommon creature as the commander and no
  longer counts the commander against the commons rule. A row's first category decides whether it
  is in the deck (Archidekt's primary category), so a 60-card Oathbreaker deck whose rows also
  carry excluded categories is 60 cards, not 63.
- Arena export leaves the Maybeboard out (Arena has no maybeboard); the Sideboard still goes.
- Simulations through the gateway never ran against the pinned Mystic Forge: `run_deck_report`,
  `compare_decks simulate: true` and the report's validation sent their arguments flat, while
  Mystic Forge's tools take one `params` object, so every call came back as a validation error
  recorded as a failed block. The gateway now sends `{"params": {...}}`; the unit tests' Mystic
  Forge double takes the same one-model signature so the shape is tested, and the end-to-end suite
  runs a report against the real service. Found by an independent review of the simulation path.
- Mystic Forge's refusals ("Commander '...' was not recognized", "No cards found") were stored as
  successful simulations; a run without its Metrics block (or an A/B without its Deltas) is now
  recorded as failed with that sentence.
- A deck with no commander was simulated with its first card as the commander and a 59-card
  library; such decks (and pasted lists without a Commander line) are now refused with a plain
  reason instead of being simulated wrongly.
- `compare_decks simulate: true` sent a pasted list to the simulator raw, headers and sideboard
  included; it now sends the same rendering the statistics use (commander first, mainboard only).
- The commander zone is Archidekt's premier category whatever it is named, as Archidekt and the
  export text already treated it; statistics and the simulators' text agree with them.
- Archidekt format ids 7 to 10 were mapped to the wrong formats (pioneer, brawl, historic and
  oathbreaker created Custom, Frontier, Future Standard and Penny Dreadful decks and were shown
  under the wrong name). The map is now Archidekt's own, read from its client code.
- The Archidekt export text is sorted by card name (it was sorted by the whole line, so "15x"
  came before "1x").

### Changed

- One tool per job. Mystic Forge's `archidekt_deck`, `archidekt_export`, `archidekt_user_decks`,
  `validate_archidekt_deck`, `precon_diff`, `goldfish_run` and `goldfish_ab` are hidden because
  `get_deck`, `list_my_decks`, `deck_stats`, `compare_decks` and `run_deck_report` do the same job;
  an assistant that calls one is told which tool owns it. The tool descriptions and server
  instructions state the owners; a test pins the list. Each owner carries every input and output
  of the tool it hides (the additions above close the gaps there were), and `run_deck_report`
  still runs the simulation through the research service.

## [0.7.1] - 2026-10-07

### Fixed

- Consent and review pages on a touch screen: the tap that unlocks the page never acts, however
  slowly the browser delivers its click. The guard used to rely on the click arriving within the
  settle time, which a busy device could miss, so a single tap could approve.

## 0.7.0 - 2026-10-07

Archidekt parity for the pages: browse and search public decks, a home page
with the full navigation, your Archidekt Collection shown and edited on the
gateway, a scan flow that ends in the collection, a deck or a new deck,
likes, bookmarks, follows and comments as your own buttons, stacks and grid
views that work on touch screens, and a layout that follows the window
(bottom bar, navigation rail or desktop bar) on the web and in the Android
app. This closes the round that began with 0.6.3: sign-in that survives
real-world token sizes, admins signing in on the admin group alone, profile
pictures, Approve and Reject cards in the chat with per-member approval
modes (0.6.3 to 0.6.6), and now the pages themselves.

### Added

- **Home page.** Signing in lands on a dashboard with the same navigation
  as every other page: your newest decks with their covers, a deck search
  box, tiles for Decks, Search, Scan, Collection, Proposals (with the
  number waiting), History and the Guide, and the
  assistant connection details in a collapsed panel at the end.
- **Deck search and user pages.** `/search` finds public decks on Archidekt
  by name, commander, format, colours and owner, ordered by newest, most
  viewed, largest or bracket, with Archidekt's own paging; `/users/{name}`
  lists one person's public decks. Results open in the gateway's deck view.
  The `search_decks` and `archidekt_user` tools give the assistant the same.
- **Collection.** `/collection` is the list of cards you own: your
  Collection on Archidekt, read and written through the account you linked,
  so what you add here is on archidekt.com at once and nothing about your
  cards is stored on the gateway. Add cards by name or from a scan, count
  copies with plus and minus, filter by name, sort by newest or set release,
  grid or list, export CSV. Owned cards show a green dot on every deck page
  with the copies Archidekt reports. `list_collection` reads it for the
  assistant; `propose_collection_changes` adds or removes cards as a
  proposal (kind `collection`) that waits for your approval or your approval
  mode, like a deck edit. The JSON endpoints under `/collection/api/` give the
  pages the same (each card is one or two Archidekt calls, about a second).
- **Your own edits save at once.** In the app, pressing Save is your
  approval: the deck editor, a drag between categories, New deck, Clone and
  Deck settings go to Archidekt straight away (still recorded as a proposal,
  applied with its snapshot, so History can undo it). Only a big removal
  (more than 10 cards, or 8 different cards) asks you to confirm first; a
  restore keeps its review page. Assistants' changes stay proposals.
- **Navigation.** On a phone the tabs are Decks, Scan, Collection, Proposals
  and More; Search is the magnifier in the top bar. More opens a sheet with
  Home, History, Guide, Account and, for admins, Admin. The rail (unfolded
  foldable, tablet, Android app) shows Search as a sixth tab.
- **Likes, bookmarks, follows and comments.** Every deck page has
  Archidekt's social buttons (Like with the deck's score, Bookmark, Follow
  the owner, a Comments panel with the thread and a reply box) and user
  pages have Follow. Each asks for a confirmation, then goes to Archidekt
  under your own name. They are browser-only on purpose: no tool and no
  `/api/v1` route exists for them, so an assistant can never like, follow or
  comment for you.
- **Scan flow.** The scan page explains its three steps on first use, and
  the list ends in **What next?**: save the cards to your collection, add
  them to one of your decks (the deck editor opens with them filled in), or
  start a new deck from them (`/decks/new?scan_session=`). "Save to gateway"
  is now "Save scan". A scan is a draft kept until you send its cards to the
  collection or a deck (which removes it) or delete it; nothing expires by
  age.
- **Guide.** `/guide` is end-user documentation inside the app: what you
  can do on each page with or without an assistant, how proposals and
  snapshots protect your decks, the Android app, privacy. Linked from the
  account menu, the home page and the footer.
- **Touch and card viewer.** On touch screens a tap fans a stack out and a
  tap on a card opens it large with its actions (edit, move to another
  category, mark as owned, Scryfall). On your own deck, cards can be dragged
  between categories with a mouse or by press-and-hold on a touch screen; the
  moves become one proposal you review.
- **Adaptive navigation.** The layout follows Android's window size
  classes on the web and in the app alike: under 600 px wide the bottom tab
  bar; from 600 to 900 px on a touch screen (an unfolded foldable or a
  tablet in a browser), and in the Android app at every width from 600 px,
  a navigation rail down the left edge; wider browsers keep the desktop top
  bar. The rules read the live window, so folding, rotating, split screen,
  pop-up windows and a browser's "Desktop site" switch re-flow at once.
- **Android app layout.** Pages rendered for the app (its user agent carries
  `MTGAssistant/`) drop the website footer and respect the display cut-out
  and gesture bar. The app's own actions (scan with the phone camera,
  reload, open in browser, change gateway) moved into the page's account
  menu; the floating button now appears only over pages that are not the
  gateway's, so it no longer covers the bottom tab bar.

### Fixed

- **View as / Group by / Sort by did nothing** on the deck page: the Quick
  add form sat inside the view form, so browsers closed the outer form early
  and the selects submitted nothing. They are separate forms now and apply
  as soon as you change them.
- The deck page's **More** menu was clipped by the banner (which hid its
  overflow for the art). The art is painted by a layer behind the banner
  now, and the menu opens over the toolbar.
- Dropdown menus (the account menu, a deck's More menu) close on a tap or
  click anywhere outside them, on Escape, and on the Back button on phones.
- Touch screens up to 900 px wide (an unfolded foldable in a browser) use the
  phone layout with the bottom tab bar instead of a squeezed desktop bar.

### Changed

- The navigation is Decks, Search, Collection, Scan, Proposals, History (and
  Admin for admins); the phone tab bar and the rail are Decks, Search, Scan,
  Collection, More (More opens the home page with every section).
- A member with a linked Archidekt account now reads every deck page with
  their own session (before, public decks were read anonymously), which is
  how Archidekt reports the copies they own, their like and their bookmark.
## [0.6.6] - 2026-10-07

### Added

- The account icon shows each member's profile picture from the identity
  provider's `picture` claim: a picture uploaded to their Authentik
  profile (embedded in the claim) or their Gravatar, which the gateway
  fetches itself so browsers never contact Gravatar. Without one it shows
  their initials. Only PNG, JPEG, GIF and WebP are kept (checked by their
  bytes, never SVG), only Gravatar addresses are fetched, and *Delete my
  data* removes the stored copy. See
  [docs/IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md#profile-pictures).
- Uploaded Authentik pictures on Authentik 2026.8.3 and newer, which leave
  them out of the `picture` claim: an optional scope mapping, printed in the
  guide, puts a 16-character fingerprint in tokens instead, and the gateway
  asks userinfo for the image itself only when the fingerprint changes.
- The end-to-end tests run against Authentik 2026.8.3, the current release,
  and install that mapping exactly as the guide prints it. The Authentik
  guide now covers two things 2026.8 needs: the provider's **Grant Types**
  must include Authorization Code and Refresh token, and the reverse proxy
  must be in Authentik's trusted proxy ranges (the defaults cover the usual
  private Docker networks).

### Fixed

- Signed-in pages kept answering *The sign-in service can't be reached to
  confirm your access* a few seconds after sign-in, with the log line
  `identity provider answered userinfo with HTTP 400; the access token is …
  bytes`. Authentik 2026.8.0 to 2026.8.2 embed an attribute-based avatar
  in the `picture` claim and copy the claims into the access token, which
  then grew past a megabyte, and the reverse proxy refused it as a request
  header. The live membership check now sends any access token over 7 KB
  in a form-encoded POST body to userinfo (RFC 6750 section 2.2, which
  Authentik accepts), so it works through Nginx Proxy Manager. It still
  asks on every check, exactly as before. Trimming the token is still
  worth doing: see the new row in
  [docs/IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md#10-troubleshooting).

### Changed

- Members of `MTG_ADMIN_GROUP` may sign in without also being in
  `MTG_REQUIRED_GROUP`. The membership check reads both groups live, so
  someone taken out of the admin group, and not in the users group, is cut
  off on their next request.
- A userinfo connection that drops before any answer is tried once more
  straight away instead of answering *Try again shortly*. Removal is still
  seen on the next request; nothing is cached longer.

## [0.6.5] - 2026-10-07

### Added

- Approval modes. Each member now picks, on their own Account page, how
  much their assistant may change on Archidekt without asking: **Ask me
  every time** (`manual`, the default on a fresh install), **Apply small,
  low-risk edits without asking** (`semi`) or **Apply every change without
  asking** (`auto`). The choice is the member's alone: it covers only their
  account, their decks and the apps they connected, it can be set only in
  their browser (never over MCP or the API, so a tricked assistant cannot
  loosen it), and every change of it is in the audit log. A proposal's
  risk is judged from its stored review rows: low means an edit of at most
  five rows (card adds, removes, quantity, category, finish or printing
  changes of at most four copies each, never the commander) or a clone; everything else (bigger edits,
  the commander, a new deck, a restore, deck details) is high. Every
  proposal result now says `approval_mode`, `risk`, `risk_reason` and
  `assistant_may_apply`, and `next_step` tells the assistant whether to call
  `apply_proposal` or leave the decision to the member. Every apply still
  snapshots the deck first, so an auto-applied change can be undone from
  History. The Account page states the trade-off next to the choices.
- `MTG_APPROVAL_MODE_DEFAULT` (the mode of members who have not chosen;
  `manual`), `MTG_APPROVAL_MODE_MAX` (the highest mode members may choose;
  `auto` = no cap) and `MTG_AUTO_APPLY_MAX_ROWS` (the row limit of a
  low-risk edit; `5`). The admin overview shows the first two.
- The in-chat card shows a big change as a summary first: rows grouped by
  what they do (added, removed, quantity, moved, finish, printing,
  commander), the first eight rows with their pictures, and a **Show all**
  button for the rest. It also shows the proposal's risk and, in the semi
  and auto modes, who applies it.

### Changed

- `MTG_APPLY_VIA_MCP` and `MTG_APPLY_MIN_AGE_SECONDS` are gone; the
  approval modes replace them. A gateway that still sets them simply
  ignores them. The assistant's `apply_proposal` now applies only what the
  member's mode allows and answers `browser_required` otherwise; the
  `apply_too_soon` error no longer exists. The REST apply route follows the
  same rule for bearer tokens.
- The review page shows each proposal's risk tier and why.

## [0.6.4] - 2026-10-07

### Added

- Approve or reject a proposed deck change on a card inside the chat. Every
  proposal tool now returns the proposal as an MCP App (the
  `io.modelcontextprotocol/ui` extension): a client that renders MCP Apps
  (Claude on the web, desktop, iOS and Android) shows the proposal as a card
  with the change summary, small card images from Scryfall, and **Approve**
  and **Reject** buttons. The gateway applies the change only when the
  button is pressed: the buttons call a tool that is marked for the app only
  (not for the model) and that needs a one-time approval code the gateway
  puts in the tool result's `_meta`, where the assistant never sees it. An
  assistant that tries to approve its own proposal gets `invalid_approval`
  and the attempt is logged. In Claude Code, which does not render apps,
  `apply_proposal` instead asks the client to open the browser review page
  and waits for your decision there. Everywhere else the card or the
  assistant shows a link to the review page, as before. Turn the card and
  the in-chat buttons off with `MTG_APPLY_IN_CHAT=false`.
- `confirm_proposal` tool (app-only; needs the `mtg.write` scope) and the
  `browser_pending`, `invalid_approval` and `in_chat_disabled` errors
  ([docs/API.md](docs/API.md)).

### Changed

- `MTG_APPLY_VIA_MCP` keeps its default of `false`. The review page and the
  card are the two ways to apply a change; leaving this on lets a tricked
  assistant apply its own proposal, so the docs now say so plainly.

## [0.6.2] - 2026-10-07

### Fixed

- Sign-in failed with *Sign-in could not be completed with the identity
  provider* and the log line `ID token validation failed:
  ExceededSizeError` when the provider's ID token was bigger than the
  token library's built-in limits (a 512-byte header, a 1 KB signature,
  about 96 KB of claims). The gateway now allows up to 16 KB of header,
  4 KB of signature and 2 MB of claims, which fits big group lists,
  avatar claims and certificate chains. The token still has to come from
  the provider's own token endpoint and pass every other check.
- A token that big usually means a scope mapping adds far more than the
  gateway needs, and Authentik puts the same claims in its access token,
  which the gateway sends to Authentik's userinfo endpoint for the group
  check. So when a sign-in brings an ID or access token over 16 KB, the log
  now warns `identity provider issued unusually large tokens` and lists the
  five biggest claims by name and size (never their values). If userinfo
  then refuses the oversized request (HTTP 400, 413 or 431), the log says
  so.

### Changed

- That sign-in failure page now answers HTTP 500 instead of 502. The
  Android app treats 502-504 as the reverse proxy's "gateway is down" page
  and covered the real message with *Your gateway is not answering right
  now*.
- When sign-in fails while the gateway is collecting your identity
  provider's answer, the page now says which setting to check instead of
  only "Try again": the client ID or secret, the sign-in code (usually
  just try again, otherwise the redirect URI), the ID token (signing key,
  encryption key, client ID or clocks), or reaching the provider. The page
  shows no part of the provider's answer and no secret.
- The log line `identity provider exchange failed` now includes the
  provider's error code (for example `HTTP 401, invalid_client`), says
  outright when the ID token is signed with HS256 (Authentik does that
  when the provider has no Signing Key), and says why the ID token was
  refused (for example `Invalid claim: 'aud'`, or `The token is expired`
  when the clocks differ).
- Troubleshooting tables in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
  and [docs/IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md) list each new message.

Nothing to do when upgrading from 0.6.1.

## [0.6.1] - 2026-10-06

A fourth security pass. The headline: taking someone out of the group at
the identity provider now cuts them off on their next request, not at their
next sign-in.

### Upgrading from 0.6.0

1. **Authentik: add the `offline_access` scope mapping.** Open the provider
   (**Applications → Providers → your provider → Edit → Advanced protocol
   settings → Scopes**) and add `authentik default OAuth Mapping: OpenID
   'offline_access'` next to the `openid`, `email` and `profile` ones. Without
   it Authentik quietly issues no refresh token, and every member is asked to
   sign in again each time Authentik's access token runs out (an hour by
   default). Other providers: see
   [docs/IDP-OTHERS.md](docs/IDP-OTHERS.md#about-the-group-check).
2. **Everyone signs in once more.** Sessions and assistant connections from
   before the upgrade have no identity-provider tokens on file, so the first
   request after the upgrade signs the person out. Each member signs in to
   the web pages and the Android app again, and each connected AI app asks
   to reconnect once.
3. `MTG_OIDC_SCOPES` now defaults to `openid profile email offline_access`.
   If you set it yourself (the 0.6.0 Compose `.env.example` did, as
   `openid profile email`), add `offline_access` to it; the value is used
   exactly as set. The gateway logs a warning when the provider issues no
   refresh token. Use the new `deploy/compose/docker-compose.yml` too: the
   0.6.0 one fell back to `openid profile email` when `.env` didn't set the
   scopes. The new stack and Compose files also pass
   `MTG_OIDC_PREVIOUS_ISSUERS`, `MTG_MEMBERSHIP_CHECK_TTL` and
   `MTG_ARCHIDEKT_CALLS_PER_10_MIN` through.
4. The example env files now ship `MTG_APPLY_VIA_MCP=false`. Your own
   deployment keeps whatever it sets; nothing changes unless you change it.
   `false` means a change is applied only by the member's own click on the
   review page. `true` lets the assistant apply after a yes in chat, which is
   weaker: text the assistant reads (a deck description, a card note) could
   trick it into applying its own proposal.
5. The database is upgraded on start (schema 10). Take a backup first, as for
   every release; the 0.6.0 image refuses to start on the upgraded file.

### Security

- **Live membership.** Before serving any request that carries a browser
  session or a bearer token, the gateway asks the identity provider's
  userinfo endpoint whether the person is still allowed in (cached for
  `MTG_MEMBERSHIP_CHECK_TTL` seconds, 5 by default). Removal from
  `MTG_REQUIRED_GROUP` or `MTG_ADMIN_GROUP`, or deactivating or deleting the
  account at the provider, takes effect on the person's next request. Before,
  it took effect at their next sign-in, up to a week later for assistants.
  Tested live against Authentik 2026.2.2, which on its own keeps honouring a
  removed member's refresh token.
- A member removed from `MTG_REQUIRED_GROUP` loses every gateway token,
  browser session, stored identity-provider token and their Archidekt link
  at once. A member the provider no longer vouches for (deactivated or
  deleted, which the provider reports only as a refused token) loses every
  token and session too; their Archidekt link stays until an admin deletes
  their data, so a provider hiccup can't unlink everyone.
- If the identity provider can't be reached, or refuses the gateway itself
  (a wrong client secret, a refused scope), requests are refused with 503
  and nothing is revoked (fail closed). Service comes back with the provider.
- When userinfo carries no groups, they are read from a freshly refreshed ID
  token. When neither has them, the person is signed out and a warning names
  `MTG_OIDC_GROUPS_CLAIM` (fail closed), instead of being let in. With
  `MTG_ALLOW_ANY_IDP_USER=true` and no `MTG_REQUIRED_GROUP` there is no group
  to leave, so a provider that sends no groups at all (Google, Entra ID)
  keeps working; a disabled or deleted account is still cut off.
- A request carrying both a browser session and a bearer token is checked
  for both people.
- Code exchanges and token refreshes at `/token` check membership the same
  way. A leftover code or refresh token of someone who deleted their data
  can't mint new tokens.
- The identity provider's access and refresh tokens are stored encrypted
  with the `mtg_fernet_key` secret.
- Each member is pinned to the identity provider (issuer) they first signed
  in with. An account from a different provider with the same subject is
  refused instead of inheriting the old member's decks, apps and Archidekt
  link; an admin can delete the old account's data. After moving the
  provider to a new address, list the old one in `MTG_OIDC_PREVIOUS_ISSUERS`.
- An ID token issued to several audiences must name the gateway as its
  authorized party (`azp`).
- **Sign out on all my devices** on the sign-out page (`/logout`) ends every
  browser and Android app session. For an hour after signing out, a sign-in to the
  gateway's pages on that device asks the identity provider for the password
  again (`prompt=login`), so the next person on a shared phone isn't signed
  straight back in.
- `Strict-Transport-Security` (one year, no `includeSubDomains`) on every
  response when `MTG_PUBLIC_URL` is https. The example proxy configs set the
  same header.
- Pages forbid framing and base-URL changes in their CSP (`frame-ancestors
  'none'`, `base-uri 'none'`), the offline page too.
- Sign-in floods: each public address may start 30 sign-ins a minute (private
  addresses, such as an untrusted reverse proxy's, are not limited, and a
  warning suggests `MTG_TRUSTED_PROXIES` when many browsers share one), and unfinished
  sign-ins are capped at 10 per browser and 500 per AI client. When a cap is
  hit, the network holding the most unfinished sign-ins loses its oldest, so
  a flood only pushes out its own.
- Client ID Metadata Documents: an address with a query string is refused; a
  failed fetch (or a name whose DNS doesn't answer in 2 seconds) blocks new
  addresses on that host for a minute; new addresses
  are fetched at most 10 times a minute per site and 60 in all, counting only
  requests actually sent; a document is capped like a registered client (20
  redirect URIs, 2000 characters each, 8 KB); the cache keeps at most 1000
  documents, 50 per site. Apps a member has signed in with are never
  throttled or evicted, are fetched in a lane of their own (fetch slots and
  DNS threads) that junk addresses can't fill, and keep their last good
  document (up to a week past its expiry) while their server can't be
  reached or answers 5xx, 408 or 429 (never after the server withdraws it
  or serves one that isn't accepted).
- Client ID Metadata Documents are checked more strictly: `grant_types` and
  `response_types` must be lists of strings, `scope` must use the OAuth
  scope characters, malformed optional links (`client_uri`, `logo_uri` and
  the like) are left out, and a document the gateway couldn't turn into a
  client is refused instead of causing server errors later. A copy cached
  by an earlier version that no longer passes is discarded and fetched
  afresh.
- Archidekt: each member may start `MTG_ARCHIDEKT_CALLS_PER_10_MIN` (120)
  Archidekt calls per 10 minutes, the research tools' `archidekt_*` calls
  included. Five failed Archidekt link attempts in 15 minutes block further
  attempts for a while, parallel attempts included.
- Research tools: oversized arguments (goldfish games, turns and odds sizes,
  text over 200 kB) are refused before anything is forwarded.
- Read-only connections: a token whose scopes are all read-only (`mtg.read`
  or `read`; `read write` keeps full access) can
  read but not propose, apply, reject, run reports or save scans.
- Proposals record the app that made them and show it on the review page.
  Disconnecting an app rejects its pending proposals. One app may hold 30
  pending proposals per member (a member 100 in all), so one app can't use
  up every slot.
- A new-deck proposal is tied to the Archidekt account linked when it was
  made; applying it after linking a different account is refused
  (`other_account`).
- An app can delete through the API only the reports it ran itself.
- An Archidekt session refresh that races an unlink or a relink no longer
  overwrites or revokes the newer link.
- A deck apply or report still running when the member deleted their data
  stores nothing for the deleted account. **Delete my data** removes the
  member's own deck covers too, never another member's.
- A deck settings change re-reads the deck after the backup copy and refuses
  if its details changed meanwhile.
- Deeply nested JSON gets a 400 instead of an unhandled error.
- Android app: the consent page's **Deny** (which goes back to the
  application's own site) always opens in the browser. The app learns the
  sign-in service from the gateway itself (`/.well-known/mtg-gateway`, or an
  older gateway's `/login` redirect), so **Approve** stays in the app. A page
  from any other site that loads without the app being asked is stopped and
  opened in the browser, at most once every 10 seconds (after that it is
  unloaded), a page can open the browser without a tap at most once every 3
  seconds, and Back no longer
  returns to the sign-in service's old pages after signing in. Sign-ins that
  pass through a second site with a form (SAML, brokered logins) open in the
  browser; use such gateways in the phone's browser.
- Compose: each service may run at most 512 processes and threads. CI checks
  the Gradle wrapper before any Android build.

### Added

- `GET /.well-known/mtg-gateway` (public): names the sign-in service's
  origin, for the Android app.
- `MTG_MEMBERSHIP_CHECK_TTL` (seconds, 0 to 60, default 5; 0 asks the
  provider on every request) and `MTG_ARCHIDEKT_CALLS_PER_10_MIN` (10 to
  100000, default 120).
- Admin page: **Delete data** for another member (a former member, or an
  account from an earlier identity provider). It needs a confirmation tick
  and can't be used on yourself.
- Admin actions on a member's account show up in that member's own activity
  log as done by an administrator.
- The Account page shows each connected app's client id next to its name,
  so two apps with the same name can be told apart.

### Changed

- The example env files ship `MTG_APPLY_VIA_MCP=false` (see Upgrading).
- Moving a card into a category the deck doesn't count (Maybeboard,
  Sideboard) is shown as "leaves the deck" and counted as a removal.
- Applied proposals are deleted a year after they were applied; closed ones
  still go after 30 days.
- The "data deleted" page and the Account page say that nightly database
  backups keep a copy until they age out (`MTG_BACKUP_KEEP_DAYS`).

## [0.6.0] - 2026-10-06

A production-readiness pass: access checks, failure handling, the database,
monitoring, testing on different screens and connections, and what people
see when something goes wrong. The findings and their sources are summarised
in the release notes' pull request.

### Upgrading from 0.5.0

1. Paste the new `deploy/portainer-stack.yml` (or `deploy/compose/docker-compose.yml`)
   and update the stack. It adds two settings: `stop_grace_period: 120s`, so a
   redeploy lets a running deck apply finish, and a `logging:` block that caps
   each container's logs at three 10 MB files. Nothing in your environment
   variables changes.
2. The database is upgraded on start (schema 6: new indexes only). Take a
   backup first, as for every release; the 0.5.0 image refuses to start on the
   upgraded file.

### Security

- `/mcp` now re-checks `MTG_REQUIRED_GROUP` on every request, like the web
  pages and the JSON API already did. Before, an assistant kept working until
  its access token expired (up to an hour) after the member's recorded groups
  lost the group.
- `/healthz` no longer puts the database error text in its public reply; the
  detail goes to the log.

### Added

- **Delete my data** on the Account page: one checkbox and one button delete
  everything the gateway keeps for you (proposals, snapshots, reports, scan
  sessions, the Archidekt link, connected apps, usage counters and the sign-in)
  and sign you out. Decks on Archidekt are not touched.
- A **System** card on the admin page: version, database schema and size, the
  newest backup, and the last backup's failure if it failed.
- A plain "You're offline" page when a gateway page can't be reached, instead
  of the browser's own error screen. The service worker builds it on the spot
  and never caches a page.
- Android app: if the page's renderer crashes (it can happen under memory
  pressure during a scan) the app rebuilds the page instead of closing; no
  connection and a restarting gateway (502/503/504) get their own plain
  message with Retry; and a camera permission that was denied for good opens
  the app's settings with an explanation.

### Changed

- Friendly error pages: an unknown address or an unexpected error now shows
  a styled page that says what happened and what to do next (JSON on
  `/api/`, `/mcp` and the OAuth endpoints), never a bare "Not Found" or
  "Internal Server Error". Unexpected errors are logged with their traceback
  and counted on the admin page.
- Every button shows that it was pressed at once, and a button that sends a
  form shows a busy state until the next page arrives; a second press of the
  same form while it is sending is ignored.
- Button and badge text now meets WCAG AA contrast: dark text on the orange
  and green buttons and badges, and a deeper red for danger buttons. White on
  the old orange was 2.4:1.
- Long button labels wrap instead of pushing the Account and Install pages
  wider than a 320 px phone screen; the deck editor's bottom bar keeps its
  side margins on phones.
- The scan page says "no connection to the gateway" instead of "Failed to
  fetch".
- Scan results split piles of more than 99 copies and add `change_batches`
  when there are more than 40 changes, so they fit `propose_deck_changes`.
- `deck_stats` and `compare_decks` accept the snapshot ids `list_snapshots`
  returns (they only recognised a `snap_` prefix that real ids don't have).

### Fixed

- A deck apply cut off by a crash or redeploy was left "applying" for up to an
  hour (or until the next nightly cleanup); it is now marked failed as soon
  as the gateway starts, pointing at the snapshot taken before it. The
  gateway also lets running requests finish for up to 100 s when it is
  stopped.
- The cleanup of expired sign-ins, tokens and proposals ran only with the
  nightly backup (so never without `MTG_BACKUP_DIR`, and not at all after a
  failed backup). It now runs hourly on its own.
- Deck snapshots were never deleted. The newest 25 of each deck are kept,
  plus any a pending restore needs. Usage counters are kept for 400 days and
  remembered deck covers for 180.
- Nightly backups are read through their own connection (requests no longer
  wait for the copy), written as standalone files, and checked with SQLite's
  `quick_check` before they are kept.
- A Mystic Forge that hangs no longer holds up the assistant's tool list for
  up to five minutes: the listing gives up after 10 seconds and is retried a
  minute later.
- A damaged database file at start gives a one-line message pointing at the
  backups instead of a Python traceback.
- Indexes for the cleanup and snapshot lists; an explicit SQLite busy timeout;
  each database upgrade step commits together with its version number.
- Outbound request URLs are logged only at `DEBUG`.

## [0.5.0] - 2026-10-06

The first release from this repository, as **MTG Assistant Gateway**. It has the
features of 0.2.0 below, under new names, with a new release process. Versions
below 1.0.0 are still in testing: expect fixes and small changes between them.
1.0.0 will be the first version declared ready.

### Upgrading from 0.2.0

1. **New names.** The images are now `ghcr.io/<owner>/mtg-assistant-gateway`
   and `ghcr.io/<owner>/mtg-assistant-mysticforge` (tag `1.3.2-mag2`). In the
   Swarm stack the services are `mtg-assistant-gateway` and
   `mtg-assistant-mysticforge`, so your reverse proxy forwards to
   `<stack>_mtg-assistant-gateway:8080`. Paste the new
   `deploy/portainer-stack.yml`, set `MTG_IMAGE` and `MF_IMAGE` (or leave
   them unset for the defaults), and change the proxy's forward host.
   Your data folder, secrets, network and identity provider settings stay
   the same, and the database needs no upgrade.
2. **The Android app** is now "MTG Assistant Gateway" with the package ID
   `local.mtgassistantgateway.app`, so it installs as a new app: uninstall
   the old one first. On the gateway it is served at
   `/app/mtg-assistant-gateway.apk`.

### Changed

- Renamed to MTG Assistant Gateway everywhere: pages,
  assistants, images, Swarm services, the Android app and the docs.
- Every update to `main` is released: CI checks that `VERSION` went up,
  creates the tag `v<version>`, publishes the image as the version,
  `<major>.<minor>` and `latest`, and creates the read-only branch `release/<version>`
  and a GitHub release ([docs/VERSIONS.md](docs/VERSIONS.md)). The example
  settings now follow `latest`; pin a version to stay on it.
- The Mystic Forge image is built and published automatically the first
  time `main` names a tag that isn't published yet.
- Android app: the scan panel uses CameraX, lays itself out around the
  hinge on a half-folded phone (tabletop and book postures, Jetpack
  WindowManager), opens gateway links shared to it, and has an App Links
  flavor for operators who build it for one gateway (`APP_LINKS_HOST`,
  `ANDROID_APP_LINKS_HOST`, `MTG_ANDROID_ASSETLINKS`). It takes its version
  from `VERSION` like everything else and is built with Gradle and the Android
  SDK (`android/scripts/build.sh` fetches the SDK; Android Studio opens the
  project directly). The release APK is shrunk to about 2 MB.

## 0.2.0 - 2026-10-05

Released before this repository existed, under an earlier name; there is no
`v0.2.0` tag here.

### Upgrading from 0.1.0

Read this before you deploy 0.2.0:

1. **`MTG_REQUIRED_GROUP` is required.** With it empty the gateway stops at
   start-up with `configuration error: MTG_REQUIRED_GROUP is empty`. Set it to
   your identity provider's group, or set `MTG_ALLOW_ANY_IDP_USER=true` if your
   provider already admits nobody else.
2. **Take a backup first.** The database is upgraded in place on the first
   start (schema version 5). Going back to 0.1.0 means restoring that backup,
   not running the old image on the upgraded file: 0.1.0 doesn't know about
   accounts disabled on the admin page and would let them in again.
3. **Everyone is signed out of the web pages once.** The cookies are now
   `__Host-` cookies. Sign-ins that are half-way through at the moment of the
   upgrade fail and have to be started again. Connected assistants keep working
   as long as the groups recorded at each person's last sign-in contain
   `MTG_REQUIRED_GROUP`; otherwise they are asked to sign in again.
4. **Swarm:** the stack's default network name is now `mtg-gateway`. If you
   deployed with the old default, set `MTG_NETWORK_NAME` to your network's exact
   name. Mystic Forge now sits on a private network of its own.
5. New optional settings worth a look: `MTG_ADMIN_GROUP` (admin page),
   `MTG_OIDC_GROUPS_CLAIM` and `MTG_OIDC_TOKEN_AUTH_METHOD` (identity providers
   other than Authentik), `MTG_APP_DIR` and `MTG_ANDROID_ASSETLINKS` (Android
   app). The Compose and Swarm files pass the first three through.

### Added

- Deck statistics with a Commander bracket estimate, stored deck reports with
  history, and deck comparison.
- Companion pages for phones and desktops: your decks (`/decks`), a deck view
  and editor that turns edits into a proposal you apply, history (`/history`)
  and activity (`/activity`).
- An admin page for members of `MTG_ADMIN_GROUP`: users and their activity,
  usage metrics, and disable, enable, revoke and unlink actions
  ([OPERATIONS.md](docs/OPERATIONS.md#the-admin-page)).
- A JSON API under `/api/v1` ([docs/API.md](docs/API.md)).
- More kinds of proposals: deck details, set a card's category, set the
  commander, change a card's finish or printing, clone a deck
  (`propose_clone_deck`); proposals can be rejected; a saved scan session can
  be the input.
- The pages now look and work like Archidekt's: the same top bar, bottom tab
  bar on phones, deck banner with art, toolbar with text / stacks / grid
  views, group and sort choices and a local filter, deck stats panel, deck
  grid with cover art and colour bar, Light / Dark / System theme in the
  account menu. New pages: New deck, Deck settings, Clone deck, CSV export;
  the editor gained numeric quantities, a printing picker, finishes, new
  categories by typing and Undo. The Android app follows the phone's Light /
  Dark setting with the same palette.
- Linked Archidekt sessions refresh themselves, so people relink far less often.
  A refresh never undoes an unlink.
- Sign-in reads groups from a configurable claim (`MTG_OIDC_GROUPS_CLAIM`, for
  example Keycloak's `realm_access.roles` or Zitadel's roles object) and can send
  the client secret with HTTP Basic (`MTG_OIDC_TOKEN_AUTH_METHOD`).
- The MTG Assistant Gateway Android app, version 0.2.0 like the gateway ([docs/ANDROID.md](docs/ANDROID.md), [android/](android/)):
  the gateway's pages full screen, pointed at any gateway at first launch, plus a
  phone-camera scan screen with torch brightness, zoom, exposure and a continuous
  Auto mode that hands each photo to the `/scan` page. Signed-in users download it
  from their own gateway at `/app` (new optional `MTG_APP_DIR`), and tagged
  releases attach it to the GitHub release when the `ANDROID_*` repository
  secrets are set. No app store involved.
- Plain Docker Compose deployment for a single machine
  ([docs/DEPLOY-COMPOSE.md](docs/DEPLOY-COMPOSE.md),
  [deploy/compose/](deploy/compose/)), alongside the Swarm and Portainer stack.
- Reverse proxy examples for Caddy, Traefik, nginx and Nginx Proxy Manager
  ([docs/REVERSE-PROXY.md](docs/REVERSE-PROXY.md)).
- Notes for identity providers other than Authentik
  ([docs/IDP-OTHERS.md](docs/IDP-OTHERS.md)).
- An architecture overview with a diagram ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))
  and a troubleshooting guide ([docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)).
- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue and pull
  request templates, and this changelog.

### Changed

- The Swarm stack's default network name is now `mtg-gateway`. If you
  deployed with the old default, set `MTG_NETWORK_NAME` to your existing
  network's exact name.
- Every AI client connection, including the weekly re-sign-in, now goes
  through the gateway's own Approve/Deny page, and a CSV import can no
  longer stall the gateway.
- Every page except the machine endpoints needs a signed-in member of
  `MTG_REQUIRED_GROUP`.
- One-click registry setup for private images in Portainer, and the proxy's
  forward host spelled out; clearer network, image pull and storage
  steps.

### Security

- A sign-in can only be approved and finished in the browser that started it.
  Before, a crafted link could make a signed-in member's browser finish a
  sign-in someone else had approved, giving them access to that member's
  account. Login and session cookies are now `__Host-` cookies, so everyone
  is signed out of the web pages once after upgrading.
- `MTG_REQUIRED_GROUP` is required: the gateway refuses to start with it
  empty unless `MTG_ALLOW_ANY_IDP_USER=true`.
- A member refused at sign-in for no longer being in the group loses their
  connected apps and web sessions at once. The weekly re-sign-in now counts
  per connected app, and a replayed authorization code revokes the tokens it
  produced.
- Authorization errors show a page instead of redirecting to the app's
  address; registration, token and CIMD requests have size, concurrency and
  timeout limits; old audit rows and unused registrations are pruned.
- Deck changes: categories and new deck names are cleaned and shown on the
  review page; only the app that proposed a change can apply it over MCP (the
  review page still can); the deck is re-checked right before writing; a
  failed apply records how far it got; Archidekt's `Retry-After` is honoured.
- Web pages: sign-out needs the form token, notices only show fixed texts,
  responses are `nosniff` and per-user data is not cached; scan lookups and
  research calls have per-person limits; request bodies are capped while
  being read; on-demand backups are owner-only (0600).
- Deploy: both containers drop every Linux capability except the four the
  start-up script needs and run with no-new-privileges; Mystic Forge is on a
  private network only the gateway can reach; docs cover an encrypted
  overlay network, HSTS, proxy body limits, trusted proxies, MFA and
  restoring a backup safely. CI jobs no longer get package write access or
  keep the checkout token.
- From this release on, a gateway refuses to open a database written by a
  newer version instead of ignoring what it doesn't know (0.1.0 has no such
  check, so going back to it still means restoring a backup). A token whose
  user record is gone is refused, also when no group is required.
- Final review of the release candidate: the Approve/Deny and Apply buttons
  unlock a moment after the page is shown and the person moves, touches or
  types (against double-click window swaps); anonymous sign-in and
  registration floods are capped; disconnecting one app no longer signs you
  out of the web pages; proposals are capped per person and their review rows
  can't be forged by card or category text; deck reports count against the
  per-person research limit; one person can't monopolise Archidekt or the art
  budget. The Android app only lets the gateway's own pages use its bridge,
  downloads and file picker, learns one sign-in site per sign-in, sends
  sign-ins that hop to a further site (for example "Sign in with Discord")
  to the browser, and excludes its data from backups and device transfer.
  Release signing runs only on version tags (and manual runs from main) in a
  protected environment, and /app shows the signing certificate's fingerprint.

## 0.1.0 - 2026-10-05

First release, before this repository existed.

- MCP gateway with its own OAuth 2.1 server for Claude and ChatGPT
  (automatic client registration and Client ID Metadata Documents, PKCE,
  rotating refresh tokens, a consent page), with sign-in handed off to an
  OpenID Connect identity provider.
- Research and goldfish tools from an internal Mystic Forge service,
  allowlisted and run as the signed-in user.
- Deck import from Archidekt links, pasted lists and Archidekt CSV exports.
- Archidekt account linking; deck edits and new decks as proposals the user
  approves, with snapshots, Archidekt backup copies before every edit, and
  faithful restore.
- The `/scan` page: on-device card recognition with printing and foil
  detection and artwork matching.
- A one-link install page and assistant plugin, and the MTG skill.
- Docker Swarm / Portainer stack, nightly database backups, audit log.

[0.7.30]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.30
[0.7.26]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.26
[0.7.25]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.25
[0.7.24]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.24
[0.7.23]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.23
[0.7.22]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.22
[0.7.21]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.21
[0.7.20]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.20
[0.7.19]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.19
[0.7.18]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.18
[0.7.16]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.16
[0.7.15]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.15
[0.7.14]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.14
[0.7.13]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.13
[0.7.12]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.12
[0.7.11]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.11
[0.7.10]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.10
[0.7.9]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.9
[0.7.8]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.8
[0.7.7]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.7
[0.7.6]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.6
[0.7.5]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.5
[0.7.4]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.4
[0.7.3]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.3
[0.7.2]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.2
[0.7.1]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.7.1
[0.6.6]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.6.6
[0.6.5]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.6.5
[0.6.4]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.6.4
[0.6.2]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.6.2
[0.6.1]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.6.1
[0.6.0]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.6.0
[0.5.0]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.5.0
