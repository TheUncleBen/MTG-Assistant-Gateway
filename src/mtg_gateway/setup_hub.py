"""The setup hub: ``/install``, one page with a section for each way in (Claude, Claude Code,
ChatGPT, Codex CLI, the assistant skill and the Android app), and ``/install.md``, the same steps
as Markdown for a person or an AI agent handed the link.

Both come from one step list (``sections``): each step is written once, with Markdown's inline
marks (**bold**, `code`, [link](url)), and ``section_html`` and ``section_md`` render it. A step can
carry a value to copy (a box with a Copy button on the page, the value in backticks in Markdown) or
a link (a button on the page, a Markdown link). What only the page can show (the Android app's
version and fingerprints, the ChatGPT instructions box) is the page's own, never install text.

``/install`` signs a browser in first; an AI agent fetching it gets the Markdown, as from
``/install.md``, which needs no sign-in. ``/skill`` and ``/app`` lead to their section here (after
the same sign-in as before); the downloads keep their addresses and rules (skill_page.py,
app_page.py, plugin_page.py).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from . import ui
from .app_page import APK_NAME, RELEASES_URL, android_facts
from .pages import _csrf, browser_session, login_redirect
from .plugin_page import (
    CONNECTOR_ICON_PATH,
    SERVER_DESCRIPTION,
    SETUP_SKILL,
    chatgpt_label,
    chatgpt_zip_name,
    claude_connect_link,
    plugin_slug,
)
from .theme import display_name, icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

# The install steps are Markdown, served as plain text: some assistants' web readers refuse a
# text/markdown answer, and every reader takes text/plain.
INSTALL_MD_TYPE = "text/plain; charset=utf-8"

# The assistants the hub connects, each with its heading: /install.md without ?for= has these.
CLIENTS: dict[str, str] = {
    "claude": "Claude: web, desktop, iPhone and Android",
    "claude-code": "Claude Code (terminal)",
    "chatgpt": "ChatGPT",
    "codex": "Codex CLI",
}
# The hub's other sections, reached with ?for= (and from /skill and /app).
EXTRAS: dict[str, str] = {
    "skill": "Assistant skill (Claude and ChatGPT)",
    "android": "Android app",
}
SECTIONS: dict[str, str] = {**CLIENTS, **EXTRAS}


def pick_client(value: str | None) -> str | None:
    """The section named by a ``?for=`` value, or None when it names none we know."""
    key = (value or "").strip().lower()
    return key if key in SECTIONS else None


# -- the step model -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Copy:
    """A value to copy: a read-only box with a Copy button on the page (export.js), the value in
    backticks in Markdown, or ``md`` instead when the value is too long for the plain text."""

    key: str
    label: str
    value: str
    rows: int = 0
    md: str | None = None


@dataclass(frozen=True)
class Link:
    """A button on the page, a link in Markdown (a path is made absolute there)."""

    label: str
    href: str
    primary: bool = True
    download: str | None = None
    icon: str | None = None


@dataclass(frozen=True)
class Step:
    text: str
    copy: Copy | None = None
    link: Link | None = None
    warn: str | None = None  # a warning under the step's box (a notice on the page)


@dataclass(frozen=True)
class Block:
    """A card on the page; in Markdown a run of text under the block's bold ``title``."""

    title: str | None = None
    paras: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    notes: tuple[str, ...] = ()
    # Said only to whoever reads the Markdown (an AI agent doing the install for someone).
    agent: tuple[str, ...] = ()
    # The page's own content (live facts, a long box to copy); not install text.
    page: str = ""
    link: Link | None = None
    warn: str | None = None  # a warning under the block's text (a notice on the page)


@dataclass(frozen=True)
class Section:
    key: str
    heading: str
    blocks: tuple[Block, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Context:
    public_url: str
    site: str
    gpt_text: str | None = None  # the ChatGPT project instructions (skill_page.chatgpt_text)
    app: dict[str, Any] | None = None  # app_page.android_facts, or None when no APK is shipped
    page: bool = True  # False for the public Markdown


_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)")


