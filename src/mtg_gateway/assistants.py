"""Each connected assistant's own approval mode (owner decision, 2026-10-28; built as the approved
mockups show): Account › Account details › Assistants.

What counts as one assistant is where it provably sends the member back after sign-in, never the
name it gives itself: every app whose sign-in addresses are all Claude's known callback addresses
(KNOWN_CALLBACKS: the scheme, host and path exactly) is "Claude" (the web, desktop and phone apps,
and one that connects again under a new client id), every one sending members back to ChatGPT's is
"ChatGPT". Any other address on those companies' hosts makes an app its own assistant, starting at
the default (security review 3, R3-02): an open redirect there would hand the code on, and a
metadata document anywhere on those hosts proves nothing about where it sends members. The wider
host match still picks the app's logo (ui.client_brand). Any other app is one assistant per client
id, and so is an app that runs on the member's own computer (a localhost address, such as Claude
Code), whatever its metadata says: anyone can claim a well-known name that way.

Each assistant follows the member's default (``users.approval_mode``, "Default for new
assistants") until the member picks a mode for it; a newly connected one always starts at that
default and never copies another assistant's mode. The mode that governs a proposal is its
creator's (only the app that made a proposal may apply it, decks.py), always under the admin's cap.
Only the member's own browser sets a mode, never an assistant over MCP or the API (modes.py).
The member's own Disconnect (the assistant's page, Disconnect all, or the older one-app Disconnect
on POST /account) forgets the assistant's mode once none of its apps is connected, so connected
again it starts at the default. What the member did not do forgets nothing: an assistant whose
sign-in lapsed, or that an administrator disconnected, gets the mode the member chose back when it
connects again (security review 3, R3-03).
"""

from __future__ import annotations

import hashlib
import html
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from . import modes, ui
from .theme import icon

PAGE = "/account/assistants"
DEFAULT_PAGE = f"{PAGE}/default"
# Our own pseudo clients: the member's own browser and the administrator are never an assistant.
NOT_ASSISTANTS = ("__browser__", "__admin__")
BRAND_NAMES = {"claude": "Claude", "openai": "ChatGPT"}
BRAND_SLUGS = {"claude": "claude", "openai": "chatgpt"}
# The host each checked assistant is shown by when it uses it (owner's words: claude.ai -> Claude,
# chatgpt.com -> ChatGPT); another of the company's hosts is shown as it is.
BRAND_HOSTS = {"claude": "claude.ai", "openai": "chatgpt.com"}
LOOPBACK = ("localhost", "127.0.0.1", "::1")
LOCAL_WORDS = "Runs on this computer (localhost)"
APP_PREFIX = "app:"
NEW_DAYS = 7  # a new assistant's notice on Home goes after this long, seen or not
NONE_YET = "No assistant is connected to your account yet."


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""


def is_local(redirects: Iterable[str]) -> bool:
    """Whether every address an app sends the member back to is on this computer (localhost)."""
    hosts = [_host(u) for u in redirects]
    return bool(hosts) and all(h in LOOPBACK for h in hosts)


# The exact addresses Claude and ChatGPT send members back to after sign-in (docs/CONNECT.md):
# brand -> (host, path). Only these group an app under the brand for its approval mode.
KNOWN_CALLBACKS = {
    "claude": (("claude.ai", "/api/mcp/auth_callback"), ("claude.com", "/api/mcp/auth_callback")),
    "openai": (("chatgpt.com", "/connector_platform_oauth_redirect"),),
}


def callback_brand(url: str) -> str | None:
    """The brand whose known callback ``url`` is exactly (https, the host in any letter case, the
    path, no port, user, query or fragment), else None."""
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    if p.scheme != "https" or port is not None or p.username or p.password or p.query or p.fragment:
        return None
    if "#" in url or "?" in url:  # an empty query or fragment is still one
        return None
    found = ((p.hostname or "").casefold(), p.path)
    return next((brand for brand, known in KNOWN_CALLBACKS.items() if found in known), None)


def key_of(client_id: str, redirects: list[str]) -> str:
    """The assistant a client belongs to: "claude" or "openai" when every address it sends members
    back to is that brand's known callback (KNOWN_CALLBACKS), else "app:<client id>". The client
    id itself (a metadata document's address) counts for nothing here, and an app on localhost is
    always its own."""
    brands = {callback_brand(u) for u in redirects}
    if len(brands) == 1 and None not in brands:
        brand = brands.pop()
        if brand in BRAND_NAMES:
            return brand
    return APP_PREFIX + client_id


