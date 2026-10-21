# Deploying with Docker Compose (one machine)

This is the simplest way to run the gateway: one machine with Docker, three
containers (four with Forge), a reverse proxy for HTTPS, and an OpenID Connect identity
provider for sign-in. No Swarm, no Portainer needed. (Portainer's **Stacks**
page on a standalone Docker environment can run this Compose file too, but
then set `MTG_DATA_DIR`, `MTG_BACKUP_DIR` and `MTG_SECRETS_DIR` to absolute
paths on the host: relative ones resolve inside Portainer's own folder.)

Running a Swarm? Use [DEPLOY.md](DEPLOY.md) instead. Both run the same
images with the same settings; only the plumbing differs.

How the pieces fit is in [ARCHITECTURE.md](ARCHITECTURE.md).

## What you need

- A machine with **Docker Engine** and the **Compose plugin** 2.24 or newer
  (`docker compose version`). amd64 or arm64, so a Raspberry Pi 4 or 5 is
  fine. About 900 MB of memory free for the three containers at their limits
  (another 1.5 GB if you turn Forge on).
- A **domain name** for the gateway, say `mtg.example.com`, pointing at a
  reverse proxy that can reach this machine.
- An **OpenID Connect identity provider** you control: Authentik
  ([guide](IDP-AUTHENTIK.md)) or another one ([notes](IDP-OTHERS.md)).
- `openssl` on the machine, for generating secrets.

Everything below runs from a copy of this repository's
[`deploy/compose/`](../deploy/compose/) folder. You don't need the rest of
the repository on the server:

```bash
mkdir -p ~/mtg-gateway && cd ~/mtg-gateway
base=https://raw.githubusercontent.com/TheUncleBen/MTG-Assistant-Gateway/main/deploy/compose
curl -fsSLO "$base/docker-compose.yml"
curl -fsSL -o .env "$base/.env.example"
curl -fsSLO "$base/make-secrets.sh" && chmod +x make-secrets.sh
# Only if you'll use the bundled Caddy for HTTPS (step 5):
proxy=https://raw.githubusercontent.com/TheUncleBen/MTG-Assistant-Gateway/main/deploy/proxy/caddy
curl -fsSLO "$proxy/compose.caddy.yml" && curl -fsSLO "$proxy/Caddyfile"
```

(Or clone the repository and work in `deploy/compose/`.)

## 1. Identity provider

Create one confidential OIDC client for the gateway with the redirect URI
`https://mtg.example.com/auth/callback`, and a group for the people who may
use it.

- **Authentik:** sections 3 to 6 of [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md).
- **Anything else:** the checklist in [IDP-OTHERS.md](IDP-OTHERS.md).

Note three things: the **issuer URL**, the **client ID** and the **client
secret**. The secret only goes into a file in the next step.

## 2. Secrets

The gateway reads five secrets from files, never from environment
variables:

| File in `secrets/` | What's in it |
| --- | --- |
| `mtg_fernet_key.txt` | Encryption key for linked Archidekt sessions and the identity provider's tokens the gateway keeps. **Keep a copy**: lose it and everyone has to relink Archidekt (and sign in once more) |
| `mtg_session_secret.txt` | Random key that signs browser cookies. No need to keep a copy |
| `mtg_oidc_client_secret.txt` | The client secret from step 1 |
| `mtg_ntfy_token.txt` | Random token the gateway uses with the stack's own notification server. You don't need to keep a copy. |
| `mtg_authentik_api_token.txt` | The word `off`, or the Authentik API token for the optional clean-up of removed members' stored Archidekt sessions ([IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#12-optional-removed-member-clean-up)) |

Run the helper as the user in `PUID` (see step 3), or with `sudo`, in which
case it hands the files to `PUID:PGID` from `.env`:

```bash
./make-secrets.sh
```

It generates the three random values, prints the Fernet key once so you can
save it in your password manager, asks for the client secret at a
hidden prompt, and writes `off` into `mtg_authentik_api_token.txt`. It never overwrites a file
that's already there, so after an update, run it again: it adds only a file that is missing.
Clear your terminal afterwards.

The files are mode `600`. Compose mounts them into the containers as they
are, so the user in `PUID` must be able to read them. If the gateway's log
says `Permission denied: '/run/secrets/...'`, run
`sudo chown <PUID>:<PGID> secrets/*.txt`.

## 3. Settings

Edit `.env`. The lines marked **REQUIRED**:

| Variable | What to put |
| --- | --- |
| `MTG_PUBLIC_URL` | `https://mtg.example.com`, no path, no trailing slash |
| `MTG_OIDC_ISSUER` | The issuer URL from step 1 |
| `MTG_OIDC_CLIENT_ID` | The client ID from step 1 |
| `MTG_REQUIRED_GROUP` | The group from step 1, exactly as your provider sends it. Required unless you set `MTG_ALLOW_ANY_IDP_USER=true` (see [IDP-OTHERS.md](IDP-OTHERS.md#about-the-group-check)) |
| `MTG_TAG` | `latest` to follow every new version, or one version to stay on it, written `X.Y.Z` (the versions are in [VERSIONS.md](VERSIONS.md)) |

Worth a look:

| Variable | In `.env.example` | Notes |
| --- | --- | --- |
| `PUID`, `PGID` | `1000` | Who owns the data folders and secret files. `id -u` and `id -g` show yours |
| `MTG_HTTP_BIND`, `MTG_HTTP_PORT` | `127.0.0.1`, `8080` | Where the gateway listens on this machine, for a proxy on the same machine. A proxy container on the gateway's network doesn't need it (the proxy overrides remove it) |
| `MTG_DATA_DIR`, `MTG_BACKUP_DIR` | `./data`, `./backups` | Local disk only, not NFS or SMB |
| `MTG_WRITES_ENABLED` | `true` | `false` keeps everything review-only: proposals work, nothing is ever applied to Archidekt. If the line is missing, the gateway's own default is `false` |
| `MTG_APPROVAL_MODE_DEFAULT` | `manual` | Same as the code default: a person who has not chosen an approval mode has every change wait for their own press, on the card in the chat or the Apply button on the review page. Each person picks their own mode (`manual`, `semi`: low-risk changes, such as a small edit or a private copy of a deck, apply without asking, `auto`: most changes apply without asking, but some still wait for the member) on their Account page |
| `MTG_APPROVAL_MODE_MAX` | `auto` | No cap on what people may choose; `semi` or `manual` caps it. `MTG_AUTO_APPLY_MAX_ROWS` (`5`) is the row limit of a low-risk edit |
| `MTG_APPLY_IN_CHAT` | `true` | The Approve/Reject card Claude and ChatGPT show next to a proposal (only its one-time code can apply) and the other cards in the chat: the waiting changes and the two pickers (printings, recognised cards). `false` removes them all, leaving the review page |
| `MTG_UPDATE_CHECK` | `true` | Admins see a notice when a newer release is out, with the update steps on the admin overview. The gateway never updates itself. `false` turns the check off |

Every other variable is explained in `.env` and in the
[environment reference](DEPLOY.md#environment-reference). `.env.example`
lists every variable `docker-compose.yml` and the proxy overrides read, and
says for each one whether it is required or optional and whether it is on the
Settings page when `.env` doesn't set it.

Lines in `.env` that start `# MTG_` (the approval modes, `MTG_APPLY_IN_CHAT`,
`MTG_UPDATE_CHECK` and the other tuning settings) are left to the gateway's
admin Settings page (`/admin/settings`, since 0.7.29), starting from the
default shown. Remove the `# ` to lock one in `.env` instead; the page then
shows it greyed out with its value, and you change it in `.env`
([OPERATIONS.md](OPERATIONS.md#the-settings-page)).

Create the folders and give them to `PUID:PGID`:

```bash
mkdir -p data backups
sudo chown -R 1000:1000 data backups   # your PUID:PGID
chmod 700 data backups
```

## 4. Images

The images are on GitHub Container Registry (GHCR):

- `ghcr.io/theuncleben/mtg-assistant-gateway`
- `ghcr.io/theuncleben/mtg-assistant-mysticforge`
- `ghcr.io/theuncleben/mtg-assistant-forge` (only pulled if you turn Forge on)

They're public: `docker compose pull` needs no `docker login` and no GitHub
token. The ntfy server is the official `binwiederhier/ntfy` image from Docker
Hub, pinned in the Compose file.

Prefer to build them yourself? From a clone of the repository:

```bash
docker build -t mtg-gateway:local .
docker build -f docker/mystic-forge/Dockerfile -t mtg-mysticforge:local .
```

and set `MTG_IMAGE=mtg-gateway`, `MTG_TAG=local`, `MF_IMAGE=mtg-mysticforge`,
`MF_TAG=local` in `.env`.

### Hardening already in the Compose file

- Every Linux capability is dropped except the four the start-up script
  needs to switch to `PUID:PGID`, and `no-new-privileges` is on. ntfy runs
  as `PUID:PGID` from the start, with no capabilities at all.
- Each service may run at most 512 processes and threads (ntfy 128)
  (`deploy.resources.limits.pids`), so a runaway process can't exhaust the
  machine. The gateway normally needs a few dozen.
- The root filesystem stays writable: when `PUID`/`PGID` differ from
  1000:1000 the start-up script edits `/etc/passwd` and `/etc/group`.
- Mystic Forge has no login of its own and publishes no port. It sits on
  the `mysticforge` network with the gateway only. That network can't be
  made `internal`, because Mystic Forge needs the internet (Scryfall,
  EDHREC, the rules). On a plain Docker host, programs running on this
  machine can still reach a container on a bridge network by its IP, so
  only run things you trust on this machine, and don't attach other
  containers to that network.

## 5. Start it

Pick how HTTPS reaches the gateway ([REVERSE-PROXY.md](REVERSE-PROXY.md) has
the details for each):

**A proxy already on this machine** (nginx, Caddy, NPM with host
networking): start the containers, then point the proxy at
`http://127.0.0.1:8080`.

```bash
docker compose up -d
```

**No proxy yet:** add Caddy, which fetches a certificate by itself. Put your
hostname in the Caddyfile first, then:

```bash
# From a clone, in deploy/compose/:
docker compose -f docker-compose.yml -f ../proxy/caddy/compose.caddy.yml up -d

# With the downloaded files, all in one folder:
echo 'CADDYFILE=./Caddyfile' >> .env
docker compose -f docker-compose.yml -f compose.caddy.yml up -d
```

**Traefik already running:** see
[REVERSE-PROXY.md](REVERSE-PROXY.md#traefik).

Then:

```bash
docker compose ps        # every service "healthy" after about 30 seconds
docker compose logs -f gateway
```

## 6. Check it works

```bash
curl -s https://mtg.example.com/healthz
# {"status":"ok"}
# ("degraded": the gateway works, the research service or Forge (when
#  it's on) doesn't answer; see docker compose logs mysticforge and forge)

curl -s https://mtg.example.com/.well-known/oauth-authorization-server | head -c 300
# JSON with "issuer":"https://mtg.example.com", ...

curl -s -o /dev/null -w '%{http_code}\n' https://mtg.example.com/mcp
# 401  (no token, which is what you want)
```

Open `https://mtg.example.com/` in a browser. It sends you to your identity
provider; once you're signed in as a member of the group you land on the
gateway's front page. That proves sign-in works end to end.

Next:

- connect your assistant: [CONNECT.md](CONNECT.md);
- invite people: [ONBOARDING.md](ONBOARDING.md#for-the-owner);
- day-to-day running, backups and updates: [OPERATIONS.md](OPERATIONS.md).

## Updating

An update is this guide again with the newest files: [UPGRADING.md](UPGRADING.md) has the steps.
There is no backup to take first: when a new version upgrades the database, the gateway saves a
copy of it in `backups/before-upgrade/` first, and going back to an older version is in
[UPGRADING.md](UPGRADING.md#going-back-to-an-older-version).

## Backups

- The gateway copies its database into `backups/` every night at
  `MTG_BACKUP_HOUR_UTC` (UTC) and keeps `MTG_BACKUP_KEEP_DAYS` days. Copy
  that folder off the machine with whatever you use for the rest.
- For a copy right now (use your `PUID:PGID`):
  `docker compose exec --user 1000:1000 gateway mtg-gateway backup`.
- For a second copy of every backup on another disk or a network mount, set
  `MTG_BACKUP_COPY_DIR` in `.env` to that folder on this machine (create it
  first). The compose file mounts it into the gateway; left out, nothing is
  copied. Copies are pruned after the same number of days.
- Backups leave out everyone's Archidekt session and the identity
  provider's tokens, so after a restore people sign in again and relink
  Archidekt.
- Before a new version upgrades the database, the gateway saves a full copy of it (Archidekt
  sessions included, still encrypted with the Fernet key) in `backups/before-upgrade/`, and keeps
  the newest 3. It is what going back to an older version uses
  ([UPGRADING.md](UPGRADING.md#going-back-to-an-older-version)).
- Keep the Fernet key (`secrets/mtg_fernet_key.txt`) somewhere safe as well.
  It isn't in the backups, on purpose.
- Don't copy `data/` while the gateway runs; use the backup files.

Restoring is in [OPERATIONS.md](OPERATIONS.md#backups): stop, swap the
database file for a backup, delete the `-wal` and `-shm` files next to it,
start.

## Moving to Swarm later

The Swarm stack ([DEPLOY.md](DEPLOY.md)) uses the same images and variables.
Create the five Swarm secrets from your five files
(`docker secret create mtg_fernet_key secrets/mtg_fernet_key.txt`, and so on),
copy `data/` to the node in `MTG_NODE`, and deploy the stack.
