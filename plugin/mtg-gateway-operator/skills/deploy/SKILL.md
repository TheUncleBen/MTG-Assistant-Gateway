---
name: deploy
description: Guided first deployment or redeployment of the MTG Assistant Gateway stack (gateway plus Mystic Forge) on Docker Swarm with Portainer, Authentik and Nginx Proxy Manager. Use when the operator says "deploy the gateway", "set up the stack", "install the gateway on my swarm" or asks to verify a deployment.
---

# Deploy the MTG Assistant Gateway

You are helping the operator deploy their own MTG Assistant Gateway. The authoritative
guide is `docs/DEPLOY.md` in the repository
(https://github.com/TheUncleBen/MTG-Assistant-Gateway/blob/main/docs/DEPLOY.md);
if this skill and that file disagree, the file wins. This skill turns it
into a conversation: ask for the few facts you need, run what can be run
from here, and give the operator ready-to-paste commands for what must run
on their Swarm manager.

First ask which shape they want, because the other guides take over for
anything that isn't the default:

- **Docker Swarm** (with or without Portainer): this skill, `docs/DEPLOY.md`.
- **One machine with plain Docker Compose**: follow `docs/DEPLOY-COMPOSE.md`
  instead, with the same rules below. Its `deploy/compose/make-secrets.sh`
  is run by the operator, never by you.
- **An identity provider other than Authentik**: `docs/IDP-OTHERS.md` for
  step 3; everything else is the same.
- **A reverse proxy other than Nginx Proxy Manager**: `docs/REVERSE-PROXY.md`
  for step 7.

## Rules

1. **Secrets never pass through this chat.** Random secrets are generated
   by a command and piped straight into `docker secret create`. The
   Authentik client secret is pasted by the operator into a terminal prompt
   that you give them, never into a message to you. If a secret is pasted
   here anyway, say so, do not use it, and ask them to rotate it in
   Authentik.
2. **Never run the secret commands yourself.** Step 4 is run by the
   operator in their own terminal on the manager; you give them the
   commands and wait. The Fernet key is printed once so a human can store
   it, and that output must never reach this chat. Ask before any other
   command that changes the Swarm (creating networks, stacks or proxy
   hosts). Reading (`docker service ps`, `curl`, `docker secret ls`) is
   fine without asking.
3. **Verify, do not assume.** Every step ends with a check and you report
   its real output. Say "not checked" for anything you could not check.
4. **Label what you know.** Menu names in Authentik, Portainer and Nginx
   Proxy Manager come from the project's docs and may have changed; say
   "look for the nearest label" rather than insisting.

## Facts to collect first (ask in one message, with defaults)

| Fact | Default | Why |
| --- | --- | --- |
| Public hostname of the gateway | none | `MTG_PUBLIC_URL`, NPM host, Authentik redirect URI |
| Identity provider and its hostname | Authentik | the issuer URL; other providers: `docs/IDP-OTHERS.md` |
| Swarm node that will run the stack | none, ask | `MTG_NODE`, `MF_NODE`, host folders |
| How the operator reaches a Swarm manager from this machine | `ssh <manager>` | whether you can run commands or must hand them over |
| `PUID:PGID` the operator uses in other stacks | `1000:1000` | folder ownership |
| Overlay network name | `mtg-gateway` | shared with the proxy |
| Allow deck writes now? | `false` | `MTG_WRITES_ENABLED`; recommend `false` until a throwaway deck test passes |
| Default approval mode for members who have not chosen one? | `manual` | `MTG_APPROVAL_MODE_DEFAULT`; recommend `manual` (every change waits for the member's own press). Each member picks their own mode (`manual`, `semi`: low-risk edits apply without asking, `auto`: everything) on their Account page |
| Cap the approval mode members may choose? | `auto` (no cap) | `MTG_APPROVAL_MODE_MAX`; `semi` or `manual` to stop members choosing looser modes. `MTG_AUTO_APPLY_MAX_ROWS` (default 5) is the row limit of a low-risk edit |
| Show the Approve/Reject card in the chat? | `true` | `MTG_APPLY_IN_CHAT`; recommend `true`: Claude and ChatGPT show each proposal as a card whose Approve button carries a one-time code the assistant never sees; the same switch gives the other cards in the chat (printings, recognised cards, a deck, the account, comparisons, statistics, deck lists, scans); `false` leaves only the review page |

Everything else uses the values in `deploy/stack.env.example`.

## Steps

Work through these in order. Each has a command or UI path and a check.

### 1. Overlay network (manager)

```bash
docker network create --driver overlay --attachable --opt encrypted mtg-gateway
docker network ls --filter name=mtg-gateway
```

The name is case-sensitive and must match `MTG_NETWORK_NAME` exactly, or the
stack deploy fails with "declared as external, but could not be found". If the
operator already has a network under another name, set `MTG_NETWORK_NAME` to it.
Keep `--attachable` so a reverse proxy running as a plain container can join.

Then the operator adds that external network to their Nginx Proxy Manager
stack (service `networks:` plus a top-level `networks:` entry with
`external: true` and `name: mtg-gateway`) and redeploys NPM. Check: `docker
network inspect mtg-gateway` lists the NPM container once it has redeployed.

### 2. Host folders on the pinned node (local disk, not CephFS)

```bash
sudo mkdir -p /srv/mtg-gateway/data /srv/mtg-gateway/backups
sudo chown -R 1000:1000 /srv/mtg-gateway
ls -ld /srv/mtg-gateway/*
```

Use the operator's `PUID:PGID` in place of `1000:1000`. `/srv/mtg-gateway` is an
example; any local folder works, and the same paths go in `MTG_DATA_DIR` and
`MTG_BACKUP_DIR`.

### 3. Authentik provider and application (browser, operator does it)

Give these as a checklist and wait for the answers you need (client ID and
issuer URL; both are not secrets):

1. Applications, Providers, Create, OAuth2/OpenID Provider. Name `MTG
   Assistant Gateway`; client type Confidential; redirect URI
   `https://<gateway host>/auth/callback` (strict); default RS256 signing
   key; scopes `openid`, `email`, `profile` and `offline_access` (the
   `offline_access` mapping is easy to miss and is required: without it
   Authentik issues no refresh token and members must sign in again every
   hour); subject mode left at the default.
   Copy the Client ID into the chat. Keep the Client Secret in the clipboard
   or password manager for step 4; do not paste it here.
2. Applications, Applications, Create. Name `MTG Assistant Gateway`, slug
   `mtg-gateway`, provider as above. Bind the group that may use the
   gateway (for example `MTG Assistant Gateway Users`) and put the operator in it.
3. Open the provider again and copy the OpenID Configuration Issuer URL
   (with slug `mtg-gateway`: `https://<authentik host>/application/o/mtg-gateway/`).

Check from here, no sign-in needed:

```bash
curl -s https://<authentik host>/application/o/mtg-gateway/.well-known/openid-configuration | head -c 400
```

A JSON document with `"issuer"` equal to the issuer URL means step 3 is right.

### 4. Docker secrets (operator only, in their own terminal on a manager)

Hand these over; do not run them, even over ssh. The first two generate
and pipe the values; the Fernet key is also shown once so the operator
can keep a copy (without it every Archidekt link, including those in old
backups, can't be decrypted), which is exactly why it must not run from
here. The third reads the Authentik client secret from the keyboard at a
silent prompt:

```bash
python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())' | tee /dev/stderr | docker secret create mtg_fernet_key -
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' | docker secret create mtg_session_secret -
# Paste the Authentik client secret at the (silent) prompt and press Enter:
read -rs S && printf %s "$S" | docker secret create mtg_oidc_client_secret - ; unset S
docker secret ls
```

Check, which you may run: `docker secret ls` shows exactly those three
names and no values. If a manager has
no `python3`, `openssl rand -base64 32 | tr '+/' '-_'` produces a valid
Fernet key and `openssl rand -base64 48` a session secret.

### 5. GHCR pull credential (Portainer, browser)

Only needed if the GHCR packages are private. The operator creates a GitHub classic token with
only `read:packages` and adds it in Portainer under Registries, Add
registry, Custom, URL `ghcr.io`, with their GitHub login. The token is a
secret: it goes into Portainer, not into this chat. The registry URL must be exactly
`ghcr.io` (no path): Portainer only sends a registry's login when its URL
equals the image's host, so a per-image entry like `ghcr.io/<owner>/mtg-assistant-gateway`
is ignored and tasks end `rejected` with `No such image`. Later updates are
then one click: Update the stack with Re-pull image on. A `docker login` on
the node does not fix Swarm pulls (docs/DEPLOY.md step 5).

### 6. Portainer stack (browser)

Stack name `mtg`; compose from `deploy/portainer-stack.yml` (paste it,
or use the repository as a git source with that path). Environment
variables: produce the full list for the operator from
`deploy/stack.env.example` with the collected facts filled in. The ones
that must change from the example are `MTG_PUBLIC_URL`, `MTG_OIDC_ISSUER`,
`MTG_OIDC_CLIENT_ID`, `MTG_NODE`, `MF_NODE`, `MTG_DATA_DIR`, `MTG_BACKUP_DIR`
(if the folders differ from step 2's example), `MTG_REQUIRED_GROUP`, `PUID`, `PGID`, `TIMEZONE`
and, if chosen, `MTG_WRITES_ENABLED`, `MTG_APPROVAL_MODE_DEFAULT`, `MTG_APPROVAL_MODE_MAX`
and `MTG_APPLY_IN_CHAT`.

Check (manager):

```bash
docker service ps mtg_mtg-assistant-gateway mtg_mtg-assistant-mysticforge
docker service logs --tail 50 mtg_mtg-assistant-gateway
```

Both tasks `Running` within about a minute. A gateway that exits at once
usually names the missing variable or secret in its last log lines.

### 7. Nginx Proxy Manager host (browser)

Proxy host for the gateway hostname: scheme `http`, forward host
`mtg_mtg-assistant-gateway` (full Swarm service name), port `8080`, Block
common exploits on, Websockets support off (the gateway has no websocket
endpoints); SSL tab with a Let's Encrypt certificate, Force SSL, HTTP/2 and
HSTS; Advanced tab:

```nginx
proxy_buffering off;
proxy_read_timeout 300s;
client_max_body_size 4m;
```

No proxy host for Mystic Forge: it stays internal.

### 8. Verify from anywhere

```bash
curl -s https://<gateway host>/healthz
curl -s https://<gateway host>/.well-known/oauth-authorization-server | head -c 300
curl -s -o /dev/null -w '%{http_code}\n' https://<gateway host>/mcp
curl -s https://<gateway host>/plugin/marketplace.json | head -c 300
```

Expected: `{"status":"ok"}` (`"degraded"` means the gateway runs but
Mystic Forge doesn't answer: check that service; the System card on the
admin page shows the version and the research service's state); metadata
JSON whose `issuer` is the public
URL; `401`; and the plugin marketplace JSON. Report each line's real
result.

### 9. Connect the operator's own assistant

The operator is now a user too. Point them at
`https://<gateway host>/install`, or do it here:

```bash
claude plugin marketplace add https://<gateway host>/plugin/marketplace.json
claude plugin install mtg-gateway@mtg-gateway
```

Then `/mcp`, authenticate, `whoami`. The `/mtg-gateway:setup-mtg-gateway` skill covers
the rest, including linking Archidekt.

### 10. After the first sign-in

- Leave `MTG_CIMD_ALLOWED_HOSTS` empty (the default). Listing only the
  host Claude's published identity came from locks ChatGPT out; narrow it
  only if the operator lists every client's host (`docs/OPERATIONS.md`,
  "Which AI clients may connect").
- Recommend narrowing `MTG_TRUSTED_PROXIES` to NPM's address or the overlay
  subnet once NPM works (`docs/DEPLOY.md` step 7).
- Keep `MTG_WRITES_ENABLED=false` until a proposal has been applied to a
  throwaway deck and verified on Archidekt (see `docs/OPERATIONS.md`, "Deck
  writes and the kill switch").

## Report

End with three lists: done and checked (with the output seen), done by the
operator but not checked from here, and not done. Never describe a step as
working when its check was not run.
