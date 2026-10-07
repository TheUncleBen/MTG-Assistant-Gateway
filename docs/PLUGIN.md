# The assistant plugin: one link to set up

The gateway packages itself as a plugin. Inside is the connector (its own
`/mcp` address), the MTG skill, and a `setup` skill that walks someone
through signing in. The gateway serves the plugin itself, so people only
need the gateway's address, never the source repository, and the plugin
always has the right URL baked in. Nothing in it is secret.

What each person types or clicks:

| App | Setup | Then |
| --- | --- | --- |
| Claude Code | Paste `https://mtg.example.com/install` and say "install this plugin", or run the two commands below | `/mcp` → Authenticate (signs in through the browser), then `/mtg-gateway:setup` |
| Claude web, desktop, iPhone, Android | Download `/plugin/mtg-gateway.zip` and go to Customize → Plugins → Add → Upload plugin; or press **Connect to Claude** on `/install` | Connect the connector, pick **Use Claude's published identity** (Register automatically works too), approve the gateway's consent page if it names Claude and returns to `claude.ai` (Deny otherwise), sign in |
| ChatGPT (web only) | Developer mode, add an app with the connector URL | Connect, sign in, paste the project instructions from `/skill` |
| Codex CLI | `codex mcp add` with the connector URL | `codex mcp login mtg-gateway` |

Use your gateway's hostname instead of `mtg.example.com`. The rest of this
page covers what's behind each row and how sure we are about it. Labels:
**verified** means read on the vendor's own docs on 2026-10-04 or
2026-10-05; **reported** means an official page says so but we could only
read it through a tool that summarises pages; **unknown** means no official
page we could read says.

## What the gateway serves

| Path | Sign-in | What it is |
| --- | --- | --- |
| `/install` | yes, in a browser | Asks which app you use, then (`?for=claude`, `claude-code`, `chatgpt` or `codex`) shows only the steps that work there, with the Connect to Claude button and the plugin download |
| `/install.md` | no | The same thing as Markdown, for an AI agent handed the link (an agent fetching `/install` itself gets this too, since only browser page loads are sent to sign in): every app unless `?for=` narrows it, plus rules not to retry a step or tool the platform doesn't support, and the browser fallbacks for blocked write tools |
| `/plugin/marketplace.json` | no | A Claude Code marketplace with one plugin, source type `archive`, pinned by SHA-256 |
| `/plugin/mtg-gateway.zip` | no | The plugin from `plugin/mtg-gateway/`, with the gateway's own `/mcp` URL written into `.mcp.json` and `mcp.json` |
| `/skill` | yes | The skill ZIP on its own, and the ChatGPT project instructions |

The archive is built once per process from the files in the image and comes
out byte-for-byte the same every time, so the digest in the marketplace file
stays valid until the image changes. The marketplace is served with
`Cache-Control: no-cache` and points at the archive by its digest
(`?v=<sha256>`), so a cached marketplace never gets paired with a newer
archive.

The archive holds the plugin manifest, the connector files, the MTG skill,
the setup skill and the ChatGPT text. It's the same text as this
repository, nothing per-user. That's why these routes don't need a sign-in:
a Claude Code user has to fetch the marketplace before they can sign in to
anything.

## Claude Code

Verified in the official Claude Code docs
(https://code.claude.com/docs/en/plugins/marketplace-reference,
https://code.claude.com/docs/en/plugins/install,
https://code.claude.com/docs/en/mcp, read 2026-10-04):

- A marketplace can be a hosted `marketplace.json` added by `https://` URL,
  and a plugin's source can be a zip `archive` over HTTPS with a `sha256`
  that Claude Code checks before installing.
- A plugin's `.mcp.json` can declare a remote `http` server. Sign-in is the
  `/mcp` command's **Authenticate**, which opens the browser (the gateway's
  usual OAuth flow with automatic client registration).
- `claude plugin install name@marketplace` works from a shell without a
  session, so an agent can do the whole install:

```
claude plugin marketplace add https://mtg.example.com/plugin/marketplace.json
claude plugin install mtg-gateway@mtg-gateway
```

The plugin's skills show up as `/mtg-gateway:mtg-gateway` (the MTG skill)
and `/mtg-gateway:setup`. The setup skill checks the connector, has the
person sign in through `/mcp`, calls `whoami`, and points them at `/account`
to link Archidekt. It never asks for a password or token.

