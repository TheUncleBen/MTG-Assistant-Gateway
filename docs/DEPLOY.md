# Deploying the gateway on Docker Swarm (with or without Portainer)

This gets you one Swarm stack with these services:

- `mtg-assistant-gateway`, the gateway itself;
- `mtg-assistant-mysticforge`, an internal research service the gateway talks to (more
  on it [near the end](#mystic-forge-internal-research-service));
- `mtg-assistant-ntfy`, an internal notification server for the members' Android app
  ([Notifications on members' phones](#notifications-on-members-phones)); and
- `mtg-assistant-forge`, the simulation engine, which runs only if you turn it on
  ([Forge simulations](#forge-simulations-optional)).

Not running a Swarm? [DEPLOY-COMPOSE.md](DEPLOY-COMPOSE.md) does the same on
one machine with plain Docker Compose. How the pieces fit together is in
[ARCHITECTURE.md](ARCHITECTURE.md), and what hardware it needs is in
[HARDWARE.md](HARDWARE.md).

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

The image names below are the project's own (`ghcr.io/theuncleben/...`). If you build the images
from your own copy of the project, put your GitHub account in place of `theuncleben`.

## 1. Overlay network (once, on a Swarm manager)

The stack doesn't create the network it shares with your reverse proxy. It
joins an existing overlay network by name, so create it before you deploy
(the small private networks between the gateway and Mystic Forge, ntfy and
Forge are created by the stack itself):

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

The stack expects five Swarm secrets with exactly these names. Secrets you already made for an
earlier version stay as they are: when you update, this step only adds a secret that is missing
(the commands in Option B skip any secret that already exists).

| Secret name | What goes in it |
| --- | --- |
| `mtg_fernet_key` | A random encryption key for linked Archidekt sessions and the identity provider's tokens the gateway keeps. **Keep a copy** (password manager or similar). Lose it and every linked account has to be relinked (and everyone signs in once more). The nightly backups hold no Archidekt sessions or sign-in tokens; the copy the gateway saves before an upgrade does, still encrypted with this key |
| `mtg_session_secret` | A random string of 32+ characters that signs the browser sign-in cookie. No need to keep a copy |
| `mtg_oidc_client_secret` | The Client Secret from the Authentik provider in step 3 |
| `mtg_ntfy_token` | The token the gateway uses with the stack's own notification server: `tk_` and 29 random lowercase letters and digits. You don't need to keep a copy. |
| `mtg_authentik_api_token` | The word `off`, unless you set up the optional hourly clean-up of removed members' stored Archidekt sessions: then the Authentik API token from [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#12-optional-removed-member-clean-up), which says how to swap `off` for the token later |

Make the random values on your own machine, right when you need them. They
only ever go into the secret, never into the stack's environment variables,
a file in this repository, or a chat with an assistant. Pick one of the two
ways below.

### Option A: in Portainer

1. On any machine with `openssl` (Linux, macOS, WSL or Git Bash), generate
   the three random values:

   ```bash
   # mtg_fernet_key: a Fernet key (URL-safe base64 of 32 random bytes)
   openssl rand -base64 32 | tr '+/' '-_'

   # mtg_session_secret
   openssl rand -base64 48

   # mtg_ntfy_token
   printf 'tk_%s\n' "$(LC_ALL=C tr -dc a-z0-9 </dev/urandom | head -c 29)"
   ```

2. In Portainer, pick your Swarm environment, then **Secrets**, and look at the list. For each
   secret below that it doesn't show yet, click **Add secret** (and skip the value for any secret
   you already have).
3. Name `mtg_fernet_key`, paste the first value into **Secret**, leave
   **Encode secret** on (the default), and click **Create the secret**.
   Save that value in your password manager now.
4. Do the same for `mtg_session_secret` (the second value),
   `mtg_ntfy_token` (the third), `mtg_oidc_client_secret` (paste the
   Client Secret straight from Authentik) and `mtg_authentik_api_token` (type the word `off`).
5. Clear your terminal (`clear`) so the values aren't left on screen.

### Option B: on a Swarm manager

This creates the same five secrets from the command line, each only if it doesn't exist yet, so
it is safe to run again. Nothing is written to disk or your shell history:

```bash
# Runs the rest of the line only when the secret named first doesn't exist yet.
missing() { if docker secret inspect "$1" >/dev/null 2>&1; then echo "$1 already exists"; false; fi; }

# 1. mtg_fernet_key. Prints the key once so you can save a copy, then creates the secret.
missing mtg_fernet_key && openssl rand -base64 32 | tr '+/' '-_' | tee /dev/stderr | docker secret create mtg_fernet_key -

# 2. mtg_session_secret. No need to keep a copy.
missing mtg_session_secret && openssl rand -base64 48 | docker secret create mtg_session_secret -

# 3. mtg_oidc_client_secret. Paste the Authentik Client Secret at the (silent) prompt and press Enter.
missing mtg_oidc_client_secret && read -rs S && printf %s "$S" | docker secret create mtg_oidc_client_secret - ; unset S

# 4. mtg_ntfy_token. You don't need to keep a copy.
missing mtg_ntfy_token && printf 'tk_%s' "$(LC_ALL=C tr -dc a-z0-9 </dev/urandom | head -c 29)" | docker secret create mtg_ntfy_token -

# 5. mtg_authentik_api_token: off, or see IDP-AUTHENTIK.md to put the token in.
missing mtg_authentik_api_token && printf off | docker secret create mtg_authentik_api_token -
```

Either way, Portainer's **Secrets** page (or `docker secret ls`) should now
list all five.

Swarm secrets can't be edited. To change one later, remove the stack (or
take the secret out of it), delete the secret, create it again with the
same name, and redeploy. Changing `mtg_fernet_key` means everyone relinks
Archidekt and signs in once more.

## 5. Images

The images are public packages on GitHub Container Registry (GHCR), so
Portainer and Docker pull them without a registry login or a GitHub token:

- `ghcr.io/theuncleben/mtg-assistant-gateway`
- `ghcr.io/theuncleben/mtg-assistant-mysticforge`
- `ghcr.io/theuncleben/mtg-assistant-forge` (only pulled if you turn Forge on)

The stack's ntfy server is the official `binwiederhier/ntfy` image from
Docker Hub, pinned in the stack file.

Updating later is in [UPGRADING.md](UPGRADING.md).

If tasks show `rejected` with `No such image: ghcr.io/...`, check the image
name and tag: a version's tag exists only once its release is published
([VERSIONS.md](VERSIONS.md)).

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
   | `MTG_IMAGE`, `MTG_TAG` | `ghcr.io/theuncleben/mtg-assistant-gateway` and `latest` to follow every new version, or one version to stay on it, written `X.Y.Z` (the versions are in [VERSIONS.md](VERSIONS.md)) |
   | `MTG_PUBLIC_URL` | `https://mtg.example.com` |
   | `MTG_OIDC_ISSUER` | the issuer URL from step 3 |
   | `MTG_OIDC_CLIENT_ID` | the Client ID from step 3 |
   | `MTG_DATA_DIR` | `/srv/mtg-gateway/data` |
   | `MTG_BACKUP_DIR` | `/srv/mtg-gateway/backups` |
   | `MTG_NODE` | the hostname of the node from step 2 (`docker node ls` shows it) |
   | `PUID`, `PGID` | the user and group that own the folders from step 2, for example `1000` |
   | `TIMEZONE` | your time zone, for example `Europe/London` |
   | `MTG_REQUIRED_GROUP` | the group from step 3, spelled exactly as your identity provider sends it (it's case-sensitive). Required: the gateway won't start with it empty unless you also set `MTG_ALLOW_ANY_IDP_USER=true` (anyone your identity provider lets through gets in) |
   | `MF_IMAGE`, `MF_NODE` | `ghcr.io/theuncleben/mtg-assistant-mysticforge`, and the node Mystic Forge should run on (can be the same one) |
   | `MTG_WRITES_ENABLED` | `true` to let approved proposals change Archidekt; `false` keeps everything review-only. Make your first edit on a deck you don't care about |
   | `MTG_APPROVAL_MODE_DEFAULT` | Leave it `manual` (the example file and the code default): a member who has not chosen an approval mode has every change wait for their own press, on the Approve button of the card in the chat or on Apply on the review page. Each member picks their own mode (`manual`, `semi`: low-risk changes, such as a small edit or a private copy of a deck, apply without asking, `auto`: most changes apply without asking, but some still wait for the member) on their Account page |
   | `MTG_APPROVAL_MODE_MAX` | `auto` (no cap) lets members choose any mode; `semi` or `manual` caps what they may choose |
   | `MTG_APPLY_IN_CHAT` | `true` (the default): Claude and ChatGPT show each proposal as a card with Approve and Reject buttons, and only the card's one-time code can apply it; the waiting-changes card and the two pickers (a card's printings, recognised cards) come with it. `false` removes every card; members use the review page |

   Everything else can stay at its default. The full list is in the
   [environment reference](#environment-reference) below.
4. Click **Deploy the stack**. The gateway should go healthy within about
   30 seconds. Check from a manager with
   `docker service logs mtg_mtg-assistant-gateway`.

### Updating

Every update, and the extra steps some versions need (0.7.30 among them), is in
[UPGRADING.md](UPGRADING.md).

### Without Portainer

The same stack from a Swarm manager's shell. `docker stack deploy` doesn't
read `.env` files, so load the variables into the shell first (this loop
copes with values that contain spaces, which `. ./file` doesn't):

```bash
cp deploy/stack.env.example mtg.env    # edit the REQUIRED lines
while IFS= read -r line; do case "$line" in ''|\#*) ;; *) export "$line" ;; esac; done < mtg.env
docker stack deploy -c deploy/portainer-stack.yml mtg
```

To update later, follow [UPGRADING.md](UPGRADING.md), which ends with the last two lines
again.

The gateway is one Python process. In the end-to-end test (amd64, idle after
a few sign-ins) it sat at about 60 MB. The stack caps it at
`MTG_MEMORY_LIMIT` (default 256 MB).

If the gateway's logs say `configuration error: MTG_REQUIRED_GROUP is empty`,
set `MTG_REQUIRED_GROUP` (or, if you really want everyone your identity
provider signs in, `MTG_ALLOW_ANY_IDP_USER=true`) and update the stack.
Stacks deployed before this check existed with an empty group stop at this
point after an upgrade.

The gateway, Mystic Forge and Forge run with every Linux capability dropped
except the four their start-up script needs to switch to `PUID:PGID` (`CHOWN`, `DAC_OVERRIDE`,
`SETUID`, `SETGID`), and the switched process can't gain privileges back
(`setpriv --no-new-privs`). ntfy runs as `PUID:PGID` from the start, with
no capabilities at all. Swarm applies `cap_drop`/`cap_add` from Docker
20.10 on; older engines ignore them and print `Ignoring unsupported options`
at deploy. The root filesystem is left writable: when `PUID`/`PGID` differ
from the image's 1000:1000 the start-up script edits `/etc/passwd` and
`/etc/group`. Swarm ignores `security_opt`, so the stack doesn't set
`no-new-privileges` there (`setpriv --no-new-privs` covers the running
process), and it sets no process limit because older Docker and Portainer
versions refuse the `pids` key and the stack wouldn't deploy. The Compose
file ([DEPLOY-COMPOSE.md](DEPLOY-COMPOSE.md)) sets both.

The images are built on the official Python (and, for Forge, Eclipse
Temurin) base images, pinned by digest: a rebuild uses exactly the pinned
base image, and newer base patches arrive when a release moves the pin. The base
image is fetched from Google's public mirror of Docker Hub (`mirror.gcr.io`,
the same images and digests), because Docker Hub limits anonymous pulls per
address and shared CI runners hit that limit. As a fallback for anything a
build still fetches from Docker Hub, the image jobs sign in to Docker Hub when
the repository secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (a
read-only access token) exist; without them the step is skipped. The
Mystic Forge image is built once per tag (`1.3.2-mag5`) and not rebuilt by
later releases; a manual run of its workflow from `main` rebuilds it from
the base image `main` pins. GitHub
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
  gateway has no websocket endpoints (MCP answers with plain JSON over HTTP,
  and the Android app's notification stream is plain HTTP too). If you turned it on in an earlier deploy,
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

  The first two keep NPM from holding back the Android app's notification
  stream and from cutting off long requests. The last
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
scripted floods you need two pieces in two different places. They are not
interchangeable: Nginx accepts `limit_req_zone` only at its top level, and
NPM's Advanced tab is inside a `server` block, so pasting it there makes
Nginx reject the whole proxy host and the site goes offline (a certificate
or connection error in the browser, `nginx: [emerg]` in NPM's log) until
you take the line out again.

1. The zone, in a file on the NPM host, **never in the Advanced tab**:
   `/data/nginx/custom/http_top.conf` inside NPM's data folder (the folder
   you mounted at `/data`; create the `custom` folder and the file if they
   don't exist), then restart the NPM container:

   ```nginx
   limit_req_zone $binary_remote_addr zone=mtg_oauth:10m rate=30r/m;
   ```

2. The limit, in the gateway proxy host's **Advanced** tab, under the
   lines above (this part belongs there, and only works once step 1 is in
   place; without it NPM rejects the host too):

   ```nginx
   location ~ ^/(register|authorize|token|login)$ {
     limit_req zone=mtg_oauth burst=30 nodelay;
     include conf.d/include/proxy.conf;
   }
   ```

A normal connection makes a handful of these calls, so these numbers never
get in a real person's way. Leave it out if you'd rather not maintain custom
NPM files. If the site is offline after any change to the Advanced tab,
remove what you pasted and save: NPM goes back to the last configuration
that worked (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md#the-site-doesnt-load)).

A `502 Bad Gateway` from NPM means it can't reach the gateway. Check that
NPM's stack has the network from step 1 (exact name) and was redeployed
after you added it, and that the forward hostname is right. From a manager,
`docker exec <npm-container> curl -s http://mtg_mtg-assistant-gateway:8080/healthz`
should print `{"status":"ok"}` (or `{"status":"degraded"}`, see step 8).

## 8. Check it works

From any machine:

```bash
curl -s https://mtg.example.com/healthz
# {"status":"ok"}
# ("degraded" means the gateway works but the research service, or Forge
#  when it's on, doesn't answer: check the Mystic Forge and Forge services.
#  The admin page's System card shows the version and which one is down.)

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

**Android app (optional).** Signed in, open `/app`. If it offers a download,
the app is in your image and there is nothing to do. If it says there is no
app file, your image was built without one; add it as described in
[ANDROID.md](ANDROID.md#adding-the-app-to-a-gateway). Everything works in a
phone's browser without it.

The repository's end-to-end test runs these same steps automatically on a
single-node Swarm with a real Authentik: [tests/e2e/README.md](../tests/e2e/README.md).

## Mystic Forge (internal research service)

The same stack runs `mtg-assistant-mysticforge`, which is
[Mystic Forge](https://github.com/Kautiontape/mystic-forge) 1.3.2 (MIT
licence, from upstream). It's built from upstream commit
`dd82cd1449645862a2e309ae70df2fb7c3a2a839` into
`ghcr.io/theuncleben/mtg-assistant-mysticforge`. It provides the card search, EDHREC,
rules, precon and goldfish tools, and the gateway relays an allowlisted set
of them to signed-in users.

Upstream code is unchanged apart from eight patches in
`docker/mystic-forge/patches/`, which is where the `-mag5` in the image tag
`1.3.2-mag5` comes from:

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
- `0004-research-output-live-shapes.patch`: fixes for EDHREC page changes and
  more. EDHREC's precon, salt and top-card pages changed
  (`edhrec_precon_upgrade` showed "?%", `edhrec_salt` no scores,
  `edhrec_top_cards` no section headings); recommendations no longer suggest
  cutting cards that were not given; double-faced cards given by their full
  name are found by the goldfish tools; precon lists add up split basic land
  lines; and the simulation's honesty report no longer files played lands
  and mana rocks as low-impact.
- `0005-scryfall-default-printing-and-finish.patch`: a card given without a
  set used Scryfall's newest printing, which can be a preview that is not out
  yet (no paper, no prices). Price lists and `format_archidekt` now use the
  newest printing already out on paper, in the finish asked for, and say when
  a card does not come in that finish.
- `0007-goldfish-constructed-games.patch`: the goldfish simulator modelled
  Commander games only and took a list's first line as the commander. The
  patch adds `constructed` to `goldfish_run`, `goldfish_ab` and
  `goldfish_annotate`: no commander, 20 life, no free mulligan and on the
  play (no draw on turn 1). The gateway sets it for a deck without a
  commander and with 60 or more cards. Commander games are unchanged.
- `0008-goldfish-skipped-copies-and-named-printing.patch`: the simulation's
  "not recognized by Scryfall, skipped" warning says how many copies of each
  card were left out ("2 Foo"), and `scryfall_named` without a set shows the
  same printing as the other lookups (the newest out on paper) and says which
  one it is.
- `0009-goldfish-rules-text-templates.patch`: the goldfish simulator reads
  common rules text (drawing, ramp, tokens, damage and drain, +1/+1 counters,
  anthems, cost reducers, extra combats and more), so far more cards play
  with their effect. Removal, board wipes and counterspells are counted as
  set aside instead of keeping a card from being cast. A card whose text is
  only partly read plays the read parts, unless an unread part limits them
  ("only once each turn", for example); `goldfish_annotate` lists what was
  read and what was not.

What to know about it:

- **It's not public.** No published ports, no login of its own. Don't make
  an NPM proxy host for it. It listens on port 8000 on a network private to
  the stack (`<stack>_mysticforge`, so `mtg_mysticforge` here; created and removed with the stack, not
  attachable), and the gateway is the only other service on it. It isn't on
  `mtg-gateway`, so NPM and other containers there can't reach it. The gateway
  still finds it as `mtg-assistant-mysticforge`. The private network isn't encrypted;
  only research queries cross it.
- **No extra setup.** No secrets, no host folders. Its `/data` lives inside the container and resets on
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
| `MF_IMAGE` | `ghcr.io/theuncleben/mtg-assistant-mysticforge` | Image. A copy of the project that builds its own images sets `ghcr.io/<your account>/mtg-assistant-mysticforge` |
| `MF_TAG` | `1.3.2-mag5` | Image tag |
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

## Forge simulations (optional)

Forge ([Card-Forge/forge](https://github.com/Card-Forge/forge), GPL-3.0) is an open-source Magic rules
engine. The `mtg-assistant-forge` image runs it unmodified behind a small job service, so
`run_deck_report` can play real games of a deck against current Commander precons. It is off by
default, and an install that leaves it off behaves exactly as before.

- **Turn it on**, in one of two ways depending on how you deploy (the two addresses are
  alternatives, not two steps; use only the one for your setup):
  - Portainer or Swarm stack: set `FORGE_REPLICAS=1`, `FORGE_NODE` to a node with spare memory, and
    `MTG_FORGE_URL=http://mtg-assistant-forge:8000`, then update the stack.
  - Docker Compose: start the `forge` profile (`COMPOSE_PROFILES=forge`) and set
    `MTG_FORGE_URL=http://forge:8000`.
- **The image tag:** the stack files name the Forge image by `FORGE_TAG` (default `2.0.15-mag5`).
  GitHub builds and publishes a new tag the first time `main` carries it (the Forge image
  workflow, after its test games pass on arm64 and amd64), so pulling a new tag fails until that
  run has finished. After an update that changes the tag, wait for the run, then update the stack.
- **No login between the gateway and Forge.** Forge's job service takes jobs from anything that
  can reach it. The stack files keep it on a private network (`forge`) that only the gateway joins,
  with no published port and no internet access, so nothing else can. Keep it that way: don't
  publish its port, don't proxy it, and don't attach other containers to that network.
- **Load:** Java starts only while Forge plays, limited by `FORGE_MEMORY_LIMIT` (default 1536M) and
  `FORGE_CPUS` (default 2); where Docker can't cap memory, see the memory note below. `MTG_FORGE_GAMES` (default 4) sets the games per
  report; **Play more games** on a finished report plays that many again against the same
  opponents and adds them to the results (50 games a report at most; on a comparison, the same
  number to both decks). An idle Forge service holds
  no Java memory.
- **Games at once:** each game of a run is its own Java process with its own seed, so up to 16 of a
  run's games can play at once when Forge has room (up to 4 with a Forge image before `2.0.15-mag4`). The seed is kept with the report for the record:
  Forge does not always replay a game exactly, so compare runs by their win rates and 95% intervals,
  not game by game. `MTG_FORGE_PARALLEL_GAMES` (default 4, or the admin Settings page) is how many
  one report asks for at once, and `FORGE_MAX_PARALLEL` on the Forge service is the most it allows,
  for all runs together: runs start in order, and a run that uses fewer lets the next one play
  beside it (with 2 or more, a Compare's second deck plays beside the first when a place is still
  free for it). Left empty (the default), Forge works the number out from its container: one game
  per 2 CPUs and per 1.2 GB of memory, 1 to 16, which is 1 inside the default limits; its log and
  `/health` (`max_parallel`, `max_parallel_from`) say which. Each game takes up to `FORGE_XMX` of
  Java memory. Measured on GitHub's build machines (not a Pi) with the default limits (1536M, 2
  CPUs, 15 four-player games, one game per Java process): one at a time peaked at about 600 MiB;
  two at once at about 970 to 1,050 MiB, and finished about 25% sooner on the arm64 machine but
  about 14% later on the x86 machine; three at once fit in the memory (about 1,210 to 1,380 MiB) but
  slowed the games so much that 3 and 7 of the 15 reached the four-minute limit. To play more at
  once, raise `FORGE_MEMORY_LIMIT` and `FORGE_CPUS`. Set `FORGE_MAX_PARALLEL` by hand only to a
  number the machine holds (in these measurements, 2 in 1536M and 2 CPUs, not 3).
  `FORGE_GAME_SECONDS` (default 240, four minutes) is how long one game may run before Forge calls
  it a draw; such games are counted as draws in the report. Forge reads a card's rules only when a
  game needs that card, which roughly halves each Java start (measured 7 s to 3 s on a 4-CPU PC).
  This is fixed in the Forge image.
- **Memory where Docker can't cap it:** on Raspberry Pi OS the kernel's memory controller is off by
  default, so Docker drops the stack's `FORGE_MEMORY_LIMIT` and nothing caps Forge's memory. Forge
  then guards its own memory from inside, with nothing to change on the machine: the stack also
  passes `FORGE_MEMORY_LIMIT` as `FORGE_MEMORY_BUDGET`, which sizes the games at once when no limit
  can be read; each Forge game gets a cap on its extra Java memory for loaded code, on top of
  `FORGE_XMX` (`FORGE_MAX_METASPACE`, default `256m`); and `FORGE_OOM_SCORE_ADJ` (default 1000)
  sets how readily the kernel stops a Forge game, so if the machine runs out of memory while Forge
  plays, the kernel stops a Forge game before the gateway or other services. These keep Forge in bounds, but
  they are not a hard limit as the kernel's memory controller is.
- **More than one machine** (optional; one copy is the default): the gateway can use several
  copies of the Forge service, for example a Pi and an x86 PC, each playing whole runs.
  `MTG_FORGE_URL` stays the same. The stack's Forge service uses `endpoint_mode: dnsrr`, so the
  gateway sees each copy's address; it starts each run on the copy with the most free places and
  keeps asking that copy about it. One run still plays on one copy, never split across machines.
  Placement is your choice: instead of `node.hostname == ${FORGE_NODE}`, label each machine
  (`docker node update --label-add mtg-forge=true <node>`), set the constraint to
  `node.labels.mtg-forge == true`, add `max_replicas_per_node: 1` under `placement`, and set
  `FORGE_REPLICAS` to the number of labelled machines. ARM and x86 machines can be mixed (the
  image is built and tested for both). The CPU and memory limits apply to each copy, so size them
  for the smallest machine. With Docker Compose, `docker compose up --scale forge=2` gives two
  copies on one host. The gateway's handling of several copies is so far tested only with a
  stand-in, not yet on real machines or on a mix of ARM and x86.
- **No upkeep:** the image carries Forge's card scripts, an index of Scryfall's alternative printed
  names, and the newest Commander precons as opponents, each with a bracket estimated from the game
  changers it holds (Scryfall's list, read once when CI builds the image). The gateway seats the
  precons of the tested deck's bracket. Each image is rebuilt from a Forge release in CI; you update
  it like any other image.
- **Privacy:** the service is on its own internal network with no internet access; only the gateway
  reaches it.
- Cards Forge cannot play are listed in each report's `forge.not_played`, and the games run without
  them. Which cards those are: [CAPABILITIES.md](CAPABILITIES.md#cards-forge-cannot-play).

## Notifications on members' phones

Members can get a short message on their phone when an assistant leaves changes waiting for their
press (one message per deck, such as "2 changes to Atraxa are waiting for you", updated as the count changes
and removed when nothing waits), a deck report finishes (Forge runs that waited in the queue
included), a watched card crosses a price alert they set, or their daily or weekly summary is
ready. They come in the gateway's own Android app. There is no iPhone version, no Firebase or
Google push service, and no separate notification app.

How it works: the stack runs its own [ntfy](https://ntfy.sh/) server (`mtg-assistant-ntfy`, free
and open source) on a private network with no published port and no internet access. The gateway
is its only user: it publishes each member's messages, with the `mtg_ntfy_token` secret, to the
member's own topic (`mtg-`, 16 random characters and a 6-character mark made with a key kept in the
gateway's database), and ntfy refuses everything else. The gateway refuses an app connection whose
topic has no valid mark, and at every start it gives a new topic to each member whose topic has none
(after the update to 0.7.30, and after a rollback and a new update). The key is not one of the
stack's secrets, so changing a secret doesn't disconnect any phone from its notifications. Debug
logs show a topic only as `mtg-…`. The app
holds one long request open to the gateway's own address (`/app/notifications/stream`), and the
gateway passes the member's messages along it. ntfy keeps messages for 12 hours, in memory, so a
phone that was offline gets them when it is back; a restart of the ntfy service drops messages not
yet picked up.

- **No new proxy host.** Don't make one for ntfy. The stream goes through the gateway's existing
  one; the gateway sends a small keepalive every 25 seconds, so a proxy read timeout of 60 seconds
  or more is enough, and it asks nginx (and Nginx Proxy Manager) not to buffer the stream. A proxy
  that buffers responses anyway delays every message until the buffer fills.
- **What a message holds:** a title, one short line and a link to the gateway page (the review
  page, the report, the watchlist, the Account page's summary). Never a deck's cards. A price
  alert names the watched card and its old and new price; a summary gives counts and totals and
  the card that moved most. The token is read from the secret file, sent only in the
  `Authorization` header and never written to the log.
- **Members** turn notifications on in the **Notifications** card of their Account page, in the
  app: they tick **Send me notifications** and press **Save notifications**, which in the app
  also presses **Get notifications on this phone** for them. That asks once to let the app run in the background, and on
  Android 13 and later it first asks to allow notifications. Android then shows a quiet "Always
  connected" notification while the app listens. The app is built to reconnect after the phone restarts and after an app update; that is tested on an emulator, not yet on a real phone. **Disconnect my phones** gives
  the member a new topic, which signs out every phone; turning notifications off ends the stream
  too. **Send test** sends one message to check it, at most once a minute.
- **Memory:** ntfy is limited to 128 MB. This is a setting: `NTFY_MEMORY_LIMIT` in your
  environment, listed in both example files.
- **Connections:** every phone reaches ntfy through the gateway, so ntfy counts them all as one
  visitor. The stack files raise ntfy's limits for that visitor to 250 subscriptions (ntfy's
  default is 30) and a burst of 600 requests, one more each second (ntfy's defaults are 60, one
  more every 5 s). The gateway takes at most 200 app connections in all and at most 4 for one
  member's topic; past those it answers `503 busy` and `429`, and the app tries again by itself.
  These values are fixed, not settings: the ntfy limits are written into the ntfy service in the
  stack files, and the gateway's limits, the 25-second keepalive and the 12 hours ntfy keeps
  messages are fixed too. ntfy's subscription limit (250) is set above the gateway's 200
  connections on purpose.

## Environment reference

**Stack variables.** These are set in Portainer and used by the stack file
itself:

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_IMAGE` | `ghcr.io/theuncleben/mtg-assistant-gateway` | Gateway image. A copy of the project that builds its own images sets `ghcr.io/<your account>/mtg-assistant-gateway` |
| `MTG_TAG` | `latest` | Gateway image tag. Pin a release rather than relying on `latest` |
| `MTG_NODE` | none, required | Hostname of the node the gateway runs on. The stack won't start without it |
| `MTG_MEMORY_LIMIT` | `256M` | Memory cap |
| `MTG_NETWORK_NAME` | `mtg-gateway` | The overlay network from step 1. Must exist before you deploy and match its name exactly (case-sensitive) |
| `TIMEZONE` | `UTC` | Passed to the containers as `TZ` |

**Gateway settings.** Read by the gateway. The ones marked *stack* are
passed by the stack file and the Compose file and listed in their example env
files. `FORGE_REPLICAS`, `FORGE_NODE` and `MTG_NODE` are Swarm-only. The ones
marked *fixed* are not settings: the stack file or the image sets them, so
leave them as they are.

Since 0.7.29 most of the tuning settings below can also be changed by an
admin on the gateway's Settings page (`/admin/settings`, see
[OPERATIONS.md](OPERATIONS.md#the-settings-page)). The stack file passes those
through empty unless your environment sets them, and `stack.env.example`
lists them commented out with their default. A value you set in the
environment wins and locks the setting: the page shows it greyed out with its
value. Leave it out to manage it on the page.

| Variable | Required | Default | What it does |
| --- | --- | --- | --- |
| `MTG_PUBLIC_URL` | yes, *stack* | | Public https URL, no path |
| `MTG_OIDC_ISSUER` | yes, *stack* | | Identity provider issuer URL |
| `MTG_OIDC_CLIENT_ID` | yes, *stack* | | OIDC client ID |
| `MTG_OIDC_CLIENT_SECRET_FILE` | yes, *fixed* | | File holding the OIDC client secret (`/run/secrets/mtg_oidc_client_secret`). Set by the stack file to the Docker secret's path; not a setting |
| `MTG_FERNET_KEY_FILE` | yes, *fixed* | | File holding the encryption key (`/run/secrets/mtg_fernet_key`). Set by the stack file to the Docker secret's path; not a setting |
| `MTG_SESSION_SECRET_FILE` | yes, *fixed* | | File holding a random secret of 32+ characters (`/run/secrets/mtg_session_secret`). Set by the stack file to the Docker secret's path; not a setting |
| `MTG_OIDC_SCOPES` | no, *stack* | `openid profile email offline_access` | Scopes requested from the identity provider, used exactly as set. Keep `offline_access` in it: the provider's refresh token is what lets the gateway keep checking membership. Leave it out only for a provider that refuses it; members then sign in again whenever the provider's access token runs out |
| `MTG_OIDC_PREVIOUS_ISSUERS` | no, *stack* | | Only when you move the identity provider to a new address: the old issuer URL(s), comma separated, with `MTG_OIDC_ISSUER` set to the new one. Members whose account was created under an old address are moved to the new one at their next sign-in. Anyone else signing in with a different issuer than their account was created with is refused, so a second provider can't take over accounts |
| `MTG_MEMBERSHIP_CHECK_TTL` | no, *stack* | `5` | Before serving any request with a browser session or a gateway token, the gateway asks the identity provider's userinfo endpoint whether the person is still in `MTG_REQUIRED_GROUP` (and `MTG_ADMIN_GROUP`). The answer is reused for this many seconds, which is the longest a removed member can keep going. `0` asks on every request; 0 to 60. If the provider can't be reached, requests get 503 and nothing is revoked |
| `MTG_REQUIRED_GROUP` | yes, *stack* | | Group a user must be in. The gateway refuses to start with it empty unless `MTG_ALLOW_ANY_IDP_USER` is `true` |
| `MTG_ALLOW_ANY_IDP_USER` | no, *stack* | `false` | `true` lets an empty `MTG_REQUIRED_GROUP` through, so anyone your identity provider signs in gets in. Only for an identity provider that already admits nobody else |
| `MTG_ADMIN_GROUP` | no, *stack* | empty (no admin page) | Identity-provider group whose members get the `/admin` pages and `/api/v1/admin` (users, activity, metrics; disable, enable, revoke, unlink, delete data). Checked live like `MTG_REQUIRED_GROUP`. Members of this group may sign in even when they aren't in `MTG_REQUIRED_GROUP` (since 0.6.6). Unset or empty, the admin routes answer 404 for everyone. Details in [OPERATIONS.md](OPERATIONS.md#the-admin-page) |
| `MTG_AUTHENTIK_API_TOKEN_FILE` | no, *stack* | `/run/secrets/mtg_authentik_api_token` in the stack and the Compose file (empty in the image, which means off) | Authentik only. File holding an Authentik API token that may view only the gateway's one or two groups (and its own tokens, see IDP-AUTHENTIK.md §12): the `mtg_authentik_api_token` secret, where the word `off` keeps the clean-up off. With a token, once an hour the gateway asks Authentik who is in `MTG_REQUIRED_GROUP` and `MTG_ADMIN_GROUP` and deletes the stored Archidekt session of every linked member who is in neither or is deactivated. An answer that can't be trusted deletes nothing. A missing or unreadable file leaves the clean-up off with one warning at start. Setup: [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#12-optional-removed-member-clean-up) |
| `MTG_AUTHENTIK_API_URL` | no, *stack* | the scheme and host of `MTG_OIDC_ISSUER` | Where the clean-up reaches Authentik's API. https only |
| `MTG_OIDC_GROUPS_CLAIM` | no, *stack* | `groups` | Dot-path to the group list in the ID token (or userinfo) claims. Authentik: `groups`; Keycloak: `realm_access.roles`; Zitadel: `urn:zitadel:iam:org:project:roles` (a dict whose keys are the role names also works; a key whose value is empty or `false` does not count). The whole value is tried as one claim name first, so Auth0-style `https://example.com/groups` works too. Names are compared exactly, with no trimming. A missing or differently shaped claim means no groups, so `MTG_REQUIRED_GROUP` refuses the sign-in |
| `MTG_OIDC_TOKEN_AUTH_METHOD` | no, *stack* | `client_secret_post` | How the gateway sends its client secret to the token endpoint: `client_secret_post` (in the form body) or `client_secret_basic` (HTTP Basic header). Match what the client is configured for in the identity provider |
| `MTG_DATA_DIR` | yes with Swarm, *stack* | `/data` | Where the SQLite database lives. In the stack, the variable is the folder on the host, mounted at `/data` |
| `MTG_BACKUP_DIR` | yes with Swarm, *stack* | `/backups` in the image | Where nightly backups go, and the copies saved before database upgrades (in its `before-upgrade` folder). In the stack and the Compose file, the folder on the host, mounted at `/backups`; the gateway itself always uses `/backups` there |
| `MTG_BACKUP_HOUR_UTC` | no, *stack* | `3` | Hour (UTC) of the nightly backup, 0 to 23 |
| `MTG_BACKUP_KEEP_DAYS` | no, *stack* | `14` | How many days of backups to keep, 1 to 3650. The copies saved before database upgrades are deleted after that many days too |
| `MTG_BACKUP_COPY_DIR` | no, *stack* | empty (no second copy) | A host folder (another disk, or storage every node mounts at the same path) that every backup is also copied to and pruned the same way. It must already exist on `MTG_NODE`. The stack mounts it at `/backup-copy`; left empty it mounts `/dev/null` there and nothing is copied. A failed copy is logged and shown on the admin page. See [OPERATIONS.md](OPERATIONS.md#backups) |
| `MTG_ALLOWED_HOSTS` | no, *stack* | worked out from the public URL | `Host` headers accepted on `/mcp` |
| `MTG_ACCESS_TOKEN_TTL` | no, *stack* | `3600` | Access token lifetime, seconds (60 to 86400) |
| `MTG_REFRESH_TOKEN_TTL` | no, *stack* | `2592000` | Refresh token lifetime, seconds (30 days; 3600 to 31536000) |
| `MTG_REAUTH_INTERVAL` | no, *stack* | `604800` | How long (seconds, default a week) after signing in an assistant can keep refreshing its tokens without a fresh sign-in (3600 to 31536000). Once this runs out the next refresh is refused and the person signs in again (and sees the gateway's consent page). Group membership doesn't wait for this: it is checked live with the identity provider as requests come in, with each answer reused for up to `MTG_MEMBERSHIP_CHECK_TTL` seconds |
| `MTG_LOG_LEVEL` | no, *stack* | `INFO` | Logging level |
| `MTG_SERVER_NAME` | no, *stack* | `MTG Assistant Gateway` | Name shown on the gateway's pages, the install page and to assistants. Set your own and the plugin's short name follows it (`Deck Helper` → `deck-helper`), and the public plugin files no longer name this project or link to its repository. Anyone who installed the plugin under the old short name installs it again from `/install`. The ChatGPT plugin file also uses this name as its developer name and author name, which OpenAI's plugin check refuses when either is over 120 characters ([plugin docs](https://developers.openai.com/plugins/llms-full.txt), `plugin_developer_name_too_long` and `plugin_author_name_too_long`, read 2026-10-16; not tested in ChatGPT). The 120 characters are counted after the same filter as `MTG_CHATGPT_NAME` below. A longer name is cut at a word there (inside the word only when the first 120 characters have no space), and the gateway says so in its log at start and on the admin page; the rest of the gateway keeps the whole name |
| `MTG_CHATGPT_NAME` | no, *stack* | empty (`MTG_SERVER_NAME`) | The ChatGPT connector name, when it should differ from `MTG_SERVER_NAME`: the name in the ChatGPT plugin file and the name the ChatGPT install steps tell people to type. Everything else keeps `MTG_SERVER_NAME`. Letters, digits, spaces and `. _ ' -` only: any run of other characters becomes a space, and repeated spaces become one; the 80-character count below is taken after that. OpenAI's plugin docs say a plugin file whose name is over 80 characters is refused on upload ([plugin docs](https://developers.openai.com/plugins/llms-full.txt), `plugin_display_name_too_long`, read 2026-10-15 and again 2026-10-16; not tested in ChatGPT). Their 30-character limit applies only to OpenAI's public plugin directory, which the gateway doesn't use, and a name typed when adding the connector by hand has no documented limit. When the ChatGPT connector name is over 80 characters, whether from this setting or from `MTG_SERVER_NAME` with this one empty, the gateway still starts and warns in three places: its log at start, the admin page and the install page's ChatGPT plugin-file step |
| `MTG_LISTEN_HOST`, `MTG_LISTEN_PORT` | no, *fixed* | `0.0.0.0`, `8080` | Address and port inside the container. Not a setting: the stack, Compose file and health checks expect 8080 |
| `MTG_MYSTIC_FORGE_URL` | no, *stack* | empty (no research tools) | Internal Mystic Forge MCP URL; the stack sets `http://mtg-assistant-mysticforge:8000/mcp` |
| `MTG_FORGE_URL` | no, *stack* | empty (Forge off) | Internal Forge runner URL; deck reports add real Forge games when set (see the Forge section above) |
| `MTG_FORGE_GAMES` | no, *stack* | `4` | Forge games per deck report (1 to 50); **Play more games** adds this many again |
| `FORGE_REPLICAS`, `FORGE_NODE` | no, *stack* | `0`, empty | Copies of the Forge service, and the node they run on. `0` leaves Forge off; `1` with `FORGE_NODE` is the usual setup. For copies on several machines, place them by a node label instead of `FORGE_NODE` (see "More than one machine" in the Forge section above) |
| *(node label)* `mtg-forge=true` | no | none | Only for Forge copies on several machines: label each machine (`docker node update --label-add mtg-forge=true <node>`) and place the Forge service by `node.labels.mtg-forge == true` instead of `FORGE_NODE` (see "More than one machine" in the Forge section above) |
| `MTG_FORGE_PARALLEL_GAMES` | no, *stack* | `4` | How many of a report's Forge games the gateway asks Forge to play at once (1 to 16). `FORGE_MAX_PARALLEL` caps it for all runs together. Also on the Settings page |
| `FORGE_MAX_METASPACE`, `FORGE_OOM_SCORE_ADJ` | no, *stack* | `256m`, `1000` | On the Forge service: each Forge game's extra Java memory for loaded code (on top of `FORGE_XMX`), and how readily the kernel stops a Forge game when the machine runs out of memory, 1 to 1000 (1000: Forge goes first; 0 turns this off; any other value is left out and Forge's log says why) |
| `FORGE_MAX_PARALLEL` | no, *stack* | empty | On the Forge service: the most games Forge plays at once, for all runs together (1 to 16). Empty: one per 2 CPUs and 1.2 GB of the container's limits (see the Forge section above) |
| `FORGE_IMAGE`, `FORGE_TAG` | no, *stack* | `ghcr.io/theuncleben/mtg-assistant-forge`, `2.0.15-mag5` | The Forge image and its version. The stack file names one exact Forge version, which changes only when you deploy a newer stack file. Leave these out to use it; set `FORGE_TAG` only to pin Forge on purpose, and it then stays on that version even when the stack file moves on |
| `FORGE_XMX` | no, *stack* | `1g` | The Java heap limit (`-Xmx`) of each Forge game |
| `FORGE_MEMORY_LIMIT`, `FORGE_CPUS` | no, *stack* | `1536M`, `2` | The Forge container's memory and CPU caps. `FORGE_MEMORY_LIMIT` is also passed to Forge as `FORGE_MEMORY_BUDGET`, which sizes the games at once where Docker can't cap memory (see the Forge section above) |
| `FORGE_GAME_SECONDS` | no, *stack* | `240` | Seconds before Forge calls one game a draw |
| `FORGE_MAX_GAMES` | no, *stack* | `50` | The most games Forge accepts in one request; a run that asks for more is refused. Each report run and each **Play more games** round is one request for at most `MTG_FORGE_GAMES` games, so keep it at or above that value. The 50 games a report can hold is the gateway's own limit |
| `MTG_WRITES_ENABLED` | no, *stack* | `false` | `true` lets approved proposals be applied to Archidekt |
| `MTG_APPROVAL_MODE_DEFAULT` | no, *stack* | `manual` | The approval mode of a member who has not chosen one on their Account page: `manual` (every change waits for their own press), `semi` (the assistant applies low-risk changes itself, such as a small edit or a private copy of a deck) or `auto` (the assistant applies most changes itself; some still wait for the member). Members pick their own; see [OPERATIONS.md](OPERATIONS.md#approval-modes-when-the-assistant-may-apply-by-itself) |
| `MTG_APPROVAL_MODE_MAX` | no, *stack* | `auto` | The highest mode members may choose; `auto` means no cap. A stored choice above the cap is read as the cap |
| `MTG_AUTO_APPLY_MAX_ROWS` | no, *stack* | `5` | How many review rows an edit may have and still count as low risk in `semi` mode (1 to 100) |
| `MTG_AUTO_BIG_EDIT_ROWS` | no, *stack* | `20` | In `auto` mode, a deck or collection edit with more card rows than this waits for the member's press (1 to 100). Also on the Settings page |
| `MTG_PROPOSAL_EXPIRY_DAYS` | no, *stack* | `7` | Days a waiting change stays open for approval (1 to 30). A change nobody approves in that time expires; an assistant's amend starts the time again. Also on the Settings page |
| `MTG_APPLY_IN_CHAT` | no, *stack* | `true` | The Approve/Reject card an AI app that renders MCP Apps shows next to a proposal. Its Approve button calls `confirm_proposal` with a one-time code the assistant never sees; the same switch turns on the waiting-changes card, the two pickers (printings, recognised cards) and the ten-minute signed data link the printings card loads (`GET /cards/data/`); `false` removes every card, the links and the tool, leaving the review page (and `apply_proposal` where the member's approval mode allows it) |
| `MTG_UPDATE_CHECK` | no, *stack* | `true` | Twice a day, one anonymous request to GitHub's releases API for this project's newest release. When it is newer than the running gateway, admins see a notice on every page and the update steps on the admin overview (Portainer, Swarm, docker compose). Notice only: the gateway never pulls an image or touches Docker. `false` sends nothing |
| `MTG_ARCHIDEKT_CALLS_PER_10_MIN` | no, *stack* | `120` | Archidekt work one member may start per 10 minutes (a deck read, a proposal, an apply, a link, and a research tool such as goldfish or precon given an Archidekt deck id or link), refilled evenly; past it the member gets `rate_limited` until enough of it has refilled. Stops a looping assistant from keeping a steady stream of requests on Archidekt. 10 to 100000 |
| `MTG_ARCHIDEKT_MIN_INTERVAL` | no, *stack* | `1.0` | Seconds between two Archidekt requests, for everyone together (0.25 to 10). See [OPERATIONS.md](OPERATIONS.md#archidekt-rate-limiting) |
| `MTG_ARCHIDEKT_MAX_PER_MINUTE` | no, *stack* | `30` | Archidekt requests in any 60 seconds, for everyone together; past it requests wait their turn (1 to 120) |
| `MTG_ARCHIDEKT_RETRIES` | no, *stack* | `2` | Retries for a read that timed out or got a server error (0 to 4). Writes and "slow down" answers are never retried |
| `MTG_ARCHIDEKT_BACKOFF_BASE` | no, *stack* | `1.0` | Seconds; a retry waits a random time up to this x 2, 4... (capped at 10) (0.1 to 10) |
| `MTG_ARCHIDEKT_CACHE_SECONDS` | no, *stack* | `60` | How long anonymous public deck reads and deck searches are reused; cleared by any write the gateway sends (0 turns it off, up to 900). A member's deck page is also shown again from memory for up to 20 seconds (or this, if shorter) |
| `MTG_ARCHIDEKT_CARD_CACHE_SECONDS` | no, *stack* | `3600` | How long card lookups are reused (0 to 86400) |
| `MTG_ARCHIDEKT_BACKUPS` | no, *stack* | `true` | Before every applied edit or restore (except the deck page's own saves, Card search's Add to deck, and the deck editor's save with its backup box unticked), copy the deck as a private deck into the user's backup folder on Archidekt (using Archidekt's own copy feature, so printings, finishes and categories are kept). If the copy fails, nothing changes and the proposal stays pending |
| `MTG_ARCHIDEKT_BACKUP_COPIES` | no, *stack* | `10` | Backup copies kept per deck in that folder (1 to 50). Older ones are deleted from Archidekt one at a time, at most one a minute, once that deck gets its next new backup copy; a copy the member changed or moved on Archidekt, or one the gateway couldn't delete, is kept and no longer counts toward this number. Copies made before 0.7.29 are never deleted and don't count toward it either. Also on the Settings page |
| `MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT` | no, *stack* | `false` | Whether the deck editor's "Also keep a backup copy on Archidekt" box starts ticked. Also on the Settings page |
| `MTG_SNAPSHOTS_PER_DECK` | no, *stack* | `25` | Restore points kept per deck on the gateway (5 to 100), for History > Restore. One that records a backup copy the gateway may still delete is kept too. Also on the Settings page |
| `MTG_ARCHIDEKT_BACKUP_FOLDER` | no, *stack* | `MTG Gateway backups` | Name of that folder (at most 100 characters), created in the account's root folder the first time. Decks in it are left out of the assistant's deck list |
| `MTG_BROWSER_SESSION_TTL` | no, *stack* | `7200` | Browser session lifetime for `/account` and review pages, seconds (300 to 86400) |
| `MTG_ARCHIDEKT_BASE` | no, *stack* | `https://archidekt.com/api` | Archidekt API base. Only the tests change it |
| `MTG_TRUSTED_PROXIES` | no, *stack* | private networks (`10/8`, `172.16/12`, `192.168/16`, loopback) | Comma-separated IPs or CIDR ranges whose `X-Forwarded-*` headers are trusted. Recommended: narrow it to NPM's address or the overlay subnet (see [step 7](#7-reverse-proxy)) |
| `MTG_CIMD_ENABLED` | no, *stack* | `true` | Accept clients that identify themselves with a Client ID Metadata Document URL (Claude's "Use Claude's published identity" and, reportedly, ChatGPT). `false` leaves only automatic registration |
| `MTG_CIMD_ALLOWED_HOSTS` | no, *stack* | empty (any https host) | Comma-separated hostnames whose metadata documents are accepted, subdomains included. **Leave it empty** unless you know every client's host: listing only Claude's can lock ChatGPT out. Details in [OPERATIONS.md](OPERATIONS.md#which-ai-clients-may-connect) |
| `MTG_NTFY_URL` | no, *stack* | the stack's own ntfy server (`http://mtg-assistant-ntfy:8080`; `http://ntfy:8080` with Docker Compose) | The ntfy server the members' notifications go through. Leave it empty to use the stack's own. See [Notifications on members' phones](#notifications-on-members-phones) |
| `MTG_NTFY_TOKEN_FILE` | no, *stack* | `/run/secrets/mtg_ntfy_token` | File holding the token the gateway uses with ntfy (the `mtg_ntfy_token` secret); never logged |
| `NTFY_IMAGE` | no, *stack* | `binwiederhier/ntfy:v2.31.1`, pinned by digest | The stack's own ntfy server's image |
| `NTFY_MEMORY_LIMIT` | no, *stack* | `128M` | Memory cap of the stack's own ntfy server |
| `MTG_DECK_SYNC_MINUTES` | no, *stack* | `30` | Deck sync (Archidekt is the master copy): minutes between background checks of Archidekt for members with proposals waiting; a member opening the app (any signed-in page, at most once in 5 minutes) checks too. A check reads the member's deck list (one request per 50 decks, at most 4) and reads a deck in full only when it has a waiting proposal and Archidekt says it changed, or the deck is someone else's (at most every 30 minutes; one Archidekt says is gone or private is left alone for 6 hours), at most 5 per check, so a proposal made before an outside change is checked again at once: Updated when it still does what was reviewed, Needs a look when it would now do something else. Never writes to Archidekt or unlinks an account, never retries and waits behind every member's own requests. Each list or deck read spends one of the member's Archidekt actions (`MTG_ARCHIDEKT_CALLS_PER_10_MIN`), and a check stops while less than a quarter of them would be left. 0 to 1440; `0` turns it off |
| `MTG_WATCHLISTS_ENABLED` | no, *stack* | `false` (off) | Watchlists and prices. Off by default: the Watchlist page and its links, the card viewer's price buttons and the watch tools (`watch_card`, `watch_deck`, `unwatch`, `list_watchlist`, `card_price_history`) are absent and the daily price check does not run. Turning it off keeps members' stored watches and prices; old prices are still pruned after `MTG_PRICE_HISTORY_DAYS`. Summaries through ntfy work either way, without prices when it is off |
| `MTG_WATCH_MAX_CARDS` | no, *stack* | `200` | Cards one member may watch (0 to 5000; 0 turns card watching off) |
| `MTG_WATCH_MAX_DECKS` | no, *stack* | `10` | Decks one member may watch (0 to 200; 0 turns deck watching off). Each watched deck is read from Archidekt once a day, paced like every read |
| `MTG_PRICE_HISTORY_DAYS` | no, *stack* | `365` | Days of daily prices and deck values kept (30 to 3650); older ones go in the hourly clean-up |
| `MTG_PRICE_HOUR_UTC` | no, *stack* | `6` | Hour (UTC) of the daily price check and the members' summaries, 0 to 23. The check asks Scryfall only for watched printings (`POST /cards/collection`, 75 a request, at the single-lookup pace above) and makes no request at all while nobody watches anything |
| `MTG_SCRYFALL_LOOKUP_INTERVAL` | no, *stack* | `0.5` | Seconds between single-card Scryfall lookups for card scanning, 0.1 to 5 |
| *(no setting)* | — | — | Typed card-name suggestions come from Scryfall's card-name catalog (`GET https://api.scryfall.com/catalog/card-names`, about 700 KB), downloaded in the background at start-up and again once a day, and kept in memory (a few MB). Until it has loaded, or if Scryfall is unreachable, suggestions fall back to Scryfall's autocomplete at the interval above; nothing else changes |
| `MTG_PLUGIN_DIR` | no, *stack* | `/usr/share/mtg-gateway/plugin` | Folder holding the assistant plugin shipped in the image; serves `/skill`, `/install` and `/plugin/` |
| `MTG_APP_DIR` | no, *stack* | `/usr/share/mtg-gateway/app` | Folder holding the Android app (`mtg-assistant-gateway.apk` and `mtg-assistant-gateway.json`) that `/app` hands out; the release build puts it in the image, see [ANDROID.md](ANDROID.md) |
| `MTG_ANDROID_ASSETLINKS` | no, *stack* | empty (an empty list is served) | The Android App Links statement list served at `/.well-known/assetlinks.json`, as one JSON list (the whole `assetlinks.json` content, starting with `[`). Anything that isn't valid JSON, or isn't a list, stops the gateway at startup with a config error. Only needed if you build the app's App Links flavor for this gateway ([ANDROID.md](ANDROID.md#12-app-links-opening-gateway-links-in-the-app)) |
| `MTG_ANDROID_PACKAGE` | no, *stack* | `local.mtgassistantgateway.app` | The Android app's package ID, which the browser sign-in hands back to ([ANDROID.md](ANDROID.md#13-privacy-and-security-notes)). Change it only if you build the app with another package ID ([ANDROID.md](ANDROID.md#11-the-package-id)); anything that isn't a package ID stops the gateway at startup with a config error |
| `PUID`, `PGID` | yes with Swarm (Compose: `1000`), *stack* | | User and group the process runs as |

The card scanning settings (`MTG_SCAN_*`) are listed in
[SCANNING.md](SCANNING.md#operator-notes).
