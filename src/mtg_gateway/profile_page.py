"""The account-side screens of parity release 2 (mockup screens/30-account.js, 40-collection.js,
50-notifications.js): on Account the Archidekt profile (picture, username, bio), the email and
notification switches, the new-device default settings and the forms that stay on Archidekt; on
My collection the Visibility line; and the bell in the top bar with Archidekt's notifications.

Every change is the member's own press on the page: the same proposal an assistant makes
(decks.propose_action "profile", profile.py), applied at once with the browser as the client, as
the My lists page does. An assistant never applies one, and the picture never comes from an
assistant at all (profile.set_avatar: the page's own upload route only). Each form works without
scripts (a plain post and a reload with the notice); static/profile.js saves the switches and
drop-downs in place and checks the picture before it is sent. The bell's count is read after the
page has loaded (static/bell.js asks /notifications/count), never during a page render, and both
the count and the list are kept a short while per member so the bar never slows a page.
"""

from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import profile, ui
from .decks import DeckError, current_client
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    apply_own_press,
    browser_session,
    login_redirect,
    read_limited,
)
from .theme import esc, icon

PROFILE_TTL = 30.0  # seconds the profile read is kept for the pages (a press forgets it)
COUNT_TTL = 60.0  # the bell's count
LIST_TTL = 10.0  # the bell's list: Archidekt's own bell asks at most once every 10 s
PICTURE_BODY_LIMIT = profile.AVATAR_MAX_BYTES + 16_000  # the file plus the form's own fields
HIST = "A copy of your profile from just before is kept under History."
# the member's own text after a save: the mockup's notices
SAVED = "Saved on Archidekt."
NOTHING_CHANGED = "Nothing changed: your Archidekt profile already said that."
PICTURE_SAVED = "Picture saved on Archidekt and read back."
UNCONFIRMED = "Sent to Archidekt, but the read-back didn't confirm the change. See Result on the review page."
CARD_NOTICES = frozenset({"profile", "email", "defaults", "picture"})
# password, e-mail, Patreon and account deletion stay on Archidekt (R-085): one link, never a form here.
# Archidekt's saved scripts confirm its settings page accountSettings/[settingsName] but not the names,
# so the link opens Archidekt's home page
ON_ARCHIDEKT_URL = "https://archidekt.com/"
# the switches, in the mockup's order and words: (name, label, help, inverted)
EMAIL_ROWS = (
    ("contactable", "Other members can contact me", "Lets members message you through Archidekt.", False),
    ("newsletter", "Archidekt's newsletter", "Occasional email about the site.", False),
    ("comment_emails", "Email me when someone comments", "On your decks and your forum posts.", False),
    (
        "site_notifications",
        "Turn off Archidekt notifications",
        "Turns off Archidekt's notifications for your account.",
        True,
    ),
)
# the new-device defaults profile.PREFERENCES supports, in the mockup's order: (name, label,
# help, choices as (value, words) for a drop-down, or None for a switch)
PREF_ROWS: tuple[tuple[str, str, str, tuple[tuple[str, str], ...] | None], ...] = (
    (
        "deck_view",
        "Deck view",
        "",
        tuple((k, k.capitalize()) for k in profile.DECK_VIEWS),
    ),
    ("unique_search", "Search unique printings", "Show one result per card name in card search.", None),
    (
        "use_default_categories",
        "Use my default categories",
        "Put cards into their default categories when added to a deck.",
        None,
    ),
    ("search_game", "Search game", "", (("paper", "Paper"), ("mtgo", "MTGO"), ("arena", "Arena"))),
    ("show_custom_uploads", "Show custom uploads", "Show other members' custom card pictures.", None),
)
KIND_WORDS = {
    "ForumThread": "Forum thread",
    "Global": "Forum",
    "Sticky": "Forum",
    "Features": "Forum",
    "Deck": "Deck",
    "DeckThread": "Deck",
    "DeckUserEditorLink": "Deck editor",
    "News": "Archidekt",
    "Profile": "Profile",
    "Contest": "Contest",
}
_DECK_URL = re.compile(r"https://archidekt\.com/decks/(\d+)$")

