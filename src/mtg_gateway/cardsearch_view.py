"""What the card search page (/cards) draws: the filters (every field of Scryfall's advanced search,
cardquery.py), the results as picture tiles or as a list, and the page's own styles.

One renderer serves the first page load and the live updates (static/cardsearch.js asks
/cards/api/search for the next results and puts the HTML it gets in place), so the two can't drift.
The page works without scripts too: the filters are a plain GET form and each picker a text box.
"""

from __future__ import annotations

from typing import Any

from . import cardquery as cq
from . import searchpanel as sp
from .deckpage import mana_html
from .theme import esc as _esc
from .theme import icon

VIEWS = ("grid", "list")

CARDS_CSS = """
/* the panel is the shared search panel (theme.SEARCH_PANEL_CSS); the form inside it lays out as
   the panel's own rows */
.cs-panel > form.cs{display:contents}
.cs-panel > .readonly{margin:0}
.cs-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,15.5rem),1fr));gap:1rem 1.25rem;
  align-items:start}
.cs-grid > .field,.cs-grid > fieldset{margin:0;min-width:0}
/* the rows of three controls need two columns' room where there are two */
/* the whole row, not two columns: at a large text size the grid has one column, and a span of two
   would add an empty second one beside it */
@media (min-width:600px){.cs-grid > .rowset{grid-column:1 / -1}}
.cs .row3 .msel-btn,.cs .row2 .msel-btn{min-width:0;width:100%}
.cs fieldset{border:0;padding:0;margin:0;min-width:0}
.cs legend,.cs .lbl{display:block;font-weight:700;margin:0 0 .35rem;padding:0}
.cs .hint{display:block;color:var(--text-muted);font-size:.86rem;margin:.3rem 0 0}
.cs .wide{grid-column:1/-1}
/* the Games toggles under More filters: a checkbox under each word, the whole pill pressable */
.cs .toggles{display:flex;flex-wrap:wrap;gap:.4rem}
.cs .tog.txt{padding:.25rem .8rem}
.cs .inline{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.cs .inline select,.cs .inline input,.cs .inline .msel-btn{width:auto;flex:1 1 7rem;min-width:0}
.cs .rows{display:flex;flex-direction:column;gap:.5rem}
.cs .row3{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr) minmax(0,.8fr) auto;gap:.4rem;
  align-items:center}
.cs .row2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.6fr) auto;gap:.4rem;align-items:center}
.cs .rm{margin:0;width:var(--ctl);min-height:var(--ctl);height:var(--ctl);padding:0;display:inline-flex;
  align-items:center;justify-content:center}
.cs .add-row{margin:.5rem 0 0;align-self:flex-start}
/* the Format group: legality, the format and Add a format in one row (more rows under it) */
.spanel .fmtrow{--gw:30em}
.spanel .fmtrow .body{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start}
.spanel .fmtrow .rows{flex:1 1 280px;min-width:0}
.spanel .fmtrow .add-row{flex:none;margin:0;display:inline-flex;align-items:center;gap:.3rem;
  height:var(--ctl)}
.spanel .fmtrow .row2{grid-template-columns:minmax(0,1fr) minmax(0,1.6fr) auto}
.spanel .fgrid > .cs-pick{--gw:220px}
.spanel .fgrid > .fg.mv{--gw:12em}
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
/* More filters: opened from the results bar (its own summary shows without scripts and on phones) */
details.cs-more{margin:0;border-top:1px solid var(--border-soft);padding-top:.25rem}
details.cs-more > summary{cursor:pointer;font-weight:700;min-height:2.75rem;display:flex;
  align-items:center;gap:.4rem;
  list-style:none;width:max-content;max-width:100%}
details.cs-more > summary::-webkit-details-marker{display:none}
details.cs-more > summary .chev{transition:transform .15s;transform:rotate(-90deg)}
details.cs-more[open] > summary .chev{transform:none}
details.cs-more > .cs-grid{margin:.5rem 0 0}
@media (min-width:600px){ details.cs-more.jsx{border-top:0;padding-top:0}
  details.cs-more.jsx > summary{display:none} details.cs-more.jsx:not([open]){display:none} }
.cs h3.sub{grid-column:1/-1;margin:.75rem 0 0;font-size:1rem;color:var(--menu-head);border-bottom:1px solid
  var(--border-soft);padding-bottom:.3rem}
.cs-results[aria-busy=true]{opacity:.55;transition:opacity .2s}
.cs-status{margin:0 0 .9rem}
.cs-status:empty{display:none}
.cs-warn{margin:0 0 .9rem}
.cs-more-wrap{display:flex;flex-direction:column;align-items:center;gap:.5rem;margin:.5rem 0 1.5rem}
.cs-more-wrap[hidden]{display:none}
.cs-more-wrap .muted{font-size:.9rem}
.cs-more-wrap button{margin:0;min-width:12rem}
/* the tile grid: as many tiles of the chosen size as fit (M 168 px at least; S and L in the theme) */
.cardgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,168px),1fr));gap:1rem .9rem;
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
/* the name and its ⋮ share the first row, the mana cost the second (a row every tile keeps, so
   a land's type line sits level with its neighbours'); a long name stops at two lines, and a
   short one keeps two lines' room. The mana cost keeps one line and the type line two, so every
   tile is as tall as every other: the cards Load more adds to a row never move its Add to deck
   buttons */
.cardgrid .nm{display:grid;grid-template-columns:minmax(0,1fr) auto;
  grid-template-rows:auto 1.4em;align-items:start;gap:.2rem .4rem;font-weight:700;
  line-height:1.25;overflow-wrap:anywhere;min-width:0}
.cardgrid .nm > span:first-child{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden;grid-column:1;grid-row:1;min-width:0;min-height:2.5em}
.cardgrid .nm .mana{grid-column:1 / -1;grid-row:2;min-width:0;flex-wrap:nowrap;overflow:hidden;
  max-width:100%;height:1.4em}
.cardgrid .nm .kebab{grid-column:2;grid-row:1}
.cardgrid .ty,.cardgrid .pr{font-size:.86rem;color:var(--text-muted);overflow-wrap:anywhere}
.cardgrid .ty{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
  line-height:1.3;height:2.6em}
.cardgrid .pr{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
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
/* phones: the stat rows keep three boxes a row; the Games toggles form a grid */
@media (max-width:599.98px){
  .cs .row3{grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,5rem) auto}
  .cs .toggles{display:grid;grid-template-columns:repeat(auto-fill,minmax(7.6rem,1fr))}
}
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


def _toggle(name: str, value: str, label: str, checked: bool) -> str:
    """A word toggle (the Games under More filters); the colours use the shared colour buttons."""
    return (
        f"<label class='tog txt'><input type='checkbox' name='{name}' value='{_esc(value)}'"
        f"{' checked' if checked else ''}><span>{_esc(label)}</span></label>"
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


def _rows(kind: str, label: str, rows_html: list[str], add_label: str, *, group: bool = False) -> str:
    """Rows that can be added to (stats, prices, formats). ``group``: as one filter group of the
    shared panel, the Add button beside the rows (the Format row)."""
    cls = "fg fmtrow rowset" if group else "rowset"
    legend = "l" if group else ""
    return (
        f"<fieldset class='{cls}' data-rows='{kind}' data-max='{cq.MAX_ROWS}'>"
        f"<legend class='{legend}'>{_esc(label)}</legend>"
        + ("<div class='body'>" if group else "")
        + f"<div class='rows'>{''.join(rows_html)}</div>"
        f"<button type='button' class='add-row' data-add-row='{kind}'>{icon('plus')} "
        f"<span class='tx'>{_esc(add_label)}</span></button>" + ("</div>" if group else "") + "</fieldset>"
    )


def filters_html(f: cq.Filters, *, view: str, total: int | None = None) -> str:
    """The search form, from the shared panel (searchpanel.py): the name row, the main filter
    groups, "More filters" with the rest of Scryfall's fields, and the results bar at the foot.
    Phones open the filters as a sheet. ``total``: the count the bar shows (None: no search yet)."""
    games = "".join(_toggle("g", g, label, g in f.games) for g, label in cq.GAMES)
    stat_rows = [_stat_row(*row) for row in f.stats] or [_stat_row("pow", "ge", "")]
    price_rows = [_price_row(*row) for row in f.prices] or [_price_row("usd", "le", "")]
    format_rows = [_format_row(*row) for row in f.formats] or [_format_row("legal", "")]
    main = sp.filter_grid(
        [
            sp.colours_group(
                label="Colors",
                name="c",
                chosen=f.colors,
                mode_name="cmp",
                mode_options=[(k, label) for k, (_op, label) in cq.COLOR_COMPARE.items()],
                mode_value=f.color_cmp,
            ),
            _picker(
                "t",
                "Type",
                f.types,
                source="types",
                signed=True,
                placeholder="Creature, Goblin, Equipment…",
            ),
            sp.group(
                "Mana value",
                "<div class='range'>"
                f"<input type='text' inputmode='decimal' name='mv' value='{_esc(f.mv_min)}' "
                "aria-label='Mana value from' placeholder='From' maxlength='5'><span>to</span>"
                f"<input type='text' inputmode='decimal' name='mvx' value='{_esc(f.mv_max)}' "
                "aria-label='Mana value to' placeholder='To' maxlength='5'></div>",
                cls="mv",
            ),
            sp.colours_group(
                label="Commander colors",
                name="id",
                chosen=f.identity,
                mode_name="idm",
                mode_options=[(k, label) for k, (_op, label) in cq.IDENTITY_COMPARE.items()],
                mode_value=f.identity_cmp,
                hint="cards that fit a commander of these colors",
            ),
            _rows("format", "Format", format_rows, "Add a format", group=True),
            sp.pick_group(
                label="Rarity", name="r", options=cq.RARITIES, chosen=f.rarity, none_text="Any rarity"
            ),
            _picker("s", "Set", f.sets, source="sets", signed=False, placeholder="Set name or code"),
        ]
    )
    more = (
        "<details class='cs-more' id='cs-more-filters'"
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
    sheet = sp.filters_sheet(
        main + more,
        show_html="<button type='button' class='btn-primary sp-show'>Show cards</button>",
        clear_html="<button type='button' class='sp-clear'>Clear filters</button>",
    )
    return (
        f"<form id='cs-form' class='cs' method='get' action='/cards' role='search' data-view='{_esc(view)}' "
        "data-count-own>"
        + sp.name_row(
            label="Card name",
            name="q",
            value=f.name,
            placeholder="Any part of a card name",
            input_id="c-q",
            search_label="Search cards",
            cross_html=sp.cross_link("Search decks instead", "/search", "decks"),
            attrs=f" maxlength='{cq.MAX_TEXT}'",
        )
        + sheet
        + results_bar_html(f, view=view, total=total)
        + "<noscript><button type='submit' class='btn-primary'>Search</button></noscript></form>"
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


def count_text(total: int | None, *, shown: bool) -> str:
    """The count in the results bar: "N cards", or "Cards" before a search."""
    if shown and total is not None:
        return f"{total:,} card{'' if total == 1 else 's'}"
    return "Cards"


def results_bar_html(f: cq.Filters, *, view: str, total: int | None) -> str:
    """The results controls at the panel's foot: More filters, the count, Sort by with its
    direction, Grid or List, the card size (Grid only) and Clear filters."""
    more = (
        "<button type='button' class='btn more' data-more-filters aria-controls='cs-more-filters' "
        f"aria-expanded='false' hidden>{icon('chev')} More filters</button>"
    )
    return sp.results_bar(
        count_html=sp.count_html(count_text(total, shown=f.chosen()), count_id="cs-count"),
        sort_html=sp.sort_group(
            order_name="order",
            orders=cq.ORDERS,
            order=f.order,
            order_id="f-order",
            dir_name="dir",
            dirs=cq.DIRECTIONS,
            direction=f.dir,
        ),
        view_html=sp.view_group(view),
        clear_html="<button type='button' class='btn sp-clear clr'>Clear filters</button>",
        more_html=more,
        cls="cards",
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
        f"<span class='ty' title='{_esc(card.get('type_line') or '')}'>{_esc(card.get('type_line') or '')}"
        f"</span>{extra}</div>"
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
