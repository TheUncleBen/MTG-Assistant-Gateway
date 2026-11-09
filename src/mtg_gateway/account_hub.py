"""The Account page your picture opens (the owner, 2026-10-25): a settings list in groups, as a phone's
Settings app draws it, balanced into two columns on a wide screen. Each row opens a page, or (Theme,
Layout) folds open its choices in place. The account's own cards (approval mode, Archidekt, apps,
notifications, your data) are one row in, under Account details (/account/details)."""

from __future__ import annotations

import html
from typing import Any

from . import modes, ui
from .theme import app_version, current_layout, current_path, current_theme, display_name, icon, in_app

# what a sign-out clears on the device (Clear-Site-Data "storage", pages.logout_post): said on
# the sign-out page and under this page's Sign out alike (audit F-14)
DRAFTS_CLEARED = "Unsent drafts, scan drafts and other unsaved work kept on this device are cleared too."

DETAILS = "/account/details"

# What a row is: (label, icon, href or "" for a fold, the value shown at its end, the fold's body,
# the app method a button row calls, and for a line the app fills in: (its answer, the words) pairs).
Row = tuple[str, str, str, str, str, str, tuple[tuple[str, str], ...]]


def _row(
    label: str,
    ic: str,
    href: str = "",
    value: str = "",
    fold: str = "",
    native: str = "",
    states: tuple[tuple[str, str], ...] = (),
) -> Row:
    return (label, ic, href, value, fold, native, states)


def _row_html(r: Row) -> str:
    label, ic, href, value, fold, native, states = r
    val = f"<span class='val'>{html.escape(value)}</span>" if value else ""
    inner = f"{icon(ic)}<span class='lbl'>{html.escape(label)}</span>{val}"
    if states:
        # a line the app's ``native`` method words (feedback.js): hidden until the app answers, so
        # an app without that method shows no empty row
        vals = "".join(
            f"<span class='val' data-native-show='{k}'>{html.escape(words)}</span>" for k, words in states
        )
        return f"<div class='yrow' data-native-state='{native}' data-native-row hidden>{inner}{vals}</div>"
    if fold:
        return (
            f"<details class='yrow'><summary>{inner}<span class='ch'>{icon('chev')}</span></summary>"
            f"<div class='yfold'>{fold}</div></details>"
        )
    if native:
        # the app's own actions (feedback.js calls window.MtgNative; hidden in an app without it)
        return f"<button type='button' class='yrow' data-native='{native}'>{inner}</button>"
    return f"<a class='yrow' href='{href}'>{inner}<span class='ch'>{icon('chev')}</span></a>"


def _choices(action: str, name: str, csrf: str, options: tuple[tuple[str, str], ...], now: str) -> str:
    """Theme's or Layout's choices: one button each, the current one ticked."""
    buttons = "".join(
        f"<button name='{name}' value='{v}' aria-pressed='{'true' if v == now else 'false'}'"
        f"{' class=on' if v == now else ''}>"
        f"{icon('check') if v == now else '<span class=i></span>'}{html.escape(label)}</button>"
        for v, label in options
    )
    return (
        f"<form method='post' action='{action}'>"
        f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
        f"<input type='hidden' name='next' value='{html.escape(current_path())}'>{buttons}</form>"
    )


THEMES = (("light", "Light"), ("dark", "Dark"), ("system", "System"))
# The app's answer to signInReturn (BrowserSignIn.returnStatus), in words
SIGN_IN_RETURN = (("auto", "Comes back by itself"), ("tap", "May need a tap in the browser"))
LAYOUTS = (("auto", "Fit the screen"), ("desktop", "Desktop layout"))


