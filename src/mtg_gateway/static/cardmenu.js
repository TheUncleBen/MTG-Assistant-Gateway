/* The card menu and the Watch button, shared by Card search (static/cardsearch.js), the deck
   editor's add box and the top bar's "Search cards and decks" suggestions (static/suggest.js).

   The menu: right-click, press and hold, the ContextMenu key or Shift+F10 on a card opens Open
   card, Watch this card (or "On your watchlist", which opens it), Art variants… (the shared
   printing picker, static/printpick.js, in its browse mode), Add to deck… where the page can add
   to any deck, and Open on Scryfall. A floating menu where the pointer was; a bottom sheet over a
   dimmed page on a phone. Watch goes through /watchlist/api/watch; the toast's Undo stops that one
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
    x: "<path d='M6 6l12 12M18 6L6 18'/>",
    external: "<path d='M14 4h6v6M20 4l-9 9'/><path d='M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5'/>",
    bookmark: "<path d='M6 3h12v18l-6-4-6 4z'/>",
    check: "<path d='M5 12l4 4L19 6'/>",
    eye: "<path d='M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z'/><circle cx='12' cy='12' r='3'/>",
    image: "<path d='M4 5h16v14H4z'/><circle cx='9' cy='10' r='1.5'/><path d='M4 17l5-5 4 4 3-3 4 4'/>"
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
  function post(path, body) {
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () { return { ok: false, status: r.status, message: "The gateway answered oddly; nothing was changed." }; });
    });
  }
  var norm = function (n) { return String(n || "").toLowerCase().replace(/\s+/g, " ").trim(); };
  function enabled() { return document.body.hasAttribute("data-watchlist"); }

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
  var menu = null, menuBack = null, scrim = null;
  function close(focusBack) {
    if (!menu) return;
    menu.remove();
    if (scrim) scrim.remove();
    menu = null; scrim = null;
    if (focusBack && menuBack && menuBack.isConnected) menuBack.focus();
    menuBack = null;
  }
  function menuItem(text, ic, onClick, focusBack) {
    var b = el("button", null, "");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.appendChild(icon(ic));
    b.appendChild(document.createTextNode(text));
    b.addEventListener("click", function (e) { e.preventDefault(); close(!!focusBack); onClick(); });
    return b;
  }
  function menuLink(text, ic, href, ext) {
    var a = el("a", null, "");
    a.setAttribute("role", "menuitem");
    a.href = href;
    if (ext) { a.target = "_blank"; a.rel = "noreferrer noopener"; }
    a.appendChild(icon(ic));
    a.appendChild(document.createTextNode(text));
    return a;
  }
  /* s: {name, onOpen(), watch: {watched, onWatch(), onArt() or null} or null (watchlists off),
     onAdd() or null, addDisabled (why it can't add, or ""), scry, focusBack (an element)} */
  function open(s, x, y) {
    close(false);
    var name = s.name || "";
    var m = el("div", "menu ctxmenu cs-menu");
    m.setAttribute("role", "menu");
    m.setAttribute("aria-label", "Card: " + name);
    m.appendChild(el("div", "head", name));
    m.appendChild(menuItem("Open card", "eye", s.onOpen));
    if (s.watch) {
      if (s.watch.watched) m.appendChild(menuLink("On your watchlist", "check", "/watchlist?q=" + enc(name)));
      else m.appendChild(menuItem("Watch this card", "bookmark", s.watch.onWatch, true));
      if (s.watch.onArt && window.MtgPrintPick) m.appendChild(menuItem("Art variants…", "image", s.watch.onArt));
    }
    if (s.onAdd) {
      var add = menuItem("Add to deck…", "plus", s.onAdd);
      if (s.addDisabled) { add.disabled = true; add.title = s.addDisabled; }
      m.appendChild(add);
    }
    m.appendChild(el("div", "sep"));
    var scry = s.scry || "https://scryfall.com/search?q=" + enc("!\"" + name + "\"");
    m.appendChild(menuLink("Open on Scryfall", "external", scry, true));
    m.addEventListener("keydown", function (e) {
      var items = $$("[role=menuitem]:not([disabled])", m);
      var i = items.indexOf(document.activeElement);
      if (e.key === "Escape") { e.preventDefault(); close(true); }
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
    m.style.visibility = "";
    menu = m;
    menuBack = s.focusBack || null;
    var first = m.querySelector("[role=menuitem]:not([disabled])");
    if (first) first.focus();
  }
  document.addEventListener("pointerdown", function (e) { if (menu && !menu.contains(e.target) && e.target !== scrim) close(false); }, true);
  window.addEventListener("resize", function () { close(false); });

  /* The ways in, on `root`: right-click, a press and hold on a touch screen (Android sends
     contextmenu itself; iOS does not), the ContextMenu key or Shift+F10. o: {itemOf(target) -> the
     card's element or null, keyItem(event) -> the same for a key (default itemOf(event.target)),
     skip (a selector a hold must not start on), open(item, x, y)}. */
  function bind(root, o) {
    // As static/deck.js: a finger or a pen (an S Pen press and hold too) holds; the hold is off
    // once the pointer has slid 8 px from where it started, or lifts, or the browser cancels it.
    // The tap that ends a hold which opened the menu is swallowed for a moment only, so nothing
    // sticks when no tap follows (a later Enter on the card, or a tap elsewhere, works as ever).
    var hold = null, opened = false, swallow = 0, startX = 0, startY = 0;
    root.addEventListener("click", function (e) {
      if (!swallow) return;
      if (menu && menu.contains(e.target)) return;  // a tap in the menu itself
      e.preventDefault();
      e.stopPropagation();
    }, true);
    root.addEventListener("pointerdown", function (e) {
      opened = false;
      if (e.pointerType === "mouse") return;
      var it = o.itemOf(e.target);
      if (!it || (o.skip && e.target.closest(o.skip))) return;
      var x = e.clientX, y = e.clientY;
      startX = x; startY = y;
      clearTimeout(hold);
      hold = setTimeout(function () { hold = null; opened = true; o.open(it, x, y); }, 550);
    });
    ["pointerup", "pointercancel", "pointermove"].forEach(function (t) {
      root.addEventListener(t, function (e) {
        if (t === "pointerup" && opened) {
          opened = false;
          clearTimeout(swallow);
          swallow = setTimeout(function () { swallow = 0; }, 400);
        }
        if (t === "pointercancel") opened = false;
        if (hold === null) return;
        if (t !== "pointermove" || Math.abs(e.clientX - startX) > 8 || Math.abs(e.clientY - startY) > 8) {
          clearTimeout(hold); hold = null;
        }
      });
    });
    root.addEventListener("contextmenu", function (e) {
      var it = o.itemOf(e.target);
      if (!it) return;
      e.preventDefault();
      if (menu) return;  // the hold already opened it
      clearTimeout(hold); hold = null;
      o.open(it, e.clientX, e.clientY);
    });
    root.addEventListener("keydown", function (e) {
      if (!(e.key === "ContextMenu" || (e.shiftKey && e.key === "F10"))) return;
      var it = o.keyItem ? o.keyItem(e) : o.itemOf(e.target);
      if (!it) return;
      e.preventDefault();
      var r = it.getBoundingClientRect();
      o.open(it, r.left + Math.min(r.width / 2, 40), r.top + Math.min(r.height / 2, 40));
    });
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
    drawButton: drawButton, listLink: listLink, watch: watch, artVariants: artVariants,
    open: open, close: close, isOpen: function () { return !!menu; }, bind: bind
  };
})();
