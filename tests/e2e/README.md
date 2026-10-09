# End-to-end test environment

`tests/e2e` deploys the gateway the way the owner does, then uses it the way
Claude and ChatGPT do, on one machine with Docker.

What it stands up (`run.sh up`), following [docs/DEPLOY.md](../../docs/DEPLOY.md):

| DEPLOY.md step | What the harness does |
| --- | --- |
| 1 overlay network | `docker network create --driver overlay --attachable mtg-gateway` on a single-node Swarm |
| 2 storage | two directories under `tests/e2e/.generated/`, bind-mounted like `/srv/mtg-gateway/...` |
| 3 Authentik | a real Authentik (server + worker, Postgres, Redis) and `authentik_setup.py`, which creates the provider, application, group bindings and four test users through the API, exactly as the admin click-path does |
| 4 secrets | the three `docker secret create` commands from the guide |
| 6 stack | `docker stack deploy -c deploy/portainer-stack.yml mtg` with the variables from `stack.env.example` |
| 7 proxy | an nginx edge (`stacks/edge-stack.yml`) with the NPM proxy-host settings from the guide, serving `https://mtg.e2e.test` and `https://auth.e2e.test` with a test CA |
| 8 verify | the three `curl` checks from the guide |

Two more services stand in for the outside world, so no test ever reaches archidekt.com or
scryfall.com:

- `archidektmock` serves `tests/fake_archidekt.py` over HTTP; the gateway stack is deployed with
  `MTG_ARCHIDEKT_BASE=http://archidektmock:8000/api` (the stack file exposes that setting, default
  unchanged). Its decks come from the sample Archidekt CSV export (`tests/fixtures/sample_deck.csv`).
- `scryfallmock` serves `tests/fake_scryfall.py` (fixtures recorded live) over HTTPS **as
  `api.scryfall.com`**: the gateway's Scryfall client has no base-URL setting, so the service gets that
  network alias and a certificate for the name signed by the test CA, which the `-trusted` gateway
  image accepts. It answers batch lookups in reverse order on purpose, to prove the gateway matches
  results by identifier and not by position.
- `cimdmock` publishes a Client ID Metadata Document at `https://cimd.e2e.test/claude-e2e.json`, the
  way an MCP client that identifies by URL does. The gateway only accepts such documents from hosts
  that resolve to a public address, so the harness creates the test overlay network in a public
  range (`E2E_SUBNET`, default `11.111.0.0/24`; nothing is routed there). That is the one departure
  from the guide's `docker network create` command.

What it checks (`run.sh test`, pytest):

- `test_01_discovery.py`: protected-resource and authorization-server metadata, the bearer challenge on `/mcp`, and that nothing but the documented pages answers anonymously.
- `test_02_sign_in.py`: the full client flow with the MCP Python SDK and a scripted Chromium signing in to Authentik: two users get distinct identities, one user from two clients is the same person, a user outside `MTG_REQUIRED_GROUP` is refused by the gateway, a user not bound to the application is refused by Authentik.
- `test_03_tokens.py`: PKCE, single-use codes, refresh rotation, cross-client refresh, revocation, foreign `resource`, unregistered redirect URIs, callback replay.
- `test_04_operations.py`: no published ports except the edge, secret values absent from the service environment, process runs as `PUID`, Mystic Forge reachable only inside the overlay network and listing its tools, `mtg-gateway backup` and a restore check, state surviving `docker service update --force`, memory under the stack limits.
- `test_05_decks.py`: the deck tools against the Archidekt mock. Proxied Mystic Forge tools are listed (and its blocked ones are not); `parse_decklist`; `parse_deck_export` on the owner's CSV (72 rows, 100 cards); `get_deck` for a public deck without a link, a private one refused; linking an Archidekt account on `/account` in the browser (wrong password shown, right one linked) for two users; `propose_deck_changes` and `propose_new_deck` with writes off: proposals are kept, `apply_proposal` answers `writes_disabled`, nothing reaches the mock; two users cannot see or apply each other's proposals or edit each other's decks; then the switches are flipped with `docker service update` as the owner would in Portainer: with `MTG_WRITES_ENABLED=true` and `MTG_APPROVAL_MODE_DEFAULT=manual`, `apply_proposal` answers `browser_required` and the review page's Apply button applies, verifies and snapshots; a deck edited elsewhere in the meantime is refused as stale and nothing is sent; with `MTG_APPROVAL_MODE_DEFAULT=auto` the assistant applies an edit over MCP, and the 72-row new deck from the CSV applies in the background (`applying` with its progress, then `applied` and verified). The audit log shows both routes; no Archidekt password or session token is in the database or the backup. The switches go back to writes off and `manual` at the end.
- `test_06_scan.py`: `resolve_cards` with exact, printing, not-found and OCR-noise inputs against the Scryfall mock, one batched collection request, results matched by identifier although the mock answers in reverse order; `save_scan_session`, `list_scan_sessions`, `get_scan_session` by id and by name; a second user sees none of it over MCP or the `/scan` API; `/scan` is behind the login and carries its own Content-Security-Policy (wasm, workers, Scryfall images) while `/account`, `/proposals` and `/` keep the strict one.
- `test_07_cimd_and_logout.py`: the browser-only paths. A client identified by URL reaches the consent page, which names the client and warns about the loopback redirect; Approve carries the browser on to Authentik (a page policy that blocks that cross-origin redirect fails here), then the loopback gets a code that the public client exchanges with PKCE alone and the token lists tools; Deny sends `access_denied` back and kills the pending login; a document that is not JSON is refused; Sign out answers with `Clear-Site-Data`, lands on the front page and the account page asks for a login again.
- `test_08_live_membership.py`: removing a member from the group in Authentik cuts them off on their next request (the access token is refused, the refresh fails) within `MTG_MEMBERSHIP_CHECK_TTL`. Authentik itself keeps honouring a removed member's refresh token, so this proves the gateway's own live membership check against the real provider.
- `test_09_profile_picture.py`: a picture uploaded in Authentik reaches the account icon (`/account/avatar`) through the optional scope mapping in [docs/IDP-AUTHENTIK.md](../../docs/IDP-AUTHENTIK.md), which `authentik_setup.py` installs exactly as printed there; a member without one gets initials.