def inline_html(text: str, public_url: str) -> str:
    """Markdown's inline marks as HTML; a link to this gateway becomes a path."""
    e = html.escape
    out: list[str] = []
    pos = 0
    for m in _INLINE.finditer(text):
        out.append(e(text[pos : m.start()], quote=False))
        if m.group(1) is not None:
            out.append(f"<strong>{e(m.group(1), quote=False)}</strong>")
        elif m.group(2) is not None:
            out.append(f"<code>{e(m.group(2), quote=False)}</code>")
        else:
            href = m.group(4)
            if href.startswith(public_url + "/"):
                href = href[len(public_url) :]
            out.append(f"<a href='{e(href)}'>{e(m.group(3), quote=False)}</a>")
        pos = m.end()
    out.append(e(text[pos:], quote=False))
    return "".join(out)


def copy_field(key: str, label: str, value: str, rows: int = 0) -> str:
    """A read-only box holding ``value`` with a Copy button (export.js), its label above it.
    ``rows`` makes it a text area, so a sentence shows whole instead of scrolling."""
    e = html.escape
    box = (
        f"<textarea id='{key}' rows='{rows}' readonly>{e(value)}</textarea>"
        if rows
        else f"<input id='{key}' type='text' readonly value='{e(value)}'>"
    )
    return (
        f"<div class='copyfield'><label for='{key}'>{e(label)}</label><div class='copyline'>{box}<button "
        f"type='button' class='btn small copybtn' data-copy='{key}' "
        f"aria-describedby='{key}-status'>{icon('copy')} <span "
        f"class='label'>Copy</span></button></div><span class='sr-only' id='{key}-status' role='status' "
        "aria-live='polite'></span></div>"
    )


def _link_html(link: Link, public_url: str) -> str:
    e = html.escape
    href = link.href[len(public_url) :] if link.href.startswith(public_url + "/") else link.href
    cls = "btn btn-primary" if link.primary else "btn"
    dl = f" download='{e(link.download)}'" if link.download else ""
    ic = f"{icon(link.icon)} " if link.icon else ""
    return f"<div class='actions'><a class='{cls}' href='{e(href)}'{dl}>{ic}{e(link.label)}</a></div>"


def _abs(href: str, public_url: str) -> str:
    return public_url + href if href.startswith("/") else href


def block_html(block: Block, heading: str | None, ctx: Context) -> str:
    h = block.title or heading
    out = [f"<div class='card'>{f'<h2>{html.escape(h)}</h2>' if h else ''}"]
    out += [f"<p>{inline_html(p, ctx.public_url)}</p>" for p in block.paras]
    if block.warn:
        out.append(f"<p class='notice warn stepnote'>{inline_html(block.warn, ctx.public_url)}</p>")
    out.append(block.page)
    if block.link:
        out.append(_link_html(block.link, ctx.public_url))
    if block.steps:
        out.append("<ol class='setup'>")
        for s in block.steps:
            out.append(f"<li>{inline_html(s.text, ctx.public_url)}")
            if s.copy:
                out.append(copy_field(s.copy.key, s.copy.label, s.copy.value, s.copy.rows))
            if s.warn:
                out.append(f"<p class='notice warn stepnote'>{inline_html(s.warn, ctx.public_url)}</p>")
            if s.link:
                out.append(_link_html(s.link, ctx.public_url))
            out.append("</li>")
        out.append("</ol>")
    out += [f"<p class='muted small'>{inline_html(n, ctx.public_url)}</p>" for n in block.notes]
    out.append("</div>")
    return "".join(out)


def section_html(section: Section, ctx: Context) -> str:
    return "".join(
        block_html(b, section.heading if i == 0 else None, ctx) for i, b in enumerate(section.blocks)
    )


def _step_md(n: int, s: Step, public_url: str) -> str:
    line = f"{n}. {s.text}"
    if s.copy:
        line += " " + (s.copy.md if s.copy.md is not None else f"{s.copy.label}: `{s.copy.value}`")
    if s.link:
        line += f" [{s.link.label}]({_abs(s.link.href, public_url)})"
    if s.warn:
        line += f"\n   {s.warn}"
    return line


def section_md(section: Section, ctx: Context) -> str:
    parts = [f"## {section.heading}"]
    for b in section.blocks:
        if b.title:
            parts.append(f"**{b.title}**")
        parts += list(b.paras)
        if b.warn:
            parts.append(b.warn)
        if b.link:
            parts.append(f"[{b.link.label}]({_abs(b.link.href, ctx.public_url)})")
        parts += list(b.agent)
        if b.steps:
            parts.append("\n".join(_step_md(i, s, ctx.public_url) for i, s in enumerate(b.steps, 1)))
        parts += list(b.notes)
    return "\n\n".join(parts) + "\n"


