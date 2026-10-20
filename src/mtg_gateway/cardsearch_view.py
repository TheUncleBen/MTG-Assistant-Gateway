"""What the card search page (/cards) draws: the filters (every field of Scryfall's advanced search,
cardquery.py), the results as picture tiles or as a list, and the page's own styles.

One renderer serves the first page load and the live updates (static/cardsearch.js asks
/cards/api/search for the next results and puts the HTML it gets in place), so the two can't drift.
The page works without scripts too: the filters are a plain GET form and each picker a text box.
"""

from __future__ import annotations

from typing import Any

from . import cardquery as cq
from .deckpage import mana_html
from .mana import pip_html
from .theme import card_size_control, icon
from .theme import esc as _esc

VIEWS = ("grid", "list")

CARDS_CSS = """
.cs-panel{padding-bottom:1rem}
.cs-top{display:flex;flex-wrap:wrap;align-items:end;gap:.75rem 1rem}
.cs-top .field{margin:0;flex:1 1 18rem;min-width:0}
.cs-top .search{margin:0}
.cs-top .search > input{border-radius:var(--radius) 0 0 var(--radius);border-right:0;flex:1 1 auto;
  min-width:0}
.cs-top .search button{height:var(--ctl);min-height:var(--ctl);padding:0 .9rem}
.cs-top > .btn{margin:0;height:var(--ctl);white-space:nowrap}
.cs-open{display:none}
.cs-open .n{display:inline-flex;align-items:center;justify-content:center;min-width:1.4rem;height:1.4rem;
  padding:0 .35rem;border-radius:.7rem;background:var(--orange-ctl);color:#fff;font-size:.8rem;
  font-weight:700}
.cs-open .n:empty{display:none}
.cs-filters{margin:1rem 0 0}
.cs-sheet-head,.cs-sheet-foot{display:none}
.cs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,15.5rem),1fr));gap:1rem 1.25rem;
  align-items:start}
.cs-grid > .field,.cs-grid > fieldset{margin:0;min-width:0}
/* the rows of three or two controls need two columns' room where there are two */
/* the whole row, not two columns: at a large text size the grid has one column, and a span of two
   would add an empty second one beside it */
@media (min-width:600px){.cs-grid > .rowset{grid-column:1 / -1}}
.cs .row3 .msel-btn,.cs .row2 .msel-btn{min-width:0;width:100%}
.cs fieldset{border:0;padding:0;margin:0;min-width:0}
.cs legend,.cs .lbl{display:block;font-weight:700;margin:0 0 .35rem;padding:0}
.cs .hint{display:block;color:var(--text-muted);font-size:.86rem;margin:.3rem 0 0}
.cs .wide{grid-column:1/-1}
/* colour and rarity toggles: a checkbox under each pip, the whole pill pressable */
.cs .toggles{display:flex;flex-wrap:wrap;gap:.4rem}
/* The main filters. The six colour pills sit on one row (White to Colorless), the colour mode
   beside them where there is room: from 1000 px the grid has five columns, Type takes one and
   Colors the other four, then Commander colors three with Mana value and Rarity beside it.
   600-999 px: Type, Colors and Commander colors take a whole row each and the mode sits under
   the pills. Phones: the pills form a grid of equal columns, two across on a 344 px screen,
   three from 360 px (one at 200% text), with the mode below at full width. */
.cs .crow{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.cs .crow .toggles{flex:0 1 auto;min-width:0}
.cs .crow .cmp,.cs .crow .msel-btn{width:auto;flex:0 1 auto;min-width:0;max-width:100%}
@media (min-width:600px) and (max-width:999.98px){
  .cs-main > [data-picker=t],.cs-main > .colors,.cs-main > .ident{grid-column:1/-1} }
@media (min-width:1000px){
  .cs-grid.cs-main{grid-template-columns:repeat(5,minmax(0,1fr));
    grid-template-areas:"type colors colors colors colors" "ident ident ident mv rar"}
  .cs-main > [data-picker=t]{grid-area:type}
  .cs-main > .colors{grid-area:colors}
  .cs-main > .ident{grid-area:ident}
  .cs-main > .mv{grid-area:mv}
  .cs-main > .rar{grid-area:rar} }
.cs .tog{margin:0;position:relative;display:inline-flex;align-items:center;gap:.4rem;min-height:2.5rem;
  padding:.25rem .65rem .25rem .4rem;border:1px solid var(--control-border);border-radius:1.25rem;
  background:var(--surface);cursor:pointer;font-weight:400;user-select:none}
.cs .tog.txt{padding:.25rem .8rem}
.cs .tog input{position:absolute;opacity:0;inset:0;width:100%;height:100%;margin:0;cursor:pointer}
.cs .tog .pip{width:1.5rem;height:1.5rem}
.cs .tog:has(input:checked){border-color:var(--orange-ctl);background:var(--orange-tint);font-weight:700}
.cs .tog:has(input:focus-visible){outline:2px solid var(--focus);outline-offset:2px}
.cs .tog:hover{border-color:var(--text-muted)}
.cs .inline{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.cs .inline select,.cs .inline input,.cs .inline .msel-btn{width:auto;flex:1 1 7rem;min-width:0}
.cs .range{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:.5rem;align-items:center}
.cs .range span{color:var(--text-muted)}
.cs .range input{width:100%;min-width:0}
.cs .rows{display:flex;flex-direction:column;gap:.5rem}
.cs .row3{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr) minmax(0,.8fr) auto;gap:.4rem;
  align-items:center}
.cs .row2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.6fr) auto;gap:.4rem;align-items:center}
.cs .rm{margin:0;width:var(--ctl);min-height:var(--ctl);height:var(--ctl);padding:0;display:inline-flex;
  align-items:center;justify-content:center}
.cs .add-row{margin:.5rem 0 0;align-self:flex-start}
/* pickers: chosen values as chips (with IS / NOT where Scryfall has it) above a search box */
.cs .cs-pick{position:relative;min-width:0}
.cs .chips{display:flex;flex-wrap:wrap;gap:.35rem;margin:0 0 .4rem;padding:0;list-style:none}
.cs .chips:empty{display:none}
.cs .chips li{display:inline-flex;align-items:center;gap:.15rem;max-width:100%;min-height:2rem;
  border:1px solid var(--control-border);border-radius:1rem;background:var(--surface-2);
  padding:0 .15rem 0 .2rem}
.cs .chips li.not{border-style:dashed}
.cs .chips .pol{margin:0;min-height:1.6rem;height:1.6rem;padding:0 .45rem;border-radius:.8rem;
  font-size:.78rem;
  font-weight:700;letter-spacing:.02em}
.cs .chips li.not .pol{background:var(--red-tint);color:var(--red-text);border-color:var(--red-text)}
.cs .chips .nm{padding:0 .3rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.cs .chips .x{margin:0;width:1.75rem;min-height:1.75rem;height:1.75rem;padding:0;border-radius:50%;
  display:inline-flex;align-items:center;justify-content:center;border:0;background:none;color:var(--text)}
.cs .chips .x:hover,.cs .chips .x:focus-visible{background:var(--surface-3)}
.cs .opts{position:absolute;left:0;right:0;top:100%;z-index:30;margin:.25rem 0 0;padding:.25rem 0;
  list-style:none;
  max-height:18rem;overflow:auto;overscroll-behavior:contain;background:var(--surface);
  border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow)}
.cs .opts[hidden]{display:none}
.cs .opts li{padding:.55rem .75rem;cursor:pointer;display:flex;justify-content:space-between;gap:.75rem;
  min-height:2.5rem;align-items:center}
.cs .opts li small{color:var(--text-muted);white-space:nowrap}
.cs .opts li.grp{cursor:default;font-size:.78rem;font-weight:700;color:var(--menu-head);min-height:0;
  padding:.5rem .75rem .2rem;text-transform:uppercase;letter-spacing:.04em}
.cs .opts li[aria-selected=true],.cs .opts li.opt:hover{background:var(--surface-2)}
.cs .opts li.none{cursor:default;color:var(--text-muted)}
.cs .checks{display:flex;flex-wrap:wrap;gap:.5rem 1.25rem}
details.cs-more{margin:1.25rem 0 0;border-top:1px solid var(--border);padding-top:.25rem}
details.cs-more > summary{cursor:pointer;font-weight:700;min-height:2.75rem;display:flex;
  align-items:center;gap:.4rem;
  list-style:none;width:max-content;max-width:100%}
details.cs-more > summary::-webkit-details-marker{display:none}
details.cs-more > summary .chev{transition:transform .15s;transform:rotate(-90deg)}
details.cs-more[open] > summary .chev{transform:none}
details.cs-more > .cs-grid{margin:.5rem 0 0}
.cs h3.sub{grid-column:1/-1;margin:.75rem 0 0;font-size:1rem;color:var(--menu-head);border-bottom:1px solid
  var(--border-soft);padding-bottom:.3rem}
.cs-actions{display:flex;flex-wrap:wrap;gap:.5rem;justify-content:flex-end;margin:1rem 0 0}
.cs-actions button{margin:0}
/* the results head: count, then sort, direction and view */
.cs-head{display:flex;flex-wrap:wrap;align-items:center;gap:.6rem 1rem;margin:0 0 .9rem}
.cs-head h2{margin:0;font-size:1.15rem;flex:1 1 12rem;min-width:0}
.cs-head h2 .muted{font-weight:400}
.cs-head .sort{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.cs-head .sort label{margin:0;font-weight:400;color:var(--text-muted)}
.cs-head .sort select,.cs-head .sort .msel-btn{width:auto;min-width:8rem;margin:0}
.cs-head .views{display:inline-flex;flex-wrap:wrap;align-items:center;gap:.5rem;min-width:0}
.cs-head .seg{display:inline-flex;border:1px solid var(--control-border);border-radius:var(--radius);
  overflow:hidden}
.cs-head .seg label{position:relative;display:inline-flex;align-items:center;gap:.35rem;margin:0;
  padding:0 .75rem;
  min-height:var(--ctl);cursor:pointer;font-weight:400;color:var(--text)}
.cs-head .seg label + label{border-left:1px solid var(--control-border)}
.cs-head .seg input{position:absolute;opacity:0;inset:0;margin:0;width:100%;height:100%;cursor:pointer}
.cs-head .seg label:has(input:checked){background:var(--orange-tint);font-weight:700}
.cs-head .seg label:has(input:focus-visible){outline:2px solid var(--focus);outline-offset:-2px}
.cs-results[aria-busy=true]{opacity:.55;transition:opacity .2s}
.cs-status{margin:0 0 .9rem}
.cs-status:empty{display:none}
.cs-warn{margin:0 0 .9rem}
.cs-more-wrap{display:flex;flex-direction:column;align-items:center;gap:.5rem;margin:.5rem 0 1.5rem}
.cs-more-wrap[hidden]{display:none}
.cs-more-wrap .muted{font-size:.9rem}
.cs-more-wrap button{margin:0;min-width:12rem}
.cardgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,10.5rem),1fr));gap:1rem .9rem;
  list-style:none;margin:0 0 1rem;padding:0}
.cardgrid .cc{display:flex;flex-direction:column;gap:.5rem;min-width:0}
.cardgrid .pic,.cardlist .pic{all:unset;display:block;cursor:pointer;position:relative;aspect-ratio:5/7;
  border-radius:4.5%;overflow:hidden;background:var(--surface-2);border:2px solid var(--card-border);
  box-sizing:border-box}
.cardgrid .pic:focus-visible,.cardlist .pic:focus-visible,.cardlist .nmb:focus-visible{outline:3px solid
  var(--orange);outline-offset:2px}
.cardgrid .pic img,.cardlist .pic img{width:100%;height:100%;display:block;object-fit:cover}
.cardgrid .pic .ph,.cardlist .pic .ph{position:absolute;inset:0;display:flex;align-items:center;
  justify-content:center;padding:.5rem;text-align:center;font-weight:700;color:var(--text-muted);
  overflow-wrap:anywhere;font-size:.85rem}
.cardgrid .meta{display:flex;flex-direction:column;gap:.15rem;min-width:0}
.cardgrid .nm{display:flex;flex-wrap:wrap;align-items:center;gap:.2rem .4rem;font-weight:700;line-height:1.25;
  overflow-wrap:anywhere;min-width:0}
.cardgrid .ty,.cardgrid .pr{font-size:.86rem;color:var(--text-muted);overflow-wrap:anywhere}
.cardgrid .add,.cardlist .add{margin:0;min-width:0}
/* every card of a row is as tall as the tallest, its Add to deck on the row's bottom edge */
.cardgrid .add{width:100%;margin-top:auto}
.cardgrid .add > span,.cardlist .add > span{min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
/* the list view: one row a card, columns that fold under the name on narrow screens */
.cardlist{list-style:none;margin:0 0 1rem;padding:0;border:1px solid var(--border);
  border-radius:var(--radius);
  background:var(--surface);overflow:hidden}
.cardlist li{display:grid;grid-template-columns:3rem minmax(0,2.2fr) minmax(0,2fr) 5.5rem 4.5rem 5rem auto;
  gap:.5rem 1rem;align-items:center;padding:.5rem .75rem;border-top:1px solid var(--border-soft)}
.cardlist li:first-child{border-top:0}
.cardlist li.hd{font-size:.8rem;font-weight:700;color:var(--menu-head);text-transform:uppercase;
  letter-spacing:.04em;background:var(--surface-2);padding-top:.4rem;padding-bottom:.4rem}
.cardlist .pic{width:3rem;border-width:1px;border-radius:6%}
.cardlist .nmc{display:flex;flex-wrap:wrap;align-items:center;gap:.15rem .5rem;min-width:0}
.cardlist .nmb{all:unset;cursor:pointer;font-weight:700;overflow-wrap:anywhere;color:var(--link)}
.cardlist .nmb:hover{text-decoration:underline}
.cardlist .ty{overflow-wrap:anywhere;min-width:0}
.cardlist .num{font-variant-numeric:tabular-nums;white-space:nowrap}
.cardlist .muted{color:var(--text-muted)}
.cardlist .add{padding:0 .7rem}
@media (max-width:899.98px){
  .cardlist li{grid-template-columns:3rem minmax(0,1fr) auto;
  grid-template-areas:"pic name add" "pic info add"}
  .cardlist li.hd{display:none}
  .cardlist .pic{grid-area:pic}
  .cardlist .nmc{grid-area:name}
  .cardlist .info{grid-area:info;display:flex;flex-wrap:wrap;gap:.1rem .75rem;font-size:.86rem;
    color:var(--text-muted);min-width:0}
  .cardlist .add{grid-area:add}
  .cardlist .add > span{display:none}
  .cardlist .ty,.cardlist .set,.cardlist .rar,.cardlist .price{display:contents}
}
@media (min-width:900px){ .cardlist .info{display:contents} }
/* phones: the filters open as a full-screen panel with "Show cards" at the bottom */
@media (max-width:599.98px){
  .cs-open{display:inline-flex}
  .cs-top > .btn.decks{display:none}
  .cs-top .field{flex-basis:100%}
  /* the search box and Filters share a row; at a large text size Filters drops below it */
  .cs-top{flex-wrap:wrap;gap:.5rem}
  .cs-top .field{flex:1 1 12rem}
  .cs-top .cs-open{flex:none}
  .cs-filters{display:none;position:fixed;inset:0;z-index:80;margin:0;background:var(--bg);overflow:auto;
    overscroll-behavior:contain;padding:0 1rem calc(5.5rem + env(safe-area-inset-bottom))}
  .cs-filters.open{display:block}
  .cs-sheet-head{display:flex;align-items:center;justify-content:space-between;gap:.75rem;
  position:sticky;top:0;
    z-index:2;background:var(--bg);padding:.75rem 0;border-bottom:1px solid var(--border);margin:0 0 .75rem}
  .cs-sheet-head h2{margin:0;font-size:1.2rem}
  .cs-sheet-head .close{margin:0;width:2.75rem;min-height:2.75rem;height:2.75rem;padding:0;border-radius:50%;
    display:inline-flex;align-items:center;justify-content:center}
  .cs-sheet-foot{display:flex;gap:.5rem;position:fixed;left:0;right:0;bottom:0;z-index:3;background:var(--bg);
    border-top:1px solid var(--border);padding:.75rem 1rem calc(.75rem + env(safe-area-inset-bottom))}
  .cs-sheet-foot button{margin:0;flex:1 1 auto;min-height:2.9rem}
  .cs-sheet-foot .btn-primary{flex:2 1 auto}
  .cs-actions{display:none}
  .cs .row3{grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,5rem) auto}
  .cs .toggles{display:grid;grid-template-columns:repeat(auto-fill,minmax(7.6rem,1fr))}
  .cs .crow > *,.cs .crow .toggles{flex:1 1 100%}
  .cs .crow .cmp,.cs .crow .msel-btn{width:100%}
  /* the results head: Sort by with its two drop-downs, then View and Card size on a row of
     their own (its label goes below 360 px; the group keeps its name) */
  .cs-head .sort{width:100%}
  .cs-head .sort select,.cs-head .sort .msel-btn{flex:1 1 8rem}
}
@media (max-width:359.98px){.cs-head .cardsize .lbl{display:none}}
html.cs-sheet-open,html.cs-sheet-open body{overflow:hidden}
/* Add to deck: a small dialog over the page (a bottom sheet on phones), themed like the card viewer */
.addsheet{position:fixed;inset:0;z-index:90;display:none;align-items:center;justify-content:center;
  background:var(--scrim-strong,rgba(0,0,0,.72));padding:1rem}
.addsheet.open{display:flex}
.addsheet .box{width:min(100%,27rem);max-height:100%;overflow:auto;overscroll-behavior:contain;
  background:var(--surface);color:var(--text);border:1px solid var(--border);
  border-radius:var(--radius-panel);padding:1.25rem;box-shadow:0 12px 40px rgba(0,0,0,.5);
  display:flex;flex-direction:column;gap:.9rem}
.addsheet .head{display:flex;align-items:flex-start;justify-content:space-between;gap:.75rem;min-width:0}
.addsheet h3{margin:0;font-size:1.25rem;line-height:1.25;overflow-wrap:anywhere;min-width:0;display:flex;
  flex-wrap:wrap;align-items:center;gap:.4rem .5rem}
.addsheet .close{flex:none;margin:0;width:2.4rem;min-height:2.4rem;height:2.4rem;font-size:1.5rem;
  line-height:1;font-weight:400;border-radius:50%}
.addsheet .field{margin:0}
.addsheet .field > label{margin:0}
.addsheet .two{display:grid;grid-template-columns:6rem minmax(0,1fr);gap:.9rem}
.addsheet .msg{margin:0;font-size:.93rem;color:var(--text-muted);min-height:1.2em}
.addsheet .msg.err{color:var(--red-text);font-weight:700}
.addsheet .note{margin:0;font-size:.9rem;color:var(--text-muted)}
.addsheet .acts{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:.5rem;margin-top:.25rem}
.addsheet .acts button{margin:0}
@media (max-width:600px){
  .addsheet{padding:0;align-items:flex-end}
  .addsheet .box{width:100%;max-height:94vh;max-height:94dvh;border-bottom:0;
    border-radius:var(--radius-panel) var(--radius-panel) 0 0;
    padding-bottom:calc(1.25rem + env(safe-area-inset-bottom))}
  .addsheet .acts button{flex:1 1 auto} }
/* Watch on each result: a pill on the picture's top right corner, a check and "Watching"
   when the card is watched; icon only at card size S and in the list view; 44 px on touch */
.cs-hint{margin:0 0 .9rem}
.picwrap{position:relative;display:block}
.picwrap > .pic{width:100%}
.wbtn{position:absolute;z-index:1;top:.4rem;right:.4rem}
html[data-card-size=s] .cardgrid .wbtn span{position:absolute;width:1px;height:1px;
  overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
html[data-card-size=s] .cardgrid .wbtn{padding:0;justify-content:center;width:32px}
.cardlist .acts{display:flex;align-items:center;gap:.4rem}
.cardlist .wbtn{position:static;width:var(--ctl);height:var(--ctl);min-height:var(--ctl);
  border-radius:var(--radius);box-shadow:none}
.cardlist .wbtn.on{box-shadow:inset 0 0 0 99px var(--green-tint)}
@media (pointer:coarse){html[data-card-size=s] .cardgrid .wbtn{width:44px}}
@media (max-width:899.98px){.cardlist .acts{grid-area:add}}
/* the card menu on results: theme.CARD_MENU_CSS (static/cardmenu.js) */
.cardgrid .pic img{-webkit-touch-callout:none}
"""

