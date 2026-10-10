"""The public install page and the self-served assistant plugin.

The gateway hosts its own plugin marketplace so nobody needs access to the
source repository to connect:

- ``/install`` (HTML) and ``/install.md`` (plain Markdown for an AI agent that
  was handed the link) describe the steps for every client.
- ``/plugin/marketplace.json`` is a Claude Code marketplace with one plugin
  whose source is the ZIP archive below, pinned by SHA-256.
- ``/plugin/<name>.zip`` is the plugin from the repository's ``plugin/``
  folder with this gateway's own address written into its connector files.
  ``<name>`` is ``mtg-gateway`` unless the owner set ``MTG_SERVER_NAME``: then the
  plugin, its connector and skills take a name made from it, and the archive drops
  the project's name and homepage, so the public files don't point at the project.

None of this is secret: the plugin holds the public MCP URL and the end-user
skill text, so these routes need no sign-in. ``/install`` is the exception: a browser
signs in first, while an AI agent fetching it gets the same Markdown as ``/install.md``.
The others never read the database and
are built once from files shipped in the image (``/usr/share/mtg-gateway/plugin``,
or ``MTG_PLUGIN_DIR``).
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from . import __version__
from .pages import _csrf, browser_session, login_redirect
from .theme import display_name, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

PLUGIN_NAME = "mtg-gateway"
MARKETPLACE_NAME = "mtg-gateway"
# The setup skill's name, so its command is /<plugin>:setup-mtg-gateway whatever the plugin is called.
SETUP_SKILL = "setup-mtg-gateway"
# The product name the plugin's files carry; an owner's MTG_SERVER_NAME replaces it.
DEFAULT_SITE = "MTG Assistant Gateway"
SLUG_MAX = 40
IMAGE_PLUGIN_DIR = Path("/usr/share/mtg-gateway/plugin")
REPO_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugin"
MAX_FILE_BYTES = 512 * 1024
# The install steps are Markdown, served as plain text: some assistants' web readers refuse a
# text/markdown answer, and every reader takes text/plain.
INSTALL_MD_TYPE = "text/plain; charset=utf-8"
ZIP_DATE = (2026, 1, 1, 0, 0, 0)
# The connector files whose ``url`` the gateway fills in with its own address.
CONNECTOR_FILES = (".mcp.json", "mcp.json")


def plugin_dir() -> Path | None:
    """The folder holding ``mtg-gateway/.claude-plugin/plugin.json``."""
    override = os.environ.get("MTG_PLUGIN_DIR", "").strip()
    candidates = [Path(override)] if override else [IMAGE_PLUGIN_DIR, REPO_PLUGIN_DIR]
    for d in candidates:
        if (d / PLUGIN_NAME / ".claude-plugin" / "plugin.json").is_file():
            return d
    return None


def plugin_slug(site: str) -> str:
    """The plugin's short name: ``mtg-gateway`` under the default name, otherwise the owner's
    name in lower case with every run of other characters turned into one hyphen."""
    if site == DEFAULT_SITE:
        return PLUGIN_NAME
    slug = re.sub(r"[^a-z0-9]+", "-", site.lower()).strip("-")[:SLUG_MAX].strip("-")
    return slug or PLUGIN_NAME


def plugin_label(site: str) -> str:
    """The owner's name as the plugin's text may carry it: letters, digits, spaces and . _ ' -
    only, so it can't break the skills' front matter or the JSON it is written into."""
    label = " ".join(re.sub(r"[^A-Za-z0-9 ._'-]+", " ", site).split())
    return label or plugin_slug(site)


def _renamed(data: bytes, rel: str, site: str, name: str) -> bytes:
    """A text file of the plugin with the product's name and short name replaced by the
    owner's, and no link to the project's homepage or its author."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    label = plugin_label(site)
    if rel.endswith(".json"):
        doc = json.loads(text)
        if isinstance(doc, dict):
            doc.pop("homepage", None)
            doc.pop("repository", None)
            if "author" in doc:
                doc["author"] = {"name": DEFAULT_SITE}  # becomes the owner's name below
        text = json.dumps(doc, indent=2) + "\n"
    # The setup skill keeps its name under every owner's name: people type it.
    text = text.replace(SETUP_SKILL, "\0setup\0").replace(DEFAULT_SITE, label).replace(PLUGIN_NAME, name)
    return text.replace("\0setup\0", SETUP_SKILL).encode("utf-8")


def _with_url(raw: bytes, mcp_url: str) -> bytes:
    """Return the connector file with every server's ``url`` set to this gateway."""
    doc = json.loads(raw.decode("utf-8"))
    for server in doc.get("mcpServers", {}).values():
        server["url"] = mcp_url
    return (json.dumps(doc, indent=2) + "\n").encode("utf-8")


