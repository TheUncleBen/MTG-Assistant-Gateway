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

import html
from contextvars import ContextVar

from starlette.responses import HTMLResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

THEME_COOKIE = "mtg_theme"
FEEDBACK_SCRIPT = "/static/feedback.js"
# The Content-Security-Policy of every page render() builds (some pages replace it with their own,
# which keeps the same frame-ancestors and base-uri). form-action is last so sources can follow it.
# img-src 'self' is for the gateway's own files only (the select arrow, /static/chevron.svg); pages
# that show card images add cards.scryfall.io in their own policy.
DEFAULT_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; worker-src 'self'; img-src 'self'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
THEMES = ("system", "light", "dark")
_theme: ContextVar[str] = ContextVar("mtg_theme", default="system")
_path: ContextVar[str] = ContextVar("mtg_path", default="/")
# True while rendering for the Android app's WebView (its user agent carries "MTGAssistant/"):
# pages then use the phone layout at every width and drop the website footer.
_app: ContextVar[bool] = ContextVar("mtg_app", default=False)
APP_UA_MARK = "MTGAssistant/"

# Archidekt's light set (body custom properties). Emitted twice: under prefers-color-scheme for
# "system" and under [data-theme=light] for an explicit choice.
_LIGHT = """
    --bg:#f9fafb; --surface:#fafafa; --surface-2:#dedede; --surface-3:#c1c1c1;
    --border:#bababa; --border-soft:#d4d4d4; --card-border:#5b5b5b;
    --text:#383838; --text-muted:#727272; --link:#2a66c9; /* Archidekt's #4183c4 is 3.8:1 */
    --toolbar-bg:#dcdcdc; --toolbar-text:#383838;
    --navbar-bg:#313131; --navbar-text:#ffffff; --navbar-muted:#d6d6d6;
    --banner-a:rgba(40,40,40,.82); --banner-b:rgba(40,40,40,.5);
    --orange-text:#a65400; --green-text:#117a45; --red-text:#b3262e; --blue-text:#2a66c9;
    --orange-tint:rgba(250,137,13,.16); --green-tint:rgba(30,187,108,.16);
    --red-tint:rgba(255,85,91,.16); --blue-tint:rgba(66,134,244,.14);
    --shadow:0 3px 6px rgba(0,0,0,.25); --scrim:rgba(0,0,0,.4);
"""

