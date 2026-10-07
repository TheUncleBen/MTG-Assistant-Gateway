# Running the gateway

Day-to-day stuff for whoever runs the gateway. Commands run on a Swarm
manager unless it says otherwise. Service names assume the stack is called
`mtg`:

| Service | Swarm name | Compose name |
| --- | --- | --- |
| Gateway | `mtg_mtg-assistant-gateway` | `gateway` |
| Mystic Forge (internal research service) | `mtg_mtg-assistant-mysticforge` | `mysticforge` |

**Running with Docker Compose?** The same things, from the folder with
`docker-compose.yml`:

| Swarm command below | Compose equivalent |
| --- | --- |
| `docker service logs -f mtg_mtg-assistant-gateway` | `docker compose logs -f gateway` |
| `docker service update --force mtg_mtg-assistant-gateway` | `docker compose restart gateway` |
| Update to a new release | change `MTG_TAG` in `.env`, then `docker compose pull && docker compose up -d` |
| `docker exec --user 1000:1000 $(docker ps -q -f name=mtg_mtg-assistant-gateway) <command>` | `docker compose exec --user 1000:1000 gateway <command>` |
| Swarm secrets | files in `secrets/`; edit the file, then `docker compose up -d --force-recreate gateway` |
| Stack variables in Portainer | `.env`, then `docker compose up -d` |

Commands that open the database run on the node where the gateway container
is running (the one in `MTG_NODE`). They use `--user 1000:1000`; swap in
your `PUID:PGID` if it's different, so any files they create stay owned by
the gateway's user.

## Contents