RARITY_NAMES = dict(cq.RARITIES)


def _opts(choices: Any, chosen: str) -> str:
    return "".join(
        f"<option value='{_esc(v)}'{' selected' if v == chosen else ''}>{_esc(label)}</option>"
        for v, label in choices
    )


def _toggle(name: str, value: str, label: str, checked: bool, *, pip: bool = False) -> str:
    inner = pip_html(value) if pip else ""
    cls = "tog" if pip else "tog txt"
    return (
        f"<label class='{cls}'><input type='checkbox' name='{name}' value='{_esc(value)}'"
        f"{' checked' if checked else ''}>{inner}<span>{_esc(label)}</span></label>"
    )


def _picker(
    key: str, label: str, values: list[str], *, source: str, signed: bool, placeholder: str, hint: str = ""
) -> str:
    """A picker: chosen values as chips (each carrying its own hidden input, so the form sends
    them), then a search box over ``source`` (static/cardsearch.js fills the list). Without
    scripts the box itself is named ``key`` and takes values separated by commas."""
    chips = ""
    for v in values:
        neg = signed and v.startswith("-")
        core = v[1:] if neg else v
        pol = (
            f"<button type='button' class='pol' aria-pressed='{'true' if neg else 'false'}' "
            f"title='Switch between IS and NOT'>{'NOT' if neg else 'IS'}</button>"
            if signed
            else ""
        )
        chips += (
            f"<li class='{'not' if neg else ''}' data-v='{_esc(core)}'>{pol}"
            f"<span class='nm'>{_esc(core)}</span>"
            f"<input type='hidden' name='{key}' value='{_esc(v)}'>"
            f"<button type='button' class='x' aria-label='Remove {_esc(core)}'>{icon('x')}</button></li>"
        )
    hint_html = f"<span class='hint'>{_esc(hint)}</span>" if hint else ""
    return (
        f"<div class='field cs-pick' data-picker='{key}' data-source='{source}'"
        f"{' data-signed' if signed else ''}><label class='lbl' for='p-{key}'>{_esc(label)}</label>"
        f"<ul class='chips' aria-label='{_esc(label)} chosen'>{chips}</ul>"
        f"<input id='p-{key}' type='text' name='{key}' autocomplete='off' placeholder='{_esc(placeholder)}' "
        f"role='combobox' aria-expanded='false' aria-autocomplete='list' aria-controls='o-{key}'>"
        f"<ul class='opts' id='o-{key}' role='listbox' hidden></ul>{hint_html}</div>"
    )


