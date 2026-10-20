"""The styles of the menus and boxes every signed-in page shares (static/menus.js on top of
static/cardmenu.js): the ⋮ button on a deck tile, a folder tile, a Home deck and a table row; the
boxes (a centred card on a wide screen, a sheet from the bottom on a phone) and the quantity row
in the Collection's card menu. Appended to theme.CSS; the menu panel itself is theme.CARD_MENU_CSS."""

MENUS_CSS = """
/* ⋮: a round button the size of a control (39 px with a mouse, 44 px on a touch screen) */
.kebab,button.kebab{display:inline-flex;align-items:center;justify-content:center;width:var(--ctl);
  height:var(--ctl);min-height:0;min-width:0;padding:0;margin:0;border:0;border-radius:50%;
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

/* boxes: a centred card over a dimmed page; a sheet from the bottom on a phone. What a box hides
   (a note, a field) stays hidden whatever display rule the page gives it. */
.mtgbox [hidden]{display:none!important}
.mtgbox-scrim{position:fixed;inset:0;z-index:70;background:var(--scrim);display:flex;align-items:center;
  justify-content:center;padding:1rem}
.mtgbox{display:flex;flex-direction:column;width:min(100%,30rem);max-height:calc(100vh - 2rem);
  padding:1rem 1.25rem 1.25rem;background:var(--surface);color:var(--text);border:1px solid var(--border);
  border-radius:var(--radius-lg);box-shadow:var(--shadow)}
.mtgbox.wide{width:min(100%,56rem)}
.mtgbox h2{margin:0 0 .75rem;font-size:1.25rem;padding-right:0}
.mtgbox .body{flex:1 1 auto;min-height:0;overflow:auto;overscroll-behavior:contain}
.mtgbox .body > .stack{display:flex;flex-direction:column;gap:.75rem}
.mtgbox .body p{margin:0}
.mtgbox .body ul{margin:0;padding-left:1.25rem}
.mtgbox .body ul li{margin:.2rem 0}
.mtgbox .body .field{margin:0}
.mtgbox .body textarea{width:100%;min-height:16rem;resize:vertical;font-family:ui-monospace,SFMono-Regular,
  Menlo,Consolas,monospace;font-size:.9rem;line-height:1.4}
.mtgbox .body .files{display:flex;flex-wrap:wrap;gap:.5rem;margin:0}
.mtgbox .body .files .btn{margin:0}
.mtgbox .body .segc{display:flex;flex-wrap:wrap;gap:.25rem;margin:0 0 .5rem}
.mtgbox .body .segc [role=tab]{min-height:var(--ctl);padding:0 .9rem;font-weight:400;
  border-color:var(--border);background:var(--surface-2);color:var(--text)}
.mtgbox .body .segc [role=tab][aria-selected=true]{background:var(--orange);border-color:var(--orange);
  color:var(--on-orange);font-weight:700}
.mtgbox .tree{display:flex;flex-direction:column;gap:.1rem;max-height:50vh;overflow:auto}
.mtgbox .tree label{display:flex;align-items:center;gap:.5rem;min-height:var(--ctl);padding:0 .5rem;
  border-radius:var(--radius);cursor:pointer;overflow-wrap:anywhere}
.mtgbox .tree label:hover{background:var(--surface-2)}
.mtgbox .tree input{margin:0;flex:none}
.mtgbox .tree svg{width:18px;height:18px;flex:none;color:var(--orange);fill:none;stroke:currentColor;
  stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.mtgbox .boxnote{margin:.75rem 0 0}
.mtgbox .acts{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:.5rem;margin-top:1rem}
.mtgbox .acts:empty{display:none}
.mtgbox .acts button{margin:0}
.mtgbox .acts button svg.i{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2}
.mtgbox .loading,.mtgbox .progress{display:flex;align-items:center;gap:.6rem}
.mtgbox .loading::before,.mtgbox .progress::before{content:'';width:1rem;height:1rem;flex:none;
  border:2px solid currentColor;border-right-color:transparent;border-radius:50%;
  animation:mtgbox-spin .8s linear infinite}
@keyframes mtgbox-spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.mtgbox .loading::before,.mtgbox .progress::before{animation:none}}
@media (max-width:599.98px){
  .mtgbox-scrim{align-items:flex-end;padding:0}
  .mtgbox,.mtgbox.wide{width:100%;max-height:calc(100vh - var(--tab-room,0px));border-bottom:0;
    border-radius:var(--radius-sheet) var(--radius-sheet) 0 0;
    padding-bottom:calc(1.25rem + env(safe-area-inset-bottom))}
  .mtgbox.wide{height:calc(100vh - var(--tab-room,0px));border-radius:0}
  .mtgbox .acts{justify-content:stretch}
  .mtgbox .acts button{flex:1 1 auto}
}
"""