CSS = (
    """
:root{
  /* Archidekt dark-mode set (body.dark-mode) */
  --bg:#181818; --surface:#232323; --surface-2:#383838; --surface-3:#4b4b4b;
  --border:#5f5f5f; --border-soft:#323232; --card-border:#525252;
  --text:#e3e3e3; --text-muted:#a8a8a8; --link:#73a8dc;
  --toolbar-bg:#2e2d2d; --toolbar-text:#f5f5f5;
  --navbar-bg:#111111; --navbar-text:#ffffff; --navbar-muted:#e3e3e3;
  --banner-a:rgba(14,14,14,.82); --banner-b:rgba(14,14,14,.5);
  --orange:#fa890d; --green:#1ebb6c; --red:#ff555b; --blue:#4286f4; --purple:#6435c9; --pink:#e03997;
  --orange-text:#fa890d; --green-text:#1ebb6c; --red-text:#ff555b; --blue-text:#7fb0ff;
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
@media (prefers-color-scheme: light){ :root:not([data-theme=dark]){"""
    + _LIGHT
    + """} }
:root[data-theme=light]{"""
    + _LIGHT
    + """}
*{box-sizing:border-box}
html{font-size:14px;scroll-padding:60px 0 120px;-webkit-text-size-adjust:100%;min-width:320px;
  font-family:Lato,"Helvetica Neue",Arial,Helvetica,sans-serif;line-height:1.15}
body{margin:0;min-height:100vh;display:flex;flex-direction:column;background:var(--bg);color:var(--text);
  font:inherit;font-size:1rem;line-height:1.35;transition:background-color .25s ease-in}
a{color:var(--link)} a:hover{color:var(--orange)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);
  white-space:nowrap;border:0}
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
.brand .mark{width:25px;height:25px;border-radius:5px;background:var(--orange);display:inline-flex;
  align-items:center;justify-content:center;color:#fff;flex:none}
.brand .mark svg{width:17px;height:17px;stroke-width:2.4}
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

/* dropdown menus (details/summary, no script): phatDropdown trigger + menu panel */
details.dd{position:relative;margin:0}
details.dd > summary{list-style:none;cursor:pointer;user-select:none}
details.dd > summary::-webkit-details-marker{display:none}
details.dd .menu{position:absolute;top:calc(100% + .25rem);right:0;z-index:30;min-width:200px;
  background:var(--surface-2);border-radius:var(--radius-panel);box-shadow:var(--shadow);padding:.25rem 0;
  display:flex;flex-direction:column}
details.dd .menu.left{left:0;right:auto}
details.dd .menu a,details.dd .menu button,details.dd .menu .item{display:flex;align-items:center;gap:.6rem;
  min-height:35px;padding:0 1rem;margin:0;width:100%;background:transparent;border:0;border-radius:0;
  color:var(--text);text-decoration:none;font:inherit;font-weight:400;font-size:1rem;cursor:pointer;
  text-align:left;white-space:nowrap;justify-content:flex-start}
details.dd .menu a:hover,details.dd .menu button:hover,details.dd .menu a:focus-visible{
  background:var(--border);color:var(--text)}
details.dd .menu .sep{height:1px;background:var(--border);margin:.25rem 0}
details.dd .menu a.danger{color:var(--danger-fill)}
details.dd .menu .head{padding:.4rem 1rem .2rem;font-size:.8rem;font-weight:700;color:var(--text-muted);
  text-transform:uppercase;letter-spacing:.04em}
details.dd .menu form{margin:0;display:contents}
details.dd .menu .on{color:var(--orange-text);font-weight:700}
/* a dropdown trigger styled like phatDropdown: bordered, 39px, orange chevron, label floating above */
.field{position:relative;display:flex;flex-direction:column;gap:.3rem;min-width:0}
.field > label,.field > .lbl{font-weight:700;margin:0;font-size:1rem}
.dd-trigger,details.dd > summary.dd-trigger{display:flex;align-items:center;justify-content:space-between;
  gap:.5rem;height:var(--ctl);min-width:140px;padding:0 .75rem 0 1rem;border-radius:var(--radius);
  border:1px solid var(--border);background:var(--surface);color:var(--text);font-weight:400;
  transition:background-color .2s ease-in-out}
.dd-trigger:hover,details.dd > summary.dd-trigger:hover{background:var(--surface-2)}
.dd-trigger .chev{color:var(--orange);font-size:.8rem}
.dd-trigger .ic{color:var(--orange);margin-right:.25rem}
details.dd[open] > summary.dd-trigger{border-color:var(--orange)}

.wrap{width:100%;max-width:52rem;margin:0 auto;padding:0 1rem}
main.wrap{flex:1;padding:1rem 1rem 3rem}
h1{font-size:2rem;font-weight:700;line-height:1.2;margin:.5rem 0 1rem;overflow-wrap:anywhere}
h2{font-size:1.5rem;font-weight:700;margin:0 0 .6rem}
h2:not(:first-child){margin-top:1.25rem}
h3{font-size:1.1rem;font-weight:700;margin:.5rem 0 .4rem}
h4{font-size:1rem;font-weight:700;margin:0}
p{margin:.5rem 0}
.muted{color:var(--text-muted)} .small{font-size:.86rem} .b{font-weight:700} .orange{color:var(--orange-text)}
ul.plain{list-style:none;margin:0;padding:0}

/* panels (Archidekt: 3px radius, 1px border, light-background, no shadow) */
.card,.panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-panel);
  padding:1rem;margin:0 0 1rem}
.card > :first-child,.panel > :first-child{margin-top:0}
.card > :last-child,.panel > :last-child{margin-bottom:0}
.panel h2,.card h2{font-size:1.5rem}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
code{font-size:.92em;background:var(--surface-2);padding:.1rem .4rem;border-radius:3px;overflow-wrap:anywhere}
pre{font-size:.86rem;line-height:1.5;background:var(--bg);border:1px solid var(--border-soft);
  border-radius:var(--radius);padding:.75rem .9rem;margin:.5rem 0;overflow:auto;white-space:pre-wrap;
  overflow-wrap:anywhere}

/* forms: phatInput / phatButton */
label{display:block;margin:.9rem 0 .3rem;font-weight:700}
input[type=text],input[type=password],input[type=email],input[type=search],input[type=number],input[type=url],
select{width:100%;height:var(--ctl);padding:0 1rem;font:inherit;border-radius:var(--radius);
  border:1px solid var(--border);background:var(--surface);color:var(--text);
  transition:background-color .2s ease-in-out}
select{appearance:none;-webkit-appearance:none;padding-right:2rem;cursor:pointer;
  background-image:url(/static/chevron.svg);
  background-repeat:no-repeat;background-position:right .75rem center;background-size:10px 6px}
input[type=number]{padding-right:.5rem}
textarea{width:100%;min-height:8rem;padding:.5rem .75rem;font:inherit;font-size:.95rem;line-height:1.45;
  border-radius:var(--radius);border:1px solid var(--border);background:var(--surface);color:var(--text);
  resize:vertical}
input:focus,textarea:focus,select:focus{outline:2px solid var(--focus);outline-offset:1px;
  border-color:var(--orange)}
input::placeholder,textarea::placeholder{color:var(--text-muted);opacity:1}
.check{display:flex;align-items:center;gap:.6rem;margin:.5rem 0;font-weight:400;min-height:2rem;
  cursor:pointer}
.check input{width:1.15rem;height:1.15rem;margin:0;accent-color:var(--orange)}
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
button.success,.btn-success{background:var(--green);border-color:var(--green);color:var(--on-green)}
button.secondary,.btn-ghost{background:transparent;border-color:transparent}
button.secondary:hover,.btn-ghost:hover{background:var(--surface-2)}
.btn-block{width:100%}
.btn-lg,button.btn-lg{height:48px;padding:0 1.4rem;font-size:1.1rem}
button.mini{width:2.25rem;min-width:2.25rem;height:2.25rem;padding:0;font-size:1.1rem;line-height:1}
button.icon-only{width:var(--ctl);padding:0}
form{margin:0}
form > button,form .btn{margin-top:1rem}
button.thumbbtn{all:unset;display:block;cursor:pointer;line-height:0;border-radius:3px}
.exportblock .head{display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin-top:1.25rem}
.exportblock .head h2{margin:0} .exportblock textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:.85rem}
button.thumbbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.choice{display:flex;flex-direction:column;gap:.6rem;margin-top:1rem}
.choice button,.choice .btn{margin-top:0;width:100%}
@media (min-width:600px){ .choice{flex-direction:row;align-items:center;flex-wrap:wrap}
  .choice button,.choice .btn{width:auto} .choice .btn:last-child{margin-left:auto} }
.actions{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem;align-items:center}
.actions .btn,.actions button,.actions form > button{margin-top:0}
.actions form{display:contents}
@media (max-width:600px){ main form > button:not(.mini):not(.inline),main .choice .btn{width:100%} }

/* badges, pills and notices */
.badge{display:inline-block;vertical-align:middle;padding:.125rem .5rem;border-radius:5px;font-size:.86rem;
  font-weight:700;line-height:1.4;background:#6b6b6b;color:#fff}
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

/* change list: the heart of the review page */
.summary{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 .75rem}
.summary span{display:inline-block;padding:.25rem .65rem;border-radius:var(--radius);font-weight:700;
  font-size:.9rem;background:var(--surface-2);border:1px solid var(--border-soft)}
.summary .add{color:var(--green-text);border-color:var(--green);background:var(--green-tint)}
.summary .del{color:var(--red-text);border-color:var(--red);background:var(--red-tint)}
.summary .chg{color:var(--orange-text);border-color:var(--orange);background:var(--orange-tint)}
ul.changes{list-style:none;margin:0 0 .75rem;padding:0;border:1px solid var(--border);
  border-radius:var(--radius-panel);overflow:hidden}
.changes li{display:grid;grid-template-columns:5.6rem minmax(0,1fr) auto;align-items:center;gap:.6rem;
  padding:.5rem .75rem;border-bottom:1px solid var(--border-soft);border-left:5px solid var(--border)}
.changes li:last-child{border-bottom:0}
.changes .act{font-size:.78rem;font-weight:900;text-transform:uppercase;letter-spacing:.06em;
  text-align:center;padding:.2rem .3rem;border-radius:3px;border:1px solid}
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
@media (max-width:420px){
  .changes li{grid-template-columns:4.9rem minmax(0,1fr);gap:.35rem .6rem}
  .changes .qty{grid-column:2;text-align:left}
}
details.raw{margin:.5rem 0 0} details.raw summary{cursor:pointer;color:var(--text-muted);font-size:.9rem}
.apply{margin-top:1rem;padding:1rem;border:2px solid var(--orange);border-radius:var(--radius-panel);
  background:var(--orange-tint)}
.apply h2{margin:0 0 .4rem;font-size:1.25rem;font-weight:900} .apply p{margin:0 0 .25rem}
.apply .choice{margin-top:.9rem}

/* consent page: who is asking, and where you end up */
.consent .ask{font-size:1.1rem;margin:0 0 .75rem}
.consent .who{display:flex;flex-direction:column;gap:.15rem;padding:.9rem 1rem;margin:0 0 1rem;
  border:1px solid var(--border);border-left:5px solid var(--blue);border-radius:var(--radius-panel);
  background:var(--blue-tint)}
.consent .who .app{font-size:1.5rem;font-weight:900;line-height:1.2;overflow-wrap:anywhere}
.consent .who .host{color:var(--text-muted);font-size:.93rem;overflow-wrap:anywhere}
.consent .meta{font-size:1rem} .consent .meta code{font-size:1em}

/* companion layout: wide pages, two panes from 600px */
.wide .wrap{max-width:2300px}
.wide main.wrap{padding-left:1rem;padding-right:1rem}
@media (min-width:600px){
  .wide main.wrap.panes{display:grid;grid-template-columns:minmax(14rem,20rem) minmax(0,1fr);gap:1rem;
    align-items:start}
  .wide main.wrap.panes > h1{grid-column:1 / -1;margin-bottom:.25rem}
  .pane.list{position:sticky;top:60px;max-height:calc(100vh - 80px);overflow:auto}
  .pane.detail.empty{display:flex;align-items:center;justify-content:center;min-height:12rem;
    border:1px dashed var(--border);border-radius:var(--radius-panel)}
}
@media (max-width:599px){ .pane.aside,.pane.detail.empty{display:none} }

@media (max-width:900px){ .topbar nav.site a{padding:0 .45rem;font-size:.93rem} .brand{margin-right:.25rem} }
/* home dashboard: section tiles (Archidekt's landing cards) */
.home .hero{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:1rem;
  margin:0 0 1rem}
.home .hero h1{margin:0;font-size:1.6rem}
.home .hero .sub{color:var(--text-muted);margin:.2rem 0 0}
.home .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));gap:1rem;margin:0 0 1rem}
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
.home .recent{display:grid;grid-template-columns:repeat(auto-fill,minmax(10rem,1fr));gap:.75rem}
.home .recent a{display:block;position:relative;aspect-ratio:16/9;border-radius:var(--radius);overflow:hidden;
  background:var(--surface-3) center/cover no-repeat;color:#fff;text-decoration:none;font-weight:700}
.home .recent a span{position:absolute;left:0;right:0;bottom:0;padding:.4rem .5rem;
  background:linear-gradient(to top,rgba(0,0,0,.85),rgba(0,0,0,0));font-size:.9rem;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.home details.connect summary{cursor:pointer;font-weight:700;display:flex;flex-wrap:wrap;gap:.25rem .75rem;
  align-items:baseline}
.home details.connect summary .addr{font-weight:400;font-size:.9rem;word-break:break-all}
.home .panel-head{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin:0 0 .75rem}
.home .panel-head h2{margin:0}
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
"""
)

