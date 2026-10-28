# Deploying the gateway on Docker Swarm (with or without Portainer)

This gets you one Swarm stack with these services:

- `mtg-assistant-gateway`, the gateway itself;
- `mtg-assistant-mysticforge`, an internal research service the gateway talks to (more
  on it [near the end](#mystic-forge-internal-research-service));
- `mtg-assistant-ntfy`, an internal notification server for the members' Android app
  ([Notifications on members' phones](#notifications-on-members-phones)); and
- `mtg-assistant-forge`, the simulation engine, on by default in the example files
  ([Forge simulations](#forge-simulations-on-by-default)).

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

The image names in the stack files are the project's own (`ghcr.io/theuncleben/...`). If you build
the images from your own copy of the project, put your GitHub account in place of `theuncleben` in
the stack file's `image:` lines.

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
- `ghcr.io/theuncleben/mtg-assistant-forge` (on by default; not pulled if you turn Forge off)

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
   | `MTG_PUBLIC_URL` | `https://mtg.example.com` |
   | `MTG_OIDC_ISSUER` | the issuer URL from step 3 |
   | `MTG_OIDC_CLIENT_ID` | the Client ID from step 3 |
   | `MTG_REQUIRED_GROUP` | the group from step 3, spelled exactly as your identity provider sends it (it's case-sensitive). Required: the gateway won't start with it empty unless you also set `MTG_ALLOW_ANY_IDP_USER=true` (anyone your identity provider lets through gets in) |
   | `MTG_ADMIN_GROUP` | a group whose members also get the admin pages, the Settings page among them. Empty means no admin pages |
   | `MTG_NODE`, `FORGE_NODE` | the hostname of the node from step 2 (`docker node ls` shows it), and the node Forge runs on (can be the same one). `FORGE_NODE` is needed even with Forge off |
   | `MTG_DATA_DIR` | `/srv/mtg-gateway/data` |
   | `MTG_BACKUP_DIR` | `/srv/mtg-gateway/backups` |
   | `PUID`, `PGID` | the user and group that own the folders from step 2, for example `1000` |
   | `MTG_NETWORK_NAME` | the overlay network from step 1, spelled exactly (`mtg-gateway` there) |
   | `MTG_TAG` | `latest` to follow every new version, or one version to stay on it, written `X.Y.Z` (the versions are in [VERSIONS.md](VERSIONS.md)) |
   | `TIMEZONE` | your time zone, for example `Europe/London` |
   | `MTG_WRITES_ENABLED` | `true` (the default) lets approved proposals change Archidekt; `false` keeps everything review-only. Make your first edit on a deck you don't care about |

   Everything else can stay at its default. The feature switches (watchlists,
   backup copies on Archidekt, Approve buttons in the chat, and others) are
   all on; an admin turns them on or off on the Settings page
   (`/admin/settings`), along with the gateway's name and every other
   setting. Approvals start safe: a member who hasn't chosen an approval mode
   has every change wait for their own press, on the Approve button of the
   card in the chat or on Apply on the review page. Each member picks their
   own mode on their Account page; the Settings page sets the mode for new
   members and the highest one allowed. The full list of variables is in the
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
Mystic Forge image is built once per tag (`1.3.2-mag6`) and not rebuilt by
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

Upstream code is unchanged apart from eleven patches in
`docker/mystic-forge/patches/`, which is where the `-mag6` in the image tag
`1.3.2-mag6` comes from:

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
- `0010-goldfish-short-run-turn-labels.patch`: a run shorter than five turns
  said "made every land drop through T5" (and "by T6", "by T4" elsewhere);
  the text and the report tiles now name the last turn the run played.
- `0011-rules-download-at-start-and-labelled-card-faces.patch`: the
  research service starts downloading the current Comprehensive Rules when
  it starts, not on the first rules question, so answers right after a
  restart don't come from the older copy built into the image; and in card
  search results a card's second face is headed "Other face of …" instead
  of a bare `---`, which read like the start of another result.
- `0012-goldfish-set-aside-opponent-cards-and-read-more-wording.patch`: the
  solo test sets aside cards that only act on opponents or their creatures
  (votes, mass bounce, "bite" spells, Auras that shut a creature down) as
  needing opponents, instead of listing them as played without their
  effect, and it reads more card wording.

What to know about it:

- **It's not public.** No published ports, no login of its own. Don't make
  an NPM proxy host for it. It listens on port 8000 on a network private to
  the stack (`<stack>_mysticforge`, so `mtg_mysticforge` here; created and removed with the stack, not
  attachable), and the gateway is the only other service on it. It isn't on
  `mtg-gateway`, so NPM and other containers there can't reach it. The gateway
  still finds it as `mtg-assistant-mysticforge`. The private network isn't encrypted;
  only research queries cross it.
- **No extra setup.** No secrets, no host folders. Its `/data` lives inside the container and resets on
  redeploy, which is fine because the gateway keeps nothing of its own
  there.
- **Price downloads and notifications are off.** The stack sets
  `MYSTIC_FORGE_NO_INGEST=1` (skips a roughly 141 MB MTGJSON download and the
  nightly price job) and `MYSTIC_FORGE_NTFY_OFF=1` (no pushes to ntfy.sh).
  Both are fixed in the stack file on purpose, not stack variables.
- **Outbound calls.** When its tools are used it calls Scryfall, EDHREC,
  Commander Spellbook and Archidekt's public deck API (never logged in to
  Archidekt). The rules tools check Wizards of the Coast for a newer
  Comprehensive Rules at most once a day.
- **Where and how big.** The stack file fixes its image, `1.3.2-mag6`, and Swarm may run it on
  any node. Memory is capped at 512 MB. After loading
  the rules index it used about 115 MB on an amd64 test machine, and about
  170 MB as an arm64 container under emulation in CI (emulation adds
  overhead). Not yet measured on a Raspberry Pi or mid-goldfish.

Check it from a manager:

```bash
docker service ps mtg_mtg-assistant-mysticforge
docker service logs mtg_mtg-assistant-mysticforge
```

Healthy looks like `Running`, with the logs ending on Uvicorn listening on
`0.0.0.0:8000`.

## Forge simulations (on by default)

Forge ([Card-Forge/forge](https://github.com/Card-Forge/forge), GPL-3.0) is an open-source Magic rules
engine. The `mtg-assistant-forge` image runs it unmodified behind a small job service, so
`run_deck_report` can play real games of a deck against current Commander precons, and play the
deck alone by the full rules beside the solo test's quick estimate (the Forge solo test). It is on
in the example files; [HARDWARE.md](HARDWARE.md) says what machine it needs. Without it, reports
have the solo test's estimate and no Forge games.

- **On (the example files):** `FORGE_REPLICAS=1`, and on a Swarm `FORGE_NODE` set to a node with
  spare memory. Docker Compose uses the same `FORGE_REPLICAS` line.
- **Turn it off:** set `FORGE_REPLICAS=0` and update the stack (with Compose, `docker compose up -d`).
  The gateway follows it. On a Swarm `FORGE_NODE` still needs a node name.
- **The Forge solo test:** with every report that simulates, Forge also plays the deck alone
  against opponents who do nothing (three for a Commander deck, one for a 60-card deck),
  as many games as **Forge games per report** on the Settings page, counted up to turn 10, and the report shows how often and on which turn
  it won, beside the solo test's estimate. It starts after the report's games against precons,
  so it never holds them up.
  Forge prints a game only once it has ended, so each game plays until the deck wins or its time
  limit stops it; the gateway gives each round of games an equal part of about four and a half
  minutes of play (at least 30 seconds, at most Forge's four-minute limit for one game). Java starting up for each
  game and any wait in Forge's queue come on top, and each game gets at least 30 seconds, so with
  many games and few places it takes longer.
  A game stopped before turn 10 or a win is left out of the numbers, and the report says how
  many. One per member runs at a time (two for reports that share opponents), and none starts
  while other members' games against precons wait their turn in the gateway's queue.
- **The image tag:** the stack files name the Forge image with its tag (`2.0.15-mag6`).
  GitHub builds and publishes a new tag the first time `main` carries it (the Forge image
  workflow, after its test games pass on arm64 and amd64), so pulling a new tag fails until that
  run has finished. After an update whose stack file names a new tag, wait for the run, then
  update the stack.
- **No login between the gateway and Forge.** Forge's job service takes jobs from anything that
  can reach it. The stack files keep it on a private network (`forge`) that only the gateway joins,
  with no published port and no internet access, so nothing else can. Keep it that way: don't
  publish its port, don't proxy it, and don't attach other containers to that network.
- **Load:** Java starts only while Forge plays, limited by `FORGE_MEMORY_LIMIT` (default 1536M) and
  `FORGE_CPUS` (default 2); where Docker can't cap memory, see the memory note below. **Forge games
  per report** on the Settings page (default 5) sets the games per report; **Play more games** on a finished report plays that many again against the same
  opponents and adds them to the results (50 games a report at most; on a comparison, the same
  number to both decks). An idle Forge service holds
  no Java memory.
- **Games at once:** each game of a run is its own Java process with its own seed, so up to 16 of a
  run's games can play at once when Forge has room (up to 4 with a Forge image before `2.0.15-mag4`). The seed is kept with the report for the record:
  Forge does not always replay a game exactly, so compare runs by their win rates and 95% intervals,
  not game by game. **Forge games played at once** on the Settings page (default 4) is how many one
  report asks for at once, and Forge allows at most what its container fits, for all runs
  together: runs start in order, and a run that uses fewer lets the next one play beside it (with
  2 or more, a Compare's second deck plays beside the first when a place is still free for it).
  Forge works that number out from its container: one game per 2 CPUs and per 1.2 GB of memory,
  1 to 16, which is 1 inside the default limits; its log and `/health` (`max_parallel`,
  `max_parallel_from`) say which. Each game takes up to 1 GB of Java memory. Measured on GitHub's build machines (not a Pi) with the default limits (1536M, 2
  CPUs, 15 four-player games, one game per Java process): one at a time peaked at about 600 MiB;
  two at once at about 970 to 1,050 MiB, and finished about 25% sooner on the arm64 machine but
  about 14% later on the x86 machine; three at once fit in the memory (about 1,210 to 1,380 MiB) but
  slowed the games so much that 3 and 7 of the 15 reached the four-minute limit. To play more at
  once, raise `FORGE_MEMORY_LIMIT` and `FORGE_CPUS`. One game may run four minutes before Forge
  calls it a draw; such games are counted as draws in the report. Forge reads a card's rules only when a
  game needs that card, which roughly halves each Java start (measured 7 s to 3 s on a 4-CPU PC).
  This is fixed in the Forge image.
- **Memory where Docker can't cap it:** on Raspberry Pi OS the kernel's memory controller is off by
  default, so Docker drops the stack's `FORGE_MEMORY_LIMIT` and nothing caps Forge's memory. Forge
  then guards its own memory from inside, with nothing to change on the machine: the stack also
  passes `FORGE_MEMORY_LIMIT` as `FORGE_MEMORY_BUDGET`, which sizes the games at once when no limit
  can be read; each Forge game gets a cap of 256 MB on its extra Java memory for loaded code, on
  top of its 1 GB; and each Forge game is marked as the first the kernel stops, so if the machine
  runs out of memory while Forge plays, the kernel stops a Forge game before the gateway or other services. These keep Forge in bounds, but
  they are not a hard limit as the kernel's memory controller is.
- **More than one machine** (optional; one copy is the default): the gateway can use several
  copies of the Forge service, for example a Pi and an x86 PC, each playing whole runs.
  The gateway's address for Forge stays the same. The stack's Forge service uses `endpoint_mode: dnsrr`, so the
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
- **Memory:** ntfy is limited to 128 MB, a fixed line in the stack files.
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

Every variable below is in `deploy/stack.env.example`, in the same order
(the Compose `.env.example` has the same ones except the two node names, plus its own address
and Traefik lines, see [DEPLOY-COMPOSE.md](DEPLOY-COMPOSE.md)). Everything else the gateway lets you tune
is on its Settings page (`/admin/settings`, see
[OPERATIONS.md](OPERATIONS.md#the-settings-page)), where an admin changes it
without touching the stack.

**Address and sign-in**

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_PUBLIC_URL` | none, required | Public https URL, no path. The `Host` headers accepted on `/mcp` are worked out from it |
| `MTG_OIDC_ISSUER` | none, required | Identity provider issuer URL. With Authentik, the removed-member clean-up reaches Authentik's API at this URL's scheme and host |
| `MTG_OIDC_CLIENT_ID` | none, required | OIDC client ID |
| `MTG_REQUIRED_GROUP` | none, required | Group a user must be in. The gateway refuses to start with it empty unless `MTG_ALLOW_ANY_IDP_USER` is `true` |
| `MTG_ADMIN_GROUP` | empty (no admin pages) | Identity-provider group whose members get the `/admin` pages and `/api/v1/admin`. Checked live like `MTG_REQUIRED_GROUP`; its members may sign in even when they aren't in `MTG_REQUIRED_GROUP`. Empty, the admin routes answer 404 for everyone. Details in [OPERATIONS.md](OPERATIONS.md#the-admin-page) |

**Where it runs**

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_NODE` | none, required (Swarm) | Hostname of the node the gateway runs on (`docker node ls`) |
| `FORGE_NODE` | none, required (Swarm) | Hostname of the node Forge runs on. Required even with Forge off. For Forge copies on several machines, place them by a node label instead (see "More than one machine" in the Forge section above) |
| `MTG_DATA_DIR` | none, required | Host folder for the SQLite database, mounted at `/data` |
| `MTG_BACKUP_DIR` | none, required | Host folder for nightly backups and the copies saved before database upgrades (its `before-upgrade` folder), mounted at `/backups` |
| `PUID`, `PGID` | none, required | User and group the process runs as; they must own both folders |
| `MTG_NETWORK_NAME` | `mtg-gateway` | The network your reverse proxy is on (Swarm: the overlay network from step 1). Must exist before you deploy and match its name exactly |

**Optional**

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_TAG` | `latest` | Gateway image tag. A version such as `0.7.45` stays on it |
| `TIMEZONE` | `UTC` | Passed to the containers as `TZ` |
| `MTG_SERVER_NAME` | empty (the Settings page's **Gateway name**, `MTG Assistant Gateway` until changed there) | Commented out in the example files. The name in the top bar, on sign-in and in the assistants' connector lists, up to 100 characters. A name here locks the Settings page's **Gateway name** row, which then shows it is set in your stack |
| `MTG_BACKUP_COPY_DIR` | empty (no second copy) | A host folder (another disk, or shared storage) every backup is also copied to and pruned the same way. It must already exist. A failed copy is logged and shown on the admin page. See [OPERATIONS.md](OPERATIONS.md#backups) |
| `MTG_MEMORY_LIMIT` | `256M` | The gateway's memory cap |

**Forge simulations**

| Variable | Default | What it does |
| --- | --- | --- |
| `FORGE_REPLICAS` | `1` | Copies of the Forge service. `0` turns Forge off; the gateway follows it (deck reports then have no Forge games) |
| `FORGE_MEMORY_LIMIT`, `FORGE_CPUS` | `1536M`, `2` | The Forge container's memory and CPU caps. Forge works out from them how many games it plays at once (one per 2 CPUs and 1.2 GB); `FORGE_MEMORY_LIMIT` is also passed to Forge as `FORGE_MEMORY_BUDGET` for machines where Docker can't cap memory (see the Forge section above) |

**Features (all on)**

Each switch is on when its line is empty, and the Settings page can change it.
`false` (`0` for the deck check) turns the feature off. Any value locks its
Settings page row, which then shows it is set in your stack.

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_WRITES_ENABLED` | `true` | Deck edits reach Archidekt. `false` keeps proposals but applies nothing. Not on the Settings page |
| `MTG_WATCHLISTS_ENABLED` | on | Watchlists and prices: the Watchlist page and its links, the card viewer's price buttons, the watch tools and the daily price check. Off keeps members' stored watches and prices |
| `MTG_ARCHIDEKT_BACKUPS` | on | Before an applied edit or restore, a private backup copy of the deck in the member's backup folder on Archidekt. If the copy fails, nothing changes and the proposal stays waiting |
| `MTG_APPLY_IN_CHAT` | on | The Approve and Reject card next to a proposal in apps that show MCP Apps, with the waiting-changes card and the two pickers. Off leaves the review page |
| `MTG_SCAN_ART_ENABLED` | on | The card scanner's artwork check, which picks the printing |
| `MTG_UPDATE_CHECK` | on | Twice a day, one anonymous request to GitHub for this project's newest release; admins see a notice when it is newer. The gateway never pulls an image itself |
| `MTG_DECK_SYNC_MINUTES` | `30` | Minutes between background checks of Archidekt for members with changes waiting, 30 to 1440; `0` turns it off |

**Security** (leave empty for the defaults)

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_TRUSTED_PROXIES` | private networks (`10/8`, `172.16/12`, `192.168/16`, loopback) | Comma-separated IPs or CIDR ranges whose `X-Forwarded-*` headers are trusted. Recommended: narrow it to your proxy's address or the overlay subnet (see [step 7](#7-reverse-proxy)) |
| `MTG_CIMD_ENABLED` | `true` | Accept clients that identify by a published metadata address (Claude's "Use Claude's published identity" and, reportedly, ChatGPT). `false` leaves only automatic registration |
| `MTG_CIMD_ALLOWED_HOSTS` | empty (any https host) | Comma-separated hostnames whose metadata documents are accepted, subdomains included. **Leave it empty** unless you know every client's host: listing only Claude's can lock ChatGPT out. Details in [OPERATIONS.md](OPERATIONS.md#which-ai-clients-may-connect) |
| `MTG_ACCESS_TOKEN_TTL` | empty (the Settings page: 1 hour) | Assistant access token lifetime, seconds (60 to 86400). Usually set on the Settings page instead (**Sign-in → Assistant access**); a value here locks that row |
| `MTG_REFRESH_TOKEN_TTL` | empty (the Settings page: 30 days) | Assistant refresh token lifetime, seconds (3600 to 31536000). Usually set on the Settings page instead (**Sign-in → Assistant refresh**); a value here locks that row |
| `MTG_BROWSER_SESSION_TTL` | empty (the Settings page: 7 days) | How long a sign-in in a browser or the Android app lasts while it is not used, seconds (300 to 2592000). Using the gateway renews it, at most once per half of this time. Whatever the use, a sign-in ends after the Settings page's **Longest website sign-in** (30 days by default; it has no stack variable). Usually set on the Settings page instead (**Sign-in → Website sign-in when unused**); a value here locks that row. Before 0.7.46 this was the whole lifetime (2 hours by default): a stack file that still sets `7200` keeps sign-ins to 2 hours unused |
| `MTG_REAUTH_INTERVAL` | `604800` | How long after signing in an assistant can keep refreshing its tokens before the person signs in again, seconds (3600 to 31536000). Group membership doesn't wait for this: it is checked live |
| `MTG_MEMBERSHIP_CHECK_TTL` | `5` | Seconds a group-membership answer from the identity provider is reused. After that, for up to 60 seconds after the provider last let the person in, a request is answered at once and the provider is asked in the background, so a removed member can keep going for at most 60 seconds. `0` makes every request wait for the provider's answer; 0 to 60. If the provider can't be reached, requests get 503 and nothing is revoked |
| `MTG_ALLOW_ANY_IDP_USER` | `false` | `true` lets an empty `MTG_REQUIRED_GROUP` through, so anyone your identity provider signs in gets in. Only for an identity provider that already admits nobody else |

**Other identity providers** ([IDP-OTHERS.md](IDP-OTHERS.md))

| Variable | Default | What it does |
| --- | --- | --- |
| `MTG_OIDC_GROUPS_CLAIM` | `groups` | Dot-path to the group list in the ID token (or userinfo) claims. Keycloak: `realm_access.roles`; Zitadel: `urn:zitadel:iam:org:project:roles`. The whole value is tried as one claim name first, so `https://example.com/groups` works too. A missing claim means no groups, so `MTG_REQUIRED_GROUP` refuses the sign-in |
| `MTG_OIDC_SCOPES` | `openid profile email offline_access` | Scopes requested, used exactly as set. Keep `offline_access`: the provider's refresh token is what lets the gateway keep checking membership. Add `groups` for a provider that needs it (Authelia, Pocket ID) |
| `MTG_OIDC_TOKEN_AUTH_METHOD` | `client_secret_post` | How the client secret is sent to the token endpoint: `client_secret_post` or `client_secret_basic`. Match the client's setting in the identity provider |
| `MTG_OIDC_PREVIOUS_ISSUERS` | empty | Only after moving the identity provider: the old issuer URL(s), comma separated. Members whose account was made under an old address move to the new one at their next sign-in; any other issuer change is refused |

**Fixed lines.** The stack file and the Compose file also set the secrets'
paths (`MTG_*_FILE`, `/run/secrets/...`), the addresses of the stack's own
services (`MTG_MYSTIC_FORGE_URL`, `MTG_FORGE_URL`, `MTG_NTFY_URL`) and
`MTG_FORGE_REPLICAS` (from `FORGE_REPLICAS`). They are not settings: leave
them as they are.

The card scanning settings are described in
[SCANNING.md](SCANNING.md#operator-notes); they are on the Settings page.
