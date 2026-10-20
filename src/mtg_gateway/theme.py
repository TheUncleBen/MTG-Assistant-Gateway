"""Shared HTML shell and stylesheet for the gateway's browser pages.

The look follows Archidekt's visual theme one to one where its public pages show it: the
same palette (dark, light), the Lato type stack at a 14px root, 5px radii, flat 39px bordered
buttons and inputs, bordered 3px panels, a flat 50px near-black toolbar whose links turn orange,
a bottom icon toolbar on phones, and a Light / Dark / System theme choice. Only the look is
reproduced: no Archidekt assets, icons, logos or fonts are loaded, every icon is our own inline
SVG, and the page's Content-Security-Policy forbids every external request.

Colour tokens were read from archidekt.com's compiled CSS (2026-10-04 and 2026-10-05: the
``body`` / ``body.dark-mode`` custom properties, phatButton, phatInput, phatDropdown,
globalToolbar, floatingToolbar, footer and panel rules). The theme is chosen with the
``mtg_theme`` cookie (``light``, ``dark`` or unset = system); the cookie is read by
:class:`ThemeMiddleware` so every page can render it without passing the request around.
"""

from __future__ import annotations

import functools
import hashlib
import html
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.responses import HTMLResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__, update_check
from .mana import SPRITE