# -- the one step list ----------------------------------------------------------------------------


def _sign_in_steps(app: str | None, back_to: str) -> tuple[Step, ...]:
    """The last two steps every connector shares: the gateway's consent page, then the sign-in."""
    names = f"names **{app}** and " if app else ""
    return (
        Step(
            f"This site shows a **Connect an application** page. Check that it {names}says you go back to "
            f"`{back_to}`, then press **Approve and sign in**. If anything else is shown, press **Deny**."
        ),
        Step("Sign in with the account the owner gave you."),
    )


def section(key: str, ctx: Context) -> Section:
    """One section of the hub, the same for the page and the Markdown."""
    pub, site = ctx.public_url, ctx.site
    name = plugin_slug(site)
    mcp = f"{pub}/mcp"
    if key == "claude-code":
        mp = f"{pub}/plugin/marketplace.json"
        return Section(
            key,
            CLIENTS[key],
            (
                Block(
                    paras=("Works on Windows, macOS and Linux.",),
                    steps=(
                        Step(
                            "In a terminal, run this command.",
                            Copy("cc-add", "Command 1", f"claude plugin marketplace add {mp}"),
                        ),
                        Step(
                            "Run this one.",
                            Copy("cc-install", "Command 2", f"claude plugin install {name}@{name}"),
                        ),
                        Step(
                            f"Start Claude Code, type `/mcp`, choose **{name}** and press **Authenticate**. "
                            "Your browser opens."
                        ),
                        Step("Sign in with the account the owner gave you."),
                        Step(f"Back in Claude Code, run `/{name}:{SETUP_SKILL}` to finish."),
                    ),
                    notes=(
                        "The plugin file is checked against a SHA-256 fingerprint in the marketplace "
                        "file, so Claude Code refuses a download that does not match.",
                    ),
                ),
            ),
        )
    if key == "claude":
        return Section(
            key,
            CLIENTS[key],
            (
                Block(
                    paras=(
                        "Works on every Claude plan (the Free plan allows one custom connector), and "
                        "every tool works, including deck edits. Add it once on claude.ai or in the "
                        "desktop app; the iPhone and Android apps then have it too. The phone apps cannot "
                        "add it themselves: on a phone, open claude.ai in the phone's web browser instead.",
                        "Pick one way. Nothing is typed into Claude: you sign in in your browser.",
                    )
                ),
                Block(
                    "Quickest: connector only",
                    steps=(
                        Step(
                            f"Press **Connect to Claude**. It opens claude.ai with the name (`{site}`) and "
                            "the connector URL filled in.",
                            link=Link("Connect to Claude", claude_connect_link(pub, site), icon="external"),
                        ),
                        Step("Check the name and URL, then continue."),
                        Step("Under **OAuth client**, if asked, choose **Use Claude's published identity**."),
                        *_sign_in_steps("Claude", "claude.ai"),
                    ),
                    notes=(
                        f"The skill is optional; it is in the [assistant skill]({pub}/install?for=skill) "
                        "section.",
                    ),
                ),
                Block(
                    "Connector and skill in one file: plugin upload",
                    steps=(
                        Step(
                            "Download the plugin. Do not unzip it.",
                            link=Link(
                                "Download the plugin (ZIP)",
                                f"{pub}/plugin/{name}.zip",
                                primary=False,
                                icon="download",
                            ),
                        ),
                        Step("On claude.ai or in the desktop app, open **Customize**, then **Plugins**."),
                        Step("Press **Add**, then **Upload plugin**, and choose the ZIP file."),
                        Step(
                            f"Open the plugin's **Connectors** tab, add the {site} connector and press "
                            "**Connect**."
                        ),
                        Step("Then follow steps 3 to 5 of the first way."),
                    ),
                ),
                Block(
                    "Turn it on in a chat",
                    paras=(f"Press **+** by the message box, open **Connectors** and switch **{site}** on.",),
                    notes=(
                        "If sign-in fails with the published identity, remove the connector, add it again "
                        "choosing **Register automatically**, and tell the owner. Claude shows a plain "
                        "tile for custom connectors rather than their own icon, so there is no icon to "
                        "set.",
                    ),
                ),
            ),
        )
    if key == "chatgpt":
        gpt_name = chatgpt_label(site)  # the name the plugin file carries too
        return Section(
            key,
            CLIENTS[key],
            (
                Block(
                    paras=(
                        "**You need:** a ChatGPT Plus, Pro, Business, Enterprise or Edu plan, and a "
                        "computer. Set it up on chatgpt.com in the computer's web browser, not in a "
                        "phone's browser or the ChatGPT desktop app.",
                        "**Works on:** chatgpt.com in a computer's web browser, with Developer mode on "
                        "(step 3 below). In our testing on Pro every tool worked there, deck proposals "
                        "included. On some plans, Plus especially, ChatGPT may block the tools that "
                        "change things: see **If ChatGPT blocks write tools** below.",
                        "**Does not work on:** the ChatGPT iPhone and Android apps, or the Free plan, "
                        f"which has no Developer mode. Use [Claude]({pub}/install?for=claude) there "
                        "instead: its phone apps work.",
                    ),
                    agent=(
                        "If the person is in a ChatGPT phone app or on the Free plan, do not attempt the "
                        "steps below: say so once and offer Claude or chatgpt.com on a computer.",
                    ),
                ),
                Block(
                    "Add the connector",
                    paras=(
                        "ChatGPT shows the name and description you type when you add the connector. "
                        f"Use “{gpt_name}” and the description below.",
                        "OpenAI's instructions say ChatGPT takes an app's icon from its plugin file or an "
                        "upload, so the gateway's icon may not show for a connector added by hand.",
                    ),
                    steps=(
                        Step("On a computer, open chatgpt.com and sign in."),
                        Step(
                            "Download the connector icon (a small PNG).",
                            link=Link(
                                "Download the icon (PNG)",
                                CONNECTOR_ICON_PATH,
                                download=f"{name}-icon.png",
                                icon="download",
                            ),
                        ),
                        Step(
                            "Open **Settings**, then **Security and login**, and turn on **Developer "
                            "mode**. If it is not there, your plan does not offer it: use "
                            f"[Claude]({pub}/install?for=claude) "
                            "instead."
                        ),
                        Step(
                            "In the sidebar, open **Plugins** (older menus: Apps) and press **+** (or "
                            "Create)."
                        ),
                        Step("Paste the name.", Copy("gpt-name", "Name", gpt_name)),
                        Step(
                            "Paste the description.",
                            Copy("gpt-desc", "Description", SERVER_DESCRIPTION, rows=3),
                        ),
                        Step("Paste the MCP server URL.", Copy("gpt-url", "Connector URL", mcp)),
                        Step(
                            "Icon: choose the file from step 2. Do it now: you may not be able to add the "
                            "icon after the connector is created."
                        ),
                        Step("Authentication: choose **OAuth**. Leave client ID and client secret empty."),
                        Step("Accept the risk notice and create the connector."),
                        Step(
                            "Press **Connect**. This site shows a **Connect an application** page. Check "
                            "that it says you go back to `chatgpt.com`, then press **Approve and sign "
                            "in**. If anything else is shown, press **Deny**."
                        ),
                        Step("Sign in with the account the owner gave you."),
                    ),
                ),
                Block(
                    "Add the instructions (recommended)",
                    steps=(
                        Step("In ChatGPT, create a project."),
                        Step(
                            "Copy the ChatGPT instructions from the [assistant "
                            f"skill]({pub}/install?for=skill) section."
                        ),
                        Step(
                            "Open the project's menu, then **Project settings**, and paste them into the "
                            "instructions."
                        ),
                        Step(f"Chat inside that project with the {gpt_name} connector on."),
                    ),
                ),
                Block(
                    "ChatGPT plugin file (Business/Enterprise admins only)",
                    paras=(
                        "The connector and the MTG skill in one file, for ChatGPT Business and Enterprise "
                        "workspace owners and admins only: it should upload under **Plugins** in the admin "
                        "console. If the upload does not work, add the connector as above instead. Plus "
                        "and Pro accounts likely have no plugin upload (Pro had none in our testing): use "
                        "the connector, as above.",
                    ),
                    link=Link(
                        "Download the ChatGPT plugin (ZIP)",
                        f"/plugin/{chatgpt_zip_name(site)}",
                        primary=False,
                        icon="download",
                    ),
                ),
                Block(
                    "If ChatGPT blocks write tools",
                    paras=(
                        "This may happen on Plus and Pro. Research, simulation, "
                        "reading decks, scanning and deck proposals still work. To apply a proposal, open "
                        "its review "
                        f"link or your [proposals]({pub}/proposals) and press Apply. To save scans, use "
                        f"the [scan page]({pub}/scan).",
                    ),
                ),
            ),
        )
    if key == "codex":
        return Section(
            key,
            CLIENTS[key],
            (
                Block(
                    steps=(
                        Step(
                            "In a terminal, run this command.",
                            Copy("codex-add", "Command 1", f"codex mcp add {name} --url {mcp}"),
                        ),
                        Step(
                            "Run this one. Your browser opens.",
                            Copy("codex-login", "Command 2", f"codex mcp login {name}"),
                        ),
                        Step("Sign in with the account the owner gave you."),
                    ),
                ),
            ),
        )
    if key == "skill":
        gpt = ctx.gpt_text
        return Section(
            key,
            EXTRAS[key],
            (
                Block(
                    paras=(
                        "The MTG skill teaches your assistant to use this gateway well: which tool to "
                        "use, how to report simulations honestly, and to show you every deck change "
                        "before making it. It is optional but recommended.",
                    )
                ),
                Block(
                    "Claude",
                    steps=(
                        Step(
                            "Download the skill (sign in first). Do not unzip it.",
                            link=Link(
                                "Download the skill (ZIP)", f"{pub}/skill/mtg-gateway.zip", icon="download"
                            ),
                        ),
                        Step(
                            "In Claude's settings, open **Capabilities** and turn on **Code execution and "
                            "file creation**."
                        ),
                        Step("On claude.ai or in the desktop app, open **Customize**, then **Skills**."),
                        Step(
                            "Click **+**, then **Create skill**, then **Upload a skill**, and choose the "
                            "ZIP file."
                        ),
                    ),
                ),
                Block(
                    "ChatGPT",
                    steps=(
                        Step("On chatgpt.com, create a project."),
                        Step("Open the project's menu, then **Project settings**."),
                        Step(
                            "Copy the ChatGPT project instructions and paste them into the project's "
                            "instructions.",
                            # the Markdown is public: it points at the signed-in page for the text
                            Copy(
                                "gpt-text",
                                "ChatGPT project instructions",
                                gpt or "",
                                rows=14,
                                md=f"They are on {pub}/install?for=skill after you sign in.",
                            )
                            if gpt or not ctx.page
                            else None,
                        ),
                        Step(f"Chat inside that project with the {chatgpt_label(site)} connector on."),
                    ),
                    notes=(
                        "ChatGPT supports custom connectors on the web only, not in its phone apps.",
                        *(
                            ("The ChatGPT instructions are not available on this gateway.",)
                            if ctx.page and not gpt
                            else ()
                        ),
                    ),
                ),
            ),
        )
    if key == "android":
        return _android(ctx)
    raise ValueError(key)


