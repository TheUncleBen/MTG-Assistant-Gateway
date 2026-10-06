# Changelog

Notable changes for people who run or use the gateway. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) as described in
[docs/VERSIONS.md](docs/VERSIONS.md): every update to `main` is a new version
with its own image (`1.2.3`), git tag (`v1.2.3`) and read-only branch
(`release/1.2.3`); `latest` is always the newest.

## [0.7.0] - 2026-10-06

A fourth security pass. The headline: taking someone out of the group at
the identity provider now cuts them off on their next request, not at their
next sign-in.

### Upgrading from 0.6.0

1. **Authentik: add the `offline_access` scope mapping.** Open the provider
   (**Applications → Providers → your provider → Edit → Advanced protocol
   settings → Scopes**) and add `authentik default OAuth Mapping: OpenID
   'offline_access'` next to the `openid`, `email` and `profile` ones. Without
   it Authentik quietly issues no refresh token, and every member is asked to
   sign in again each time Authentik's access token runs out (an hour by
   default). Other providers: see
   [docs/IDP-OTHERS.md](docs/IDP-OTHERS.md#about-the-group-check).
2. **Everyone signs in once more.** Sessions and assistant connections from
   before the upgrade have no identity-provider tokens on file, so the first
   request after the upgrade signs the person out. Each member signs in to
   the web pages and the Android app again, and each connected AI app asks
   to reconnect once.
3. `MTG_OIDC_SCOPES` now defaults to `openid profile email offline_access`.
   If you set it yourself, `offline_access` is added for you.
4. The example env files now ship `MTG_APPLY_VIA_MCP=false`. Your own
   deployment keeps whatever it sets; nothing changes unless you change it.
   `false` means a change is applied only by the member's own click on the
   review page. `true` lets the assistant apply after a yes in chat, which is
   weaker: text the assistant reads (a deck description, a card note) could
   trick it into applying its own proposal.
5. The database is upgraded on start (schema 9). Take a backup first, as for
   every release; the 0.6.0 image refuses to start on the upgraded file.

### Security

- **Live membership.** Before serving any request that carries a browser
  session or a bearer token, the gateway asks the identity provider's
  userinfo endpoint whether the person is still allowed in (cached for
  `MTG_MEMBERSHIP_CHECK_TTL` seconds, 5 by default). Removal from
  `MTG_REQUIRED_GROUP` or `MTG_ADMIN_GROUP`, or deactivating or deleting the
  account at the provider, takes effect on the person's next request. Before,
  it took effect at their next sign-in, up to a week later for assistants.
  Tested live against Authentik 2026.2.2, which on its own keeps honouring a
  removed member's refresh token.
- A member removed from `MTG_REQUIRED_GROUP` (or deactivated or deleted at
  the provider) loses every gateway token, browser session, stored
  identity-provider token and their Archidekt link at once.
- If the identity provider can't be reached, requests are refused with 503
  and nothing is revoked (fail closed). Service comes back with the provider.
- Code exchanges and token refreshes at `/token` check membership the same
  way. A leftover code or refresh token of someone who deleted their data
  can't mint new tokens.
- The identity provider's access and refresh tokens are stored encrypted
  with the `mtg_fernet_key` secret.
- Each member is pinned to the identity provider (issuer) they first signed
  in with. An account from a different provider with the same subject is
  refused instead of inheriting the old member's decks, apps and Archidekt
  link; an admin can delete the old account's data.
- An ID token issued to several audiences must name the gateway as its
  authorized party (`azp`).
- **Sign out on all my devices** on the sign-out page (`/logout`) ends every
  browser and Android app session. For an hour after signing out, a sign-in to the
  gateway's pages on that device asks the identity provider for the password
  again (`prompt=login`), so the next person on a shared phone isn't signed
  straight back in.
- `Strict-Transport-Security` (one year, no `includeSubDomains`) on every
  response when `MTG_PUBLIC_URL` is https. The example proxy configs set the
  same header.
- Pages forbid framing and base-URL changes in their CSP (`frame-ancestors
  'none'`, `base-uri 'none'`), the offline page too.
- Sign-in floods: each network may start 30 sign-ins a minute, and unfinished
  sign-ins are capped at 10 per browser and 500 per AI client. When a cap is
  hit, the network holding the most unfinished sign-ins loses its oldest, so
  a flood only pushes out its own.
- Client ID Metadata Documents: an address with a query string is refused; a
  failed fetch blocks new addresses on that host for a minute; new addresses
  are fetched at most 10 times a minute per site and 60 in all; a document is
  capped like a registered client (20 redirect URIs, 2000 characters each,
  8 KB); the cache keeps at most 1000 documents, 50 per host.
- Archidekt: each member may start `MTG_ARCHIDEKT_CALLS_PER_10_MIN` (120)
  Archidekt calls per 10 minutes, the research tools' `archidekt_*` calls
  included. Five failed Archidekt link attempts in 15 minutes block further
  attempts for a while.
- Research tools: oversized arguments (goldfish games, turns and odds sizes,
  text over 200 kB) are refused before anything is forwarded.
- Read-only connections: a token issued only for the `mtg.read` scope can
  read but not propose, apply, reject, run reports or save scans.
- Proposals record the app that made them and show it on the review page.
  Disconnecting an app rejects its pending proposals. One app may hold 30
  pending proposals per member (a member 100 in all), so one app can't use
  up every slot.
- A new-deck proposal is tied to the Archidekt account linked when it was
  made; applying it after linking a different account is refused
  (`other_account`).
- An app can delete through the API only the reports it ran itself.
- An Archidekt session refresh that races an unlink or a relink no longer
  overwrites or revokes the newer link.
- A deck apply or report still running when the member deleted their data
  stores nothing for the deleted account. **Delete my data** removes deck
  covers too.
- A deck settings change re-reads the deck after the backup copy and refuses
  if its details changed meanwhile.
- Deeply nested JSON gets a 400 instead of an unhandled error.
- Android app: the consent page's **Deny** (which goes back to the
  application's own site) always opens in the browser, the app learns the
  sign-in service only from a gateway `/login` redirect, and a page from
  another site that loads without the app being asked is stopped and opened
  in the browser.
- Compose: each service may run at most 512 processes and threads. CI checks
  the Gradle wrapper before any Android build.

### Added

- `MTG_MEMBERSHIP_CHECK_TTL` (seconds, 0 to 60, default 5; 0 asks the
  provider on every request) and `MTG_ARCHIDEKT_CALLS_PER_10_MIN` (10 to
  100000, default 120).
- Admin page: **Delete data** for another member (a former member, or an
  account from an earlier identity provider). It needs a confirmation tick
  and can't be used on yourself.
- Admin actions on a member's account show up in that member's own activity
  log as done by an administrator.
- The Account page shows each connected app's client id next to its name,
  so two apps with the same name can be told apart.

### Changed

- The example env files ship `MTG_APPLY_VIA_MCP=false` (see Upgrading).
- Moving a card into a category the deck doesn't count (Maybeboard,
  Sideboard) is shown as "leaves the deck" and counted as a removal.
