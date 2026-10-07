---
name: setup
description: Connect this assistant to the MTG Assistant Gateway for the first time, or fix a connection that stopped working. Use when the user says "set up", "connect", "install" or "sign in to" the MTG gateway, when a gateway tool fails with 401 or "not authenticated", or right after the mtg-gateway plugin was installed.
---

# Set up the MTG Assistant Gateway connection

You are walking one person through connecting to a self-hosted MTG Assistant Gateway.
The whole setup is three checks. Do them in order, stop at the first one
that fails, and tell the user exactly what to do. Nothing here needs a
password, token or key typed into this chat.

## Rules

1. **Never ask for, accept or store a secret.** Sign-in happens in the
   user's browser on the gateway owner's sign-in page. Archidekt is linked
   on the gateway's own `/account` page. If the user pastes a password or
   token here, say not to, do not use it, and suggest changing it.
2. **Say what you checked.** Each step below names the command or tool and
   what a good result looks like. Report the real result, not the expected
   one.
3. **Do not change anything outside the plugin.** No edits to other MCP
   configurations, shell profiles or settings files unless the user asks.

## Step 1: is the connector configured?

The plugin provides one MCP server named `mtg-gateway`.

- **Installed from the gateway** (`/plugin marketplace add https://<gateway>/plugin/marketplace.json`):
  the gateway address is already filled in. Skip to step 2.
- **Installed from the git repository**: the address is read from the
  `MTG_GATEWAY_URL` environment variable; until it is set, `/mcp` and
  `claude mcp list` report the server as failed with "invalid MCP url". Ask the user for the gateway address
  (the owner gave it to them, for example `https://mtg.example.com`), then
  tell them to set `MTG_GATEWAY_URL` to that address followed by `/mcp` in
  their shell profile (or run Claude Code with it set) and restart Claude
  Code. The address is not a secret.

If the user is unsure of the address, the owner's gateway shows it on its
front page and at `https://<gateway>/install`.

## Step 2: sign in

Signing in is a browser step the user does themselves:

1. In Claude Code, run `/mcp`, select **mtg-gateway**, and choose
   **Authenticate**. A browser window opens on the owner's sign-in service
   (for example Authentik). The user signs in with the account the owner gave them.
2. If the gateway first shows a page titled **Connect an application**, it
   is confirming which app is asking. The user presses the approve button
   if the named app is the one they are using, otherwise Deny.
3. The browser says the sign-in finished; the user comes back here.

Then call the `whoami` tool. Good result: the user's own name or email.

If it fails:

| What you see | What to do |
| --- | --- |
| The sign-in page says the user has no access to the application | The owner has not added their account to the gateway's group yet. Ask the owner. |
| "This sign-in link has expired or was already used" | Run `/mcp` and authenticate again; links last ten minutes. |
| "This sign-in was started in a different browser" | Retry and keep the whole sign-in in one browser window. |
| "invalid MCP url" or the server shows "not configured" | Step 1 was skipped; the address is missing. |
| 401 or "not authenticated" on a tool call after it used to work | The sign-in expired (by default the gateway asks for a fresh sign-in about once a week, and after about 30 days unused), the owner revoked it or took the user out of the gateway's group, or the owner upgraded the gateway to 0.6.1 (everyone signs in once more). Run `/mcp` and authenticate again; if the sign-in page then says the user has no access, ask the owner. |
| HTTP 503, "The sign-in service can't be reached to confirm your access" (`idp_unavailable`) | The gateway checks with the owner's sign-in service before serving each request and couldn't reach it. Nothing was revoked; wait a minute and try again, and tell the owner if it persists. |

## Step 3: link Archidekt (optional, for the user's own decks)

Call `account_status`. If `linked` is false and the user wants to read or
edit their own Archidekt decks, give them the `account_page` URL it returns
(it is `https://<gateway>/account`). They sign in there in the browser and
link Archidekt once; the gateway keeps an encrypted session, never the
password. Research, public decks, pasted lists and CSV exports work without
this step.

When `writes_enabled` is false, the gateway owner has not switched deck
edits on: proposals can be made and reviewed but not applied. Say so if the
user asks to change a deck.

## Finish

Tell the user, in three short lines, what is now working: signed in as
whom (`whoami`), Archidekt linked or not (`account_status`), and that the
MTG skill (`/mtg-gateway:mtg-gateway`) describes how the assistant will
propose and apply deck changes. Suggest one first thing to try, such as
loading a public Archidekt deck by link with `get_deck`.

Other ways to use the same gateway (for the user's other apps):

- **Claude.ai, Claude Desktop, Claude iOS and Android:** add
  `https://<gateway>/mcp` once as a custom connector under Customize,
  Connectors (web or desktop); phones pick it up. The skill ZIP is at
  `https://<gateway>/skill` after signing in.
- **ChatGPT (web only):** Developer mode, add an app with the same URL;
  project instructions text is on the same `/skill` page.
- **Scanning physical cards:** `https://<gateway>/scan` on a phone.

The gateway's `https://<gateway>/install` page has the current steps for
every client.