CSS = """
.acc .pr-me{display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
.acc .pr-me .big,.pr-box .big{width:88px;height:88px;border-radius:50%;background:var(--surface-3);
  display:grid;place-items:center;font-size:2.2rem;font-weight:900;flex:none;overflow:hidden;color:var(--text)}
.pr-box .big img{width:100%;height:100%;object-fit:cover;display:block}
.acc .pr-me .ui-actions{margin:0}
.acc .ui-row label,.acc .ui-row .lab{font-weight:700}
.acc .ui-row .help{margin:.15rem 0 0}
.acc .ui-row.sw .ctl{justify-self:end}
.acc .ui-row.full{grid-template-columns:minmax(0,1fr)}
.acc .ui-row.full .ctl{grid-column:1;grid-row:auto}
.acc .ui-row .ctl .switch .sw-text{font-size:.95rem}
.acc .ui-row.sw .switch{margin:0;min-height:44px;font-weight:400}
.acc .ui-row .ctl input[type=text]{width:100%}
.acc .ui-row .ctl form{margin:0;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
/* the drop-down is the themed button (static/select.js); its native select stays off-screen */
.acc .ui-row .ctl form select:not(.msel-native),.acc .ui-row .ctl form .msel-btn{
  flex:1 1 8rem;width:auto;min-width:0;max-width:100%;margin:0}
.acc .pr-bio{min-height:7rem;width:100%;font:inherit;font-size:1rem;line-height:1.4;resize:vertical}
.acc .pr-hist{margin:.5rem 0 0}
.acc .pr-hist svg.i,.pr-box .pr-hist svg.i{width:15px;height:15px;vertical-align:-2px}
.acc .card .notice{margin:.5rem 0}
.pr-box .pr-me{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin:.75rem 0}
.pr-box .pr-file{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden}
.pr-box .pr-name{color:var(--text-muted);font-size:.9rem;margin:.3rem 0 0;overflow-wrap:anywhere}
@media (max-width:599.98px){.acc .ui-row{grid-template-columns:minmax(0,1fr)}
  .acc .ui-row.sw .ctl{justify-self:start}}
"""
COLLECTION_CSS = """
.col-vis{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem 1rem;margin:0 0 .9rem;padding:.6rem .8rem;
  border:1px solid var(--card-border);border-radius:var(--radius-panel);background:var(--surface)}
.col-vis .t{flex:1 1 14rem;min-width:0;font-size:.95rem}
.col-vis .t b{display:inline-flex;align-items:center;gap:.3rem;vertical-align:-3px}
.col-vis .t b svg.i{width:16px;height:16px}
.col-vis form{margin:0}
.col-vis .btn,.col-vis button{margin:0}
.col-vis .notice{flex:1 1 100%;margin:0}
.col-visbox .pr-hist svg.i{width:15px;height:15px;vertical-align:-2px}
"""


# -- the short per-member caches ---------------------------------------------------------------------

