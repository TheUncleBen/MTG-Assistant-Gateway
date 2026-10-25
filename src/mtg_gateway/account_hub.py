"""The Account page your picture opens (Ben, 2026-10-25): a settings list in groups, as a phone's
Settings app draws it, balanced into two columns on a wide screen. Each row opens a page, or (Theme,
Layout) folds open its choices in place. The account's own cards (approval mode, Archidekt, apps,
notifications, your data) are one row in, under Account details (/account/details)."""

from __future__ import annotations

import html
from typing import Any

from . import modes
from .theme import app_version, current_layout, current_path, current_theme, display_name, icon, in_app

DETAILS = "/account/details"

# What a row is: (label, icon, href or "" for a fold, the value shown at its end, the fold's body,
# the app method a button row calls).
Row = tuple[str, str, str, str, str, str]


def _row(label: str, ic: str, href: str = "", value: str = "", fold: str = "", native: str = "") -> Row:
    return (label, ic, href, value, fold, native)


def _row_html(r: Row) -> str:
    label, ic, href, value, fold, native = r
    val = f"<span class='val'>{html.escape(value)}</span>" if value else ""
    inner = f"{icon(ic)}<span class='lbl'>{html.escape(label)}</span>{val}"
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
                _row("My activity", "activity", "/activity"),
                _row("Forum", "comment", "/forum"),
            ],
        ),
        (1, "Look", look),
        (
            2,
            "Assistants and apps",
            [
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
                    # the installed version, from the app's user agent
                    _row("Check for app updates", "download", value=app_version(), native="checkForUpdates"),
                ],
            )
        )
    return out


def balance(gs: list[tuple[int, str, list[Row]]]) -> tuple[list[list[Any]], list[Any]]:
    """Two even columns and, with an odd number of groups, the smallest one centred under them
    (a member on the website: Your account beside Assistants and apps, Look under both). The
    largest group goes first into the shorter column; each column keeps the groups' order, and
    Your account is always at the top left."""
    rest = list(gs)
    lone: list[Any] = []
    if len(rest) % 2:
        small = min(rest, key=lambda g: (len(g[2]), -g[0]))
        rest.remove(small)
        lone = [small]
    cols: list[list[Any]] = [[], []]
    for g in sorted(rest, key=lambda g: (-len(g[2]), g[0])):
        h = [sum(len(x[2]) + 1 for x in c) for c in cols]
        cols[0 if h[0] <= h[1] else 1].append(g)
    cols = [sorted(c) for c in cols]
    if cols[1] and cols[1][0][0] == 0:
        cols.reverse()
    return cols, lone


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
    cols, lone = balance(groups(csrf, admin=admin, app=app))
    return (
        "<div class='yhub'>"
        "<div class='yme'><img class='av' src='/account/avatar' alt='' width='56' height='56'>"
        f"<span class='who'><b>{html.escape(display_name(user) or 'You')}</b>"
        f"<span class='sub'>{linked}{f' · Approval: {mode}' if mode else ''}</span></span></div>"
        "<div class='ycols'>"
        + "".join(f"<div class='ycol'>{''.join(_group_html(g) for g in c)}</div>" for c in cols if c)
        + "</div>"
        + "".join(f"<div class='ylone'>{_group_html(g)}</div>" for g in lone)
        + "<form class='ysignout' method='post' action='/logout'>"
        f"<input type='hidden' name='csrf' value='{html.escape(csrf)}'>"
        "<button type='submit' class='btn' data-busy-text='Signing out…'>"
        f"{icon('x')}<span>Sign out</span></button>"
        "</form></div>"
    )


HUB_CSS = """
.yhub{max-width:62rem;margin:0 auto}
.yhub .yme{display:flex;align-items:center;gap:1rem;padding:.9rem 1rem;margin:0 0 1.25rem;border-radius:14px;
  background:var(--surface);border:1px solid var(--border-soft);color:var(--text);text-decoration:none}
.yhub .yme img.av{width:56px;height:56px;border-radius:50%;object-fit:cover;flex:none}
.yhub .yme .who{display:flex;flex-direction:column;gap:.2rem;min-width:0;flex:1}
.yhub .yme b{font-size:1.15rem;overflow-wrap:anywhere}
.yhub .yme .sub{color:var(--text-muted);font-size:.9rem;overflow-wrap:anywhere}
.ycols{display:grid;gap:0 1.5rem;align-items:start}
@media (min-width:700px){ .ycols{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .ylone{max-width:calc(50% - .75rem);margin:0 auto} }
.ycol{display:flex;flex-direction:column;min-width:0}
.ygrp{margin:0 0 1.25rem}
.ygrp h2{margin:0 0 .4rem .9rem;font-size:.8rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;
  color:var(--menu-head)}
.ygrp .rows{background:var(--surface);border:1px solid var(--border-soft);border-radius:14px;overflow:hidden}
.ygrp .yrow{display:flex;align-items:center;gap:.8rem;width:100%;min-height:50px;margin:0;padding:0 1rem;
  border:0;border-radius:0;background:transparent;color:var(--text);text-decoration:none;font:inherit;
  font-weight:400;text-align:left;cursor:pointer;justify-content:flex-start}
.ygrp .rows > * + *{border-top:1px solid var(--border-soft)}
.ygrp .yrow > svg.i,.ygrp summary > svg.i{width:20px;height:20px;color:var(--orange-text)}
.ygrp .yrow .lbl{flex:1;min-width:0}
.ygrp .yrow .val{color:var(--text-muted);font-size:.92rem}
.ygrp .ch svg.i{width:16px;height:16px;color:var(--text-muted)}
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
@media (hover:hover) and (pointer:fine){
  .ygrp .yrow{min-height:44px} .ygrp details.yrow > summary{min-height:44px}
  .ygrp a.yrow:hover,.ygrp button.yrow:hover,.ygrp details.yrow > summary:hover,
  .ygrp .yfold button:hover{background:var(--surface-2)} }
/* Sign out: a real button, alone in the middle at the foot, its icon and words centred together */
.ysignout{display:flex;justify-content:center;margin:.5rem 0 1rem}
.ysignout .btn{display:inline-flex;align-items:center;justify-content:center;gap:.5rem;min-height:48px;
  min-width:min(16rem,100%);padding:0 1.5rem;border-radius:14px;background:var(--surface);
  border:1px solid var(--border);color:var(--danger-text);font-weight:700;line-height:1}
.ysignout .btn svg.i{width:18px;height:18px}
.ysignout .btn:hover{background:var(--surface-2);color:var(--danger-text)}
"""