def slug_of(key: str) -> str:
    """The assistant's address part on its page: "claude", "chatgpt", or a short hash for any other
    app (never its client id in an address)."""
    if key in BRAND_SLUGS:
        return BRAND_SLUGS[key]
    return "app-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def assistant_key(db: Any, client_id: str | None) -> str | None:
    """The assistant ``client_id`` belongs to, or None for none (the member's own browser, the
    administrator, a proposal made before apps were recorded)."""
    if not client_id or client_id in NOT_ASSISTANTS:
        return None
    return key_of(client_id, db.client_redirects(client_id))


def chosen_mode(db: Any, sub: str, client_id: str | None) -> str | None:
    """The mode the member chose for the assistant ``client_id`` belongs to, or None while it
    follows their default (or it is no assistant)."""
    key = assistant_key(db, client_id)
    if key is None:
        return None
    mode = db.assistant_mode(sub, key)
    return mode if modes.valid_mode(mode) else None


def note_sign_in(db: Any, sub: str, client_id: str) -> None:
    """An app signed in (new tokens): remember its assistant, which starts at the member's default."""
    key = assistant_key(db, client_id)
    if key is not None:
        db.note_assistant(sub, key, client_id)


@dataclass
class Assistant:
    """One connected assistant as the pages show it: one or more apps (client ids) holding a live
    token, grouped by key_of."""

    key: str
    name: str
    brand: str | None  # "claude", "openai", or None (no logo)
    local: bool
    hosts: list[str]
    client_ids: list[str] = field(default_factory=list)
    last_used: int = 0
    mode: str | None = None  # the member's own choice; None follows their default
    first_seen: int = 0
    opened: int | None = None
    names: list[str] = field(default_factory=list)  # the apps' registered names, newest first

    @property
    def slug(self) -> str:
        return slug_of(self.key)

    @property
    def href(self) -> str:
        return f"{PAGE}/{self.slug}"

    @property
    def where(self) -> str:
        """Its host in one phrase: "claude.ai", "client.example", or the localhost words."""
        if self.local:
            return LOCAL_WORDS
        return host_words(self.hosts) or "no address"

    @property
    def identified_by(self) -> str:
        if self.local:
            return "localhost"
        return host_words(self.hosts) or "a name it chose for itself"

    @property
    def new(self) -> bool:
        return self.opened is None


def host_words(hosts: list[str]) -> str:
    if not hosts:
        return ""
    return " and ".join(hosts) if len(hosts) < 3 else ", ".join(hosts[:-1]) + " and " + hosts[-1]


def connected(db: Any, sub: str) -> list[Assistant]:
    """The member's connected assistants, the most recently used first: every app holding a live
    token, grouped into assistants, each with the mode the member chose for it."""
    rows = db.assistant_modes(sub)
    out: dict[str, Assistant] = {}
    for app in db.user_clients(sub):  # newest token first
        cid = app["client_id"]
        if cid in NOT_ASSISTANTS:
            continue
        redirects = db.client_redirects(cid)
        key = key_of(cid, redirects)
        a = out.get(key)
        if a is None:
            brand = key if key in BRAND_NAMES else None
            local = is_local(redirects)
            row = rows.get(key) or {}
            mode = row.get("mode")
            a = out[key] = Assistant(
                key=key,
                name=BRAND_NAMES[brand] if brand else ui.app_name(app.get("name")),
                brand=brand,
                local=local,
                hosts=[],
                mode=mode if modes.valid_mode(mode) else None,
                first_seen=int(row.get("first_seen_at") or app.get("last_issued_at") or 0),
                # an app connected before this page existed has no row yet: not new
                opened=row.get("opened_at") if row else int(app.get("last_issued_at") or 0),
            )
        a.client_ids.append(cid)
        a.last_used = max(a.last_used, int(app.get("last_issued_at") or 0))
        if app.get("name"):
            a.names.append(str(app["name"]))
        # a metadata document's own host when it names no address to send members back to
        url_id = cid.startswith("https://") and not redirects
        for h in [_host(cid)] if url_id else [_host(u) for u in redirects]:
            if h and h not in a.hosts:
                a.hosts.append(h)
    for a in out.values():
        if a.brand:  # the owner's host first when it is used: "claude.ai", "chatgpt.com"
            main = BRAND_HOSTS[a.brand]
            a.hosts = [main] if main in a.hosts else sorted(a.hosts)[:1]
        elif not a.local:
            a.hosts.sort()
    return sorted(out.values(), key=lambda a: -a.last_used)


