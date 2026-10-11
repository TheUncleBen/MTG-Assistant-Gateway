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
from .theme import display_name, icon, render

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
# What an app shows about the connector: MCP ``serverInfo.description`` (app.py), and the text the
# install page offers to copy into ChatGPT's description box. It names no project, so it suits an
# owner's own MTG_SERVER_NAME.
SERVER_DESCRIPTION = (
    "Magic: The Gathering deck research, goldfish testing and safe Archidekt deck edits, "
    "from a self-hosted gateway."
)
# The icon to upload when adding the connector by hand. ChatGPT takes a PNG of at most 10 KB, so
# this one is kept small (a test checks it); it is first in serverInfo.icons too. /static/ needs
# no sign-in, so it downloads on a phone as well.
CONNECTOR_ICON_PATH = "/static/gateway-icon-connector.png"
CONNECTOR_ICON_PX = 256
CONNECTOR_ICON_MAX_BYTES = 10 * 1024


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


def chatgpt_zip_name(site: str) -> str:
    """The ChatGPT plugin archive's file name (chatgpt_plugin.py builds it)."""
    return f"{plugin_slug(site)}-chatgpt.zip"


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
    """One client's steps. Keep them in step with ``_section_html``: same steps, same order."""
    name = plugin_slug(site)
    mp = f"{public_url}/plugin/marketplace.json"
    mcp = f"{public_url}/mcp"
    if client == "claude-code":
        return f"""## Claude Code (terminal)

Works on Windows, macOS and Linux.

1. In a terminal, run `claude plugin marketplace add {mp}`
2. Run `claude plugin install {name}@{name}`
3. Start Claude Code, type `/mcp`, choose **{name}** and press
   **Authenticate**. Your browser opens.
4. Sign in with the account the owner gave you.
5. Back in Claude Code, run `/{name}:{SETUP_SKILL}` to finish.

The plugin file is checked against a SHA-256 fingerprint in the marketplace
file, so Claude Code refuses a download that does not match.
"""
    if client == "claude":
        link = claude_connect_link(public_url, site)
        return f"""## Claude (claude.ai, desktop app, iPhone and Android)

Works on every Claude plan (the Free plan allows one custom connector), and
every tool works, including deck edits. Add it once on claude.ai or in the
desktop app; the iPhone and Android apps then have it too. The phone apps
cannot add it themselves: on a phone, open claude.ai in the phone's web
browser instead.

Pick one way. Nothing is typed into Claude: you sign in in your browser.

**Quickest: connector only**

1. Open [this link]({link}). It opens claude.ai with the name
   (`{site}`) and the connector URL filled in.
2. Check the name and URL, then continue.
3. Under **OAuth client**, if asked, choose **Use Claude's published
   identity**.
4. This site shows a **Connect an application** page. Check that it names
   **Claude** and says you go back to `claude.ai`, then press **Approve and
   sign in**. If anything else is shown, press **Deny**.
5. Sign in with the account the owner gave you.

The skill is optional; get it at `{public_url}/skill` after you sign in.

**Connector and skill in one file: plugin upload**

1. Download `{public_url}/plugin/{name}.zip`. Do not unzip it.
2. On claude.ai or in the desktop app, open **Customize → Plugins**.
3. Press **Add → Upload plugin** and choose the ZIP file.
4. Open the plugin's **Connectors** tab, add the {site} connector and press
   **Connect**.
5. Then steps 3 to 5 above.

**Turn it on in a chat:** press **+** by the message box, open
**Connectors** and switch **{site}** on.

If sign-in fails with the published identity, remove the connector, add it
again choosing **Register automatically**, and tell the owner.

Claude shows a plain tile for custom connectors, not their own icon
(reported by developers, not in Anthropic's documentation), so there is no
icon to set.
"""
    if client == "chatgpt":
        return f"""## ChatGPT

What OpenAI's pages say, and what was checked: on 2026-10-10 the owner
connected ChatGPT on the web (Pro plan) to this gateway, and ChatGPT used
every tool, deck proposals included. Other plans are as OpenAI's pages say.

- **Works:** chatgpt.com in a computer's web browser, on Plus, Pro,
  Business, Enterprise or Edu, with Developer mode on. Write tools may be
  blocked on Plus (reported); on Pro they worked in the owner's check.
- **Does not work:** the Free plan (no Developer mode) and the ChatGPT
  iPhone and Android apps ("web only"). Use Claude there instead
  (`{public_url}/install?for=claude`).
- **ChatGPT in a phone's browser or the desktop app:** not documented. Set
  it up on chatgpt.com on a computer.

If the person is in a ChatGPT phone app or on the Free plan, do not attempt
the steps below: say so once and offer Claude or chatgpt.com on a computer.

You will copy three things into ChatGPT:

- Name: `{site}`
- Description: `{SERVER_DESCRIPTION}`
- Connector URL: `{mcp}`

**Add the connector**

1. On a computer, open chatgpt.com and sign in.
2. Download the connector icon: `{public_url}{CONNECTOR_ICON_PATH}`
   (a PNG under 10 KB, ChatGPT's limit).
3. Open **Settings → Security and login** and turn on **Developer mode**.
   If it is not there, your plan does not offer it: stop here and use
   Claude instead.
4. In the sidebar, open **Plugins** (older menus: Apps) and press **+** (or
   Create).
5. Name: paste `{site}`.
6. Description: paste the description above.
7. MCP server URL: paste `{mcp}`.
8. Icon: choose the file from step 2. Do it now: ChatGPT reportedly lets
   you set the icon only while creating the connector (a user report, not
   OpenAI's documentation).
9. Authentication: choose **OAuth**. Leave client ID and client secret
   empty.
10. Accept the risk notice and create the connector.
11. Press **Connect**. This site shows a **Connect an application** page.
    Check that it says you go back to `chatgpt.com`, then press **Approve
    and sign in**. If anything else is shown, press **Deny**.
12. Sign in with the account the owner gave you.

**Add the instructions (recommended)**

1. In ChatGPT, create a project.
2. Copy the ChatGPT instructions from `{public_url}/skill`.
3. Open the project's menu, then **Project settings**, and paste them into
   the instructions.
4. Chat inside that project with the {site} connector on.

**ChatGPT plugin file (workspace admins only)**

[Download the ChatGPT plugin (ZIP)]({public_url}/plugin/{chatgpt_zip_name(site)}):
the connector and the MTG skill in one file. OpenAI's help pages say
workspace owners and admins on ChatGPT Business and Enterprise upload it
under Plugins in the admin console (reported, not yet tested with this
gateway). Individual Plus and Pro accounts have no plugin upload (checked
on a Pro account, 2026-10-11): add the connector as above.

If ChatGPT will not run `apply_proposal`, deck edits still work: see "If a
tool is blocked" below.
"""
    if client == "codex":
        return f"""## Codex CLI

As documented by OpenAI (not yet tested with this gateway):

1. In a terminal, run `codex mcp add {name} --url {mcp}`
2. Run `codex mcp login {name}`. Your browser opens.
3. Sign in with the account the owner gave you.
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

These steps connect your AI assistant to this gateway, a Magic: The
Gathering service. The assistant can then read your decks, research cards
and propose deck changes that you approve. You need the account the owner
gave you. You never need a client ID, API key or token, and you must
never paste a password into a chat.

Connector URL: `{mcp}`

{pick}{AGENT_RULES}
{sections}
## After connecting

1. Ask your assistant to call `whoami`. It should answer with your name.
2. To let it read and edit your own Archidekt decks, open
   `{public_url}/account` and link Archidekt once.

Deck changes are always shown to you first and applied only after you
approve.

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
            "<div class='card'><p>These steps connect your AI assistant to this site. It can then read "
            "your decks, research cards and propose deck changes that you approve. You need the account "
            "the owner gave you. You never need a client ID, key or token, and you never type a password "
            "into a chat.</p>"
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
                "connector is added on claude.ai or in the desktop app. ChatGPT's phone apps do not "
                "support custom connectors, according to OpenAI.</p>"
                + _copy_field("mcp-url", "Connector URL", s.mcp_url)
                + "</div>"
            )
        else:
            body = (
                intro
                + _section_html(client, s.public_url, s.server_name, s.mcp_url)
                + "<div class='card'><h2>After connecting</h2><ol>"
                "<li>Ask your assistant to call <code>whoami</code>. It should answer with your name.</li>"
                "<li>To let it read and edit your own Archidekt decks, <a href='/account'>link "
                "Archidekt</a> once.</li></ol>"
                "<p>Every deck change is shown to you first and applied only after you approve.</p></div>"
                f"<div class='card'><p class='muted small'>Other apps:</p><div class='actions'>{picker}"
                "</div></div>"
            )
        # Signed in, so the page gets the member's navigation (tab bar, rail, account menu).
        user = state.db.get_user(_sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        resp = render(
            "Guided setup",
            body,
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            user=display_name(user),
            current="/install",
            scripts=True,  # export.js: the Copy buttons
            head_extra=f"<style>{INSTALL_CSS}</style><script src='/static/export.js' defer></script>",
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp


# The install page's copy boxes and numbered steps (export.js runs the Copy buttons).
INSTALL_CSS = (
    ".setup>li{margin:.7rem 0}"
    ".copyfield{margin:.5rem 0}.copyfield label{margin:.2rem 0 .3rem}"
    ".copyline{display:flex;gap:.5rem;align-items:center}"
    ".copyline input,.copyline textarea{flex:1;min-width:0;margin:0;"
    "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
    "font-size:.85rem}"
    ".copyline .btn{flex:none;margin:0}"
)


def _copy_field(key: str, label: str, value: str, rows: int = 0) -> str:
    """A read-only box holding ``value`` with a Copy button (export.js), its label above it.
    ``rows`` makes it a text area, so a sentence shows whole instead of scrolling."""
    e = html.escape
    box = (
        f"<textarea id='{key}' rows='{rows}' readonly>{e(value)}</textarea>"
        if rows
        else f"<input id='{key}' type='text' readonly value='{e(value)}'>"
    )
    return (
        f"<div class='copyfield'><label for='{key}'>{e(label)}</label><div class='copyline'>{box}"
        f"<button type='button' class='btn small copybtn' data-copy='{key}' aria-describedby='{key}-status'>"
        f"{icon('copy')} <span class='label'>Copy</span></button></div>"
        f"<span class='sr-only' id='{key}-status' role='status' aria-live='polite'></span></div>"
    )


def _sign_in_steps(app: str, back_to: str) -> str:
    """The last two steps every client shares: the gateway's consent page, then the sign-in."""
    return (
        f"<li>This site shows a <strong>Connect an application</strong> page. Check that it names {app} "
        f"and says you go back to <code>{back_to}</code>, then press <strong>Approve and sign in</strong>. "
        "If anything else is shown, press <strong>Deny</strong>.</li>"
        "<li>Sign in with the account the owner gave you.</li>"
    )


