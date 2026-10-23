/* The menu and box layer every page shares, plus the card menu and the Watch button.

   window.MtgMenu: one floating menu (a bottom sheet over a dimmed page on a phone) for cards,
   decks, folders and list rows: `open({head, label, items, focusBack}, x, y)` with items from
   `item()`, `link()`, `sep()` and `node()`; `bind(root, {itemOf, keyItem, skip, button, open})`
   wires the ways in on `root`: right-click, a press and hold on a touch screen, the ⋮ button
   (`button`, a selector inside the item) and the ContextMenu key or Shift+F10 on a focused item.
   Escape, a press outside, the page scrolling, a resize and the phone's Back close it. A hold
   never leaves anything marked: nothing is added to the item while the finger is down, a drag the
   browser starts under a finger is refused, and only the tap that ends the hold is swallowed.
   `kebab(label)` makes a ⋮ button; `toast`, `post`, `getJSON`, `copyText` and `collect` (add one
   copy of a card to the collection) are the small shared actions.

   window.MtgBox (static/box.js, loaded before this file): the box (dialog) every pop-up shares;
   opening a box closes an open menu.

   window.MtgCardMenu: the card menu used by Card search (static/cardsearch.js), the deck editor's
   add box, the top bar's "Search cards and decks" suggestions (static/suggest.js) and the other
   pages' card rows (static/menus.js): Open card, Add to deck…, Add to collection, Watch this
   card (or "On your watchlist", which opens it) and Art variants… (the shared printing picker,
   static/printpick.js, in its browse mode) with watchlists on, Copy name, Open on Scryfall, and a
   page's own items. Watch goes through /watchlist/api/watch; the toast's Undo stops that one
   watch again through /watchlist/api/unwatch, by its id. Nothing of the watch shows with
   watchlists off (the page's body has no data-watchlist then).

   Suggestion lists: a box with data-suggest-menu gets the menu on its card rows (the top bar: the
   menu only). A box with data-suggest-watch (a JSON list of the watched names, folded) also gets an
   icon Watch button on each card row (the deck editor; its rows already add to this deck, so its
   menu has no Add to deck…). The box's own `mtgCardMenu` property may give `open(name, card)` and
   `pick(name, printing)` (Art variants… then also offers the printing for this deck).
   Loaded on every signed-in page, before the pages' own scripts. */