def build_zip(base: Path, mcp_url: str, site: str = DEFAULT_SITE) -> bytes:
    """ZIP of ``base/mtg-gateway`` with the folder at the root and the MCP URL filled in.
    Under an owner's own ``site`` name, folders and text are renamed (``_renamed``).

    Only regular files are included; symlinks and oversized files are skipped. Hidden
    folders other than ``.claude-plugin`` are left out (there are none in the repository,
    but a stray ``.git`` must never ship).
    """
    root = base / PLUGIN_NAME
    name = plugin_slug(site)
    custom = site != DEFAULT_SITE
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            rel_parts = path.relative_to(root).parts
            if any(p.startswith(".") and p != ".claude-plugin" for p in rel_parts[:-1]):
                continue
            data = path.read_bytes()
            rel = path.relative_to(base).as_posix()
            if custom:
                data = _renamed(data, rel, site, name)
                rel = "/".join(name if part == PLUGIN_NAME else part for part in rel.split("/"))
            if len(rel_parts) == 1 and rel_parts[0] in CONNECTOR_FILES:
                data = _with_url(data, mcp_url)
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def marketplace(public_url: str, owner: str, zip_sha256: str) -> dict:
    """The ``marketplace.json`` document pointing at the served archive."""
    name = plugin_slug(owner)
    return {
        "name": MARKETPLACE_NAME if name == PLUGIN_NAME else name,
        "owner": {"name": owner, "url": public_url},
        "metadata": {
            "description": f"The assistant plugin for {owner}: the gateway connector plus the MTG skill.",
            "version": __version__,
        },
        "plugins": [
            {
                "name": name,
                "description": "Magic: The Gathering research, goldfish simulation and safe Archidekt "
                f"deck edits through this gateway. Run /{name}:{SETUP_SKILL} after installing.",
                "version": __version__,
                "category": "productivity",
                "keywords": ["mtg", "magic-the-gathering", "archidekt", "mcp"],
                "source": {
                    "source": "archive",
                    # Content-addressed so a cached marketplace never pairs with a newer archive.
                    "url": f"{public_url}/plugin/{name}.zip?v={zip_sha256}",
                    "sha256": zip_sha256,
                },
            }
        ],
    }


def claude_connect_link(public_url: str, name: str) -> str:
    """claude.ai's documented install link that pre-fills the Add custom connector dialog."""
    return (
        "https://claude.ai/customize/connectors?modal=add-custom-connector"
        f"&connectorName={quote(name, safe='')}&connectorUrl={quote(public_url + '/mcp', safe='')}"
    )


# Each client the install page can be narrowed to with ``?for=``, and its heading.
CLIENTS: dict[str, str] = {
    "claude": "Claude: web, desktop, iPhone and Android",
    "claude-code": "Claude Code (terminal)",
    "chatgpt": "ChatGPT",
    "codex": "Codex CLI",
}


def pick_client(value: str | None) -> str | None:
    """The client named by a ``?for=`` value, or None when it names none we know."""
    key = (value or "").strip().lower()
    return key if key in CLIENTS else None


AGENT_RULES = """## If you are an AI agent doing this install

- Follow only the section for the app you are running in, and skip the rest.
- If a step cannot be done here (the menu is missing, the plan does not offer
  it, or it is a phone app that does not support custom connectors), stop. Tell
  the person once, in one or two sentences, what does not work on this platform
  and which path does, then wait. Do not retry it, and do not try another
  section's steps instead.
- Never ask for or accept a password, token or client secret in the chat.
  Sign-in always happens in the person's browser.
- Test once by calling `whoami`. If the app itself refuses or hides a tool
  (not an error from the gateway), do not call that tool again in this chat;
  use the fallback described under "If a tool is blocked".
"""

BLOCKED_TOOLS = """## If a tool is blocked

Some apps or plans allow only tools that read. Everything except four tools is
marked read-only, so research, goldfish simulation, reading decks, scanning
and making deck proposals keep working. The four write tools have browser
fallbacks:

- `apply_proposal`: open the proposal's review link (`review_url`, or
  `{public_url}/proposals`) and press Apply there. Same preview, same checks.
- `reject_proposal`: press "Reject this proposal" on the same review page.
- `run_deck_report`: press "Run deck report" on the deck's page under
  `{public_url}/decks`.
- `save_scan_session`: scan at `{public_url}/scan` in the browser instead, or
  keep the resolved list in the chat.
"""