def _android(ctx: Context) -> Section:
    intro = Block(
        paras=(
            "The MTG Assistant Gateway app is the Android app for this gateway: the same pages as here, "
            "full screen, plus a phone-camera scan screen with torch brightness, zoom and exposure. It is "
            "free, has no app store and no account of its own, and on first launch you type this "
            "gateway's address.",
        )
    )
    app = ctx.app
    if app is None:
        return Section(
            "android",
            EXTRAS["android"],
            (
                intro,
                Block(
                    "No app file on this gateway",
                    paras=(
                        "The person who runs this gateway hasn't added the app file yet. In the meantime, "
                        f"open **{ctx.public_url}** in your phone's browser and add it to your home screen.",
                    ),
                    link=Link("Back to decks", "/decks", primary=False),
                ),
            ),
        )
    if app.get("cert_sha256"):
        verify = (
            "Compare the signing certificate SHA-256 on this gateway's setup page with the one in the "
            f"[project's GitHub release]({RELEASES_URL}) for this version (in the release notes and its "
            "`mtg-assistant-gateway.json`), or with one the owner of this gateway gave you some other way. "
            "If they differ, do not install the app; tell the owner."
        )
    else:
        verify = (
            "This build did not record its signing certificate, so the setup page cannot show one. Ask the "
            "owner of this gateway for the certificate SHA-256 before installing."
        )
    return Section(
        "android",
        EXTRAS["android"],
        (
            intro,
            Block(
                "Install",
                page=app.get("facts_html", ""),
                steps=(
                    Step(
                        "Press **Download the app (APK)**, on your Android phone.",
                        link=Link("Download the app (APK)", f"/app/{APK_NAME}", icon="download"),
                    ),
                    Step(f"Check that it is the real app. {verify}"),
                    Step(
                        "On a Samsung Galaxy phone, turn **Auto Blocker** off (Settings > Security and "
                        "privacy > Auto Blocker). While it is on, the phone won't install apps that don't "
                        "come from an app store, so neither this app nor its updates."
                    ),
                    Step(
                        "Open the downloaded file. Android asks once to allow installs from your browser: "
                        "allow it."
                    ),
                    Step(
                        "Play Protect may warn that the developer is unknown. That is "
                        "expected: the app is signed by whoever runs this gateway, not by an app store. "
                        "It may also offer to scan the app first: scanning it or not are both fine. "
                        "Only continue if the signing certificate matched in step 2. If Play Protect says "
                        "the app is harmful, or you are unsure, do not install it and ask the owner of "
                        "this gateway."
                    ),
                    Step(f"Open MTG Assistant Gateway and type this gateway's address: `{ctx.public_url}`."),
                ),
                notes=(
                    "Both values come from this gateway, the same place as the file. The file SHA-256 only "
                    "shows the download arrived intact; it does not show who made the app. For a stronger "
                    "check, on a "
                    f"computer run `apksigner verify --print-certs {APK_NAME}` and compare its certificate "
                    "SHA-256 digest with the published one.",
                    "Updates: the app asks this gateway for the version it offers here and offers it "
                    "itself; Update installs it over the app you have (with Auto Blocker still off on a "
                    "Samsung phone; scanning it with Play Protect or not are both fine). Android only "
                    "accepts an update "
                    "signed with the same key as the installed app, so your settings and sign-in are "
                    "kept, and a file signed by anyone else is refused. An app from before 0.7.12 was "
                    "signed with a test key: uninstall it once and install this file.",
                ),
            ),
        ),
    )


