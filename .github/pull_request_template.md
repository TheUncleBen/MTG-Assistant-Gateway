## What changes

Before:

After:

## How

<!-- A short paragraph on the approach, if it isn't obvious from the diff. -->

## How I checked it

- [ ] `ruff check src tests scripts` and `ruff format --check src tests scripts`
- [ ] `pytest -q`
- [ ] `VERSION` raised (MAJOR / MINOR / PATCH) with a matching `CHANGELOG.md` section ([docs/VERSIONS.md](../docs/VERSIONS.md))
- [ ] Docs updated if a user or operator would notice
- [ ] No secrets, personal details or site-specific values in code, tests, fixtures, screenshots or this description
