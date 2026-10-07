# Troubleshooting

Start with the gateway's log. It says why almost everything fails.

```bash
docker compose logs --tail 100 gateway             # Compose
docker service logs --tail 100 mtg_mtg-assistant-gateway  # Swarm, stack named mtg
```

Then find the symptom below. Problems specific to one piece have their own
tables too: Authentik in [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#10-troubleshooting),
connecting an assistant in [CONNECT.md](CONNECT.md#if-sign-in-fails).

## The gateway doesn't start

| What you see | Cause and fix |
| --- | --- |
| Log: `configuration error: ...` and the container exits | A setting is missing or out of range; the message names it. Common ones: `MTG_REQUIRED_GROUP is empty` (set your group, or `MTG_ALLOW_ANY_IDP_USER=true`; see [IDP-OTHERS.md](IDP-OTHERS.md#about-the-group-check)); `MTG_PUBLIC_URL` must be `https://` with no path; `MTG_OIDC_ISSUER` must be `https://`; the session secret must be at least 32 characters; the Fernet key must be a valid key (regenerate it with the command in the deploy guide) |
| Log mentions a `*_FILE` path that can't be read | The secret file isn't mounted or isn't readable by `PUID`. Compose: check `secrets/*.txt` exist and `chown` them to `PUID:PGID`. Swarm: check the three secrets exist with exactly the names in the stack (`docker secret ls`) |
| `Permission denied` on `/data` or `/backups` | The host folders aren't owned by `PUID:PGID`. `sudo chown -R <PUID>:<PGID>` them |
| Compose: `required variable ... is missing a value` | A REQUIRED line in `.env` is empty |
| Swarm: `network "..." is declared as external, but could not be found` | Create the overlay network first, or set `MTG_NETWORK_NAME` to your existing network's exact name (it's case-sensitive) |
| Swarm: tasks stay `pending` with `no suitable node` | `MTG_NODE` or `MF_NODE` doesn't match a node's hostname. `docker node ls` shows them |
| Swarm: tasks `rejected` with `No such image` | Wrong image or tag, or private images without a registry login. See [DEPLOY.md](DEPLOY.md#5-registry-login-in-portainer) |
| `mtg-gateway check-config` | Run it in the container (`docker compose run --rm --no-deps gateway mtg-gateway check-config`) to print the public URL, issuer and the redirect URI to register, without starting the server |

## The site doesn't load

| What you see | Cause and fix |
| --- | --- |
| `502 Bad Gateway` from the proxy | The proxy can't reach the gateway. Check it's on the same Docker network (exact name) or pointed at the right host port, and that the gateway is healthy. From the proxy container: `curl -s http://<gateway name>:8080/healthz` |
| `421 Invalid Host header` on `/mcp` | The proxy rewrote the `Host` header. Make it pass the original host, or list the host it sends in `MTG_ALLOWED_HOSTS` |
| Certificate errors | Fix the proxy's certificate. Claude and ChatGPT refuse self-signed certificates |
| Android app: *No connection to your gateway. Check that the phone is online, then tap Retry.* | The phone couldn't reach the address at all (no signal, DNS, or a typo in the gateway address). It often clears by itself after a moment; tap Retry. If it doesn't, open the same address in the phone's browser. *Your gateway is not answering right now* instead means the proxy answered but the gateway didn't (502 to 504): see the `502 Bad Gateway` row |
| `/healthz` returns `503` | The database can't be opened. Check the log, the data folder's ownership, and that it's on local disk, not NFS or SMB |
| The gateway is under a path (`https://host/mtg/`) and nothing works | Not supported. Give it its own hostname |

## Signing in fails

| What you see | Cause and fix |
| --- | --- |
| Log: `cannot load identity-provider metadata from ...` | The gateway can't reach your identity provider: DNS, firewall, an internal-only network, or a certificate not from a public CA. Test from inside the container: `docker compose exec gateway python -c "import urllib.request;print(urllib.request.urlopen('<issuer>/.well-known/openid-configuration').status)"` |
| Same, and the identity provider is behind the same proxy on the same machine | The gateway resolves the provider's public name to your public IP, and many home routers don't route that back in ("NAT loopback"). Give the container a direct route: `extra_hosts: ["auth.example.com:<proxy's LAN IP>"]` in Compose, or a network alias for the proxy on a shared Docker network |
| Log: `identity-provider metadata issuer ... does not match configured ...` | Copy the issuer exactly as the provider's discovery document shows it |
| Provider says the redirect URI is invalid | Register exactly `https://<your host>/auth/callback` |
| Page *Sign-in could not be completed: the identity provider refused the gateway's client ID or client secret*; log: `identity provider rejected the code exchange (HTTP 401, invalid_client)` | Wrong client secret or client ID, the provider's client type isn't Confidential, or the provider expects a different client authentication method: set `MTG_OIDC_TOKEN_AUTH_METHOD` to `client_secret_post` (the default) or `client_secret_basic` to match ([IDP-OTHERS.md](IDP-OTHERS.md)) |
| Page *… refused the sign-in code*; log: `… (HTTP 400, invalid_grant)` | Usually harmless: the sign-in page was reloaded or the code took more than a minute to come back, so try again. If it happens every time, the provider's redirect URI doesn't exactly match `MTG_PUBLIC_URL` + `/auth/callback` |
| Page *… could not verify the identity provider's ID token*; log: `ID token validation failed: …` | `signed with HS256`: the provider has no signing key, so pick an RSA or EC one. `ExpiredTokenError` or `issued in the future`: the clocks are more than a minute apart. `InvalidClaimError ('aud')`: the client ID doesn't match. `InvalidClaimError ('iss')`: `MTG_OIDC_ISSUER` doesn't match the provider. `ExceededSizeError`: the token is over the gateway's size limits (16 KB header, 2 MB claims); before 0.6.2 the limits were much lower, so upgrade |
| Gateway page: *Your account is not in the group that may use this service* | The groups claim (`MTG_OIDC_GROUPS_CLAIM`, default `groups`) doesn't contain `MTG_REQUIRED_GROUP` (or, since 0.6.5, `MTG_ADMIN_GROUP`) exactly. See [what your provider sends](IDP-OTHERS.md#about-the-group-check) |
| Everyone suddenly appears as a new user | The provider's subject (`sub`) changed: a different subject mode, or a different provider. Put it back |
| Members are signed out about every hour and have to sign in again | The identity provider issues no refresh token. Authentik: add the `offline_access` scope mapping to the provider ([IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#4-create-the-oauth2openid-provider)). Others: allow refresh tokens / `offline_access` for the client ([IDP-OTHERS.md](IDP-OTHERS.md#about-the-group-check)) |
| Page *The sign-in service can't be reached to confirm your access* (HTTP 503, `idp_unavailable` for apps) | The gateway checks membership with the identity provider before serving signed-in requests, and the provider is down or unreachable from the gateway (same causes as `cannot load identity-provider metadata` above), or it refuses the gateway's own client (log: `identity provider refused the refresh with HTTP 400/401 (invalid_client)`, for example after the client secret changed). Nothing is revoked; it works again once the provider answers. A connection that drops before any answer is tried once more first (0.6.5 and newer) |
| Members are signed out on every click; log says `membership check found no '...' claim in userinfo or a refreshed ID token` | The provider sends the groups claim (`MTG_OIDC_GROUPS_CLAIM`) neither from its userinfo endpoint nor in a refreshed ID token, so the live check can't see a removal and fails closed. Make the provider send it ([IDP-OTHERS.md](IDP-OTHERS.md#about-the-group-check)) |
| Pages say *The sign-in service can't be reached to confirm your access*; log: `answered userinfo with HTTP 400; the access token is … bytes` | The provider's access token is too big for a request header (Nginx refuses header lines over 8 KB by default). 0.6.5 and newer send such a token in the request body instead; upgrade. The `unusually large tokens` log line names the claim that bloats it; for Authentik see [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#10-troubleshooting) |
| Everyone was signed out right after an upgrade to 0.6.1 | Expected, once: sessions from before have no identity-provider tokens on file for the live membership check. Everyone signs in again and reconnects their AI app |
| Page *This account belongs to a different sign-in provider than the one this gateway uses now* | `MTG_OIDC_ISSUER` now points at another provider than the one this account first signed in with. If you only moved the same provider to a new address, list the old issuer URL in `MTG_OIDC_PREVIOUS_ISSUERS` instead. Otherwise, if it's the same person, delete the old account's data on the admin page, then they sign in again ([IDP-OTHERS.md](IDP-OTHERS.md#switching-providers-later)) |
| *Too many sign-in attempts from your network* | More than 30 sign-ins started from one address in a minute. Wait a minute. If it hits everyone, the gateway sees your proxy's address for every visitor: list the proxy in `MTG_TRUSTED_PROXIES` and make it send `X-Forwarded-For` |
| *This sign-in was started in a different browser* | Finish sign-in in the browser the assistant opened. If it happens to everyone, make sure people reach the gateway only at the exact host in `MTG_PUBLIC_URL`, over https (the sign-in cookie is tied to that host), and that nothing in between strips cookies |

## Using it

| What you see | Cause and fix |
| --- | --- |
| The account icon shows initials instead of the member's picture | The gateway shows the picture the identity provider sends: a Gravatar, or with Authentik an uploaded picture. On Authentik 2026.8.3 and newer, uploaded pictures need the optional scope mapping in [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#profile-pictures). SVG pictures and addresses other than Gravatar are never used. A change shows up within a few seconds of the member's next page |
| Research tools answer "the research service is unavailable right now" | The gateway is fine, Mystic Forge isn't. Check its container and logs |
| No research tools at all | `MTG_MYSTIC_FORGE_URL` is empty. Set it to `http://mysticforge:8000/mcp` (Compose) or `http://mtg-assistant-mysticforge:8000/mcp` (Swarm stack) |
| `apply_proposal` answers `writes_disabled` | `MTG_WRITES_ENABLED` is `false` |
| `apply_proposal` answers `browser_required` | `MTG_APPLY_VIA_MCP` is `false`; approve on the card in the chat or on the review page |
| No Approve/Reject card appears next to a proposal | The app doesn't render MCP Apps (Claude Code, older clients), the person declined to display the app the first time Claude asked, or `MTG_APPLY_IN_CHAT` is `false`. The review link in the text always works |
| `confirm_proposal` answers `invalid_approval`, log shows `approval_refused` | Something called the card's tool without its one-time code: a stale card, another app, or an assistant trying it. Nothing was sent; the proposal is still pending for the review page |
| `apply_proposal` answers `apply_too_soon` | Working as designed: wait `retry_after_seconds` and try again |
| `rate_limited`: "Your account has used its ... Archidekt requests" | The member's Archidekt budget (`MTG_ARCHIDEKT_CALLS_PER_10_MIN`, 120 per 10 minutes) is used up, often by an assistant stuck in a loop. It refills over a few minutes |
| `insufficient_scope` | The app was connected with the read-only scope `mtg.read`. Reconnect it with the `mtg` scope to make changes |
| Archidekt link keeps dropping | See [OPERATIONS.md](OPERATIONS.md#archidekt-links-and-relinking) |
| The assistant can't connect at all | Work through [CONNECT.md](CONNECT.md#if-sign-in-fails) |

Still stuck? Open an issue with the gateway version, how you deploy, your
identity provider and proxy, and the log lines, with secrets and hostnames
removed.