- Applied proposals are deleted a year after they were applied; closed ones
  still go after 30 days.
- The "data deleted" page and the Account page say that nightly database
  backups keep a copy until they age out (`MTG_BACKUP_KEEP_DAYS`).

## [0.6.0] - 2026-10-06

A production-readiness pass: access checks, failure handling, the database,
monitoring, testing on different screens and connections, and what people
see when something goes wrong. The findings and their sources are summarised
in the release notes' pull request.

### Upgrading from 0.5.0

1. Paste the new `deploy/portainer-stack.yml` (or `deploy/compose/docker-compose.yml`)
   and update the stack. It adds two settings: `stop_grace_period: 120s`, so a
   redeploy lets a running deck apply finish, and a `logging:` block that caps
   each container's logs at three 10 MB files. Nothing in your environment
   variables changes.
2. The database is upgraded on start (schema 6: new indexes only). Take a
   backup first, as for every release; the 0.5.0 image refuses to start on the
   upgraded file.

### Security

- `/mcp` now re-checks `MTG_REQUIRED_GROUP` on every request, like the web
  pages and the JSON API already did. Before, an assistant kept working until
  its access token expired (up to an hour) after the member's recorded groups
  lost the group.
- `/healthz` no longer puts the database error text in its public reply; the
  detail goes to the log.

