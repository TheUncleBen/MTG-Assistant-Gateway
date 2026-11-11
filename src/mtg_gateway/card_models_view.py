"""Card models: the page /history/models, where a member learns what a card model is and reviews
their own.

At the top, what a card model is, how it differs from looking a card up, how to add, change and
remove one, and who sees them (EXPLAIN; open while the member keeps none). Then the member's own,
each a row (the card's name and when it was added or last changed) that opens to what their quick
solo tests do with it in plain words (card_models.describe), the exact saved data, and its actions:
Open card (the card viewer), Ask your assistant to change it (a request to copy, holding what is
saved now) and Remove, which asks first. Each row has the same actions in its menu (right-click,
press and hold, ⋮: static/menus.js). The list is searched by card name in the page
(static/cardmodels.js). Only the member's own are ever listed (card_models.KeptModels.mine). No hand
editing: a model is written by the assistant and checked by the gateway, so a change goes through
the assistant. The report page and the Goldfish page link here (opener) where saved models are
mentioned, and the card viewer to one card's model (anchor).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from . import ui
from .card_models import MAX_PER_MEMBER, SIM_VERSION, describe
from .mana import rules_html
from .theme import esc, icon

TITLE = "Card models"
PAGE = "/history/models"
REMOVE_ACTION = "/history/models/remove"
SUB_TEXT = "Notes that teach the solo test, which plays your deck by itself, what a card does."
# What a card model is, for a member who has never heard the words (the owner, 2026-11-11: "what's the
# difference between a card model and just looking up a card?"). The first answer stays open on
# the page; the report and the card viewer link here.
EXPLAIN: tuple[tuple[str, str], ...] = (
    (
        "What is a card model?",
        "A short note that tells the quick solo test (the test that plays your deck by itself, "
        "hundreds of times, to see how it draws and casts) what one card does. For example, a card "
        "says “When this enters, draw two cards”. If the solo test can't read that card, it casts "
        "it and nothing happens, so your report never counts those two cards. A card model for "
        "it says just “When it enters: draw 2 cards”, and from then on your quick solo tests "
        "play the card that way. The solo test reads most cards by itself; card models are only "
        "for the ones it can't.",
    ),
    (
        "How is it different from looking up a card?",
        "Looking up a card shows it to you: its text, picture, rulings and prices. A card model "
        "teaches it to the quick solo test, so the test plays it. It doesn't change the card, "
        "your deck on Archidekt, or full games against opponents (Forge, where your gateway has "
        "it), which play nearly every card by its real rules.",
    ),
    (
        "How do I add one?",
        "Open a deck report and find “Played without their effect”. Press “Copy a request for your "
        "assistant” and paste it into Claude or ChatGPT. Your assistant reads each card's exact "
        "text, writes the models it can and runs a new report. The gateway checks each model and "
        "saves the ones that pass. Some cards can't be modeled, such as cards that only matter "
        "against opponents.",
    ),
    (
        "How do I change or remove one?",
        "Open a card model below. “Ask your assistant to change it” copies a request that holds "
        "what is saved now; the corrected model your assistant saves replaces it. Remove deletes "
        "it, and later tests play the card without its effect again. Reports that already ran "
        "keep their numbers.",
    ),
    (
        "Who sees them?",
        f"Only you. Your card models play only in your own quick solo tests, never in anyone "
        f"else's. You can keep up to {MAX_PER_MEMBER}.",
    ),
)
EMPTY_TEXT = "No card models yet. Open “How do I add one?” above to make your first."
WHAT_HEADING = "What your quick solo tests do with it"
SAVED_HEADING = "Exact saved data"
CHANGE_LABEL = "Ask your assistant to change it"
OLDER_TEXT = (
    "Saved for an older version of the solo test. Ask your assistant to check it again: a saved "
    "model it rewrites is checked against the current version."
)
REMOVE_TEXT = (
    "Later solo tests play “{card}” without its effect again, until a new model is saved. "
    "The reports that already played it keep their numbers."
)
# where a Remove may send the member back: their own pages that show the ?models= notice
BACK_PREFIXES = ("/history/", "/decks/")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).casefold()).strip("-") or "card"


def safe_back(back: str | None, default: str = PAGE) -> str:
    """The page a Remove returns to: one of the member's own pages, never an outside address."""
    b = str(back or "")
    if b.startswith(BACK_PREFIXES) and not b.startswith("//") and "\n" not in b and len(b) <= 200:
        return b
    return default