_PROFILE: dict[str, tuple[float, dict[str, Any]]] = {}
_COUNT: dict[str, tuple[float, int]] = {}
_LIST: dict[str, tuple[float, dict[str, Any]]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_ERROR: dict[str, str] = {}


def forget(sub: str) -> None:
    """A press changed the profile: the next page reads it again."""
    _PROFILE.pop(sub, None)


def forget_all(sub: str) -> None:
    for d in (_PROFILE, _COUNT, _LIST, _LAST_ERROR):
        d.pop(sub, None)  # type: ignore[arg-type]


def _lock(sub: str) -> asyncio.Lock:
    return _LOCKS.setdefault(sub, asyncio.Lock())


async def read_profile(decks: Any, sub: str) -> dict[str, Any]:
    """The profile as Archidekt answers it, kept PROFILE_TTL seconds for the pages."""
    hit = _PROFILE.get(sub)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    async with _lock(sub):
        hit = _PROFILE.get(sub)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        now = await profile.read(decks, sub)
        _PROFILE[sub] = (time.monotonic() + PROFILE_TTL, now)
        return now


async def profile_or_none(decks: Any, sub: str) -> dict[str, Any] | None:
    """The profile, or None when Archidekt can't be asked right now (the page says so)."""
    try:
        return await read_profile(decks, sub)
    except DeckError:
        return None


async def unseen_count(decks: Any, sub: str) -> int:
    hit = _COUNT.get(sub)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    uid = profile.user_id(decks, sub)
    n = int(await decks._call(sub, lambda token, *_: decks.client.notification_count(token, uid)))
    _COUNT[sub] = (time.monotonic() + COUNT_TTL, n)
    return n


async def notification_list(decks: Any, sub: str) -> dict[str, Any]:
    hit = _LIST.get(sub)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    got = await profile.notifications(decks, sub)
    _LIST[sub] = (time.monotonic() + LIST_TTL, got)
    # the site zeroes its badge once the list was read (Archidekt has no mark-as-read)
    _COUNT[sub] = (time.monotonic() + COUNT_TTL, 0)
    return got


# -- pieces ----------------------------------------------------------------------------------------------


def _hist(what: str = HIST) -> str:
    return f"<p class='muted small pr-hist'>{icon('history')} {esc(what)}</p>"


def _hidden(csrf: str | None, stamp: str) -> str:
    """What every press form carries: the CSRF token and the page's render stamp."""
    return (
        f"<input type='hidden' name='csrf' value='{esc(csrf or '')}'>"
        f"<input type='hidden' name='shown' value='{esc(stamp)}'>"
    )


def _row(label_html: str, help_text: str, ctl_html: str, *, cls: str = "") -> str:
    h = f"<p class='help'>{esc(help_text)}</p>" if help_text else ""
    return (
        f"<div class='ui-row{(' ' + cls) if cls else ''}'><p class='lab'>{label_html}</p>{h}"
        f"<div class='ctl'>{ctl_html}</div></div>"
    )


def _switch_form(
    csrf_in: str, *, action: str, hidden: dict[str, str], name: str, label: str, on: bool
) -> str:
    """One switch that saves when pressed (static/profile.js); without scripts its Save button."""
    hid = "".join(f"<input type='hidden' name='{esc(k)}' value='{esc(v)}'>" for k, v in hidden.items())
    fid = f"pr-{name}"
    return (
        f"<form method='post' action='/account/profile' data-saves>{csrf_in}"
        f"<input type='hidden' name='action' value='{esc(action)}'>{hid}"
        f"<input type='hidden' name='was' value='{'1' if on else '0'}'>"
        f"<label class='switch'><input id='{fid}' type='checkbox' name='on' value='1'"
        f"{' checked' if on else ''}>"
        f"<span class='track' aria-hidden='true'></span><span class='sw-text'>{esc(label)}</span></label>"
        "<noscript><button class='btn-plain inline'>Save</button></noscript></form>"
    )


def _select_form(
    csrf_in: str, *, name: str, label: str, choices: tuple[tuple[str, str], ...], cur: str | None
) -> str:
    fid = f"pr-{name}"
    opts = "".join(
        f"<option value='{esc(v)}'{' selected' if v == cur else ''}>{esc(w)}</option>" for v, w in choices
    )
    if cur is None:
        opts = "<option value='' selected disabled>Not set</option>" + opts
    return (
        f"<form method='post' action='/account/profile' data-saves>{csrf_in}"
        "<input type='hidden' name='action' value='preference'>"
        f"<input type='hidden' name='preference' value='{esc(name)}'>"
        f"<input type='hidden' name='was' value='{esc(cur or '')}'>"
        f"<select id='{fid}' name='value' aria-label='{esc(label)}'>{opts}</select>"
        "<noscript><button class='btn-plain inline'>Save</button></noscript></form>"
    )


def _initial(name: str) -> str:
    return (name.strip()[:1] or "?").upper()


def picture_box(csrf_in: str, username: str, *, open: bool, error: str = "") -> str:
    body = (
        "<p>Choose a JPEG or PNG up to 1 MB. The gateway checks the type and size, then sends it to "
        "Archidekt as your picture. Only you can do this, here on this page: an assistant can't change "
        "your picture.</p>"
        f"<form id='pr-pic-f' method='post' action='/account/picture' enctype='multipart/form-data'>{csrf_in}"
        "<div class='pr-me'><span class='big' id='pr-preview' aria-hidden='true'>"
        f"{esc(_initial(username))}</span>"
        "<div><input type='file' id='pr-file' name='picture' accept='image/jpeg,image/png' class='pr-file' "
        ">"
        f"<button type='button' class='btn-plain' id='pr-pick'>{icon('image')} Choose a picture…</button>"
        "<p class='pr-name' id='pr-filename' hidden></p>"
        "<noscript><p class='muted small'>Choose the file above, then press Use this picture.</p></noscript>"
        "</div></div>"
        + (
            f"<p class='notice error boxnote' id='pr-err' role='alert'>{esc(error)}</p>"
            if error
            else "<p class='notice error boxnote' id='pr-err' role='alert' hidden></p>"
        )
        + "</form>"
    )
    return ui.box(
        "picture",
        "Change your Archidekt profile picture",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Use this picture", kind="primary", form="pr-pic-f", icon_name="check", attrs="id='pr-use'"
            ),
        ],
        open=open,
        close_href="/account#profile",
        cls="pr-box",
    )