THEME_COOKIE = "mtg_theme"
# "auto" (unset) follows the window width; "desktop" asks a phone or the app for the computer layout.
LAYOUT_COOKIE = "mtg_layout"
LAYOUTS = ("auto", "desktop")
FEEDBACK_SCRIPT = "/static/feedback.js"
# The Content-Security-Policy of every page render() builds (some pages replace it with their own,
# which keeps the same frame-ancestors and base-uri). form-action is last so sources can follow it.
# img-src 'self' is for the gateway's own files only (the select arrow, /static/chevron.svg); pages
# that show card images add cards.scryfall.io in their own policy. style-src 'self' is for the
# shared stylesheets (/static/site.css, /static/deck.css); every policy that replaces this one keeps it.
DEFAULT_CSP = (
    # The top bar's search box is on every page: connect-src 'self' lets it ask the gateway for
    # suggestions, and cards.scryfall.io is the small card picture each suggestion shows.
    "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; worker-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
# The site icon's art as a data: URL, for documents that cannot load /favicon.ico (the offline
# page, a downloaded report).
ICON_DATA_URL = "data:image/svg+xml," + quote(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 108 108'>"
    "<rect width='108' height='108' rx='24' fill='#1c1f26'/><g transform='rotate(-8 54 54)'>"
    "<path d='M36 26h36a4 4 0 0 1 4 4v48a4 4 0 0 1-4 4H36a4 4 0 0 1-4-4V30a4 4 0 0 1 4-4z' "
    "fill='#232323' stroke='#fa890d' stroke-width='3'/>"
    "<path d='M54 40l10 14-10 14-10-14z' fill='#fa890d'/>"
    "<path d='M40 70h28v4H40z' fill='#e3e3e3'/></g></svg>",
    safe="",
)

THEMES = ("system", "light", "dark")
_theme: ContextVar[str] = ContextVar("mtg_theme", default="system")
_layout: ContextVar[str] = ContextVar("mtg_layout", default="auto")
_path: ContextVar[str] = ContextVar("mtg_path", default="/")
# True while rendering for the Android app's WebView (its user agent carries "MTGAssistant/"):
# pages then use the phone layout at every width and drop the website footer.
_app: ContextVar[bool] = ContextVar("mtg_app", default=False)
APP_UA_MARK = "MTGAssistant/"
# The app's version follows the mark ("MTGAssistant/0.7.22"); the Account page shows it.
_APP_VERSION_RE = re.compile(r"MTGAssistant/(\d{1,3}\.\d{1,3}\.\d{1,3})(?![\w.])")
_app_version: ContextVar[str] = ContextVar("mtg_app_version", default="")
# The signed-in member's card size (prefs.py), set by pages.browser_session for this request.
_card_size: ContextVar[str] = ContextVar("mtg_card_size", default="m")

# Archidekt's light set (body custom properties). Emitted twice: under prefers-color-scheme for
# "system" and under [data-theme=light] for an explicit choice.
_LIGHT = """
    --bg:#f9fafb; --surface:#fafafa; --surface-2:#dedede; --surface-3:#c1c1c1;
    --border:#bababa; --border-soft:#d4d4d4; --card-border:#5b5b5b;
    --text:#383838; --link:#1d55b3; /* 5.2:1 on --surface-2 (the menu); Archidekt's #4183c4 is 3.8:1 */
    /* 4.8:1 on --surface-2 and 6.2:1 on --bg; Archidekt's #727272 is 3.6:1 on --surface-2 */
    --text-muted:#5e5e5e;
    --menu-head:#595959; /* 5.2:1 on --surface-2 */
    --toolbar-bg:#dcdcdc; --toolbar-text:#383838;
    --navbar-bg:#313131; --navbar-text:#ffffff; --navbar-muted:#d6d6d6;
    --banner-a:rgba(40,40,40,.82); --banner-b:rgba(40,40,40,.5);
    /* coloured text: at least 4.5:1 on --bg, --surface, --surface-2 and on its own tint */
    --orange-text:#944b00; --green-text:#0d6639; --red-text:#b3262e; --blue-text:#1d55b3;
    --orange-ctl:#c45f00; /* a checked box or radio: 3.1:1 on --surface-2, 4:1 on --surface */
    --orange-tint:rgba(250,137,13,.16); --green-tint:rgba(30,187,108,.16);
    --red-tint:rgba(255,85,91,.16); --blue-tint:rgba(66,134,244,.14);
    --danger-text:#b3262e; --toolbar-active:#8a4600; /* dark values: #ff8086 and #fa890d */
    /* the focus ring and the edges of inputs and selects: at least 3:1 (WCAG 1.4.11) on the page;
       the dark bars (top bar, footer, deck banner) switch the ring back to orange below */
    --focus:#944b00; --control-border:#7a7a7a;
    --shadow:0 3px 6px rgba(0,0,0,.25); --scrim:rgba(0,0,0,.4);
"""

# Tiles, sparklines, charts and the filter-bar select: shared by the deck, report and history
# pages and copied into a report's self-contained HTML export.
VIZ_CSS = """
/* summary tiles, sparklines and the small script-free charts (deck page, reports, history trends) */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,6.5rem),1fr));gap:.5rem;
  margin:0 0 .75rem}
.tile{background:var(--surface-2);border:1px solid var(--border-soft);border-radius:3px;padding:.5rem .6rem;
  display:flex;flex-direction:column;gap:.1rem;min-width:0}
.tile b{font-size:1.25rem;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.tile span{color:var(--text-muted);font-size:.8rem;overflow-wrap:anywhere}
.tiles.wide{grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr));gap:.75rem}
.tiles.wide .tile{padding:.7rem .8rem;gap:.2rem} .tiles.wide .tile b{font-size:1.5rem}
/* a confidence interval under a tile's number: a track with the interval filled in */
.tile .ci{position:relative;display:block;height:6px;margin:.3rem 0 .1rem;border-radius:3px;
  background:var(--surface-3);overflow:hidden}
.tile .ci i{position:absolute;top:0;bottom:0;background:var(--orange);border-radius:3px}
.bars{display:flex;align-items:flex-end;gap:.4rem;height:8rem;padding:.25rem 0;
  border-bottom:1px solid var(--border)}
.bars .bar{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;
  min-width:0;font-size:.75rem}
.bars .bar span{display:block;width:70%;background:var(--orange);border-radius:2px 2px 0 0}
.bars .bar b{font-variant-numeric:tabular-nums;margin-bottom:.15rem}
.bars .bar em{font-style:normal;font-weight:700;margin-top:.3rem;overflow-wrap:anywhere;text-align:center}
.twocol{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,16rem),1fr));gap:1rem}
.twocol h3{margin-top:0}
.pips li{display:flex;align-items:center;gap:.5rem;padding:.25rem 0;flex-wrap:wrap}
.pips li span:nth-child(2){flex:1 1 5rem} .pips b{font-variant-numeric:tabular-nums}
.pips i{font-style:normal;color:var(--text-muted);font-size:.86rem}
/* SVG charts drawn on the server: lines and columns take their colours from the theme */
.chart{min-width:0} .chart h3{margin:0 0 .4rem}
.chart svg{display:block;width:100%;height:auto;color:var(--text-muted);font-size:11px;overflow:visible}
.chart text{fill:currentColor} .chart .grid{stroke:var(--border-soft);stroke-width:1}
.chart .axis{stroke:var(--border)} .chart .col{fill:var(--orange)}
.chart .s1{stroke:var(--orange)} .chart .s2{stroke:var(--blue)} .chart .s3{stroke:var(--green)}
.chart .s4{stroke:var(--purple)} .chart polyline{fill:none;stroke-width:2.5;stroke-linejoin:round;
  stroke-linecap:round} .chart circle.s1{fill:var(--orange)} .chart circle.s2{fill:var(--blue)}
.chart circle.s3{fill:var(--green)} .chart circle.s4{fill:var(--purple)}
.legend{display:flex;flex-wrap:wrap;gap:.3rem 1rem;margin:.4rem 0 0;padding:0;list-style:none;
  font-size:.86rem}
.legend li{display:inline-flex;align-items:center;gap:.4rem}
.legend i{display:inline-block;width:14px;height:4px;border-radius:2px;background:var(--orange)}
.legend .s2{background:var(--blue)} .legend .s3{background:var(--green)} .legend .s4{background:var(--purple)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,18rem),1fr));gap:1rem 1.5rem;
  margin:.5rem 0 0}
/* a select with a leading icon (filter bars) */
.sel{position:relative;display:block}
.sel > svg{position:absolute;left:.75rem;top:50%;transform:translateY(-50%);color:var(--orange);
  pointer-events:none}
.sel > svg ~ select,.sel > svg ~ .msel-btn{padding-left:2.1rem}
"""

CSS = (
    """
:root{
  /* Archidekt dark-mode set (body.dark-mode) */
  --bg:#181818; --surface:#232323; --surface-2:#383838; --surface-3:#4b4b4b;
  --border:#5f5f5f; --border-soft:#323232; --card-border:#525252;
  --text:#e3e3e3; --text-muted:#a8a8a8; --link:#73a8dc;
  --menu-head:#b0b0b0; /* 5.4:1 on --surface-2 */
  --toolbar-bg:#2e2d2d; --toolbar-text:#f5f5f5;
  --navbar-bg:#111111; --navbar-text:#ffffff; --navbar-muted:#e3e3e3;
  --banner-a:rgba(14,14,14,.82); --banner-b:rgba(14,14,14,.5);
  --orange:#fa890d; --green:#1ebb6c; --red:#ff555b; --blue:#4286f4; --purple:#6435c9; --pink:#e03997;
  /* --red-text: Archidekt's #ff555b is 3.7:1 on --surface-2 */
  --orange-text:#fa890d; --green-text:#1ebb6c; --red-text:#ff7a7f; --blue-text:#7fb0ff;
  --orange-ctl:#fa890d;
  --danger-text:#ff8086; --toolbar-active:#fa890d;
  --control-border:#868686; /* input and select edges: 3.2:1 on --surface-2, 4:1 on --surface */
  --on-color:#ffffff;
  /* Text on the orange and green fills, and the red used as a fill: WCAG AA (4.5:1) for bold 16px
     labels. White on Archidekt's orange is 2.4:1 and on its red 3.1:1, so those labels were hard to read. */
  --on-orange:#111111; --on-green:#111111; --danger-fill:#c0182b;
  --orange-tint:rgba(250,137,13,.14); --green-tint:rgba(30,187,108,.14);
  --red-tint:rgba(255,85,91,.14); --blue-tint:rgba(66,134,244,.14);
  --orange-select:rgba(250,137,13,.3);
  --focus:#fa890d; --radius:5px; --radius-panel:3px; --radius-lg:8px; --radius-sheet:12px;
  --shadow:0 3px 6px rgba(0,0,0,.5); --scrim:rgba(0,0,0,.4);
  --ctl:39px; /* phatButton / phatInput / phatDropdown height */
}
/* Touch screens: every standard control (buttons, inputs, selects, the custom select) is 44 px
   tall, the release's touch-target rule; with a mouse the 39 px Archidekt height stays */
@media (pointer:coarse){ :root{--ctl:44px} }
@media (prefers-color-scheme: light){ :root:not([data-theme=dark]){"""
    + _LIGHT
    + """} }
:root[data-theme=light]{"""
    + _LIGHT
    + """}
*{box-sizing:border-box}
/* --bar-room is the top bar's height plus 10 px (static/topbar.js). Without scripts: 60 px over the
   one-row bar (50 px), and 9.5rem where the bar's links wrap, which covers it at every text size
   from 100% to 200% (measured from 600 to 1920 px: at most 191 px at 200%
   text, and at most about 8.7rem, 183 px, at 920 px and 150%). The links wrap when the bar is
   narrower than 42.5em (600 to 899 px with a mouse) or 58em (900 px and wider; the container
   queries below); the bar's em is the root's, so the test is in rem, with 20 px for a scroll bar.
   At 600 to 899 px the links can also wrap within the one row a little above 42.5em (an admin's
   eight links up to 625 px at the normal size), so there the large room lasts to 45rem.
   clamp((T - 100vw) * 1000) is 0 above T and 9.5rem below it (9.5rem everywhere would leave
   133 px over the one-row bar).
   --foot-room is what stays at the foot of the window plus 10 px (the phone's tab bar, the waiting
   changes' Apply bar, the Guide's Back to top; static/topbar.js). Without scripts: 120 px, or 9rem
   once that is more, so the room grows with the text. */
html{font-size:14px;scroll-padding:var(--bar-room,60px) 0 var(--foot-room,max(120px,9rem));
  -webkit-text-size-adjust:100%;min-width:320px;font-family:Lato,"Helvetica Neue",Arial,Helvetica,sans-serif;
  line-height:1.15}
@media (min-width:600px) and (max-width:899.98px) and (not ((pointer:coarse) or (hover:none))){
  html{scroll-padding-top:var(--bar-room,max(60px,clamp(0px,(45rem + 20px - 100vw) * 1000,9.5rem)))} }
@media (width >= 900px){
  html{scroll-padding-top:var(--bar-room,max(60px,clamp(0px,(58rem + 20px - 100vw) * 1000,9.5rem)))} }
body{margin:0;min-height:100vh;display:flex;flex-direction:column;background:var(--bg);color:var(--text);
  font:inherit;font-size:1rem;line-height:1.35;transition:background-color .25s ease-in}
a{color:var(--link)} a:hover{color:var(--orange-text)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
/* the dark bars keep the orange ring in the light theme too (the light ring is made for pale panels) */
.topbar,footer.site,.banner{--focus:#fa890d}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);
  white-space:nowrap;border:0}
/* skip link: the first thing Tab reaches; hidden until it has focus, then over the top bar */
a.skip{position:absolute;left:.5rem;top:-100px;z-index:50;padding:.6rem 1rem;border-radius:var(--radius);
  background:var(--orange);color:var(--on-orange);font-weight:700;text-decoration:none}
a.skip:focus-visible{top:.5rem;outline-color:var(--on-orange)}
main:focus{outline:none}
/* an image that failed to load (feedback.js marks it): hide it, show the name in its place */
/* (!important: a row's own img rule, such as .erow .thumb's inline-flex, must not show it again) */
img.img-broken{display:none !important}
.img-fallback{display:flex;align-items:center;justify-content:center;width:100%;height:100%;min-height:2rem;
  padding:.5rem;text-align:center;font-size:.8rem;font-weight:700;color:var(--text-muted);
  background:linear-gradient(160deg,var(--surface-3),var(--surface-2));overflow-wrap:anywhere}
/* too small for the name (a row's thumbnail): the plain tile a row without a picture shows */
.img-fallback.img-fallback-mini{font-size:0;padding:0;min-height:0;overflow:hidden}
svg.i{width:1em;height:1em;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;
  stroke-linejoin:round;vertical-align:-.15em;flex:none}
@media (prefers-reduced-motion: reduce){ *{transition:none !important;animation:none !important} }

/* global toolbar (navbar): flat, 50px, links bold white turning orange */
.topbar{background:var(--navbar-bg);color:var(--navbar-text);position:sticky;top:0;z-index:7}
.topbar .wrap{display:flex;align-items:center;justify-content:space-between;gap:1rem;height:50px;
  max-width:none;padding:0 2rem}
.topbar .left,.topbar .right{display:flex;align-items:center;gap:.25rem;min-width:0}
.brand{display:inline-flex;align-items:center;gap:.5rem;color:var(--navbar-text);text-decoration:none;
  font-weight:900;font-size:1.15rem;letter-spacing:.01em;white-space:nowrap;margin-right:.75rem}
.brand:hover{color:var(--orange)}
/* the brand mark is the site icon's art (static/gateway-icon.svg): an orange card on a dark tile */
.brand .mark{width:25px;height:25px;border-radius:5px;background:#1c1f26;display:inline-flex;
  align-items:center;justify-content:center;color:var(--orange);flex:none;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}
.brand .mark svg{width:25px;height:25px;stroke-width:2.4}
.topbar nav{display:flex;align-items:center;gap:.25rem}
.topbar nav a,.topbar nav summary,.topbar nav button{display:inline-flex;align-items:center;gap:.4rem;
  height:40px;padding:0 .7rem;margin:0;color:var(--navbar-text);background:transparent;border:0;
  border-radius:var(--radius);text-decoration:none;font:inherit;font-weight:700;font-size:1rem;
  cursor:pointer;white-space:nowrap;transition:color .2s ease}
.topbar nav a:hover,.topbar nav a:focus-visible,.topbar nav summary:hover,.topbar nav button:hover{
  color:var(--orange);background:transparent}
.topbar nav a[aria-current=page]{color:var(--orange)}
.topbar nav form{display:inline;margin:0}
.topbar .icon-btn{width:40px;padding:0;justify-content:center;font-size:1.2rem}
.topbar .icon-btn img.av{width:28px;height:28px;border-radius:50%;object-fit:cover;display:block}

/* dropdown menus (details/summary, no script): phatDropdown trigger + menu panel. The panel
   styling is the shared .menu class, so a script-made menu (the deck page's card menu) looks the
   same; details.dd only adds where its panel sits. */
details.dd{position:relative;margin:0}
details.dd > summary{list-style:none;cursor:pointer;user-select:none}
details.dd > summary::-webkit-details-marker{display:none}
.menu{z-index:30;min-width:200px;
  background:var(--surface-2);border-radius:var(--radius-panel);box-shadow:var(--shadow);padding:.25rem 0;
  display:flex;flex-direction:column}
/* An absolute panel hanging from a small button would shrink to its min-width and wrap even short
   items ("Change printing"): it takes its content's width instead, capped so a long item still
   wraps inside a phone's window (and a desktop menu never runs half the screen wide). */
details.dd .menu{position:absolute;top:calc(100% + .25rem);right:0;width:max-content;
  max-width:min(30rem,calc(100vw - 16px))}
details.dd .menu.left{left:0;right:auto}
/* Each item rule is written twice, plain and under details.dd: inside the top bar the plain
   .menu rule would lose to .topbar nav a (the bar's white text, 40px, bold) and the account
   menu's links would be white on the light theme's menu. */
.menu a,.menu button,.menu .item,details.dd .menu a,details.dd .menu button,details.dd .menu .item{
  display:flex;align-items:center;gap:.6rem;
  min-height:35px;padding:0 1rem;margin:0;width:100%;background:transparent;border:0;border-radius:0;
  color:var(--text);text-decoration:none;font:inherit;font-weight:400;font-size:1rem;cursor:pointer;
  text-align:left;white-space:nowrap;justify-content:flex-start;height:auto}
/* An item carrying a member's text (a category name) wraps inside the panel instead of widening
   it past the window (a 51-character unbroken category name at 320 px, for one). */
details.dd .menu a,details.dd .menu button{white-space:normal;overflow-wrap:anywhere;min-width:0}
.menu a:hover,.menu button:hover,.menu a:focus-visible,.menu button:focus-visible,
details.dd .menu a:hover,details.dd .menu button:hover,details.dd .menu a:focus-visible,
details.dd .menu button:focus-visible{background:var(--border);color:var(--text)}
.menu button:disabled,details.dd .menu button:disabled{color:var(--text-muted);cursor:default;
  background:transparent}
.menu .sep{height:1px;background:var(--border);margin:.25rem 0}
.menu a.danger,.menu button.danger,details.dd .menu a.danger,details.dd .menu button.danger{
  color:var(--danger-text);background:transparent;border:0}
.menu button.danger:hover,.menu button.danger:focus-visible,details.dd .menu button.danger:hover,
details.dd .menu button.danger:focus-visible{background:var(--border);color:var(--danger-text)}
.menu .head{padding:.4rem 1rem .2rem;font-size:.8rem;font-weight:700;color:var(--menu-head);
  text-transform:uppercase;letter-spacing:.04em;overflow-wrap:anywhere}
.menu .head.who{text-transform:none;letter-spacing:0;font-weight:400;font-size:.86rem;
  padding:.5rem 1rem .4rem;border-bottom:1px solid var(--border);margin-bottom:.25rem;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:18rem}
.menu .head.who b{color:var(--text)}
.topbar .signin{height:34px;min-height:34px;padding:0 .9rem;background:var(--orange);
  border-color:var(--orange);color:var(--on-orange);white-space:nowrap}
.topbar .signin:hover{background:var(--orange);color:var(--on-orange);filter:brightness(1.1)}
.menu form{margin:0;display:contents}
/* the chosen theme: --orange-text would be 4.0:1 on the light menu */
.menu .on,details.dd .menu .on{color:var(--toolbar-active);font-weight:700}
/* a dropdown trigger styled like phatDropdown: bordered, 39px, orange chevron, label floating above */
.field{position:relative;display:flex;flex-direction:column;gap:.3rem;min-width:0}
.field > label,.field > .lbl{font-weight:700;margin:0;font-size:1rem}
.dd-trigger,details.dd > summary.dd-trigger{display:flex;align-items:center;justify-content:space-between;
  gap:.5rem;height:var(--ctl);min-width:140px;padding:0 .75rem 0 1rem;border-radius:var(--radius);
  border:1px solid var(--control-border);background:var(--surface);color:var(--text);font-weight:400;
  transition:background-color .2s ease-in-out}
.dd-trigger:hover,details.dd > summary.dd-trigger:hover{background:var(--surface-2)}
.dd-trigger .chev{color:var(--orange);font-size:.8rem}
.dd-trigger .ic{color:var(--orange);margin-right:.25rem}
details.dd[open] > summary.dd-trigger{border-color:var(--orange)}

.wrap{width:100%;max-width:52rem;margin:0 auto;padding:0 1rem}
main.wrap{flex:1;padding:1rem 1rem 3rem}
h1{font-size:2rem;font-weight:700;line-height:1.2;margin:.5rem 0 1rem;overflow-wrap:anywhere}
h2{font-size:1.5rem;font-weight:700;margin:0 0 .6rem;overflow-wrap:anywhere}
h2:not(:first-child){margin-top:1.25rem}
h3{font-size:1.1rem;font-weight:700;margin:.5rem 0 .4rem}
h4{font-size:1rem;font-weight:700;margin:0}
p{margin:.5rem 0}
details.identity{margin:.6rem 0}details.identity dd{overflow-wrap:anywhere}
details.identity > summary{cursor:pointer;color:var(--text-muted);font-size:.86rem;padding:.3rem 0}
.muted{color:var(--text-muted)} .small{font-size:.86rem} .b{font-weight:700} .orange{color:var(--orange-text)}
ul.plain{list-style:none;margin:0;padding:0}

/* panels (Archidekt: 3px radius, 1px border, light-background, no shadow) */
.card,.panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-panel);
  padding:1rem;margin:0 0 1rem}
.card > :first-child,.panel > :first-child{margin-top:0}
.card > :last-child,.panel > :last-child{margin-bottom:0}
.panel h2,.card h2{font-size:1.5rem}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
code{font-size:.92em;background:var(--surface-2);padding:.1rem .4rem;border-radius:3px;overflow-wrap:anywhere}
pre{font-size:.86rem;line-height:1.5;background:var(--bg);border:1px solid var(--border-soft);
  border-radius:var(--radius);padding:.75rem .9rem;margin:.5rem 0;overflow:auto;white-space:pre-wrap;
  overflow-wrap:anywhere}

/* forms: phatInput / phatButton */
label{display:block;margin:.9rem 0 .3rem;font-weight:700}
input:not([type]),input[type=text],input[type=password],input[type=email],input[type=search],input[type=number],
input[type=url],select{width:100%;height:var(--ctl);padding:0 1rem;font:inherit;border-radius:var(--radius);
  border:1px solid var(--control-border);background:var(--surface);color:var(--text);
  transition:background-color .2s ease-in-out}
/* a field inside a <label> keeps its normal weight: only the label's own text is bold */
label input,label textarea,label select,label .msel-btn{font-weight:400}
select{appearance:none;-webkit-appearance:none;padding-right:2rem;cursor:pointer;
  background-image:url(/static/chevron.svg);
  background-repeat:no-repeat;background-position:right .75rem center;background-size:10px 6px}
input[type=number]{padding-right:.5rem}
textarea{width:100%;min-height:8rem;padding:.5rem .75rem;font:inherit;font-size:.95rem;line-height:1.45;
  border-radius:var(--radius);border:1px solid var(--control-border);background:var(--surface);
  color:var(--text);resize:vertical}
input:focus,textarea:focus,select:focus{outline:2px solid var(--focus);outline-offset:1px;
  border-color:var(--orange)}
/* themed dropdown lists (static/select.js): the native select is kept for the form and for scripts
   but sits off-screen; the button in its place looks like the closed select, and the open list is
   the shared .menu panel (a bottom sheet under 600px or with a coarse pointer). The .menu button
   rules would restyle a trigger inside a menu panel (the collection's row details), hence the
   doubled selector. */
select.msel-native{position:absolute;width:1px;height:1px;margin:-1px;padding:0;border:0;overflow:hidden;
  clip:rect(0,0,0,0);opacity:0;pointer-events:none}
.msel-btn,.menu button.msel-btn,details.dd .menu button.msel-btn{display:flex;align-items:center;
  justify-content:flex-start;gap:0;width:100%;
  height:var(--ctl);min-height:0;margin:0;padding:0 2rem 0 1rem;font:inherit;font-size:1rem;font-weight:400;
  text-align:left;white-space:nowrap;border-radius:var(--radius);border:1px solid var(--control-border);
  color:var(--text);cursor:pointer;background-color:var(--surface);background-image:url(/static/chevron.svg);
  background-repeat:no-repeat;background-position:right .75rem center;background-size:10px 6px;
  transition:background-color .2s ease-in-out}
.msel-btn:hover,.menu button.msel-btn:hover,details.dd .menu button.msel-btn:hover{
  background-color:var(--surface-2);color:var(--text)}
.msel-btn:focus-visible,.menu button.msel-btn:focus-visible,details.dd .menu button.msel-btn:focus-visible{
  outline:2px solid var(--focus);outline-offset:1px;
  border-color:var(--orange);background-color:var(--surface)}
.msel-btn[aria-expanded=true]{border-color:var(--orange)}
/* A form inside a dropdown (a collection row's Details) keeps its primary Save button: the menu
   item rule above would otherwise strip its colour, border and radius. */
details.dd .menu .actions button.primary{background:var(--orange);border:1px solid var(--orange);
  color:var(--on-orange);border-radius:var(--radius);justify-content:center;height:var(--ctl);
  min-height:var(--ctl);font-weight:600}
details.dd .menu .actions button.primary:hover,details.dd .menu .actions button.primary:focus-visible{
  background:var(--orange);color:var(--on-orange);filter:brightness(1.08)}
.msel-btn:active:not(:disabled){transform:none;filter:none}
.msel-btn .msel-txt{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
/* above every panel or dialog a list can open from (the card search Filters panel is 80, the add
   sheet 90): the sheet's dim background must cover the panel, or a tap above the sheet reaches it */
.msel-list{position:fixed;z-index:200;max-width:calc(100vw - 1rem);overflow:auto;overscroll-behavior:contain;
  border:1px solid var(--border)}
.msel-list [role=option]{min-height:40px;white-space:normal;overflow-wrap:anywhere;padding:.3rem 1rem}
.msel-list [role=option][aria-selected=true]{background:var(--border);box-shadow:inset 3px 0 0 var(--orange)}
.msel-list .head.title{padding:.6rem 1rem .4rem;font-size:.9rem}
.msel-scrim{position:fixed;inset:0;z-index:199;background:var(--scrim)}
.msel-list.sheet{left:0;right:0;bottom:0;top:auto;width:auto;max-width:none;max-height:min(70vh,70dvh);
  min-width:0;border-radius:var(--radius-sheet) var(--radius-sheet) 0 0;border-bottom:0;
  padding:.5rem 0 calc(.5rem + env(safe-area-inset-bottom));animation:mselup .15s ease-out}
.msel-list.sheet [role=option]{min-height:44px}
@keyframes mselup{from{transform:translateY(12px)}to{transform:none}}
@media (prefers-reduced-motion:reduce){ .msel-list.sheet{animation:none} }
/* file pickers (static/filepick.js): a themed Choose a file button and the chosen name; the input is
   off-screen but keeps the keyboard (Tab reaches it, Enter or Space opens the system's picker) */
.filepick{display:flex;flex-direction:row;flex-wrap:wrap;align-items:center;justify-content:flex-start;
  gap:.4rem .75rem}
.filepick > .lbl{flex:0 0 100%}
.filepick > label.filebtn{display:inline-flex;margin:0;font-weight:400;cursor:pointer}
.filepick .filebtn input{position:absolute;width:1px;height:1px;margin:-1px;padding:0;border:0;
  overflow:hidden;clip:rect(0,0,0,0)}
.filepick .filebtn input:focus-visible + .btn{outline:2px solid var(--focus);outline-offset:1px;
  border-color:var(--orange)}
.filepick .fname{color:var(--text-muted);min-width:0;overflow-wrap:anywhere}
input::placeholder,textarea::placeholder{color:var(--text-muted);opacity:1}
.check{display:flex;align-items:center;gap:.6rem;margin:.5rem 0;font-weight:400;min-height:2rem;
  cursor:pointer}
/* every tick box is themed (never the browser's blue): a bordered square, orange with our own
   check when on (static/check.svg, so the page's image policy stays 'self') */
input[type=checkbox]{appearance:none;-webkit-appearance:none;width:1.15rem;height:1.15rem;margin:0;flex:none;
  border:1px solid var(--control-border);border-radius:3px;background:var(--surface) center/80% no-repeat;
  cursor:pointer;vertical-align:-.2em;display:inline-block;transition:background-color .15s}
input[type=checkbox]:checked{background-color:var(--orange-ctl);border-color:var(--orange-ctl);
  background-image:url(/static/check.svg)}
input[type=checkbox]:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
input[type=checkbox]:disabled{opacity:.5;cursor:default}
label:has(> input[type=checkbox]){display:inline-flex;align-items:center;gap:.5rem;font-weight:400;
  cursor:pointer}
.check input{width:1.15rem;height:1.15rem;margin:0}
input[type=radio]{appearance:none;-webkit-appearance:none;width:1.15rem;height:1.15rem;margin:0;flex:none;
  border:1px solid var(--control-border);border-radius:50%;background:var(--surface);cursor:pointer;
  display:inline-block;vertical-align:-.2em}
input[type=radio]:checked{border:.35rem solid var(--orange-ctl);background:var(--surface)}
input[type=radio]:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
/* a choice with a description under its label: the dot sits level with the label's first line */
.check.mode{align-items:flex-start}
.check.mode input{margin-top:.15rem}
/* toggle switch 40x20, orange when on */
.switch{position:relative;display:flex;align-items:center;gap:.6rem;cursor:pointer;font-weight:400;
  margin:.5rem 0;min-height:2rem}
.switch input{position:absolute;opacity:0;width:0;height:0}
.switch .track{width:40px;height:20px;border-radius:20px;background:var(--surface-3);position:relative;
  flex:none;transition:background-color .2s ease-in-out}
.switch .track::after{content:'';position:absolute;top:1px;left:1px;width:18px;height:18px;border-radius:50%;
  background:#fff;transition:transform .2s ease-in-out}
.switch input:checked + .track{background:var(--orange)}
.switch input:checked + .track::after{transform:translateX(20px)}
.switch input:focus-visible + .track{outline:2px solid var(--focus);outline-offset:2px}
ol,ul:not([class]){padding-left:1.4rem} ol li,ul:not([class]) li{margin:.25rem 0}
button,.btn{display:inline-flex;align-items:center;justify-content:center;gap:.5rem;min-height:var(--ctl);
  margin:0;padding:.35rem 1rem;font:inherit;font-size:1rem;font-weight:700;text-align:center;cursor:pointer;
  -webkit-tap-highlight-color:transparent;touch-action:manipulation;
  border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);color:var(--text);
  text-decoration:none;transition:background-color .2s ease-in-out,filter .2s ease-in-out}
button:hover,.btn:hover{background:var(--surface-2);color:var(--text)}
button:disabled,.btn[aria-disabled=true]{opacity:.5;cursor:default}
/* Every press shows at once: pressed look on touch, and a busy state while a form submits
   (static/feedback.js sets aria-busy on the button that sent it). */
button:active:not(:disabled),.btn:active{transform:translateY(1px);filter:brightness(.9)}
button[aria-busy=true],.btn[aria-busy=true]{cursor:progress;opacity:.75}
button[aria-busy=true]::after,.btn[aria-busy=true]::after{content:'';width:.9em;height:.9em;flex:none;
  border-radius:50%;border:2px solid currentColor;border-right-color:transparent;
  animation:busy .8s linear infinite}
@keyframes busy{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){
  button[aria-busy=true]::after,.btn[aria-busy=true]::after{animation:none} }
.btn-primary,button.primary{background:var(--orange);border-color:var(--orange);color:var(--on-orange)}
.btn-primary:hover,button.primary:hover{background:var(--orange);color:var(--on-orange);filter:brightness(1.1)}
button.danger,.btn-danger{background:var(--danger-fill);border-color:var(--danger-fill);color:#fff}
button.danger:hover,.btn-danger:hover{background:var(--danger-fill);color:#fff;filter:brightness(1.1)}
button.secondary,.btn-ghost{background:transparent;border-color:transparent}
button.secondary:hover,.btn-ghost:hover{background:var(--surface-2)}
.btn-lg,button.btn-lg{height:48px;padding:0 1.4rem;font-size:1.1rem}
button.mini{width:2.25rem;min-width:2.25rem;height:2.25rem;padding:0;font-size:1.1rem;line-height:1}
button.icon-only{width:var(--ctl);padding:0}
form{margin:0}
form > button,form .btn{margin-top:1rem}
button.thumbbtn{all:unset;display:block;cursor:pointer;line-height:0;border-radius:3px}
.exportblock .head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem;
  margin-top:1.25rem}
.exportblock .head h2{margin:0;min-width:0;overflow-wrap:anywhere}
.exportblock textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:.85rem}
button.thumbbtn:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.choice{display:flex;flex-direction:column;gap:.6rem;margin-top:1rem}
/* The three choices (Apply…, Reject…, Not now) sit in one row only from 800px: narrower than
   that the row would wrap and leave "Not now" alone on a second line, so they stack full width. */
.choice button,.choice .btn{margin-top:0;width:100%}
@media (min-width:800px){ .choice{flex-direction:row;align-items:center;flex-wrap:wrap}
  .choice button,.choice .btn{width:auto} }
.actions{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem;align-items:center}
.actions .btn,.actions button,.actions form > button{margin-top:0}
.actions form{display:contents}
@media (max-width:600px){ main form > button:not(.mini):not(.inline){width:100%} }
@media (max-width:799.98px){ main .choice .btn{width:100%} }
/* one row of equal-height buttons at the end of a form; on phones they stack full width */
.form-actions{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-top:1rem}
.form-actions[hidden]{display:none}
.form-actions > button,.form-actions > .btn,.form-actions form > button{margin:0}
.form-actions form{display:contents}
.form-actions .status{margin:0 0 0 auto;color:var(--text-muted);font-size:.9rem}
@media (max-width:600px){ .form-actions > button:not(.mini),.form-actions > .btn,
  .form-actions form > button{width:100%} }

/* mana and rules-text symbols: own glyphs on Archidekt-coloured discs (sprite from mana.py) */
.mana{display:inline-flex;gap:2px;align-items:center;white-space:nowrap;vertical-align:middle}
.mana .sep{color:var(--text-muted);font-size:.8em;margin:0 .15em}
.pip{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;
  color:#111;background:#cbc2bf;box-shadow:-1px 1px 0 rgba(0,0,0,.55);flex:none;vertical-align:-.2em;
  font-style:normal;line-height:1}
.pip svg{width:70%;height:70%;fill:currentColor;display:block}
.pip b{font-size:10px;font-weight:900;line-height:1}
.pip.big b{font-size:8px}
.pip.sm{width:14px;height:14px;margin:0 1px}
.pip.sm b{font-size:9px}
.pip-W{background:#f8f6d8} .pip-U{background:#c1d7e9} .pip-B{background:#bab1ab} .pip-R{background:#e49977}
.pip-G{background:#a3c095} .pip-C{background:#cbc2bf} .pip-g{background:#cbc2bf}
.pip-T,.pip-Q,.pip-E,.pip-P{background:#cbc2bf} .pip-S{background:#dfe8ee}
.pip.hy{background:linear-gradient(135deg,var(--h1) 50%,var(--h2) 50%)}
.pip-W.hy{--h1:#f8f6d8} .pip-U.hy{--h1:#c1d7e9} .pip-B.hy{--h1:#bab1ab} .pip-R.hy{--h1:#e49977}
.pip-G.hy{--h1:#a3c095}
.pip.hy[data-b=W]{--h2:#f8f6d8} .pip.hy[data-b=U]{--h2:#c1d7e9} .pip.hy[data-b=B]{--h2:#bab1ab}
.pip.hy[data-b=R]{--h2:#e49977} .pip.hy[data-b=G]{--h2:#a3c095}

/* typed suggestions (static/suggest.js): a listbox under the box, themed like the dropdown menus */
.suggest{position:relative;display:block;min-width:0}
.suggest > input{width:100%}
.suggest-list{position:absolute;top:calc(100% + 3px);left:0;right:0;z-index:70;margin:0;
  padding:.3rem 0;list-style:none;max-height:min(19rem,55vh);overflow:auto;overscroll-behavior:contain;
  background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  box-shadow:var(--shadow)}
.suggest-list li{display:block;padding:.55rem 1rem;cursor:pointer;line-height:1.3;overflow-wrap:anywhere;
  font-weight:400}
.suggest-list li[aria-selected=true],.suggest-list li:hover{background:var(--surface-2)}
.suggest-list li[aria-selected=true]{box-shadow:inset 3px 0 0 var(--orange)}
.suggest-list li.none{color:var(--text-muted);cursor:default}
.suggest-list .rich{display:flex;align-items:center;gap:.6rem;min-width:0}
.suggest-list .rich .thumb{width:28px;height:39px;border-radius:2px;object-fit:cover;flex:none;
  background:var(--surface-2)}
.suggest-list .rich .txt{display:flex;flex-direction:column;min-width:0;gap:.1rem}
.suggest-list .rich .nm{display:flex;align-items:center;gap:.4rem;flex-wrap:wrap}
.suggest-list .rich .ty{font-size:.82rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.suggest-list mark{background:none;color:inherit;font-weight:700;text-decoration:underline;
  text-decoration-color:var(--orange);text-underline-offset:.15em}
/* the picker over a short list (static/suggest.js, data-suggest="list") reads as one: the select
   chevron in the box, finger-sized rows (44 px; roomier on a touch screen) */
.suggest > input[data-suggest=list]{padding-right:2.25rem;background-image:url(/static/chevron.svg);
  background-repeat:no-repeat;background-position:right .9rem center;background-size:10px 6px}
.suggest-list.picklist li:not(.head){min-height:44px;padding-top:max(.55rem,calc((44px - 1.3em) / 2));
  padding-bottom:max(.55rem,calc((44px - 1.3em) / 2))}
@media (pointer:coarse){ .suggest-list.picklist li:not(.head){padding-top:.75rem;padding-bottom:.75rem} }

/* badges, pills and notices */
.badge{display:inline-block;vertical-align:middle;padding:.125rem .5rem;border-radius:5px;font-size:.86rem;
  font-weight:700;line-height:1.4;background:#6b6b6b;color:#fff;max-width:100%;overflow-wrap:anywhere}
.badge.ok{background:var(--green);color:var(--on-green)}
.badge.warn{background:var(--orange);color:var(--on-orange)}
.badge.danger{background:var(--danger-fill)}
.badge.info{background:#2a66c9}
.pill{display:inline-block;padding:.125rem .25rem;border-radius:3px;border:1px solid var(--border);
  background:var(--toolbar-bg);color:var(--toolbar-text);font-weight:700;font-size:.86rem;line-height:1.3}
.chip{display:inline-block;padding:.25rem .5rem;border-radius:3px;background:var(--surface-2);
  font-size:.86rem}
.notice{border:1px solid var(--border);border-left:4px solid var(--blue);background:var(--surface);
  border-radius:var(--radius-panel);padding:.7rem .9rem;margin:0 0 1rem;font-weight:700}
.notice.error{border-left-color:var(--red);background:var(--red-tint);color:var(--red-text)}
.notice.ok{border-left-color:var(--green);background:var(--green-tint);color:var(--green-text)}
.notice.warn{border-left-color:var(--orange);background:var(--orange-tint);color:var(--orange-text)}
.notice.strong{display:grid;grid-template-columns:auto 1fr;gap:.25rem .75rem;align-items:start;
  border-width:1px 1px 1px 6px;border-color:var(--orange);padding:.9rem 1rem;font-size:1.02rem;
  font-weight:400;color:var(--text)}
.notice.strong::before{content:'!';grid-row:span 2;width:2rem;height:2rem;border-radius:50%;
  display:inline-flex;align-items:center;justify-content:center;font-weight:900;font-size:1.3rem;
  background:var(--orange);color:var(--on-orange)}
.notice.strong .lead{font-weight:900;color:var(--orange-text);font-size:1.1rem;margin:0}
.notice.strong p{margin:0}
.notice.disclosure{font-weight:400;color:var(--text);border-left-color:var(--orange)}
.disclosure h3{font-size:1rem;margin:.9rem 0 .3rem}
.disclosure ul{margin:0 0 .4rem;padding-left:1.25rem}
/* a long path such as /decks/{deck_id} breaks rather than widen the page (200% text on a phone) */
.disclosure li{margin:.2rem 0;line-height:1.45;overflow-wrap:anywhere}
.disclosure p{margin:0 0 .4rem;overflow-wrap:anywhere}
details.disclosure{margin:.75rem 0 0;border-top:1px solid var(--border);padding-top:.6rem}
details.disclosure > summary{cursor:pointer;font-weight:700;min-height:2rem;display:flex;align-items:center}
/* the folded full detail inside the link-form disclosure (link_disclosure.form_html) */
details.disclosure-detail{margin:.6rem 0 0;border-top:1px solid var(--border);padding-top:.4rem}
details.disclosure-detail > summary{cursor:pointer;font-weight:700;min-height:2.75rem;display:flex;
  align-items:center}
details.disclosure-detail > summary::before{content:'\\25B8';margin-right:.5rem}
details.disclosure-detail[open] > summary::before{content:'\\25BE'}
details.disclosure > summary::before{content:'\\25B8';margin-right:.5rem}
details.disclosure[open] > summary::before{content:'\\25BE'}
.toast{position:fixed;right:2rem;bottom:2rem;z-index:20;width:350px;max-width:calc(100% - 2rem);
  border:1px solid var(--border);border-radius:var(--radius-sheet);background:var(--surface);padding:1rem;
  box-shadow:var(--shadow)}
.toast.error{border-color:var(--red)}

/* key/value meta rows */
.meta{display:grid;grid-template-columns:max-content 1fr;gap:.3rem 1rem;margin:0 0 1rem;font-size:.93rem}
.meta dt{color:var(--text-muted);font-weight:700;margin:0} .meta dd{margin:0;overflow-wrap:anywhere}
@media (max-width:420px){ .meta{grid-template-columns:1fr;gap:.1rem} .meta dd{margin-bottom:.4rem} }

/* row lists (proposals, history, activity, connected apps) */
.plist li{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap;min-height:44px;padding:.6rem 0;
  border-top:1px solid var(--surface-2)}
.plist li:first-child{border-top:0}
.plist .name{font-weight:700;flex:1 1 12rem;overflow-wrap:anywhere}
.plist .when{color:var(--text-muted);font-size:.86rem}
.plist form{margin:0;display:inline}
.plist form button{margin:0;height:35px;padding:0 .7rem;width:auto}
/* Admin > Card models: the name and who added it on the left, every Remove button in one column,
   44 px tall; a long name breaks between words.
   Where the text has too little room (a phone at 200% text) Remove moves under it.
   The text's basis is in em, so it grows with the text. */
.plist.models li{flex-wrap:wrap}
.plist.models .model{display:flex;flex-direction:column;gap:.15rem;flex:1 1 12em;min-width:0}
.plist.models .name{flex:none;overflow-wrap:break-word;hyphens:auto;-webkit-hyphens:auto}
.plist.models .model .small{overflow-wrap:anywhere}
.plist.models form{flex:none;margin-left:auto}
.plist.models form button{height:auto;min-height:44px}
/* Play more games under the Forge card: a 44px target, its words wrapping at 200% text */
.more-games button{height:auto;min-height:44px;max-width:100%;white-space:normal}
.more-games p{margin:.5rem 0 0}

"""
    + VIZ_CSS
    + """/* change list: the heart of the review page */
.summary{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 .75rem}
.summary span{display:inline-block;padding:.25rem .65rem;border-radius:var(--radius);font-weight:700;
  font-size:.9rem;background:var(--surface-2);border:1px solid var(--border-soft)}
.summary .add{color:var(--green-text);border-color:var(--green);background:var(--green-tint)}
.summary .del{color:var(--red-text);border-color:var(--red);background:var(--red-tint)}
.summary .chg{color:var(--orange-text);border-color:var(--orange);background:var(--orange-tint)}
ul.changes{list-style:none;margin:0 0 .75rem;padding:0;border:1px solid var(--border);
  border-radius:var(--radius-panel);overflow:hidden;container-type:inline-size}
.changes li{display:grid;grid-template-columns:6.8rem minmax(0,1fr) auto;align-items:center;gap:.6rem;
  padding:.5rem .75rem;border-bottom:1px solid var(--border-soft);border-left:5px solid var(--border)}
.changes li:last-child{border-bottom:0}
.changes .act{font-size:.78rem;font-weight:900;text-transform:uppercase;letter-spacing:.06em;
  text-align:center;padding:.2rem .3rem;border-radius:3px;border:1px solid;min-width:0;
  hyphens:auto;-webkit-hyphens:auto;overflow-wrap:anywhere}
.changes .name{font-weight:700;font-size:1.05rem;overflow-wrap:anywhere}
.changes .qty{font-weight:700;white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
.changes .qty .was{color:var(--text-muted);font-weight:400;text-decoration:line-through}
.changes li.add{border-left-color:var(--green);background:var(--green-tint)}
.changes li.add .act,.changes li.add .qty{color:var(--green-text);border-color:var(--green)}
.changes li.del{border-left-color:var(--red);background:var(--red-tint)}
.changes li.del .act,.changes li.del .qty{color:var(--red-text);border-color:var(--red)}
.changes li.del .name{text-decoration:line-through;text-decoration-thickness:2px;
  text-decoration-color:var(--red)}
.changes li.chg{border-left-color:var(--orange);background:var(--orange-tint)}
.changes li.chg .act,.changes li.chg .qty{color:var(--orange-text);border-color:var(--orange)}
.changes li.action .name{text-decoration:none;font-weight:700;display:flex;flex-direction:column;gap:.25rem}
.changes li.action .qty{white-space:normal;max-width:9rem}
.changes blockquote.said{margin:0;padding:.4rem .6rem;border-left:3px solid var(--border);
  background:var(--surface);color:var(--text);font-weight:400;white-space:pre-wrap;overflow-wrap:anywhere}
@media (max-width:420px){
  .changes li{grid-template-columns:6.8rem minmax(0,1fr);gap:.35rem .6rem}
  .changes .qty{grid-column:2;text-align:left}
  /* an add or remove count (+1, -1) is short: it stays on the card's line, one line a row */
  .changes li.add,.changes li.del:not(.action){grid-template-columns:6.8rem minmax(0,1fr) auto}
  .changes li.add .qty,.changes li.del:not(.action) .qty{grid-column:auto;text-align:right}
}
/* a list too narrow for the label beside the change (a small phone, or large text): the label goes
   above it, and the change has the whole row */
@container (max-width:20em){
  .changes li,.changes li.add,.changes li.del:not(.action){grid-template-columns:minmax(0,1fr) auto}
  /* capitals spaced out don't fit a word like DESCRIPTION across a 320 px phone at 200% text */
  .changes .act{grid-column:1/-1;justify-self:start;text-transform:capitalize;letter-spacing:0}
  .changes .name{grid-column:1}
  .changes .qty{grid-column:2;text-align:right}
  .changes li.action .qty,.changes li.chg .qty{grid-column:1/-1;text-align:left}
}
details.raw{margin:.5rem 0 0} details.raw summary{cursor:pointer;color:var(--text-muted);font-size:.9rem}
.apply{margin-top:1rem;padding:1rem;border:2px solid var(--orange);border-radius:var(--radius-panel);
  background:var(--orange-tint)}
.apply h2{margin:0 0 .4rem;font-size:1.25rem;font-weight:900} .apply p{margin:0 0 .25rem}
.apply .choice{margin-top:.9rem}
/* Locked (until the click guard unlocks them), Apply and Reject keep labels that read: outlined in
   their colour, not faded. */
.apply form.guarded button[data-guard]:disabled{opacity:1;background:transparent;
  border:1px dashed currentColor}
.apply form.guarded button.primary:disabled{color:var(--orange-text)}
.apply form.guarded button.danger:disabled{color:var(--danger-text)}

/* consent page: who is asking, and where you end up */
.consent .ask{font-size:1.1rem;margin:0 0 .75rem}
.consent .who{display:flex;flex-direction:column;gap:.15rem;padding:.9rem 1rem;margin:0 0 1rem;
  border:1px solid var(--border);border-left:5px solid var(--blue);border-radius:var(--radius-panel);
  background:var(--blue-tint)}
.consent .who .app{font-size:1.5rem;font-weight:900;line-height:1.2;overflow-wrap:anywhere}
.consent .who .host{color:var(--text-muted);font-size:.93rem;overflow-wrap:anywhere}
.consent .meta{font-size:1rem} .consent .meta code{font-size:1em}

/* companion layout: wide pages */
.wide .wrap{max-width:2300px}
.wide main.wrap{padding-left:1rem;padding-right:1rem}

/* below 1024 px the links sit closer and a little smaller, so the search box keeps room for its
   whole placeholder beside an admin's eight links, in wide fonts too */
@media (max-width:1023.98px){ .topbar nav.site a{padding:0 .4rem;font-size:.93rem}
  .brand{margin-right:.25rem} }
/* and with large text (a raised font size, a text zoom) from 1024 px, while the bar is narrower
   than 68em: a font about 15% wider than Lato (DejaVu Sans, a monospace font) needs up to 65em
   for an admin's one row with the full-size links, and at most 57.5em with these, so the row
   fits until the links wrap at 58em (below; the wrapped rows keep the full size). 68em is 952 px
   at the normal size, so there the 1024 px rule above still decides. */
@media (width >= 1024px){ @container topbar (58em <= width < 68em){
  body:not(.app) .topbar nav.site a{padding:0 .4rem;font-size:.93rem}
  body:not(.app) .topbar .brand{margin-right:.25rem} } }
/* The proposal review's three choices sit at one height, and the deck banner's
   Comments link matches its Like and Bookmark buttons. */
.choice button,.choice .btn{min-height:48px}
.banner .social a.soc,.banner .social button.soc{height:34px;min-height:34px}
/* A control that carries text a member typed (a username in Follow, a deck's category in the
   editor's chips, a folder's name) shrinks and ellipsises inside its row, with the whole text
   in its title, instead of widening the page (a 40-character username, for one). */
.soc,.btn.soc,.addbox button.tchip,.chips button{max-width:100%;min-width:0}
.soc > span,.addbox button.tchip{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.social .confirm{max-width:100%;flex-wrap:wrap;min-width:0}
.social .confirm > span{min-width:0;overflow-wrap:anywhere}
/* Touch screens: every control at least 44 px tall (the release's rule), including the small
   ones a mouse never minds: the editor's add-to chips, the deck page's category
   button and odds form, folded Details, footer links, the brand mark, list links, crumbs. */
@media (pointer:coarse){
  .addbox button.tchip,.popular a,.cardview .chip{min-height:44px}
  .odds form.oddsform select,.odds form.oddsform input,.odds form.oddsform button{min-height:44px;height:44px}
  /* kept a list item (a flex summary loses its native fold-out marker in Chromium); the padding
     and the minimum make the 44 px */
  details > summary{min-height:44px;padding:.6rem 0}
  .tabbar details.more > summary{padding:0}  /* the More tab has its own 56 px; padding would grow the bar */
  details.dd > summary.icon-only{min-height:44px;min-width:44px}
  .topbar nav a,.topbar nav summary,.topbar nav button{height:44px}
  .topbar .icon-btn{width:44px}
  .msel-list [role=option]{min-height:44px}
  .check,.addbox form.addcard .go .foil{min-height:44px}
  .reports-page a.rp-deck,.coll-totals a.ext{display:inline-flex;align-items:center;min-height:44px}
  .guide .anchor{display:inline-flex;align-items:center;justify-content:center;min-width:44px;min-height:44px;
    margin-top:-.6rem;margin-bottom:-.6rem}
  footer.site .links a{display:inline-flex;align-items:center;min-height:44px;padding:0 .5rem}
  .brand{min-height:44px;--brand-line:max(44px,1.35em)}
  .hrow a.kind,.hrow a.name,.proposals a.name,.report .crumbs a,li > a.name{display:inline-block;
    padding:.6rem 0;min-height:44px;box-sizing:border-box}
  .banner .social a.soc,.banner .social button.soc{height:44px;min-height:44px;border-radius:22px}
  /* :not(._) only raises specificity: the pages' own rules (same selectors, loaded later) set
     2rem and 30px and would otherwise win */
  .hrow details > summary:not(._),.report details summary:not(._){min-height:44px;display:flex;
    align-items:center}
  ul.rows .row:not(._){height:44px}
}
/* A narrow window with a mouse (600 to 800 px, a half-width desktop window or an open Fold):
   the bar keeps its text links and drops the brand's word, so nothing runs under the account
   button and the page needs no sideways scroll. The word returns above 800 px. */
@media (min-width:600px) and (max-width:799.98px) and (hover:hover) and (pointer:fine){
  .topbar .brand .word{display:none} }
/* Where the bar has no text links (a phone, the app): the brand takes the room the account side
   leaves, and a long MTG_SERVER_NAME shortens the brand's word (an ellipsis). */
.topbar .left{flex:1 1 0}
.topbar nav.site{flex:none}
/* More ▾ on a computer narrower than 85.7em of the bar's 14 px text (1200 px at the normal size;
   a container query, so large text folds the links too): Proposals and the links after it go
   under it */
.topbar nav.site .navmore{display:none}
.topbar nav.site .navmore > summary.on{color:var(--orange)}
.topbar nav.site .navmore > summary span{font-size:.8em}
@container topbar (width < 85.7em){
  body:not(.app) .topbar nav.site a.fold{display:none}
  body:not(.app) .topbar nav.site .navmore{display:block} }
.topbar .right{flex:0 1 auto}
.brand{min-width:0}
/* Where it has them (600 px and wider, not the app): the brand, the links and the account side
   are the bar's own flex items (.left only groups the brand and the links in the markup). The
   links never shrink. When the row is short, the brand's word gives way first (an ellipsis), then
   the search box, down to its 9rem; before even that is not enough, the links wrap (the container
   queries below). With .left{flex:1 1 0} the left side could shrink below its links, which then
   ran under the search box, so a click on History or Admin landed in the box. */
@media (min-width:600px){
  body:not(.app) .topbar .wrap{gap:.25rem}
  body:not(.app) .topbar .left{display:contents}
  body:not(.app) .topbar .brand{flex:0 1000 auto;min-width:25px}
  body:not(.app) .topbar .right{min-width:auto;margin-left:auto;padding-left:.75rem} }
.brand .word{overflow:hidden;text-overflow:ellipsis;min-width:0}
/* Where the brand's word gives way, it shows at least 3em of it ("MTG…") or none: below that the
   word wraps onto a second line inside the brand, 100px below the first, which the brand clips,
   instead of showing a stub such as "M.". The brand keeps its one line's height (--brand-line;
   40px on a touch screen), and the empty ::before holds the first line at that height, so the
   mark stays in the middle with the word gone too. */
@media (min-width:600px){
  body:not(.app) .topbar .brand{flex-wrap:wrap;row-gap:100px;overflow:hidden;
    height:var(--brand-line,max(25px,1.35em))}
  body:not(.app) .topbar .brand::before{content:"";width:0;margin-right:-.5rem;
    height:var(--brand-line,max(25px,1.35em))}
  body:not(.app) .topbar .brand .word{flex:1 1 3em;max-width:max-content} }
/* A font about 15% wider than Lato (DejaVu Sans) needs up to about 45em for an admin's links on
   one row at 600 to 899 px, so a bar wider than 42.5em (where they wrap, below) could still be
   too short for them. There the links wrap within the row (the bar grows) instead of running past
   the window, but only as the last resort: when the row is short, the brand's word gives way
   first, then the bar's right padding (the ::after, 2rem, as before, when the account button
   could sit nearer the edge), and only then do the links. Any shrink of the links at all would
   wrap them, so the brand's and the padding's shrink factors are large enough that the links'
   share stays under a pixel's 1/64 while either can still give way. */
@media (min-width:600px) and (max-width:899.98px) and (not ((pointer:coarse) or (hover:none))){
  body:not(.app) .topbar .wrap{height:auto;min-height:50px;padding-right:0}
  body:not(.app) .topbar .wrap::after{content:"";flex:0 1000000 2rem;min-width:0;margin-left:-.25rem}
  body:not(.app) .topbar .brand{flex-shrink:100000000}
  body:not(.app) .topbar nav.site{flex:0 1 auto;flex-wrap:wrap;min-width:0} }
/* Large text with a mouse (a raised font size, a text-only zoom, 200% text): when the bar's text
   links no longer fit on one row beside the brand and the account button, they wrap onto more
   rows between them (_NAV_ROW_RULES), so the page never scrolls sideways and nothing runs under
   the search box or the account button. The bar is a size container, so the test is in the bar's own em
   (its text size): 42.5em is just under a 600 px window at the normal size, where the eight links
   of an admin still fit on one row; with the search box (900 px and wider) one row needs 58em.
   The rail and the phone layout (touch, the app) hide these links, so they are left as they are. */
.topbar{container:topbar / inline-size}
@media (min-width:600px) and (max-width:899.98px) and (not ((pointer:coarse) or (hover:none))){
  @container topbar (width < 42.5em){ __NAVROW__ } }
@media (width >= 900px){ @container topbar (width < 58em){ __NAVROW__ } }
/* home dashboard: section tiles (Archidekt's landing cards) */
.home .hero{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:1rem;
  margin:0 0 1rem}
.home .hero h1{margin:0;font-size:1.6rem}
.home .hero .sub{color:var(--text-muted);margin:.2rem 0 0}
.home .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,14rem),1fr));gap:1rem;
  margin:0 0 1rem}
.home .tile{display:flex;flex-direction:column;gap:.35rem;padding:1rem;background:var(--surface);
  border:1px solid var(--border);border-radius:var(--radius-panel);color:var(--text);text-decoration:none;
  min-height:7.5rem;transition:border-color .2s ease}
.home .tile:hover{border-color:var(--orange)}
.home .tile .ti{display:flex;align-items:center;gap:.5rem;font-weight:900;font-size:1.1rem}
.home .tile .ti svg{color:var(--orange)}
.home .tile .n{font-size:1.6rem;font-weight:900;font-variant-numeric:tabular-nums;line-height:1}
.home .tile .d{color:var(--text-muted);font-size:.9rem}
.home .tile.cta{border-color:var(--orange);background:var(--orange-tint)}
.home .searchbox{display:flex;gap:.5rem;align-items:end;flex-wrap:wrap}
.home .searchbox .field{flex:1 1 14rem;margin:0}
.home .searchbox button{margin:0}
.home .recent{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,10rem),1fr));gap:.75rem}
.home .recent a{display:block;position:relative;aspect-ratio:16/9;border-radius:var(--radius);overflow:hidden;
  background:linear-gradient(135deg,#3a3a3a,#1f1f1f) center/cover no-repeat;color:#fff;text-decoration:none;
  font-weight:700}
.home .recent a[style]{background-color:var(--surface-3)}
.home .recent a span{position:absolute;left:0;right:0;bottom:0;padding:.4rem .5rem;
  background:linear-gradient(to top,rgba(0,0,0,.85),rgba(0,0,0,0));font-size:.9rem;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.home details.connect summary{cursor:pointer;font-weight:700;display:flex;flex-wrap:wrap;gap:.25rem .75rem;
  align-items:baseline}
.home details.connect summary .addr{font-weight:400;font-size:.9rem;word-break:break-all}
.home .panel-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;
  gap:.5rem 1rem;margin:0 0 .75rem}
.home .panel-head h2{margin:0}
/* the deck list couldn't be read: the /decks notice inside the panel, over any covers read */
.home .decks-unreadable p{margin:0}
.home .decks-unreadable .actions{margin-top:.75rem}
.home .decks-unreadable .actions .btn{min-height:44px}
.home .decks-unreadable + .recent{margin-top:1rem}
/* placeholders while a deck list is still being read (decks.js swaps the real list in) */
.skeleton>*{display:block;background:var(--surface-2);border:1px solid var(--border);
  border-radius:var(--radius);animation:skeleton 1.4s ease-in-out infinite}
.home .recent.skeleton>span{aspect-ratio:16/9}
ul.decklist.skeleton>li{height:230px;list-style:none}
@keyframes skeleton{50%{opacity:.55}}
@media (prefers-reduced-motion:reduce){ .skeleton>*{animation:none} }
/* Adaptive navigation after Android's window size classes. Compact (under 600px, any device): a
   bottom tab bar (Archidekt's floatingToolbar). Medium (600 to 899px) on a touch screen, such as
   an unfolded foldable or a tablet in a browser, and the Android app at every width from 600px:
   a navigation rail down the left edge. Wider browsers, or a browser whose "Desktop site" switch
   widens the viewport past 900px: the desktop top bar. Media queries track the live viewport, so
   folding, rotating, split screen and pop-up windows re-flow at once; see _MOBILE_RULES and
   _RAIL_RULES below. */
.tabbar{display:none}
.topbar .searchbtn{display:none}
.tabbar details.more{position:relative}
/* An open top-bar suggestion list or phone More sheet sits above the editor's save bar
   (z-index 20), so a tap on a row can never land on Save changes underneath. */
.topbar:focus-within{z-index:25}
body .tabbar:has(details.more[open]){z-index:25}
.tabbar details.more summary{list-style:none;cursor:pointer}
.tabbar details.more summary::-webkit-details-marker{display:none}
.tabbar details.more .menu.sheet{position:fixed;left:50%;transform:translateX(-50%);top:auto;
  bottom:calc(56px + env(safe-area-inset-bottom) + .5rem);width:min(18rem,calc(100vw - 2rem));
  z-index:30;border-radius:16px;padding:.5rem}
.tabbar details.more .menu.sheet a{min-height:44px;padding:0 1rem}
.tabbar details.more .menu.sheet a svg{width:20px;height:20px}
__MOBILE__
/* search / filter rows */
.search{display:flex;gap:0;margin:0 0 .75rem}
.search input{flex:1;border-radius:var(--radius) 0 0 var(--radius);border-right:0}
.search button{margin:0;width:auto;border-radius:0 var(--radius) var(--radius) 0}
.openform button{margin-top:.6rem}

footer.site{color:#fff;background:var(--navbar-bg);text-align:center;padding:20px 20px 24px;
  display:flex;flex-direction:column;align-items:center;gap:.5rem;font-size:.86rem}
footer.site .links{display:flex;flex-wrap:wrap;justify-content:center;gap:1rem}
footer.site .links a{color:#fff;text-decoration:none;font-weight:700;text-transform:uppercase;
  letter-spacing:.04em}
footer.site .links a:hover{color:var(--orange)}
footer.site .legal{color:#ababab;max-width:60rem}
/* Windows High Contrast and other forced palettes: the fills that carry state become borders */
@media (forced-colors: active){
  .switch .track{border:1px solid ButtonText} .switch input:checked + .track{border-color:Highlight}
  .switch .track::after{background:ButtonText}
  .tabbar > a[aria-current=page],.topbar nav a[aria-current=page]{text-decoration:underline}
  .tabbar > a[aria-current=page]::before{background:Highlight}
  .home .tile,.card,.panel,.tile,.menu,.toast{border:1px solid CanvasText}
  :focus-visible,input:focus,select:focus,textarea:focus{outline:2px solid Highlight}
  input[type=checkbox]:checked{border-color:Highlight;background-color:Highlight}
  .btn-primary,button.primary,.badge,.pill{forced-color-adjust:none}
}
/* print: the page's own content on white, without the bars, menus and the footer */
@media print{
  html{scroll-padding:0} body{background:#fff;color:#000;display:block}
  .topbar,.tabbar,footer.site,a.skip,.editbar,.toast,.msel-list,.msel-scrim,.suggest-list,
  details.dd .menu,.cardview,.picker,button[type=submit],.form-actions,.actions{display:none !important}
  body.has-tabbar{padding-left:0} main.wrap{padding:0;max-width:none}
  .card,.panel{border:1px solid #999;break-inside:avoid;box-shadow:none}
  a{color:inherit;text-decoration:underline}
}
"""
)

# The compact layout (bottom tab bar), written once and applied under each condition (a media
# query cannot see a body class, so the rules are emitted per condition). {b} prefixes selectors
# on <body>.
_MOBILE_RULES = """
  body{b} .topbar nav.site .navmore{display:none !important}
  body{b} .topbar .wrap{padding:0 1rem;gap:.5rem;padding-left:max(1rem,env(safe-area-inset-left));
    padding-right:max(1rem,env(safe-area-inset-right))}
  body{b} .topbar nav.site a:not(.keep){display:none}
  body{b} .topbar .searchbtn{display:inline-flex}
  body{b} .tabbar > a.railonly{display:none}
  body{b}.has-tabbar main.wrap{padding-bottom:calc(5.5rem + env(safe-area-inset-bottom))}
  body{b}.has-tabbar footer{padding-bottom:calc(5.5rem + env(safe-area-inset-bottom))}
  body{b} .tabbar{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);position:fixed;
    left:0;right:0;bottom:0;z-index:8;background:var(--toolbar-bg);color:var(--toolbar-text);
    border-top:1px solid var(--border);
    padding-bottom:env(safe-area-inset-bottom);box-shadow:0 -2px 10px rgba(0,0,0,.08)}
  body{b} .tabbar > a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.2rem;
    height:56px;color:var(--toolbar-text);text-decoration:none;font-weight:700;font-size:11px;
    transition:color .2s ease;-webkit-tap-highlight-color:transparent}
  body{b} .tabbar > a svg{width:22px;height:22px;transition:transform .15s ease}
  body{b} .tabbar > a:active svg{transform:scale(.92)}
  body{b} .tabbar > a:hover,body{b} .tabbar > a:focus-visible,
  body{b} .tabbar > a[aria-current=page]{color:var(--toolbar-active)}
  body{b} .tabbar > a[aria-current=page]::before{content:'';position:absolute;top:0;width:2.5rem;height:3px;
    background:var(--orange);border-radius:0 0 3px 3px}
  body{b} .tabbar > a{position:relative}
  body{b} .tabbar details.more summary{display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:.2rem;height:56px;color:var(--toolbar-text);font-weight:700;font-size:11px;
    position:relative;-webkit-tap-highlight-color:transparent}
  body{b} .tabbar details.more summary svg{width:22px;height:22px}
  /* large text (a phone's font scale): a label wraps inside its tab and the bar grows */
  body{b} .tabbar > a,body{b} .tabbar details.more summary{min-width:0;height:auto;min-height:56px;
    text-align:center;line-height:1.15;overflow-wrap:anywhere;hyphens:auto}
  body{b} .tabbar details.more summary.on,
  body{b} .tabbar details.more[open] summary{color:var(--toolbar-active)}
  body{b} details.dd .menu{min-width:min(14rem,calc(100vw - 16px))}
"""
# The medium layout: the same tabs as a navigation rail (80px wide, icon in a pill with the label
# under it, as Material's rail draws it) fixed to the left edge; the top bar keeps the brand and
# the account menu and loses its text links. The rail lists every page (More's too) and drops
# More; on a short screen it scrolls. The body is padded by the rail's width so every page
# re-centres in the remaining space.
_RAIL_RULES = """
  body{b} .topbar nav.site .navmore{display:none !important}
  body{b} .topbar nav.site a:not(.keep){display:none}
  body{b}.has-tabbar{padding-left:calc(80px + env(safe-area-inset-left))}
  body{b} .topbar .wrap{padding:0 1.25rem;padding-right:max(1.25rem,env(safe-area-inset-right))}
  body{b} .tabbar{display:flex;flex-direction:column;align-items:stretch;gap:.25rem;position:fixed;left:0;
    top:0;bottom:0;width:calc(80px + env(safe-area-inset-left));z-index:9;background:var(--toolbar-bg);
    color:var(--toolbar-text);border-right:1px solid var(--border);overflow-y:auto;scrollbar-width:none;
    padding:calc(.75rem + env(safe-area-inset-top)) 0 calc(.5rem + env(safe-area-inset-bottom))
      env(safe-area-inset-left)}
  body{b} .tabbar > a{position:relative;display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:.3rem;height:64px;flex:none;color:var(--toolbar-text);text-decoration:none;
    font-weight:700;font-size:11px;-webkit-tap-highlight-color:transparent}
  body{b} .tabbar > a svg{width:24px;height:24px;position:relative;z-index:1}
  body{b} .tabbar > a::before{content:'';position:absolute;top:7px;left:50%;width:56px;height:32px;
    margin-left:-28px;border-radius:16px;background:transparent;transition:background .15s ease}
  body{b} .tabbar > a:hover::before,
  body{b} .tabbar > a:focus-visible::before{background:rgba(127,127,127,.18)}
  body{b} .tabbar > a[aria-current=page]{color:var(--toolbar-active)}
  body{b} .tabbar > a[aria-current=page]::before{background:rgba(255,111,0,.22)}
  body{b} .tabbar details.more{display:none}
  body{b} details.dd .menu{min-width:min(14rem,calc(100vw - 16px))}
"""
CSS = CSS.replace(
    "__MOBILE__",
    "@media (max-width:599.98px){ .topbar .brand .word{display:none} "
    + _MOBILE_RULES.replace("{b}", "")
    + "}\n@media (min-width:600px) and (max-width:899.98px) and ((pointer:coarse) or (hover:none)){"
    + _RAIL_RULES.replace("{b}", "")
    + "}\n@media (min-width:600px){"
    + _RAIL_RULES.replace("{b}", ".app")
    + "}\n"
    + "body.app footer.site{display:none}\n"
    + "body.app .topbar{padding-top:env(safe-area-inset-top)}\n"
    + "body.app .topbar .brand .word{display:inline}\n",
)
# The top bar's second row for large text with a mouse (the container queries above): the brand
# mark and the account side share the first row, the text links wrap in the row under them.
# The links wrap between the brand and the search box and account button, which sit at the foot of
# the links' last row, so the bar reads, and Tab goes, brand, links, search, account at every text
# size (with the links on a row of their own under the account side, Tab
# went from the links' row back up to search and account).
_NAV_ROW_RULES = (
    "body:not(.app) .topbar .wrap{flex-wrap:wrap;height:auto;row-gap:0;column-gap:1rem;padding-bottom:.35rem;"
    "padding-right:2rem}"
    "body:not(.app) .topbar .wrap::after{display:none}"
    "body:not(.app) .topbar .left{display:contents}"
    "body:not(.app) .topbar .brand{min-height:50px;align-self:flex-start}"
    "body:not(.app) .topbar .brand .word{display:none}"
    "body:not(.app) .topbar nav.site{flex:1 1 12em;flex-wrap:wrap;min-width:0}"
    "body:not(.app) .topbar .right{margin-left:auto;padding-left:0;min-height:50px;align-self:flex-end}"
)
CSS = CSS.replace("__NAVROW__", _NAV_ROW_RULES)

# Card size S / M / L (0.7.29): M is exactly the size each
# page had before; S is about 75% and L about 135% of its width. On a phone the deck page shows
# 3, 2 or 1 cards across; on a wide screen My decks shows 6, 5 or 4 decks across. The choice
# follows the account (prefs.py) and sits on <html data-card-size>, set when the page is drawn
# (no flash) and by static/prefs.js when the S / M / L switch is pressed.
CARD_SIZES = ("s", "m", "l")
CSS += """
/* the S / M / L switch, styled as card search's Grid / List switch */
.sizeseg{display:inline-flex;border:1px solid var(--control-border);border-radius:var(--radius);
  overflow:hidden;vertical-align:middle}
.sizeseg button{margin:0;min-width:2.75rem;min-height:calc(var(--ctl) - 2px);height:auto;padding:0 .75rem;
  border:0;border-radius:0;background:transparent;color:var(--text);font:inherit;font-weight:400;box-shadow:none}
.sizeseg button + button{border-left:1px solid var(--control-border)}
.sizeseg button[aria-pressed=true]{background:var(--orange-tint);font-weight:700}
.sizeseg button:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}
@media (pointer:coarse){ .sizeseg button{min-height:44px;min-width:44px} }
.field.cardsize .sizeseg{display:flex;width:max-content}
.cs-head .cardsize{display:inline-flex;align-items:center;gap:.5rem}
.cs-head .cardsize .lbl{font-weight:400;color:var(--text-muted)}
@media (min-width:1000.02px){
  .listbar .controls:has(.cardsize){
    grid-template-columns:1fr minmax(140px,auto) auto minmax(140px,auto) auto} }
/* card search switches Grid / List without a reload: the size switch goes with the Grid view */
.cs-head:has(input[name=view][value=list]:checked) .cardsize{display:none}
/* the deck page: stacks, grid and scroll */
html[data-card-size=s] .deckview.stacks{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}
html[data-card-size=l] .deckview.stacks{grid-template-columns:repeat(auto-fill,minmax(270px,1fr))}
html[data-card-size=s] .deckview.grid .cards{grid-template-columns:repeat(auto-fill,minmax(128px,1fr))}
html[data-card-size=l] .deckview.grid .cards{grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
html[data-card-size=s] .deckview.scroll .c{flex-basis:clamp(105px,16.5vw,150px)}
/* at S a stack's name gets its own line under the grip and menu, so a narrow stack still shows it
   whole, and a tile without a picture starts its name below the 38 px quantity corner */
html[data-card-size=s] .deckview.stacks .stackhead h2{flex-wrap:wrap;row-gap:0}
html[data-card-size=s] .deckview.stacks .stackhead .title{
  order:2;flex:1 0 100%;white-space:normal;overflow-wrap:anywhere;hyphens:auto;-webkit-hyphens:auto}
html[data-card-size=s] .deckview .c .ph{padding-top:max(12%,34px)}
html[data-card-size=l] .deckview.scroll .c{flex-basis:clamp(190px,30vw,270px)}
/* My decks and the folders in it */
html[data-card-size=s] ul.decklist.grid{grid-template-columns:repeat(6,minmax(0,1fr))}
html[data-card-size=l] ul.decklist.grid{grid-template-columns:repeat(4,minmax(0,1fr))}
html[data-card-size=s] ul.decklist .thumb{height:140px}
html[data-card-size=l] ul.decklist .thumb{height:220px}
html[data-card-size=s] ul.folderlist.grid{
  grid-template-columns:repeat(auto-fill,minmax(min(100%,11.25rem),1fr))}
html[data-card-size=l] ul.folderlist.grid{
  grid-template-columns:repeat(auto-fill,minmax(min(100%,20.25rem),1fr))}
/* card search, the collection and the printing picker */
html[data-card-size=s] .cardgrid{grid-template-columns:repeat(auto-fill,minmax(min(100%,7.9rem),1fr))}
html[data-card-size=l] .cardgrid{grid-template-columns:repeat(auto-fill,minmax(min(100%,14.2rem),1fr))}
html[data-card-size=s] ul.collgrid{
  grid-template-columns:repeat(auto-fill,minmax(min(100%,max(128px,9rem)),1fr))}
html[data-card-size=l] ul.collgrid{
  grid-template-columns:repeat(auto-fill,minmax(min(100%,max(230px,16.2rem)),1fr))}
html[data-card-size=s] .printpick .ppgrid{grid-template-columns:repeat(auto-fill,minmax(112px,1fr))}
html[data-card-size=l] .printpick .ppgrid{grid-template-columns:repeat(auto-fill,minmax(203px,1fr))}
@media (max-width:1500px){
  html[data-card-size=s] ul.decklist.grid{grid-template-columns:repeat(5,minmax(0,1fr))}
  html[data-card-size=l] ul.decklist.grid{grid-template-columns:repeat(3,minmax(0,1fr))} }
@media (max-width:1200px){
  html[data-card-size=s] ul.decklist.grid{grid-template-columns:repeat(4,minmax(0,1fr))}
  html[data-card-size=l] ul.decklist.grid{grid-template-columns:repeat(2,minmax(0,1fr))} }
@media (max-width:1000px){
  html[data-card-size=s] ul.decklist.grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  html[data-card-size=l] ul.decklist.grid{grid-template-columns:minmax(0,1fr)} }
@media (max-width:700px){
  html[data-card-size=s] .printpick .ppgrid{grid-template-columns:repeat(auto-fill,minmax(104px,1fr))}
  html[data-card-size=l] .printpick .ppgrid{grid-template-columns:repeat(auto-fill,minmax(186px,1fr))} }
@media (max-width:600px){
  html[data-card-size=s] .deckview.grid .cards,
  html[data-card-size=s] .deckview.stacks{grid-template-columns:repeat(3,minmax(0,1fr))}
  html[data-card-size=l] .deckview.grid .cards,
  html[data-card-size=l] .deckview.stacks{grid-template-columns:minmax(0,1fr)}
  html[data-card-size=s] ul.decklist.grid{grid-template-columns:repeat(2,minmax(0,1fr))} }
"""

# UI standard v1 (docs: UI-STANDARD.md §3-§5): the shared pieces of the dashboard and list pages,
# drawn by ui.py. Theme tokens only. Names that the theme or a page already uses for something
# else carry a ui- prefix (ui-lbl, ui-app, ui-when, ui-chip, ui-chg, ui-locked); the rest keep the
# approved mockup's names. It comes before the top bar's search rules: the desktop (900 px)
# layout never keys off a touch screen (tests/test_social_layout.py).
UI_CSS = """
/* §3 width: lists and tables are wide, up to 1200 px (render(main_class='ui-wide')) */
main.wrap.ui-wide{max-width:1200px}
.ui-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem 1rem;margin:0 0 .25rem}
.ui-head h1{margin:0}
/* enlarged text on a phone: the heading shrinks with the screen so a short word stays whole
   (overflow-wrap still breaks a word too long for any screen) */
@media (max-width:599.98px){.ui-head h1{font-size:min(2rem,14vw)}}
.ui-sub{color:var(--text-muted);margin:.1rem 0 1rem;font-size:.93rem}

/* §4 labels: a word, a colour and an icon */
.ui-lbl{display:inline-flex;align-items:center;gap:.3rem;height:24px;padding:0 .55rem 0 .45rem;
  border-radius:999px;font-size:.82rem;font-weight:700;line-height:1;white-space:nowrap;
  border:1px solid transparent;background:var(--surface-2);color:var(--text);vertical-align:middle}
.ui-lbl svg.i{width:15px;height:15px;stroke-width:2.2;vertical-align:0}
.ui-lbl.orange{background:var(--orange-tint);color:var(--orange-text)}
.ui-lbl.green{background:var(--green-tint);color:var(--green-text)}
.ui-lbl.red{background:var(--red-tint);color:var(--red-text)}
.ui-lbl.blue{background:var(--blue-tint);color:var(--blue-text)}
.ui-lbl.grey{background:transparent;color:var(--text-muted);border-color:var(--card-border)}
.ui-lbl.plain{background:var(--surface-2);color:var(--text)}
/* the app a row came from: its name with a letter tile; a dash when none is recorded */
.ui-app{display:inline-flex;align-items:center;gap:.35rem;color:var(--text);font-size:.9rem;
  white-space:nowrap}
.ui-app i{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:4px;font-style:normal;
  font-size:.7rem;font-weight:900;background:var(--surface-2);color:var(--text)}
.ui-app.none{color:var(--text-muted)}
/* §3 dates: relative within 7 days, then the date; the full date and time in the tooltip */
.ui-when{color:var(--text-muted);font-size:.9rem;white-space:nowrap;font-variant-numeric:tabular-nums}

/* tabs with counts */
.ui-tabs{display:flex;gap:.25rem;border-bottom:1px solid var(--card-border);margin:0 0 .75rem;overflow-x:auto}
.ui-tabs a{display:inline-flex;align-items:center;gap:.45rem;min-height:44px;padding:0 1rem;
  color:var(--text-muted);font-weight:700;text-decoration:none;border-bottom:3px solid transparent;
  margin-bottom:-1px;white-space:nowrap}
.ui-tabs a[aria-current]{color:var(--text);border-bottom-color:var(--orange)}
.ui-tabs .n{display:inline-grid;place-items:center;min-width:22px;height:22px;padding:0 6px;
  border-radius:999px;
  background:var(--surface-2);color:var(--text);font-size:.8rem}

/* quick-filter chips with counts */
.ui-chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 .6rem}
.ui-chip{display:inline-flex;align-items:center;gap:.35rem;min-height:36px;padding:0 .8rem;
  border-radius:999px;
  border:1px solid var(--control-border);color:var(--text);font-weight:700;font-size:.9rem;
  text-decoration:none;
  background:transparent}
.ui-chip:hover{color:var(--text);background:var(--surface-2)}
@media (pointer:coarse){.ui-chip{min-height:44px}}
.ui-chip[aria-pressed=true],.ui-chip[aria-current]{background:var(--orange-tint);
  border-color:var(--orange-text);
  color:var(--orange-text)}
.ui-chip .n{color:var(--text-muted);font-weight:700}
.ui-chip[aria-pressed=true] .n,.ui-chip[aria-current] .n{color:inherit}
.ui-chip svg.i{width:15px;height:15px}
.list-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:.25rem 1rem;
  margin:0 0 .5rem}
.list-head h2{margin:0;font-size:1.2rem}
.list-head h2 .ui-sub{font-size:1rem;margin:0}
.list-head > .ui-sub{margin:0}

/* toolbar: one search box, then the filters */
.ui-tools{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:0 0 .75rem}
.ui-tools .search{flex:1 1 16rem;position:relative;min-width:0;align-self:stretch;display:flex;
  align-items:center}
.ui-tools label.search{margin:0;font-weight:400}
.ui-tools .search svg.i{position:absolute;left:.7rem;top:50%;transform:translateY(-50%);width:16px;
  height:16px;
  color:var(--text-muted);pointer-events:none}
.ui-tools .search input{width:100%;margin:0;padding-left:2.2rem;font-weight:400;
  border-right:1px solid var(--control-border);border-radius:var(--radius)}
.ui-tools .f{flex:0 1 10.5rem;min-width:8.5rem;display:flex;align-items:center}
.ui-tools select{margin:0}
@media (max-width:599.98px){.ui-tools .f{flex:1 1 calc(50% - .5rem)}}
.ui-note{color:var(--text-muted);font-size:.88rem;margin:.25rem 0 .75rem}

/* §3 the table: lined-up columns from 900 px (--cols); 600-899 px the low-priority columns
   (.md-hide) fold into a second line (.sub) under the title (--cols-md); below 600 px two-line rows
   (--cols-sm) with the status label beside the title (.stin) */
.ui-table{border:1px solid var(--card-border);border-radius:var(--radius-panel);background:var(--surface);
  margin:0 0 1rem}
.ui-table .tr{display:grid;grid-template-columns:var(--cols);align-items:center;gap:0 .75rem;min-height:44px;
  padding:.45rem .75rem;border-top:1px solid var(--border-soft)}
.ui-table .tr:first-child{border-top:0}
.ui-table .th{font-size:.8rem;font-weight:700;color:var(--text-muted);text-transform:uppercase;
  letter-spacing:.03em;background:var(--surface-2);min-height:36px}
.ui-table .th button{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:.2rem}
.ui-table .th button:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.ui-table .group{background:var(--surface-2);font-weight:700;grid-template-columns:32px 1fr auto}
.ui-table .group a{font-weight:700}
.ui-table .group .meta{color:var(--text-muted);font-weight:400;font-size:.88rem;white-space:nowrap}
.ui-table .tr.sel{background:var(--orange-tint)}
.ui-table .tr.open{box-shadow:inset 3px 0 0 var(--orange)}
.ui-table .title{min-width:0}
.ui-table .title .t{display:block;overflow-wrap:anywhere}
.ui-table .title .no{color:var(--text-muted);font-weight:700;margin-right:.35rem;
  font-variant-numeric:tabular-nums}
.ui-table .title .deck{display:block;color:var(--text-muted);font-size:.86rem}
.ui-table .sub{display:none;margin-top:.3rem;gap:.35rem .6rem;flex-wrap:wrap;align-items:center}
.ui-table .stin{display:none}
.ui-table .chk{display:grid;place-items:center;width:32px;height:44px;margin:-.45rem 0;cursor:pointer}
.ui-table .chk input{width:20px;height:20px;margin:0;accent-color:var(--orange-ctl)}
.ui-table .act{justify-self:end}
.ui-table .act .acts{display:inline-flex;gap:.4rem}
.ui-table .act .btn,.ui-table .act button{white-space:nowrap;display:inline-flex;align-items:center;
  gap:.35rem}
.ui-table .act .chev{display:none}
.ui-table .flag{display:block;color:var(--text-muted);font-size:.84rem;margin-top:.15rem}
.ui-table .flag b{color:var(--red-text)}
.ui-table .sm-only{display:none}
.daygroup,.ui-table .tr.daygroup{background:transparent;color:var(--text-muted);font-weight:700;
  font-size:.85rem;
  grid-template-columns:1fr;min-height:32px}
@media (pointer:coarse){
  /* the tick box's cell is the 44 px target; it overhangs its 32 px column into the padding */
  .ui-table .chk{width:44px;margin:-.45rem -6px}
  .ui-table .th button,.ui-table .group a{display:inline-flex;align-items:center;min-height:44px}
}
@media (max-width:899.98px){
  .ui-table .tr{grid-template-columns:var(--cols-md)}
  .ui-table .md-hide{display:none}
  .ui-table .sub{display:flex}
}
@media (max-width:599.98px){
  .ui-table .tr{grid-template-columns:var(--cols-sm);padding:.55rem .6rem}
  .ui-table .th{display:none}
  .ui-table .sm-hide,.ui-table .act .sm-hide{display:none}
  .ui-table .stin{display:inline-flex;margin-left:.35rem;vertical-align:middle}
  .ui-table .group{grid-template-columns:32px 1fr}
  .ui-table .group .meta{grid-column:2;white-space:normal}
  .ui-table .sm-only{display:inline;color:var(--text-muted);font-size:.84rem;flex-basis:100%}
  .ui-table .act .btn,.ui-table .act button{padding:0 .6rem;font-size:.86rem}
  .ui-table .act .chev{display:inline-grid;place-items:center;width:44px;height:44px;font-size:1.7rem;
    line-height:1;color:var(--text);flex-basis:auto}
  .ui-table .act.linkbtn{padding:0}
}

/* §5 buttons: one orange main button; Reject, Delete and Remove as a red outline; the rest plain */
.btn-outline-danger,button.btn-outline-danger{background:transparent;color:var(--danger-text);
  border:1px solid var(--danger-text);font-weight:700}
.btn-outline-danger:hover,button.btn-outline-danger:hover{background:var(--red-tint);color:var(--danger-text)}
.btn-plain,button.btn-plain{background:transparent;border:1px solid var(--control-border);color:var(--text);
  font-weight:700}
.linkbtn{display:inline-flex;align-items:center;min-height:44px;padding:0 .4rem;font-weight:700}
@media (pointer:fine){.linkbtn{min-height:32px}}

/* the sticky action bar while a list has a selection */
.ui-bar{position:sticky;bottom:.75rem;z-index:20;display:flex;flex-wrap:wrap;
  align-items:center;gap:.5rem;padding:.6rem .75rem;margin:.5rem 0 1rem;background:var(--surface);
  border:1px solid var(--card-border);border-radius:var(--radius-lg);box-shadow:var(--shadow)}
.ui-bar[hidden]{display:none}
.ui-bar .count{font-weight:700;margin-right:auto}
.ui-bar button,.ui-bar .btn{margin:0}
/* it stays above the phone's bottom tab bar (--tab-room: its real height, static/topbar.js; 4rem
   without scripts covers it at 200% text) */
.has-tabbar .ui-bar{bottom:calc(var(--tab-room,0px) + .75rem)}
@media (max-width:599.98px){
  .has-tabbar .ui-bar{bottom:calc(var(--tab-room,calc(max(56px,
  4rem) + env(safe-area-inset-bottom))) + .5rem)} }

/* paging: "1-25 of 51", Newer and Older */
.ui-pager{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem;
  margin:.5rem 0 1rem}
.ui-pager .info{color:var(--text-muted)}
.ui-pager .btns{display:flex;flex-wrap:wrap;gap:.5rem}
.ui-pager .btns .btn,.ui-pager .btns button{margin:0}
.ui-pager button[disabled],.ui-pager .btn[aria-disabled=true]{opacity:.55}

/* §3 opening one item: from 1100 px a side panel beside the list (a split, not an overlay: the
   list folds to its 600-899 layout and stays in view); narrower, the item is its own page */
.ui-split{position:relative}
.ui-drawer{display:none}
@media (min-width:1100px){
  main.wrap.ui-wide:has(.ui-split.with-panel){max-width:1440px}
  .ui-split.with-panel{display:grid;grid-template-columns:minmax(0,1fr) 440px;gap:1rem;align-items:start}
  .ui-split.with-panel .ui-drawer{display:block;position:sticky;top:var(--bar-room,60px);
    max-height:calc(100vh - var(--bar-room,60px) - 12px);overflow:auto;background:var(--surface);
    border:1px solid var(--card-border);border-radius:var(--radius-panel);padding:1rem 1.25rem 1.5rem;
    box-shadow:var(--shadow)}
  .ui-split.with-panel .page-only{display:none}
  .with-panel .ui-table .tr{grid-template-columns:var(--cols-md)}
  .with-panel .ui-table .md-hide{display:none}
  .with-panel .ui-table .sub{display:flex}
  .with-panel .ui-widgets{grid-template-columns:repeat(auto-fit,minmax(min(100%,11rem),1fr))}
}
@media (max-width:1099.98px){
  .ui-split.with-panel .list-only{display:none}
  .ui-split.with-panel .ui-drawer{display:block}
  .ui-split.with-panel .panel-only{display:none}
}
.ui-drawer .dh{display:flex;align-items:flex-start;gap:.5rem;margin:0 0 .5rem}
.ui-drawer .dh h2{margin:0;flex:1;font-size:1.3rem;overflow-wrap:anywhere}
.ui-drawer .close{min-width:44px;min-height:44px;display:inline-grid;place-items:center;
  background:transparent;
  border:1px solid var(--control-border);padding:0;color:var(--text);text-decoration:none}
.ui-drawer .pills{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 .75rem}
.ui-meta{display:grid;grid-template-columns:auto 1fr;gap:.35rem 1rem;margin:0 0 1rem;font-size:.95rem}
.ui-meta dt{color:var(--text-muted)} .ui-meta dd{margin:0;overflow-wrap:anywhere}
.ui-why{color:var(--text-muted);font-size:.88rem}
/* a proposal's changes: ADD / REMOVE / QTY, the card and the count */
ul.ui-chg{padding:0;border:1px solid var(--card-border);border-radius:var(--radius-panel);
  margin:.5rem 0 .75rem}
.ui-chg li{display:grid;grid-template-columns:5.5rem 1fr auto;align-items:center;gap:.6rem;min-height:40px;
  padding:.3rem .6rem;border-top:1px solid var(--border-soft);list-style:none;margin:0}
.ui-chg li:first-child{border-top:0}
.ui-chg .k{font-size:.75rem;font-weight:900;text-align:center;border-radius:3px;padding:.15rem 0;
  letter-spacing:.04em}
.ui-chg .add .k{color:var(--green-text);border:1px solid var(--green-text)}
.ui-chg .rm .k{color:var(--red-text);border:1px solid var(--red-text)}
.ui-chg .qty .k{color:var(--blue-text);border:1px solid var(--blue-text)}
.ui-chg .rm .n{text-decoration:line-through}
.ui-chg .d{font-weight:900;font-variant-numeric:tabular-nums}
.ui-chg .add .d{color:var(--green-text)} .ui-chg .rm .d{color:var(--red-text)}
.ui-actions{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.75rem 0 .25rem}
.ui-actions button,.ui-actions .btn,.ui-actions form > button{margin:0}
.ui-actions form{display:contents}
.ui-hint{color:var(--text-muted);font-size:.84rem;margin:.25rem 0 0}
.ui-section{margin:1.25rem 0 .5rem;font-size:1.05rem}
.ui-next{margin:0 0 .75rem;padding:.5rem .75rem;border-left:3px solid var(--blue-text);
  background:var(--surface-2)}

/* forms (Account, Settings): rows that line up, one panel per topic, in the 52rem column */
.ui-form{max-width:52rem}
.ui-card{border:1px solid var(--card-border);border-radius:var(--radius-panel);background:var(--surface);
  padding:1rem;margin:0 0 1rem}
.ui-card > h2{margin:0 0 .25rem;font-size:1.15rem}
.ui-card > .ui-sub{margin:0 0 .75rem}
.ui-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,16rem);gap:.25rem 1rem;align-items:center;
  padding:.6rem 0;border-top:1px solid var(--border-soft)}
.ui-row:first-of-type{border-top:0}
.ui-row .lab{font-weight:700;margin:0} .ui-row .help{grid-column:1;color:var(--text-muted);font-size:.86rem}
.ui-row .ctl{grid-column:2;grid-row:1 / span 2;min-width:0}
@media (max-width:599.98px){.ui-row{grid-template-columns:1fr}.ui-row .ctl{grid-column:1;grid-row:auto}}
.ui-checks{display:flex;flex-direction:column;gap:.25rem}
.ui-checks label{display:flex;align-items:center;gap:.6rem;min-height:44px;margin:0;font-weight:400}
.ui-checks input{width:20px;height:20px;margin:0;accent-color:var(--orange-ctl)}
.ui-locked{display:inline-flex;align-items:center;gap:.3rem;color:var(--text-muted);font-size:.84rem}
/* the admin pages' own tabs: even, wrapping in full rows; the current one tinted */
.adm-nav{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,6.4rem),1fr));gap:.35rem;
  margin:0 0 .75rem}
.adm-nav a{display:flex;align-items:center;justify-content:center;min-height:var(--ctl);padding:0 .4rem;
  border:1px solid var(--card-border);border-radius:var(--radius);background:var(--surface);color:var(--text);
  font-weight:700;text-decoration:none;white-space:nowrap}
.adm-nav a[aria-current]{background:var(--orange-tint);border-color:var(--orange-text);
  color:var(--orange-text)}

/* §3a dashboard pages: a row of widgets (quick starts), then the full list */
.ui-widgets{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));gap:.75rem;
  margin:0 0 1.25rem}
.wdg{display:flex;flex-direction:column;gap:.35rem;min-width:0;padding:.85rem 1rem;background:var(--surface);
  border:1px solid var(--card-border);border-radius:var(--radius-panel)}
.wdg .lab{display:flex;align-items:center;gap:.4rem;color:var(--text-muted);font-size:.85rem;font-weight:700;
  text-transform:uppercase;letter-spacing:.03em}
.wdg .lab svg.i{width:16px;height:16px}
.wdg .big{font-size:2rem;font-weight:900;line-height:1.1;font-variant-numeric:tabular-nums}
.wdg .big small{font-size:.95rem;font-weight:700;color:var(--text-muted);margin-left:.35rem}
.wdg .det{color:var(--text-muted);font-size:.88rem;display:flex;flex-wrap:wrap;gap:.3rem .5rem;
  align-items:center}
.wdg .det .tx{display:inline}
.wdg .det a{font-weight:700}
.wdg .det .ui-lbl{height:22px;font-size:.78rem}
.wdg .go{margin-top:auto;padding-top:.35rem;display:flex;flex-wrap:wrap;gap:.4rem}
.wdg .go .btn,.wdg .go button,.wdg .go a.btn-plain{min-height:var(--ctl);padding:0 .8rem;font-size:.92rem;
  margin:0}
.wdg .go form{display:contents}
.wdg.accent{border-top:3px solid var(--orange)}
.wdg.alert{border-top:3px solid var(--red-text)}
.bar-split{display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--surface-2)}
.bar-split i{display:block;height:100%}
@media (min-width:600px) and (max-width:899.98px){
  .ui-widgets{grid-template-columns:repeat(2,minmax(0,1fr))}
  .wdg.wide-sm{grid-column:1 / -1}
}
@media (max-width:599.98px){
  /* two a row, or one a row when enlarged text leaves a half too narrow for 8.5rem */
  .ui-widgets{grid-template-columns:repeat(auto-fit,minmax(min(100%,max(8.5rem,calc(50% - .25rem))),1fr));
    gap:.5rem}
  .wdg{padding:.6rem .7rem}
  .wdg .big{font-size:1.5rem}
  .wdg .det{font-size:.82rem}
  .wdg .go .btn,.wdg .go button,.wdg .go a.btn-plain{width:100%;justify-content:center;padding:0 .4rem;
    font-size:.86rem}
  .wdg.wide-sm{grid-column:1 / -1}
}
@media (min-width:600px){.wdg .go .btn,.wdg .go button{width:auto}}
/* a fold-out under a row or a section (a report's details, a watched card's history) */
.rv-det > summary{cursor:pointer;display:flex;flex-wrap:wrap;align-items:center;gap:.2rem .75rem;
  min-height:44px;font-weight:700;color:var(--link)}

/* the Activity pages (admin Activity, My activity): a search on its own line on narrow screens,
   and each event as a short label then its name as plain text that wraps. Each page sets its own
   .act-tools and .act-table columns. */
.act-tools .search{position:relative;display:block;margin:0}
.act-tools .search svg.i{position:absolute;left:.75rem;top:50%;transform:translateY(-50%);
  color:var(--text-muted)}
.act-tools .search input{padding-left:2.3rem;width:100%}
.act-tools .f{min-width:0}
.act-table .c-ev,.act-table .c-det{min-width:0}
.act-table .none,.act-table .gone{color:var(--text-muted)}
.act-table .gone{font-style:italic}
.act-table .evt{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem .45rem;min-width:0}
.act-table .evt .ui-lbl{height:22px;font-size:.78rem;flex:none}
.act-table .evt .en{display:inline-flex;align-items:flex-start;gap:.35rem;font-weight:700;font-size:.92rem;
  line-height:1.3;overflow-wrap:anywhere;min-width:0}
.act-table .evt .en svg.i{width:16px;height:16px;margin-top:.1rem;color:var(--text-muted)}
"""
CSS += UI_CSS


# The card menu and the Watch button (static/cardmenu.js): Card search's results, the deck page,
# the deck editor's add box and the top bar's suggestions. The deck page adds its own items'
# styles in deck.css. Kept before the first 900 px rule (test_social_layout).
CARD_MENU_CSS = """
/* the card menu: the shared .menu panel, fixed where the pointer or finger was; a bottom sheet
   over a dimmed page on a phone (.cs-menu) */
.ctxmenu{position:fixed;z-index:58;min-width:220px;max-width:min(20rem,calc(100vw - 1rem));overflow:auto;
  overscroll-behavior:contain;border:1px solid var(--border)}
.ctxmenu .head{text-transform:none;letter-spacing:0;font-size:.9rem;color:var(--text);white-space:normal;
  overflow-wrap:anywhere;padding-bottom:.35rem;border-bottom:1px solid var(--border);margin-bottom:.25rem}
.ctxmenu .ic,.ctxmenu .i{width:18px;height:18px;flex:none;color:var(--orange);fill:none;stroke:currentColor;
  stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.ctxmenu a,.ctxmenu button,.ctxmenu .item{white-space:normal;min-height:38px}
/* The menu scrolls as a whole when the window is short; its items never shrink to make room (a
   "Move to" list opened near the bottom of the window was squeezed to 2 px and could not be used) */
.ctxmenu > *{flex-shrink:0}
.ctxmenu.cs-menu{display:flex;flex-direction:column;max-height:calc(100vh - 16px)}
.cs-scrim{display:none}
@media (max-width:599.98px){
  .cs-scrim{display:block;position:fixed;inset:0;z-index:57;background:var(--scrim)}
  .ctxmenu.cs-menu{left:0!important;right:0;top:auto!important;bottom:0;width:auto;min-width:0;
    max-width:none;max-height:85vh;border-radius:var(--radius-sheet) var(--radius-sheet) 0 0;
    padding-bottom:calc(.75rem + env(safe-area-inset-bottom))}
}
/* Watch (K7): a pill, a check and "Watching" when the card is watched; .icon is the icon alone */
.wbtn{display:inline-flex;align-items:center;gap:.3rem;
  margin:0;height:32px;min-height:32px;padding:0 .55rem;border-radius:999px;border:1px solid
  var(--control-border);background:var(--surface);color:var(--text);font-size:.8rem;font-weight:700;
  line-height:1;box-shadow:var(--shadow);cursor:pointer}
.wbtn svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:2.2;flex:none}
.wbtn:hover{border-color:var(--text-muted)}
.wbtn.on{color:var(--green-text);border-color:var(--green-text);
  box-shadow:inset 0 0 0 99px var(--green-tint),var(--shadow)}
.wbtn[aria-busy=true]{opacity:.6}
.wbtn.icon span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.wbtn.icon{padding:0;justify-content:center;width:32px}
/* a press and hold on a card row opens its menu: no text selection or image callout under it */
.suggest-list li[role=option][data-name]{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
/* the deck editor's suggestions: the Watch button at the row's end */
.suggest-list li.watchable{position:relative;padding-right:3.25rem}
.suggest-list li.watchable > .wbtn{position:absolute;right:.5rem;top:50%;transform:translateY(-50%);
  box-shadow:none}
.suggest-list li.watchable > .wbtn.on{box-shadow:inset 0 0 0 99px var(--green-tint)}
@media (pointer:coarse){
  .wbtn{height:44px;min-height:44px;min-width:44px}
  .wbtn.icon{width:44px}
  .suggest-list li.watchable{padding-right:4rem}
  .ctxmenu.cs-menu a,.ctxmenu.cs-menu button{min-height:44px}
}
/* the toast after a watch (and after an add on Card search) */
.toast.cards-toast{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem .75rem;z-index:95}
.toast.cards-toast .msg{flex:1 1 12rem;min-width:0;overflow-wrap:anywhere}
.toast.cards-toast .btn,.toast.cards-toast button{margin:0}
.toast.cards-toast .close{width:2rem;min-height:2rem;height:2rem;padding:0;border-radius:50%;font-size:1.2rem}
.toast.cards-toast .ok{width:18px;height:18px;flex:none;fill:none;stroke:var(--green-text);stroke-width:2.4}
"""
CSS += CARD_MENU_CSS


# The top bar's search box (cards and decks, static/suggest.js in its site mode): in the bar from
# 900 px and on the touch rail (600 to 899 px); under 600 px folded behind the magnifier
# (static/sitesearch.js opens it under the bar). A narrow window with a mouse (600 to 899 px)
# keeps its text links, Search among them, and has no room for the box.
_TOPSEARCH_CSS = """
.topbar .topsearch{display:none;margin:0;flex:0 1 20rem;min-width:9rem}
.topbar .topsearch .box{position:relative;display:block}
.topbar .topsearch .box > svg.i{position:absolute;left:.75rem;top:50%;transform:translateY(-50%);z-index:1;
  color:var(--navbar-muted);pointer-events:none}
.topbar .topsearch .suggest > input{height:36px;padding:0 .9rem 0 2.3rem;border-radius:18px;
  background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.2);color:var(--navbar-text)}
.topbar .topsearch .suggest > input::placeholder{color:var(--navbar-muted)}
.topbar .topsearch .suggest > input:focus{background:var(--surface);color:var(--text);
  border-color:var(--orange);outline-offset:1px}
.topbar .topsearch .suggest > input:focus::placeholder{color:var(--text-muted)}
.topbar .topsearch .box:focus-within > svg.i{color:var(--orange)}
/* the list keeps the page's text colour and weight, not the bar's white bold */
.topbar .topsearch .suggest-list{left:auto;right:0;width:min(27rem,calc(100vw - 2rem));
  max-height:min(26rem,70vh);color:var(--text);font-weight:400;text-align:left}
@media (min-width:600px) and (max-width:899.98px) and ((pointer:coarse) or (hover:none)){
  .topbar .topsearch{display:block} }
@media (min-width:600px){ body.app .topbar .topsearch{display:block} }
@media (min-width:900px){ .topbar .topsearch{display:block} .topbar .searchbtn{display:none} }
@media (max-width:599.98px){
  body.search-open .topbar .topsearch{display:block;position:absolute;left:0;right:0;top:100%;
    padding:.5rem max(1rem,env(safe-area-inset-right)) .6rem max(1rem,env(safe-area-inset-left));
    background:var(--navbar-bg);box-shadow:var(--shadow);flex:none;min-width:0}
  body.search-open .topbar .topsearch .suggest-list{left:0;right:0;width:auto}
  body.search-open .topbar .searchbtn{color:var(--orange)} }
/* the site search's groups in the suggestion list */
.suggest-list li.head{padding:.5rem 1rem .2rem;font-size:.78rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.04em;color:var(--menu-head);cursor:default}
.suggest-list li.head:hover{background:none}
.suggest-list li.head:not(:first-child){border-top:1px solid var(--border-soft);margin-top:.25rem;
  padding-top:.6rem}
.suggest-list li.go{display:flex;align-items:center;gap:.6rem}
.suggest-list li.go svg.i{color:var(--orange);flex:none}
"""
CSS += _TOPSEARCH_CSS

# The navigation bar (static/feedback.js): a thin line along the top of the window from the tap on
# a link to another page until that page replaces this one, so a slow answer never looks like a
# missed tap. It creeps towards the end and never finishes on its own.
CSS += """
.navprogress{position:fixed;top:0;left:0;right:0;height:3px;z-index:1000;pointer-events:none;
  background:var(--orange);transform-origin:0 50%;transform:scaleX(0);opacity:0}
html.navigating .navprogress{opacity:1;animation:navprogress 8s cubic-bezier(.1,.7,.3,1) forwards}
@keyframes navprogress{from{transform:scaleX(.08)}to{transform:scaleX(.92)}}
@media (prefers-reduced-motion:reduce){html.navigating .navprogress{animation:none;transform:scaleX(.6)}}
"""


# Own inline icons (simple 24-unit strokes). None is an Archidekt or Font Awesome path.
ICONS = {
    "decks": "<path d='M4 7h16v13H4z'/><path d='M7 4h13v13'/>",
    "scan": "<path d='M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3"
    "M8 20H5a1 1 0 0 1-1-1v-3'/><path d='M4 12h16'/>",
    "proposals": "<path d='M9 11l3 3 8-8'/>"
    "<path d='M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9'/>",
    "history": "<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>",
    "more": "<circle cx='12' cy='5' r='1.5' fill='currentColor'/><circle cx='12' cy='12' r='1.5' "
    "fill='currentColor'/><circle cx='12' cy='19' r='1.5' fill='currentColor'/>",
    "account": "<circle cx='12' cy='8' r='4'/><path d='M4 21a8 8 0 0 1 16 0'/>",
    "moon": "<path d='M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4"
    "M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'/>",
    "search": "<circle cx='11' cy='11' r='7'/><path d='M20 20l-4-4'/>",
    "edit": "<path d='M4 20h4l11-11-4-4L4 16z'/><path d='M13 7l4 4'/>",
    "clone": "<path d='M9 9h11v11H9z'/><path d='M4 15V4h11'/>",
    "eye": "<path d='M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z'/><circle cx='12' cy='12' r='3'/>",
    "eye-off": "<path d='M3 3l18 18'/><path d='M10.6 6.2A10 10 0 0 1 12 6c6.5 0 10 6 10 6a16 16 0 0 1-3.1 3.7"
    "M6.6 6.6A16 16 0 0 0 2 12s3.5 6 10 6a9.7 9.7 0 0 0 4-.8'/>",
    "layers": "<path d='M12 3l9 5-9 5-9-5z'/><path d='M3 13l9 5 9-5'/>",
    "grid": "<path d='M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z'/>",
    "list": "<path d='M8 6h12M8 12h12M8 18h12'/><circle cx='4' cy='6' r='1' fill='currentColor'/>"
    "<circle cx='4' cy='12' r='1' fill='currentColor'/><circle cx='4' cy='18' r='1' fill='currentColor'/>",
    "sort": "<path d='M4 6h10M4 12h7M4 18h4'/><path d='M17 8v10M14 15l3 3 3-3'/>",
    "tag": "<path d='M3 12V4h8l9 9-8 8z'/><circle cx='7' cy='8' r='1.2' fill='currentColor'/>",
    "trash": "<path d='M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13'/><path d='M10 11v6M14 11v6'/>",
    "folder": "<path d='M3 6h6l2 2h10v11H3z'/>",
    "lock": "<rect x='5' y='11' width='14' height='10' rx='2'/><path d='M8 11V8a4 4 0 0 1 8 0v3'/>",
    "image": "<path d='M4 5h16v14H4z'/><circle cx='9' cy='10' r='1.5'/><path d='M4 17l5-5 4 4 3-3 4 4'/>",
    "box": "<path d='M3 8l9-5 9 5-9 5z'/><path d='M3 8v8l9 5 9-5V8'/><path d='M12 13v8'/>",
    "play": "<circle cx='12' cy='12' r='9'/><path d='M10 8l6 4-6 4z' fill='currentColor'/>",
    "plus": "<path d='M12 5v14M5 12h14'/>",
    "minus": "<path d='M5 12h14'/>",
    "check": "<path d='M5 12l4 4L19 6'/>",
    "x": "<path d='M6 6l12 12M18 6L6 18'/>",
    "undo": "<path d='M9 14L4 9l5-5'/><path d='M4 9h10a6 6 0 0 1 0 12h-3'/>",
    "redo": "<path d='M15 14l5-5-5-5'/><path d='M20 9H10a6 6 0 0 0 0 12h3'/>",
    "settings": "<circle cx='12' cy='12' r='3'/><path d='M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1"
    "M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1'/>",
    "download": "<path d='M12 4v11M7 10l5 5 5-5'/><path d='M4 20h16'/>",
    "external": "<path d='M14 4h6v6M20 4l-9 9'/>"
    "<path d='M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'/>",
    "menu": "<path d='M4 7h16M4 12h16M4 17h16'/>",
    "filter": "<path d='M3 5h18l-7 8v6l-4-2v-4z'/>",
    "stats": "<path d='M4 20V10M10 20V4M16 20v-7M22 20H2'/>",
    "report": "<path d='M6 3h9l5 5v13H6z'/><path d='M14 3v6h6M9 14h6M9 17h6'/>",
    "camera": "<path d='M4 8h4l2-3h4l2 3h4v11H4z'/><circle cx='12' cy='13' r='3'/>",
    "copy": "<path d='M9 9h11v11H9z'/><path d='M4 15V4h11'/>",
    "user": "<circle cx='12' cy='8' r='4'/><path d='M4 21a8 8 0 0 1 16 0'/>",
    "heart": "<path d='M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z'/>",
    "bookmark": "<path d='M6 3h12v18l-6-4-6 4z'/>",
    "comment": "<path d='M4 5h16v11H9l-5 4z'/>",
    "follow": "<circle cx='10' cy='8' r='4'/><path d='M2 21a8 8 0 0 1 16 0'/><path d='M19 8v6M16 11h6'/>",
    "thumb": "<path d='M7 11v9H3v-9zM7 11l4-8a2 2 0 0 1 2 2v4h5a2 2 0 0 1 2 2l-1.5 7a2 2 0 0 1-2 2H7'/>",
    "chev": "<path d='M6 9l6 6 6-6'/>",
    "swap": "<path d='M4 7h13l-3-3M20 17H7l3 3'/>",
    "collection": "<path d='M4 6h12v14H4z'/><path d='M8 6V3h12v14h-3'/><path d='M7 11h6M7 15h6'/>",
    "home": "<path d='M3 11l9-7 9 7'/><path d='M5 10v10h14V10'/><path d='M10 20v-6h4v6'/>",
    "guide": "<path d='M4 5a2 2 0 0 1 2-2h5v17H6a2 2 0 0 0-2 2z'/>"
    "<path d='M20 5a2 2 0 0 0-2-2h-5v17h5a2 2 0 0 1 2 2z'/>",
    "refresh": "<path d='M20 12a8 8 0 1 1-2.3-5.7'/><path d='M20 4v5h-5'/>",
    "link": "<path d='M10 14a4 4 0 0 1 0-5.7l3-3a4 4 0 0 1 5.7 5.7l-1.5 1.5'/>"
    "<path d='M14 10a4 4 0 0 1 0 5.7l-3 3a4 4 0 0 1-5.7-5.7L6.8 11.5'/>",
    # the site icon's art (static/gateway-icon.svg) for the top bar's brand mark
    "brand": "<g transform='rotate(-8 12 12)'><rect x='7' y='5.5' width='10' height='13.5' rx='1.2' "
    "fill='#232323' stroke='#fa890d' stroke-width='1.1'/><path d='M12 8.6l2.4 3.4-2.4 3.4-2.4-3.4z' "
    "fill='#fa890d' stroke='none'/><path d='M8.9 16.3h6.2' stroke='#e3e3e3' stroke-width='1'/></g>",
    "activity": "<path d='M3 12h4l3-7 4 14 3-7h4'/>",
    "login": "<path d='M10 17l5-5-5-5'/><path d='M15 12H3'/>"
    "<path d='M12 4h7a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-7'/>",
    "drag": "<circle cx='9' cy='6' r='1.3' fill='currentColor'/>"
    "<circle cx='15' cy='6' r='1.3' fill='currentColor'/><circle cx='9' cy='12' r='1.3' fill='currentColor'/>"
    "<circle cx='15' cy='12' r='1.3' fill='currentColor'/>"
    "<circle cx='9' cy='18' r='1.3' fill='currentColor'/>"
    "<circle cx='15' cy='18' r='1.3' fill='currentColor'/>",
    # UI standard §4 labels, widgets and actions (ui.py)
    "clock": "<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>",
    "alert": "<circle cx='12' cy='12' r='9'/><path d='M12 7v6M12 16.5v.5'/>",
    "pause": "<path d='M9 6v12M15 6v12'/>",
    "hourglass": "<path d='M7 3h10M7 21h10M8 3c0 5 8 6 8 9s-8 4-8 9M16 3c0 5-8 6-8 9'/>",
    "shield": "<path d='M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z'/>",
    "triangle": "<path d='M12 4l9 16H3z'/><path d='M12 10v4M12 17v.5'/>",
    "hand": "<path d='M8 13V6a1.5 1.5 0 0 1 3 0v5M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v7"
    "c0 4-2.5 7-6 7-2.5 0-4-1.5-5.5-3.5L4 13.5a1.5 1.5 0 0 1 2.3-1.9L8 13'/>",
    "spinner": "<path d='M12 3a9 9 0 1 0 9 9'/>",
    "arrows": "<path d='M4 8h13l-3-3M20 16H7l3 3'/>",
    "back": "<path d='M9 14L4 9l5-5'/><path d='M4 9h10a6 6 0 0 1 0 12h-3'/>",
    "bell": "<path d='M6 16V11a6 6 0 0 1 12 0v5l2 2H4z'/><path d='M10 21h4'/>",
    "send": "<path d='M4 12l16-8-6 16-2-7z'/>",
}


def esc(value: Any) -> str:
    """HTML-escape a value for page text or a quoted attribute; None is empty."""
    return html.escape("" if value is None else str(value), quote=True)


def display_name(user: dict | None) -> str:
    """The name the account menu shows for a member: their username, else their name or email."""
    u = user or {}
    return str(u.get("preferred_username") or u.get("name") or u.get("email") or "")


def time_text(ts: int | float | None) -> str:
    """A timestamp as plain text in UTC (``2027-01-15 08:00 UTC``), for file names, Markdown and
    anything that is escaped after; pages use time_html."""
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def time_html(ts: int | float | None, *, day: bool = True) -> str:
    """A timestamp as ``<time datetime>``: the page shows it in UTC, and static/feedback.js
    rewrites it in the viewer's own time zone and locale (``day=False``: the time only)."""
    if not ts:
        return ""
    try:
        t = time.gmtime(int(ts))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", t)
    shown = time.strftime("%Y-%m-%d %H:%M UTC" if day else "%H:%M UTC", t)
    return f"<time datetime='{iso}'{'' if day else ' data-fmt=time'}>{shown}</time>"


def plural(n: int | float, word: str, plural_word: str | None = None) -> str:
    """'1 card', '2 cards': a real plural instead of 'card(s)'. ``plural_word`` for irregular
    nouns (``plural(2, "copy", "copies")``)."""
    try:
        one = float(n) == 1
    except (TypeError, ValueError):
        one = False
    count = f"{int(n):,}" if isinstance(n, int) or float(n).is_integer() else f"{n:g}"
    return f"{count} {word if one else (plural_word or word + 's')}"


def icon(name: str, *, cls: str = "i") -> str:
    """One of our own inline SVG icons (``aria-hidden``; pair it with visible or sr-only text)."""
    return f"<svg class='{cls}' viewBox='0 0 24 24' aria-hidden='true'>{ICONS[name]}</svg>"


def current_theme() -> str:
    return _theme.get()


def current_path() -> str:
    """Path of the request being rendered (for the theme form's return address; no query string)."""
    return _path.get()


def in_app() -> bool:
    """True when the page is rendered for the Android app (see :data:`APP_UA_MARK`)."""
    return _app.get()


def app_version() -> str:
    """The Android app's version from its user agent (MAJOR.MINOR.PATCH), or "" if not shown."""
    return _app_version.get()


def theme_from_cookie(value: str | None) -> str:
    return value if value in THEMES else "system"


def layout_from_cookie(value: str | None) -> str:
    return value if value in LAYOUTS else "auto"


# "View as" on the deck page, the deck list, card search and the collection follows the account
# from 0.7.29 (prefs.py). Before that it was a cookie per browser with these names; prefs.py
# copies a cookie's value to the account once and removes the cookie.
VIEW_COOKIE = "mtg_view_"
VIEW_KINDS = ("deck", "decks", "collection", "cards")


def current_layout() -> str:
    return _layout.get()


def set_card_size(size: object) -> None:
    """The card size this request's page is drawn with (the account's, prefs.py)."""
    _card_size.set(size if isinstance(size, str) and size in CARD_SIZES else "m")


def current_card_size() -> str:
    return _card_size.get()


def card_size_control() -> str:
    """The S / M / L switch next to "View as" (static/prefs.js presses it and saves the choice)."""
    size = current_card_size()
    buttons = "".join(
        f"<button type='button' data-card-size-set='{k}' aria-pressed='{'true' if k == size else 'false'}' "
        f"aria-label='{label} cards' title='{label} cards'>{k.upper()}</button>"
        for k, label in (("s", "Small"), ("m", "Medium"), ("l", "Large"))
    )
    return (
        "<div class='field cardsize'><span class='lbl' aria-hidden='true'>Card size</span>"
        f"<span class='sizeseg' role='group' aria-label='Card size'>{buttons}</span></div>"
    )


# The site navigation, in Archidekt's order of sections as far as the gateway has them: Home, then
# decks, deck search, cards you own, scanning, then the gateway's own review pages. The same list
# feeds the top bar on wide screens and the bottom tab bar on phones.
NAV_LINKS = [
    ("/", "Home", "home"),
    ("/decks", "Decks", "decks"),
    ("/search", "Search", "search"),
    ("/collection", "Collection", "collection"),
    ("/scan", "Scan", "scan"),
    ("/proposals", "Proposals", "proposals"),
    ("/history", "History", "history"),
    ("/reports", "Reports", "report"),
    ("/watchlist", "Watchlist", "stats"),
]
# On a computer the links from Proposals on fold into "More ▾" while the bar is narrower than
# 85.7em (1200 px at the normal text size): the More menu lists the phone's More sheet.
NAV_FOLD_FROM = "/proposals"
# The tabs, as Material's navigation bar (3 to 5 destinations) has them: on a compact screen Home,
# Decks, Scan, Collection and More, with Search as the icon in the top bar. More opens a sheet with
# the remaining pages (Proposals, History, Reports, Watchlist, Guide, Account, Admin) so nothing
# is more than two taps away; Home is never under More. The rail (an unfolded Fold, a tablet, the app from
# 600px) is tall enough for every page, so it lists them all as tabs and has no More.
TAB_LINKS = [
    ("/", "Home", "home", ""),
    ("/decks", "Decks", "decks", ""),
    ("/search", "Search", "search", "railonly"),
    ("/scan", "Scan", "scan", ""),
    ("/collection", "Collection", "collection", ""),
]
MORE_LINKS = [
    ("/proposals", "Proposals", "proposals"),
    ("/history", "History", "history"),
    ("/reports", "Reports", "report"),
    ("/watchlist", "Watchlist", "stats"),
    ("/guide", "Guide", "guide"),
    ("/account", "Account", "account"),
]


# The site's public origin (MTG_PUBLIC_URL), set by create_app: link previews need an absolute image.
PUBLIC_URL = ""
# Whether watchlists are on (MTG_WATCHLISTS_ENABLED), set by create_app: off, no page links to
# /watchlist and the card viewer offers no price history (static/cardview.js reads data-watchlist).
WATCHLISTS = False

STATIC_DIR = Path(__file__).parent / "static"


def _deck_css() -> str:
    from .deckpage import DECK_CSS  # deckpage imports this module

    return DECK_CSS


# Stylesheets the pages share, served from memory at /static/<name> (companion_shell.py's static route)
# instead of inline in every page: the browser keeps them, so a page brings only its own markup.
STYLESHEETS: dict[str, Callable[[], str]] = {"site.css": lambda: CSS, "deck.css": _deck_css}


def stylesheet_link(name: str) -> str:
    return f"<link rel='stylesheet' href='/static/{name}'>"


@functools.cache
def static_version(name: str) -> str | None:
    """A short digest of a /static file (or shared stylesheet), None for one that doesn't exist.
    The files only change with a new image, so each is read once."""
    if name in STYLESHEETS:
        data = STYLESHEETS[name]().encode()
    else:
        try:
            data = (STATIC_DIR / name).read_bytes()
        except OSError:
            return None
    return hashlib.sha256(data).hexdigest()[:12]


_STATIC_REF = re.compile(r"((?:src|href)=')/static/([A-Za-z0-9_.-]{1,80})'")


def versioned_static(doc: str) -> str:
    """Every ``src='/static/x'`` and ``href='/static/x'`` in a page gets ``?v=<digest>``: the
    static route lets the browser keep a file asked for by its current digest for a year without
    asking again, and a new release changes the address of every file it changes."""

    def add(m: re.Match[str]) -> str:
        v = static_version(m.group(2))
        return f"{m.group(1)}/static/{m.group(2)}?v={v}'" if v else m.group(0)

    return _STATIC_REF.sub(add, doc)


def render(
    title: str,
    body: str,
    *,
    site: str,
    status: int = 200,
    signed_in: bool = False,
    csrf: str | None = None,
    form_action: tuple[str, ...] = (),
    admin: bool = False,
    wide: bool = False,
    current: str | None = None,
    heading: bool = True,
    head_extra: str = "",
    body_class: str = "",
    main_class: str = "",
    user: str | None = None,
) -> HTMLResponse:
    """Render a page. ``form_action`` adds exact origins a form on this page may submit or be
    redirected to (browsers apply form-action to the redirect that follows a POST too).
    Scripts load only from the gateway's own origin (``script-src 'self'``; inline script stays
    forbidden). ``current`` names the nav link to mark as the current page (its path);
    ``heading=False`` leaves the <h1> to the body (deck banner). ``user`` is the signed-in
    member's name for the account menu's "Signed in as" line. ``main_class`` adds classes to
    <main> (``ui-wide`` for the UI standard's 1200 px list pages)."""
    theme = current_theme()
    app = in_app()
    # "Use desktop layout": a wide fixed viewport, as a browser's "Desktop site" switch
    # would give, so the width-based media queries pick the computer layout and the device zooms out;
    # the app's forced rail is dropped too.
    desktop = current_layout() == "desktop"
    nav = ""
    tabbar = ""
    cur = current or ""
    if signed_in:
        csrf_in = f"<input type='hidden' name='csrf' value='{html.escape(csrf or '')}'>"
        links = [x for x in NAV_LINKS if WATCHLISTS or x[0] != "/watchlist"]
        if admin:
            links.append(("/admin", "Admin", "settings"))
        fold_at = [x[0] for x in links].index(NAV_FOLD_FROM)

        def a(href: str, label: str, keep: bool = False, fold: bool = False) -> str:
            aria = " aria-current='page'" if cur == href else ""
            cls = "keep" if keep else "fold" if fold else ""
            return f"<a href='{href}'{aria}{f' class={cls}' if cls else ''}>{label}</a>"

        theme_items = "".join(
            f"<button name='theme' value='{t}'{' class=on' if theme == t else ''} "
            f"aria-pressed='{'true' if theme == t else 'false'}'>"
            f"{icon('check') if theme == t else '<span class=i></span>'}{label} theme</button>"
            for t, label in (("light", "Light"), ("dark", "Dark"), ("system", "System"))
        )
        who = (
            f"<div class='head who' title='{html.escape(user)}'>Signed in as <b>{html.escape(user)}</b></div>"
            if user
            else ""
        )
        account_menu = (
            "<details class='dd'><summary class='icon-btn' aria-label='Account menu' title='Account menu'>"
            # The member's own picture (their provider's, else their initials: /account/avatar).
            "<img class='av' src='/account/avatar' alt='' width='28' height='28'></summary><div class='menu'>"
            f"{who}"
            f"<a href='/'>{icon('home')}Home</a>"
            f"<a href='/account'>{icon('account')}Account</a>"
            f"<a href='/proposals'>{icon('proposals')}Proposals</a>"
            f"<a href='/history'>{icon('history')}History</a>"
            # Reports: here, in the top bar from 1200 px (its More below that), a tab on the rail
            f"<a href='/reports'>{icon('report')}Reports</a>"
            + (f"<a href='/watchlist'>{icon('stats')}Watchlist</a>" if WATCHLISTS else "")
            + f"<a href='/activity'>{icon('activity')}My activity</a>"
            f"<a href='/guide'>{icon('guide')}Guide</a>"
            f"<a href='/install?for=skill'>{icon('report')}Assistant skill</a>"
            + (f"<a href='/install?for=android'>{icon('download')}Android app</a>" if not app else "")
            + (
                # The Android app's own actions (its floating button offers them over other pages);
                # feedback.js calls window.MtgNative for each data-native button. Open a copied link
                # and Check for app updates live in the app's own menu and the Account page's App card.
                "<div class='sep'></div><div class='head'>App</div>"
                f"<button type='button' data-native='openCamera'>{icon('camera')}Scan with the app's camera"
                "</button>"
                f"<button type='button' data-native='reload'>{icon('refresh')}Reload</button>"
                f"<button type='button' data-native='openInBrowser'>{icon('link')}Open in browser</button>"
                f"<button type='button' data-native='changeGateway'>{icon('settings')}Change gateway</button>"
                if app
                else ""
            )
            + "<div class='sep'></div><div class='head'>Site theme</div>"
            f"<form method='post' action='/theme'>{csrf_in}"
            f"<input type='hidden' name='next' value='{html.escape(current_path())}'>"
            f"{theme_items}</form>"
            "<div class='sep'></div><div class='head'>Site layout</div>"
            f"<form method='post' action='/layout'>{csrf_in}"
            f"<input type='hidden' name='next' value='{html.escape(current_path())}'>"
            f"<button name='layout' value='auto'{' class=on' if not desktop else ''} "
            f"aria-pressed='{'false' if desktop else 'true'}'>"
            f"{icon('check') if not desktop else '<span class=i></span>'}Fit the screen</button>"
            f"<button name='layout' value='desktop'{' class=on' if desktop else ''} "
            f"aria-pressed='{'true' if desktop else 'false'}'>"
            f"{icon('check') if desktop else '<span class=i></span>'}Desktop layout</button></form>"
            "<div class='sep'></div>"
            f"<form method='post' action='/logout'>{csrf_in}"
            f"<button data-busy-text='Signing out…'>{icon('x')}Sign out</button></form>"
            "</div></details>"
        )
        more_links = [m for m in MORE_LINKS if WATCHLISTS or m[0] != "/watchlist"] + (
            [("/admin", "Admin", "settings")] if admin else []
        )
        more_open = cur in {href for href, _l, _i in more_links}
        nav = (
            "<nav class='site' aria-label='Site'>"
            + "".join(a(href, label, fold=i >= fold_at) for i, (href, label, _ic) in enumerate(links))
            # the folded links (and Guide and Account) under More ▾ on a narrower computer screen
            + "<details class='dd navmore'><summary"
            + (" class='on'" if more_open else "")
            + ">More<span aria-hidden='true'>▾</span></summary><div class='menu'>"
            + "".join(
                f"<a href='{href}'{' aria-current=page' if cur == href else ''}>{icon(ic)}{label}</a>"
                for href, label, ic in more_links
            )
            + "</div></details></nav>"
        )
        search_btn = (
            f"<a class='icon-btn searchbtn' href='/search' aria-label='Search cards and decks'"
            " title='Search cards and decks'"
            f"{' aria-current=page' if cur == '/search' else ''}>{icon('search')}</a>"
        )
        # The site search (cards and decks; cardsearch.py, static/suggest.js): Enter on plain text
        # is the deck search, a card row opens /cards for it, and a right-click or a press and hold
        # on one opens the card menu (static/cardmenu.js). Folded behind the magnifier on phones.
        search_form = (
            "<form class='topsearch' id='topsearch' role='search' method='get' action='/search'>"
            "<label for='site-q' class='sr-only'>Search cards and decks</label>"
            f"<span class='box'>{icon('search')}<input id='site-q' type='search' name='q' maxlength='100' "
            "placeholder='Search cards and decks' data-suggest='cards' data-suggest-site='/cards?q=' "
            "data-suggest-menu autocomplete='off' enterkeyhint='search'></span></form>"
        )
        right = f"{search_form}<nav class='user' aria-label='Account'>{search_btn}{account_menu}</nav>"
        # The More pages are tabs on the rail too (railonly: hidden on the phone bar, which has More)
        rail_more = [(href, label, ic, "railonly") for href, label, ic in more_links]
        tabbar = (
            "<nav class='tabbar' aria-label='Sections'>"
            + "".join(
                f"<a href='{href}' class='{cls}'{' aria-current=page' if cur == href else ''}>"
                f"{icon(ic)}{label}</a>"
                for href, label, ic, cls in TAB_LINKS + rail_more
            )
            + "<details class='dd more'><summary"
            + (" class='on'" if more_open else "")
            + f" aria-label='More sections'>{icon('more')}More</summary><div class='menu sheet'>"
            + "".join(
                f"<a href='{href}'{' aria-current=page' if cur == href else ''}>{icon(ic)}{label}</a>"
                for href, label, ic in more_links
            )
            + "</div></details></nav>"
        )
    else:
        # Signed out: the way in sits where the account menu would be.
        right = (
            "<nav class='user' aria-label='Account'>"
            f"<a class='btn signin' href='/login'>{icon('login')} Sign in</a></nav>"
        )
    h1 = f"<h1>{html.escape(title)}</h1>" if heading else ""
    # Admins hear about a newer gateway release on every page (update_check.py); the admin overview
    # carries the steps, so it shows its own card instead of this line.
    found = update_check.newer() if signed_in and admin else None
    if found and "id='updates'" not in body:
        h1 = (
            f"<div class='notice warn' role='status'>Gateway {html.escape(found['version'])} is available "
            f"(this one runs {html.escape(__version__)}). <a href='/admin#updates'>How to update</a></div>"
        ) + h1
    classes = " ".join(
        c
        for c in (
            "wide" if wide else "",
            "has-tabbar" if tabbar else "",
            "app" if app and not desktop else "",
            "desktop" if desktop else "",
            body_class,
        )
        if c
    )
    viewport = "width=1100" if desktop else "width=device-width, initial-scale=1, viewport-fit=cover"
    # a page with its own web app (the scanner) names its own manifest; a second one would win
    manifest = "" if "rel='manifest'" in head_extra else "<link rel='manifest' href='/app.webmanifest'>"
    # The browser's own chrome follows the top bar: the light bar is #313131, the dark one #111111.
    theme_color = (
        "<meta name='theme-color' media='(prefers-color-scheme: light)' content='#313131'>"
        "<meta name='theme-color' media='(prefers-color-scheme: dark)' content='#111111'>"
        if theme == "system"
        else f"<meta name='theme-color' content='{'#313131' if theme == 'light' else '#111111'}'>"
    )
    description = f"{site}: a private gateway to your Magic: The Gathering decks on Archidekt."
    doc = (
        f"<!doctype html><html lang='en'{f' data-theme={theme}' if theme != 'system' else ''}"
        f"{f' data-card-size={current_card_size()}' if signed_in else ''}>"
        "<head><meta charset='utf-8'>"
        f"<meta name='viewport' content='{viewport}'>"
        "<meta name='referrer' content='no-referrer'>"
        f"<meta name='color-scheme' content='{'dark light' if theme == 'system' else theme}'>"
        f"{theme_color}"
        # Nothing here is for search engines (robots.txt says the same); the description and the
        # Open Graph fields give a pasted link a name and an icon, and never a member's data.
        "<meta name='robots' content='noindex, nofollow'>"
        f"<meta name='description' content='{html.escape(description)}'>"
        f"<meta property='og:site_name' content='{html.escape(site)}'>"
        f"<meta property='og:title' content='{html.escape(title)} · {html.escape(site)}'>"
        f"<meta property='og:description' content='{html.escape(description)}'>"
        f"<meta property='og:image' content='{html.escape(PUBLIC_URL)}/static/gateway-icon-512.png'>"
        f"{manifest}"
        "<link rel='icon' href='/favicon.ico' sizes='32x32'>"
        "<link rel='icon' href='/static/gateway-icon.svg' type='image/svg+xml'>"
        "<link rel='apple-touch-icon' href='/apple-touch-icon.png'>"
        f"<title>{html.escape(title)} · {html.escape(site)}</title>"
        f"{stylesheet_link('site.css')}"
        # static/prefs.js: the choices that follow the account (prefs.py), ready before any page
        # script that reads them (deferred scripts run in document order; pages add theirs in
        # head_extra)
        + ("<script src='/static/prefs.js' defer></script>" if signed_in else "")
        # static/topbar.js: the scroll room above a focused element follows the bar's height
        + "<script src='/static/topbar.js' defer></script>"
        # static/cardmenu.js: the card menu and Watch button the pages and the top bar share,
        # before the page scripts that use it
        + ("<script src='/static/cardmenu.js' defer></script>" if signed_in else "")
        + f"{head_extra}</head>"
        f"<body class='{classes}'{' data-watchlist' if WATCHLISTS else ''}>"
        f"<a class='skip' href='#main'>Skip to content</a>{SPRITE}"
        "<header class='topbar'><div class='wrap'><div class='left'>"
        f"<a class='brand' href='/'{' aria-current=page' if cur == '/' else ''} title='{html.escape(site)}'>"
        f"<span class='mark'>{icon('brand')}</span>"
        f"<span class='word'>{html.escape(site)}</span></a>{nav}</div>"
        f"<div class='right'>{right}</div></div></header>"
        f"<main class='{html.escape(' '.join(('wrap', main_class)).strip())}' id='main' tabindex='-1'>"
        f"{h1}{body}</main>"
        + (
            # The website footer; the Android app has no footer (its pages end at the tab bar).
            "<footer class='site'><div class='links'>"
            + (
                "<a href='/decks'>Decks</a><a href='/search'>Search</a>"
                "<a href='/collection'>Collection</a><a href='/scan'>Scan</a>"
                if signed_in
                else ""
            )
            + "<a href='/guide'>Guide</a><a href='/install?for=skill'>Assistant</a>"
            + "<a href='/install?for=android'>Apps</a>"
            + ("<a href='/account'>Account</a>" if signed_in else "<a href='/login'>Sign in</a>")
            + "</div>"
            "<div class='legal'>Private deck gateway. Nothing on these pages is indexed or shared. "
            "Magic: The Gathering is a trademark of Wizards of the Coast. Card data and images come from "
            "Scryfall; decks live on Archidekt.</div></footer>"
            if not app
            else ""
        )
        + f"{tabbar}<script src='{FEEDBACK_SCRIPT}' defer></script>"
        "<script src='/static/mana.js' defer></script><script src='/static/suggest.js' defer></script>"
        "<script src='/static/select.js' defer></script>"
        + ("<script src='/static/sitesearch.js' defer></script>" if signed_in else "")
        + "</body></html>"
    )
    return HTMLResponse(
        versioned_static(doc),
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            # Scripts only from the gateway's own origin, never inline: every page loads
            # static/feedback.js (button feedback, the offline page), and some pages more. No page
            # may be framed (frame-ancestors, with X-Frame-Options for old browsers) or change
            # its base URL (base-uri). form-action stays last: callers append sources to it.
            "Content-Security-Policy": DEFAULT_CSP + "".join(f" {src}" for src in form_action),
        },
    )