Test users (passwords are generated per run and written to `.generated/authentik.json`):

| User | Authentik groups | Expected |
| --- | --- | --- |
| `alice-test`, `bob-test` | MTG Assistant Gateway Users (bound to the app, and `MTG_REQUIRED_GROUP`) | sign in, call `whoami` |
| `guest-test` | MTG Assistant Gateway Guests (bound to the app, not the required group) | Authentik allows, gateway refuses |
| `outsider-test` | none | Authentik refuses |

Every sign-in step the tests check runs in Chromium (the DCR flow through the MCP SDK, the raw
PKCE flow, the consent page, Authentik's login, the account and review pages, sign out); no test
reaches a browser page with an HTTP client that follows redirects, so what a browser would refuse
(for example a Content-Security-Policy that blocks a cross-origin redirect after a form POST) fails CI.

Run it locally (needs Docker, Python 3.12, Chromium via `python -m playwright install chromium`, and root or sudo for `/etc/hosts`):

```bash
pip install -c constraints.txt -e ".[dev]" playwright
tests/e2e/run.sh up
tests/e2e/run.sh test
tests/e2e/run.sh down
```

The workflow's matrix knows three Authentik versions: `2025.6.4`, which `docs/DEPLOY.md` was written
against, `2026.2.2`, and `2026.8.3`, the current release. A pull request runs the suite once, against the current
release, and only when its diff touches the gateway package (the whole `src/mtg_gateway/scan/` subpackage excluded), the stack or Docker
files, the suite itself or its dependencies; a manual run (Actions tab, "Run workflow") runs
`2025.6.4` and `2026.8.3`, or the one chosen, and is how main is re-checked. `authentik_setup.py`
records what each version produced in `.generated/authentik-evidence.json` (version, applied
blueprints, flows, provider fields, group bindings) and the sign-in test records the identity
Authentik asserted in `.generated/identity-evidence.json`; the workflow prints and uploads both,
nothing secret in either.

A local `run.sh up` deploys Authentik `2025.6.4` unless you set `E2E_AUTHENTIK_IMAGE` (pull requests
use `ghcr.io/goauthentik/server:2026.8.3`). `run.sh all` does up, test and down in one go; CI runs the
three steps separately, with `down` always. `run.sh test` passes extra arguments to pytest (for
example `tests/e2e/run.sh test -k decks`); plain `pytest tests/e2e` collects nothing, because
`pyproject.toml` leaves the suite out of the normal test run.

`run.sh` knobs: `E2E_AUTHENTIK_IMAGE`, `E2E_NGINX_IMAGE`, `E2E_POSTGRES_IMAGE`, `E2E_REDIS_IMAGE`,
`MTG_IMAGE`/`MTG_TAG` and `MF_IMAGE`/`MF_TAG` (deploy existing images instead of the two built
locally), `E2E_SKIP_BUILD=1`, `E2E_DOCKER_BUILD_ARGS` (extra `docker build` flags, for example
`--network=host` behind a proxy), `E2E_NO_HOSTS=1`, `E2E_HTTPS_PORT`, `E2E_SUBNET`,
`MTG_ARCHIDEKT_BASE`, `E2E_CHROMIUM` (browser executable),
`E2E_SWARM_DNSRR=1` (sandboxes without IPVS cannot route Swarm virtual IPs; this switches the
gateway stack's two services to DNS round robin after deploying, the stack file is untouched).
The test host names are added to `no_proxy`, so an outbound proxy never sees them.
The suite can run more than once against one deployment: the deck tests reset the Archidekt
mock and unlink leftover accounts first.
Everything generated lives in `tests/e2e/.generated/` (git-ignored) and is removed by `down`.

The gateway image gets a test-only extra layer (`<tag>-trusted`) holding the test
CA, because production Authentik sits behind a public certificate and the image
has no hook for private CAs. The gateway code is not modified.