def _text_field(key: str, label: str, value: str, *, placeholder: str = "", hint: str = "") -> str:
    hint_html = f"<span class='hint' id='h-{key}'>{_esc(hint)}</span>" if hint else ""
    described = f" aria-describedby='h-{key}'" if hint else ""
    return (
        f"<div class='field'><label for='f-{key}'>{_esc(label)}</label>"
        f"<input id='f-{key}' type='text' name='{key}' value='{_esc(value)}' maxlength='{cq.MAX_TEXT}' "
        f"placeholder='{_esc(placeholder)}' autocomplete='off'{described}>{hint_html}</div>"
    )


def _stat_row(stat: str, cmp: str, value: str) -> str:
    return (
        "<div class='row3' data-row='stat'>"
        f"<select name='st' aria-label='Stat'>{_opts(cq.STATS, stat)}</select>"
        f"<select name='sto' aria-label='Comparison'>"
        + _opts([(k, label) for k, _op, label in cq.COMPARE], cmp)
        + "</select>"
        f"<input type='text' inputmode='decimal' name='stv' value='{_esc(value)}' aria-label='Value' "
        "placeholder='Value' maxlength='7'>"
        f"<button type='button' class='rm' aria-label='Remove this line'>{icon('x')}</button></div>"
    )


def _price_row(cur: str, cmp: str, value: str) -> str:
    return (
        "<div class='row3' data-row='price'>"
        f"<select name='pc' aria-label='Currency'>{_opts(cq.CURRENCIES, cur)}</select>"
        f"<select name='po' aria-label='Comparison'>"
        + _opts([(k, label) for k, _op, label in cq.PRICE_COMPARE], cmp)
        + "</select>"
        f"<input type='text' inputmode='decimal' name='pv' value='{_esc(value)}' aria-label='Amount' "
        "placeholder='Amount' maxlength='7'>"
        f"<button type='button' class='rm' aria-label='Remove this line'>{icon('x')}</button></div>"
    )