class ThemeMiddleware:
    """Read the ``mtg_theme`` cookie into a context variable for :func:`render`."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        value = None
        layout = None
        app = False
        app_ver = ""
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                for part in v.decode("latin-1").split(";"):
                    name, _, val = part.strip().partition("=")
                    if name == THEME_COOKIE:
                        value = val.strip()
                    elif name == LAYOUT_COOKIE:
                        layout = val.strip()
            elif k == b"user-agent" and APP_UA_MARK in v.decode("latin-1", "replace"):
                app = True
                m = _APP_VERSION_RE.search(v.decode("latin-1", "replace"))
                app_ver = m.group(1) if m else ""
        token = _theme.set(theme_from_cookie(value))
        ltoken = _layout.set(layout_from_cookie(layout))
        path = scope.get("path") or "/"
        ptoken = _path.set(path)
        atoken = _app.set(app)
        vtoken = _app_version.set(app_ver)
        ctoken = _card_size.set("m")
        try:
            await self.app(scope, receive, send)
        finally:
            _card_size.reset(ctoken)
            _theme.reset(token)
            _layout.reset(ltoken)
            _path.reset(ptoken)
            _app.reset(atoken)
            _app_version.reset(vtoken)


# One year, the value browsers' preload lists expect. No includeSubDomains: the gateway is usually
# one host under a domain that also serves other things (a home lab, a router, plain-http
# devices), and HSTS on the parent would force https on all of them. Add it at the proxy if every
# subdomain is https. Sent only when MTG_PUBLIC_URL is https.
HSTS_VALUE = "max-age=31536000"


class NoSniffMiddleware:
    """Add ``X-Content-Type-Options: nosniff`` to every HTTP response that lacks it, so no
    browser guesses a script or page out of JSON, text or a download; with ``hsts`` (the public
    URL is https) add ``Strict-Transport-Security`` too, so a browser that has been here once
    never makes a plain-http first request that an attacker on the network could answer."""

    def __init__(self, app: ASGIApp, hsts: bool = False):
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                if b"x-content-type-options" not in names:
                    headers.append((b"x-content-type-options", b"nosniff"))
                if self.hsts and b"strict-transport-security" not in names:
                    headers.append((b"strict-transport-security", HSTS_VALUE.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