# The compact layout (bottom tab bar), written once and applied under each condition (a media
# query cannot see a body class, so the rules are emitted per condition). {b} prefixes selectors
# on <body>.
_MOBILE_RULES = """
  body{b} .topbar .wrap{padding:0 1rem;gap:.5rem;padding-left:max(1rem,env(safe-area-inset-left));
    padding-right:max(1rem,env(safe-area-inset-right))}
  body{b} .topbar nav.site a:not(.keep){display:none}
  body{b} .topbar .searchbtn{display:inline-flex}
  body{b} .tabbar > a.railonly{display:none}
  body{b}.has-tabbar main.wrap{padding-bottom:calc(5.5rem + env(safe-area-inset-bottom))}
  body{b}.has-tabbar footer{padding-bottom:calc(5.5rem + env(safe-area-inset-bottom))}
  body{b} .tabbar{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;position:fixed;left:0;right:0;
    bottom:0;z-index:8;background:var(--toolbar-bg);color:var(--toolbar-text);
    border-top:1px solid var(--border);
    padding-bottom:env(safe-area-inset-bottom);box-shadow:0 -2px 10px rgba(0,0,0,.08)}
  body{b} .tabbar > a{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.2rem;
    height:56px;color:var(--toolbar-text);text-decoration:none;font-weight:700;font-size:11px;
    transition:color .2s ease;-webkit-tap-highlight-color:transparent}
  body{b} .tabbar > a svg{width:22px;height:22px;transition:transform .15s ease}
  body{b} .tabbar > a:active svg{transform:scale(.92)}
  body{b} .tabbar > a:hover,body{b} .tabbar > a:focus-visible,
  body{b} .tabbar > a[aria-current=page]{color:var(--orange)}
  body{b} .tabbar > a[aria-current=page]::before{content:'';position:absolute;top:0;width:2.5rem;height:3px;
    background:var(--orange);border-radius:0 0 3px 3px}
  body{b} .tabbar > a{position:relative}
  body{b} .tabbar details.more summary{display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:.2rem;height:56px;color:var(--toolbar-text);font-weight:700;font-size:11px;
    position:relative;-webkit-tap-highlight-color:transparent}
  body{b} .tabbar details.more summary svg{width:22px;height:22px}
  body{b} .tabbar details.more summary.on,body{b} .tabbar details.more[open] summary{color:var(--orange)}
  body{b} details.dd .menu{min-width:14rem}
"""
# The medium layout: the same tabs as a navigation rail (80px wide, icon in a pill with the label
# under it, as Material's rail draws it) fixed to the left edge; the top bar keeps the brand and
# the account menu and loses its text links. The body is padded by the rail's width so every page
# re-centres in the remaining space.
_RAIL_RULES = """
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
  body{b} .tabbar > a[aria-current=page]{color:var(--orange)}
  body{b} .tabbar > a[aria-current=page]::before{background:rgba(255,111,0,.22)}
  body{b} .tabbar details.more summary{position:relative;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:.3rem;height:64px;flex:none;color:var(--toolbar-text);
    font-weight:700;
    font-size:11px;-webkit-tap-highlight-color:transparent}
  body{b} .tabbar details.more summary svg{width:24px;height:24px;position:relative;z-index:1}
  body{b} .tabbar details.more summary::before{content:'';position:absolute;top:7px;left:50%;width:56px;
    height:32px;margin-left:-28px;border-radius:16px;background:transparent}
  body{b} .tabbar details.more summary.on,body{b} .tabbar details.more[open] summary{color:var(--orange)}
  body{b} .tabbar details.more[open] summary::before{background:rgba(255,111,0,.22)}
  body{b} .tabbar details.more .menu.sheet{left:calc(84px + env(safe-area-inset-left));transform:none;
    bottom:auto;top:calc(7rem + env(safe-area-inset-top) + 4 * 64px)}
  body{b} details.dd .menu{min-width:14rem}
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
    "image": "<path d='M4 5h16v14H4z'/><circle cx='9' cy='10' r='1.5'/><path d='M4 17l5-5 4 4 3-3 4 4'/>",
    "box": "<path d='M3 8l9-5 9 5-9 5z'/><path d='M3 8v8l9 5 9-5V8'/><path d='M12 13v8'/>",
    "play": "<circle cx='12' cy='12' r='9'/><path d='M10 8l6 4-6 4z' fill='currentColor'/>",
    "plus": "<path d='M12 5v14M5 12h14'/>",
    "minus": "<path d='M5 12h14'/>",
    "check": "<path d='M5 12l4 4L19 6'/>",
    "x": "<path d='M6 6l12 12M18 6L6 18'/>",
    "undo": "<path d='M9 14L4 9l5-5'/><path d='M4 9h10a6 6 0 0 1 0 12h-3'/>",
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
    "drag": "<circle cx='9' cy='6' r='1.3' fill='currentColor'/>"
    "<circle cx='15' cy='6' r='1.3' fill='currentColor'/><circle cx='9' cy='12' r='1.3' fill='currentColor'/>"
    "<circle cx='15' cy='12' r='1.3' fill='currentColor'/>"
    "<circle cx='9' cy='18' r='1.3' fill='currentColor'/>"
    "<circle cx='15' cy='18' r='1.3' fill='currentColor'/>",
}


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


def theme_from_cookie(value: str | None) -> str:
    return value if value in THEMES else "system"


# The site navigation, in Archidekt's order of sections as far as the gateway has them: decks,
# deck search, cards you own, scanning, then the gateway's own review pages. The same list feeds
# the top bar on wide screens and the bottom tab bar on phones.
NAV_LINKS = [
    ("/decks", "Decks", "decks"),
    ("/search", "Search", "search"),
    ("/collection", "Collection", "collection"),
    ("/scan", "Scan", "scan"),
    ("/proposals", "Proposals", "proposals"),
    ("/history", "History", "history"),
]
# The tabs, as Material's navigation bar (3 to 5 destinations) and rail (up to 7) have them: on a
# compact screen Decks, Scan, Collection, Proposals and More, with Search as the icon in the top
# bar; the rail has room for Search as a sixth tab. More opens a sheet with the remaining pages
# (Home, History, Guide, Admin, Account) so nothing is more than two taps away.
TAB_LINKS = [
    ("/decks", "Decks", "decks", ""),
    ("/search", "Search", "search", "railonly"),
    ("/scan", "Scan", "scan", ""),
    ("/collection", "Collection", "collection", ""),
    ("/proposals", "Proposals", "proposals", ""),
]
MORE_LINKS = [
    ("/", "Home", "home"),
    ("/history", "History", "history"),
    ("/guide", "Guide", "guide"),
    ("/account", "Account", "account"),
]


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
    scripts: bool = False,
    panes: bool = False,
    current: str | None = None,
    heading: bool = True,
    head_extra: str = "",
    body_class: str = "",
) -> HTMLResponse:
    """Render a page. ``form_action`` adds exact origins a form on this page may submit or be
    redirected to (browsers apply form-action to the redirect that follows a POST too).
    ``scripts`` allows the page to load scripts from the gateway's own origin (``script-src
    'self'``; inline script stays forbidden), used by the click guard of clickguard.py and the
    deck pages. ``current`` names the nav link to mark as the current page (its path);
    ``heading=False`` leaves the <h1> to the body (deck banner)."""
    theme = current_theme()
    app = in_app()
    nav = ""
    tabbar = ""
    cur = current or ""
    if signed_in:
        csrf_in = f"<input type='hidden' name='csrf' value='{html.escape(csrf or '')}'>"
        links = list(NAV_LINKS)
        if admin:
            links.append(("/admin", "Admin", "settings"))

        def a(href: str, label: str, keep: bool = False) -> str:
            aria = " aria-current='page'" if cur == href else ""
            return f"<a href='{href}'{aria}{' class=keep' if keep else ''}>{label}</a>"

        theme_items = "".join(
            f"<button name='theme' value='{t}'{' class=on' if theme == t else ''}>"
            f"{icon('check') if theme == t else '<span class=i></span>'}{label} theme</button>"
            for t, label in (("light", "Light"), ("dark", "Dark"), ("system", "System"))
        )
        account_menu = (
            "<details class='dd'><summary class='icon-btn' aria-label='Account menu'>"
            # The member's own picture (their provider's, else their initials: /account/avatar).
            "<img class='av' src='/account/avatar' alt='' width='28' height='28'></summary><div class='menu'>"
            f"<a href='/'>{icon('home')}Home</a>"
            f"<a href='/account'>{icon('account')}Account</a>"
            f"<a href='/proposals'>{icon('proposals')}Proposals</a>"
            f"<a href='/history'>{icon('history')}History</a>"
            f"<a href='/activity'>{icon('history')}My activity</a>"
            f"<a href='/guide'>{icon('guide')}Guide</a>"
            f"<a href='/skill'>{icon('report')}Assistant skill</a>"
            + (f"<a href='/app'>{icon('download')}Android app</a>" if not app else "")
            + (
                # The Android app's own actions (its floating button offers them over other pages);
                # feedback.js calls window.MtgNative for each data-native button.
                "<div class='sep'></div><div class='head'>App</div>"
                f"<button type='button' data-native='openCamera'>{icon('camera')}Scan with phone camera"
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
            "<div class='sep'></div>"
            f"<form method='post' action='/logout'>{csrf_in}<button>{icon('x')}Sign out</button></form>"
            "</div></details>"
        )
        nav = (
            "<nav class='site' aria-label='Site'>"
            + "".join(a(href, label) for href, label, _ic in links)
            + "</nav>"
        )
        search_btn = (
            f"<a class='icon-btn searchbtn' href='/search' aria-label='Search decks'"
            f"{' aria-current=page' if cur == '/search' else ''}>{icon('search')}</a>"
        )
        right = f"<nav class='user' aria-label='Account'>{search_btn}{account_menu}</nav>"
        more_links = list(MORE_LINKS) + ([("/admin", "Admin", "settings")] if admin else [])
        more_open = cur in {href for href, _l, _i in more_links} and cur != "/"
        tabbar = (
            "<nav class='tabbar' aria-label='Sections'>"
            + "".join(
                f"<a href='{href}' class='{cls}'{' aria-current=page' if cur == href else ''}>"
                f"{icon(ic)}{label}</a>"
                for href, label, ic, cls in TAB_LINKS
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
        right = ""
    h1 = f"<h1>{html.escape(title)}</h1>" if heading else ""
    classes = " ".join(
        c
        for c in ("wide" if wide else "", "has-tabbar" if tabbar else "", "app" if app else "", body_class)
        if c
    )
    main_cls = "wrap panes" if panes else "wrap"
    doc = (
        f"<!doctype html><html lang='en'{f' data-theme={theme}' if theme != 'system' else ''}>"
        "<head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
        "<meta name='referrer' content='no-referrer'>"
        f"<meta name='color-scheme' content='{'dark light' if theme == 'system' else theme}'>"
        "<meta name='theme-color' content='#111111'>"
        "<link rel='manifest' href='/app.webmanifest'>"
        f"<title>{html.escape(title)} · {html.escape(site)}</title><style>{CSS}</style>{head_extra}</head>"
        f"<body class='{classes}'>"
        "<header class='topbar'><div class='wrap'><div class='left'>"
        f"<a class='brand' href='/'{' aria-current=page' if cur == '/' else ''}>"
        f"<span class='mark'>{icon('layers')}</span>"
        f"<span class='word'>{html.escape(site)}</span></a>{nav}</div>"
        f"<div class='right'>{right}</div></div></header>"
        f"<main class='{main_cls}'>{h1}{body}</main>"
        + (
            # The website footer; the Android app has no footer (its pages end at the tab bar).
            "<footer class='site'><div class='links'><a href='/decks'>Decks</a><a href='/search'>Search</a>"
            "<a href='/collection'>Collection</a><a href='/scan'>Scan</a><a href='/guide'>Guide</a>"
            "<a href='/skill'>Assistant</a><a href='/app'>Apps</a><a href='/account'>Account</a></div>"
            "<div class='legal'>Private deck gateway. Nothing on these pages is indexed or shared. "
            "Magic: The Gathering is a trademark of Wizards of the Coast. Card data and images come from "
            "Scryfall; decks live on Archidekt.</div></footer>"
            if not app
            else ""
        )
        + f"{tabbar}<script src='{FEEDBACK_SCRIPT}' defer></script></body></html>"
    )
    return HTMLResponse(
        doc,
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
        app = False
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                for part in v.decode("latin-1").split(";"):
                    name, _, val = part.strip().partition("=")
                    if name == THEME_COOKIE:
                        value = val.strip()
            elif k == b"user-agent" and APP_UA_MARK in v.decode("latin-1", "replace"):
                app = True
        token = _theme.set(theme_from_cookie(value))
        path = scope.get("path") or "/"
        ptoken = _path.set(path)
        atoken = _app.set(app)
        try:
            await self.app(scope, receive, send)
        finally:
            _theme.reset(token)
            _path.reset(ptoken)
            _app.reset(atoken)


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