def groups(csrf: str, *, admin: bool, app: bool) -> list[tuple[int, str, list[Row]]]:
    """The groups in their order (their place, title, rows): the website shows Android app and no
    This app; the app the other way round. Admin only for admins."""
    theme, layout = current_theme(), current_layout()
    look = [
        _row(
            "Theme",
            "moon" if theme == "dark" else "sun",
            value=dict(THEMES)[theme],
            fold=_choices("/theme", "theme", csrf, THEMES, theme),
        ),
        _row(
            "Layout",
            "grid",
            value=dict(LAYOUTS)[layout],
            fold=_choices("/layout", "layout", csrf, LAYOUTS, layout),
        ),
    ]
    out = [
        (
            0,
            "Your account",
            [
                _row("Account details", "account", DETAILS),
                # the lists live in Binder (mockup screen 8); the row is the way there from here
                _row("Lists", "list", "/collection/lists", value="Binder"),
                _row("My activity", "activity", "/activity"),
            ],
        ),
        (1, "Look", look),
        (
            2,
            "Assistants and apps",
            [
                # each assistant's own approval mode, three taps from any page (0.7.46 re-audit
                # QG-14): avatar › Assistants › the assistant
                _row("Assistants", "proposals", "/account/assistants?from=account"),
                _row("Guide", "guide", "/guide"),
                _row("Assistant skill", "report", "/install?for=skill"),
                *([] if app else [_row("Android app", "download", "/install?for=android")]),
                *([_row("Admin", "shield", "/admin")] if admin else []),
            ],
        ),
    ]
    if app:
        out.append(
            (
                3,
                "This app",
                [
                    _row("Scan with the app's camera", "camera", native="openCamera"),
                    _row("Reload", "refresh", native="reload"),
                    _row("Open a copied link", "link", native="openCopiedLink"),
                    _row("Change gateway", "swap", native="changeGateway"),
                    # whether the browser sign-in comes back by itself (an Auth Tab browser on the
                    # phone) or may wait for a tap on the gateway's page (a Custom Tab)
                    _row("Sign-in", "login", native="signInReturn", states=SIGN_IN_RETURN),
                    # the installed version, from the app's user agent
                    _row("Check for app updates", "download", value=app_version(), native="checkForUpdates"),
                ],
            )
        )
    return out


def _group_html(g: tuple[int, str, list[Row]]) -> str:
    return (
        f"<section class='ygrp'><h2>{html.escape(g[1])}</h2>"
        f"<div class='rows'>{''.join(_row_html(r) for r in g[2])}</div></section>"
    )


def hub_html(state: Any, sub: str, csrf: str, *, admin: bool) -> str:
    user = state.db.get_user(sub) or {}
    info = state.decks.status(sub)
    linked = (
        f"Archidekt: {html.escape(info.get('archidekt_username') or 'linked')}"
        if info.get("linked")
        else "Archidekt not linked"
    )
    mode = html.escape(modes.MODE_LABELS.get(state.decks.mode_of(sub), ""))
    app = in_app()
    sub_line = ui.facts([linked, f"Default approval: {mode}" if mode else ""], html=True)
    # one centred column of grouped lists on every screen, as iOS Settings draws them: two columns
    # side by side left a hole under the shorter one on a desktop (Ben, 0.7.46 checklist)
    gs = sorted(groups(csrf, admin=admin, app=app))
    return (
        "<div class='yhub'>"
        "<div class='yme'><img class='av' src='/account/avatar' alt='' width='56' height='56'>"
        f"<span class='who'><b>{html.escape(display_name(user) or 'You')}</b>"
        f"<span class='sub'>{sub_line}</span></span></div>"
        "<div class='ycols'><div class='ycol'>"
        + "".join(_group_html(g) for g in gs)
        + "</div></div>"
        + "<form class='ysignout' method='post' action='/logout'>"
        f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
        "<button type='submit' class='btn' data-busy-text='Signing out…'>"
        f"{icon('x')}<span>Sign out</span></button>"
        f"<p class='muted small'>{html.escape(DRAFTS_CLEARED)}</p>"
        "</form></div>"
    )


