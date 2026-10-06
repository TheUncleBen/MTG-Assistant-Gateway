# Changelog

Notable changes for people who run or use the gateway. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) as described in
[docs/VERSIONS.md](docs/VERSIONS.md): every update to `main` is a new version
with its own image (`1.2.3`), git tag (`v1.2.3`) and read-only branch
(`release/1.2.3`); `latest` is always the newest.

## [0.5.0] - 2026-10-06

The first release from this repository, as **MTG Assistant Gateway**. It has the
features of 0.2.0 below, under new names, with a new release process. Versions
below 1.0.0 are still in testing: expect fixes and small changes between them.
1.0.0 will be the first version declared ready.

### Upgrading from 0.2.0

1. **New names.** The images are now `ghcr.io/<owner>/mtg-assistant-gateway`
   and `ghcr.io/<owner>/mtg-assistant-mysticforge` (tag `1.3.2-mag2`). In the
   Swarm stack the services are `mtg-assistant-gateway` and
   `mtg-assistant-mysticforge`, so your reverse proxy forwards to
   `<stack>_mtg-assistant-gateway:8080`. Paste the new
   `deploy/portainer-stack.yml`, set `MTG_IMAGE` and `MF_IMAGE` (or leave
   them unset for the defaults), and change the proxy's forward host.
   Your data folder, secrets, network and identity provider settings stay
   the same, and the database needs no upgrade.
2. **The Android app** is now "MTG Assistant Gateway" with the package ID
   `local.mtgassistantgateway.app`, so it installs as a new app: uninstall
   the old one first. On the gateway it is served at
   `/app/mtg-assistant-gateway.apk`.

### Changed

- Renamed to MTG Assistant Gateway everywhere: pages,
  assistants, images, Swarm services, the Android app and the docs.
- Every update to `main` is released: CI checks that `VERSION` went up,
  creates the tag `v<version>`, publishes the image as the version,
  `<major>.<minor>` and `latest`, and creates the read-only branch `release/<version>`
  and a GitHub release ([docs/VERSIONS.md](docs/VERSIONS.md)). The example
  settings now follow `latest`; pin a version to stay on it.
- The Mystic Forge image is built and published automatically the first
  time `main` names a tag that isn't published yet.
- Android app: the scan panel uses CameraX, lays itself out around the
  hinge on a half-folded phone (tabletop and book postures, Jetpack
  WindowManager), opens gateway links shared to it, and has an App Links
  flavor for operators who build it for one gateway (`APP_LINKS_HOST`,
  `ANDROID_APP_LINKS_HOST`, `MTG_ANDROID_ASSETLINKS`). It takes its version
  from `VERSION` like everything else and is built with Gradle and the Android
  SDK (`android/scripts/build.sh` fetches the SDK; Android Studio opens the
  project directly). The release APK is shrunk to about 2 MB.

## 0.2.0 - 2026-10-05

Released before this repository existed, under an earlier name; there is no
`v0.2.0` tag here.

### Upgrading from 0.1.0

Read this before you deploy 0.2.0:

1. **`MTG_REQUIRED_GROUP` is required.** With it empty the gateway stops at
   start-up with `configuration error: MTG_REQUIRED_GROUP is empty`. Set it to
   your identity provider's group, or set `MTG_ALLOW_ANY_IDP_USER=true` if your
   provider already admits nobody else.
2. **Take a backup first.** The database is upgraded in place on the first
   start (schema version 5). Going back to 0.1.0 means restoring that backup,
   not running the old image on the upgraded file: 0.1.0 doesn't know about
   accounts disabled on the admin page and would let them in again.
3. **Everyone is signed out of the web pages once.** The cookies are now
   `__Host-` cookies. Sign-ins that are half-way through at the moment of the
   upgrade fail and have to be started again. Connected assistants keep working
   as long as the groups recorded at each person's last sign-in contain
   `MTG_REQUIRED_GROUP`; otherwise they are asked to sign in again.