- [Logs and health](#logs-and-health)
- [Restart or update](#restart-or-update)
- [Adding and removing people](#adding-and-removing-people)
- [Which AI clients may connect](#which-ai-clients-may-connect)
- [The assistant skill page](#the-assistant-skill-page)
- [Backups](#backups)
- [Rotating secrets](#rotating-secrets)
- [Revoking access](#revoking-access)
- [Archidekt links and relinking](#archidekt-links-and-relinking)
- [Deck writes and the kill switch](#deck-writes-and-the-kill-switch)
- [The admin page](#the-admin-page)
- [Looking at proposals, snapshots, links and the audit log](#looking-at-proposals-snapshots-links-and-the-audit-log)

## Logs and health

```bash
docker service ps mtg_mtg-assistant-gateway mtg_mtg-assistant-mysticforge
docker service logs -f mtg_mtg-assistant-gateway
docker service logs -f mtg_mtg-assistant-mysticforge
curl -s https://mtg.example.com/healthz
```

`/healthz` returns `{"status":"ok","version":"..."}` when the gateway is up
and its database answers, and HTTP 503 with `"detail":"database unavailable"`
otherwise (the reason is in the gateway's log, never in the reply). The admin
page's **System** card shows the version, database size and schema, and when
the newest backup was written.

The gateway logs one line per notable event to standard output (`MTG_LOG_LEVEL`,
default `INFO`). An unexpected error is logged with its full traceback, counted
under "server_error" on the admin page, and shown to the person only as a plain
"Something went wrong" page or JSON message. Outbound request URLs are logged
only at `DEBUG`. Docker keeps at most three 10 MB log files per container
(the `logging:` block in the stack and compose files), so logs can't fill a
small disk; raise `max-size` there if you want more history. Nothing is sent
to any outside error-tracking or analytics service.

If research tools answer "the research service is unavailable right now",
the gateway is fine but can't reach Mystic Forge. Check that service and its
logs.

## Restart or update

In Portainer: **Stacks** → `mtg` → **Update the stack**, with "Re-pull
image" ticked. With `MTG_TAG=latest` that pulls the newest version; with a
pinned version (for example `0.6.6`), change `MTG_TAG` first
([VERSIONS.md](VERSIONS.md)). Or from the command line:

```bash
docker service update --force mtg_mtg-assistant-gateway       # restart
docker service update --image ghcr.io/<owner>/mtg-assistant-gateway:<tag> mtg_mtg-assistant-gateway
```

Updates are `stop-first`, so expect a few seconds of downtime. On a stop the
gateway finishes requests already running (such as a deck apply) for up to
100 seconds; the stack gives it 120 (`stop_grace_period`) before Docker kills
it. An apply that a crash or a kill still cuts off is marked failed when the
gateway starts again, with a pointer to the snapshot taken before it. A failed
update is not rolled back automatically on purpose: a new version may have
upgraded the database, and the old image refuses to start on it (below).
Signed-in
assistants keep working because tokens live in the database on disk. Linked
Archidekt accounts and proposals survive restarts too. (One exception: the
first start of 0.6.1 or newer after an older version signs everyone out once,
because sessions from before have no identity-provider tokens on file for
the live membership check. See the CHANGELOG.)

Before moving to a new release, read the [CHANGELOG](../CHANGELOG.md) and
take a backup ([Backups](#backups)). A release can upgrade the database on
start; to go back, restore that backup and run the old image on it, never the
old image on the upgraded file. An older image refuses to start on a database
a newer one upgraded (`database schema version ... is newer than this gateway
supports`); only the very first release, 0.1.0, had no such check.

## Adding and removing people

People are managed in Authentik, not in the gateway. To add someone, put
their Authentik account in the group bound to the MTG Assistant Gateway application
and send them [ONBOARDING.md](ONBOARDING.md). Step by step in
[IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#8-add-people-accounts-invitations-groups).
To remove someone, see [Revoking access](#revoking-access).

## Which AI clients may connect

Who can sign in is decided in Authentik. Separately, the gateway accepts two
kinds of AI client:

- **Clients that register themselves** (Dynamic Client Registration, for
  example Claude's "Register automatically"). Always on.
- **Clients that identify themselves with a published description** (a
  Client ID Metadata Document, for example Claude's "Use Claude's published
  identity"). The client's id is an `https` web address. The gateway fetches
  the JSON document there, checks it, and caches it for somewhere between
  five minutes and a day, depending on the document's `Cache-Control`
  header.

Every sign-in from an AI client, of either kind, first shows the gateway's
**Connect an application** page. It names the client and the host the sign-in
returns to, and Authentik opens only after **Approve**. Authentik can't tell
AI clients apart (they all reach it through the gateway's one OIDC client), so
without this page anyone could register a client of their own and send a
signed-in member a link that hands them that member's access.

Fetching an address a client hands you is risky, so it's locked down:
`https` on port 443 only, a hostname rather than an IP address, every DNS
answer has to be a public address, and the connection goes to the address
that was checked (the certificate is still verified against the hostname).
Redirects aren't followed, one five-second deadline covers the whole fetch
(including waiting for a slot), documents over 64 KB are refused, at most
eight fetches run at once (one per site), and an address that failed isn't tried again for a
minute. Only public clients are accepted (`token_endpoint_auth_method`
`none`).

So that nobody can use the gateway to send requests to someone else's site,
a client address with a query string (`?...`) is refused, a failed fetch
blocks every other new address on that host for a minute, and requests that
actually go out are limited to 10 a minute per site and 60 a minute in all.
A "site" is the registrable domain (`example.com`, `example.co.uk`), so
subdomains share one budget. Only a request that is about to be sent counts:
an address refused without one (not on the allowlist, an IP address, a name
that doesn't resolve or resolves to a private address) uses up nothing.

A client that a member has completed a sign-in through (remembered for 180
days after its last sign-in) is never held back by those limits: its
document is fetched in a lane of its own (its own fetch slots and DNS
threads) that first-time addresses can't use or fill, and while its server
can't be reached (or answers 5xx, 408 or 429) the last good copy is used
for up to a week past its expiry (never once
the server itself has withdrawn or changed the document to one that isn't
accepted). So once Claude or ChatGPT has connected here, nobody can lock
it out by pointing junk addresses at the gateway. A host on
`MTG_CIMD_ALLOWED_HOSTS` isn't blocked after a failure, but its new
addresses still count against the budgets. A name whose DNS doesn't answer
within 2 seconds counts as failed. A client connecting for the very first
time can still be delayed by someone who keeps the fetcher busy with junk
addresses (on an open gateway, on several domains of their own; with an
allowlist, on the allowed hosts); it connects once that stops.

What one document may store is capped like a self-registered client (at
most 20 redirect URIs of up to 2000 characters, 8 KB in all), and the cache
keeps at most 1000 documents, at most 50 per site; when it is full, the site
with the most cached documents loses its oldest one first. Clients a member
has signed in through are never removed by these caps.

Two optional stack variables control this:

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_CIMD_ENABLED` | `true` | `false` turns published descriptions off; only self-registering clients can connect. |
| `MTG_CIMD_ALLOWED_HOSTS` | empty: any public `https` host | Comma-separated hostnames whose documents are accepted. Subdomains count too, so `example.com` also allows `docs.example.com`. A client whose document lives anywhere else can't connect with its published identity (it can still register itself). |

**Our advice: leave `MTG_CIMD_ALLOWED_HOSTS` empty.** The tradeoff is that
anyone who can reach `/authorize` can make the gateway fetch a document from
any public host, under the limits above. Narrowing the list closes that, but
you have to include every client you use. Claude and ChatGPT each have their
own host (OpenAI says ChatGPT also uses a published identity, reported), so
listing only one locks the other out, and neither hostname has been
confirmed for this guide. If you do want to narrow it, sign in once from
each client first, then read its host off the confirmation page ("identified
by ...") or the audit log, where each document is recorded as
`cimd_accepted` or `cimd_rejected` with its address:

```sql
SELECT datetime(at, 'unixepoch') AS when_utc, event, client_id, detail_json FROM audit_log
WHERE event IN ('cimd_accepted', 'cimd_rejected') ORDER BY at DESC LIMIT 20;
```

(How to run SQL like this is in
[Looking at proposals, snapshots, links and the audit log](#looking-at-proposals-snapshots-links-and-the-audit-log).)

Change either variable in Portainer (**Stacks** → `mtg` → environment
variables) and update the stack.

## The assistant skill page

Signed-in people download the Claude skill and copy the ChatGPT instructions
at `https://mtg.example.com/skill`. The files are part of the assistant
plugin, `plugin/mtg-gateway/` in the repository
(`skills/mtg-gateway/SKILL.md` and `chatgpt-instructions.md`). The image
copies that to `/usr/share/mtg-gateway/plugin`, so a new image brings new
skill text and a new plugin archive at `/plugin/mtg-gateway.zip`.

To serve your own edited copy without rebuilding, mount a folder with the
same layout (`mtg-gateway/` inside it) and point `MTG_PLUGIN_DIR` at it
(add it under the gateway's `environment:` in the stack file). The `/skill`
page, `/install` and the plugin marketplace all read from there.

## Backups

**What's backed up:** the gateway's SQLite database. It holds users
(identity provider subject, name, email, groups, disabled flag), registered
OAuth clients, hashed tokens, the identity provider's tokens kept for the
live membership check and Archidekt links (both encrypted with the Fernet
key), proposals, deck snapshots, deck reports, scan sessions, usage
counters and the audit log. Mystic Forge
keeps nothing worth backing up.

**What isn't:** the Fernet key. It's a Docker secret and stays out of the
backup on purpose. Without it, a restored database still works for sign-in,
proposals and the audit log, but every Archidekt link is unreadable and
everyone has to relink (and sign in once more, since the stored
identity-provider tokens are unreadable too). Keep your copy of the key with your other
credentials.

Separately, every applied edit also leaves a private backup copy of the deck
in the user's own Archidekt account (see
[Deck writes](#deck-writes-and-the-kill-switch)). Those live on Archidekt,
not in your backups.

- **Nightly:** at `MTG_BACKUP_HOUR_UTC` the gateway writes
  `mtg-gateway-<timestamp>.sqlite` into `MTG_BACKUP_DIR` and deletes copies
  older than `MTG_BACKUP_KEEP_DAYS`. Each copy is a standalone file (no
  `-wal` beside it) that passed SQLite's `quick_check` before it was kept; a
  copy that fails is not kept, the failure is logged and the admin page says
  so. The copy is read through its own connection, so the gateway keeps
  answering while it runs.
- **Back up right now:**

  ```bash
  docker exec --user 1000:1000 $(docker ps -q -f name=mtg_mtg-assistant-gateway) mtg-gateway backup
  ```

- **Restore:** stop the stack, replace `<MTG_DATA_DIR>/mtg-gateway.sqlite`
  with a backup copy (delete any `-wal` and `-shm` files next to it), start
  the stack.

  A restore also undoes every revocation made after the backup was taken:
  tokens you revoked, browser sessions people logged out of, and Archidekt
  accounts that were unlinked come back, and accounts you disabled on the
  admin page are enabled again (disable them again after the restore). Before you start the stack again,
  end every session in the restored file so everyone signs in once more
  (which re-checks their Authentik groups). On the gateway's node, as the
  `PUID` user (`1000` here):

  ```bash
  sudo -u '#1000' python3 -c "import sqlite3; c=sqlite3.connect('<MTG_DATA_DIR>/mtg-gateway.sqlite'); \
  print(c.execute('UPDATE tokens SET revoked=1').rowcount, 'tokens'); \
  print(c.execute('DELETE FROM browser_sessions').rowcount, 'browser sessions'); \
  c.execute('DELETE FROM auth_codes'); c.execute('DELETE FROM login_sessions'); c.commit(); c.close()"
  ```

  Everyone then reconnects their assistant and signs in to `/account` again.
  If you unlinked anyone's Archidekt account after the backup was taken,
  unlink it again ([Archidekt links](#archidekt-links-and-relinking)).

## Rotating secrets

Swarm secrets can't be edited. To rotate one, create a new secret under a
new name, point the stack's `secrets:` entry at it (map it to the old name
inside the container, as in
[IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#7-gateway-settings-and-the-docker-secret),
or change the matching `*_FILE` variable), redeploy, then remove the old
secret.

| Secret | What happens when you rotate it |
| --- | --- |
| `mtg_session_secret` | Safe any time. Sign-ins in progress fail once, and anyone with `/account` or a review page open has to reload it. |
| `mtg_oidc_client_secret` | Rotate it together with the client secret on the Authentik provider. |
| `mtg_fernet_key` | Every stored Archidekt session becomes unreadable, so everyone has to relink at `/account`. The identity-provider tokens kept for the live membership check become unreadable too, so everyone is signed out once on their next request and signs in again (their AI apps reconnect once). Proposals aren't affected. |

## Revoking access

1. Take the person out of the `MTG_REQUIRED_GROUP` group in Authentik, or
   deactivate or delete their Authentik user. That's enough.
2. The gateway notices on their next request. Before serving any request
   that carries a browser session or a gateway token, it asks Authentik's
   userinfo endpoint for the person's current groups (the answer is cached
   for `MTG_MEMBERSHIP_CHECK_TTL` seconds, 5 by default, so that's the worst
   case). Someone taken out of the group loses every gateway token, every
   browser session (web pages and Android app), the Authentik tokens the
   gateway kept, and their Archidekt link, all at once; the audit log gets
   a `membership_revoked` row with the reason `not_in_group`. A deactivated
   or deleted user shows up only as a refused token, so they lose every
   token and session but keep their Archidekt link (an admin can remove it
   with **Delete data**); the audit log gets a `membership_unverifiable` row
   with the reason (`idp_refused_refresh`, `idp_refused_userinfo` and so
   on). A new sign-in then fails at Authentik.

   Taking someone out of `MTG_ADMIN_GROUP` works the same way: the admin
   page is gone on their next request.

   If Authentik can't be reached, the gateway refuses requests with a 503
   ("The sign-in service can't be reached to confirm your access") and
   revokes nothing; everything works again once Authentik answers. If
   Authentik answers but has no refresh token on file for someone (the
   `offline_access` scope mapping is missing, see
   [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#4-create-the-oauth2openid-provider)),
   that person's gateway tokens and sessions are revoked once Authentik's
   access token runs out, and they sign in again (audit row
   `membership_unverifiable`).

   You can also revoke someone's tokens and sessions yourself, without
   touching Authentik: the admin page's **Revoke tokens and sessions** or
   **Disable** ([The admin page](#the-admin-page)), or by hand:

   ```bash
   docker exec -it --user 1000:1000 $(docker ps -q -f name=mtg_mtg-assistant-gateway) \
     python -c "import sqlite3; c=sqlite3.connect('/data/mtg-gateway.sqlite'); \
     sub=c.execute('SELECT sub FROM users WHERE email=?', ('person@example.com',)).fetchone()[0]; \
     print(c.execute('UPDATE tokens SET revoked=1 WHERE sub=?', (sub,)).rowcount, 'tokens'); \
     print(c.execute('DELETE FROM browser_sessions WHERE sub=?', (sub,)).rowcount, 'browser sessions'); \
     c.commit()"
   ```

3. If you cut someone off on the gateway only (not in Authentik), unlink
   their Archidekt account too if you want (next section). A removal in
   Authentik already did that.

Registered AI clients (one per connector someone added) that never finished
a sign-in, or whose tokens have all expired and been cleared out, get
deleted by the hourly cleanup after seven days. Nothing to do by hand; the
client registers again next time someone connects.

The same cleanup bounds what anyone on the internet can fill the database
with: audit log entries older than a year are deleted, the anonymous ones
(client registrations and client-metadata fetches) are also capped at the
newest 5000 and keep only a short summary, unfinished sign-ins are capped
at 5000 (expired ones are also dropped whenever a new sign-in starts), and
unused registered clients at the newest 2000.

Unfinished sign-ins are the part a flood could use against people signing in
at the same time, so they are capped more carefully: at most 10 per browser,
500 per AI client (the web pages count as one client), and 5000 in all, and
when a cap is reached the network (IP address, or /64 for IPv6) holding the
most unfinished sign-ins loses its oldest one. Someone hammering `/login` or
`/authorize` therefore only pushes out their own sign-ins. Each network may
also start at most 30 sign-ins a minute; the 31st gets a "too many sign-in
attempts" page. This depends on the gateway seeing each visitor's address:
it takes it from `X-Forwarded-For` only when the request comes from an
address in `MTG_TRUSTED_PROXIES`. The rate limit applies only to public
addresses. A request from a private, loopback, link-local, CGNAT
(`100.64.0.0/10`, which Tailscale uses) or IPv6 ULA address, or from an
address in `MTG_TRUSTED_PROXIES`, isn't rate-limited: such an address is
usually your reverse proxy, and if the gateway doesn't trust it, every
visitor arrives from it and shares one limit, so anyone could block every
sign-in. The per-browser and per-client caps still apply to them, and all
those visitors count as one network. When 50 different browsers have started
sign-ins from one address, the gateway logs a warning (once per address)
naming it and suggesting you add it to `MTG_TRUSTED_PROXIES`. A reverse
proxy on a public address must be in `MTG_TRUSTED_PROXIES`, or every visitor
shares its 30-a-minute limit and anyone can use it up. The cleanup runs
at start and then every hour, with or without backups. It also keeps the
newest 25 snapshots of each member's deck (plus any a pending restore needs),
usage counters for 400 days and remembered deck covers for 180 days. Closed
proposals (expired, rejected, failed) go after 30 days, applied ones after a
year. Snapshots and reports stay with the member, not with the Archidekt
account: after unlinking and linking another Archidekt account, the member
still sees the snapshots and reports taken before (they are the member's own
backups); they age out with the limits above or go with **Delete my data**.
"Delete my data" removes a member's rows from the live database; the nightly
backups in `MTG_BACKUP_DIR` keep a copy until they age out after
`MTG_BACKUP_KEEP_DAYS`, including the member's encrypted Archidekt session. A flood of sign-up attempts can therefore cost a connector that was
registered but not used yet; that client just registers again. To slow
floods down at the proxy, see the optional rate limit in
[DEPLOY.md](DEPLOY.md#7-reverse-proxy).

## Archidekt links and relinking

Users link and unlink themselves on `/account`. A link stops working when
Archidekt stops accepting the stored session (the gateway marks it revoked
and tells the user to relink), when you rotate `mtg_fernet_key`, or when
someone unlinks. The fix is always the same: the user signs in at `/account`
and links again. The gateway never sees or stores the Archidekt password
beyond that one login.

To unlink someone yourself (deletes the stored session, same as their
button):

```bash
docker exec -it --user 1000:1000 $(docker ps -q -f name=mtg_mtg-assistant-gateway) \
  python -c "import sqlite3; c=sqlite3.connect('/data/mtg-gateway.sqlite'); \
  print(c.execute(\"UPDATE archidekt_links SET status='revoked', secret_enc='' WHERE sub=(SELECT sub FROM users WHERE email=?)\", ('person@example.com',)).rowcount); c.commit()"
```

That doesn't end the session on Archidekt's side, and whether Archidekt
offers a way to do that for API sessions is unknown. If they want the old
session dead for sure, they should change their Archidekt password.

## Deck writes and the kill switch

The gateway only writes to someone's Archidekt account in two steps:

1. `propose_deck_changes` (edit a deck), `propose_new_deck` (create one) or
   `propose_restore_snapshot` (undo an edit) saves a proposal with the exact
   diff.
2. The user approves it: with Approve on the card their AI app shows next
   to the proposal (Claude, ChatGPT; `MTG_APPLY_IN_CHAT`) or with the Apply
   button on `/proposals/<id>`. A user who chose a looser approval mode on
   their Account page lets the assistant apply low-risk edits (semi) or
   everything (auto) itself with `apply_proposal`; see below.

Applying an edit or restore:

- re-reads the deck and refuses if it changed since the proposal;
- saves a snapshot in the gateway's database;
- copies the deck into the user's "MTG Gateway backups" folder on Archidekt
  as a private deck (`MTG_ARCHIDEKT_BACKUPS`, on by default). If that copy
  fails, nothing changes and the proposal stays pending;
- sends the change one card at a time, then reads the deck back to verify
  it;
- refuses to apply the same proposal twice.

Applying a new deck creates it (private unless the user asked otherwise),
adds the cards and verifies the same way. Proposals expire after 24 hours.
The gateway never deletes a deck. A proposal stuck in `applying` for over an
hour (say, after a restart mid-apply) is marked `failed` at startup and
nightly, with the snapshot and deck ids in its result.

Writes have been run live against a throwaway Archidekt account (create,
add, remove, quantity changes, categories, commander, and the backup folder
and copy). Still, make your own first edit on a deck you don't care about,
and check the result on Archidekt.

**The kill switch is `MTG_WRITES_ENABLED`.** It decides whether any proposal
can be applied. The code default is off; the example env files
(`deploy/stack.env.example`, `deploy/compose/.env.example`) turn it on.

- **To turn writes on:** set it to `true` in the stack's environment
  variables in Portainer and update the stack. Any other value, or leaving it
  unset, keeps writes off.
- **To turn writes off:** set it to `false` (or remove it) and update the
  stack. It kicks in when the service restarts.

With writes off, people can still sign in, link Archidekt, use research
tools, and make and review proposals. Applying returns "Deck writes are
switched off on this gateway" and nothing reaches Archidekt. A proposal made
while writes were off can be applied once you turn them back on, as long as
it hasn't expired and the deck hasn't changed.

### Approving on the card in the chat

With `MTG_APPLY_IN_CHAT` on (the default), an AI app that renders MCP Apps
(Claude on the web, desktop and phones; ChatGPT) shows every proposal as a
card with the change, each card's picture, and Approve and Reject buttons.
The buttons call the `confirm_proposal` tool, which the app offers to the
card and not to the model, and which needs a one-time code the gateway
puts only in the tool result's `_meta` (handed to the card, kept out of the
model's context). A call without the right code is refused and logged as
`approval_refused`, with nothing sent; the code is tied to the proposal, the
member and the app that proposed, and is spent by the apply. The gateway
cannot see who pressed; it relies on the app keeping the tool and the code
away from the model, as the MCP Apps standard says. Set it to `false` and
the card, the code and the tool disappear: members use the review page.
An app that cannot show the card (Claude Code, older clients) sees the text
and the review link as before; Claude Code can also open the review page
for the member when the assistant calls `apply_proposal`.

### Approval modes: when the assistant may apply by itself

Each member picks an **approval mode** on their own Account page. It is
theirs alone: it governs only their proposals, their decks and the apps they
connected, and it can only be set there, in their browser session (never
over MCP or the API, so a tricked assistant cannot loosen it). Every change
of mode is in the audit log as `approval_mode_set`.

| Mode | What the assistant may apply with `apply_proposal` |
|---|---|
| `manual` (default) | Nothing. Every proposal waits for the member's press on the card or the review page; `apply_proposal` answers `browser_required`. |
| `semi` | Low-risk proposals only. High-risk ones wait for the member's press as in manual. |
| `auto` | Every proposal. |

The risk of a proposal comes from its stored review rows, so it is judged
on what the review page would show, not on what the assistant says:

| Tier | Proposals |
|---|---|
| Low | An edit to an existing deck with at most `MTG_AUTO_APPLY_MAX_ROWS` rows (default 5), each a card add, remove, quantity change, category move, finish or printing change, no row moving more than four copies, none touching the commander. Cloning a deck (a copy; nothing that exists changes). |
| High | Everything else: more rows than that, any commander change, creating a new deck, restoring a snapshot, and deck details (name, format, description, visibility). |

Every proposal result carries `approval_mode`, `risk`, `risk_reason` and
`assistant_may_apply`, and `next_step` tells the assistant whether to call
`apply_proposal` or hand the decision to the member. The service re-checks
the mode and the risk inside the apply itself, over MCP and over the REST
API alike, so there is no path around it. An assistant apply the mode does
not allow is logged as `apply_needs_user`. Every apply, in every mode,
snapshots the deck first and backs it up on Archidekt, so an auto-applied
change can be undone from the History page.

Two settings are the operator's:

- `MTG_APPROVAL_MODE_DEFAULT` (default `manual`) is the mode of a member who
  has not chosen one. Leave it `manual` on a shared gateway.
- `MTG_APPROVAL_MODE_MAX` (default `auto`, no cap) is the highest mode
  members may choose; a stored choice above it is read as the cap, and the
  Account page shows the capped choices disabled.

The trade-off, which the Account page states next to the choices: an
assistant can be tricked by text it reads (a web page, a deck description, a
pasted list) into proposing a change the member did not ask for, and in a
semi or auto mode such a change lands on Archidekt without their press. In
manual mode a person's own press always sits between anything the assistant
read and a write to Archidekt.

Tell people to keep `apply_proposal` on "ask every time" (or "needs
approval") in their AI app rather than "always allow".

Each proposal records the app that made it (or "browser"), and the review
page and the proposal list show it. An app may apply or reject only its own
proposals (`other_client` otherwise). When a member disconnects an app on
their Account page, that app's pending proposals are rejected and can no
longer be applied. One app may hold at most 30 pending proposals for a
member, and a member 100 in all, so a runaway app can't use up every slot.

An app connected with the read-only scope `mtg.read` can read decks,
proposals, snapshots and reports but can't propose, apply, reject, run
reports or save scans (`insufficient_scope`).

### Archidekt rate limiting

The gateway paces its own Archidekt requests. If Archidekt says "slow down"
(HTTP 429) it waits as asked. After repeated failures it pauses all
Archidekt requests for a while, and users see "Archidekt requests are paused
after repeated failures; try later". It clears on its own.

Each member also has their own limits, so one looping assistant can't keep
Archidekt busy for everyone:

- at most three Archidekt requests running or waiting at once;
- at most `MTG_ARCHIDEKT_CALLS_PER_10_MIN` (120 by default) started per 10
  minutes, refilled evenly. Deck reads, proposals, applies, links and the
  research tools' `archidekt_*` calls all count. Past it the member gets
  `rate_limited` and waits a few minutes;
- at most five failed Archidekt link attempts (wrong username or password)
  in 15 minutes, so the Account page can't be used to guess Archidekt
  passwords from the gateway's address. The attempted username isn't
  logged.

## The admin page

`/admin` is a browser page for whoever runs the gateway. It exists only when
`MTG_ADMIN_GROUP` is set to the name of a group in your identity provider
(set it in the stack's environment variables or in `.env`; the stack and
Compose files already pass it through). Members of that group see it after
signing in; since 0.6.6 they don't also need to be in `MTG_REQUIRED_GROUP`,
because the admin group lets its members sign in too. In the site menu it's
the **Admin** link, shown only to them. For everyone else, and whenever the variable is unset, `/admin`
and everything under it answers 404, so ordinary users can't tell the area
exists. Group membership is what the live membership check last recorded
(at most `MTG_MEMBERSHIP_CHECK_TTL` seconds old, see
[Revoking access](#revoking-access)); a disabled account is never an admin.

What it shows:

- **Overview** (`/admin`): how many people have signed in at least once, how
  many are disabled and how many have linked Archidekt; proposals by state,
  applies, tool calls and errors over the last 30 days, with a line per
  counter kind by day; and a **System** card with the version, database
  schema and size, and the newest backup (or the last backup's failure).
- **Users** (`/admin/users`): every account the identity provider has ever
  signed in, newest sign-in first, with name, email, subject, groups, first
  and last seen, Archidekt username, the AI clients connected, live token
  and browser-session counts, and the action buttons below.
- **Activity** (`/admin/activity`): the last 200 audit rows for everyone, or
  for one person from the Users page (`?sub=`).
- **Metrics** (`/admin/metrics`): 30-day totals per counter (see
  [the metrics table](#the-metrics-table) below).

The same data is at `/api/v1/admin/overview`, `/api/v1/admin/users` and
`/api/v1/admin/metrics`, and the actions at `POST /api/v1/admin/users/<sub>`
(see [API.md](API.md)). Both need the admin's browser session cookie; the
API writes also need the `X-CSRF-Token` header.

The five actions, and exactly what each one does:

| Button | What happens |
| --- | --- |
| **Disable** | Sets `disabled_at` on the user, revokes every token they hold, deletes their browser sessions, any sign-in codes in flight and the identity-provider tokens the gateway kept for them. From then on the gateway refuses them everywhere: a sign-in through the identity provider is rejected with "Your account has been disabled on this gateway" (audited as `login_rejected_disabled`), a token refresh fails (`refresh_rejected`, reason `disabled`), an access token that is still in someone's hands is refused on its next use and its chain revoked (`disabled_user_refused`), and a browser session cookie is treated as signed out. You can't disable your own account. |
| **Enable** | Clears `disabled_at`. Nothing is handed back: the person signs in again and reconnects their assistant. |
| **Revoke tokens and sessions** | The same revocation as Disable (tokens, browser sessions, pending codes, identity-provider tokens) without disabling. The person can sign in again straight away. This is the button version of the SQL in [Revoking access](#revoking-access). |
| **Unlink Archidekt** | Marks their Archidekt link revoked and deletes the stored session, the same as their own Unlink button on `/account`. They can relink any time. |
| **Delete data** | Deletes everything the gateway keeps about that person, the same as their own **Delete my data**: proposals, snapshots, reports, scan sessions, the remembered covers of their own decks (never a cover of someone else's deck they cloned or reported on), the Archidekt link, every app grant and browser session, the identity-provider tokens, the copy of their profile picture, usage counters and the user record. It needs the confirmation tick next to the button, and you can't use it on yourself (use your own Account page). Meant for former members, and for an account left over from an earlier identity provider (the gateway refuses a new provider's account whose `sub` matches an old one until the old one is deleted). Their decks on Archidekt are not touched, and the audit log keeps its rows. If they're still in the group, they can sign in again as a new, empty account. |

Every action writes an `admin_disable`, `admin_enable`, `admin_revoke`,
`admin_unlink` or `admin_delete_data` row to the audit log under the admin's
subject, with the target and counts in `detail_json`, and bumps an `admin`
counter in the metrics table. The person's own activity log shows the
action too, as done by an administrator. The admin page never creates a user or changes a group: that
stays in the identity provider. Disabling only refuses the account on this
gateway; the account itself is untouched.

### The metrics table

The database has a `metrics` table of per-day counters: one row per UTC
day, user, `kind` and `name`, with a count `n`. Kinds are `tool` (one per
tool call, by tool name, counted in the MCP middleware, so research calls
relayed to Mystic Forge count too), `error` (a tool call that failed or
reported an error), `api` (one per JSON API call, by method and route) and
`admin` (one per admin action). A
counter that can't be written is logged and dropped; nothing else depends
on it. The admin Overview and Metrics pages sum the last 30 days; the
`days` query parameter of `/api/v1/admin/metrics` goes up to 365. The
30-day totals in SQL:

```sql
SELECT kind, name, SUM(n) AS n FROM metrics
WHERE day >= date('now', '-29 days') GROUP BY kind, name ORDER BY n DESC;
```

### Archidekt session refresh

Archidekt hands out a short-lived access token and a refresh token when a
user links. Before using the stored session the gateway refreshes the access
token if its expiry is unreadable or less than five minutes away, and once
more if Archidekt still answers 401. A successful refresh stores the new
token under the same link (same key, same username) and writes an
`archidekt_session_refreshed` audit row whose `detail_json` says why
(`expiring` or `rejected`). Seeing these rows is normal and means the link
is healthy. The link is only marked revoked, with an `archidekt_link_expired`
row and a "relink at /account" message to the user, when there is no refresh
token, the refresh itself is rejected, or a freshly refreshed token is
rejected too.

## Looking at proposals, snapshots, links and the audit log

Everything lives in `<MTG_DATA_DIR>/mtg-gateway.sqlite` (profile pictures are
separate files under `<MTG_DATA_DIR>/avatars`, fetched again when missing). The container has
Python but no `sqlite3` command, so run queries through Python on the
gateway's node. For example, the latest proposals:

```bash
docker exec -it --user 1000:1000 $(docker ps -q -f name=mtg_mtg-assistant-gateway) \
  python -c "import sqlite3; c=sqlite3.connect('/data/mtg-gateway.sqlite'); \
  [print(r) for r in c.execute('SELECT id, owner_sub, kind, deck_id, deck_name, state, created_at FROM proposals ORDER BY created_at DESC LIMIT 20')]"
```

For the SQL snippets on this page, put the query inside the
`c.execute('...')` the same way.

| Table | What's in it |
| --- | --- |
| `proposals` | Each proposal: owner, kind (`edit`, `create_deck`, `restore` or `details`), deck (the new deck's id once a create is applied), change list, diff, state (`pending`, `applying`, `applied`, `failed`, `rejected`; shown as `expired` after 24 hours), result and timestamps |
| `snapshots` | The full deck as read from Archidekt just before a proposal was applied, plus `backup_deck_id` and `backup_url` for the private backup copy made in the user's Archidekt backup folder at the same moment. Users see theirs with `list_snapshots` and undo an edit with `propose_restore_snapshot`. `deck_json` is the raw deck |
| `archidekt_links` | One row per user who linked Archidekt: Archidekt username, encrypted session, status and timestamps |
| `reports` | Stored deck reports (`run_deck_report`, the "Run deck report" button and `POST /api/v1/reports`): owner, deck, a fingerprint of the deck as read, the statistics, and the goldfish and validation results as JSON. The newest 200 per user are kept |
| `metrics` | Per-day usage counters, see [The metrics table](#the-metrics-table) |
| `audit_log` | Kept for a year. Sign-ins and rejected sign-ins, token events, proposals created and rejected, every apply attempt (refusals included), link, unlink, refresh and expiry events, reports created, and `admin_*` actions. `client_id` is the OAuth client that made the request (`__browser__` for the review page) and `detail_json` carries its registered `client_name`, so you can tell which assistant did what |

`PRAGMA table_info(proposals);` lists a table's columns. No table holds a
plaintext password or token.

A user's audit history:

```sql
SELECT datetime(at, 'unixepoch') AS when_utc, event, detail_json FROM audit_log
WHERE sub = (SELECT sub FROM users WHERE email = 'person@example.com')
ORDER BY at DESC LIMIT 50;
```

### Undoing an applied change

The user does this themselves: they ask their assistant to restore the deck
from the snapshot taken before the edit (`list_snapshots`, then
`propose_restore_snapshot`). That's a normal proposal, okayed and applied
like any other, and it puts every card back with the same printing, finish,
quantity and categories, commander, sideboard and maybeboard included. It
leaves the deck's name, description and format alone. Their Archidekt backup
folder also holds a full copy from just before each edit.

A wrongly created deck is deleted by its owner on Archidekt. If a create
fails partway, the proposal is `failed` and its `deck_id` column holds the
new deck's id; the half-filled deck stays on Archidekt until the owner
deletes it. To see exactly what a deck held before an edit, read the
snapshot named in the proposal's `snapshot_id`:

```sql
SELECT deck_json FROM snapshots WHERE id = '<snapshot_id>';
```