def opener(*, kind: str = "plain", cls: str = "") -> str:
    """The button that opens the Card models page, where saved models are mentioned."""
    return ui.button_link(TITLE, PAGE, kind=kind, cls=cls, icon_name="layers")


def anchor(name: str) -> str:
    """The page's own address for one card's model, opened (static/cardmodels.js)."""
    return f"{PAGE}#m-{slug(name)}"


def _when(m: dict[str, Any]) -> str:
    added, changed = m.get("added_at"), m.get("updated_at")
    if changed and added and int(changed) != int(added):
        return f"changed {ui.when(changed)}"
    return f"added {ui.when(added or changed)}" if (added or changed) else ""


def change_request(name: str, model: dict[str, Any] | None) -> str:
    """What the member pastes into Claude or ChatGPT to have one saved model checked and, when it
    is wrong, replaced: the same words for both. A model is replaced only by a new report run on a
    deck that holds the card (card_models.KeptModels.keep)."""
    saved = ""
    if model:
        words = " ".join(describe(model))
        saved = (
            f" It is saved as {json.dumps(model, separators=(',', ':'))}, which in plain words is: {words}"
        )
    return (
        f"My MTG gateway keeps a card model for “{name}” that my quick solo tests play.{saved} "
        "Check it against the card's exact Oracle text (the gateway's card text and rules tools, "
        "never from memory) and tell me whether it is right. If it should change, write the "
        f"corrected model and run a new deck report for one of my decks that holds {name}, with the "
        "corrected model as its annotation, so the gateway saves it in place of the old one. Then "
        "tell me what you changed."
    )


def _item(m: dict[str, Any]) -> str:
    """One saved model: its row (name, when) opens what is saved and what can be done with it."""
    name = str(m.get("name") or "")
    sid = slug(name)
    model = m.get("model") if isinstance(m.get("model"), dict) else None
    when = _when(m)
    lines = describe(model) if model else []
    what = (
        "<ul class='mm-does'>" + "".join(f"<li>{rules_html(t)}</li>" for t in lines) + "</ul>"
        if lines
        else "<p class='muted'>The saved model could not be read.</p>"
    )
    older = (
        f"<p class='notice warn mm-older'>{esc(OLDER_TEXT)}</p>"
        if model and m.get("sim_version") and m.get("sim_version") != SIM_VERSION
        else ""
    )
    raw = (
        f"<details class='mm-raw'><summary>{esc(SAVED_HEADING)}{icon('chev', cls='i chev')}</summary>"
        f"<pre>{esc(json.dumps(model, indent=2))}</pre></details>"
        if model
        else ""
    )
    ask_id = f"mm-ask-{sid}"
    actions = (
        "<div class='mm-acts'>"
        f"<button type='button' class='btn btn-plain cardlink' data-card='{esc(name)}'>"
        f"{icon('eye')} Open card</button>"
        f"<button type='button' class='btn btn-plain copybtn' data-copy='{ask_id}'>{icon('copy')}"
        f"<span class='label'>{esc(CHANGE_LABEL)}</span></button>"
        f"<a class='btn btn-outline-danger mm-rm' href='{PAGE}?remove={quote(name)}' "
        f"data-remove-model='{esc(name)}'>{icon('trash')} Remove</a>"
        f"<span id='{ask_id}-status' class='sr-only' role='status' aria-live='polite'></span>"
        f"<textarea id='{ask_id}' class='sr-only' readonly tabindex='-1' "
        f"aria-label='{esc(CHANGE_LABEL)}: {esc(name)}'>{esc(change_request(name, model))}</textarea>"
        "</div>"
    )
    return (
        f"<li class='mm-item' id='m-{sid}' data-name='{esc(name.casefold())}' data-card='{esc(name)}'>"
        f"<details><summary class='mm-sum'><span class='mm-t'><span class='nm'>{esc(name)}</span>"
        + (f"<span class='mm-when'>{when}</span>" if when else "")
        + f"</span>{icon('chev', cls='i chev')}</summary>"
        f"<div class='mm-body'><h3>{esc(WHAT_HEADING)}</h3>{what}{older}{raw}{actions}</div></details>"
        f"<button type='button' class='kebab' aria-label='Menu for {esc(name)}'>{icon('more')}</button>"
        "</li>"
    )


