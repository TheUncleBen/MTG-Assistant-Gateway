# Connecting Claude or ChatGPT to the gateway

You need an account on the owner's sign-in service (an identity provider
such as Authentik) that's been given access to the gateway. Don't have one? Ask the owner, and see
[ONBOARDING.md](ONBOARDING.md).

The connector URL is:

```
https://mtg.example.com/mcp
```

Swap `mtg.example.com` for the hostname the owner gave you, and keep the
`/mcp` on the end.

**Before you add it, check the address from outside.** Claude and ChatGPT
reach the gateway from their own servers, not from your network, so an
address that works at home can still fail for them ("couldn't reach this
address", or the connector never finishes connecting). On a phone with Wi‑Fi
off (mobile data), open `https://mtg.example.com/healthz`: it should show
`{"status":"ok"}`. Then open `https://mtg.example.com/mcp`: a one-line message saying
`Authentication required` is the right answer (the connector signs in through
OAuth; a blank timeout or a proxy error page is not). If
either fails on mobile data but works on your home Wi‑Fi, the owner has a
checklist in [TROUBLESHOOTING.md](TROUBLESHOOTING.md#the-site-doesnt-load)
(the "couldn't reach this address" row).

The gateway's setup hub, `https://mtg.example.com/install` (signed in), has
every way in on one page, with numbered steps and Copy buttons, and can show
just one section: `?for=claude`, `?for=claude-code`, `?for=chatgpt`,
`?for=codex`, the assistant skill (`https://mtg.example.com/install?for=skill`)
or the Android app (`https://mtg.example.com/install?for=android`). The same
steps as plain text are at `https://mtg.example.com/install.md`, for an AI
agent you hand the link to. The older `/skill` and `/app` addresses lead to
their section of the hub.

You never need a client ID, client secret or API key. The gateway sets up
each AI client automatically, and you prove who you are by signing in to
the owner's sign-in service in your browser.

**About the labels below.** Every claim about Claude or ChatGPT comes from
the vendor's own help pages, read on 2026-10-04 or 2026-10-05, and is
marked:

- **verified**: an official page says so, and we read the exact wording;
- **reported**: an official page says so, but we could only read it through
  a tool that summarises pages, so the wording isn't checked;
- **unverified**: no official page we could read says either way.

Vendors rename menus and change plans all the time, so if a label doesn't
match, look for the closest thing. Not every client and device has been
tested end to end with this gateway yet; the owner keeps those results.

## Which apps and devices work

| Client | Add the connector | Use it once added |
| --- | --- | --- |
| Claude web (claude.ai) | Yes (verified) | Yes (verified) |
| Claude desktop app | Yes (verified; Anthropic doesn't separate Windows and Mac) | Yes (verified) |
| Claude iOS and Android | Beta, and not documented for custom connectors (unverified). Add it on the web or desktop instead | Yes, once added on the web or desktop (verified) |
| ChatGPT web, Pro plan | Yes, in Developer mode (verified 2026-10-10) | Yes, every tool, deck proposals included (verified 2026-10-10) |
| ChatGPT web, Plus plan | OpenAI's pages disagree (reported) | OpenAI's pages disagree (reported) |
| ChatGPT desktop app | Unverified; OpenAI's pages only mention the web | Unverified |
| ChatGPT iOS and Android apps | **No, OpenAI doesn't support it** (reported: "No - web only") | **No, OpenAI doesn't support it** (reported) |
| ChatGPT in a phone's web browser | Unverified, untested | Unverified, untested |

In plain terms:

- **Claude works on every device.** Add the connector once on claude.ai or
  in the desktop app and it works on your phone too.
- **ChatGPT is web only.** OpenAI says custom connectors are web only, so
  the ChatGPT phone apps can't use this gateway. Its help center says Pro
  gets read and fetch tools only, and full support (including tools that
  change things) is for Business, Enterprise and Edu. OpenAI's developer
  guide says Plus and Pro get read and write tools on the web. On
  2026-10-10 the owner checked ChatGPT on the web with a Pro plan against
  this gateway: it used every tool, creating proposals included (verified).
  Plus is still untested.
- **What that means for deck changes in ChatGPT.** The gateway marks every
  tool as read-only except the ones that apply, approve, reject or tidy
  waiting changes, start Forge games, save a scan, remove a saved card
  description or change your watchlist: `apply_proposal`, `confirm_proposal`, `confirm_waiting`, `reject_proposal`,
  `amend_proposal`, `withdraw_proposal`, `combine_proposals`,
  `run_deck_report`, `play_more_games`, `save_scan_session` and
  `remove_card_model`, and, when the operator has turned watchlists on,
  `watch_card`, `watch_deck` and `unwatch`.
  Making a proposal only records it, so even if ChatGPT limits you to
  read-only tools you should still be able to propose changes and then press
  Approve on the card ChatGPT shows with the proposal, or Apply on the
  gateway's review page in your browser. On Pro no such limit showed in the
  owner's check. Whether a plan with the "read/fetch" limit follows those
  markings, and whether ChatGPT shows the card for a connector added in
  developer mode, hasn't been tested yet.
- **On ChatGPT Plus and Android? Use Claude for deck edits.** Claude's free
  plan allows one custom connector (verified), and it works in Claude's
  Android app once added on the web. ChatGPT might still work for you in a
  computer's browser; whether Plus can call the gateway's tools is unknown
  until someone tries it.

## Claude

Custom connectors work on Claude's Free, Pro, Max, Team and Enterprise plans
(verified; Free is limited to one custom connector).

### Add the connector (web or desktop app)

1. Open **Customize → Connectors**.
2. Click **+ Add**, then **Add custom connector**.
3. Name: `MTG Assistant Gateway`. Remote MCP server URL: the URL above. Click
   **Continue**.
4. Look over the authentication settings and click **Continue**.
5. When asked how to sign in, pick **Sign in now**.
6. Under **OAuth client**, Claude offers three options (verified). The
   gateway supports the first two. Pick **Use Claude's published identity**:
   - **Use Claude's published identity** (recommended, by us and by
     Anthropic): Claude identifies itself with a web address that points to
     a description of Claude hosted by Anthropic (a Client ID Metadata
     Document). The gateway fetches and checks it, and nothing has to be
     registered up front. You get one extra page before signing in (below)
     that names Claude and shows where your sign-in will be sent.
   - **Register automatically**: also works. Claude registers itself with
     the gateway first (Dynamic Client Registration). You get the same
     extra page, without the "identified by" host. Use this if the
     published identity option fails.
   - **Use your own OAuth client**: don't pick this. There's no client ID to
     enter.

   Some accounts see one screen instead, with the client ID and secret
   under **Advanced settings** (verified). Leave those empty.
7. Click **Add**. A browser window opens.
   - You first see the gateway's **Connect an application** page. It says "An application wants to connect to your
     account", names the application (**Claude**) and the host it's
     "identified by", lists what it'll be able to do (read your decks and
     propose deck changes, and apply them only when you approve, unless you
     chose an approval mode on your account page that lets it apply changes
     without asking), and
     shows "After sign-in you go to" a host. That host should be
     `claude.ai`, Claude's sign-in return address for its hosted apps
     (verified). If it all looks right, press **Approve and sign in**. If
     not, press **Deny**. The sign-in is cancelled, Claude is told it was
     refused, and nothing gets connected.
   - Then the owner's sign-in page opens. Sign in with the account
     the owner gave you. The window closes or tells you to go back to Claude.

   If the page also says **Warning: this application runs on this
   computer**, the sign-in will be handed to an app on your own computer (a
   localhost address). Anyone can claim a well-known name that way, so only
   approve if you started this sign-in yourself a moment ago. Claude's
   documented return address is `claude.ai`, so you shouldn't see this
   warning when connecting Claude (inferred from that address, not tested).

The **Connect an application** page is tested in Chromium (the engine behind
Chrome and Edge) by an automated browser test that presses Approve and Deny.
It hasn't been tested in Firefox or Safari, or with the real Claude app yet.
If sign-in gets stuck on that page, remove the connector, add it again with
**Register automatically**, and tell the owner which browser you used.

Claude can't change a connector's sign-in settings after it's added
(verified). To switch options, remove the connector and add it again.

### The connector's icon in Claude

Claude shows a generic tile for this connector (a globe, a letter, or the
favicon of the gateway's parent domain), not the gateway's own icon. Nothing
on the gateway can change that today. The gateway does send its icons the
way the MCP standard describes (`serverInfo.icons`), but developers report
that claude.ai doesn't read them for custom connectors yet
([anthropics/claude-ai-mcp issue #152](https://github.com/anthropics/claude-ai-mcp/issues/152),
open with no reply from Anthropic; this is a report, not Anthropic's
documentation). The same reports say Claude takes the favicon that Google
has for the last two parts of the connector's host name, the parent domain
(`example.com` for `mtg.example.com`), and shows a letter or placeholder
when there is none. A self-hoster who wants the MTG icon there would have
to make it the favicon of that parent domain's own website. That changes
the favicon of that whole site too, and the same reports say it is
unreliable, so we don't recommend it; it is the owner's call.

On a **Team or Enterprise** plan, only an organization owner can add custom
connectors (Organization settings → Connectors). Once they've added it, you
connect and sign in from Customize → Connectors.

For the gateway owner: Claude's sign-in returns to
`https://claude.ai/api/mcp/auth_callback` (verified). Clients send their
return address when they register automatically or in their published
description, so there's no list of addresses to keep on the gateway. The
gateway only accepts `https` return addresses, or `http` on `localhost` for
apps running on your own computer, and refuses anything else. To limit which
published descriptions are accepted, see
[OPERATIONS.md](OPERATIONS.md#which-ai-clients-may-connect).

### Use it in a chat (web, desktop, iOS, Android)

1. In a new chat, click the **+** button at the lower left of the message
   box (or type `/`), hover over **Connectors**, and switch **MTG Assistant Gateway**
   on.
2. Ask: "Use the MTG Assistant Gateway to call whoami." You should see your own name.

A connector added on the web or desktop shows up in the Claude iOS and
Android apps the next time you sign in there (verified). Anthropic calls
adding connectors from the phone apps a beta, and its custom-connector page
doesn't list mobile, so add it on the web or desktop.

### Approving a change in the chat

When the assistant proposes a deck change, Claude shows the proposal as a
card right in the conversation: the exact change with a small picture of
each card, and **Approve** and **Reject** buttons (Claude on the web,
desktop, iOS and Android; Claude Code shows the text and the review link
instead). The first time, Claude asks whether to display the app; allow it
for this connector. Nothing reaches Archidekt until you press Approve on
the card or Apply on the review page, unless you chose a looser approval
mode on your Account page (see below). The card's Approve button sends a
one-time code the assistant never receives, and the gateway refuses that
tool without it, so a tricked assistant can't approve its own proposal.
That rests on Claude keeping the card's tool and code away from the model,
as the MCP Apps standard says (reported, not yet verified in a live
session); the review page in your browser never relies on it and is linked
from every card.

### The other cards in the chat

The same mechanism shows a card wherever you have something to pick: the
**printings** of a card as pictures in a grid (tap the one you mean, filter by
set or finish, expand it), the **cards read from your photos or list** with a
tick per row and suggestions for names it was unsure about (press **Use these
cards** when the list is right), a **deck** by category with pictures and
rules text when you tap a card, and your **account** with what still needs
setting up. Four more only show what the tool answered, so you can read it at
a glance: a **comparison** of two decks (the cards in one only, the other
only or both, and the statistics that differ), a deck's **statistics** (mana
curve, colours, price, legality and the bracket estimate), a **list of decks**
(yours, or a search) with covers and links to open each one, and a **scan**
with the cards it recognised and the ones that need a look, linking the Scan
page. A tap on these cards is sent to the assistant as plain text and
it carries on from there, with a proposal if anything is to change; none of
them changes anything by itself. The pictures and rules text come from
Scryfall; the deck rows and printings come from the gateway through a link
that is yours alone and expires after ten minutes. All of this is verified
in a browser test against the cards themselves, not yet in a live Claude
session; Claude Code shows the text instead and asks about unsure names with
a form.

**Your approval mode.** On the gateway's Account page you choose how much
your assistant may do without asking: *Manual* (every change waits for
your press; the default), *Semi-auto* (it applies a small deck or
collection edit itself: up to the row limit, 5 card rows by default,
each a card added or removed, or a new quantity, category, finish,
printing, color tag or custom mana value, no more than 4 copies a row,
never the commander; it also makes a private copy of a deck; a new deck,
a restore, the deck's details and bigger edits still ask you) or *Full
auto* (it applies the rest too, except a restore, a commander change, a new deck, an edit of more
than 20 rows, making a deck public or unlisted, and any edit of a description that has formatting, such as bold text, links or pictures, which still wait for your press; so does a held change, a change waiting for
another change first, one another app had a hand in, one that replaces another app's change, or one
that another app's waiting change would replace).
Actions on your Archidekt account (likes, votes, bookmarks, follows,
comments, new folders and collaborator changes) and deleting a deck are
never applied by an assistant: each one waits for your own press, in every mode, on the card in the chat or on its review page (deleting a deck, and an edit of a comment that has formatting: on the review page only). It is your own setting:
it covers only your account, your decks and the apps you connected, and
only you can change it, in your browser. In either auto mode the assistant
applies what the mode allows with `apply_proposal` and tells you; the card
then shows the change without buttons. Every change still keeps a snapshot
you can restore from the History page. The cost: an assistant can be
tricked by text it reads into proposing a change you did not ask for, and
in an auto mode that change lands without your press.

Claude lets you set each tool to always allow, needs approval, or blocked
(verified). Keep `apply_proposal` on **needs approval**: in an auto mode
that's the tool that actually changes your deck. Claude's Research mode can
call connector tools without asking (verified). Everything except
`apply_proposal`, `confirm_proposal` and `confirm_waiting` (the card's own
buttons), `reject_proposal`, `amend_proposal`, `withdraw_proposal`,
`combine_proposals`, `run_deck_report`, `play_more_games`,
`save_scan_session`, `remove_card_model`, `watch_card`, `watch_deck` and
`unwatch` only reads or records proposals.

## ChatGPT

Read [Which apps and devices work](#which-apps-and-devices-work) first.
ChatGPT supports this gateway on the **web only**. On Pro every tool
worked when the owner checked it on 2026-10-10 (verified); Plus is untested.

### Add the connector (ChatGPT on the web)

The menu path comes from OpenAI's developer guide (reported); the field
names are **unverified**. The gateway's own page,
`https://mtg.example.com/install?for=chatgpt`, has the same steps with Copy
buttons for the name, description and URL, and the icon to download.

1. On a computer, open chatgpt.com and sign in.
2. Download the connector icon:
   `https://mtg.example.com/static/gateway-icon-connector.png`. It's a PNG
   under 10 KB; ChatGPT's form refuses a larger one (seen in the form,
   2026-10-11). It needs no sign-in, so it downloads on a phone too.
3. Open **Settings → Security and login** (reported, OpenAI developer docs
   seen 2026-10-05) and turn on **Developer mode**. Not there? Look under
   Apps, then Advanced settings. If it's missing everywhere, your plan may
   not offer it (see the table above).
4. Open **Plugins** in the sidebar and press **+** (reported, OpenAI
   developer docs seen 2026-10-05; older pages call this Apps, then Create).
5. Name: the gateway's name as the owner set it (`MTG Assistant Gateway`
   unless they changed it; the install page shows the right one). An owner
   can set `MTG_CHATGPT_NAME` to give the ChatGPT connector a different name
   from `MTG_SERVER_NAME` ([DEPLOY.md](DEPLOY.md)).
6. Description: `Magic: The Gathering deck research, goldfish testing and
   safe Archidekt deck edits, from a self-hosted gateway.` This is the
   description the gateway also gives apps itself.
7. MCP server URL: the connector URL above, exactly as shown.
8. Icon: the file from step 2. Set it now: ChatGPT reportedly lets you set
   the icon only while creating the connector (a user report on GitHub,
   not OpenAI's documentation).
9. Authentication: **OAuth**. Leave any client ID and secret fields empty.
   OpenAI says ChatGPT uses a published client identity (a "client ID
   metadata document") when the server supports one, and otherwise
   registers itself (reported). The gateway supports both, so either works.
10. Accept the risk notice and create it.
11. Click **Connect**. The gateway first shows a **Connect an application**
    page: check that "After sign-in you go to" says `chatgpt.com`, then
    approve. If sign-in fails saying the client isn't allowed, tell the
    owner: their gateway might be limited to Claude's published identity
    (`MTG_CIMD_ALLOWED_HOSTS`).
12. Sign in with your sign-in account when the browser opens.

### The ChatGPT plugin file (Business/Enterprise admins only)

**Download the ChatGPT plugin (ZIP)** from the ChatGPT section of the
gateway's setup hub, or directly at
`https://mtg.example.com/plugin/mtg-gateway-chatgpt.zip` (the file name
follows the owner's `MTG_SERVER_NAME`, as the Claude plugin's does; the
connector name inside it is their `MTG_CHATGPT_NAME` when set, otherwise
their `MTG_SERVER_NAME`). It holds the
connector (this gateway's address) and the MTG skill in one file, in the
Agent Plugins format that OpenAI's plugins use. It needs no sign-in to
download: it holds nothing secret.

Who can install it:

- **Workspace owners and admins on ChatGPT Business and Enterprise** upload
  it under Plugins in the admin console (reported from OpenAI's help pages,
  not yet tested with this gateway).
- **Plus and Pro accounts have no plugin upload** (checked by the owner on
  a Pro account, 2026-10-11): they use the connector, added as above; it
  works the same.

### Use it in a chat

Start a new chat on the web, open the tools menu next to the message box,
and pick the MTG Assistant Gateway app (in developer mode it may be under a developer
mode or apps entry). Then ask it to call `whoami`. (Unverified.)

OpenAI says ChatGPT asks for confirmation before write actions by default,
and may block some risky ones (reported). OpenAI also says ChatGPT renders
MCP Apps (reported), so a proposal should appear as a card with Approve and
Reject buttons like in Claude; this hasn't been tried with ChatGPT yet. If
no card appears and ChatGPT refuses or hides `apply_proposal`, ask for the
proposal's review link and press Apply there. The other cards (printings,
recognised cards, a deck, the account, a comparison, statistics, a deck list,
a scan) follow the same standard and should
appear the same way (reported, not yet tried); without them, the assistant's
text has everything, and it asks which set you mean instead of showing the
pictures.

### If ChatGPT blocks write tools

OpenAI says some plans only get reading tools (reported). Every gateway
tool is marked read-only except six, so all of this keeps working: research
(Scryfall, EDHREC, rules), goldfish simulation, reading and importing decks,
card lookups for scanning, and making deck proposals. For the write tools
(collection changes: add or remove cards on the `/collection` page instead):

- `apply_proposal`: press **Approve** on the proposal card if ChatGPT shows
  one, or open the proposal's review link (or **Proposals** on the
  gateway) and press **Apply** there. Same preview, same checks. If you
  chose an auto approval mode on your Account page, the assistant applies
  what the mode allows itself.
- `reject_proposal`: press **Reject this proposal** on the review page.
- `run_deck_report`: press **Run full test** on the deck's page under
  **Decks** on the gateway.
- `save_scan_session`: scan on the gateway's `/scan` page in your browser
  instead.

The assistant should tell you this once and not keep retrying a tool the app
has blocked.

### ChatGPT desktop and phone apps

- **iOS and Android apps: OpenAI doesn't support them.** Its help center
  answers "Are MCP apps available on mobile?" with "No - web only"
  (reported, seen again 2026-10-05). OpenAI's newer Plugins pages say
  plugins from its directory work on web, iOS and Android, but say nothing
  about apps you create in Developer mode, so don't count on them on a
  phone.
- **ChatGPT in your phone's browser** (chatgpt.com, not the app):
  unverified and untested. It's the web version, so it might work. Tell the
  owner if you try it.
- **Desktop (Windows and Mac): unverified.** OpenAI's pages only mention the
  web. If you try it, ask the assistant to call `whoami` and tell the owner
  what happened, including any error text.

## Linking Archidekt

Research tools work as soon as the connector is signed in. To let the
assistant read and edit your own decks, link Archidekt once in a browser at
`https://mtg.example.com/account`. See
[ONBOARDING.md](ONBOARDING.md#3-link-your-archidekt-account).

## If sign-in fails

| Message | What to do |
| --- | --- |
| The sign-in page (for example Authentik) says you don't have access to the application | The owner hasn't added your account to the gateway's group yet. Ask them. |
| "Your account is not in the group that may use this service" | Same thing, reported by the gateway. Ask the owner. |
| "This sign-in link has expired or was already used" | Go back to the AI app and press Connect again. Sign-in links last ten minutes, including time spent on the confirmation page. |
| Claude shows an error right after you picked **Use Claude's published identity**, before any gateway page appears | The gateway couldn't fetch or accept Claude's published description, or the owner has limited which ones it accepts. Wait a minute (a failed fetch isn't retried sooner) and try again, or remove the connector and add it with **Register automatically**. Tell the owner either way. |
| "This sign-in was started in a different browser" | The AI app opened sign-in in one browser and you finished it in another. Try again and keep the whole sign-in in one browser window. On a phone this can happen when a link opens in a different browser app. |
| The connector worked before but now wants you to sign in again | Normal. By default the gateway asks you to sign in again about once a week (the owner can change that), and also after about 30 days of not using it, if the owner revoked your access, and once after the owner upgrades the gateway to version 0.6.1. Reconnect it in the app's connector settings. |

Your sign-in password only ever goes into the owner's sign-in page,
never into the AI app or the gateway.