def _format_row(status: str, fmt: str) -> str:
    return (
        "<div class='row2' data-row='format'>"
        f"<select name='fs' aria-label='Legality'>{_opts(cq.FORMAT_STATUS, status)}</select>"
        "<select name='f' aria-label='Format'><option value=''>Any format</option>"
        f"{_opts(cq.FORMATS, fmt)}</select>"
        f"<button type='button' class='rm' aria-label='Remove this line'>{icon('x')}</button></div>"
    )


def _rows(kind: str, label: str, rows_html: list[str], add_label: str) -> str:
    return (
        f"<fieldset class='rowset' data-rows='{kind}' data-max='{cq.MAX_ROWS}'><legend>{_esc(label)}</legend>"
        f"<div class='rows'>{''.join(rows_html)}</div>"
        f"<button type='button' class='add-row' data-add-row='{kind}'>{icon('plus')} "
        f"{_esc(add_label)}</button>"
        "</fieldset>"
    )


def filters_html(f: cq.Filters, *, view: str) -> str:
    """The search form: the name box, the main filters, "More filters" with the rest of
    Scryfall's fields, and the phone panel's head and foot. Sort, direction and view sit in
    the results head (results_head_html) and belong to this form by its id."""
    colors = "".join(_toggle("c", c, cq.COLOR_NAMES[c], c in f.colors, pip=True) for c in cq.COLORS)
    identity = "".join(_toggle("id", c, cq.COLOR_NAMES[c], c in f.identity, pip=True) for c in cq.COLORS)
    rarity = "".join(_toggle("r", r, label, r in f.rarity) for r, label in cq.RARITIES)
    games = "".join(_toggle("g", g, label, g in f.games) for g, label in cq.GAMES)
    stat_rows = [_stat_row(*row) for row in f.stats] or [_stat_row("pow", "ge", "")]
    price_rows = [_price_row(*row) for row in f.prices] or [_price_row("usd", "le", "")]
    format_rows = [_format_row(*row) for row in f.formats] or [_format_row("legal", "")]
    main = (
        "<div class='cs-grid cs-main'>"
        + _picker(
            "t",
            "Type",
            f.types,
            source="types",
            signed=True,
            placeholder="Creature, Goblin, Equipment…",
        )
        + "<fieldset class='colors'><legend>Colors</legend><div class='crow'>"
        f"<div class='toggles'>{colors}</div>"
        f"<select name='cmp' aria-label='How the colors match' class='cmp'>"
        + _opts([(k, label) for k, (_op, label) in cq.COLOR_COMPARE.items()], f.color_cmp)
        + "</select></div></fieldset>"
        f"<fieldset class='ident'><legend>Commander colors</legend><div class='toggles'>{identity}</div>"
        "<span class='hint'>Cards that fit a commander of these colors.</span></fieldset>"
        "<fieldset class='mv'><legend>Mana value</legend><div class='range'>"
        f"<input type='text' inputmode='decimal' name='mv' value='{_esc(f.mv_min)}' "
        "aria-label='Mana value from' "
        "placeholder='From' maxlength='5'><span>to</span>"
        f"<input type='text' inputmode='decimal' name='mvx' value='{_esc(f.mv_max)}' "
        "aria-label='Mana value to' "
        "placeholder='To' maxlength='5'></div></fieldset>"
        f"<fieldset class='rar'><legend>Rarity</legend><div class='toggles'>{rarity}</div></fieldset>"
        + _rows("format", "Format", format_rows, "Add a format")
        + _picker("s", "Set", f.sets, source="sets", signed=False, placeholder="Set name or code")
        + "</div>"
    )
    more = (
        "<details class='cs-more'"
        + (" open" if _more_used(f) else "")
        + f"><summary>{icon('chev', cls='i chev')} More filters</summary><div class='cs-grid'>"
        "<h3 class='sub'>Card text</h3>"
        + _text_field(
            "o", "Rules text", f.oracle, placeholder="draw a card", hint="Use ~ for the card's own name."
        )
        + _text_field(
            "m", "Mana cost", f.mana, placeholder="{2}{G}{G}", hint="Symbols like {W}, {2} or {R/G}."
        )
        + _text_field("ft", "Flavor text", f.flavor)
        + _text_field("lore", "Lore Finder", f.lore, hint="Searches every part of the card for these words.")
        + "<h3 class='sub'>Stats and prices</h3>"
        + _rows("stat", "Power, toughness, loyalty and mana value", stat_rows, "Add a stat")
        + _rows("price", "Price", price_rows, "Add a price")
        + "<h3 class='sub'>Printing</h3>"
        + f"<fieldset><legend>Games</legend><input type='hidden' name='g' value='any'>"
        f"<div class='toggles'>{games}</div>"
        "<span class='hint'>Paper only unless you pick Arena or Magic Online too.</span></fieldset>"
        + _picker("b", "Block or group", f.blocks, source="blocks", signed=False, placeholder="Block name")
        + _text_field("a", "Artist", f.artist)
        + "<div class='field'><label for='f-lang'>Language</label>"
        f"<select id='f-lang' name='lang'>{_opts(cq.LANGUAGES, f.lang)}</select></div>"
        + "<h3 class='sub'>Criteria</h3>"
        + _picker(
            "is",
            "Criteria",
            f.criteria,
            source="criteria",
            signed=True,
            placeholder="Foil, Full Art, Reprint…",
        )
        + "<fieldset><legend>Matching</legend><div class='checks'>"
        f"<label><input type='checkbox' name='tany' value='1'{' checked' if f.types_any else ''}> "
        "Any of the types</label>"
        f"<label><input type='checkbox' name='isany' value='1'{' checked' if f.criteria_any else ''}> "
        "Any of the criteria</label></div>"
        "<span class='hint'>Unticked, a card must have every type and criterion picked.</span></fieldset>"
        + "<h3 class='sub'>Results</h3>"
        + "<div class='field'><label for='f-prefer'>Prefer printing</label>"
        f"<select id='f-prefer' name='prefer'>{_opts(cq.PREFER, f.prefer)}</select></div>"
        + "<fieldset><legend>Show</legend><div class='checks'>"
        f"<label><input type='checkbox' name='prints' value='1'{' checked' if f.prints else ''}> "
        "Every printing</label>"
        f"<label><input type='checkbox' name='extras' value='1'{' checked' if f.extras else ''}> "
        "Extra cards (tokens, planes, schemes)</label></div></fieldset>"
        + _text_field(
            "x",
            "Scryfall syntax",
            f.syntax,
            placeholder="o:draw t:creature",
            hint="Added to everything picked above.",
        )
        + "</div></details>"
    )
    return (
        f"<form id='cs-form' class='cs' method='get' action='/cards' role='search' data-view='{_esc(view)}'>"
        "<div class='cs-top'><div class='field'><label for='c-q'>Card name</label><span class='search'>"
        f"<input id='c-q' type='search' name='q' value='{_esc(f.name)}' maxlength='{cq.MAX_TEXT}' "
        "placeholder='Any part of a card name' autocomplete='off' enterkeyhint='search'>"
        f"<button type='submit' aria-label='Search cards'>{icon('search')}</button></span></div>"
        f"<button type='button' class='btn cs-open' aria-controls='cs-filters' aria-expanded='false'>"
        f"{icon('filter')} Filters <span class='n'></span></button>"
        f"<a class='btn decks' href='/search'>{icon('decks')} Search decks instead</a></div>"
        "<div id='cs-filters' class='cs-filters'>"
        "<div class='cs-sheet-head'><h2>Filters</h2>"
        f"<button type='button' class='close' aria-label='Close filters'>{icon('x')}</button></div>"
        + main
        + more
        + "<div class='cs-actions'><button type='button' class='cs-clear'>Clear filters</button>"
        "<noscript><button type='submit' class='btn-primary'>Search</button></noscript></div>"
        "<div class='cs-sheet-foot'><button type='button' class='cs-clear'>Clear</button>"
        "<button type='button' class='btn-primary cs-show'>Show cards</button></div>"
        "</div></form>"
    )