# -- the page and the Markdown --------------------------------------------------------------------

INTRO = (
    "Your assistant can then read your decks and collection and propose changes for you to approve, "
    "or apply them itself if your approval mode allows. You never need a key or token; never paste a "
    "password into a chat."
)
# The Markdown (for an agent handed the link) says what the steps are for before INTRO's "then".
MD_LEAD = "These steps connect your AI assistant to this site. You need the account the owner gave you."
PURPOSE = (
    "Connect Claude, ChatGPT or Codex to this gateway, add the assistant skill, or install the Android app."
)
PICK = "Which app will you use?"


def _after(ctx: Context) -> Block:
    """What to do once connected, for every assistant."""
    return Block(
        steps=(
            # F12: a plain ask, not a tool name
            Step("Ask your assistant “Who am I on the MTG gateway?” It should answer with your name."),
            Step(
                "To let it read and edit your own Archidekt decks, open your "
                f"[account]({ctx.public_url}/account) "
                "and link Archidekt once."
            ),
        ),
        notes=("Every deck change is shown to you first and applied only after you approve.",),
    )


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


def install_markdown(
    public_url: str, site: str, client: str | None = None, *, ctx: Context | None = None
) -> str:
    """The install steps as Markdown, for a person or an AI agent given the link. With ``client``
    set, only that section; without it, every assistant's section."""
    ctx = ctx or Context(public_url, site)
    mcp = f"{public_url}/mcp"
    keys = [client] if client else list(CLIENTS)
    pick = (
        ""
        if client
        else "Only one section applies to you. For just your app's steps, add `?for=` and one of "
        + ", ".join(f"`{c}`" for c in CLIENTS)
        + f", for example `{public_url}/install.md?for=chatgpt`. The assistant skill and the Android app "
        "have their own sections: `?for=skill` and `?for=android`.\n\n"
    )
    sections = "\n".join(section_md(section(k, ctx), ctx) for k in keys)
    after = section_md(Section("after", "After connecting", (_after(ctx),)), ctx)
    return f"""# Install the {site} assistant plugin

{MD_LEAD} {INTRO}

Connector URL: `{mcp}`

{pick}{AGENT_RULES}
{sections}
{after}
{BLOCKED_TOOLS.format(public_url=public_url)}"""