def account_cards(
    now: dict[str, Any] | None,
    csrf: str | None,
    *,
    stamp: str = "",
    ok: str = "",
    saved: str = "",
    error: str = "",
    error_in: str = "",
    values: dict[str, str] | None = None,
    open_box: str | None = None,
) -> str:
    """The four cards after Your Archidekt lists while Archidekt is linked. ``now`` is the profile
    as Archidekt answers it (None: it couldn't be read); ``ok``/``saved`` the notice a redirect
    carries; ``error``/``error_in`` Archidekt's refusal and the card it belongs to; ``values`` what
    was typed, kept in the fields after a refusal. ``stamp`` is the page's render stamp
    (pages.press_stamp): every form carries it as ``shown``, and the press is refused without it."""
    csrf_in = _hidden(csrf, stamp)
    v = values or {}
    if now is None:
        return (
            "<div class='card' id='profile'><h2>Archidekt profile</h2>"
            + ui.notice(
                "Archidekt did not answer, so your profile and settings can't be shown right now. "
                "Reload the page to try again.",
                "warn",
                role="status",
            )
            + "</div>"
        )
    me = profile.summary(now)
    username = str(v.get("username", me["username"]))
    bio = str(v.get("bio", me["bio"]))

    def note(card: str) -> str:
        if error and error_in == card:
            return ui.notice(error, "error", role="alert")
        if ok == card:
            if card == "profile":
                which = ", ".join(w for w in saved.split(",") if w in ("bio", "username"))
                text = f"Profile saved on Archidekt: {which}. Read back." if which else NOTHING_CHANGED
            elif card == "picture":
                text = PICTURE_SAVED
            else:
                text = SAVED
            return ui.notice(text, "ok", role="status")
        if ok == "unconfirmed" and card == "profile":
            return ui.notice(UNCONFIRMED, "warn", role="status")
        return ""

    prof = (
        f"<div class='card' id='profile'><h2>Archidekt profile</h2>{note('profile')}{note('picture')}"
        "<div class='ui-row full'><p class='lab'>Picture</p>"
        "<p class='help'>Your own JPEG or PNG up to 1 MB. Only you can change it, here.</p>"
        "<div class='ctl'><div class='pr-me'><span class='big' aria-hidden='true'>"
        f"{esc(_initial(me['username']))}"
        "</span><div class='ui-actions'>"
        + ui.box_opener("Upload a picture…", "/account?picture=1#profile", "picture", icon_name="image")
        + "</div></div></div></div>"
        f"<form method='post' action='/account/profile' id='pr-f'>{csrf_in}"
        "<input type='hidden' name='action' value='profile'>"
        + _row(
            "<label for='pr-un'>Username</label>",
            "Shown on your decks and comments on Archidekt. Archidekt says if a name is taken.",
            f"<input id='pr-un' type='text' name='username' value='{esc(username)}' maxlength='150' "
            "autocomplete='username' spellcheck='false'>",
        )
        + "<div class='ui-row full'><p class='lab'><label for='pr-bio'>Bio</label></p><div class='ctl'>"
        f"<textarea id='pr-bio' class='pr-bio' name='bio' rows='4' maxlength='{profile.BIO_MAX}'>{esc(bio)}"
        "</textarea></div></div>"
        f"<div class='ui-actions'><button class='btn-plain'>{icon('check')} Save profile</button></div>"
        "</form>"
        + _hist()
        + picture_box(
            csrf_in, me["username"], open=open_box == "picture", error=error if error_in == "picture" else ""
        )
        + "</div>"
    )
    email_rows = []
    for name, label, help_text, inverted in EMAIL_ROWS:
        got = me["email"].get(name)
        on = (not got) if inverted else bool(got)
        email_rows.append(
            _row(
                f"<label for='pr-{name}'>{esc(label)}</label>",
                help_text,
                _switch_form(csrf_in, action="email", hidden={"switch": name}, name=name, label=label, on=on),
                cls="sw",
            )
        )
    email = (
        f"<div class='card' id='email'><h2>Email and notifications on Archidekt</h2>{note('email')}"
        + "".join(email_rows)
        + "<p class='muted small'>Each switch saves to Archidekt when you press it.</p></div>"
    )
    pref_rows = []
    for name, label, help_text, choices in PREF_ROWS:
        cur = me["preferences"].get(name)
        if choices is None:
            ctl = _switch_form(
                csrf_in,
                action="preference",
                hidden={"preference": name},
                name=name,
                label=label,
                on=cur == "on",
            )
            pref_rows.append(_row(f"<label for='pr-{name}'>{esc(label)}</label>", help_text, ctl, cls="sw"))
        else:
            ctl = _select_form(csrf_in, name=name, label=label, choices=choices, cur=cur)
            pref_rows.append(_row(f"<label for='pr-{name}'>{esc(label)}</label>", help_text, ctl))
    defaults = (
        "<div class='card' id='defaults'><h2>New device default settings</h2>"
        "<p class='muted small'>What Archidekt starts with on a device you haven't set up yet. Each choice "
        f"saves when you change it.</p>{note('defaults')}" + "".join(pref_rows) + "</div>"
    )
    only = (
        "<div class='card' id='archidekt-only'><h2>On Archidekt only</h2>"
        "<p>Your password, e-mail, Patreon link and account deletion are changed on Archidekt's own site.</p>"
        f"<p><a class='btn btn-plain' href='{ON_ARCHIDEKT_URL}' target='_blank' rel='noopener noreferrer'>"
        f"{icon('external')} Open Archidekt</a></p></div>"
    )
    return prof + email + defaults + only