def _more_used(f: cq.Filters) -> bool:
    """Whether a field under More filters is in use, so the section opens with the page."""
    return bool(
        f.oracle
        or f.mana
        or f.flavor
        or f.lore
        or f.stats
        or f.prices
        or f.games != ["paper"]
        or f.blocks
        or f.artist
        or f.lang
        or f.criteria
        or f.types_any
        or f.criteria_any
        or f.prefer
        or f.prints
        or f.extras
        or f.syntax
    )


def results_head_html(f: cq.Filters, *, view: str, total: int | None, shown: bool) -> str:
    count = ""
    if shown and total is not None:
        count = f"{total:,} card{'' if total == 1 else 's'}"
    return (
        "<div class='cs-head'>"
        f"<h2 id='cs-count' aria-live='polite'>{_esc(count) if count else 'Cards'}</h2>"
        "<div class='sort'><label for='f-order'>Sort by</label>"
        f"<select id='f-order' name='order' form='cs-form'>{_opts(cq.ORDERS, f.order)}</select>"
        f"<select name='dir' form='cs-form' aria-label='Direction'>{_opts(cq.DIRECTIONS, f.dir)}</select>"
        # View and Card size move as one group, so the size control never leaves the View row
        "<span class='views'><span class='seg' role='radiogroup' aria-label='View'>"
        + "".join(
            f"<label title='{label}'><input type='radio' name='view' value='{v}' form='cs-form'"
            f"{' checked' if v == view else ''}>{icon(v)}<span>{label}</span></label>"
            for v, label in (("grid", "Grid"), ("list", "List"))
        )
        + "</span>"
        # card size (0.7.29): shown with the Grid view only (cs-head[data-view], cardsearch.js)
        + card_size_control().replace("class='field cardsize'", "class='cardsize'")
        + "</span></div></div>"
    )