# The install page (F11): the app list beside the steps on a computer (from 760 px of the page's
# width), a "Steps for:" drop-down above them on a phone or a narrow window; the copy boxes and
# numbered steps (export.js runs the Copy buttons). The picked app is tinted with an orange edge,
# never an orange button, so each section's own main button is the page's one orange.
INSTALL_CSS = (
    ".inst{container:inst/inline-size}"
    ".inst .cols{display:grid;grid-template-columns:minmax(0,1fr);gap:1rem;align-items:start}"
    ".inst .pick{display:none}"
    "@container inst (min-width:760px){.inst .cols{grid-template-columns:17rem minmax(0,1fr)}"
    ".inst .drop{display:none}.inst .pick{display:block}}"
    "@supports not (container-type:inline-size){@media (min-width:900px){"
    ".inst .cols{grid-template-columns:17rem minmax(0,1fr)}.inst .drop{display:none}"
    ".inst .pick{display:block}}}"
    ".inst .pick{position:sticky;top:.5rem;border:1px solid var(--border);border-radius:var(--radius-panel);"
    "background:var(--surface);padding:.5rem}"
    ".inst .pick h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.03em;color:var(--text-muted);"
    "margin:.25rem .5rem .4rem}"
    ".inst .pick a,.inst .drop .list a{display:flex;align-items:center;min-height:44px;white-space:normal;"
    "line-height:1.25;width:100%;text-align:left;border:0;border-left:3px solid transparent;border-radius:0;"
    "background:transparent;color:var(--text);font-weight:700;padding:.4rem .6rem;text-decoration:none}"
    ".inst .pick a:hover,.inst .drop .list a:hover{background:var(--surface-2)}"
    ".inst .pick a[aria-current=true],.inst .drop .list a[aria-current=true]{background:var(--orange-tint);"
    "color:var(--orange-text);border-left-color:var(--orange)}"
    ".inst .pick .grp{border-top:1px solid var(--border-soft);margin:.4rem 0 0;padding-top:.4rem}"
    ".inst .drop{border:1px solid var(--border);border-radius:var(--radius-panel);background:var(--surface);"
    "margin:0 0 1rem}"
    ".inst .drop>summary{display:flex;align-items:center;gap:.5rem;min-height:44px;padding:0 .75rem;"
    "cursor:pointer;list-style:none}"
    ".inst .drop>summary::-webkit-details-marker{display:none}"
    ".inst .drop>summary .v{flex:1;min-width:0;font-weight:700;overflow-wrap:anywhere}"
    ".inst .drop>summary .sfk{color:var(--text-muted);font-size:.86rem}"
    ".inst .drop>summary svg{flex:none;transition:transform .15s}"
    ".inst .drop[open]>summary svg{transform:rotate(180deg)}"
    ".inst .drop .list{border-top:1px solid var(--border-soft);padding:.25rem 0}"
    ".inst .card>h2{margin-bottom:.4rem}"
    ".inst p,.inst li{overflow-wrap:anywhere}"
    # the address breaks at its <wbr> first; mid-word only when the address alone is too wide
    ".inst a.nb code{overflow-wrap:anywhere}"
    ".setup>li{margin:.7rem 0}"
    ".copyfield{margin:.5rem 0}.copyfield label{margin:.2rem 0 .3rem}"
    ".copyline{display:flex;gap:.5rem;align-items:flex-start}"
    ".copyline input,.copyline textarea{flex:1;min-width:0;margin:0;"
    "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;"
    "font-size:.85rem}"
    ".copyline .btn{flex:none;margin:0}"
    ".notice.stepnote{margin:.5rem 0 0;overflow-wrap:anywhere}"
)


