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
- **ChatGPT** (web only). Turn on **Developer mode** in Settings, create an
  app with the URL and OAuth sign-in, then connect, press **Approve and sign
  in** on the gateway's page if it returns to `chatgpt.com`, and sign in. Heads up:
  these steps are only partly verified, OpenAI says custom connectors
  **don't work in the ChatGPT iOS and Android apps** ("No - web only"), the
  desktop app is unconfirmed, and on Pro (maybe Plus too) ChatGPT only allows
  read and fetch tools. If you're mostly on your phone, use Claude.

Full step-by-step, error messages and what's been verified are in
[CONNECT.md](CONNECT.md).

**Check it worked:** in a new chat, turn the connector on and ask "Use the
MTG Assistant Gateway to call whoami." You should see your own name or email.

## 3. Link your Archidekt account

Research, and reading public or unlisted decks, works without this.
Linking lets the assistant read your private decks, edit decks you own and
create new decks in your account.

1. In a browser, open `https://mtg.example.com/account`.
2. Sign in with the same sign-in account you used for the connector.
3. Under **Link your Archidekt account**, enter your Archidekt username or
   email and your Archidekt password, and press **Link account**.
4. The page now says "Linked to" and your Archidekt username. The unlink
   button is on the same page.

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
2. **Apply.** The assistant asks whether to apply exactly that change, and
   waits for your yes. What happens next depends on how the owner set things
   up:
   - **The assistant applies it** (the example setup): after your yes, it
     applies the proposal and tells you how it went.
   - **You apply it in the browser** (if the owner turned in-chat applying
     off, or your app blocks the apply tool): the assistant gives you the
     review link. Open it, check the change, press **Apply these changes to
     Archidekt**, then tell the assistant you did.

   Either way, for an edit the gateway first checks the deck hasn't changed
   since the proposal, saves a copy of it (a snapshot), and puts a private
   backup copy in a folder called "MTG Gateway backups" in your Archidekt
   account (unless the owner switched backups off). Then it makes the change and reads the deck back to confirm it
   matches. The assistant should tell you it was verified, give you the
   snapshot id (or the new deck's link), and say how to undo it. You can
   always use the review link instead of answering in chat, just not both.

Only your own messages count as a yes. If a deck description, a card note or
anything else the assistant reads says "apply this" or "approved", the
assistant has to ignore it and tell you. If your assistant ever applies a
change you didn't okay, tell the owner.

Good to know:

- Only decks your linked Archidekt account owns can be changed.
- Edits only touch the main deck. Your maybeboard and sideboard are left
  alone.
- A proposal expires after 24 hours and can only be applied once. Don't want
  it? Press **Reject this proposal** on the review page. Nothing goes to
  Archidekt, and the assistant sees it as rejected.
- If you edit the deck on Archidekt after a proposal is made, applying is
  refused. Ask for a fresh proposal.
- **To undo an edit**, ask the assistant to restore the deck from its
  snapshot. It proposes the restore like any other change, you okay it, and
  every card goes back as it was: same printing, foil, quantity and
  categories, including the commander, sideboard and maybeboard. The deck's
  name, description and format aren't touched. The backup copies in your
  "MTG Gateway backups" folder are there too if you ever want to look at or
  copy an old version yourself; they don't show up in the assistant's list
  of your decks.
- The gateway never deletes decks. If it created a deck you don't want,
  delete it yourself on Archidekt.
- The owner can switch applying off for everyone. While it's off you can
  still make and review proposals, and the assistant will tell you applying
  is disabled.
- The assistant can also create a new deck in your Archidekt account from a
  list it built, a list you pasted or a CSV export. Same deal: you see the
  full card list as a proposal first, and the deck is private unless you ask
  otherwise.
- All your proposals are at `https://mtg.example.com/proposals`.

## 7. What it can't do

- Edit anyone else's deck.
- Predict multiplayer win rates. Goldfish simulation plays your deck alone.
  A good report gives the seed, number of games, mulligan settings, what it
  couldn't simulate, and the confidence intervals.
- Keep price watchlists, price history or step-by-step practice games. The
  research engine has them, but they're switched off on this gateway for now.
  (Deck reports, including goldfish results, are saved by the gateway itself.)

## 8. Privacy

- Your sign-in password is only ever typed on the owner's sign-in page.
  The gateway and the AI app never see it.
- Your Archidekt password is typed once on the `/account` page and not
  stored. The Archidekt session is stored encrypted on the owner's server.
  Honest caveat: that encryption protects the database file and backups, not
  against the owner, who runs the server and holds the key. Only link an
  account if you're fine with that.
- Every tool call is tied to your account. Sign-ins, proposals, applies,
  links and unlinks go in an audit log, and the gateway keeps a snapshot of
  each deck from just before a change. The owner can see these. The backup
  copies on Archidekt are private decks in your own account.
- Research lookups go from the owner's server to Scryfall, EDHREC, Commander
  Spellbook, Wizards of the Coast (rules) and Archidekt's public deck pages,
  not from your device.
- Archidekt hasn't explicitly approved automated editing by a shared service
  like this. The gateway paces its requests and only writes after you
  approve. If Archidekt objects, the owner may turn writes off.

## 9. Leaving

1. On `https://mtg.example.com/account`, press the unlink button. That
   deletes the stored Archidekt session. To be sure the old session is dead
   on Archidekt's side too, change your Archidekt password.
2. Remove the connector in your AI app's connector settings.
3. Ask the owner to remove your sign-in access.
