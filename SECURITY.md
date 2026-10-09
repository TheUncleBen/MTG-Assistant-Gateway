# Security policy

The gateway holds people's sign-ins and, when they link it, an encrypted
session for their Archidekt account. I take reports about it seriously.

## Reporting a vulnerability

Please **don't open a public issue** for a security problem.

Report it privately through GitHub instead: open the repository's
**Security** tab and click **Report a vulnerability**. Only the maintainers
can see what you send.

Helpful things to include:

- what an attacker can do, and what they need first (a member account, a
  registered MCP client, network access to the server, nothing at all);
- the version or commit you tested (the System card on the admin page shows the version);
- steps or a short script that shows it, against your own test deployment;
- anything about your setup that matters (identity provider, reverse proxy,
  non-default settings).

What to expect:

- an acknowledgement within a week (this is a hobby project run in spare
  time, so sometimes sooner, rarely later);
- a fix in a new release, with the report credited in the release notes
  unless you'd rather not be named;
- a heads-up before anything is published about it.

## Please don't

- test against anybody else's gateway, or against Archidekt, Scryfall or
  other upstream services beyond normal use;
- touch other people's Archidekt decks or accounts;
- run denial-of-service tests against anything you don't own.

Run your own copy instead. [docs/DEPLOY-COMPOSE.md](docs/DEPLOY-COMPOSE.md)
gets one going on a single machine.

## Supported versions

Only the newest version (`latest`) gets security fixes; a fix ships as a new
version, not as a change to an old one. With `MTG_TAG=latest`, upgrading is a
redeploy with the image pulled again; with a pinned version, change the tag first
([docs/VERSIONS.md](docs/VERSIONS.md)).

## What's in scope

- the gateway itself: its OAuth server (`/authorize`, `/token`, `/register`,
  `/revoke`, consent), the `/mcp` endpoint and its tools, the browser pages
  (`/account`, `/proposals`, `/scan`, `/install`), and how it stores tokens
  and Archidekt sessions;
- the container images, stack and compose files, and example proxy configs
  in this repository;
- the assistant plugins and skill in `plugin/`.

Out of scope: weaknesses in your identity provider, reverse proxy or Docker
host themselves, and issues in upstream Mystic Forge that the gateway's
allowlist doesn't expose (report those
[upstream](https://github.com/Kautiontape/mystic-forge)).

The design and its trust boundaries are described in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#security-model).