def _picker(client: str | None, e) -> tuple[str, str]:
    """The app list (the left column on a computer) and the drop-down (narrow screens), both with
    the picked app marked; each app keeps its own ?for= address."""

    def item(key: str, label: str) -> str:
        cur = " aria-current='true'" if key == client else ""
        return f"<a href='/install?for={key}'{cur}>{e(label)}</a>"

    items = [item(k, label) for k, label in SECTIONS.items()]
    n = len(CLIENTS)  # the assistants, a divider, then the skill and the app
    nav = (
        f"<nav class='pick' aria-label='Which app'><h2>{PICK}</h2>{''.join(items[:n])}"
        f"<div class='grp'></div>{''.join(items[n:])}</nav>"
    )
    summary = (
        f"<span class='sfk'>Steps for:</span><span class='v'>{e(SECTIONS[client])}</span>"
        if client
        else f"<span class='v'>{PICK}</span>"
    )
    drop = (
        f"<details class='drop'><summary>{summary}{icon('chev')}</summary>"
        f"<div class='list'>{''.join(items)}</div></details>"
    )
    return nav, drop


def _context(state: AppState, *, page: bool) -> Context:
    """What the sections need from this gateway: the shipped app's facts, and for the signed-in
    page the ChatGPT instructions (the public Markdown points at the page for them)."""
    s = state.settings
    gpt = None
    if page:
        from .skill_page import chatgpt_text, skill_dir

        base = skill_dir()
        gpt = chatgpt_text(base) if base is not None else None
    return Context(s.public_url, s.server_name, gpt_text=gpt, app=android_facts(), page=page)