def _section_md(client: str, public_url: str, site: str) -> str:
    name = plugin_slug(site)
    mp = f"{public_url}/plugin/marketplace.json"
    mcp = f"{public_url}/mcp"
    if client == "claude-code":
        return f"""## Claude Code (terminal)

Works on Windows, macOS and Linux. Run these two commands, then tell the
person to run `/mcp`, choose **{name}** and **Authenticate**, which
opens the sign-in page in their browser. Then run the `/{name}:{SETUP_SKILL}`
skill to finish.

```
claude plugin marketplace add {mp}
claude plugin install {name}@{name}
```

The plugin archive is pinned by SHA-256 in the marketplace file, so Claude
Code refuses a download that does not match.
"""
    if client == "claude":
        link = claude_connect_link(public_url, site)
        return f"""## Claude (claude.ai, desktop app, iPhone and Android)

Works on every Claude plan (the Free plan allows one custom connector), and
all tools work, including deck edits. Set it up on claude.ai or in the desktop
app; the phone apps cannot add a custom connector, but use it as soon as it is
added. If you are on a phone, ask the person to do this on a computer or in
the phone's web browser at claude.ai.

Pick one. Both end with a sign-in in your browser; nothing is typed into Claude.

1. **Plugin upload (connector and skill in one file):** download
   `{public_url}/plugin/{name}.zip`, then on claude.ai or in the desktop
   app open **Customize → Plugins → Add → Upload plugin** and choose the ZIP.
   Open the plugin's **Connectors** tab, add the {site} connector and
   press **Connect**. Under OAuth client choose **Use Claude's published
   identity** when asked.
2. **Connector only:** open [this link]({link}), which pre-fills the Add
   custom connector dialog with the name and URL above. Confirm, choose
   **Use Claude's published identity**, and sign in. The skill is optional;
   it is at `{public_url}/skill` after you sign in.

With the published identity, the gateway first shows a **Connect an
application** page. Check that it names **Claude** and says you go back to
`claude.ai` after sign-in, then press **Approve and sign in**; otherwise press
**Deny**. If that option fails, remove the connector and add it again with
**Register automatically**, which also works, and tell the owner.
"""
    if client == "chatgpt":
        return f"""## ChatGPT

What OpenAI's pages say (not yet tested with this gateway):

- **chatgpt.com on a computer, Plus, Pro, Business, Enterprise or Edu:** yes,
  in Developer mode. Reading tools work; write tools may be blocked on Plus
  and Pro (reported).
- **Free plan:** Developer mode is not offered. Use Claude instead.
- **iPhone and Android apps:** not supported ("web only"). Use Claude's phone
  app, or chatgpt.com in the phone's browser (untested).
- **Desktop app:** not documented. Set it up on chatgpt.com.

If the person is in a ChatGPT phone app or on the Free plan, do not attempt
the steps below: say so once and offer Claude (`{public_url}/install?for=claude`)
or chatgpt.com on a computer.

On chatgpt.com: **Settings → Security and login → Developer mode** on, then
**Plugins** in the sidebar (older menus: Apps) and **+** (or Create). Paste
`{mcp}`, choose **OAuth**, leave client ID and secret empty, and **Connect**.
If Developer mode is not in Settings, the plan does not offer it: stop there
and offer Claude (`{public_url}/install?for=claude`).
Copy the project instructions from `{public_url}/skill` into a ChatGPT
project and chat inside it.

If ChatGPT will not run `apply_proposal`, deck edits still work: see "If a
tool is blocked" below.
"""
    if client == "codex":
        return f"""## Codex CLI

As documented by OpenAI:

```
codex mcp add {name} --url {mcp}
codex mcp login {name}
```
"""
    raise ValueError(client)


def install_markdown(public_url: str, site: str, client: str | None = None) -> str:
    """The install steps as Markdown, for a person or an AI agent given the link.

    With ``client`` set, only that client's steps are included.
    """
    mcp = f"{public_url}/mcp"
    clients = [client] if client else list(CLIENTS)
    pick = (
        ""
        if client
        else "Only one section applies to you. For just your app's steps, add `?for=` and one of "
        + ", ".join(f"`{c}`" for c in CLIENTS)
        + f", for example `{public_url}/install.md?for=chatgpt`.\n\n"
    )
    sections = "\n".join(_section_md(c, public_url, site) for c in clients)
    blocked = BLOCKED_TOOLS.format(public_url=public_url)
    return f"""# Install the {site} assistant plugin

This gateway is a remote MCP server for Magic: The Gathering. One plugin adds
the connector and the MTG skill to your assistant. You need an account on the
owner's sign-in service; the owner gives you one. You never need a client ID,
API key or token, and you must never paste a password into a chat.

Connector URL: `{mcp}`

{pick}{AGENT_RULES}
{sections}
## After connecting

Ask your assistant to call `whoami`; it should answer with your name. To let
it read and edit your own Archidekt decks, sign in at `{public_url}/account` and link
Archidekt once there. Deck changes are always shown to you first and applied only after you approve.

{blocked}"""


