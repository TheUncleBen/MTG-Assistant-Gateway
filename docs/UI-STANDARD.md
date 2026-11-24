# UI standard: one look for every page

Every page of the gateway (website, Android app, phones, the Fold, tablets, computers) is built
from the same pieces. A page keeps what it is for: its own sections, controls and data. What it
does not get is a look of its own for a piece the whole site shares. A page that needs a piece the
standard does not have adds it to the standard (here and in `theme.py`), not to the page.

Two tools hold the standard:

- `python -m tests.ui_cohesion OUT` draws every page and records each piece's measured look
  (size, weight, corners, edges, fill, padding). Each role below lists how many looks it may
  have. `tests/test_ui_cohesion_browser.py` fails when a page brings a new one.
- `python -m tests.ui_clicks OUT` presses every button, link, chip, tab and menu item on every
  page, from a fresh page each time, and reports any that do nothing, throw a script error or
  lead to a failed request.

The UI matrix (`tests/ui_matrix.py`) still checks each page for overflow, contrast, 44 px touch
targets, 200% text, menus and stranded buttons.

## Type (root 14 px)

| Role | Element | Look | Where |
|---|---|---|---|
| Page title | `h1` inside `ui.page_head` (`.ui-head`) | 2rem (28 px), 700, no top margin | every page, once |
| Purpose line | `p.ui-sub` right under the title, before the head's actions on a phone (`ui.page_head` writes it so) | .93rem, muted, two lines at most on a phone | every page that needs one |
| Section heading | `h2` | 1.2rem (17 px; 6vw on a narrow phone at large text), 700, 1.5rem above (none when first), .5rem below | every section; a list's heading with its count (`ui.list_head`) |
| Group label | `h2.ui-group` | .8rem, 700, upper case, `--menu-head` | over an inset grouped list (Account, Binder, Install) |
| Sub-heading | `h3` | 1.05rem, 700 | inside a section |

Exceptions, on purpose: the deck page's title sits on the deck's art in white (the deck banner);
pages whose switch names them (Assistant, Decks) keep the `h1` for screen readers only;
the deck list's category headings are part of the list.

## Buttons

| Role | Class | Look |
|---|---|---|
| Main action (one per screen or panel) | `button.primary`, `.btn.btn-primary` | orange fill, dark words |
| Everything else | `button`, `.btn`, `.btn-plain` (the same look) | `--surface` fill, 1 px `--control-border`, text colour |
| Delete, Remove, Reject (before the final step) | `.btn-outline-danger` | red outline and words |
| Final "are you sure" step | `button.danger`, `.btn-danger` | solid red |
| Quiet (inside a toolbar or a row) | `button.secondary`, `.btn-ghost` | no fill and no edge until hovered |
| Icon only | `button.icon-only` | square, the control height, with an `aria-label` |
| Row or inline action | `.ui-cap` (`ui.cap_link`) | grey capsule: `--surface-2` fill, no edge, .86rem 600, 36 px drawn inside a 44 px tap area |
| Show more in place | `summary.fold-btn`, `.disc`, `button.disc` | the same capsule with a chevron that turns when open (How to update, Plain-text diff, More filters) |
| A tile row's summary on a phone | `ui.widgets` (`.glance-lbl`) | the one other disclosure: a full-width bar (panel edge, 44 px, an icon, the summary's words, a chevron at its end) that stands for the folded row of tiles ("At a glance", "15 waiting · 15 applied"); only below 600 px, only for a row of tiles |
| The way back a level | `ui.back_link` (`a.ui-cap.ui-back`) | the capsule with the back arrow, on a line of its own at the page's top left, above the title |

Which one: an action that stands for a page or a panel (Open, Review, Details, Open deck, Open
report, Test a pasted list) is a standard button, in a tile, a toolbar or a list row alike; on a
phone a list row's main action is its 44 px chevron. A capsule is only for a small action inside a
row, a list line or a sentence (Why, Undo, Show oldest first, Group by deck, Review all 3). The
same verb on the same page is the same piece. Every page that has a way back a level uses
`ui.back_link`; a trail of several levels (Guide, Forum, Lists, folders) is capsules with `›`
between them.

All of them: 1rem (14 px), 700, 1rem padding at the sides, `--radius` (5 px) corners, the
control height (`--ctl`, 44 px). A narrow row wraps whole buttons onto the next line
(`.even-rows`), it never squeezes a button's padding or font.