def collection_visibility(
    now: dict[str, Any] | None,
    csrf: str | None,
    sub: str,
    *,
    stamp: str = "",
    open_box: bool = False,
    ok: str = "",
) -> str:
    """The Visibility line under My collection's title, and the "are you sure" box behind Make it
    public…; nothing when the profile couldn't be read."""
    if now is None:
        return ""
    pub = (now.get("profile") or {}).get("is_collection_public") is True
    csrf_in = _hidden(csrf, stamp)
    hidden = (
        f"{csrf_in}<input type='hidden' name='action' value='collection_public'>"
        "<input type='hidden' name='next' value='/collection'>"
    )
    if pub:
        words = (
            "<b>"
            + icon("eye")
            + " Public</b> <span class='muted'>· anyone can see it on your Archidekt profile</span>"
        )
        act = (
            f"<form method='post' action='/account/profile'>{hidden}<input type='hidden' name='on' value='0'>"
            f"<button class='btn-plain'>{icon('eye-off')} Make it private</button></form>"
        )
    else:
        words = "<b>" + icon("eye-off") + " Private</b> <span class='muted'>· only you can see it</span>"
        act = ui.box_opener("Make it public…", "/collection?visibility=1", "col-vis", icon_name="eye")
    err = _LAST_ERROR.pop(sub, "")
    strip = ""
    if err:
        strip = ui.notice(err, "error", role="alert")
    elif ok == "public":
        strip = ui.notice("Your collection is public on Archidekt.", "ok", role="status")
    elif ok == "private":
        strip = ui.notice("Your collection is private on Archidekt.", "ok", role="status")
    elif ok == "unconfirmed":
        strip = ui.notice(UNCONFIRMED, "warn", role="status")
    box = ui.box(
        "col-vis",
        "Make your collection public?",
        f"<form id='col-vis-f' method='post' action='/account/profile'>{hidden}"
        "<input type='hidden' name='on' value='1'><input type='hidden' name='confirmed' value='1'>"
        "<p>Anyone can then see every card you own, with finish and condition, on your Archidekt profile. "
        "You can make it private again at any time, with one press.</p>"
        + _hist("Your choice from just before is kept under History, and the result is read back to check.")
        + "</form>",
        actions=[
            ui.box_button("Keep it private", cancel=True),
            ui.box_button("Make it public", kind="primary", form="col-vis-f", icon_name="eye"),
        ],
        open=open_box and not pub,
        close_href="/collection",
        cls="col-visbox",
    )
    return f"<div class='col-vis' id='visibility'><span class='t'>{words}</span>{act}{strip}</div>{box}"