def add_plugin_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    cache: dict[str, tuple[bytes, str]] = {}

    def archive() -> tuple[bytes, str] | None:
        base = plugin_dir()
        if base is None:
            return None
        key = f"{base}|{s.mcp_url}|{s.server_name}"
        hit = cache.get(key)
        if hit is None:
            data = build_zip(base, s.mcp_url, s.server_name)
            hit = cache[key] = (data, hashlib.sha256(data).hexdigest())
        return hit

    static = {"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}

    @server.custom_route("/plugin/marketplace.json", methods=["GET"], include_in_schema=False)
    async def marketplace_json(_request: Request) -> Response:
        hit = archive()
        if hit is None:
            return JSONResponse({"error": "plugin not installed on this gateway"}, status_code=503)
        # Revalidate every time: the digest inside must match the archive served now.
        return JSONResponse(
            marketplace(s.public_url, s.server_name, hit[1]),
            headers={**static, "Cache-Control": "no-cache"},
        )

    zip_name = f"{plugin_slug(s.server_name)}.zip"

    @server.custom_route(f"/plugin/{zip_name}", methods=["GET"], include_in_schema=False)
    async def plugin_zip(_request: Request) -> Response:
        hit = archive()
        if hit is None:
            return PlainTextResponse("plugin not installed on this gateway", status_code=503)
        return Response(
            hit[0],
            media_type="application/zip",
            headers={
                **static,
                "Content-Disposition": f'attachment; filename="{zip_name}"',
                "ETag": f'"{hit[1]}"',
            },
        )

    @server.custom_route("/install.md", methods=["GET"], include_in_schema=False)
    async def install_md(request: Request) -> Response:
        client = pick_client(request.query_params.get("for"))
        return PlainTextResponse(
            install_markdown(s.public_url, s.server_name, client),
            media_type=INSTALL_MD_TYPE,
            headers=static,
        )

    @server.custom_route("/install", methods=["GET"], include_in_schema=False)
    async def install(request: Request) -> Response:
        client = pick_client(request.query_params.get("for"))
        _sub, sid = browser_session(state, request)
        if _sub is None:
            # A person's browser signs in first. An AI agent handed this address ("install this
            # plugin") gets the same public steps as /install.md: browsers mark page loads with
            # Sec-Fetch-Mode: navigate, HTTP clients and agent fetchers don't.
            if request.headers.get("sec-fetch-mode") == "navigate":
                target = "/install" + (f"?for={client}" if client else "")
                return login_redirect(target)
            return PlainTextResponse(
                install_markdown(s.public_url, s.server_name, client),
                media_type=INSTALL_MD_TYPE,
                headers={"Cache-Control": "no-store", "Vary": "Sec-Fetch-Mode, Cookie"},
            )
        e = html.escape
        md_url = f"{s.public_url}/install.md" + (f"?for={client}" if client else "")
        intro = (
            "<div class='card'><p>One plugin adds this gateway and the MTG skill to your assistant. "
            "You need the account the owner gave you. You never need a client ID, key or token, and "
            "you never type a password into a chat.</p>"
            f"<p>Connector URL</p><pre>{e(s.mcp_url)}</pre>"
            "<p class='muted small'>Giving an AI agent this page's address and saying \"install this "
            f'plugin" is enough: the plain-text version is at <code>{e(md_url)}</code>.'
            "</p></div>"
        )
        picker = "".join(
            f"<a class='btn{' btn-primary' if key == client else ''}' href='/install?for={key}'>"
            f"{e(label)}</a>"
            for key, label in CLIENTS.items()
        )
        if client is None:
            body = (
                intro + "<div class='card'><h2>Which app will you use?</h2>"
                "<p>Pick one to see only the steps that work there.</p>"
                f"<div class='actions'>{picker}</div>"
                "<p class='muted small'>On a phone? Claude's iPhone and Android apps work once the "
                "connector is added on the web or desktop. ChatGPT's phone apps do not support custom "
                "connectors, according to OpenAI.</p></div>"
            )
        else:
            body = (
                intro
                + _section_html(client, s.public_url, s.server_name, s.mcp_url)
                + "<div class='card'><h2>After connecting</h2>"
                "<p>Ask your assistant to call <code>whoami</code>. To let it read and edit your own "
                "Archidekt decks, <a href='/account'>link Archidekt</a> once. Every deck change is shown "
                "to you first and applied only after you approve.</p></div>"
                f"<div class='card'><p class='muted small'>Other apps:</p><div class='actions'>{picker}"
                "</div></div>"
            )
        # Signed in, so the page gets the member's navigation (tab bar, rail, account menu).
        user = state.db.get_user(_sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        resp = render(
            "Install the assistant plugin",
            body,
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            user=display_name(user),
            current="/install",
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp


def _section_html(client: str, public_url: str, site: str, mcp_url: str) -> str:
    e = html.escape
    name = plugin_slug(site)
    if client == "claude-code":
        mp = f"{public_url}/plugin/marketplace.json"
        return (
            "<div class='card'><h2>Claude Code</h2>"
            "<p>Two commands, then <code>/mcp</code> to sign in in your browser and "
            f"<code>/{e(name)}:{SETUP_SKILL}</code> to finish.</p>"
            f"<pre>claude plugin marketplace add {e(mp)}\nclaude plugin install {e(name)}@{e(name)}</pre>"
            "</div>"
        )
    if client == "claude":
        link = claude_connect_link(public_url, site)
        zip_url = f"{public_url}/plugin/{name}.zip"
        return (
            "<div class='card'><h2>Claude: web, desktop, iPhone and Android</h2>"
            "<p>Every tool works here, including deck edits. Add it on claude.ai or in the desktop app; "
            "the phone apps pick it up automatically. On a phone, open claude.ai in the browser to add "
            "it.</p>"
            "<p><strong>Plugin upload</strong> (connector and skill together): download the plugin, then "
            "on claude.ai or in the desktop app open Customize, Plugins, Add, Upload plugin and choose "
            "the file. Open the plugin's Connectors tab, add the connector and press Connect. Choose "
            "Use Claude's published identity if asked, then sign in.</p>"
            f"<div class='actions'><a class='btn btn-primary' href='{e(zip_url)}'>Download the plugin (ZIP)"
            "</a></div>"
            "<p><strong>Connector only:</strong> the button below opens claude.ai with the name and URL "
            "filled in. Confirm, choose Use Claude's published identity, and sign in. The skill is "
            "optional and lives on the <a href='/skill'>skill page</a>.</p>"
            f"<div class='actions'><a class='btn' href='{e(link)}'>Connect to Claude</a></div>"
            "<p class='muted small'>With the published identity, the gateway first shows a Connect an "
            "application page. Check that it names Claude and says you go back to claude.ai after "
            "sign-in, then press Approve and sign in; otherwise press Deny. If that option fails, remove "
            "the connector and add it again with Register automatically, which also works, and tell the "
            "owner.</p></div>"
        )
    if client == "chatgpt":
        return (
            "<div class='card'><h2>ChatGPT</h2>"
            "<p><strong>Works on:</strong> chatgpt.com on a computer, on Plus, Pro, Business, Enterprise "
            "or Edu, in Developer mode (OpenAI's pages; not yet tested with this gateway).</p>"
            "<p><strong>Does not work on:</strong> the ChatGPT iPhone and Android apps, or the Free plan. "
            "Use <a href='/install?for=claude'>Claude</a> there instead, whose phone apps work.</p>"
            "<p>On chatgpt.com: Settings, Security and login, turn on Developer mode. If it is not there, "
            "your plan does not offer it. Then open Plugins in the sidebar (older menus: Apps) and press + "
            "(or Create), paste the connector URL, choose OAuth, leave client ID and secret empty, and "
            "press Connect. Copy the project instructions from the <a href='/skill'>skill page</a> into a "
            "ChatGPT project.</p>"
            "<p><strong>If ChatGPT blocks write tools</strong> (OpenAI's pages "
            "report this may happen on Plus and Pro): research, simulation, reading decks, scanning "
            "and deck proposals still work. To apply "
            "a proposal, open its review link or your <a href='/proposals'>proposals</a> and press Apply. "
            "To save scans, use the <a href='/scan'>scan page</a>.</p></div>"
        )
    if client == "codex":
        return (
            "<div class='card'><h2>Codex CLI</h2>"
            f"<pre>codex mcp add {e(name)} --url {e(mcp_url)}\ncodex mcp login {e(name)}</pre></div>"
        )
    raise ValueError(client)


__all__ = [
    "CLIENTS",
    "add_plugin_routes",
    "build_zip",
    "claude_connect_link",
    "install_markdown",
    "marketplace",
    "pick_client",
    "plugin_dir",
    "plugin_label",
    "plugin_slug",
]