def find(db: Any, sub: str, slug: str) -> Assistant | None:
    return next((a for a in connected(db, sub) if a.slug == slug), None)


def logo(a: Assistant, *, big: bool = False) -> str:
    """The assistant's mark: Claude's or OpenAI's own (only for a checked one), a terminal for an
    app on this computer, or the neutral "connected app" icon."""
    cls = "alogo big" if big else "alogo"
    if a.brand == "claude":
        return f"<span class='{cls}'><img src='{ui.CLAUDE_LOGO}' alt='' width='28' height='28'></span>"
    if a.brand == "openai":
        size = "alt='' width='28' height='28'"
        return (
            f"<span class='{cls} oa'><img class='on-light' src='{ui.OPENAI_LOGO_LIGHT}' {size}>"
            f"<img class='on-dark' src='{ui.OPENAI_LOGO_DARK}' {size}></span>"
        )
    return f"<span class='{cls} term'>{icon('terminal' if a.local else 'app')}</span>"


def mode_label(mode: str) -> str:
    return modes.MODE_LABELS.get(mode, modes.MODE_LABELS[modes.DEFAULT_MODE])


def waiting_from(state: Any, sub: str, a: Assistant) -> int:
    """How many of the member's waiting proposals this assistant made."""
    ids = set(a.client_ids)
    rows = state.db.list_proposals(sub, states=("pending",), limit=500)
    return sum(1 for r in rows if r.get("created_by_client") in ids)


def proposals_href(a: Assistant) -> str:
    """The Proposals page's waiting changes from this assistant (its "From" filter, by name)."""
    name = a.names[0] if a.names else ""
    return f"/proposals?from={quote(name.strip().casefold(), safe='')}" if name else "/proposals"


# -- the pages ----------------------------------------------------------------------------------
def _row(label: str, ic: str, value: str = "", href: str = "", *, chev: bool = True) -> str:
    val = f"<span class='val'>{html.escape(value)}</span>" if value else ""
    inner = f"{icon(ic)}<span class='lbl'>{html.escape(label)}</span>{val}"
    if href:
        ch = f"<span class='ch'>{icon('chev')}</span>" if chev else ""
        return f"<a class='yrow' href='{html.escape(href)}'>{inner}{ch}</a>"
    return f"<div class='yrow'>{inner}</div>"


def _group(title: str, rows: list[str], foot: str = "", *, gid: str = "") -> str:
    t = f"<h2>{html.escape(title)}</h2>" if title else ""
    f = f"<p class='yfoot'>{foot}</p>" if foot else ""
    i = f" id='{gid}'" if gid else ""
    return f"<section class='ygrp'{i}>{t}<div class='rows'>{''.join(rows)}</div>{f}</section>"


def _app_row(a: Assistant, default: str) -> str:
    new = " <span class='ui-lbl orange'>New</span>" if a.new else ""
    mode = mode_label(a.mode) if a.mode else mode_label(default)
    used = ui.when_text(a.last_used)
    return (
        f"<a class='yrow app' href='{a.href}'>{logo(a)}<span class='lbl'><span class='nm'>"
        f"{html.escape(a.name)}{new}</span><span class='sb'>{html.escape(a.where)}"
        f"{f' · last used {html.escape(used)}' if used else ''}</span></span>"
        f"<span class='val'>{html.escape(mode)}</span><span class='ch'>{icon('chev')}</span></a>"
    )


