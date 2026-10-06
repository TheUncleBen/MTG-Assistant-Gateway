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
| `/healthz` returns `503` | The database can't be opened. Check the log, the data folder's ownership, and that it's on local disk, not NFS or SMB |
| The gateway is under a path (`https://host/mtg/`) and nothing works | Not supported. Give it its own hostname |

## Signing in fails

| What you see | Cause and fix |
| --- | --- |
| Log: `cannot load identity-provider metadata from ...` | The gateway can't reach your identity provider: DNS, firewall, an internal-only network, or a certificate not from a public CA. Test from inside the container: `docker compose exec gateway python -c "import urllib.request;print(urllib.request.urlopen('<issuer>/.well-known/openid-configuration').status)"` |
| Same, and the identity provider is behind the same proxy on the same machine | The gateway resolves the provider's public name to your public IP, and many home routers don't route that back in ("NAT loopback"). Give the container a direct route: `extra_hosts: ["auth.example.com:<proxy's LAN IP>"]` in Compose, or a network alias for the proxy on a shared Docker network |
| Log: `identity-provider metadata issuer ... does not match configured ...` | Copy the issuer exactly as the provider's discovery document shows it |
| Provider says the redirect URI is invalid | Register exactly `https://<your host>/auth/callback` |
| Log: `identity provider rejected the code exchange (HTTP 400/401)` | Wrong client secret or client ID, or the provider expects a different client authentication method: set `MTG_OIDC_TOKEN_AUTH_METHOD` to `client_secret_post` (the default) or `client_secret_basic` to match ([IDP-OTHERS.md](IDP-OTHERS.md)) |
| Log: `ID token validation failed` | The provider signs with HS256 (pick an RSA or EC signing key), the client ID doesn't match, or the clocks are more than a minute apart |
| Gateway page: *Your account is not in the group that may use this service* | The groups claim (`MTG_OIDC_GROUPS_CLAIM`, default `groups`) doesn't contain `MTG_REQUIRED_GROUP` exactly. See [what your provider sends](IDP-OTHERS.md#about-the-group-check) |
| Everyone suddenly appears as a new user | The provider's subject (`sub`) changed: a different subject mode, or a different provider. Put it back |
| *This sign-in was started in a different browser* | Finish sign-in in the browser the assistant opened. If it happens to everyone, make sure people reach the gateway only at the exact host in `MTG_PUBLIC_URL`, over https (the sign-in cookie is tied to that host), and that nothing in between strips cookies |

## Using it

| What you see | Cause and fix |
| --- | --- |
| Research tools answer "the research service is unavailable right now" | The gateway is fine, Mystic Forge isn't. Check its container and logs |
| No research tools at all | `MTG_MYSTIC_FORGE_URL` is empty. Set it to `http://mysticforge:8000/mcp` (Compose) or `http://mtg-assistant-mysticforge:8000/mcp` (Swarm stack) |
| `apply_proposal` answers `writes_disabled` | `MTG_WRITES_ENABLED` is `false` |
| `apply_proposal` answers `browser_required` | `MTG_APPLY_VIA_MCP` is `false`; apply on the review page |
| `apply_proposal` answers `apply_too_soon` | Working as designed: wait `retry_after_seconds` and try again |
| Archidekt link keeps dropping | See [OPERATIONS.md](OPERATIONS.md#archidekt-links-and-relinking) |
| The assistant can't connect at all | Work through [CONNECT.md](CONNECT.md#if-sign-in-fails) |

Still stuck? Open an issue with the gateway version, how you deploy, your
identity provider and proxy, and the log lines, with secrets and hostnames
removed.