### Added

- **Delete my data** on the Account page: one checkbox and one button delete
  everything the gateway keeps for you (proposals, snapshots, reports, scan
  sessions, the Archidekt link, connected apps, usage counters and the sign-in)
  and sign you out. Decks on Archidekt are not touched.
- A **System** card on the admin page: version, database schema and size, the
  newest backup, and the last backup's failure if it failed.
- A plain "You're offline" page when a gateway page can't be reached, instead
  of the browser's own error screen. The service worker builds it on the spot
  and never caches a page.
- Android app: if the page's renderer crashes (it can happen under memory
  pressure during a scan) the app rebuilds the page instead of closing; no
  connection and a restarting gateway (502/503/504) get their own plain
  message with Retry; and a camera permission that was denied for good opens
  the app's settings with an explanation.

### Changed

- Friendly error pages: an unknown address or an unexpected error now shows
  a styled page that says what happened and what to do next (JSON on
  `/api/`, `/mcp` and the OAuth endpoints), never a bare "Not Found" or
  "Internal Server Error". Unexpected errors are logged with their traceback
  and counted on the admin page.
- Every button shows that it was pressed at once, and a button that sends a
  form shows a busy state until the next page arrives; a second press of the
  same form while it is sending is ignored.
- Button and badge text now meets WCAG AA contrast: dark text on the orange
  and green buttons and badges, and a deeper red for danger buttons. White on
  the old orange was 2.4:1.
- Long button labels wrap instead of pushing the Account and Install pages
  wider than a 320 px phone screen; the deck editor's bottom bar keeps its
  side margins on phones.
- The scan page says "no connection to the gateway" instead of "Failed to
  fetch".
- Scan results split piles of more than 99 copies and add `change_batches`
  when there are more than 40 changes, so they fit `propose_deck_changes`.
- `deck_stats` and `compare_decks` accept the snapshot ids `list_snapshots`
  returns (they only recognised a `snap_` prefix that real ids don't have).

### Fixed

- A deck apply cut off by a crash or redeploy was left "applying" for up to an
  hour (or until the next nightly cleanup); it is now marked failed as soon
  as the gateway starts, pointing at the snapshot taken before it. The
  gateway also lets running requests finish for up to 100 s when it is
  stopped.
- The cleanup of expired sign-ins, tokens and proposals ran only with the
  nightly backup (so never without `MTG_BACKUP_DIR`, and not at all after a
  failed backup). It now runs hourly on its own.
- Deck snapshots were never deleted. The newest 25 of each deck are kept,
  plus any a pending restore needs. Usage counters are kept for 400 days and
  remembered deck covers for 180.
- Nightly backups are read through their own connection (requests no longer
  wait for the copy), written as standalone files, and checked with SQLite's
  `quick_check` before they are kept.
- A Mystic Forge that hangs no longer holds up the assistant's tool list for
  up to five minutes: the listing gives up after 10 seconds and is retried a
  minute later.
- A damaged database file at start gives a one-line message pointing at the
  backups instead of a Python traceback.
- Indexes for the cleanup and snapshot lists; an explicit SQLite busy timeout;
  each database upgrade step commits together with its version number.
- Outbound request URLs are logged only at `DEBUG`.

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