## Chips and tags

| Role | Class | Look |
|---|---|---|
| Filter or choice chip (pressable) | `.ui-chip` | pill, 44 px tall, .9rem 700, `--control-border`; chosen: orange tint and orange edge |
| A row of filter chips | `.ui-chips` (`ui.chips`) | from 600 px the chips wrap, every one whole; on a phone one row that scrolls sideways, its end faded |
| Status label | `.ui-lbl.<colour>` (`ui.label`) | pill, 24 px, .82rem 700, a colour and an icon |
| Tag (a deck's or a card's tag, a count) | `.ui-tag` (`ui.tag`) | pill, 24 px, .82rem 600, `--surface-2` fill, no edge; never cut where its `title` cannot be read: under 600 px a tag longer than its row wraps at its spaces (whole words, still a pill, 12 px corners), from 600 px it is one line ending in "…"; it always carries its whole text in `title`, a script-built one too |

## Panels and lists

| Role | Class | Look |
|---|---|---|
| Panel | `.panel`, `.card`, `.ui-card` | `--surface` fill, 1 px `--card-border`, `--radius-panel` (3 px), 1rem padding |
| Inset grouped list | `.ygrp .rows` (account_hub.HUB_CSS: Account, Binder, Install) | `--group-bg`, `--radius-lg`, rows 44 px with a divider |
| Notice | `ui.notice` | tinted strip with a coloured left edge |
| Tile / widget | `ui.widget` | as §3a of the dashboard rule; its label wraps beside its icon |
| Empty state | `ui.empty(title, text, action=None)` (`.ui-empty`) | a panel of its own, 1rem padding: one bold line (what is missing), one muted line (what to do), at most one standard button. It stands in for the whole list or table: no column header over nothing, and never inside another panel |
| A list's heading | `ui.list_head` (`.list-head`) | the `h2` with its count, a note at the row's end (the order, with its capsule), both centred on one 36 px row |

No panel sits inside another panel's border.

## Fields

Every text box, number box, select and pick list (`.msel-btn`): the control height, `--radius`,
1 px `--control-border`, `--surface` fill, 1rem text. A text area: the same edge, fill and corners.

A select's or a pick list's chosen value is one line, ending in "…" where the box is narrower; a
row of filters (`ui.toolbar`) wraps whole controls onto the next row instead of squeezing one.

A search field is `ui.search_field` (`.ui-search`) on every page: the magnifier inside the field at
its left, no button beside it. Where the field's form submits (Decks, Search, Cards, Precons, the
Binder's collection, the Guide), the magnifier is that form's submit button inside the field and
Enter submits; a list's filter has the plain magnifier and filters as you type.

## Switches

One look on every page: a grey track (`--seg-track`) with the chosen part as a raised pill
(`--seg-on`). Two sizes of it:

- The place switch and tabs (`.hubseg`): centred, or full width on a phone, never left hanging
  to one side. Parts are equal in width, on a phone too; where they need more than one row, a
  shorter last row sits in the middle (seven parts: four over three), and no word is broken.
- The compact switch (`.ui-seg`; also `.sizeseg`, the search panel's `.segc`, `.budget`: Grid /
  List, S / M / L, a chart's range, a package's budget): the control height (44 px). It keeps its
  own width and ends its toolbar row on the Fold and a computer; nothing stretches it.

## Page column

| Kind | Width | Pages |
|---|---|---|
| Wide (lists, dashboards, grids) | up to 1200 px | Home, Decks, Binder, Proposals, History, Reports, Watchlist, Search, Cards, Precons, the deck page, Admin |
| Narrow (forms, settings, grouped lists, reading) | 44rem (616 px) | Account and its pages, the Binder page where it is one column (a phone, an open Fold), Binder settings, Alerts, Card models, Scan, deck settings, Install |

Both are centred, with the page's gutter at the sides on a phone. Every wide page has the same
left edge: the title, the toolbar and the lists of `render(wide=True)` and of
`render(main_class="ui-wide")` sit in the one 1200 px column. Only a grid of cards (`ul.cardgrid`,
`ul.decklist.grid`, the deck's `.deckview`) grows past it, from a 1600 px screen, evenly to the
screen's edges and at most 2300 px; its head and toolbar stay in the column.

## Every button does something

A control that is shown can be pressed and does what it says. A control that cannot act right
now is either hidden or disabled with the reason beside it. The click sweep holds this.
