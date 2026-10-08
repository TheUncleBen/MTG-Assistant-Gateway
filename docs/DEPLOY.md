# Deploying the gateway on Docker Swarm (with or without Portainer)

This gets you one Swarm stack with two services:

- `mtg-assistant-gateway`, the gateway itself, and
- `mtg-assistant-mysticforge`, a private research service the gateway talks to (more
  on it [near the end](#mystic-forge-internal-research-service)).

Not running a Swarm? [DEPLOY-COMPOSE.md](DEPLOY-COMPOSE.md) does the same on
one machine with plain Docker Compose. How the pieces fit together is in
[ARCHITECTURE.md](ARCHITECTURE.md).

The steps use Portainer, Nginx Proxy Manager (NPM) and Authentik because
that's the setup the guide was written on, but none of them is required:

- **No Portainer?** Every Portainer step has a command-line equivalent, shown
  alongside it; the stack deploys with `docker stack deploy`.
- **Another reverse proxy?** Step 7 links to Caddy, Traefik and nginx
  examples in [REVERSE-PROXY.md](REVERSE-PROXY.md).
- **Another identity provider?** Step 3 links to
  [IDP-OTHERS.md](IDP-OTHERS.md) for Keycloak, Authelia, Zitadel, Pocket ID,
  Kanidm and others.

The steps are in the order you'll need them. The only place you ever paste a
secret is into a Docker secret.

Placeholders used below:

| Placeholder | Means |
| --- | --- |
| `mtg.example.com` | the gateway's public hostname |
| `auth.example.com` | your identity provider's public hostname |
| `<owner>` | the GitHub account or organisation whose copy of this repository builds the images |

## 1. Overlay network (once, on a Swarm manager)

The stack doesn't create the network it shares with your reverse proxy. It
joins an existing overlay network by name, so create it before you deploy
(the small private network between the gateway and Mystic Forge is created
by the stack itself):

```bash
docker network create --driver overlay --attachable --opt encrypted mtg-gateway
docker network ls --filter name=mtg-gateway   # check: one overlay network, swarm scope
```

`--opt encrypted` matters when NPM and the gateway can run on different
nodes. NPM finishes HTTPS and forwards plain HTTP over this network, so
without it every sign-in cookie, OAuth token and the Archidekt password
typed on `/account` crosses your LAN unencrypted between the nodes. With it,
Docker encrypts node-to-node traffic on the network with IPsec. Containers
join it the same way, NPM included, and `--attachable` still works. Things
to know:

- The nodes must let IPsec ESP (IP protocol 50) through to each other, as
  well as the usual Swarm ports (TCP 2377, TCP/UDP 7946, UDP 4789). Linux
  hosts with no firewall between them, the usual Raspberry Pi setup, need
  nothing. If services on different nodes can't reach each other after
  this, a firewall dropping ESP is the likely cause.
- It costs some CPU on each node. For a gateway's traffic on a Pi that's
  small.
- On a single-node Swarm (everything on one machine) traffic never leaves
  the host, so you can leave the option off.
- You can't add it to an existing network. To switch, remove the network
  from the gateway and NPM stacks, delete it, create it again with the
  option, and redeploy both stacks. Nothing in the gateway changes. If you
  skip this, keep NPM and the gateway on the same node (put NPM's node in
  `MTG_NODE`) so the plain HTTP hop never crosses the LAN.

Docker network names are case-sensitive. `mtg-gateway` and `MTG-Gateway` are
different networks, and if the name doesn't match exactly the deploy fails
with `network "mtg-gateway" is declared as external, but could not be found`.
If you already have a network under another name, set `MTG_NETWORK_NAME` to
that exact name instead of creating a new one.

Keep `--attachable`. It lets plain containers (not Swarm services) join the
network, which you need if your reverse proxy runs as a standalone container.
A Swarm-service proxy works either way. You can't change it on an existing
network: remove the network and create it again (only while nothing uses it).
If you create it in Portainer instead (**Networks → Add network**), pick the
`overlay` driver and turn on manual container attachment.

Then add that network to your reverse proxy's stack so it can reach the
gateway. For NPM:

```yaml
    networks:
      - mtgnet        # on the proxy service
...
networks:
  mtgnet:
    external: true
    name: mtg-gateway
```

Redeploy the proxy's stack.

If you'd rather use a different network name, set `MTG_NETWORK_NAME` in the
gateway stack to match, spelled exactly the same.

## 2. Storage on the node that runs the gateway

Pick the node the gateway will live on. Its hostname goes in `MTG_NODE`
later. Put the folders on that node's local disk, not on a network file
system (SQLite and network file systems don't get along). Any local
folder works; `/srv/mtg-gateway` below is just an example, so use the same
paths in `MTG_DATA_DIR` and `MTG_BACKUP_DIR` later:

```bash
sudo mkdir -p /srv/mtg-gateway/data \
             /srv/mtg-gateway/backups
sudo chown -R 1000:1000 /srv/mtg-gateway
sudo chmod 700 /srv/mtg-gateway/data \
               /srv/mtg-gateway/backups
```

Use the same `PUID:PGID` you'll set in the stack (`1000:1000` above). The
gateway creates its database and backups readable by that user only.

## 3. Identity provider: one OpenID Connect client for the gateway

Using something other than Authentik? Follow the checklist in
[IDP-OTHERS.md](IDP-OTHERS.md) instead, then carry on at step 4 with your
provider's issuer URL, client ID and client secret.

Here's the short version for Authentik. For every field explained, plus
troubleshooting, see [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md).

In the Authentik admin UI:

1. **Directory → Groups → Create** a group for the people who may use the
   gateway, for example `MTG Assistant Gateway Users`. Add yourself.
2. **Applications → Providers → Create → OAuth2/OpenID Provider**
   - Name: `MTG Assistant Gateway`
   - Authorization flow: your usual implicit or explicit consent flow
   - Client type: **Confidential**
   - Redirect URIs: `https://mtg.example.com/auth/callback` (Strict)
   - Signing key: your default certificate (RS256). Don't leave it empty.
   - Scopes (under **Advanced protocol settings**): `openid`, `email`,
     `profile` **and** `authentik default OAuth Mapping: OpenID
     'offline_access'`. Make sure the last one is selected. Without it
     Authentik quietly issues no refresh token, and members have to sign in
     again every time Authentik's access token runs out (an hour by
     default). The gateway uses the refresh token to ask Authentik on every
     request whether the person is still in the group.
   - Subject mode: **Based on the User's hashed ID** (the default). Keep it.
     The subject is the only thing the gateway knows a person by: their
     Archidekt link, proposals and tokens all hang off it. The username and
     email modes are unsafe, because people can change their own username or
     email in Authentik, and whoever takes over an old value would inherit
     that person's gateway account. Don't change the mode once people have
     signed in either, or everyone shows up as a brand new user.
   - Leave **Include claims in id_token** on (it's the default).
   - Note the **Client ID**. Note the **Client Secret** too, but it only
     goes into a Docker secret in step 4.
3. **Applications → Applications → Create**
   - Name `MTG Assistant Gateway`, slug `mtg-gateway`, provider: the one you just made.
   - Launch URL: `https://mtg.example.com/` (optional).
   - **Policy / Group / User Bindings**: bind the group from step 1. Only
     people who pass this binding can sign in, so this is where you control
     access. Set `MTG_REQUIRED_GROUP` to the same group in step 6 as a
     second check.
4. Open the provider again and copy the **OpenID Configuration Issuer** URL.
   With slug `mtg-gateway` it looks like
   `https://auth.example.com/application/o/mtg-gateway/`.
5. Strongly recommended: require a second factor. Anyone holding a
   member's Authentik password can change that person's Archidekt decks
   through the gateway. See
   [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#mfa-and-who-can-join) for MFA and for
   keeping invitations and social sign-in from adding people to the group
   by themselves.

## 4. Docker secrets (once)

The stack expects three Swarm secrets with exactly these names:

| Secret name | What goes in it |
| --- | --- |
| `mtg_fernet_key` | A random encryption key for linked Archidekt sessions and the identity provider's tokens the gateway keeps. **Keep a copy** (password manager or similar). Lose it and every linked account has to be relinked (and everyone signs in once more), and old backups' Archidekt sessions can't be decrypted |
| `mtg_session_secret` | A random string of 32+ characters that signs the browser sign-in cookie. No need to keep a copy |
| `mtg_oidc_client_secret` | The Client Secret from the Authentik provider in step 3 |

A fourth secret is optional: `mtg_authentik_api_token`, an Authentik API
token that may view only the gateway's one or two groups (and its own tokens, see IDP-AUTHENTIK.md §12), turns on the hourly clean-up of removed
members' stored Archidekt sessions. Its setup, and the three stack lines to
uncomment, are in
[IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#12-optional-removed-member-clean-up).
Without it the gateway works the same, with the clean-up off.

Make the random values on your own machine, right when you need them. They
only ever go into the secret, never into the stack's environment variables,
a file in this repository, or a chat with an assistant. Pick one of the two
ways below.

### Option A: in Portainer

1. On any machine with `openssl` (Linux, macOS, WSL or Git Bash), generate
   the two random values:

   ```bash
   # mtg_fernet_key: a Fernet key (URL-safe base64 of 32 random bytes)
   openssl rand -base64 32 | tr '+/' '-_'

   # mtg_session_secret
   openssl rand -base64 48
   ```

2. In Portainer, pick your Swarm environment, then **Secrets → Add secret**.
3. Name `mtg_fernet_key`, paste the first value into **Secret**, leave
   **Encode secret** on (the default), and click **Create the secret**.
   Save that value in your password manager now.
4. Do the same for `mtg_session_secret` (the second value) and for
   `mtg_oidc_client_secret` (paste the Client Secret straight from
   Authentik).
5. Clear your terminal (`clear`) so the values aren't left on screen.

### Option B: on a Swarm manager

Same three secrets from the command line. Nothing is written to disk or your
shell history:

```bash
# 1. mtg_fernet_key. Prints the key once so you can save a copy, then creates the secret.
openssl rand -base64 32 | tr '+/' '-_' | tee /dev/stderr | docker secret create mtg_fernet_key -

# 2. mtg_session_secret. No need to keep a copy.
openssl rand -base64 48 | docker secret create mtg_session_secret -

# 3. mtg_oidc_client_secret. Paste the Authentik Client Secret at the (silent) prompt and press Enter.
read -rs S && printf %s "$S" | docker secret create mtg_oidc_client_secret - ; unset S
```

Either way, Portainer's **Secrets** page (or `docker secret ls`) should now
list all three.

Swarm secrets can't be edited. To change one later, remove the stack (or
take the secret out of it), delete the secret, create it again with the
same name, and redeploy. Changing `mtg_fernet_key` means everyone relinks
Archidekt and signs in once more.

## 5. Registry login in Portainer

The images live in GitHub Container Registry (GHCR) under `<owner>`. If the
packages are private (the default for a private repository), Portainer
needs a login to pull them. Set it up once like this and every later update
is one click:

1. On GitHub, create a classic personal access token with only the
   `read:packages` scope.
2. In Portainer, **Registries → Add registry → Custom**:
   - Registry URL: exactly `ghcr.io`. Just the host, no `/<owner>` or image
     path. Portainer hands a registry's login to a stack service only when
     the registry URL equals the image's host, so an entry like
     `ghcr.io/<owner>/mtg-assistant-gateway` is never used and the deploy fails.
   - Authentication on, username your GitHub login, password that token.
3. Deploy (step 6). Portainer stores the login with each service, so
   whichever node runs it can pull. To update later, open the stack, change
   `MTG_TAG` if you're moving to a new release, and click **Update the
   stack** with **Re-pull image** on.

If tasks show `rejected` with `No such image: ghcr.io/<owner>/...`, check
the image name and tag first, then the registry URL above. A `docker login`
on the node doesn't fix it: Swarm pulls with the login stored on the
service, not the node's own. Pulling by hand on the node gets one deploy
going (Swarm then uses the cached image), but you'd have to repeat it for
every new tag.

If the packages are public you can skip this step.

## 6. Portainer stack

1. Portainer → **Stacks → Add stack**, name it `mtg`. (The service names
   in the other guides assume that name. Use another and the services become
   `<name>_mtg-assistant-gateway` and so on.)
2. Build method **Web editor**. Open
   [`deploy/portainer-stack.yml`](../deploy/portainer-stack.yml), copy the
   whole file and paste it in unchanged. (Or use the **Repository** build
   method with this repository and that compose path.) You shouldn't need to
   edit the stack file: everything you set lives in the variables.
3. Copy [`deploy/stack.env.example`](../deploy/stack.env.example) to a file
   on your machine, say `mtg.env`, and change the lines marked
   `REQUIRED` (the table below says what each one wants). The comments in
   that file explain every other line. Then, under **Environment
   variables**, either:
   - click **Load variables from .env file** and pick `mtg.env`, or
   - switch to **Advanced mode** and paste the file's contents.

   Check the list Portainer shows afterwards. Each variable should appear
   once with the value you expect. No secret values belong here; they're
   already in the Swarm secrets from step 4.

   | Variable | What to put |
   | --- | --- |
   | `MTG_IMAGE`, `MTG_TAG` | `ghcr.io/<owner>/mtg-assistant-gateway` and `latest` to follow every new version, or one version to stay on it, for example `0.7.8` ([VERSIONS.md](VERSIONS.md)) |
   | `MTG_PUBLIC_URL` | `https://mtg.example.com` |
   | `MTG_OIDC_ISSUER` | the issuer URL from step 3 |
   | `MTG_OIDC_CLIENT_ID` | the Client ID from step 3 |
   | `MTG_DATA_DIR` | `/srv/mtg-gateway/data` |
   | `MTG_BACKUP_DIR` | `/srv/mtg-gateway/backups` |
   | `MTG_NODE` | the hostname of the node from step 2 (`docker node ls` shows it) |
   | `PUID`, `PGID` | the user and group that own the folders from step 2, for example `1000` |
   | `TIMEZONE` | your time zone, for example `Europe/London` |
   | `MTG_REQUIRED_GROUP` | the group from step 3, spelled exactly as your identity provider sends it (it's case-sensitive). Required: the gateway won't start with it empty unless you also set `MTG_ALLOW_ANY_IDP_USER=true` (anyone your identity provider lets through gets in) |
   | `MF_IMAGE`, `MF_NODE` | `ghcr.io/<owner>/mtg-assistant-mysticforge`, and the node Mystic Forge should run on (can be the same one) |
   | `MTG_WRITES_ENABLED` | `true` to let approved proposals change Archidekt; `false` keeps everything review-only. Writes have been run live against a throwaway Archidekt account, but make your own first edit on a deck you don't care about |
   | `MTG_APPROVAL_MODE_DEFAULT` | Leave it `manual` (the example file and the code default): a member who has not chosen an approval mode has every change wait for their own press, on the Approve button of the card in the chat or on Apply on the review page. Each member picks their own mode (`manual`, `semi`: small low-risk edits apply without asking, `auto`: every change) on their Account page |
   | `MTG_APPROVAL_MODE_MAX` | `auto` (no cap) lets members choose any mode; `semi` or `manual` caps what they may choose |
   | `MTG_APPLY_IN_CHAT` | `true` (the default): Claude and ChatGPT show each proposal as a card with Approve and Reject buttons, and only the card's one-time code can apply it; the other cards (printings, recognised cards, a deck, the account) come with it. `false` removes every card; members use the review page |

   Everything else can stay at its default. The full list is in the
   [environment reference](#environment-reference) below.
4. Click **Deploy the stack**. The gateway should go healthy within about
   30 seconds. Check from a manager with
   `docker service logs mtg_mtg-assistant-gateway`.

### Without Portainer

The same stack from a Swarm manager's shell. `docker stack deploy` doesn't
read `.env` files, so load the variables into the shell first (this loop
copes with values that contain spaces, which `. ./file` doesn't):

```bash
cp deploy/stack.env.example mtg.env    # edit the REQUIRED lines
while IFS= read -r line; do case "$line" in ''|\#*) ;; *) export "$line" ;; esac; done < mtg.env
docker stack deploy --with-registry-auth -c deploy/portainer-stack.yml mtg
```

`--with-registry-auth` hands your `docker login ghcr.io` to the nodes, which
private images need. To update later, change `MTG_TAG` in the file and run
the last two lines again.

The gateway is one Python process. In the end-to-end test (amd64, idle after
a few sign-ins) it sat at about 60 MB. The stack caps it at
`MTG_MEMORY_LIMIT` (default 256 MB).

If the gateway's logs say `configuration error: MTG_REQUIRED_GROUP is empty`,
set `MTG_REQUIRED_GROUP` (or, if you really want everyone your identity
provider signs in, `MTG_ALLOW_ANY_IDP_USER=true`) and update the stack.
Stacks deployed before this check existed with an empty group stop at this
point after an upgrade.

Both services run with every Linux capability dropped except the four their
start-up script needs to switch to `PUID:PGID` (`CHOWN`, `DAC_OVERRIDE`,
`SETUID`, `SETGID`), and the switched process can't gain privileges back
(`setpriv --no-new-privs`). Swarm applies `cap_drop`/`cap_add` from Docker
20.10 on; older engines ignore them and print `Ignoring unsupported options`
at deploy. The root filesystem is left writable: when `PUID`/`PGID` differ
from the image's 1000:1000 the start-up script edits `/etc/passwd` and
`/etc/group`. Swarm ignores `security_opt`, so the stack doesn't set
`no-new-privileges` there (`setpriv --no-new-privs` covers the running
process), and it sets no process limit because older Docker and Portainer
versions refuse the `pids` key and the stack wouldn't deploy. The Compose
file ([DEPLOY-COMPOSE.md](DEPLOY-COMPOSE.md)) sets both.

The images are built on the official Python base images by tag, so every
release build picks up the base image's latest security patches. GitHub
Actions are pinned by commit, Python dependencies by version
(`constraints.txt`), and the Android build checks its Gradle wrapper before
running it.

## 7. Reverse proxy

Using Caddy, Traefik or nginx? See [REVERSE-PROXY.md](REVERSE-PROXY.md); the
forward address is `mtg_mtg-assistant-gateway:8080` on the network from step 1.
For NPM, **Hosts → Proxy Hosts → Add Proxy Host**:

- Domain: `mtg.example.com`
- Scheme `http`, forward hostname `mtg_mtg-assistant-gateway` (the full Swarm
  service name: stack name, underscore, service; it resolves on the network
  from step 1), port `8080`
- Turn on **Block common exploits**. Leave **Websockets support** off: the
  gateway has no websocket endpoints (MCP uses plain HTTP and server-sent
  events, which work without it). If you turned it on in an earlier deploy,
  you can turn it off.
- SSL tab: request a Let's Encrypt certificate, turn on **Force SSL**,
  HTTP/2 and **HSTS Enabled**. HSTS makes browsers refuse plain-HTTP visits,
  so nobody on a hostile Wi-Fi can intercept the first redirect to the
  sign-in page. Turn on **HSTS Subdomains** only if every subdomain of the
  domain serves HTTPS. Do the same on your identity provider's proxy host.
- Advanced tab, custom Nginx configuration:

  ```nginx
  proxy_buffering off;
  proxy_read_timeout 300s;
  client_max_body_size 4m;
  ```

  The first two stop long-running MCP responses getting buffered. The last
  caps request bodies at 4 MB. NPM allows 2000 MB by default, and the
  gateway's largest legitimate requests (scan and CSV imports) stay under
  2 MB, so anything bigger is only a way to make the gateway run out of
  memory.

That's the only thing you expose. The gateway reads the original `Host`
header, so leave NPM's default of passing it through. Every page and
endpoint (the companion pages `/decks`, `/history`, `/activity`, the
`/admin` pages, `/api/v1` and `/.well-known/assetlinks.json`) sits under the
same hostname and port, so one proxy host covers them all; no extra
locations or rules are needed.

**Narrow the trusted proxies (recommended).** The gateway believes
`X-Forwarded-*` headers from any private address by default, so any
container on `mtg-gateway` could make its requests look as if they came from
any client address in the gateway's request logs. Once NPM
works, find its address on the network and set `MTG_TRUSTED_PROXIES` to it
(or to the network's subnet if NPM's address changes on redeploy):

```bash
docker network inspect mtg-gateway --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'
docker network inspect mtg-gateway --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
```

Use the address without the `/24` suffix, for example
`MTG_TRUSTED_PROXIES=10.0.5.2`, or the subnet, for example `10.0.5.0/24`.
If you get it wrong nothing breaks: the gateway builds its links and
cookies from `MTG_PUBLIC_URL`, not from these headers. The request logs just
show NPM's address instead of the client's. Check after the next NPM
redeploy, since a Swarm task can come back with a new address.

**Rate limiting (optional).** `/register`, `/authorize`, `/token` and `/login`
answer anyone. The gateway trims abandoned sign-ins and anonymous log rows as
they pile up, but it doesn't slow the requests down. To slow down
scripted floods, add one line to NPM's `/data/nginx/custom/http_top.conf`
(create the file in NPM's data folder, then restart NPM):

```nginx
limit_req_zone $binary_remote_addr zone=mtg_oauth:10m rate=30r/m;
```

and this to the gateway proxy host's Advanced tab, under the lines above:

```nginx
location ~ ^/(register|authorize|token|login)$ {
  limit_req zone=mtg_oauth burst=30 nodelay;
  include conf.d/include/proxy.conf;
}
```

A normal connection makes a handful of these calls, so these numbers never
get in a real person's way. Leave it out if you'd rather not maintain custom
NPM files.

A `502 Bad Gateway` from NPM means it can't reach the gateway. Check that
NPM's stack has the network from step 1 (exact name) and was redeployed
after you added it, and that the forward hostname is right. From a manager,
`docker exec <npm-container> curl -s http://mtg_mtg-assistant-gateway:8080/healthz`
should print `{"status":"ok",...}`.

## 8. Check it works

From any machine:

```bash
curl -s https://mtg.example.com/healthz
# {"status":"ok","version":"0.7.8","mystic_forge":"ok"}
# ("degraded" with "mystic_forge":"down" means the gateway works but the
#  research service doesn't answer: check the Mystic Forge service)

curl -s https://mtg.example.com/.well-known/oauth-authorization-server | head -c 300
# JSON with "issuer":"https://mtg.example.com", "authorization_endpoint", ...

curl -s -o /dev/null -w '%{http_code}\n' https://mtg.example.com/mcp
# 401  (no token, which is what you want)
```

Then open `https://mtg.example.com/` in a browser. Every page except the
machine endpoints above (`/mcp`, `/healthz`, the OAuth metadata, `/install.md`
and the plugin files) sends you to Authentik first, and only members of
`MTG_REQUIRED_GROUP` get in. Signing in proves the Authentik side works.
Then connect your assistant: [CONNECT.md](CONNECT.md). To invite people, see
[ONBOARDING.md](ONBOARDING.md#for-the-owner).

The repository's end-to-end test runs these same steps automatically on a
single-node Swarm with a real Authentik: [tests/e2e/README.md](../tests/e2e/README.md).

## Mystic Forge (internal research service)

The same stack runs `mtg-assistant-mysticforge`, which is
[Mystic Forge](https://github.com/Kautiontape/mystic-forge) 1.3.2 (MIT
licence, from upstream). It's built from upstream commit
`dd82cd1449645862a2e309ae70df2fb7c3a2a839` into
`ghcr.io/<owner>/mtg-assistant-mysticforge`. It provides the card search, EDHREC,
rules, precon and goldfish tools, and the gateway relays an allowlisted set
of them to signed-in users.

Upstream code is unchanged apart from three small patches in
`docker/mystic-forge/patches/`, which is where the `-mag2` in the image tag
`1.3.2-mag2` comes from:

- `0001-archidekt-null-categories.patch`: Archidekt sends
  `"categories": null` for cards that never got a category (it happens in
  some imported decks). Without the patch, Mystic Forge's Archidekt readers
  (`archidekt_deck`, `archidekt_export`, `validate_archidekt_deck`, and
  `goldfish_run` given a deck id) crash on those decks. Since 0.7.2 those
  tools are hidden behind the gateway's own (`get_deck`, `deck_stats`,
  `run_deck_report`), which read Archidekt themselves, but the patch still
  matters for a `deck` argument given as an Archidekt link. It fills in the
  category Archidekt's own deck page shows: the card's suggested category,
  or `Land`.
- `0002-archidekt-user-decks-owner-filter.patch`: Archidekt's deck search
  ignores the `owner` filter Mystic Forge sends, so `archidekt_user_decks`
  listed other people's decks. The patch uses `ownerUsername`, which is what
  Archidekt's own site uses, and drops any deck the named user doesn't own.
- `0003-edhrec-current-page-shape.patch`: EDHREC changed its average-deck
  data, so `edhrec_average_deck` came back empty and `edhrec_commander`
  showed "? decks". The patch reads the new shape.

What to know about it:

- **It's not public.** No published ports, no login of its own. Don't make
  an NPM proxy host for it. It listens on port 8000 on a network private to
  the stack (`<stack>_mysticforge`, so `mtg_mysticforge` here; created and removed with the stack, not
  attachable), and the gateway is the only other service on it. It isn't on
  `mtg-gateway`, so NPM and other containers there can't reach it. The gateway
  still finds it as `mtg-assistant-mysticforge`. The private network isn't encrypted;
  only research queries cross it.
- **No extra setup.** It uses the registry login from step 5. No secrets, no
  host folders. Its `/data` lives inside the container and resets on
  redeploy, which is fine because nothing it keeps there matters while
  watchlists are off.
- **Price downloads and notifications are off.** The stack sets
  `MYSTIC_FORGE_NO_INGEST=1` (skips a roughly 141 MB MTGJSON download and the
  nightly price job) and `MYSTIC_FORGE_NTFY_OFF=1` (no pushes to ntfy.sh).
  Both are fixed in the stack file on purpose, not stack variables.
- **Outbound calls.** When its tools are used it calls Scryfall, EDHREC,
  Commander Spellbook and Archidekt's public deck API (never logged in to
  Archidekt). The rules tools check Wizards of the Coast for a newer
  Comprehensive Rules at most once a day.
- **Memory.** Capped at `MF_MEMORY_LIMIT` (default 512 MB). After loading
  the rules index it used about 115 MB on an amd64 test machine, and about
  170 MB as an arm64 container under emulation in CI (emulation adds
  overhead). Not yet measured on a Raspberry Pi or mid-goldfish.

| Variable | Default | What it does |
| --- | --- | --- |
| `MF_IMAGE` | `ghcr.io/theuncleben/mtg-assistant-mysticforge` | Image. A fork that builds its own images sets `ghcr.io/<owner>/mtg-assistant-mysticforge` |
| `MF_TAG` | `1.3.2-mag2` | Image tag |
| `MF_PUBLIC_BASE` | `http://mtg-assistant-mysticforge:8000` | Base URL Mystic Forge uses in links (its own default points at the upstream author's site, so keep this) |
| `MF_NODE` | none, required | Hostname of the node it runs on. The stack won't start without it |
| `MF_MEMORY_LIMIT` | `512M` | Memory cap |

Check it from a manager:

```bash
docker service ps mtg_mtg-assistant-mysticforge
docker service logs mtg_mtg-assistant-mysticforge
```

Healthy looks like `Running`, with the logs ending on Uvicorn listening on
`0.0.0.0:8000`.

## Environment reference

**Stack variables.** These are set in Portainer and used by the stack file
itself:

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_IMAGE` | `ghcr.io/theuncleben/mtg-assistant-gateway` | Gateway image. A fork that builds its own images sets `ghcr.io/<owner>/mtg-assistant-gateway` |
| `MTG_TAG` | `latest` | Gateway image tag. Pin a release rather than relying on `latest` |
| `MTG_NODE` | none, required | Hostname of the node the gateway runs on. The stack won't start without it |
| `MTG_MEMORY_LIMIT` | `256M` | Memory cap |
| `MTG_NETWORK_NAME` | `mtg-gateway` | The overlay network from step 1. Must exist before you deploy and match its name exactly (case-sensitive) |
| `TIMEZONE` | `UTC` | Passed to the containers as `TZ` |

**Gateway settings.** Read by the gateway. The stack file already passes
through the ones marked *stack*; for any other, add a line under the
gateway's `environment:` in the stack file, or it has no effect.

| Variable | Required | Default | What it does |
| --- | --- | --- | --- |
| `MTG_PUBLIC_URL` | yes, *stack* | | Public https URL, no path |
| `MTG_OIDC_ISSUER` | yes, *stack* | | Identity provider issuer URL |
| `MTG_OIDC_CLIENT_ID` | yes, *stack* | | OIDC client ID |
| `MTG_OIDC_CLIENT_SECRET_FILE` | yes, *stack* | | File holding the OIDC client secret (`/run/secrets/mtg_oidc_client_secret`) |
| `MTG_FERNET_KEY_FILE` | yes, *stack* | | File holding the encryption key (`/run/secrets/mtg_fernet_key`) |
| `MTG_SESSION_SECRET_FILE` | yes, *stack* | | File holding a random secret of 32+ characters (`/run/secrets/mtg_session_secret`) |
| `MTG_OIDC_SCOPES` | no, *stack* | `openid profile email offline_access` | Scopes requested from the identity provider, used exactly as set. Keep `offline_access` in it: the provider's refresh token is what lets the gateway keep checking membership. Leave it out only for a provider that refuses it; members then sign in again whenever the provider's access token runs out |
| `MTG_OIDC_PREVIOUS_ISSUERS` | no, *stack* | | Only when you move the identity provider to a new address: the old issuer URL(s), comma separated, with `MTG_OIDC_ISSUER` set to the new one. Members whose account was created under an old address are moved to the new one at their next sign-in. Anyone else signing in with a different issuer than their account was created with is refused, so a second provider can't take over accounts |
| `MTG_MEMBERSHIP_CHECK_TTL` | no, *stack* | `5` | Before serving any request with a browser session or a gateway token, the gateway asks the identity provider's userinfo endpoint whether the person is still in `MTG_REQUIRED_GROUP` (and `MTG_ADMIN_GROUP`). The answer is reused for this many seconds, which is the longest a removed member can keep going. `0` asks on every request; 0 to 60. If the provider can't be reached, requests get 503 and nothing is revoked |
| `MTG_REQUIRED_GROUP` | yes, *stack* | | Group a user must be in. The gateway refuses to start with it empty unless `MTG_ALLOW_ANY_IDP_USER` is `true` |
| `MTG_ALLOW_ANY_IDP_USER` | no, *stack* | `false` | `true` lets an empty `MTG_REQUIRED_GROUP` through, so anyone your identity provider signs in gets in. Only for an identity provider that already admits nobody else |
| `MTG_ADMIN_GROUP` | no, *stack* | empty (no admin page) | Identity-provider group whose members get the `/admin` pages and `/api/v1/admin` (users, activity, metrics; disable, enable, revoke, unlink, delete data). Checked live like `MTG_REQUIRED_GROUP`. Members of this group may sign in even when they aren't in `MTG_REQUIRED_GROUP` (since 0.6.6). Unset or empty, the admin routes answer 404 for everyone. Details in [OPERATIONS.md](OPERATIONS.md#the-admin-page) |
| `MTG_AUTHENTIK_API_TOKEN_FILE` | no, *stack* | empty (off) | Authentik only. File holding an Authentik API token that may view only the gateway's one or two groups (and its own tokens, see IDP-AUTHENTIK.md §12) (`/run/secrets/mtg_authentik_api_token`, an optional secret). With it, once an hour the gateway asks Authentik who is in `MTG_REQUIRED_GROUP` and `MTG_ADMIN_GROUP` and deletes the stored Archidekt session of every linked member who is in neither or is deactivated. An answer that can't be trusted deletes nothing. A missing or unreadable file leaves the clean-up off with one warning at start. Setup: [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#12-optional-removed-member-clean-up) |
| `MTG_AUTHENTIK_API_URL` | no, *stack* | the scheme and host of `MTG_OIDC_ISSUER` | Where the clean-up reaches Authentik's API. https only |
| `MTG_OIDC_GROUPS_CLAIM` | no, *stack* | `groups` | Dot-path to the group list in the ID token (or userinfo) claims. Authentik: `groups`; Keycloak: `realm_access.roles`; Zitadel: `urn:zitadel:iam:org:project:roles` (a dict whose keys are the role names also works; a key whose value is empty or `false` does not count). The whole value is tried as one claim name first, so Auth0-style `https://example.com/groups` works too. Names are compared exactly, with no trimming. A missing or differently shaped claim means no groups, so `MTG_REQUIRED_GROUP` refuses the sign-in |
| `MTG_OIDC_TOKEN_AUTH_METHOD` | no, *stack* | `client_secret_post` | How the gateway sends its client secret to the token endpoint: `client_secret_post` (in the form body) or `client_secret_basic` (HTTP Basic header). Match what the client is configured for in the identity provider |
| `MTG_DATA_DIR` | no, *stack* | `/data` | Where the SQLite database lives. In the stack, the variable is the folder on the host, mounted at `/data` |
| `MTG_BACKUP_DIR` | no, *stack* | `/backups` in the image | Where nightly backups go; empty turns them off. In the stack, the folder on the host, mounted at `/backups` |
| `MTG_BACKUP_HOUR_UTC` | no, *stack* | `3` | Hour (UTC) of the nightly backup, 0 to 23 |
| `MTG_BACKUP_KEEP_DAYS` | no, *stack* | `14` | How many days of backups to keep, 1 to 3650 |
| `MTG_BACKUP_COPY_DIR` | no, *stack* | empty (no second copy) | A host folder (another disk, or storage every node mounts at the same path) that every backup is also copied to and pruned the same way. It must already exist on `MTG_NODE`. The stack mounts it at `/backup-copy`; left empty it mounts `/dev/null` there and nothing is copied. A failed copy is logged and shown on the admin page. See [OPERATIONS.md](OPERATIONS.md#backups) |
| `MTG_ALLOWED_HOSTS` | no, *stack* | worked out from the public URL | `Host` headers accepted on `/mcp` |
| `MTG_ACCESS_TOKEN_TTL` | no, *stack* | `3600` | Access token lifetime, seconds (60 to 86400) |
| `MTG_REFRESH_TOKEN_TTL` | no, *stack* | `2592000` | Refresh token lifetime, seconds (30 days; 3600 to 31536000) |
| `MTG_REAUTH_INTERVAL` | no, *stack* | `604800` | How long (seconds, default a week) after signing in an assistant can keep refreshing its tokens without a fresh sign-in (3600 to 31536000). Once this runs out the next refresh is refused and the person signs in again (and sees the gateway's consent page). Group membership doesn't wait for this: it is checked live with the identity provider on every request (`MTG_MEMBERSHIP_CHECK_TTL`) |
| `MTG_LOG_LEVEL` | no, *stack* | `INFO` | Logging level |
| `MTG_SERVER_NAME` | no, *stack* | `MTG Assistant Gateway` | Name shown on the gateway's pages, the install page and to assistants |
| `MTG_LISTEN_HOST`, `MTG_LISTEN_PORT` | no | `0.0.0.0`, `8080` | Address and port inside the container. Leave them; the stack, Compose file and health checks expect 8080 |
| `MTG_MYSTIC_FORGE_URL` | no, *stack* | empty (no research tools) | Internal Mystic Forge MCP URL; the stack sets `http://mtg-assistant-mysticforge:8000/mcp` |
| `MTG_WRITES_ENABLED` | no, *stack* | `false` | `true` lets approved proposals be applied to Archidekt |
| `MTG_APPROVAL_MODE_DEFAULT` | no, *stack* | `manual` | The approval mode of a member who has not chosen one on their Account page: `manual` (every change waits for their own press), `semi` (the assistant applies low-risk edits itself) or `auto` (the assistant applies everything). Members pick their own; see [OPERATIONS.md](OPERATIONS.md#approval-modes-when-the-assistant-may-apply-by-itself) |
| `MTG_APPROVAL_MODE_MAX` | no, *stack* | `auto` | The highest mode members may choose; `auto` means no cap. A stored choice above the cap is read as the cap |
| `MTG_AUTO_APPLY_MAX_ROWS` | no, *stack* | `5` | How many review rows an edit may have and still count as low risk in `semi` mode (1 to 100) |
| `MTG_APPLY_IN_CHAT` | no, *stack* | `true` | The Approve/Reject card an AI app that renders MCP Apps shows next to a proposal. Its Approve button calls `confirm_proposal` with a one-time code the assistant never sees; The same switch turns on the other cards (printings, recognised cards, a deck, the account) and the ten-minute signed data links they load (`GET /cards/data/`); `false` removes every card, the links and the tool, leaving the review page (and `apply_proposal` where the member's approval mode allows it) |
| `MTG_ARCHIDEKT_CALLS_PER_10_MIN` | no, *stack* | `120` | Archidekt work one member may start per 10 minutes (a deck read, a proposal, an apply, a link, and the proxied `archidekt_*` research tools), refilled evenly; past it the member gets `rate_limited` for a few minutes. Stops a looping assistant from keeping a steady stream of requests on Archidekt. 10 to 100000 |
| `MTG_ARCHIDEKT_MIN_INTERVAL` | no, *stack* | `1.0` | Seconds between two Archidekt requests, for everyone together (0.25 to 10). See [OPERATIONS.md](OPERATIONS.md#archidekt-rate-limiting) |
| `MTG_ARCHIDEKT_MAX_PER_MINUTE` | no, *stack* | `40` | Archidekt requests in any 60 seconds, for everyone together; past it requests wait their turn (1 to 120) |
| `MTG_ARCHIDEKT_RETRIES` | no, *stack* | `2` | Retries for a read that timed out or got a server error (0 to 4). Writes and "slow down" answers are never retried |
| `MTG_ARCHIDEKT_BACKOFF_BASE` | no, *stack* | `1.0` | Seconds; a retry waits a random time up to this x 2, 4... (capped at 10) (0.1 to 10) |
| `MTG_ARCHIDEKT_CACHE_SECONDS` | no, *stack* | `60` | How long anonymous public deck reads and deck searches are reused; cleared by any write the gateway sends (0 turns it off, up to 900) |
| `MTG_ARCHIDEKT_CARD_CACHE_SECONDS` | no, *stack* | `3600` | How long card lookups are reused (0 to 86400) |
| `MTG_ARCHIDEKT_BACKUPS` | no, *stack* | `true` | Before every applied edit or restore, copy the deck as a private deck into the user's backup folder on Archidekt (using Archidekt's own copy feature, so printings, finishes and categories are kept). If the copy fails, nothing changes and the proposal stays pending |
| `MTG_ARCHIDEKT_BACKUP_FOLDER` | no, *stack* | `MTG Gateway backups` | Name of that folder (at most 100 characters), created in the account's root folder the first time. Decks in it are left out of the assistant's deck list |
| `MTG_BROWSER_SESSION_TTL` | no, *stack* | `7200` | Browser session lifetime for `/account` and review pages, seconds (300 to 86400) |
| `MTG_ARCHIDEKT_BASE` | no, *stack* | `https://archidekt.com/api` | Archidekt API base. Only the tests change it |
| `MTG_TRUSTED_PROXIES` | no, *stack* | private networks (`10/8`, `172.16/12`, `192.168/16`, loopback) | Comma-separated IPs or CIDR ranges whose `X-Forwarded-*` headers are trusted. Recommended: narrow it to NPM's address or the overlay subnet (see [step 7](#7-reverse-proxy)) |
| `MTG_CIMD_ENABLED` | no, *stack* | `true` | Accept clients that identify themselves with a Client ID Metadata Document URL (Claude's "Use Claude's published identity" and, reportedly, ChatGPT). `false` leaves only automatic registration |
| `MTG_CIMD_ALLOWED_HOSTS` | no, *stack* | empty (any https host) | Comma-separated hostnames whose metadata documents are accepted, subdomains included. **Leave it empty** unless you know every client's host: listing only Claude's can lock ChatGPT out. Details in [OPERATIONS.md](OPERATIONS.md#which-ai-clients-may-connect) |
| `MTG_SCRYFALL_LOOKUP_INTERVAL` | no, *stack* | `0.5` | Seconds between single-card Scryfall lookups for card scanning, 0.1 to 5 |
| `MTG_PLUGIN_DIR` | no | `/usr/share/mtg-gateway/plugin` | Folder holding the assistant plugin shipped in the image; serves `/skill`, `/install` and `/plugin/` |
| `MTG_APP_DIR` | no | `/usr/share/mtg-gateway/app` | Folder holding the Android app (`mtg-assistant-gateway.apk` and `mtg-assistant-gateway.json`) that `/app` hands out; the release build puts it in the image, see [ANDROID.md](ANDROID.md) |
| `MTG_ANDROID_ASSETLINKS` | no | empty (an empty list is served) | The Android App Links statement list served at `/.well-known/assetlinks.json`, as one JSON list (the whole `assetlinks.json` content, starting with `[`). Anything that isn't valid JSON, or isn't a list, stops the gateway at startup with a config error. Only needed if you build the app's App Links flavor for this gateway ([ANDROID.md](ANDROID.md#12-app-links-opening-gateway-links-in-the-app)) |
| `PUID`, `PGID` | yes, *stack* | | User and group the process runs as |

The card scanning settings (`MTG_SCAN_*`) are listed in
[SCANNING.md](SCANNING.md#operator-notes).