def list_html(db: Any, sub: str, default: str, csrf_in: str, *, open_box: bool = False) -> str:
    """The Assistants page: "Default for new assistants" on top, then one row per connected
    assistant with its mark, name, host and last use, and its mode as the value."""
    apps = connected(db, sub)
    g1 = _group(
        "",
        [_row("Default for new assistants", "shield", mode_label(default), DEFAULT_PAGE)],
        "A newly connected assistant starts with this; each one below can have its own.",
    )
    if apps:
        g2 = _group(
            "Connected",
            [_app_row(a, default) for a in apps],
            "How much each assistant may change on Archidekt without asking you first.",
        )
        all_box = ui.box(
            "discall",
            "Disconnect all assistants?",
            f"<form id='discallf' class='inbox' method='post' action='{PAGE}'>{csrf_in}"
            "<input type='hidden' name='action' value='disconnect_all'>"
            "<p>Every assistant connected to your account loses access at once, and the modes you "
            "chose for them are forgotten. Each one has to be connected and approved again.</p></form>",
            actions=[
                ui.box_button("Cancel", cancel=True),
                ui.box_button("Disconnect all", kind="danger-solid", form="discallf"),
            ],
            open=open_box,
            close_href=PAGE,
        )
        foot = (
            "<div class='ydisc'>"
            + ui.box_opener(
                "Disconnect all assistants",
                f"{PAGE}?disconnect_all=1",
                "discall",
                kind="danger",
                icon_name="x",
            )
            + f"</div>{all_box}"
        )
    else:
        g2 = _group(
            "Connected",
            [f"<div class='yrow yempty'><span class='lbl'>{NONE_YET}</span></div>"],
            "Connect Claude, ChatGPT or another assistant with Guided setup. Each one starts with "
            "your default.",
        )
        foot = f"<div class='ydisc'>{ui.button_link('Guided setup', '/install', icon_name='download')}</div>"
    return (
        f"<a class='yback' href='/account/details'>{icon('chev')}Account details</a><h1>Assistants</h1>"
        f"<div class='yhub ylone'><div class='ycols'><div class='ycol'>{g1}</div>"
        f"<div class='ycol'>{g2}{foot}</div></div></div>"
    )


_SAVED = {
    "manual": "asks you every time",
    "semi": "applies low-risk changes without asking",
    "auto": "applies changes without asking",
}


def saved_text(a: Assistant, choice: str, default: str) -> str:
    """The notice after a choice on an assistant's page, naming it."""
    if choice == "default":
        return f"Saved: “{a.name}” follows your default ({mode_label(default)})."
    return f"Saved: “{a.name}” {_SAVED[choice]}."


def loosens(before: str, after: str) -> bool:
    return modes.rank(after) > modes.rank(before)


def _choice(value: str, label: str, *, on: bool, allowed: bool) -> str:
    off = "" if allowed else " disabled"
    note = "" if allowed else "<small>Not allowed on this gateway</small>"
    return (
        f"<button class='yrow ychoice{' on' if on else ''}' name='mode' value='{value}' "
        f"aria-pressed='{'true' if on else 'false'}'{off}><span class='radio' aria-hidden='true'></span>"
        f"<span class='lbl'>{html.escape(label)}{note}</span></button>"
    )


def confirm_box(a: Assistant, mode: str, waiting: int, csrf_in: str, *, open: bool, post: str = "") -> str:
    """The box that asks before an assistant may apply changes without asking (any loosening): it
    names the app and its host, and says its waiting changes may then be applied by it. ``post`` is
    what Allow saves ("default" when "Same as my default" loosens it), ``mode`` by default."""
    name = html.escape(a.name)
    if mode == "auto":
        title = f"Let “{a.name}” apply changes without asking?"
        what = (
            "the changes it proposes to your Archidekt account itself, except the ones that always "
            "wait for you"
        )
    else:
        title = f"Let “{a.name}” apply low-risk changes without asking?"
        what = (
            "the low-risk changes it proposes to your Archidekt account itself; everything else "
            "still waits for you"
        )
    where = f" ({html.escape(a.identified_by)})"
    body = f"<p>“{name}”{where} could then apply {what}.</p>"
    if waiting:
        body += f"<p>Its {waiting} waiting change{'s' if waiting != 1 else ''} may then be applied by it.</p>"
    if a.local:
        body += (
            "<p>It runs on this computer, and anyone can give an app a well-known name that way. "
            "Allow it only if you started it yourself.</p>"
        )
    form = (
        f"<form id='pmf' class='inbox' method='post' action='{a.href}'>{csrf_in}"
        "<input type='hidden' name='action' value='mode'><input type='hidden' name='confirm' value='yes'>"
        f"<input type='hidden' name='mode' value='{html.escape(post or mode)}'>{body}</form>"
    )
    return ui.box(
        "pmode",
        title,
        form,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(f"Allow for “{a.name}”", kind="primary", form="pmf", icon_name="check"),
        ],
        open=open,
        close_href=a.href,
    )


