# Getting started with the MTG Assistant Gateway

The MTG Assistant Gateway hooks your AI assistant (Claude or ChatGPT) up to Magic
data. It can look up cards, rules, prices, combos and EDHREC stats, goldfish
your deck to see how it plays, and edit your own Archidekt decks once you've
okayed each change. All you need is a browser and the AI app you already
use. Nothing to install or run.

It takes about ten minutes:

1. The owner gives you an account ([section 1](#1-getting-access)).
2. You connect Claude or ChatGPT ([section 2](#2-connect-your-ai-assistant)).
3. You link your Archidekt account ([section 3](#3-link-your-archidekt-account)).
4. Optional: you add the MTG skill so the assistant sticks to the safe way
   of doing things without being reminded ([section 4](#4-install-the-mtg-skill-optional-recommended)).

Wherever you see `mtg.example.com` below, use the hostname the owner gave
you.

## 1. Getting access

### For the person joining

Ask the owner for access. You'll get:

- the gateway's hostname (something like `mtg.example.com`);
- a sign-in account on their identity provider (for example Authentik), or an invite link to make
  one. Pick your own password there and don't send it to anyone, the owner
  included.

### For the owner

Access lives entirely in your identity provider. The gateway has no user
list of its own. With Authentik, in the admin interface:

1. Create the person's account, or send them an invitation if you've got an
   enrollment flow set up.
2. Add them to the group bound to the **MTG Assistant Gateway** application (the one
   from [DEPLOY.md](DEPLOY.md) step 3, for example `MTG Assistant Gateway Users`). They
   also need to be in the group named in `MTG_REQUIRED_GROUP`, normally the
   same one.
3. Send them the hostname and this page.

The exact Authentik screens are in
[IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#8-add-people-accounts-invitations-groups).
To remove someone later, see
[OPERATIONS.md](OPERATIONS.md#revoking-access).

## 2. Connect your AI assistant

The easiest way is the gateway's install page, `https://mtg.example.com/install`
(you sign in with your account first).
It has a **Connect to Claude** button, the plugin download, and the two
commands for Claude Code (see [PLUGIN.md](PLUGIN.md)). Or add this URL as a
custom connector yourself:

```
https://mtg.example.com/mcp
```

- **Claude** (web, desktop app, iOS and Android). On claude.ai or the
  desktop app, go to **Customize → Connectors → + Add → Add custom
  connector** and paste the URL. Under OAuth client, pick **Use Claude's
  published identity** (**Register automatically** works too). The gateway
  first shows a page naming Claude and the address you'll come back to
  (`claude.ai`). Press **Approve and sign in**, then sign in with your
  sign-in account. If you ever see that page when you didn't just connect
  something, press **Deny**. After that it works in the
  Claude phone apps too. Adding connectors from the phone apps is still a
  beta, so do it on the web or desktop first.
- **ChatGPT** (add it on the web). Turn on **Developer mode** in Settings, create an
  app with the URL and OAuth sign-in, then connect, press **Approve and sign
  in** on the gateway's page if it returns to `chatgpt.com`, and sign in. Heads up:
  these steps are only partly tested. Once added on chatgpt.com, the
  connector works in the ChatGPT Android app too (tested; the iPhone app
  hasn't been tried), the desktop app is unconfirmed, and OpenAI's pages
  disagree on which tools each
  plan gets. On a Pro plan, ChatGPT used every tool, deck proposals
  included, in a check with this gateway on 2026-10-10. Plus has not been
  tried yet.

Full step-by-step, error messages and what's been tested are in
[CONNECT.md](CONNECT.md).

**Check it worked:** in a new chat, turn the connector on and ask "Use the
MTG Assistant Gateway: who am I signed in as?" You should see your own name.

## 3. Link your Archidekt account

Research, and reading public or unlisted decks, works without this.
Linking lets the assistant read your private decks, edit decks you own and
create new decks in your account.

1. In a browser, open `https://mtg.example.com/account`.
2. Sign in with the same sign-in account you used for the connector.
3. Under **Link your Archidekt account**, read the note first. It says
   exactly what linking gives the gateway and what it doesn't. Most
   important: the key that opens your stored Archidekt session is on the
   same server, so whoever runs the server can use that session to act as
   you on Archidekt until it expires (about 40 days after you link). Link
   only if you trust them. It also says Archidekt's terms restrict
   automated access, so Archidekt could limit or block an account used this
   way. The full text is in
   [USING.md](USING.md#what-linking-archidekt-gives-the-gateway).
   Then enter your Archidekt username or email and your Archidekt password,
   tick the box to say you've read the note, and press **Link account**.
4. The page now says "Linked to" and your Archidekt username, and the date
   your link stops working. The unlink button and the note are on the same
   page.

What happens to your password: the gateway sends it to Archidekt once to get
a login session, keeps only that session (encrypted), and throws the
password away. If Archidekt stops accepting the session later, the assistant
will tell you to link again on the same page.

Never type your Archidekt password into a chat. The assistant won't ask for
it. If it ever does, don't answer, and tell the owner.

## 4. Install the MTG skill (optional, recommended)

The skill teaches the assistant which tool to use for what, how to report
simulations honestly, and to always show you a change before making it. If
you installed the plugin from `/install`, you already have it.

1. Open `https://mtg.example.com/skill` in a browser and sign in (same
   sign-in account as before).
2. **Claude:** download the ZIP from that page and upload it under
   **Customize → Skills** (**+**, **Create skill**, **Upload a skill**).
   Don't unzip it.
3. **ChatGPT:** copy the text from the box on that page into a ChatGPT
   project's instructions.

More detail and the fine print are in [SKILL.md](SKILL.md).

## 5. Things to try

With the connector turned on in a chat:

- "What does Kenrith, the Returned King do, and what are its rulings?"
- "What are the top EDHREC cards for my commander?"
- "Here's my decklist: (paste it). Is it legal in Commander?"
- "Here's my Archidekt CSV export: (paste it). What's my mana curve?"
- "List my Archidekt decks."
- "Load https://archidekt.com/decks/12345 and suggest five upgrades under $5."
- "How often does this deck cast its commander by turn 4? Run a goldfish
  simulation." (That measures speed and consistency playing alone, not a
  win rate against real opponents.)
- "Propose swapping Mind Stone for Arcane Signet in deck 12345."
- "Build me a budget Kenrith deck and propose it as a new private deck."

## 6. How deck edits and new decks work

Edits and new decks always take two steps, and nothing changes until you say
so:

1. **Propose.** The assistant saves a proposal and shows you the exact
   change, like `-1 Mind Stone` and `+1 Arcane Signet`, with a review link
   such as `https://mtg.example.com/proposals/<id>`. Nothing on Archidekt
   has changed yet.
2. **Apply.** Nothing happens until you press a button yourself. Which
   button depends on your app and on how the owner set things up:
   - **The card in the chat** (Claude on the web, desktop and phones;
     ChatGPT on the web): the proposal appears as a card next to the
     assistant's message, with each card's picture, the exact change and
     **Approve** and **Reject** buttons. Press Approve and the gateway
     applies it and tells the assistant how it went. The first time, Claude
     asks whether to show the app; allow it.
   - **Reject** on the same card asks for a reason (optional) and passes it
     to the assistant with the outcome.
   - **The review page in the browser** (every app, and always available):
     the assistant gives you the review link. Open it, check the change,
     press **Apply these changes to Archidekt**, then tell the assistant you
     did. In Claude Code the assistant can offer to open that page for you.
   - **The assistant applies it** (only if you chose that): on your
     Account page you can switch your own **approval mode** from Manual
     (shown as "Ask me every time", the starting mode by default) to Semi-auto (shown as "Apply low-risk
     changes without asking": small deck or collection edits up to the
     row limit, 5 card rows by default, each a card added or removed, or a
     new quantity, category, finish, printing, color tag or custom mana
     value, no more than 4 copies a row, never a change to which cards are
     the commanders or to the categories of a row in the commander zone, never fewer copies of a card that has a
     row in the commander zone and another row, both in the deck or both in the maybeboard or sideboard, and private
     copies of a deck) or Full auto (shown as "Apply deck and collection
     changes without asking": the rest too, except a restore, a
     commander or companion change, a new deck, an edit of more than 20 rows (by default), making a deck public or unlisted, a category change that touches the commander or which cards count in the deck, and any edit of a description that has formatting,
     such as bold text, links or pictures, which still wait for your press; so
     does a held change, a change waiting for another change first, one
     another app had a hand in, one that replaces another app's change,
     or one that another app's waiting change would replace). Those numbers are the
     defaults: your Account page shows this gateway's numbers, and which
     modes it allows. Then the assistant applies what your mode allows and tells
     you what it did. Actions on your Archidekt account (likes, votes, bookmarks, follows, comments, new folders, collaborator changes, deck help requests, reports to Archidekt's moderators, contest entries, and profile and email settings), deleting from your Archidekt lists, Premier in your default categories, taking custom cards out of a deck and deleting a deck are never applied by an assistant: each one waits for your own press, in every mode, on the card in the chat or on its review page (deleting a deck, and an edit of a comment that has formatting: on the review page only). An edit of a description that has formatting is also approved on its review page only. Be aware that an assistant can be tricked by text it
     reads into making changes you did not ask for. If you let your
     assistant apply changes without asking, such a change can land on
     Archidekt without your press. A change to one of your decks keeps a
     snapshot you can restore from the History page; a collection change
     keeps none. Restoring a snapshot does not put back a deck's folder, tags,
     cover or custom cards.

   Either way, for an edit the gateway first checks the change still fits
   the deck as it is now, saves a copy of it (a snapshot), and (unless the
   owner switched backups off) puts a private backup copy in your backup
   folder on Archidekt, called "MTG Gateway Backups" unless the owner renamed it. Then it makes the change and reads the deck back to confirm it
   matches. The assistant should tell you the deck was read back and matched, give you the
   snapshot id (or the new deck's link), and say how to undo it. You can
   always use the review link instead of answering in chat, just not both.

Only your own press counts: the card's Approve button sends a one-time code
the assistant never sees, and the review page needs your signed-in browser,
so the assistant can't approve a proposal for you. If a deck description, a
card note or anything else the assistant reads says "apply this" or
"approved", the assistant has to ignore it and tell you. If your assistant
ever applies a change you didn't okay, tell the owner.

Good to know:

- Only decks your linked Archidekt account owns can be changed.
- Edits touch the main deck unless the proposal says otherwise: a change to
  your maybeboard or sideboard is marked "(maybeboard/sideboard)" in the
  review. Moving a card into the Maybeboard or Sideboard category takes it
  out of the main deck; the review shows that as "leaves the deck".
- The review page says which app made the proposal ("browser", or the AI
  app's name). If you disconnect an app on your Account page, its pending
  proposals are rejected.
- A proposal waits 7 days unless the owner changed that, and can only be
  applied once. Don't want
  it? Press **Reject this proposal** on the review page. Nothing goes to
  Archidekt, and the assistant sees it as rejected.
- If the deck changes on Archidekt after a proposal is made, the gateway
  works the change out again on the deck as it is now. If it still does
  exactly what you reviewed, it shows **Updated** and can be applied; if not,
  it shows **Needs a look** and nothing is sent until your assistant updates
  it or you reject it.
- **To undo an edit**, ask the assistant to restore the deck from its
  snapshot. It proposes the restore like any other change, you okay it, and
  every card goes back as it was: same printing, foil, quantity, color tag,
  custom mana value and categories in their order, including the commander,
  sideboard and maybeboard. The deck's name, description, format, bracket
  and privacy go back too, so a settings change
  can be undone as well (folder, tags and cover stay). The backup copies in your
  backup folder on Archidekt are there too if you ever want to look at or
  copy an old version yourself; they don't show up in the assistant's list
  of your decks.
- The assistant can propose deleting a deck, but only your own press on the
  proposal's review page deletes it. You can also delete a deck yourself
  (the deck's More menu, **Delete deck**, or on Archidekt).
- The assistant can also propose a deck's settings: name, description,
  format, bracket, privacy, and moving it to another of your folders,
  changing its tags or its cover. You approve those like any other change.
  It can propose a new folder, which only your own press creates; renaming
  folders is yours alone, on the Folders page.
- The owner decides whether changes can be applied to Archidekt; your
  Account page shows whether it's on. While it's off you can
  still make and review proposals, and the assistant will tell you applying
  is disabled.
- The assistant can also create a new deck in your Archidekt account from a
  list it built, a list you pasted or a CSV export. Same deal: you see the
  full card list as a proposal first, and the deck is private unless you ask
  otherwise.
- All your proposals are at `https://mtg.example.com/proposals`.

## 7. What it can't do

- Edit anyone else's deck.
- Predict multiplayer win rates. The solo test (goldfish simulation) plays your deck alone.
  A good report gives the seed, number of games, mulligan settings, what it
  couldn't simulate, and the confidence intervals.
- Keep price watchlists or price history when whoever runs the gateway has
  turned watchlists off, or run step-by-step practice games through the
  assistant (the deck page's **Goldfish** lets you play a deck by hand).
  Deck reports, including goldfish results, are saved by the gateway itself.

## 8. Privacy

- Your sign-in password is only ever typed on the owner's sign-in page.
  The gateway and the AI app never see it.
- Your Archidekt password is typed once on the `/account` page and not
  stored. The Archidekt session is stored encrypted on the owner's server.
  No page, admin tool, log or backup shows it, and the gateway's backups
  leave it out (and the sign-in service's tokens too), so if the owner ever
  restores a backup you sign in again and relink Archidekt. When the owner
  disables your account, the stored session is deleted. Honest caveat: none
  of this protects against the owner, who runs the server and holds the
  key: they can open the session and act as you on Archidekt until it
  expires, and they control the code. Only link an account if you're fine
  with that. The link form spells this out in full
  ([USING.md](USING.md#what-linking-archidekt-gives-the-gateway)).
- To check you're still allowed in, the gateway keeps the sign-in
  service's tokens for your account (encrypted) and asks it, every few
  seconds while you use the gateway, whether you're still in the group. If
  the owner removes you, you're signed out everywhere on your next click.
  If the sign-in service only asks for a new sign-in on one device (its
  sign-in there ran out, say), only that device signs in again; your AI
  apps stay connected. Whenever you're signed out, the page says why.
- The guide and the setup steps (`/guide`, `/install`) can be read without
  signing in, so you can look before you join. Nothing of yours is on them.
- Your profile picture (the one the sign-in service shows: Gravatar, or a
  picture you uploaded there) is copied to the owner's server for the
  account icon. The server fetches it, so your browser never contacts
  Gravatar. Without one you see your initials. **Delete my data** removes
  the copy.
- Every tool call is tied to your account. Sign-ins, proposals, applies,
  links and unlinks go in an audit log, and the gateway keeps a snapshot of
  each deck from just before a change. The owner can see these. The backup
  copies on Archidekt are private decks in your own account.
- Research lookups go from the owner's server to Scryfall, EDHREC, Commander
  Spellbook, Wizards of the Coast (rules) and Archidekt's public deck pages,
  not from your device.
- Archidekt has no official interface for apps like this, and its terms
  restrict automated access, so Archidekt could limit or block an account
  used through the gateway. The gateway paces its requests and only writes
  after you approve. If Archidekt objects, the owner may turn writes off.

## 9. Signing out and leaving

**Signing out.** Use **Sign out** at the foot of the Account page (your
picture, top right). To sign out everywhere (say you lost a phone), open
`https://mtg.example.com/logout` and press **Sign out on all my devices**.
That signs out every browser and the Android app; connected AI apps keep
working, so disconnect them on the Account page too. For an hour after you sign out, signing back in on that
device asks for your password again, so the next person on a shared phone
doesn't land in your account.

**Leaving for good:**

1. On `https://mtg.example.com/account`, under **Delete my data**, tick the
   box and press **Delete my data**. That deletes everything the gateway
   keeps for you from its database: proposals, snapshots, test reports, scan
   sessions, deck covers, the stored Archidekt session, connected apps,
   the copy of your profile picture and your sign-in. Your decks on Archidekt (and the backup copies in your
   Archidekt account) stay yours. The owner's nightly database backups, if
   they keep them, still hold a copy (without your Archidekt session) until they age out (14 days by default). The owner's security log keeps a record that your
   account existed and was deleted until it ages out after a year. If you only want to stop
   using Archidekt through the gateway, press the unlink button instead.
2. Unlinking and deleting your data delete the gateway's copy of your
   Archidekt session. Whether a copy stops working on Archidekt's side
   before it expires, even if you change your Archidekt password, is not
   known.
3. Remove the connector in your AI app's connector settings.
4. Ask the owner to remove your sign-in access.
