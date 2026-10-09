# MTG Assistant Gateway

**[Overview](#overview) · [Quick start](#quick-start) · [Documentation](#documentation) · [Features](#features) · [How it works](#how-it-works) · [Security](#security-model) · [Repository](#repository-layout) · [Development](#development) · [License](#license)**

A self-hosted [Model Context Protocol](https://modelcontextprotocol.io) (MCP)
server for Magic: The Gathering. You run it in Docker, on one machine with
Compose or on a Swarm, for you and whoever you invite. Each person adds one URL to Claude or ChatGPT and signs
in with their own account. From there their assistant can look up cards and
Commander data, goldfish a deck, and edit their own
[Archidekt](https://archidekt.com) decks. It never changes a deck until that
person has said yes to the exact change.

## Contents

- [Overview](#overview)
- [Quick start](#quick-start)
  - [Running a gateway](#running-a-gateway)
  - [Using a gateway someone gave you](#using-a-gateway-someone-gave-you)
- [Documentation](#documentation)
- [Features](#features)
  - [Status](#status)
- [How it works](#how-it-works)
- [Security model](#security-model)
- [Repository layout](#repository-layout)
- [Development](#development)
- [License](#license)

## Overview

- **Two kinds of people use it.**
  - The **operator** deploys it and decides who gets in.
  - **Users** connect their assistant and link their Archidekt account.
- **What it runs on:** two Docker containers (amd64 or arm64, so a Raspberry
  Pi is fine), behind any HTTPS reverse proxy, with any OpenID Connect
  identity provider handling sign-in.
  - **Deploy with** Docker Compose on one machine, or a Docker Swarm stack
    (Portainer optional).
  - **Tested end to end with** Swarm, Authentik and an nginx proxy in CI.
    Caddy, Traefik, nginx and Nginx Proxy Manager configs are included;
    Keycloak, Authelia, Zitadel, Pocket ID and others have setup notes but
    haven't been tested with the gateway yet.
  - The picture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Which assistants:** Claude (web, desktop, iOS, Android) and ChatGPT (web),
  plus any MCP client that does OAuth.
  What works where, and the limits, are in [docs/CONNECT.md](docs/CONNECT.md).
- **On a phone:** the pages work in any browser. There is also an Android app
  that each gateway can hand out itself (on its `/app` page, once the app file
  is in the image or added by the operator), adding a camera scan screen with torch
  brightness that lays itself out around a foldable's hinge
  ([docs/ANDROID.md](docs/ANDROID.md)).
- **What it talks to:** Archidekt for decks, Scryfall for cards, and a
  private [Mystic Forge](https://github.com/Kautiontape/mystic-forge)
  container for research and simulation. Mystic Forge pulls from EDHREC,
  Commander Spellbook and the Comprehensive Rules.
- **Accountability:** every tool call is tied to whoever signed in, and every
  deck change lands in an audit log.

## Quick start

### Running a gateway

You need a machine with Docker, a domain name, and an OpenID Connect
identity provider you control (Authentik, Keycloak, Authelia, ...).

1. Pick a guide and work through it top to bottom:
   - **One machine:** [docs/DEPLOY-COMPOSE.md](docs/DEPLOY-COMPOSE.md).
     Plain `docker compose`, with an optional Caddy that gets the HTTPS
     certificate for you.
   - **Docker Swarm, with or without Portainer:**
     [docs/DEPLOY.md](docs/DEPLOY.md).

   Both go in the order you'll need things: identity provider, secrets,
   settings, start, proxy, then a quick check that it all works. Identity
   provider details are in [docs/IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md)
   and [docs/IDP-OTHERS.md](docs/IDP-OTHERS.md); proxy details in
   [docs/REVERSE-PROXY.md](docs/REVERSE-PROXY.md).
2. Optional: if you use Claude Code, the `mtg-gateway-operator` plugin walks
   you through the same steps and generates the secrets for you. See
   [docs/PLUGIN.md](docs/PLUGIN.md#for-the-operator).
3. Keep [docs/OPERATIONS.md](docs/OPERATIONS.md) handy for running it:
   updates, backups, adding and removing people, and turning deck writes on
   or off.
4. To invite someone, give them an account in your identity provider, then
   send them your gateway's address and [docs/ONBOARDING.md](docs/ONBOARDING.md).

### Using a gateway someone gave you

1. Follow [docs/ONBOARDING.md](docs/ONBOARDING.md). It takes about ten
   minutes and all you need is a browser and the AI app you already use.
2. The quickest way in is the install page on the gateway itself:
   `https://<gateway>/install`. More on that in [docs/PLUGIN.md](docs/PLUGIN.md).

## Documentation

| Guide | For | What's in it |
| --- | --- | --- |
| [ONBOARDING.md](docs/ONBOARDING.md) | Users | Getting access, connecting your assistant, linking Archidekt, how edits get approved, privacy, leaving |
| [CONNECT.md](docs/CONNECT.md) | Users | Step-by-step connection for each Claude and ChatGPT app, which devices work, and fixes for sign-in errors |
| [PLUGIN.md](docs/PLUGIN.md) | Users and operators | The one-link plugin for Claude Code, Claude, ChatGPT and Codex, plus the operator plugin |
| [SKILL.md](docs/SKILL.md) | Users | The MTG skill and the ChatGPT instructions that teach the assistant the safe way to work |
| [API.md](docs/API.md) | Contributors and app builders | The JSON API and the companion pages: authentication, every endpoint, proposal kinds, the page routes the Android app wraps |
| [SCANNING.md](docs/SCANNING.md) | Users and operators | Turning a pile of physical cards into a decklist, a deck or your collection with your phone camera or a photo |
| [USING.md](docs/USING.md) | Users | What the pages do: home, decks, search, scan, collection, proposals and history, the guide; the same text the in-app Guide shows |
| [CAPABILITIES.md](docs/CAPABILITIES.md) | Everyone | The capability map: every assistant tool and who owns each job, every hand action on the pages and in the app, and how the two paths relate |
| [EXPORT-IMPORT.md](docs/EXPORT-IMPORT.md) | Users | Which decklist formats go where between archidekt.com, the gateway's pages, the app and the assistant, and what survives each trip |
| [ANDROID.md](docs/ANDROID.md) | Users and operators | The Android app: getting it from your gateway, the phone-camera scan screen, fold postures, App Links, building, signing and distributing it without an app store |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Everyone | How the pieces fit, what you can swap, how sign-in and deck edits work, the security model |
| [DEPLOY-COMPOSE.md](docs/DEPLOY-COMPOSE.md) | Operators | A full deploy on one machine with Docker Compose |
| [DEPLOY.md](docs/DEPLOY.md) | Operators | A full deploy on Docker Swarm (Portainer optional), plus every environment variable |
| [REVERSE-PROXY.md](docs/REVERSE-PROXY.md) | Operators | What the proxy must do, with Caddy, Traefik, nginx and Nginx Proxy Manager examples |
| [IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md) | Operators | Authentik, field by field: group, provider, application, binding, adding people, checks and troubleshooting |
| [IDP-OTHERS.md](docs/IDP-OTHERS.md) | Operators | The identity provider checklist, and notes for Keycloak, Authelia, Zitadel, Pocket ID, Kanidm, Entra ID and Google |
| [VERSIONS.md](docs/VERSIONS.md) | Operators | Following `latest` or staying on one version, what the version numbers mean, and how every update to `main` becomes a release |
| [OPERATIONS.md](docs/OPERATIONS.md) | Operators | Logs, updates, people, which AI clients may connect, backups, rotating secrets, revoking access, deck writes, the audit log |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Operators | Symptoms and fixes, from a container that won't start to a sign-in that fails |
| [THIRD-PARTY-NOTICES.md](docs/THIRD-PARTY-NOTICES.md) | Everyone | Licences of the components the gateway is built on |
| [tests/e2e/README.md](tests/e2e/README.md) | Contributors | The end-to-end test setup: a real Swarm stack, Authentik and a scripted client |

Screenshots of the browser pages are in [docs/screenshots/](docs/screenshots/).

## Features

- **Sign in.** Users sign in from Claude or ChatGPT with OAuth, through the
  operator's identity provider. The `whoami` tool shows who's signed in.
- **Research.** Card search, prices, rulings, the Comprehensive Rules,
  EDHREC, Commander Spellbook combos, precons, deck validation and goldfish
  simulation.
  - These come from Mystic Forge. The gateway only passes through tools on
    an allowlist, and always as the signed-in user. Mystic Forge tools that
    duplicate a gateway tool are hidden, so each job has exactly one tool
    (see [One tool per job](#one-tool-per-job)).
  - Mystic Forge features that keep per-user state (watchlists, its own
    saved reports, interactive games) stay hidden until the gateway can
    track who owns them. Saved reports are covered by the gateway's own
    deck reports below.
- **Deck import.** Three ways to read a deck. Counts leave out the
  maybeboard and sideboard.
  - `get_deck` reads any public or unlisted Archidekt deck from a link
    (as plain text, statistics only, card rows, Archidekt import text or
    everything: its `view`);
  - `parse_decklist` reads a pasted list (lines that are not cards, such as
    a `Total: 100` footer, are skipped and listed back);
  - `parse_deck_export` reads an Archidekt CSV export.
- **Your decks.** Link your Archidekt account once on the `/account` page.
  After that, `list_my_decks` (filter by name, format or folder) lists your
  decks and `get_deck` reads them, private ones included (its `owner` says
  whose deck it is). The gateway
  refreshes the stored Archidekt session on its own when it is about to
  expire or Archidekt rejects it, so a link lasts until Archidekt refuses
  the refresh too; each refresh is audited.
- **Deck statistics and bracket estimate.** `deck_stats` computes the mana
  curve, colour pips against mana sources, types, rarities, lands, average
  mana value, price total, format legality problems, salt, game changers,
  tutors, extra turns and mass land denial from Archidekt's own card data,
  plus a Commander bracket estimate (2 to 4) from those flags and the
  structural checks (deck size for the format, commander zone, colour
  identity, singleton rule, uncategorised rows). It is an
  estimate, not an official bracket, and the tools say so.
- **Deck reports and history.** `run_deck_report` stores the statistics
  together with a decklist validation and a goldfish simulation (when Mystic
  Forge is up; the simulator's options such as annotations, seed, turns,
  opponents and mulligan rules pass through) so a deck's numbers can be followed over time with
  `list_deck_reports` and `get_deck_report`, or on the `/history` page next
  to the deck's proposals and snapshots.
- **Compare decks.** `compare_decks` lists the cards added, removed and
  changed between any two of: an Archidekt deck, a snapshot
  (`get_snapshot` shows one in full) or a pasted list, with the change in
  the statistics, a precon-style summary (cut and added percentages, basic
  lands apart) and, on request, a paired goldfish A/B of the two.
- **Safe writes.** Every edit starts as a proposal:
  - `propose_deck_changes` (edit a deck), `propose_new_deck` (build one
    from a card list, a pasted list or a CSV; it warns about a deck size
    the format does not allow) and `propose_deck_details` (name,
    description, format, bracket, privacy, folder, tags, cover) save the
    exact diff and a review link.
  - The user approves it: on the card the AI app shows next to the
    proposal (Claude on the web, desktop and phones; ChatGPT), with the
    Apply button on the review page, or, when the member chose a Semi-auto
    or Full-auto approval mode on their Account page, the assistant applies
    it with `apply_proposal`. The card's Approve button carries a one-time code
    the assistant never sees, so a tricked assistant can't press it.
  - For an edit, the gateway checks the deck hasn't changed since the proposal, saves a
    snapshot, puts a private backup copy of the deck in the user's
    "MTG Gateway backups" folder on Archidekt, makes the change, then reads
    the deck back to confirm it.
  - Undo is the same flow in reverse: `list_snapshots`, then
    `propose_restore_snapshot`, which puts every card back as it was
    (printing, foil, quantity, categories, commander, sideboard and
    maybeboard).
  - A pending proposal the user no longer wants is closed with
    `reject_proposal` or the Reject button.
  - Every step is audited and limited to the signed-in user.
- **Companion pages.** A home page with the full navigation, `/decks`,
  `/search` (public decks on Archidekt by name, commander, format, colours
  or owner, and `/users/{name}` for one person's decks), `/collection`,
  `/scan`, `/history`, `/activity` and `/guide` are a phone-friendly deck
  view and editor behind the same sign-in: browse, search and open decks as
  text, stacks or grid (touch works: tap to fan a stack, press and hold to
  move a card between categories), see statistics, run a report, edit
  quantities, categories and additions (your own saves go straight to
  Archidekt, with a snapshot first), and look back over
  proposals, snapshots and reports. They are also the pages the Android app
  wraps.
  - Everything Archidekt's own deck page offers is there for your own decks:
    the editor also edits maybeboard and sideboard rows and takes a pasted
    list; the settings page sets the cover image, deck tags and folder;
    `/folders` creates and renames folders; Delete deck asks you to type the
    deck's name and keeps a snapshot (and the Archidekt backup copy) first.
    `/precons` lists every preconstructed deck Archidekt knows, by set.
    The assistant reaches folder, tags and cover only through a
    `propose_deck_details` proposal you approve; deleting a deck and
    creating or renaming folders are your own buttons alone.
- **Collection.** The cards you own, which is your Collection on Archidekt
  shown and edited through the gateway (nothing about them is stored here):
  add by name or from a scan, count copies, set a card's finish, condition,
  language and price paid, filter, export CSV. Owned cards
  get a green dot on every deck page. `list_collection` and
  `propose_collection_changes` (a proposal, approved like a deck edit, on
  the in-chat card too, and read back after it is applied) give the
  assistant the same.
- **Likes, bookmarks, follows and comments.** Archidekt's social buttons on
  every deck and user page, each behind a confirmation and sent under your
  own Archidekt name; your own comments can be edited and deleted. Browser-only
  by design: no tool can do any of it.
- **Adaptive layout.** Bottom tab bar on phones, a navigation rail from
  600 px on touch screens and in the Android app, the desktop bar in wider
  browsers; the layout follows the live window, so a foldable, split screen
  or the browser's "Desktop site" switch re-flows at once.
- **Profile pictures.** The account icon shows each member's picture from
  the identity provider (Gravatar, or a picture uploaded to Authentik),
  fetched by the gateway, or their initials. See
  [docs/IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md#profile-pictures).
- **Admin page.** Members of `MTG_ADMIN_GROUP` get `/admin` (they don't
  also need `MTG_REQUIRED_GROUP`): who has signed
  in, their activity, per-day usage counts, a System card (version, database
  size and schema, newest backup and newest backup copy, the research
  service's state), and buttons to disable or enable an
  account, revoke its tokens and sessions, unlink Archidekt, or delete
  everything the gateway keeps about a person. Unset, the page does not
  exist. Details in
  [docs/OPERATIONS.md](docs/OPERATIONS.md#the-admin-page).
- **JSON API.** Everything the tools and pages do is also under `/api/v1`
  for app builders, with the same sign-in and the same proposal flow. See
  [docs/API.md](docs/API.md).
- **Cards in the chat.** Where a person has to pick or press, the tool result
  comes with an interactive card (MCP Apps; Claude on the web, desktop and
  phones, ChatGPT as reported): the printings of a card as pictures to tap,
  the cards read from photos or a list to keep or drop, a deck by category
  with pictures and rules text, the account's setup, and the proposal with
  Approve and Reject. A pick on a card goes back to the assistant as plain
  text; it never changes anything by itself. Bulky data reaches the card
  through a ten-minute signed link, not through the model.
- **Card scanning.** The `/scan` page reads physical cards with your phone
  camera. Text recognition runs on the phone, no third-party app needed.
  The scanned list goes into your collection, into one of your decks or
  into a new deck. `resolve_cards` turns card names read from photos into
  exact cards.
- **One-link setup.** The gateway serves its own assistant plugin and an
  install page at `/install`.


### One tool per job

No two assistant tools offer the same capability. Where Mystic Forge has a
tool that does what a gateway tool already does, the Mystic Forge one is
hidden (an assistant that calls it is told which tool owns the job), so an
assistant never has to guess which one to use:

| Job | The one tool | Hidden duplicates |
| --- | --- | --- |
| Read any Archidekt deck, the member's own private ones included (rules text on request), or export it in Archidekt's import syntax | `get_deck` | `archidekt_deck`, `archidekt_export` |
| List the signed-in member's decks | `list_my_decks` | |
| Search public decks, and list one user's public decks (`owner`) | `search_decks` | `archidekt_user_decks` |
| Change a deck's settings, folder, tags or cover | `propose_deck_details` | |
| Legality, structural checks, bracket, curve, colours and price of a deck | `deck_stats` | `validate_archidekt_deck` |
| Legality of a pasted list that is not a deck yet | `validate_decklist` | |
| Cuts and adds between two decks, a precon included, with the summary and basics apart | `compare_decks` | `precon_diff` |
| Paired goldfish A/B of two decks (same seeds game for game, deltas with confidence intervals) | `compare_decks` with `simulate` | `goldfish_ab` |
| Goldfish simulation of one deck, stored, with the simulator's options | `run_deck_report` | `goldfish_run` |
| Draw odds without a simulation, and what the engine models | `goldfish_odds`, `goldfish_annotate` | |
| Card names from photos or text to exact printings | `resolve_cards` | |

A test pins the hidden list, and the tool descriptions say who owns what.

### Status

The gateway is in testing: versions below 1.0.0 can still change in small ways
between releases ([docs/VERSIONS.md](docs/VERSIONS.md)).

| Area | State |
| --- | --- |
| Sign-in, research, deck import, account linking, proposals, scanning | Built. Tested against fakes, recorded Archidekt and Scryfall responses, and a real Swarm stack with Authentik in CI |
| Applying edits, creating decks, backups and restores on Archidekt | Built, and run live against a throwaway Archidekt account (create, add, remove, quantities, categories, commander, backup folder and copy). The code default is off (`MTG_WRITES_ENABLED=false`); the example env files turn writes on (each change is still applied only by the member's own press, the Approve button on the in-chat card or the Apply button on the review page, unless the member chose a looser approval mode on their Account page). Try your first edit on a deck you don't care about |
| Client and device coverage | See [docs/CONNECT.md](docs/CONNECT.md#which-apps-and-devices-work), which labels each claim as verified, reported or unverified |
| Deck statistics, stored deck reports and history, compare, companion pages, admin page, JSON API | Built and covered by the test suite against fakes. The companion pages and admin page have not yet had the same live Swarm run-through as the rest; treat that as unverified |
| Deck deletion, cover image, folders, tags, comment editing, collection details (0.7.2) | Built against Archidekt routes read from its own site code and covered by tests against a fake; not yet exercised live. Each one reads the result back and reports a mismatch rather than trusting Archidekt's answer |
| Watchlists, price history | Not yet (Mystic Forge's own saved goldfish reports stay hidden too; the gateway's stored deck reports replace them) |

## How it works

The full picture, with diagrams, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). In short:

```
Claude / ChatGPT / browser ──HTTPS──▶ reverse proxy ──▶ mtg-gateway ──internal network──▶ Mystic Forge
                                                          ├─ OAuth 2.1 authorization server for MCP clients
                                                          │    (client registration or metadata documents, PKCE, refresh, revoke)
                                                          ├─ login handed off to your identity provider (OIDC)
                                                          ├─ MCP endpoint at /mcp; JSON API at /api/v1
                                                          ├─ browser pages /account, /proposals, /scan, /decks, /history, /activity, /admin, /install
                                                          ├─ Archidekt adapter (paced, per-user sessions) ──▶ archidekt.com
                                                          └─ SQLite on local disk, nightly backup export
```

- **The gateway is its own OAuth server.**
  - An AI client either registers itself, or identifies itself with a
    Client ID Metadata Document URL. The gateway fetches that document under
    tight network rules.
  - The client sends the user to `/authorize`. If the client identified
    itself with a metadata document, the user first sees a page naming the
    client and where the sign-in returns to.
  - The gateway then sends the browser to your identity provider and, once
    that's done, hands the client its own opaque tokens.
- **One identity per person.** A single confidential OIDC client at your
  identity provider covers every AI client, so each person is the same user
  across Claude, ChatGPT and the browser pages.
- **No site details in the code.** Everything deployment-specific comes from
  environment variables and Docker secrets.

## Security model

- **Who can sign in** is decided in your identity provider, through the
  application binding and groups. The gateway also requires a specific
  group (`MTG_REQUIRED_GROUP`) and refuses to start without one unless
  you opt out with `MTG_ALLOW_ANY_IDP_USER=true`.
- **Membership is checked live.** Before serving a request, the gateway asks
  the identity provider whether the person is still in the group (cached
  for a few seconds, `MTG_MEMBERSHIP_CHECK_TTL`). Take someone out of the
  group, or deactivate them, and their next request fails: their tokens and
  sessions are revoked, and a removal from the group revokes their
  Archidekt link too (at that next request; press **Disable** on the admin
  page as well to delete it at once,
  [docs/OPERATIONS.md](docs/OPERATIONS.md#revoking-access)). If the provider can't be reached, requests are
  refused rather than let through. With Authentik
  this needs the `offline_access` scope mapping
  ([docs/IDP-AUTHENTIK.md](docs/IDP-AUTHENTIK.md)).
- **Sign out on all my devices** (on the `/logout` page) ends every
  browser and app session, and for an hour the next sign-in on that device
  asks for the password again.
- **Tokens:** `/mcp` only accepts tokens the gateway issued itself, so a
  token straight from the identity provider is rejected.
  - Tokens and registered clients' secrets are stored hashed.
  - Refresh tokens rotate, and reusing an old one kills the whole chain.
  - Authorization codes are single-use and tied to PKCE.
  - Clients can only register `https` return addresses, or `http` on
    localhost.
- **Archidekt passwords** are used once to get a session and never stored.
  - The session is encrypted with a key that lives in a Docker secret, and
    no page, admin tool or log shows it. Backups leave it out altogether
    (and the identity provider's tokens too), so after a restore members
    sign in again and relink Archidekt.
  - That doesn't protect against whoever runs the server, who holds the
    key; users are told so on the Account page, and tick a box before
    linking.
- **Deck changes** need the user's own yes. Text found in decks or tool
  output is never treated as an instruction to edit. Each proposal shows
  which app made it, and disconnecting an app rejects its pending
  proposals. An app connected with the read-only scope `mtg.read` can't
  change anything.
- **Limits.** Sign-in attempts are limited per network, metadata-document
  fetches per site, and Archidekt actions per member
  (`MTG_ARCHIDEKT_CALLS_PER_10_MIN`), so neither a flood nor a looping
  assistant can wear the gateway or Archidekt down. Archidekt requests from
  everyone together are spaced out and capped per minute, repeat reads are
  briefly cached, and "slow down" answers are honoured.
- **Mystic Forge** has no published ports and no login of its own. Only the
  gateway can reach it.

## Repository layout

| Path | What's there |
| --- | --- |
| `src/mtg_gateway/` | Application code |
| `tests/` | Tests against a fake identity provider, a fake Archidekt and recorded live responses; `tests/e2e/` runs a real Swarm stack |
| `plugin/`, `.claude-plugin/` | The assistant plugins (for users and for operators) and the repository's plugin marketplace. `plugin/mtg-gateway/` holds the MTG skill and the ChatGPT instructions; the gateway serves that plugin at `/plugin/` and its skill at `/skill` |
| `Dockerfile`, `docker/` | Gateway and Mystic Forge container images (multi-arch, run as `PUID:PGID`) |
| `deploy/` | The Swarm stack file and its example settings; `deploy/compose/` for plain Docker Compose; `deploy/proxy/` with Caddy, Traefik and nginx examples |
| `android/` | The Android app (Kotlin, CameraX, WindowManager) as a Gradle project with a build script that fetches the SDK; see [docs/ANDROID.md](docs/ANDROID.md) |
| `docs/` | The guides listed under [Documentation](#documentation), plus screenshots |
| `.github/workflows/` | Tests, end-to-end tests, container smoke test, and the release on every merge to `main` (tag, image on GHCR, Android app, GitHub release; see [docs/VERSIONS.md](docs/VERSIONS.md)) |

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -c constraints.txt -e ".[dev]"
ruff check src tests scripts && ruff format --check src tests scripts && pytest -q
```

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[code of conduct](CODE_OF_CONDUCT.md). Please report security problems
privately, as described in [SECURITY.md](SECURITY.md). Notable changes are
in the [CHANGELOG](CHANGELOG.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE): use it, change it and share it for
anything noncommercial. Mystic Forge is a separate project under its own MIT
licence. Everything else the gateway is built on is listed in
[docs/THIRD-PARTY-NOTICES.md](docs/THIRD-PARTY-NOTICES.md).