def _section_html(client: str, public_url: str, site: str, mcp_url: str) -> str:
    """One client's steps. Keep them in step with ``_section_md``: same steps, same order."""
    e = html.escape
    name = plugin_slug(site)
    if client == "claude-code":
        mp = f"{public_url}/plugin/marketplace.json"
        return (
            "<div class='card'><h2>Claude Code</h2>"
            "<p>Works on Windows, macOS and Linux.</p><ol class='setup'>"
            "<li>In a terminal, run this command."
            + _copy_field("cc-add", "Command 1", f"claude plugin marketplace add {mp}")
            + "</li><li>Run this one."
            + _copy_field("cc-install", "Command 2", f"claude plugin install {name}@{name}")
            + f"</li><li>Start Claude Code, type <code>/mcp</code>, choose <strong>{e(name)}</strong> and "
            "press <strong>Authenticate</strong>. Your browser opens.</li>"
            "<li>Sign in with the account the owner gave you.</li>"
            f"<li>Back in Claude Code, run <code>/{e(name)}:{SETUP_SKILL}</code> to finish.</li></ol></div>"
        )
    if client == "claude":
        link = claude_connect_link(public_url, site)
        zip_url = f"{public_url}/plugin/{name}.zip"
        consent = _sign_in_steps("Claude", "claude.ai")
        return (
            "<div class='card'><h2>Claude: web, desktop, iPhone and Android</h2>"
            "<p>Works on every Claude plan (the Free plan allows one custom connector), and every tool "
            "works, including deck edits. Add it once on claude.ai or in the desktop app; the iPhone and "
            "Android apps then have it too. On a phone, open claude.ai in the phone's web browser to add "
            "it.</p><p>Pick one way. Nothing is typed into Claude: you sign in in your browser.</p></div>"
            "<div class='card'><h2>Quickest: connector only</h2><ol class='setup'>"
            "<li>Press the button. It opens claude.ai with the name and the connector URL filled in."
            f"<div class='actions'><a class='btn btn-primary' href='{e(link)}'>Connect to Claude</a></div>"
            "</li><li>Check the name and URL, then continue.</li>"
            "<li>Under <strong>OAuth client</strong>, if asked, choose <strong>Use Claude's published "
            f"identity</strong>.</li>{consent}</ol>"
            "<p class='muted small'>The skill is optional; it is on the <a href='/skill'>skill page</a>."
            "</p></div>"
            "<div class='card'><h2>Connector and skill in one file: plugin upload</h2><ol class='setup'>"
            "<li>Download the plugin. Do not unzip it."
            f"<div class='actions'><a class='btn btn-primary' href='{e(zip_url)}'>Download the plugin (ZIP)"
            "</a></div></li>"
            "<li>On claude.ai or in the desktop app, open <strong>Customize</strong>, then "
            "<strong>Plugins</strong>.</li>"
            "<li>Press <strong>Add</strong>, then <strong>Upload plugin</strong>, and choose the ZIP file."
            "</li><li>Open the plugin's <strong>Connectors</strong> tab, add the connector and press "
            "<strong>Connect</strong>.</li>"
            "<li>Then follow steps 3 to 5 of the first way.</li></ol></div>"
            "<div class='card'><h2>Turn it on in a chat</h2>"
            "<p>Press <strong>+</strong> by the message box, open <strong>Connectors</strong> and switch "
            f"<strong>{e(site)}</strong> on.</p>"
            "<p class='muted small'>If sign-in fails with the published identity, remove the connector, "
            "add it again choosing Register automatically, and tell the owner. Claude shows a plain tile "
            "for custom connectors, not their own icon (reported by developers, not in Anthropic's "
            "documentation), so there is no icon to set.</p></div>"
        )
    if client == "chatgpt":
        icon_name = f"{name}-icon.png"
        return (
            "<div class='card'><h2>ChatGPT</h2>"
            "<p><strong>Works on:</strong> chatgpt.com in a computer's web browser, on Plus, Pro, Business, "
            "Enterprise or Edu, with Developer mode on (OpenAI's pages). Checked with this gateway "
            "on the Pro plan on 2026-10-10: ChatGPT used every tool, deck proposals included.</p>"
            "<p><strong>Does not work on:</strong> the ChatGPT iPhone and Android apps, or the Free plan. "
            "Use <a href='/install?for=claude'>Claude</a> there instead, whose phone apps work.</p></div>"
            "<div class='card'><h2>Add the connector</h2><ol class='setup'>"
            "<li>On a computer, open chatgpt.com and sign in.</li>"
            "<li>Download the connector icon. It is a PNG under 10 KB, ChatGPT's limit."
            f"<div class='actions'><a class='btn btn-primary' href='{CONNECTOR_ICON_PATH}' "
            f"download='{e(icon_name)}'>{icon('download')} Download the icon (PNG)</a></div></li>"
            "<li>Open <strong>Settings</strong>, then <strong>Security and login</strong>, and turn on "
            "<strong>Developer mode</strong>. If it is not there, your plan does not offer it: use "
            "<a href='/install?for=claude'>Claude</a> instead.</li>"
            "<li>In the sidebar, open <strong>Plugins</strong> (older menus: Apps) and press "
            "<strong>+</strong> (or Create).</li>"
            "<li>Name: copy and paste this." + _copy_field("gpt-name", "Name", site) + "</li>"
            "<li>Description: copy and paste this."
            + _copy_field("gpt-desc", "Description", SERVER_DESCRIPTION, rows=3)
            + "</li><li>MCP server URL: copy and paste this."
            + _copy_field("gpt-url", "Connector URL", mcp_url)
            + "</li><li>Icon: choose the file from step 2. Do it now: ChatGPT reportedly lets you set the "
            "icon only while creating the connector (a user report, not OpenAI's documentation).</li>"
            "<li>Authentication: choose <strong>OAuth</strong>. Leave client ID and client secret empty."
            "</li><li>Accept the risk notice and create the connector.</li>"
            "<li>Press <strong>Connect</strong>. This site shows a <strong>Connect an application</strong> "
            "page. Check that it says you go back to <code>chatgpt.com</code>, then press <strong>Approve "
            "and sign in</strong>. If anything else is shown, press <strong>Deny</strong>.</li>"
            "<li>Sign in with the account the owner gave you.</li></ol></div>"
            "<div class='card'><h2>Add the instructions (recommended)</h2><ol class='setup'>"
            "<li>In ChatGPT, create a project.</li>"
            "<li>Copy the ChatGPT instructions from the <a href='/skill'>skill page</a>.</li>"
            "<li>Open the project's menu, then <strong>Project settings</strong>, and paste them into the "
            "instructions.</li>"
            f"<li>Chat inside that project with the {e(site)} connector on.</li></ol></div>"
            "<div class='card'><h2>ChatGPT plugin file (workspace admins only)</h2>"
            "<p>The connector and the MTG skill in one file. OpenAI's help pages say workspace owners "
            "and admins on ChatGPT Business and Enterprise upload it under Plugins in the admin console "
            "(reported, not yet tested with this gateway). Individual Plus and Pro accounts have no "
            "plugin upload (checked on a Pro account, 2026-10-11): add the connector as above.</p>"
            f"<div class='actions'><a class='btn' href='/plugin/{e(chatgpt_zip_name(site))}'>"
            f"{icon('download')} Download the ChatGPT plugin (ZIP)</a></div></div>"
            "<div class='card'><h2>If ChatGPT blocks write tools</h2>"
            "<p>OpenAI's pages report this may happen on Plus and Pro. Research, simulation, reading decks, "
            "scanning and deck proposals still work. To apply a proposal, open its review link or your "
            "<a href='/proposals'>proposals</a> and press Apply. To save scans, use the "
            "<a href='/scan'>scan page</a>.</p></div>"
        )
    if client == "codex":
        return (
            "<div class='card'><h2>Codex CLI</h2>"
            "<p>As documented by OpenAI (not yet tested with this gateway).</p><ol class='setup'>"
            "<li>In a terminal, run this command."
            + _copy_field("codex-add", "Command 1", f"codex mcp add {name} --url {mcp_url}")
            + "</li><li>Run this one. Your browser opens."
            + _copy_field("codex-login", "Command 2", f"codex mcp login {name}")
            + "</li><li>Sign in with the account the owner gave you.</li></ol></div>"
        )
    raise ValueError(client)


__all__ = [
    "CLIENTS",
    "add_plugin_routes",
    "build_zip",
    "chatgpt_zip_name",
    "claude_connect_link",
    "install_markdown",
    "marketplace",
    "pick_client",
    "plugin_dir",
    "plugin_label",
    "plugin_slug",
]