(function () {
  "use strict";
  if (window.MtgCardMenu) return;
  var enc = encodeURIComponent;
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  var PATHS = {
    plus: "<path d='M12 5v14M5 12h14'/>",
    minus: "<path d='M5 12h14'/>",
    x: "<path d='M6 6l12 12M18 6L6 18'/>",
    external: "<path d='M14 4h6v6M20 4l-9 9'/><path d='M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5'/>",
    bookmark: "<path d='M6 3h12v18l-6-4-6 4z'/>",
    check: "<path d='M5 12l4 4L19 6'/>",
    eye: "<path d='M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z'/><circle cx='12' cy='12' r='3'/>",
    image: "<path d='M4 5h16v14H4z'/><circle cx='9' cy='10' r='1.5'/><path d='M4 17l5-5 4 4 3-3 4 4'/>",
    more: "<circle cx='12' cy='5' r='1.5' fill='currentColor'/><circle cx='12' cy='12' r='1.5' fill='currentColor'/><circle cx='12' cy='19' r='1.5' fill='currentColor'/>",
    edit: "<path d='M4 20h4l11-11-4-4L4 16z'/><path d='M13 7l4 4'/>",
    clone: "<path d='M9 9h11v11H9z'/><path d='M4 15V4h11'/>",
    copy: "<path d='M9 9h11v11H9z'/><path d='M4 15V4h11'/>",
    play: "<circle cx='12' cy='12' r='9'/><path d='M10 8l6 4-6 4z' fill='currentColor'/>",
    stats: "<path d='M4 20V10M10 20V4M16 20v-7M22 20H2'/>",
    report: "<path d='M6 3h9l5 5v13H6z'/><path d='M14 3v6h6M9 14h6M9 17h6'/>",
    folder: "<path d='M3 6h6l2 2h10v11H3z'/>",
    download: "<path d='M12 4v11M7 10l5 5 5-5'/><path d='M4 20h16'/>",
    layers: "<path d='M12 3l9 5-9 5-9-5z'/><path d='M3 13l9 5 9-5'/>",
    settings: "<circle cx='12' cy='12' r='3'/><path d='M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1'/>",
    link: "<path d='M10 14a4 4 0 0 1 0-5.7l3-3a4 4 0 0 1 5.7 5.7l-1.5 1.5'/><path d='M14 10a4 4 0 0 1 0 5.7l-3 3a4 4 0 0 1-5.7-5.7L6.8 11.5'/>",
    trash: "<path d='M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13'/><path d='M10 11v6M14 11v6'/>",
    collection: "<path d='M4 6h12v14H4z'/><path d='M8 6V3h12v14h-3'/><path d='M7 11h6M7 15h6'/>",
    swap: "<path d='M4 7h13l-3-3M20 17H7l3 3'/>",
    bell: "<path d='M6 16V11a6 6 0 0 1 12 0v5l2 2H4z'/><path d='M10 21h4'/>",
    undo: "<path d='M9 14L4 9l5-5'/><path d='M4 9h10a6 6 0 0 1 0 12h-3'/>",
    history: "<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>",
    decks: "<path d='M4 7h16v13H4z'/><path d='M7 4h13v13'/>",
    proposals: "<path d='M9 11l3 3 8-8'/><path d='M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9'/>",
    hand: "<path d='M8 13V6a1.5 1.5 0 0 1 3 0v5M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v7'/><path d='M17 13v3a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-2.7L3 15a1.5 1.5 0 0 1 2.4-1.8L8 15v-2'/>",
    flag: "<path d='M5 21V4h12l-2 4 2 4H5'/>",
    lock: "<rect x='5' y='11' width='14' height='10' rx='2'/><path d='M8 11V8a4 4 0 0 1 8 0v3'/>",
    package: "<path d='M12 3l8 4.5v9L12 21l-8-4.5v-9z'/><path d='M4 7.5l8 4.5 8-4.5M12 12v9'/>",
    trophy: "<path d='M7 4h10v5a5 5 0 0 1-10 0z'/><path d='M7 6H4a3 3 0 0 0 3 3M17 6h3a3 3 0 0 1-3 3M12 14v4M8 21h8'/>",
    pdf: "<path d='M6 3h8l4 4v14H6z'/><path d='M14 3v4h4'/><path d='M9 13h6M9 17h6'/>",
    comment: "<path d='M4 5h16v11H9l-5 4z'/>",
    tag: "<path d='M3 12V4h8l9 9-8 8z'/><circle cx='7' cy='8' r='1.2' fill='currentColor'/>"
  };
  function icon(name) {
    var span = el("span");
    span.innerHTML = "<svg class='i' viewBox='0 0 24 24' aria-hidden='true'>" + (PATHS[name] || "") + "</svg>";
    return span.firstChild;
  }
  function csrf() {
    var i = document.querySelector("input[name=csrf]");
    return i ? i.value : "";
  }
  var ODD = "The gateway sent an answer this page could not read. Reload the page to see whether the change was made.";
  function post(path, body) {
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () { return { ok: false, status: r.status, message: ODD }; });
    });
  }
  function getJSON(path) {
    return fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () { return { ok: false, status: r.status, message: ODD }; });
    });
  }
  var norm = function (n) { return String(n || "").toLowerCase().replace(/\s+/g, " ").trim(); };
  function enabled() { return document.body.hasAttribute("data-watchlist"); }
  function phone() { return window.matchMedia && window.matchMedia("(max-width: 599.98px)").matches; }
  // Where the phone's Back should close a menu instead of leaving the page: a phone, a touch
  // screen in the rail range (600 to 1279.98 px, as theme.py draws the rail) and the Android
  // app at any width (static/deck.js and static/feedback.js ask the same)
  function backCloses() {
    return phone() ||
      (window.matchMedia && window.matchMedia("(max-width: 1279.98px) and ((pointer: coarse) or (hover: none))").matches) ||
      document.body.classList.contains("app");
  }

  // -- toast: what was done, with its links; errors stay until dismissed -------------------------
  var toastEl = null, toastTimer = null;
  function dismissToast() { if (toastEl) toastEl.remove(); toastEl = null; clearTimeout(toastTimer); }
  function toast(text, opts) {
    opts = opts || {};
    dismissToast();
    toastEl = el("div", "toast cards-toast" + (opts.error ? " error" : ""));
    toastEl.setAttribute("role", opts.error ? "alert" : "status");
    if (opts.ok) { var ok = icon("check"); ok.setAttribute("class", "i ok"); toastEl.appendChild(ok); }
    toastEl.appendChild(el("span", "msg", text));
    (opts.actions || []).forEach(function (a) { toastEl.appendChild(a); });
    var x = el("button", "close icon-only", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", dismissToast);
    toastEl.appendChild(x);
    document.body.appendChild(toastEl);
    if (!opts.error) toastTimer = setTimeout(dismissToast, opts.ms || 9000);
  }
  // Copies text: execCommand first, inside the press (the app's WebView may refuse the async
  // clipboard), then the async API. Resolves true when it was copied.
  function copyText(text) {
    var temp = el("textarea");
    temp.value = text;
    temp.setAttribute("readonly", "");
    temp.style.position = "fixed";
    temp.style.top = "-1000px";
    document.body.appendChild(temp);
    temp.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    temp.remove();
    if (ok) return Promise.resolve(true);
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
    return Promise.resolve(false);
  }
  function copyAndSay(text, said) {
    return copyText(text).then(function (ok) {
      if (ok) toast(said, { ok: true, ms: 4000 });
      else toast("Copying was refused by the browser.", { error: true });
    });
  }
  // "Add to collection": one copy, as "I own this card" on the deck page adds it
  function collect(name) {
    return post("/collection/api/add", { items: [{ name: name, quantity: 1 }], source: "manual" }).then(function (d) {
      if (d.ok && d.added && d.added.length) {
        var a = el("a", null, "Open the collection");
        a.href = "/collection?q=" + enc(name);
        toast(name + " added to your collection.", { ok: true, actions: [a] });
        return true;
      }
      toast(d.message || "The card could not be added to your collection.", { error: true });
      return false;
    }, function () { toast("No connection; the card was not added.", { error: true }); return false; });
  }

  // -- Watch -------------------------------------------------------------------------------------
  // The Watch button's look for a card: "Watch", or a check and "Watching" when it is watched.
  function drawButton(b, name, on) {
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.setAttribute("aria-label", on ? name + " is on your watchlist" : "Watch " + name);
    b.title = on ? "On your watchlist" : "Watch " + name;
    b.textContent = "";
    b.appendChild(icon(on ? "check" : "bookmark"));
    b.appendChild(el("span", null, on ? "Watching" : "Watch"));
  }
  function listLink(name) {
    var a = el("a", null, "Open the watchlist");
    a.href = "/watchlist?q=" + enc(name);
    return a;
  }
  /* what: {name, set_code, collector_number, finish}; o: {btn (busy while it goes), was (watched
     before), onState(on) (the page redraws its buttons)}. Resolves with the watch, rejects on a
     refusal or no connection (the toast said why). */
  function watch(what, o) {
    o = o || {};
    var btn = o.btn;
    if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
    var done = function () { if (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); } };
    var state = function (on) { if (o.onState) o.onState(on); };
    var body = { name: what.name, finish: what.finish || "nonfoil" };
    if (what.set_code && what.collector_number) { body.set_code = what.set_code; body.collector_number = what.collector_number; }
    return post("/watchlist/api/watch", body).then(function (d) {
      done();
      if (!d.ok) {
        toast(d.message || "The card could not be watched.", { error: true });
        throw new Error("refused");
      }
      var w = d.watch || {};
      var name = w.name || what.name;
      state(true);
      if (!w.created) { toast(name + " is on your watchlist.", { ok: true, actions: [listLink(name)] }); return w; }
      var undo = el("button", "btn-plain", "Undo");
      undo.type = "button";
      undo.addEventListener("click", function () {
        undo.disabled = true;
        post("/watchlist/api/unwatch", { watch_id: w.watch_id }).then(function (u) {
          if (!u.ok && u.status !== 404) { toast(u.message || "The watch could not be undone.", { error: true }); return; }
          if (!o.was) state(false);
          toast(name + " is no longer on your watchlist.");
        }).catch(function () { toast("No connection; the card is still watched.", { error: true }); });
      });
      toast(name + " added to your watchlist.", { ok: true, actions: [listLink(name), undo] });
      return w;
    }, function (err) {
      done();
      toast("No connection; the card was not watched.", { error: true });
      throw err;
    });
  }
  // Art variants…: every printing in the picker's browse mode, each with "Watch this printing";
  // with onPick (a deck) also the page's own pick. o: {returnFocus, onState(on), was() (watched
  // before), onPick(printing), pickLabel}.
  function artVariants(name, o) {
    var Pick = window.MtgPrintPick;
    if (!Pick) return;
    o = o || {};
    var opts = {
      name: name,
      current: null,
      finish: "nonfoil",
      returnFocus: o.returnFocus || null,
      onWatch: function (p, finish) {
        var what = { name: p.name || name, set_code: p.set, collector_number: p.collector_number, finish: finish };
        return watch(what, { onState: o.onState, was: o.was ? o.was() : false });
      }
    };
    if (o.onPick) { opts.onPick = o.onPick; if (o.pickLabel) opts.pickLabel = o.pickLabel; }
    Pick.open(opts);
  }

  // -- the menu ----------------------------------------------------------------------------------
  var menu = null, menuBack = null, scrim = null, menuPushed = false, ignorePop = false, menuScroll = [0, 0];
  function close(focusBack, fromHistory) {
    if (!menu) return;
    menu.remove();
    if (scrim) scrim.remove();
    menu = null; scrim = null;
    if (menuPushed && !fromHistory) {
      // going back over the menu's entry fires popstate later; that one is ours, not a Back press
      menuPushed = false;
      ignorePop = true;
      try { history.back(); } catch (e) { ignorePop = false; }
    }
    menuPushed = false;
    if (focusBack && menuBack && menuBack.isConnected) menuBack.focus();
    menuBack = null;
  }
  /* An item: a button that closes the menu and runs onClick. o: {danger, disabled, title (why it
     is disabled), keepOpen (the menu stays, as a quantity step), cls}. */
  function item(text, ic, onClick, o) {
    o = o || {};
    var b = el("button", o.cls || "", "");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    if (o.danger) b.classList.add("danger");
    if (ic) b.appendChild(icon(ic));
    b.appendChild(document.createTextNode(text));
    if (o.disabled) { b.disabled = true; if (o.title) b.title = o.title; }
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (!o.keepOpen) close(!!o.focusBack);
      if (onClick) onClick(e);
    });
    return b;
  }
  function link(text, ic, href, o) {
    o = o || {};
    var a = el("a", o.cls || "", "");
    a.setAttribute("role", "menuitem");
    a.href = href;
    if (o.danger) a.classList.add("danger");
    if (o.ext) { a.target = "_blank"; a.rel = "noreferrer noopener"; }
    if (ic) a.appendChild(icon(ic));
    a.appendChild(document.createTextNode(text));
    a.addEventListener("click", function () { close(false); });
    return a;
  }
  function sep() { return el("div", "sep"); }
  /* s: {head (the item's name), label (for readers; default "Menu: head"), items (nodes), cls,
     focusBack (an element)}; x, y: where it opens (the pointer, or the ⋮). */
  function openMenu(s, x, y) {
    close(false);
    var m = el("div", "menu ctxmenu cs-menu" + (s.cls ? " " + s.cls : ""));
    m.setAttribute("role", "menu");
    m.setAttribute("aria-label", s.label || ("Menu: " + (s.head || "")));
    if (s.head) m.appendChild(el("div", "head", s.head));
    (s.items || []).forEach(function (n) { if (n) m.appendChild(n); });
    m.addEventListener("keydown", function (e) {
      var items = $$("[role=menuitem]:not([disabled])", m);
      var i = items.indexOf(document.activeElement);
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === "ArrowDown") { e.preventDefault(); (items[i + 1] || items[0]).focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); (items[i - 1] || items[items.length - 1]).focus(); }
      else if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
      else if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); }
      else if (e.key === "Tab") { close(false); }
    });
    m.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    scrim = el("div", "cs-scrim");
    scrim.addEventListener("click", function () { close(true); });
    document.body.appendChild(scrim);
    m.style.visibility = "hidden";
    document.body.appendChild(m);
    var pad = 8, w = m.offsetWidth, h = m.offsetHeight;
    m.style.left = Math.max(pad, Math.min(x, window.innerWidth - w - pad)) + "px";
    m.style.top = Math.max(pad, Math.min(y, window.innerHeight - h - pad)) + "px";
    m.style.maxHeight = (window.innerHeight - parseFloat(m.style.top) - pad) + "px";
    m.style.visibility = "";
    menu = m;
    menuBack = s.focusBack || null;
    menuScroll = [window.scrollX, window.scrollY];
    var first = m.querySelector("[role=menuitem]:not([disabled])");
    if (first) first.focus({ preventScroll: true });
    if (backCloses()) { try { history.pushState({ mtgmenu: 1 }, ""); menuPushed = true; } catch (e) { menuPushed = false; } }
    return m;
  }
  document.addEventListener("pointerdown", function (e) { if (menu && !menu.contains(e.target) && e.target !== scrim) close(false); }, true);
  document.addEventListener("keydown", function (e) { if (menu && e.key === "Escape" && !menu.contains(e.target)) close(true); });
  window.addEventListener("resize", function () { close(false); });
  // the page scrolling away closes the menu (a list inside it may scroll)
  window.addEventListener("scroll", function (e) {
    if (!menu || (e.target instanceof Element && menu.contains(e.target))) return;
    if (Math.abs(window.scrollX - menuScroll[0]) > 4 || Math.abs(window.scrollY - menuScroll[1]) > 4) close(false);
  }, { capture: true, passive: true });
  window.addEventListener("popstate", function () {
    if (ignorePop) { ignorePop = false; return; }
    if (menu && menuPushed) { menuPushed = false; close(true, true); }
  });

  // A ⋮ button for a tile or row; label: "Menu for NAME"
  function kebab(label) {
    var b = el("button", "kebab");
    b.type = "button";
    b.setAttribute("aria-label", label);
    b.setAttribute("aria-haspopup", "menu");
    b.title = "Menu";
    b.appendChild(icon("more"));
    return b;
  }

  /* The ways in, on `root`: right-click, a press and hold on a touch screen (Android sends
     contextmenu itself; iOS does not), the ⋮ button and the ContextMenu key or Shift+F10.
     o: {itemOf(target) -> the item's element or null, keyItem(event) -> the same for a key
     (default itemOf(event.target)), skip (a selector a hold must not start on), button (the
     selector of the item's ⋮), open(item, x, y, how)}. */
  function bind(root, o) {
    // As static/deck.js: a finger or a pen (an S Pen press and hold too) holds; the hold is off
    // once the pointer has slid 8 px from where it started, or lifts, or the browser cancels it.
    // The tap that ends a hold which opened the menu is swallowed for a moment only, so nothing
    // sticks when no tap follows (a later Enter on the item, or a tap elsewhere, works as ever).
    var hold = null, opened = false, swallow = 0, lift = false, startX = 0, startY = 0, touchPress = false;
    root.addEventListener("click", function (e) {
      var b = o.button && e.target.closest ? e.target.closest(o.button) : null;
      if (b && o.itemOf(b)) {
        e.preventDefault();
        e.stopPropagation();
        var r = b.getBoundingClientRect();
        o.open(o.itemOf(b), r.left, r.bottom, "button");
        return;
      }
      if (!swallow) return;
      // a tap in the menu itself acts, except the lift of the finger that held: the menu opened
      // under it (a sheet rising over the tile on a phone), so that lift would pick whatever item
      // sits there
      if (menu && menu.contains(e.target) && !lift) return;
      lift = false;
      e.preventDefault();
      e.stopPropagation();
    }, true);
    root.addEventListener("pointerdown", function (e) {
      opened = false;
      // a new press is a real tap: the lift of the hold is over even when no click followed it
      lift = false;
      touchPress = e.pointerType !== "mouse";
      if (!touchPress) return;
      var it = o.itemOf(e.target);
      if (!it || (o.skip && e.target.closest(o.skip)) || (o.button && e.target.closest(o.button))) return;
      var x = e.clientX, y = e.clientY;
      startX = x; startY = y;
      clearTimeout(hold);
      hold = setTimeout(function () {
        hold = null; opened = true;
        if (navigator.vibrate) { try { navigator.vibrate(20); } catch (err) { /* no vibration */ } }
        o.open(it, x, y, "hold");
      }, 450);
    });
    ["pointerup", "pointercancel", "pointermove"].forEach(function (t) {
      root.addEventListener(t, function (e) {
        if (t === "pointerup" && opened) {
          opened = false;
          lift = true;
          clearTimeout(swallow);
          swallow = setTimeout(function () { swallow = 0; lift = false; }, 400);
        }
        if (t === "pointercancel") opened = false;
        if (hold === null) return;
        if (t !== "pointermove" || Math.abs(e.clientX - startX) > 8 || Math.abs(e.clientY - startY) > 8) {
          clearTimeout(hold); hold = null;
        }
      });
    });
    // a link tile is draggable by itself: never under a finger, where the drag the browser starts
    // from a hold would leave the tile marked and the next tap refused
    root.addEventListener("dragstart", function (e) {
      if (touchPress && o.itemOf(e.target)) e.preventDefault();
    });
    root.addEventListener("contextmenu", function (e) {
      var it = o.itemOf(e.target);
      if (!it) return;
      e.preventDefault();
      if (menu) return;  // the hold already opened it
      clearTimeout(hold); hold = null;
      o.open(it, e.clientX, e.clientY, "context");
    });
    root.addEventListener("keydown", function (e) {
      if (!(e.key === "ContextMenu" || (e.shiftKey && e.key === "F10"))) return;
      var it = o.keyItem ? o.keyItem(e) : o.itemOf(e.target);
      if (!it) return;
      e.preventDefault();
      var r = it.getBoundingClientRect();
      o.open(it, r.left + Math.min(r.width / 2, 40), r.top + Math.min(r.height / 2, 40), "key");
    });
  }

  window.MtgMenu = {
    open: openMenu, close: close, isOpen: function () { return !!menu; }, bind: bind,
    item: item, link: link, sep: sep, kebab: kebab, icon: icon, el: el,
    toast: toast, dismissToast: dismissToast, post: post, getJSON: getJSON, csrf: csrf,
    copyText: copyText, copyAndSay: copyAndSay, collect: collect, enabled: enabled, phone: phone, norm: norm,
    // true while the menu's own history.back() has not landed yet (static/box.js asks)
    backPending: function () { return ignorePop; }
  };

  // -- the card menu -----------------------------------------------------------------------------
  /* s: {name, onOpen(), watch: {watched, onWatch(), onArt() or null} or null (watchlists off),
     onAdd() or null, addHref (Add to deck… as a link, where the page has no add box), addDisabled
     (why it can't add, or ""), collect (true: Add to collection), copyName (default true), scry,
     before (nodes above Copy name), after (nodes under Open on Scryfall), focusBack (an element)} */
  function open(s, x, y) {
    var name = s.name || "";
    var items = [item("Open card", "eye", s.onOpen)];
    if (s.onAdd) {
      items.push(item("Add to deck…", "plus", s.onAdd, { disabled: !!s.addDisabled, title: s.addDisabled }));
    } else if (s.addHref) {
      items.push(link("Add to deck…", "plus", s.addHref));
    }
    if (s.collect) items.push(item("Add to collection", "collection", function () { collect(name); }));
    if (s.watch) {
      if (s.watch.watched) items.push(link("On your watchlist", "check", "/watchlist?q=" + enc(name)));
      else items.push(item("Watch this card", "bookmark", s.watch.onWatch, { focusBack: true }));
      if (s.watch.onArt && window.MtgPrintPick) items.push(item("Art variants…", "image", s.watch.onArt));
    }
    (s.before || []).forEach(function (n) { items.push(n); });
    if (s.copyName !== false) items.push(item("Copy name", "copy", function () { copyAndSay(name, "Name copied."); }));
    items.push(sep());
    var scry = s.scry || "https://scryfall.com/search?q=" + enc("!\"" + name + "\"");
    items.push(link("Open on Scryfall", "external", scry, { ext: true }));
    (s.after || []).forEach(function (n) { items.push(n); });
    return openMenu({ head: name, label: "Card: " + name, items: items, focusBack: s.focusBack }, x, y);
  }

  // -- suggestion lists (static/suggest.js) ------------------------------------------------------
  function boxOf(node) {
    var wrap = node && node.closest ? node.closest(".suggest") : null;
    var input = wrap ? wrap.querySelector("input[data-suggest-menu], input[data-suggest-watch]") : null;
    return input;
  }
  var watchedSets = typeof WeakMap === "function" ? new WeakMap() : null;
  function watchedOf(input) {
    if (!watchedSets || !input.hasAttribute("data-suggest-watch")) return null;
    var s = watchedSets.get(input);
    if (!s) {
      s = {};
      try { (JSON.parse(input.getAttribute("data-suggest-watch") || "[]") || []).forEach(function (n) { s[norm(n)] = true; }); }
      catch (e) { /* none known: every row says Watch */ }
      watchedSets.set(input, s);
    }
    return s;
  }
  function setWatched(input, name, on) {
    var s = watchedOf(input);
    if (s) { if (on) s[norm(name)] = true; else delete s[norm(name)]; }
    var list = input.closest(".suggest") && input.closest(".suggest").querySelector(".suggest-list");
    if (list) $$(".wbtn[data-watch]", list).forEach(function (b) {
      if (norm(b.getAttribute("data-watch")) === norm(name)) drawButton(b, b.getAttribute("data-watch"), on);
    });
  }
  function cardRow(target) {
    var li = target && target.closest ? target.closest(".suggest-list li[role=option]") : null;
    return li && !li.hasAttribute("data-href") && li.getAttribute("data-name") && boxOf(li) ? li : null;
  }
  // the card's details suggest.js read for its rich rows (picture, mana cost, type line)
  function detailsOf(li) {
    var img = li.querySelector("img.thumb"), ty = li.querySelector(".ty");
    return { name: li.getAttribute("data-name"), img: img ? img.src : "", type: ty ? ty.textContent : "" };
  }
  function openCard(input, li) {
    var name = li.getAttribute("data-name");
    var own = input.mtgCardMenu;
    if (own && own.open) { own.open(name); return; }
    var View = window.MtgCardView;
    if (View && View.open) {
      var base = detailsOf(li);
      base.loading = !!View.text;
      View.open(base, []);
      if (View.text) View.text(name).then(function (t) {
        var card = {};
        Object.keys(base).forEach(function (k) { card[k] = base[k]; });
        if (t) Object.keys(t).forEach(function (k) { if (t[k] || k === "faces") card[k] = t[k]; });
        delete card.loading;
        View.update(card, []);
      });
      return;
    }
    // no viewer on this page: the card's own search, as picking the row does
    location.href = (input.getAttribute("data-suggest-site") || "/cards?q=") + enc(name);
  }
  function rowMenu(li, x, y) {
    var input = boxOf(li);
    if (!input) return;
    var name = li.getAttribute("data-name");
    var watched = watchedOf(input);
    var onState = function (on) { setWatched(input, name, on); };
    var own = input.mtgCardMenu || {};
    open({
      name: name,
      onOpen: function () { openCard(input, li); },
      watch: enabled() ? {
        watched: !!(watched && watched[norm(name)]),
        onWatch: function () { watch({ name: name }, { was: false, onState: onState }).catch(function () {}); },
        onArt: function () {
          artVariants(name, {
            returnFocus: input, onState: onState,
            was: function () { var s = watchedOf(input); return !!(s && s[norm(name)]); },
            onPick: own.pick ? function (p) { own.pick(name, { set_code: p.set, collector_number: String(p.collector_number) }); } : null,
            pickLabel: "Add this printing"
          });
        }
      } : null,
      onAdd: null,
      copyName: false,  // a suggestion row: the name is the typed text already
      focusBack: input
    }, x, y);
  }
  bind(document, {
    itemOf: cardRow,
    // from the box itself: the highlighted card row
    keyItem: function (e) {
      var t = e.target;
      if (!t || !t.matches || !t.matches("input[data-suggest-menu], input[data-suggest-watch]")) return cardRow(t);
      var id = t.getAttribute("aria-activedescendant");
      return id ? cardRow(document.getElementById(id)) : null;
    },
    skip: ".wbtn",
    open: rowMenu
  });
  // the deck editor's rows: an icon Watch button on each card row (with watchlists on)
  document.addEventListener("suggest:rows", function (e) {
    var input = e.target;
    if (!enabled() || !input.hasAttribute || !input.hasAttribute("data-suggest-watch")) return;
    var watched = watchedOf(input) || {};
    $$("li[role=option]", input.closest(".suggest")).forEach(function (li) {
      if (li.hasAttribute("data-href") || li.querySelector(".wbtn")) return;
      var name = li.getAttribute("data-name");
      var b = el("button", "wbtn icon");
      b.type = "button";
      b.tabIndex = -1;  // in a list option: the box keeps the focus; the menu has the same Watch
      b.setAttribute("data-watch", name);
      drawButton(b, name, !!watched[norm(name)]);
      b.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
      b.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();  // not a pick of the row
        if (b.disabled) return;
        if (b.classList.contains("on")) { toast(name + " is on your watchlist.", { ok: true, actions: [listLink(name)] }); return; }
        watch({ name: name }, { btn: b, was: false, onState: function (on) { setWatched(input, name, on); } }).catch(function () {});
      });
      li.classList.add("watchable");
      li.appendChild(b);
    });
  });

  window.MtgCardMenu = {
    enabled: enabled, icon: icon, toast: toast, dismissToast: dismissToast, norm: norm,
    drawButton: drawButton, listLink: listLink, watch: watch, artVariants: artVariants, collect: collect,
    open: open, close: close, isOpen: function () { return !!menu; }, bind: bind
  };
})();