def app_html(
    state: Any,
    sub: str,
    a: Assistant,
    default: str,
    cap: str,
    csrf_in: str,
    *,
    confirm: str = "",
    confirm_default: bool = False,
    open_disconnect: bool = False,
) -> str:
    """One assistant's page: what it may do (its mode, or "Same as my default"), where it is
    identified by, its last use and waiting changes, and Disconnect."""
    name = html.escape(a.name)
    new = " <span class='ui-lbl orange'>New</span>" if a.new else ""
    head = (
        f"<div class='yme'>{logo(a, big=True)}<span class='who'><b>{name}{new}</b>"
        f"<span class='sub'>{html.escape(a.where)} · connected "
        f"{html.escape(ui.when_text(a.first_seen) or 'just now')}</span></span></div>"
    )
    allowed = {m: modes.rank(m) <= modes.rank(cap) for m in modes.MODES}
    rows = [
        _choice(
            "default",
            f"Same as my default ({mode_label(default)})",
            on=a.mode is None,
            allowed=True,
        )
    ] + [_choice(m, modes.MODE_LABELS[m], on=a.mode == m, allowed=allowed[m]) for m in modes.MODES]
    g1 = (
        f"<form method='post' action='{a.href}' class='ychoices'>{csrf_in}"
        "<input type='hidden' name='action' value='mode'>"
        + _group(
            f"What {a.name} may do",
            rows,
            f"Only changes proposed by “{name}”. A tighter choice also covers its changes already waiting.",
        )
        + "</form>"
    )
    waiting = waiting_from(state, sub, a)
    g2 = _group(
        "This app",
        [
            _row("Identified by", "link", a.identified_by),
            _row("Last used", "clock", ui.when_text(a.last_used) or "never"),
            _row("Waiting changes", "proposals", str(waiting), proposals_href(a) if waiting else ""),
        ],
    )
    box_id = "disc"
    disc_box = ui.box(
        box_id,
        f"Disconnect “{a.name}”?",
        f"<form id='discf' class='inbox' method='post' action='{a.href}'>{csrf_in}"
        "<input type='hidden' name='action' value='disconnect'>"
        f"<p>“{name}” can no longer use your account, and the mode you chose for it is forgotten. "
        "To use it again, connect it again and approve it; it then starts with your default.</p></form>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Disconnect", kind="danger-solid", form="discf"),
        ],
        open=open_disconnect,
        close_href=a.href,
    )
    disc = (
        "<div class='ydisc'>"
        + ui.box_opener(
            f"Disconnect “{a.name}”", f"{a.href}?disconnect=1", box_id, kind="danger", icon_name="x"
        )
        + f"</div>{disc_box}"
    )
    box = ""
    if confirm in ("semi", "auto"):
        post = "default" if confirm_default else ""
        box = confirm_box(a, confirm, waiting, csrf_in, open=True, post=post)
    return (
        f"<a class='yback' href='{PAGE}'>{icon('chev')}Assistants</a><h1>{name}</h1>"
        f"<div class='yhub ylone'>{head}<div class='ycols'><div class='ycol'>{g1}</div>"
        f"<div class='ycol'>{g2}{disc}</div></div></div>{box}"
    )


def home_notice(db: Any, sub: str, default: str, *, now: float) -> str:
    """Home's one notice for an assistant connected for the first time (until its page is opened,
    or for NEW_DAYS): it names the default the assistant starts with; one button opens its page."""
    fresh = [a for a in connected(db, sub) if a.new and now - a.first_seen < NEW_DAYS * 86400]
    if not fresh:
        return ""
    a = max(fresh, key=lambda x: x.first_seen)
    mode = mode_label(a.mode) if a.mode else mode_label(default)
    uses = f"It uses: {mode}." if a.mode else f"It uses your default: {mode}."
    return (
        f"<div class='justin' role='status'>{logo(a)}<span class='t'><b>“{html.escape(a.name)}” "
        f"connected {html.escape(ui.when_text(a.first_seen) or 'just now')}.</b> {html.escape(uses)}</span>"
        f"<a class='btn btn-plain' href='{a.href}'>Change for “{html.escape(a.name)}”</a></div>"
    )


# An assistant's mark: OpenAI's black file on the light theme, its white one on the dark (as
# theme.UI_CSS does for .ui-app); OpenAI's file keeps clear space inside it, so it is drawn larger.
LOGO_CSS = """
.alogo{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;flex:none}
.alogo img{width:28px;height:28px;object-fit:contain;display:block}
.alogo.oa img{width:42px;height:42px;margin:-7px}
.alogo img.on-light{display:none}
@media (prefers-color-scheme:light){
  :root:not([data-theme=dark]) .alogo img.on-light{display:block}
  :root:not([data-theme=dark]) .alogo img.on-dark{display:none} }
:root[data-theme=dark] .alogo img.on-light{display:none}
:root[data-theme=dark] .alogo img.on-dark{display:block}
:root[data-theme=light] .alogo img.on-light{display:block}
:root[data-theme=light] .alogo img.on-dark{display:none}
.alogo.term svg.i{width:26px;height:26px;color:var(--text-muted)}
"""

