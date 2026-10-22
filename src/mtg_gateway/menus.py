"""The styles of the menus and boxes every signed-in page shares (static/menus.js on top of
static/cardmenu.js): the ⋮ button on a deck tile, a folder tile, a Home deck and a table row, and the
quantity row in the Collection's card menu. Appended to theme.CSS; the menu panel itself is
theme.CARD_MENU_CSS and the boxes are theme.BOX_CSS (static/box.js)."""

MENUS_CSS = """
/* ⋮: a round 44 px button (the touch target, with a mouse too) */
.kebab,button.kebab{display:inline-flex;align-items:center;justify-content:center;width:44px;
  height:44px;min-height:0;min-width:0;padding:0;margin:0;border:0;border-radius:50%;
  background:transparent;color:var(--text-muted);box-shadow:none;cursor:pointer}
.kebab:hover,.kebab:focus-visible,button.kebab:hover{background:var(--surface-2);color:var(--text)}
.kebab svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round}
/* a deck tile: the ⋮ at the end of the title line, outside the tile's link; a press and hold on
   the tile opens the same menu, so nothing is selected or called out under the finger */
ul.decklist .deck{position:relative;-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
ul.decklist .deck > .kebab{position:absolute;right:.4rem;top:calc(175px + .5rem + (75px - var(--ctl)) / 2)}
ul.decklist .deck .info{padding-right:calc(var(--ctl) + .75rem)}
ul.decklist.list .deck > .kebab{top:50%;transform:translateY(-50%)}
ul.decklist.list .deck a{padding-right:calc(var(--ctl) + .5rem)}
ul.decklist.list .deck .info{padding-right:.75rem}
/* a folder tile: the ⋮ before the lock, at the row's end */
ul.folderlist .folder{position:relative;-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
ul.folderlist .folder a{padding-right:calc(var(--ctl) + .85rem)}
ul.folderlist .folder > .kebab{position:absolute;right:.4rem;top:50%;transform:translateY(-50%)}
/* Home's recent decks: the ⋮ over the picture's corner */
.home .recent .rd{position:relative;display:block;min-width:0}
.home .recent .rd > .kebab{position:absolute;right:.25rem;top:.25rem;color:#fff;background:rgba(0,0,0,.55)}
.home .recent .rd > .kebab:hover,.home .recent .rd > .kebab:focus-visible{background:rgba(0,0,0,.75)}
/* Card search: the ⋮ at the end of the name line of a tile or a row; the deck editor's rows and
   the Collection's keep their own buttons, the menu comes from a right-click or a hold */
.cardgrid .nm .kebab.inline,.cardlist .nmc .kebab.inline{width:34px;height:34px;margin-left:auto}
@media (pointer:coarse){.cardgrid .nm .kebab.inline,.cardlist .nmc .kebab.inline{width:44px;height:44px}}
/* a table row's ⋮ sits with its open action; in a proposal row, after the title's words */
.ui-table .tr .act + .kebab,.ui-table .tr .kebab{flex:none;align-self:center}
.ui-table .title .kebab.inline{vertical-align:middle;margin-left:.25rem;width:34px;height:34px}
@media (pointer:coarse){.ui-table .title .kebab.inline{width:44px;height:44px}}
/* the My decks heading: + New folder and New deck at its end (New deck the one orange button) */
.ui-head.phead{align-items:center;justify-content:space-between;margin:0 0 12px}
.ui-head.phead .acts{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.ui-head.phead .acts .btn,.ui-head.phead .acts button{margin:0}
/* the Collection menu's quantity row: one fewer, the count, one more; the menu stays open */
.ctxmenu .mqty{display:flex;align-items:center;gap:.25rem;padding:.15rem .5rem .15rem 1rem;min-height:38px}
.ctxmenu .mqty .lbl{flex:1;color:var(--text-muted)}
.ctxmenu .mqty .n{min-width:2ch;text-align:center;font-variant-numeric:tabular-nums}
.ctxmenu .mqty button.step{width:var(--ctl);min-height:var(--ctl);padding:0;justify-content:center;
  border:1px solid var(--control-border);border-radius:var(--radius);font-weight:700}
"""
