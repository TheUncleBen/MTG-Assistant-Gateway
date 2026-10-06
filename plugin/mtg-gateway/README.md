# MTG Assistant Gateway plugin

One plugin, two files that matter: the connector (`.mcp.json` for Claude
Code, `mcp.json` for Codex and ChatGPT) and the skills under `skills/`.

- `skills/mtg-gateway/` is the end-user MTG skill, and
  `chatgpt-instructions.md` the same rules as text for a ChatGPT project.
  The gateway's `/skill` page serves both from here; this is their only copy.
- `skills/setup/` walks a person through connecting: sign in, check
  `whoami`, link Archidekt in the browser. It never asks for a secret.

The copy in this repository has no gateway address: the connector URL is
`${MTG_GATEWAY_URL:-}` until you set that variable (unset, Claude Code
reports the server as failed with "invalid MCP url"). The running gateway serves a copy
with its own address filled in at `https://<gateway>/plugin/marketplace.json`;
install from there and nothing needs setting. See `docs/PLUGIN.md`.
