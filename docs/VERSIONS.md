# Versions: following latest or staying on one

Every update to `main` is a new version of the gateway. Each one gets:

| What | Name | Use it to |
|---|---|---|
| Container image | `ghcr.io/<owner>/mtg-assistant-gateway:1.2.3` | run exactly that version |
| Git tag | `v1.2.3` | see or check out the code of that version |
| Branch | `release/1.2.3` | the same code as the tag, kept as a read-only branch |
| GitHub release | **Releases → v1.2.3** | read what changed, download the Android app when it was built |

`main` always holds the newest version, and two moving image tags follow it:
`latest` (the newest version) and `1.2` (the newest `1.2.x`).

## Following latest

Set `MTG_TAG=latest` (the default in `deploy/stack.env.example` and the Compose
`.env.example`). To update, redeploy with the image pulled again:

- Portainer: **Stacks** → your stack → **Update the stack** with **Re-pull image**
  on.
- Compose: `docker compose pull && docker compose up -d`.

Read the [CHANGELOG](../CHANGELOG.md) entry first. A major version (see below)
can need a change on your side.

## Staying on one version

Set `MTG_TAG` to a version, for example `MTG_TAG=0.6.6`, and redeploy. You stay
on it until you change the variable. To go back to an older version, set its
number, but read [OPERATIONS.md](OPERATIONS.md#restart-or-update) first: a newer
version may have upgraded the database, and an older image refuses to open it.
Restore the backup taken before the upgrade instead.

The code of any version stays on GitHub as the tag `v1.2.3` and the branch
`release/1.2.3`, for example to build it yourself:
`git clone --branch release/1.2.3 https://github.com/TheUncleBen/MTG-Assistant-Gateway`.

## What the numbers mean

Versions are `MAJOR.MINOR.PATCH`:

- **MAJOR** (`1.0.0` → `2.0.0`): a big update. It may need you to change
  something when you upgrade; the CHANGELOG says what.
- **MINOR** (`1.0.0` → `1.1.0`): a mid-sized release, new features for the same
  major version, no action needed.
- **PATCH** (`1.1.0` → `1.1.1`): a small fix or tweak.

Versions below `1.0.0` (`0.x.y`) are still in testing: things can change
between them, and the CHANGELOG says what. `1.0.0` will be the first version
declared ready; until then a MINOR step (`0.5.0` → `0.6.0`) is the mid-sized
release and a PATCH step the small fix.

Mystic Forge, the research service, has its own image tag
(`<upstream version>-mag<n>`, for example `1.3.2-mag2`) that changes only when
its build changes. The stack file names the right one. The first `main` commit
that carries a new Mystic Forge tag publishes it and marks it with the git tag
`mystic-forge-1.3.2-mag2`, so later commits never rebuild or overwrite it. The
same build also moves the image's `latest` tag and adds a tag carrying the
short commit SHA; deploy the versioned tag the stack file names.

## For contributors: making a release

There is no separate release step. Every pull request:

1. raises `VERSION` (one line, for example `1.1.0`), picking MAJOR, MINOR or
   PATCH as above, and changes the version in `pyproject.toml`,
   `src/mtg_gateway/__init__.py`, `.claude-plugin/marketplace.json` and the plugin manifests to match (a unit
   test checks they agree; the Android app reads `VERSION` on its own);
2. adds a `## [1.1.0] - YYYY-MM-DD` section to `CHANGELOG.md`.

CI refuses a pull request whose `VERSION` isn't above every released version or
has no CHANGELOG section. When the pull request is merged, CI on `main` tests it,
creates the tag `v1.1.0` (which claims the number), publishes the image as
`1.1.0`, moves `1.1` and `latest` to it, then creates the branch
`release/1.1.0` and the GitHub release. Never push to `release/*` branches or
move `v*` tags; the repository's rulesets block it.

- Two pull requests that both picked `1.1.0` can both pass their checks. The
  first one merged gets the number; the second one's release stops with
  "v1.1.0 belongs to another commit" and publishes nothing. Raise `VERSION` in a
  new pull request; that one releases the code of both.
- A release that stopped partway (a network error, a runner lost) is finished
  with **Re-run failed jobs** on its run in the Actions tab.

## For forks: locking the version branches and tags

The release job creates `release/*` branches and `v*` tags but can't lock them;
that needs two rulesets, set up once by the repository's owner
(**Settings → Rules → Rulesets → New ruleset**):

1. **New branch ruleset**: name `Locked versions`, enforcement **Active**,
   target branches **Add target → Include by pattern** `release/**`, rules
   **Restrict updates**, **Restrict deletions** and **Block force pushes**
   (leave **Restrict creations** off so the release job can create them).
   **Create**.
2. **New tag ruleset**: name `Locked version tags`, enforcement **Active**,
   target tags **Include by pattern** `v*` and again **Include by pattern**
   `mystic-forge-*`, the same three rules. **Create**.