def explain_html() -> str:
    """What a card model is, as grouped questions; the first, with its example, always open (audit AY2)."""
    rows = "".join(
        f"<details class='mm-q'{' open' if i == 0 else ''}><summary>{esc(q)}"
        f"{icon('chev', cls='i chev')}</summary><p>{esc(a)}</p></details>"
        for i, (q, a) in enumerate(EXPLAIN)
    )
    return f"<section class='mm-explain' aria-label='About card models'>{rows}</section>"


def page_html(models: list[dict[str, Any]], *, csrf: str, remove: str = "", notice_html: str = "") -> str:
    """The Card models page: what card models are, then the member's own, each opening to what is
    saved and what can be done with it (Open card, change through the assistant, Remove). One
    hidden form removes (its card filled in by the press); ``remove`` names a card whose confirm
    box is drawn open (the no-script path)."""
    body = [ui.page_head(TITLE, SUB_TEXT), notice_html, explain_html()]
    if models:
        n = len(models)
        body.append(
            f"<section class='mm-mine' aria-labelledby='mm-mine-h'><div class='mm-top'>"
            f"<h2 id='mm-mine-h'>Your card models <span class='mm-n'>{n}</span></h2>"
            + (
                "<div class='field mm-search'><label for='mm-q' class='sr-only'>Search your card models"
                "</label><input id='mm-q' type='search' data-models-filter placeholder='Card name'"
                " autocomplete='off'></div>"
                if n > 5
                else ""
            )
            + "</div><p class='sr-only' data-models-count role='status' aria-live='polite'></p>"
            f"<ul class='mm-list' data-models-list>{''.join(_item(m) for m in models)}</ul>"
            "<p class='muted mm-none' data-models-none hidden>No card model matches that name.</p></section>"
        )
    else:
        body.append(f"<p class='mm-empty'>{esc(EMPTY_TEXT)}</p>")
    body.append(
        f"<form id='mm-rm-f' method='post' action='{REMOVE_ACTION}' hidden>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='back' value='{PAGE}'>"
        "<input type='hidden' name='card' value=''></form>"
    )
    out = f"<div class='mm-page'>{''.join(body)}</div>"
    if remove:
        out += confirm_box_html(remove, csrf=csrf, back=PAGE)
    return out


def confirm_box_html(card: str, *, csrf: str, back: str) -> str:
    """The confirm for one card's Remove, drawn open on the page (the no-script path; with scripts
    static/cardmodels.js asks with MtgBox.confirm instead)."""
    fid = f"rm-mine-{slug(card)}-f"
    body = (
        f"<form id='{fid}' method='post' action='{REMOVE_ACTION}'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='back' value='{esc(back)}'>"
        f"<input type='hidden' name='card' value='{esc(card)}'></form>"
        f"<p>{esc(REMOVE_TEXT.format(card=card))}</p>"
    )
    return ui.box(
        f"rm-mine-{slug(card)}",
        f"Remove the card model of “{card}”?",
        body,
        actions=[
            ui.box_button("Keep it", cancel=True),
            ui.box_button("Remove", kind="danger-solid", form=fid, icon_name="trash"),
        ],
        open=True,
        close_href=PAGE,
    )


