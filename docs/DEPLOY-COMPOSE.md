# Deploying with Docker Compose (one machine)

This is the simplest way to run the gateway: one machine with Docker, two
containers, a reverse proxy for HTTPS, and an OpenID Connect identity
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
  fine. About 800 MB of memory free for the two containers at their limits.
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

The gateway reads three secrets from files, never from environment
variables:

| File in `secrets/` | What's in it |
| --- | --- |
| `mtg_fernet_key.txt` | Encryption key for linked Archidekt sessions and the identity provider's tokens the gateway keeps. **Keep a copy**: lose it and everyone has to relink Archidekt (and sign in once more) |
| `mtg_session_secret.txt` | Random key that signs browser cookies. No need to keep a copy |
| `mtg_oidc_client_secret.txt` | The client secret from step 1 |

Run the helper as the user in `PUID` (see step 3), or with `sudo`, in which
case it hands the files to `PUID:PGID` from `.env`:

```bash
./make-secrets.sh
```

It generates the two random values, prints the Fernet key once so you can
save it in your password manager, and asks for the client secret at a
hidden prompt. It never overwrites a file that's already there. Clear your
terminal afterwards.

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
| `MTG_TAG` | `latest` to follow every new version, or one version to stay on it, for example `0.6.3` ([VERSIONS.md](VERSIONS.md)) |

Worth a look:

| Variable | In `.env.example` | Notes |
| --- | --- | --- |
| `PUID`, `PGID` | `1000` | Who owns the data folders and secret files. `id -u` and `id -g` show yours |
| `MTG_HTTP_BIND`, `MTG_HTTP_PORT` | `127.0.0.1`, `8080` | Where the gateway listens on this machine, for a proxy on the same machine. A proxy container on the gateway's network doesn't need it (the proxy overrides remove it) |
| `MTG_DATA_DIR`, `MTG_BACKUP_DIR` | `./data`, `./backups` | Local disk only, not NFS or SMB |
| `MTG_WRITES_ENABLED` | `true` | `false` keeps everything review-only: proposals work, nothing is ever applied to Archidekt. If the line is missing, the gateway's own default is `false` |
| `MTG_APPLY_VIA_MCP` | `false` | Same as the code default: only the Apply button on the review page applies a change. `true` lets the assistant apply after the person says yes in chat, which is weaker: text the assistant reads (deck descriptions, card notes) could trick it into applying its own proposal once `MTG_APPLY_MIN_AGE_SECONDS` has passed |

Every other variable is explained in `.env` and in the
[environment reference](DEPLOY.md#environment-reference). A setting only
reaches the gateway if `docker-compose.yml` lists it under the gateway's
`environment:`; for one that isn't there yet, add a line there as well as in
`.env`.

Create the folders and give them to `PUID:PGID`:

```bash
mkdir -p data backups
sudo chown -R 1000:1000 data backups   # your PUID:PGID
chmod 700 data backups
```

## 4. Images

The images are on GitHub Container Registry:

- `ghcr.io/theuncleben/mtg-assistant-gateway`
- `ghcr.io/theuncleben/mtg-assistant-mysticforge`

If the packages are private, log in once with a GitHub token that has only
the `read:packages` scope: `docker login ghcr.io`.

Prefer to build them yourself? From a clone of the repository:

```bash
docker build -t mtg-gateway:local .
docker build -f docker/mystic-forge/Dockerfile -t mtg-mysticforge:local .
```

and set `MTG_IMAGE=mtg-gateway`, `MTG_TAG=local`, `MF_IMAGE=mtg-mysticforge`,
`MF_TAG=local` in `.env`.

### Hardening already in the Compose file

- Every Linux capability is dropped except the four the start-up script
  needs to switch to `PUID:PGID`, and `no-new-privileges` is on.
- Each service may run at most 512 processes and threads
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
networking): start the two containers, then point the proxy at
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
docker compose ps        # both services "healthy" after about 30 seconds
docker compose logs -f gateway
```

## 6. Check it works

```bash
curl -s https://mtg.example.com/healthz
# {"status":"ok","version":"0.6.3"}

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

```bash
# take a backup first (your PUID:PGID):
docker compose exec --user 1000:1000 gateway mtg-gateway backup
# change MTG_TAG in .env to the new release, then:
docker compose pull
docker compose up -d
```

Read the [CHANGELOG](../CHANGELOG.md) first; it says when a release needs a
settings change. The gateway upgrades its database on start and records the
schema version in the file. To go back to an older release, restore the backup
you took before updating (see [OPERATIONS.md](OPERATIONS.md#backups)) and run
the old image on that; don't run an old image on an upgraded database.

## Backups

- The gateway copies its database into `backups/` every night at
  `MTG_BACKUP_HOUR_UTC` (UTC) and keeps `MTG_BACKUP_KEEP_DAYS` days. Copy
  that folder off the machine with whatever you use for the rest.
- For a copy right now (use your `PUID:PGID`):
  `docker compose exec --user 1000:1000 gateway mtg-gateway backup`.
- Keep the Fernet key (`secrets/mtg_fernet_key.txt`) somewhere safe as well.
  It isn't in the backups, on purpose.
- Don't copy `data/` while the gateway runs; use the backup files.

Restoring is in [OPERATIONS.md](OPERATIONS.md#backups): stop, swap the
database file for a backup, delete the `-wal` and `-shm` files next to it,
start.

## Moving to Swarm later

The Swarm stack ([DEPLOY.md](DEPLOY.md)) uses the same images and variables.
Create the three Swarm secrets from your three files
(`docker secret create mtg_fernet_key secrets/mtg_fernet_key.txt`, and so on),
copy `data/` to the node in `MTG_NODE`, and deploy the stack.