def _price(card: dict[str, Any]) -> str:
    prices = card.get("prices") or {}
    for key, fmt in (("usd", "${}"), ("usd_foil", "${} foil"), ("usd_etched", "${} etched"), ("eur", "€{}")):
        if prices.get(key):
            return fmt.format(prices[key])
    return ""


def _stats(card: dict[str, Any]) -> str:
    if card.get("power") or card.get("toughness"):
        return f"{card.get('power', '')}/{card.get('toughness', '')}"
    if card.get("loyalty"):
        return f"Loyalty {card['loyalty']}"
    return ""


def _pic_attrs(card: dict[str, Any]) -> str:
    """The data attributes the card viewer (static/cardview.js) reads from a picture button."""
    name = str(card.get("name") or "")
    img = card.get("image_normal") or card.get("image_small") or ""
    set_code = str(card.get("set") or "").upper()
    printing = f"{set_code} {card.get('collector_number') or ''}".strip()
    legal = "commander" if card.get("legal_commander") == "legal" else ""
    return (
        f" data-card='{_esc(name)}'"
        + (f" data-img='{_esc(img)}'" if img else "")
        + f" data-set='{_esc(printing)}'"
        f" data-type='{_esc(card.get('type_line') or '')}'"
        f" data-mana='{_esc(card.get('mana_cost') or '')}'"
        + (f" data-rarity='{_esc(card['rarity'])}'" if card.get("rarity") else "")
        + (f" data-legal='{legal}'" if legal else "")
        + (f" data-scry='{_esc(card['scryfall_uri'])}'" if card.get("scryfall_uri") else "")
    )