HUB_CSS = """
.yhub{max-width:42rem;margin:0 auto}
.yhub .yme{display:flex;align-items:center;gap:1rem;padding:.9rem 1rem;margin:0 0 1.25rem;border-radius:14px;
  background:var(--surface);border:1px solid var(--border-soft);color:var(--text);text-decoration:none}
.yhub .yme img.av{width:56px;height:56px;border-radius:50%;object-fit:cover;flex:none}
/* the words go under the picture when they have less than 12em beside it (200% text on a phone) */
.yhub .yme{flex-wrap:wrap}
.yhub .yme .who{display:flex;flex-direction:column;gap:.2rem;min-width:0;flex:1 1 12em}
.yhub .yme b{font-size:1.15rem;overflow-wrap:break-word}
.yhub .yme .sub{color:var(--text-muted);font-size:.9rem;overflow-wrap:anywhere}
.ycols{display:grid;grid-template-columns:minmax(0,1fr);gap:0;align-items:start}
.ycol{display:flex;flex-direction:column;min-width:0}
.ygrp{margin:0 0 1.25rem}
/* a group with no row left (This app in an app without the bridge, or an older one) shows no
   heading over nothing (audit D16); feedback.js hides the rows an app can't answer */
.ygrp:not(:has(.yrow:not([hidden]))){display:none}
.ygrp h2{margin:0 0 .4rem .9rem;font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
  color:var(--menu-head)}
.ygrp .rows{background:var(--surface);border:1px solid var(--border-soft);border-radius:14px;overflow:hidden}
.ygrp .yrow{display:flex;align-items:center;gap:.8rem;width:100%;min-height:50px;margin:0;padding:0 1rem;
  border:0;border-radius:0;background:transparent;color:var(--text);text-decoration:none;font:inherit;
  font-weight:400;text-align:left;cursor:pointer;justify-content:flex-start}
.ygrp .rows > * + *{border-top:1px solid var(--border-soft)}
.ygrp .yrow > svg.i,.ygrp summary > svg.i{width:20px;height:20px;color:var(--orange-text)}
/* the label and the value (“Fit the screen”) each wrap by word at large text, neither squeezed
   under its longest word, so one never runs over the other */
.ygrp .yrow .lbl{flex:1 1 auto}
.ygrp .yrow .val{flex:0 1 auto;text-align:right;color:var(--text-muted);font-size:.92rem}
.ygrp .ch svg.i{width:16px;height:16px;color:var(--text-muted)}
.ygrp div.yrow{cursor:default}
.ygrp .yrow[hidden]{display:none}
.ygrp details.yrow{display:block;padding:0;min-height:0}
.ygrp details.yrow > summary{display:flex;align-items:center;gap:.8rem;min-height:50px;padding:0 1rem;
  list-style:none;cursor:pointer}
.ygrp details.yrow > summary::-webkit-details-marker{display:none}
.ygrp a.yrow .ch svg.i{transform:rotate(-90deg)}  /* opens a page: points on */
.ygrp details.yrow[open] > summary .ch svg.i{transform:rotate(180deg)}  /* folds open in place */
.ygrp .yfold form{display:flex;flex-direction:column;margin:0;padding:0 0 .4rem 2.8rem}
.ygrp .yfold button{display:flex;align-items:center;gap:.6rem;min-height:44px;margin:0;padding:0 1rem 0 0;
  border:0;background:transparent;color:var(--text);font:inherit;justify-content:flex-start;text-align:left}
.ygrp .yfold button svg.i,.ygrp .yfold button .i{width:18px;height:18px;flex:none;color:var(--orange-text)}
.ygrp .yfold button.on{font-weight:700}
/* large text on a narrow phone (the group under about 20 em of its own text: 200% at 375 px): a
   row is a grid of its icon, its words and its chevron, the value on a line under the label, so
   the icon never sits above the label and the chevron never drops to the left (independent
   review 2, L11; the Assistants and Binder › Lists rows the same) */
.ygrp .rows{container-type:inline-size}
@container (max-width:20em){
  .ygrp .rows > .yrow:not(details),.ygrp .rows > details.yrow > summary{display:grid;
    grid-template-columns:auto minmax(0,1fr) auto;column-gap:.45rem;row-gap:.1rem;align-items:center;
    padding:.45rem .4rem .45rem .5rem}  /* the words get the room the 1 rem sides took */
  .ygrp .rows > .yrow > svg.i,.ygrp .rows > details.yrow > summary > svg.i,
  .ygrp .rows > .yrow > .alogo,.ygrp .rows > .yrow > .radio{grid-column:1;grid-row:1 / span 2}
  .ygrp .rows .yrow .lbl{grid-column:2;grid-row:1}
  .ygrp .rows .yrow .val,.ygrp .rows .yrow.app .val{grid-column:2;grid-row:2;justify-self:start;
    text-align:left;margin:0;max-width:none}
  .ygrp .rows .yrow .ch,.ygrp .rows .yrow > details.dd{grid-column:3;grid-row:1 / span 2}
  .ygrp .rows .yrow .ch{margin-right:-.2rem}
}
@media (hover:hover) and (pointer:fine){
  .ygrp .yrow{min-height:44px} .ygrp details.yrow > summary{min-height:44px}
  .ygrp a.yrow:hover,.ygrp button.yrow:hover,.ygrp details.yrow > summary:hover,
  .ygrp .yfold button:hover{background:var(--surface-2)} }
/* Sign out: a real button at the foot, its icon and words centred together, with the note on what
   it clears on its own line under it (beside the button, the note ran out of a 320 px window) */
.ysignout{display:flex;flex-direction:column;align-items:stretch;gap:.5rem;margin:.5rem 0 1rem}
.ysignout > p{margin:0 .9rem;max-width:100%}
.ysignout .btn{display:flex;align-items:center;justify-content:center;gap:.5rem;min-height:50px;width:100%;
  margin:0;padding:0 1.5rem;border-radius:14px;background:var(--surface);
  border:1px solid var(--border);color:var(--danger-text);font-weight:700;line-height:1.2}
.ysignout .btn svg.i{width:18px;height:18px}
.ysignout .btn:hover{background:var(--surface-2);color:var(--danger-text)}
"""