def _gateway_url(url: str | None) -> str | None:
    """A deck's notification opens the gateway's own deck page; everything else stays on Archidekt."""
    if not url:
        return None
    m = _DECK_URL.match(url)
    return f"/decks/{m.group(1)}" if m else url


def notifications_html(got: dict[str, Any], *, linked: bool = True) -> str:
    """The bell's list: each row the notification's text and kind, tinted while unseen, a link to
    its target where Archidekt names one (the gateway's deck page for a deck)."""
    if not linked:
        return (
            "<p class='muted'>Link your Archidekt account on the Account page to see its notifications "
            "here.</p>"
        )
    rows = got.get("notifications") or []
    if not rows:
        return "<p class='muted'>Nothing new on Archidekt.</p>"
    out = []
    for n in rows:
        kind = KIND_WORDS.get(str(n.get("kind") or ""), "Archidekt")
        cls = "bell-row" + (" new" if not n.get("seen") else "")
        inner = (
            f"<i aria-hidden='true'>{esc(kind[:1])}</i><span class='t'>{esc(n.get('text') or '')}"
            f"<span class='s'>{esc(kind)}{'' if n.get('seen') else ' · new'}</span></span>"
        )
        href = _gateway_url(n.get("url"))
        if href and href.startswith("/"):
            out.append(f"<li><a class='{cls}' href='{esc(href)}'>{inner}</a></li>")
        elif href:
            out.append(
                f"<li><a class='{cls}' href='{esc(href)}' target='_blank' rel='noopener noreferrer'>{inner}"
                "</a></li>"
            )
        else:
            out.append(f"<li><div class='{cls}'>{inner}</div></li>")
    return f"<ul class='bell-list' role='list'>{''.join(out)}</ul>"


BELL_NOTE = (
    "The count clears when you open this list. The items stay here for as long as Archidekt keeps them. "
    "Turn them off under Account › Email and notifications."
)


def home_widget() -> str:
    """Home's On Archidekt widget: the count comes after the page has loaded (static/bell.js)."""
    return (
        f"<div class='wdg' data-bell-widget><a class='lab' href='/notifications'>{icon('bell')}On Archidekt"
        "</a>"
        "<span class='big'><span data-bell-big>…</span></span>"
        "<span class='det'><span data-bell-det>Archidekt's notifications</span></span>"
        "<span class='go'><a class='btn btn-plain' href='/notifications' data-bell-open>Open</a></span></div>"
    )


# -- the routes --------------------------------------------------------------------------------------------