def _add_button(card: dict[str, Any], *, can_add: bool, reason: str, printing: bool) -> str:
    name = str(card.get("name") or "")
    pin = ""
    if printing and card.get("set") and card.get("collector_number"):
        pin = f" data-set-code='{_esc(card['set'])}' data-number='{_esc(card['collector_number'])}'"
    return (
        f"<button type='button' class='btn add' data-add='{_esc(name)}'{pin}"
        + ("" if can_add else f" disabled title='{_esc(reason)}'")
        + f" aria-label='Add {_esc(name)} to a deck'>{icon('plus')}<span>Add to deck</span></button>"
    )


HINT_HTML = (
    "<p class='ui-hint cs-hint' id='cs-hint'>Watch a card with its <b>Watch</b> button, or right-click it "
    "(press and hold on a phone) for more.</p>"
)


def watch_button(card: dict[str, Any], *, watched: frozenset[str], printing: bool = False) -> str:
    """The Watch button on a result: "Watch", or a check and "Watching" when the member
    already watches the card. A printing search watches the shown printing."""
    name = str(card.get("name") or "")
    pin = ""
    if printing and card.get("set") and card.get("collector_number"):
        pin = f" data-set-code='{_esc(card['set'])}' data-number='{_esc(card['collector_number'])}'"
    if _norm(name) in watched:
        return (
            f"<button type='button' class='wbtn on' data-watch='{_esc(name)}'{pin} aria-pressed='true' "
            f"aria-label='{_esc(name)} is on your watchlist' title='On your watchlist'>"
            f"{icon('check')}<span>Watching</span></button>"
        )
    return (
        f"<button type='button' class='wbtn' data-watch='{_esc(name)}'{pin} aria-pressed='false' "
        f"aria-label='Watch {_esc(name)}' title='Watch {_esc(name)}'>"
        f"{icon('bookmark')}<span>Watch</span></button>"
    )


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def watched_names(rows: list[dict[str, Any]]) -> frozenset[str]:
    """The names a member watches (any printing), as the Watch buttons compare them."""
    return frozenset(_norm(str(r.get("name") or "")) for r in rows)