CSS = (
    LOGO_CSS
    + """
.ygrp .yfoot{margin:.45rem .9rem 0;font-size:.86rem;color:var(--text-muted);line-height:1.4}
.ygrp div.yrow{cursor:default}
.ygrp .yrow.yempty .lbl{color:var(--text-muted);padding:.7rem 0}
/* an assistant's row: its mark in the icon slot, two lines of words, its mode at the right */
.ygrp .yrow.app{padding-top:.55rem;padding-bottom:.55rem;min-height:64px}
.ygrp .yrow.app .lbl{display:flex;flex-direction:column;gap:.15rem;min-width:0}
.ygrp .yrow.app .lbl .nm{display:flex;align-items:center;flex-wrap:wrap;gap:.45rem;font-weight:600}
.ygrp .yrow.app .lbl .sb{font-size:.86rem;color:var(--text-muted);line-height:1.35;overflow-wrap:anywhere}
.ygrp .yrow.app .val{flex:0 1 auto;max-width:44%;text-align:right;line-height:1.3}
@media (max-width:599.98px){ .ygrp .yrow.app .val{max-width:38%;font-size:.86rem} }
/* a choice row: a radio circle in the icon slot (a button: one press saves it) */
.ygrp .ychoice{min-height:50px}
.ygrp .ychoice .radio{width:22px;height:22px;flex:none;border-radius:50%;border:2px solid var(--text-muted);
  box-sizing:border-box}
.ygrp .ychoice.on .radio{border:6px solid var(--orange)}
.ygrp .ychoice.on .lbl{font-weight:700}
.ygrp .ychoice .lbl small{display:block;color:var(--text-muted);font-size:.86rem;font-weight:400}
.ygrp .ychoice[disabled]{cursor:not-allowed}
.ygrp .ychoice[disabled] .lbl{color:var(--text-muted)}
.ychoices{margin:0}
/* the assistant's head on its own page */
.yhub .yme .alogo.big{width:56px;height:56px;border-radius:50%;background:var(--surface-2)}
.yhub .yme .alogo.big img{width:40px;height:40px}
.yhub .yme .alogo.big.oa img{width:60px;height:60px;margin:-10px}
.yhub .yme .alogo.big.term svg.i{width:32px;height:32px}
.yhub .yme b{display:flex;align-items:center;flex-wrap:wrap;gap:.5rem}
.ydisc{display:flex;justify-content:center;margin:.5rem 0 1rem}
.ydisc .btn{display:inline-flex;align-items:center;justify-content:center;gap:.5rem;min-height:48px;
  min-width:min(16rem,100%);padding:0 1.5rem;border-radius:14px;white-space:normal;text-align:center}
.ydisc .btn svg.i{width:18px;height:18px;flex:none}
/* a short page: one column, as wide as a phone screen at most */
.yhub.ylone{max-width:40rem;margin:0}
.yhub.ylone .ycols{grid-template-columns:minmax(0,1fr)}
.mtgbox .body form.inbox p{margin:0 0 .6rem;line-height:1.45}
"""
)

# Home's one-time notice, in the site's notice style (an orange edge on a tinted strip).
HOME_CSS = (
    LOGO_CSS
    + """
.justin{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;margin:0 0 1rem;padding:.6rem .9rem;
  border-radius:0 var(--radius,10px) var(--radius,10px) 0;border-left:4px solid var(--orange);
  background:var(--orange-tint);color:var(--text)}
.justin .alogo,.justin .alogo img{width:24px;height:24px}
.justin .alogo.oa img{width:36px;height:36px;margin:-6px}
.justin .alogo.term svg.i{width:22px;height:22px}
.justin .t{flex:1 1 14rem;min-width:0;line-height:1.35}
.justin .t b{font-weight:700}
.justin .btn{min-height:44px;white-space:normal;text-align:center}
@media (max-width:599.98px){ .justin .btn{flex:1 1 100%;justify-content:center} }
"""
)
