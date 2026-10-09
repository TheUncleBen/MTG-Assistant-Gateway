# Contributing

Thanks for wanting to help. Bug reports, fixes, docs improvements and
deployment recipes for setups I don't run are all welcome.

## Before you start

- **Bugs and small fixes:** open a pull request, or an issue first if you're
  not sure it's a bug.
- **Bigger changes** (new tools, new pages, changes to sign-in or how deck
  edits are applied): open an issue describing what you want to do before
  writing a lot of code, so we can agree on the shape.
- **Security problems:** don't open an issue. See [SECURITY.md](SECURITY.md).

## Setting up

You need Python 3.11 or newer (CI uses 3.12) and, for the browser tests,
Chromium through Playwright.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -c constraints.txt -e ".[dev]"
python scripts/fetch_ocr_assets.py       # the /scan page's OCR files (not committed)
python -m playwright install --with-deps chromium  # for the browser tests
```

Run the gateway locally against your own identity provider with the
variables in [docs/DEPLOY.md](docs/DEPLOY.md#environment-reference), or just
run the tests: they bring their own fake identity provider, Archidekt and
Scryfall.

## Checks

Run these before you push. CI runs the same ones.

```bash
ruff check src tests scripts
ruff format --check src tests scripts
pytest -q
git fetch --tags && python3 scripts/release_version.py check   # VERSION above every release
```

Some tests skip instead of failing when something is missing, so check the
skip count: the OCR test needs `scripts/fetch_ocr_assets.py` to have run, the
browser tests need a Chromium (Playwright's own, or one found under
`/opt/pw-browsers/` or `/usr/bin/chromium*`), and one scan test needs Node.js.
Install with `-c constraints.txt` as above: it pins packages some tests and
scripts import directly.

Opt-in extras: `SCAN_LIVE=1` runs one scan test against the real Scryfall API;
`MTG_CARD_SHOTS=<dir>` saves card screenshots; `SWEEP_OUT=<file>` writes the
page-overflow sweep's findings to a file.

`tests/e2e/` deploys the real stack on a single-node Swarm with a real
Authentik and drives it with a browser. It needs Docker and takes a while;
see [tests/e2e/README.md](tests/e2e/README.md). CI runs it on pull requests
that touch the server, sign-in code, stack or Docker files (not on drafts).
Run it with `tests/e2e/run.sh up`, `tests/e2e/run.sh test`, `tests/e2e/run.sh down`;
plain `pytest tests/e2e` collects nothing.

What CI runs on a pull request, on a merge to `main` and by hand is described
at the top of each file in `.github/workflows/`. Pull requests always run lint
and unit tests; non-draft ones also build and smoke-test the image (unless only
docs or `android/` changed), run the end-to-end suite when relevant, and build
the Android app when `android/` changed. The live research smoke test
(`live-research-smoke.yml`) runs only by hand, against real public services,
and needs a public Archidekt deck id (its `deck_id` input or the
`LIVE_SMOKE_DECK_ID` repository variable); a fork must change the image names
in it.

Never point tests at real Archidekt accounts or decks you don't own.
`tests/live/` has opt-in live checks; read the safety rules at the top of
each script first.

## What goes in the repository

The repository holds code, tests, Docker and stack files, CI and public
docs. Please keep it that way:

- **No secrets, ever.** Not in code, tests, fixtures, screenshots, commit
  messages or pull request text. Secrets belong in Docker secrets.
- **No personal details.** Use `example.com` hosts, `swarm-node-1` style
  node names and placeholder usernames. Recorded API responses get their
  owner fields replaced (see
  [tests/fixtures/live/README.md](tests/fixtures/live/README.md)).
- **Nothing site-specific in code.** Anything that differs between
  deployments is an environment variable or a Docker secret, with a sensible
  default, and is documented in the environment reference.

## Style

- Python: `ruff` decides formatting (line length 110). Match the
  surrounding code's naming and comment density.
- Docs: plain English, short sentences, steps in the order people do them.
  Say what you tested and how; don't describe untested behaviour as working.
- Commits and pull requests: a title that says what changes for a user or
  operator, and a description with what it was like before, what it's like
  after, and how you checked it. The pull request template has the outline.
- Every pull request is a release once merged, so raise `VERSION` (MAJOR for a
  big update, MINOR for a mid-sized one, PATCH for a small fix), change the
  other version strings to match (`tests/test_version.py` lists them) and add a
  `## [x.y.z]` section to [CHANGELOG.md](CHANGELOG.md) saying what a user or
  operator would notice. CI checks both. See [docs/VERSIONS.md](docs/VERSIONS.md).

## Mystic Forge

The research tools come from upstream
[Mystic Forge](https://github.com/Kautiontape/mystic-forge), built from a
pinned commit with small patches in `docker/mystic-forge/patches/`. Fixes
that belong upstream should go upstream first; patches here are for
things the gateway needs before upstream has them.

## Licence of contributions

By contributing you agree that your contribution is licensed under the
same licence as the project ([LICENSE](LICENSE)).

Everyone taking part is expected to follow the
[code of conduct](CODE_OF_CONDUCT.md).