def card_tile_html(
    card: dict[str, Any],
    *,
    can_add: bool,
    reason: str,
    printing: bool = False,
    watched: frozenset[str] | None = None,
) -> str:
    """One result as a tile: the picture (a button that opens the card viewer) with the Watch
    button on its corner when watchlists are on, name with mana pips, type line, the printing and
    its price when the search is about printings, Add to deck."""
    name = str(card.get("name") or "")
    img = card.get("image_normal") or card.get("image_small") or ""
    body = (
        f"<img src='{_esc(img)}' alt='' loading='lazy' decoding='async'>"
        if img
        else f"<span class='ph'>{_esc(name)}</span>"
    )
    extra = ""
    if printing:
        where = f"{str(card.get('set_name') or card.get('set') or '')} #{card.get('collector_number') or ''}"
        price = _price(card)
        extra = f"<span class='pr'>{_esc(where)}{' · ' + _esc(price) if price else ''}</span>"
    pic = (
        f"<button type='button' class='pic'{_pic_attrs(card)} aria-label='{_esc(name)}: show the card'>"
        f"{body}</button>"
    )
    if watched is not None:
        pic = f"<span class='picwrap'>{pic}{watch_button(card, watched=watched, printing=printing)}</span>"
    return (
        f"<li class='cc'>{pic}<div class='meta'><span class='nm'><span>{_esc(name)}</span>"
        f"{mana_html(str(card.get('mana_cost') or ''))}</span>"
        f"<span class='ty'>{_esc(card.get('type_line') or '')}</span>{extra}</div>"
        + _add_button(card, can_add=can_add, reason=reason, printing=printing)
        + "</li>"
    )


def card_row_html(
    card: dict[str, Any],
    *,
    can_add: bool,
    reason: str,
    printing: bool = False,
    watched: frozenset[str] | None = None,
) -> str:
    """One result as a list row: a small picture, name and cost, type, set, rarity, price and
    power/toughness, the Watch button as an icon (watchlists on) and Add to deck. Under 900px the
    columns fold under the name."""
    name = str(card.get("name") or "")
    img = card.get("image_small") or card.get("image_normal") or ""
    body = (
        f"<img src='{_esc(img)}' alt='' loading='lazy' decoding='async'>"
        if img
        else f"<span class='ph'>{_esc(name[:1])}</span>"
    )
    where = f"{str(card.get('set') or '').upper()} {card.get('collector_number') or ''}".strip()
    rarity = RARITY_NAMES.get(str(card.get("rarity") or "")[:1], str(card.get("rarity") or "").title())
    stats = _stats(card)
    price = _price(card)
    return (
        f"<li><button type='button' class='pic'{_pic_attrs(card)} aria-label='{_esc(name)}: show the card'>"
        f"{body}</button>"
        f"<span class='nmc'><button type='button' class='nmb' data-open>{_esc(name)}</button>"
        f"{mana_html(str(card.get('mana_cost') or ''))}</span>"
        "<span class='info'>"
        f"<span class='ty'>{_esc(card.get('type_line') or '')}{(' · ' + _esc(stats)) if stats else ''}</span>"
        f"<span class='set num' title='{_esc(card.get('set_name') or '')}'>{_esc(where)}</span>"
        f"<span class='rar'>{_esc(rarity)}</span>"
        f"<span class='price num'>{_esc(price) if price else '<span class=muted>–</span>'}</span>"
        "</span>"
        + (
            ""
            if watched is None
            else "<span class='acts'>"
            + watch_button(card, watched=watched, printing=printing).replace(
                "class='wbtn", "class='wbtn icon", 1
            )
        )
        + _add_button(card, can_add=can_add, reason=reason, printing=printing)
        + ("" if watched is None else "</span>")
        + "</li>"
    )


LIST_HEAD = (
    "<li class='hd' aria-hidden='true'><span></span><span>Card</span><span class='info'><span>Type</span>"
    "<span>Set</span><span>Rarity</span><span>Price</span></span><span></span></li>"
)


def results_items_html(
    cards: list[dict[str, Any]],
    *,
    view: str,
    can_add: bool,
    reason: str,
    printing: bool,
    watched: frozenset[str] | None = None,
) -> str:
    """The results' rows (tiles or list rows) without their list, so a next page appends.
    ``watched``: the member's watched names, or None when watchlists are off (no Watch button)."""
    one = card_row_html if view == "list" else card_tile_html
    return "".join(one(c, can_add=can_add, reason=reason, printing=printing, watched=watched) for c in cards)


def results_list_html(
    cards: list[dict[str, Any]],
    *,
    view: str,
    can_add: bool,
    reason: str,
    printing: bool,
    linked: bool,
    watched: frozenset[str] | None = None,
) -> str:
    items = results_items_html(
        cards, view=view, can_add=can_add, reason=reason, printing=printing, watched=watched
    )
    cls = "cardlist" if view == "list" else "cardgrid"
    head = LIST_HEAD if view == "list" else ""
    return (
        f"<ul class='{cls}' id='cardgrid' data-view='{view}'{' data-linked' if linked else ''}"
        f"{' data-printing' if printing else ''}>{head}{items}</ul>"
    )
