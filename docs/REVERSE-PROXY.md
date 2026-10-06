# Putting a reverse proxy in front

The gateway speaks plain HTTP on port 8080. Something in front of it has to
give it an `https://` address on your domain, because Claude and ChatGPT
only connect to HTTPS servers. Any reverse proxy works. Ready-made configs
for the common ones are in [`deploy/proxy/`](../deploy/proxy/).

What's been checked: the Caddyfile passes `caddy validate` (Caddy 2.10), the
nginx block passes `nginx -t` (nginx 1.24), the Compose overrides pass
`docker compose config`, and the end-to-end tests run behind an nginx with
the same header, buffering and timeout settings. The Traefik labels and the NPM settings follow those
projects' documentation and real use, but aren't run by the tests.

## What every proxy needs

| Requirement | Why |
| --- | --- |
| Serve the gateway at the **root** of its own hostname (`https://mtg.example.com/`), not under a path | OAuth discovery for MCP clients lives at fixed paths on the host, and every gateway route is absolute. `MTG_PUBLIC_URL` with a path is refused at startup |
| **Pass the original `Host` header** | The `/mcp` endpoint only answers requests whose `Host` matches `MTG_PUBLIC_URL` (protection against DNS rebinding). Most proxies do this by default. If yours can't, set `MTG_ALLOWED_HOSTS` |
| Send `X-Forwarded-For` and `X-Forwarded-Proto` | Used for logs and the request scheme. The gateway trusts them only from addresses in `MTG_TRUSTED_PROXIES` (default: private networks and loopback) |
| Allow request bodies of at least **2 MB** | Saved card-scan sessions can be up to 2 MB. nginx's default of 1 MB is too small |
| Read timeout of about **300 seconds** | Most calls take well under a second, but goldfish simulations and some research calls can take a while |
| A valid certificate | Let's Encrypt is fine. Self-signed certificates won't work with Claude or ChatGPT |

What it doesn't need: websockets, sticky sessions, special headers, or
response buffering turned off (the `/mcp` endpoint answers with plain JSON,
not a stream). Turning buffering off is harmless, and the examples do it.

**Never proxy Mystic Forge.** It has no login. Only the gateway should
reach it.

## Which example to use