4. **Swarm:** the stack's default network name is now `mtg-gateway`. If you
   deployed with the old default, set `MTG_NETWORK_NAME` to your network's exact
   name. Mystic Forge now sits on a private network of its own.
5. New optional settings worth a look: `MTG_ADMIN_GROUP` (admin page),
   `MTG_OIDC_GROUPS_CLAIM` and `MTG_OIDC_TOKEN_AUTH_METHOD` (identity providers
   other than Authentik), `MTG_APP_DIR` and `MTG_ANDROID_ASSETLINKS` (Android
   app). The Compose and Swarm files pass the first three through.

### Added

- Deck statistics with a Commander bracket estimate, stored deck reports with
  history, and deck comparison.
- Companion pages for phones and desktops: your decks (`/decks`), a deck view
  and editor that turns edits into a proposal you apply, history (`/history`)
  and activity (`/activity`).
- An admin page for members of `MTG_ADMIN_GROUP`: users and their activity,
  usage metrics, and disable, enable, revoke and unlink actions
  ([OPERATIONS.md](docs/OPERATIONS.md#the-admin-page)).
- A JSON API under `/api/v1` ([docs/API.md](docs/API.md)).
- More kinds of proposals: deck details, set a card's category, set the
  commander, change a card's finish or printing, clone a deck
  (`propose_clone_deck`); proposals can be rejected; a saved scan session can
  be the input.
- The pages now look and work like Archidekt's: the same top bar, bottom tab
  bar on phones, deck banner with art, toolbar with text / stacks / grid
  views, group and sort choices and a local filter, deck stats panel, deck
  grid with cover art and colour bar, Light / Dark / System theme in the
  account menu. New pages: New deck, Deck settings, Clone deck, CSV export;
  the editor gained numeric quantities, a printing picker, finishes, new
  categories by typing and Undo. The Android app follows the phone's Light /
  Dark setting with the same palette.
- Linked Archidekt sessions refresh themselves, so people relink far less often.
  A refresh never undoes an unlink.
- Sign-in reads groups from a configurable claim (`MTG_OIDC_GROUPS_CLAIM`, for
  example Keycloak's `realm_access.roles` or Zitadel's roles object) and can send
  the client secret with HTTP Basic (`MTG_OIDC_TOKEN_AUTH_METHOD`).
- The MTG Assistant Gateway Android app, version 0.2.0 like the gateway ([docs/ANDROID.md](docs/ANDROID.md), [android/](android/)):
  the gateway's pages full screen, pointed at any gateway at first launch, plus a
  phone-camera scan screen with torch brightness, zoom, exposure and a continuous
  Auto mode that hands each photo to the `/scan` page. Signed-in users download it
  from their own gateway at `/app` (new optional `MTG_APP_DIR`), and tagged
  releases attach it to the GitHub release when the `ANDROID_*` repository
  secrets are set. No app store involved.
- Plain Docker Compose deployment for a single machine
  ([docs/DEPLOY-COMPOSE.md](docs/DEPLOY-COMPOSE.md),
  [deploy/compose/](deploy/compose/)), alongside the Swarm and Portainer stack.
- Reverse proxy examples for Caddy, Traefik, nginx and Nginx Proxy Manager
  ([docs/REVERSE-PROXY.md](docs/REVERSE-PROXY.md)).
- Notes for identity providers other than Authentik
  ([docs/IDP-OTHERS.md](docs/IDP-OTHERS.md)).
- An architecture overview with a diagram ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))
  and a troubleshooting guide ([docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)).
- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue and pull
  request templates, and this changelog.

### Changed

- The Swarm stack's default network name is now `mtg-gateway`. If you
  deployed with the old default, set `MTG_NETWORK_NAME` to your existing
  network's exact name.
- Every AI client connection, including the weekly re-sign-in, now goes
  through the gateway's own Approve/Deny page, and a CSV import can no
  longer stall the gateway.