You can also install straight from the GitHub repository:
`claude plugin marketplace add <owner>/MTG-Assistant-Gateway`, where
`<owner>/MTG-Assistant-Gateway` is the repository that holds this code. That needs
read access to the repository through your own git credentials, and gives
you the same plugin without an address: its `.mcp.json` URL is
`${MTG_GATEWAY_URL:-}`, which Claude Code fills in from the environment
(verified in its docs and checked with the CLI: with the variable set,
`claude mcp list` shows the address; unset, it reports the server as failed
with "invalid MCP url" instead of quietly skipping it).

## Claude: web, desktop app and phones

Verified on claude.com/docs and support.claude.com (read 2026-10-05:
https://claude.com/docs/plugins/overview,
https://claude.com/docs/plugins/platform-support,
https://claude.com/docs/connectors/building/directory-vs-custom and
https://support.claude.com/en/articles/13837440-use-plugins-in-claude):

- Plugins live on claude.ai and the desktop app under Customize → Plugins,
  on Pro, Max, Team and Enterprise. You can add one from the directory, from
  a Git repository marketplace (GitHub, including private repositories
  through the Claude GitHub App), or by uploading a `.zip` that holds one
  `.claude-plugin/plugin.json`. The plugins overview page says: "Add >
  Upload plugin: upload a plugin you have as a folder on your computer, as a
  .zip or .plugin file. Zip either the plugin folder itself or its contents;
  both work as long as the archive holds one .claude-plugin/plugin.json".
  The ZIP the gateway serves fits that.
- A remote MCP server with a fixed URL inside a plugin "is listed on the
  plugin's Connectors tab; works once you add or connect it there". So
  adding the plugin doesn't sign anyone in: the person opens the plugin's
  Connectors tab, adds the connector and connects. The skills load either
  way.
- A plugin added on the web or desktop is saved to the account and follows
  the person to the phone apps, Cowork and Claude Code.
- Claude also documents an install link that pre-fills the Add custom
  connector dialog:
  `https://claude.ai/customize/connectors?modal=add-custom-connector&connectorName=NAME&connectorUrl=URL`.
  The **Connect to Claude** button on `/install` is that link. The person
  reviews and confirms; nothing gets added without them.

Unknown: whether a plugin or skill can be added from the phone apps
themselves (every documented way is the web or desktop app). A connector
added there does work on the phone (verified).

Not used: Claude Desktop's `.mcpb` extensions, because they're for local
servers only (verified).

## ChatGPT and Codex

Every OpenAI page is blocked from the environment this was built in, so all
of this was read through search summaries and is **reported**, not
verified:

- ChatGPT has plugins (manifest `plugin.json`, remote servers in `mcp.json`
  with `"type": "streamable-http"`). The plugin folder carries both files,
  so the same ZIP is in the right shape. Importing a marketplace from GitHub
  into ChatGPT is documented for workspace admins (Business and
  Enterprise), and the repository's `.claude-plugin/marketplace.json` is one
  of the accepted formats. Whether a Plus or Pro user can import a plugin
  from a repository or a ZIP: unknown.
- Custom MCP apps for individuals: Developer mode, web only, OAuth with
  automatic registration. Pro accounts are documented as read and fetch
  tools only. The gateway marks every tool read-only except
  `apply_proposal`, `confirm_proposal`, `reject_proposal`, `run_deck_report`,
  `save_scan_session` and `propose_collection_changes`, so proposing and
  reviewing still work there.
- Codex CLI: `codex mcp add <name> --url <url>` and
  `codex mcp login <name>` for OAuth servers.

The `/install` page gives these steps with the same caveat. What's known about
each app are tracked in [CONNECT.md](CONNECT.md).

## For the operator

The repository's marketplace also lists `mtg-gateway-operator`, a plugin
with no server and two skills:

- `/mtg-gateway-operator:deploy` walks through [DEPLOY.md](DEPLOY.md) step
  by step, generates the random secrets with commands that pipe straight into
  `docker secret create`, and checks each step.
- `/mtg-gateway-operator:invite` adds a person in Authentik and drafts the
  message with the install link.

Install it with:

```
claude plugin marketplace add <owner>/MTG-Assistant-Gateway
claude plugin install mtg-gateway-operator@mtg-gateway
```

## One source for the skill

`plugin/mtg-gateway/skills/mtg-gateway/` is the only copy of the MTG skill,
and `plugin/mtg-gateway/chatgpt-instructions.md` the only copy of the
ChatGPT text. The `/skill` page, the plugin archive and the repository
marketplace all read them from there (`MTG_PLUGIN_DIR` in the image). So the
ChatGPT text, like the skill, is in the public plugin archive on purpose:
it's prompt text, with nothing per-user or secret in it.