CSS = """
.mm-page{max-width:46rem;margin:0 auto}
.mm-explain{margin:1rem 0 1.5rem;border:1px solid var(--border-soft);border-radius:14px;overflow:hidden;
  background:var(--surface)}
.mm-q+.mm-q{border-top:1px solid var(--border-soft)}
.mm-q summary,.mm-sum{display:flex;align-items:center;gap:8px;min-height:48px;padding:8px 16px;
  cursor:pointer;list-style:none;font-weight:600}
.mm-q summary::-webkit-details-marker,.mm-sum::-webkit-details-marker{display:none}
.mm-q summary .chev,.mm-sum .chev,.mm-raw summary .chev{margin-left:auto;flex:none;transition:transform .15s}
.mm-q[open] summary .chev,.mm-item details[open] .mm-sum .chev,
.mm-raw[open] summary .chev{transform:rotate(180deg)}
.mm-q p{margin:0;padding:0 1rem 1rem;color:var(--text-muted);line-height:1.5}
.mm-top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem 1rem;
  margin:0 0 .5rem}
.mm-top h2{margin:0;font-size:1.15rem}
.mm-n{color:var(--text-muted);font-weight:600}
.mm-search{margin:0;flex:1 1 14rem;max-width:20rem}
@media (max-width:600px){.mm-search{max-width:none}}
.mm-search input{width:100%}
.mm-list{list-style:none;margin:0;padding:0;border:1px solid var(--border-soft);border-radius:14px;
  overflow:hidden;background:var(--surface)}
.mm-item{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start}
.mm-item+.mm-item{border-top:1px solid var(--border-soft)}
.mm-item[hidden]{display:none}
.mm-item>details{min-width:0;container-type:inline-size}
/* large text on a narrow screen: the name keeps the row's room; the row still opens on a tap */
@container (max-width:15rem){.mm-sum .chev{display:none}}
.mm-sum{padding:8px 0 8px 12px}
.mm-t{display:flex;flex-direction:column;min-width:0;flex:1 1 auto}
.mm-sum .nm{overflow-wrap:break-word}
.mm-when{color:var(--text-muted);font-weight:400;font-size:.85rem;overflow-wrap:break-word}
.mm-item>.kebab{margin:2px 4px 0 0}
.mm-body{padding:0 12px 12px}
.mm-body h3{font-size:.95rem;margin:.25rem 0 .5rem;color:var(--text-muted);font-weight:600}
.mm-does{margin:0 0 .75rem;padding-left:1.1em;line-height:1.5;overflow-wrap:break-word}
.mm-does li+li{margin-top:.25rem}
.mm-older{margin:0 0 .75rem}
.mm-raw{margin:0 0 .75rem}
.mm-raw summary{cursor:pointer;min-height:44px;display:flex;align-items:center;gap:.5rem;list-style:none;
  color:var(--text-muted);font-weight:600}
.mm-raw summary::-webkit-details-marker{display:none}
.mm-raw summary .chev{margin-left:0}
.mm-raw pre{margin:0;padding:.75rem;border-radius:10px;background:var(--surface-2,var(--bg));
  border:1px solid var(--border-soft);font-size:.85rem;line-height:1.4;white-space:pre-wrap;
  overflow-wrap:anywhere;max-height:20rem;overflow:auto}
.mm-acts{display:flex;flex-wrap:wrap;gap:.5rem}
.mm-acts .cardlink:hover{text-decoration:none}
.mm-acts .btn{margin:0;min-height:44px;flex:1 1 auto;justify-content:center;padding:0 1rem;gap:.4rem;
  text-wrap:balance}
.mm-empty{margin:0;color:var(--text-muted)}
.gf-models,.rv-models{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin:.5rem 0 0}
.gf-models .btn,.rv-models .btn{margin:0}
"""