- Every page except the machine endpoints needs a signed-in member of
  `MTG_REQUIRED_GROUP`.
- One-click registry setup for private images in Portainer, and the proxy's
  forward host spelled out; clearer network, image pull and storage
  steps.

### Security

- A sign-in can only be approved and finished in the browser that started it.
  Before, a crafted link could make a signed-in member's browser finish a
  sign-in someone else had approved, giving them access to that member's
  account. Login and session cookies are now `__Host-` cookies, so everyone
  is signed out of the web pages once after upgrading.
- `MTG_REQUIRED_GROUP` is required: the gateway refuses to start with it
  empty unless `MTG_ALLOW_ANY_IDP_USER=true`.
- A member refused at sign-in for no longer being in the group loses their
  connected apps and web sessions at once. The weekly re-sign-in now counts
  per connected app, and a replayed authorization code revokes the tokens it
  produced.
- Authorization errors show a page instead of redirecting to the app's
  address; registration, token and CIMD requests have size, concurrency and
  timeout limits; old audit rows and unused registrations are pruned.
- Deck changes: categories and new deck names are cleaned and shown on the
  review page; only the app that proposed a change can apply it over MCP (the
  review page still can); the deck is re-checked right before writing; a
  failed apply records how far it got; Archidekt's `Retry-After` is honoured.
- Web pages: sign-out needs the form token, notices only show fixed texts,
  responses are `nosniff` and per-user data is not cached; scan lookups and
  research calls have per-person limits; request bodies are capped while
  being read; on-demand backups are owner-only (0600).
- Deploy: both containers drop every Linux capability except the four the
  start-up script needs and run with no-new-privileges; Mystic Forge is on a
  private network only the gateway can reach; docs cover an encrypted
  overlay network, HSTS, proxy body limits, trusted proxies, MFA and
  restoring a backup safely. CI jobs no longer get package write access or
  keep the checkout token.
- From this release on, a gateway refuses to open a database written by a
  newer version instead of ignoring what it doesn't know (0.1.0 has no such
  check, so going back to it still means restoring a backup). A token whose
  user record is gone is refused, also when no group is required.
- Final review of the release candidate: the Approve/Deny and Apply buttons
  unlock a moment after the page is shown and the person moves, touches or
  types (against double-click window swaps); anonymous sign-in and
  registration floods are capped; disconnecting one app no longer signs you
  out of the web pages; proposals are capped per person and their review rows
  can't be forged by card or category text; deck reports count against the
  per-person research limit; one person can't monopolise Archidekt or the art
  budget. The Android app only lets the gateway's own pages use its bridge,
  downloads and file picker, learns one sign-in site per sign-in, sends
  sign-ins that hop to a further site (for example "Sign in with Discord")
  to the browser, and excludes its data from backups and device transfer.
  Release signing runs only on version tags (and manual runs from main) in a
  protected environment, and /app shows the signing certificate's fingerprint.

## 0.1.0 - 2026-10-05

First release, before this repository existed.

- MCP gateway with its own OAuth 2.1 server for Claude and ChatGPT
  (automatic client registration and Client ID Metadata Documents, PKCE,
  rotating refresh tokens, a consent page), with sign-in handed off to an
  OpenID Connect identity provider.
- Research and goldfish tools from an internal Mystic Forge service,
  allowlisted and run as the signed-in user.
- Deck import from Archidekt links, pasted lists and Archidekt CSV exports.
- Archidekt account linking; deck edits and new decks as proposals the user
  approves, with snapshots, Archidekt backup copies before every edit, and
  faithful restore.
- The `/scan` page: on-device card recognition with printing and foil
  detection and artwork matching.
- A one-link install page and assistant plugin, and the MTG skill.
- Docker Swarm / Portainer stack, nightly database backups, audit log.

[0.5.0]: https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases/tag/v0.5.0