| You run | Use | Guide |
| --- | --- | --- |
| Nothing yet, one machine | Caddy, added to the Compose deployment | [Caddy](#caddy) |
| Traefik already | Docker labels | [Traefik](#traefik) |
| nginx on the host | A server block | [nginx](#nginx) |
| Nginx Proxy Manager | A proxy host in its UI | [Nginx Proxy Manager](#nginx-proxy-manager) |
| Something else (HAProxy, Cloudflare Tunnel, Pangolin, ...) | The table above is all it needs | |

## Caddy

Caddy gets and renews certificates by itself, which makes it the simplest
choice for a fresh single machine.

1. Point your domain's DNS at the machine, and make sure ports 80 and 443
   reach it from the internet (Let's Encrypt checks them).
2. Edit [`deploy/proxy/caddy/Caddyfile`](../deploy/proxy/caddy/Caddyfile):
   replace `mtg.example.com` with your hostname.
3. From `deploy/compose/`, start everything with the Caddy override:

   ```bash
   docker compose -f docker-compose.yml -f ../proxy/caddy/compose.caddy.yml up -d
   ```

   The override adds a `caddy` service on the gateway's network and removes
   the gateway's own port on the host, so Caddy is the only way in. It uses
   the `!reset` tag, which needs Docker Compose 2.24 or newer
   (`docker compose version`).

Already run Caddy elsewhere? Copy the site block from the Caddyfile and
change `gateway:8080` to wherever your Caddy can reach the gateway:
`127.0.0.1:8080` if Caddy is installed on the same machine (not in a
container), or `gateway:8080` after putting your Caddy container on the
gateway's network.

## Traefik

For a Traefik you already run with the Docker provider:

1. Add to `deploy/compose/.env`:

   ```bash
   MTG_HOSTNAME=mtg.example.com      # same host as MTG_PUBLIC_URL
   TRAEFIK_NETWORK=proxy             # the Docker network your Traefik is on
   TRAEFIK_ENTRYPOINT=websecure      # your HTTPS entrypoint's name
   TRAEFIK_CERTRESOLVER=letsencrypt  # your certificate resolver's name
   ```

2. Start with the Traefik override from `deploy/compose/`:

   ```bash
   docker compose -f docker-compose.yml -f ../proxy/traefik/compose.traefik.yml up -d
   ```

   It attaches the gateway to Traefik's network, adds the router labels,
   keeps Mystic Forge out of Traefik, and removes the port on the host.
   If your Traefik uses a default or wildcard certificate instead of a
   resolver, replace the override's `...mtg-gateway.tls.certresolver` label
   with `traefik.http.routers.mtg-gateway.tls: "true"`.

On **Swarm**, the same labels go under the gateway service's `deploy:`
`labels:` in your copy of the stack file, and the gateway joins Traefik's
overlay network. Traefik's Swarm provider reads `deploy.labels`, not
container labels.

## nginx

[`deploy/proxy/nginx/mtg-gateway.conf`](../deploy/proxy/nginx/mtg-gateway.conf)
is a complete server block: HTTP to HTTPS redirect, a 5 MB body limit, the
`Host` and forwarded headers, buffering off and a 300 second read timeout.

1. Get a certificate for the hostname first, for example
   `sudo certbot certonly --standalone -d mtg.example.com` (stop nginx for a
   moment, or use `--webroot -w /var/www/certbot` while nginx serves the
   port-80 block, which answers ACME challenges from that folder).
2. Copy the file to `/etc/nginx/conf.d/` and change `server_name`, the
   certificate paths, and the `proxy_pass` address (`127.0.0.1:8080` when
   nginx is installed on the same machine as a Compose deployment).
3. `sudo nginx -t && sudo nginx -s reload`.


## Nginx Proxy Manager

In NPM, **Hosts → Proxy Hosts → Add Proxy Host**:

- **Domain names:** `mtg.example.com`
- **Scheme** `http`, **Forward hostname** the gateway's name on a network
  NPM shares with it, **port** `8080`:
  - Swarm stack named `mtg`: `mtg_mtg-assistant-gateway` (stack name, underscore,
    service name);
  - Compose: `gateway`, after putting the gateway on NPM's Docker network
    (the Traefik override shows the pattern). NPM runs in a container, so
    `127.0.0.1` there means NPM itself, not the host; going through the host
    instead needs `MTG_HTTP_BIND` set to an address NPM can reach, such as
    the Docker bridge address `172.17.0.1`.
- **Block common exploits:** on. **Websockets support:** optional, the
  gateway doesn't use websockets.
- **SSL** tab: request a Let's Encrypt certificate, turn on **Force SSL** and
  **HTTP/2**.
- **Advanced** tab, custom Nginx configuration:

  ```nginx
  client_max_body_size 5m;
  proxy_buffering off;
  proxy_read_timeout 300s;
  ```

NPM passes the original `Host` header by default; leave it that way.

A `502 Bad Gateway` from NPM means it can't reach the gateway. Check that
NPM is on the same Docker network (the exact name, it's case-sensitive) and
was redeployed after you added it, and that the forward hostname is right.

## Checking it

```bash
curl -s https://mtg.example.com/healthz
# {"status":"ok","version":"..."}

curl -s https://mtg.example.com/.well-known/oauth-authorization-server | head -c 200
# JSON whose "issuer" is exactly your MTG_PUBLIC_URL

curl -s -o /dev/null -w '%{http_code}\n' https://mtg.example.com/mcp
# 401 (no token yet, which is right)
```

A `421 Invalid Host header` from `/mcp` means the proxy changed the `Host`
header on the way through. More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