def add_setup_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    static = {"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}

    @server.custom_route("/install.md", methods=["GET"], include_in_schema=False)
    async def install_md(request: Request) -> Response:
        client = pick_client(request.query_params.get("for"))
        return PlainTextResponse(
            install_markdown(s.public_url, s.server_name, client, ctx=_context(state, page=False)),
            media_type=INSTALL_MD_TYPE,
            headers=static,
        )

    @server.custom_route("/install", methods=["GET"], include_in_schema=False)
    async def install(request: Request) -> Response:
        client = pick_client(request.query_params.get("for"))
        sub, sid = browser_session(state, request)
        if sub is None:
            # A person's browser signs in first. An AI agent handed this address ("install this
            # plugin") gets the same public steps as /install.md: browsers mark page loads with
            # Sec-Fetch-Mode: navigate, HTTP clients and agent fetchers don't.
            if request.headers.get("sec-fetch-mode") == "navigate":
                target = "/install" + (f"?for={client}" if client else "")
                return login_redirect(target)
            return PlainTextResponse(
                install_markdown(s.public_url, s.server_name, client, ctx=_context(state, page=False)),
                media_type=INSTALL_MD_TYPE,
                headers={"Cache-Control": "no-store", "Vary": "Sec-Fetch-Mode, Cookie"},
            )
        e = html.escape
        ctx = _context(state, page=True)
        md_url = f"{s.public_url}/install.md" + (f"?for={client}" if client else "")
        # the address is a link that breaks only after the gateway's address, never mid-word
        md_shown = f"{e(s.public_url)}/<wbr>install.md" + (f"?for={client}" if client else "")
        intro = (
            f"<div class='card'><p>{e(INTRO, quote=False)}</p>"
            "<p class='muted small'>Giving an AI agent this page's address and saying \"install this "
            f"plugin\" is enough: the plain-text version is at <a class='nb' href='{e(md_url)}'>"
            f"<code>{md_shown}</code></a>.</p></div>"
        )
        nav, drop = _picker(client, e)
        if client is None:
            right = (
                intro + "<div class='card'>"
                "<p class='muted small'>On a phone? Claude's iPhone and Android apps work once the "
                "connector is added on claude.ai or in the desktop app. ChatGPT's phone apps do not "
                "support custom connectors: set ChatGPT up on chatgpt.com on a computer.</p>"
                + copy_field("mcp-url", "Connector URL", s.mcp_url)
                + "</div>"
            )
        else:
            right = (
                (intro if client in CLIENTS else "")
                + section_html(section(client, ctx), ctx)
                + (block_html(_after(ctx), "After connecting", ctx) if client in CLIENTS else "")
            )
        body = (
            f"<div class='inst'>{ui.page_head('Guided setup', PURPOSE)}"
            f"<div class='cols'><div>{drop}{nav}</div><div>{right}</div></div></div>"
        )
        # Signed in, so the page gets the member's navigation (tab bar, rail, account menu).
        user = state.db.get_user(sub) or {}
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
            heading=False,
            main_class="ui-wide",
            head_extra=f"<style>{INSTALL_CSS}</style><script src='/static/export.js' defer></script>",
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp


__all__ = [
    "CLIENTS",
    "EXTRAS",
    "SECTIONS",
    "add_setup_routes",
    "install_markdown",
    "pick_client",
    "section",
    "section_html",
    "section_md",
]