def add_profile_routes(server: Any, state: Any, h: SimpleNamespace) -> None:
    decks = h.decks
    page = h.page
    check_csrf = h.check_csrf
    s = h.s

    def wants_json(request: Request) -> bool:
        return "application/json" in (request.headers.get("accept") or "")

    def expired(request: Request, sub: str, sid: str | None) -> Response:
        if wants_json(request):
            return JSONResponse(
                {"ok": False, "message": "This form expired. Reload and try again."}, status_code=403
            )
        return page("Account", h.EXPIRED, sub=sub, sid=sid, status=403)

    async def press(sub: str, shown: str | None, **params: Any) -> dict[str, Any]:
        """The member's own press: the proposal an assistant would make, applied at once by the
        review page's own path (pages.apply_own_press: the signed-in session, the page's CSRF
        token and its render stamp ``shown``), the one way an account action is ever applied."""
        current_client.set(BROWSER_CLIENT_ID)
        p = await decks.propose_action(sub, "profile", **params)
        try:
            return await apply_own_press(state, sub, p["proposal_id"], shown)
        finally:
            forget(sub)

    def verified(out: dict[str, Any]) -> bool | None:
        result = out.get("result") if isinstance(out.get("result"), dict) else {}
        return result.get("verified")

    async def account_again(
        request: Request, sub: str, sid: str | None, *, error: str, error_in: str, values: dict[str, str]
    ) -> Response:
        """The Account page drawn again with Archidekt's refusal in the card it belongs to."""
        from .pages import ACCOUNT_CSP, ACCOUNT_CSS, account_html

        body = await account_html(
            state,
            sub,
            _csrf(s, sid),
            open_box="picture" if error_in == "picture" else None,
            profile_error=error,
            profile_error_in=error_in,
            profile_values=values,
        )
        return page(
            "Account",
            body,
            sub=sub,
            sid=sid,
            status=400,
            csp=ACCOUNT_CSP,
            extra_css=ACCOUNT_CSS + CSS,
            extra_scripts=("pages.js", "profile.js"),
        )

    @server.custom_route("/account/profile", methods=["POST"], include_in_schema=False)
    async def profile_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/account")
        raw = await read_limited(request, 16_384)
        fields = parse_qs((raw or b"").decode("utf-8", "replace"), keep_blank_values=True)
        data = {k: v[0] for k, v in fields.items()}
        if not check_csrf(sid, data):
            return expired(request, sub, sid)
        action = data.get("action") or ""
        on = data.get("on") == "1"
        shown = data.get("shown")
        back = "/collection" if data.get("next") == "/collection" else "/account"
        card = {"profile": "profile", "email": "email", "preference": "defaults"}.get(action, "")
        try:
            if action == "profile":
                me = profile.summary(await read_profile(decks, sub))
                saved = []
                unconfirmed = False
                username = (data.get("username") or "").strip()
                bio = data.get("bio") or ""
                if bio.strip() != me["bio"]:
                    out = await press(sub, shown, field="bio", bio=bio)
                    saved.append("bio")
                    unconfirmed = unconfirmed or verified(out) is False
                if username and username != me["username"]:
                    out = await press(sub, shown, field="username", username=username)
                    saved.append("username")
                    unconfirmed = unconfirmed or verified(out) is False
                code = "ok=unconfirmed" if unconfirmed else f"ok=profile&saved={','.join(saved)}"
                return RedirectResponse(f"/account?{code}#profile", status_code=303)
            if action == "email":
                switch = data.get("switch") or ""
                inverted = switch == "site_notifications"
                out = await press(sub, shown, field="email", switch=switch, on=(not on) if inverted else on)
            elif action == "preference":
                # a drop-down sends its value; a switch sends on=1 or 0
                value = data.get("value") if "value" in data else ("on" if on else "off")
                out = await press(
                    sub, shown, field="preference", preference=data.get("preference") or "", value=value or ""
                )
            elif action == "collection_public":
                if on and data.get("confirmed") != "1":
                    return RedirectResponse("/collection?visibility=1", status_code=303)
                out = await press(sub, shown, field="collection_public", on=on)
                code = "unconfirmed" if verified(out) is False else ("public" if on else "private")
                return RedirectResponse(f"/collection?ok={code}#visibility", status_code=303)
            else:
                raise DeckError("invalid", "That is not something this page can do.")
        except DeckError as exc:
            if wants_json(request):
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
            if back == "/collection":
                _LAST_ERROR[sub] = str(exc)
                return RedirectResponse("/collection#visibility", status_code=303)
            values = {k: data.get(k, "") for k in ("username", "bio")} if action == "profile" else {}
            return await account_again(
                request, sub, sid, error=str(exc), error_in=card or "profile", values=values
            )
        text = UNCONFIRMED if verified(out) is False else SAVED
        if wants_json(request):
            return JSONResponse({"ok": True, "notice": text, "verified": verified(out)})
        code = "ok=unconfirmed" if verified(out) is False else f"ok={card}"
        return RedirectResponse(f"/account?{code}#{card}", status_code=303)

    @server.custom_route("/account/picture", methods=["POST"], include_in_schema=False)
    async def picture_post(request: Request) -> Response:
        """The member's own profile picture: checked here (type, size) and sent as Archidekt's box
        sends it (profile.set_avatar). The page's own route: never an assistant."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/account")
        declared = request.headers.get("content-length", "")
        too_big = declared.isdigit() and int(declared) > PICTURE_BODY_LIMIT
        data: dict[str, str] = {}
        raw = b""
        if not too_big:
            try:
                posted = await request.form(max_files=1, max_fields=10)
            except Exception:  # noqa: BLE001  # a body that is not a form
                posted = None
            if posted is not None:
                for k, v in posted.multi_items():
                    if hasattr(v, "read"):
                        raw = await v.read()
                        if len(raw) > PICTURE_BODY_LIMIT:
                            raw = raw[: PICTURE_BODY_LIMIT + 1]
                    else:
                        data[k] = str(v)
            if not check_csrf(sid, data):
                return expired(request, sub, sid)
        try:
            if too_big:
                raise DeckError(
                    "invalid", "The picture is larger than 1 MB, Archidekt's limit. Choose a smaller one."
                )
            current_client.set(BROWSER_CLIENT_ID)
            out = await profile.set_avatar(decks, sub, raw)
        except DeckError as exc:
            forget(sub)
            if wants_json(request):
                return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
            return await account_again(request, sub, sid, error=str(exc), error_in="picture", values={})
        forget(sub)
        if wants_json(request):
            return JSONResponse({"ok": True, "notice": PICTURE_SAVED, "verified": out.get("verified")})
        return RedirectResponse("/account?ok=picture#profile", status_code=303)

    # -- the bell ---------------------------------------------------------------------------------

    def linked(sub: str) -> bool:
        return bool(decks.status(sub)["linked"])

    @server.custom_route("/notifications/count", methods=["GET"], include_in_schema=False)
    async def count(request: Request) -> Response:
        """Archidekt's unseen count for the bell, asked after the page has loaded (static/bell.js)."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False}, status_code=401, headers={"Cache-Control": "no-store"})
        n = 0
        if linked(sub):
            try:
                n = await unseen_count(decks, sub)
            except DeckError:
                n = 0
        return JSONResponse({"ok": True, "unseen": n}, headers={"Cache-Control": "no-store"})

    @server.custom_route("/notifications", methods=["GET"], include_in_schema=False)
    async def notifications(request: Request) -> Response:
        """The list: as a box on the page with scripts (JSON), as a small page without them."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/notifications")
        is_linked = linked(sub)
        got: dict[str, Any] = {"notifications": [], "unseen": 0}
        error = ""
        if is_linked:
            try:
                got = await notification_list(decks, sub)
            except DeckError as exc:
                error = str(exc)
        body = ui.notice(error, "error", role="alert") if error else notifications_html(got, linked=is_linked)
        if wants_json(request):
            return JSONResponse(
                {"ok": not error, "unseen": int(got.get("unseen") or 0), "html": body, "message": error},
                headers={"Cache-Control": "no-store"},
            )
        badge = f" <span class='bell-badge'>{got['unseen']} new</span>" if got.get("unseen") else ""
        return page(
            "Notifications",
            f"<div class='bell-page'><div class='ui-head'><h1>Notifications{badge}</h1></div>"
            "<p class='ui-sub'>What Archidekt's bell holds for you.</p>"
            f"<div class='card'>{body}<p class='muted small bell-foot'>{esc(BELL_NOTE)}</p>"
            "<div class='ui-actions'><a class='btn btn-plain' href='/account#email'>"
            f"{icon('settings')} Settings</a>"
            "</div></div></div>",
            sub=sub,
            sid=sid,
            current="/notifications",
            heading=False,
        )
