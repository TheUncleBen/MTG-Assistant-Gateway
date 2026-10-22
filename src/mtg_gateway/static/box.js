/* The box (dialog) every pop-up on the gateway's pages shares: window.MtgBox. Loaded on every page
   before static/cardmenu.js (which puts its menus on top of it) and the pages' own scripts.

   One look everywhere (theme.BOX_CSS): a rounded card with a soft shadow, centred over a dimmed
   page on a wide screen, a sheet from the bottom on a phone; a header with the title and a 44 px
   × close; a calm open and close (none when the system asks for reduced motion); long content
   scrolls inside; Escape, the scrim, the × and any [data-cancel] close it; focus goes into the box
   and back to the opener; on a phone the Back button closes it instead of leaving the page.

   Two ways to a box:
   - MtgBox.open({title, body, actions, wide, label, returnFocus, onClose}) builds one from nodes
     (the menus' boxes: Rename, Move, Delete, Export…). Returns the box: {panel, body, acts,
     close(), lock(on), note(text, error), busy(on), setTitle(t), setBody(node), setActions(nodes),
     focusFirst()}. MtgBox.button(text, cls, onClick, {icon, cancel}) makes its buttons;
     MtgBox.confirm({title, text, ok, danger, onOk}) is the short "are you sure" box.
   - A box the page already holds (ui.box in ui.py: a hidden .ui-box with the dialog's markup)
     opens from any element with data-box-open="<its id>": a link to the old page address, so the
     button still works without scripts, or a menu item. MtgBox.openElement(idOrNode, {returnFocus})
     does the same from a script. The box moves into the scrim while open and back when closed, so
     forms inside it keep their place in the page and submit as they would have. A .ui-box drawn
     with data-open (the old address, "open this page with the box open") opens as the page loads;
     its × then points at the page without the box, and closing it puts that address in the bar. */
(function () {
  "use strict";
  if (window.MtgBox) return;
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  var PATHS = {
    x: "<path d='M6 6l12 12M18 6L6 18'/>",
    check: "<path d='M5 12l4 4L19 6'/>",
    plus: "<path d='M12 5v14M5 12h14'/>",
    trash: "<path d='M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13'/><path d='M10 11v6M14 11v6'/>"
  };
  function icon(name) {
    var other = window.MtgCardMenu && window.MtgCardMenu.icon;
    if (other && !PATHS[name]) return other(name);
    var span = el("span");
    span.innerHTML = "<svg class='i' viewBox='0 0 24 24' aria-hidden='true'>" + (PATHS[name] || "") + "</svg>";
    return span.firstChild;
  }
  var MOTION = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  function reduced() { return !!(MOTION && MOTION.matches); }
  function phone() { return window.matchMedia && window.matchMedia("(max-width: 599.98px)").matches; }
  // Where the phone's Back should close the box instead of leaving the page (as static/cardmenu.js
  // asks for its menus): a phone, a touch screen in the rail range and the Android app
  function backCloses() {
    return phone() ||
      (window.matchMedia && window.matchMedia("(max-width: 1279.98px) and ((pointer: coarse) or (hover: none))").matches) ||
      document.body.classList.contains("app");
  }
  // The menu that opened this box may have gone back over its own history entry a moment ago
  // (static/cardmenu.js); that popstate is not a Back press on the box
  function menuBackPending() {
    var M = window.MtgMenu;
    return !!(M && M.backPending && M.backPending());
  }
  function push() {
    try { history.pushState({ mtgbox: 1 }, ""); pushed = true; } catch (e) { pushed = false; }
  }
  var FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']";
  var FIRST = "input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([disabled]), textarea:not([disabled]), select:not([disabled]), input[type=radio]:checked, .acts button:not([disabled]), .acts a[href]";

  var box = null, leaving = null, pushed = false, ignorePop = false;

  // -- closing: the scrim fades and the panel slides out, then everything is put back ------------
  function finish(b) {
    if (b.done) return;
    b.done = true;
    if (leaving === b) leaving = null;
    clearTimeout(b.timer);
    if (b.adopted) {
      // the page's own box goes back where it was, hidden again
      var node = b.panel;
      node.classList.remove("in-scrim");
      if (b.home.parent) b.home.parent.insertBefore(node, b.home.next && b.home.next.parentNode === b.home.parent ? b.home.next : null);
      node.hidden = true;
      node.removeAttribute("data-open");
      node.removeAttribute("aria-busy");
      if (b.addressed && b.closeHref && b.closeHref !== "#") {
        try { history.replaceState(history.state, "", b.closeHref); } catch (e) { /* the address stays */ }
      }
    }
    b.scrim.remove();
    if (!box && !leaving) document.documentElement.classList.remove("mtgbox-open");
    if (b.onClose) b.onClose();
    if (b.back && b.back.isConnected && b.back.focus) b.back.focus({ preventScroll: true });
  }
  // a box that must stay (a delete job running inside it) refuses Esc and the scrim; the box's
  // own code closes it with force once the outcome is shown
  function closeBox(force, fromHistory) {
    if (!box || (box.locked && !force)) return;
    var b = box;
    box = null;
    if (pushed && !fromHistory) {
      // going back over the box's entry fires popstate later; that one is ours, not a Back press
      pushed = false;
      ignorePop = true;
      try { history.back(); } catch (e) { ignorePop = false; }
    }
    pushed = false;
    b.scrim.classList.remove("in");
    b.scrim.classList.add("out");
    if (reduced() || !b.scrim.isConnected) { finish(b); return; }
    leaving = b;
    b.scrim.addEventListener("transitionend", function (e) { if (e.target === b.scrim) finish(b); });
    b.timer = setTimeout(function () { finish(b); }, 320);
  }

  // -- opening: the scrim, the keys, focus --------------------------------------------------------
  function mount(b, o) {
    closeBox(true);
    if (leaving) finish(leaving);
    if (window.MtgMenu && window.MtgMenu.close) window.MtgMenu.close(false);
    var sc = b.scrim, panel = b.panel;
    sc.addEventListener("click", function (e) {
      if (e.target === sc) { closeBox(); return; }
      var c = e.target.closest ? e.target.closest("[data-cancel], .bx") : null;
      if (c && panel.contains(c)) { e.preventDefault(); closeBox(); }
    });
    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeBox(); return; }
      if (e.key !== "Tab") return;
      var f = $$(FOCUSABLE, panel).filter(function (x) { return x.offsetParent !== null && !x.closest("[hidden]"); });
      if (!f.length) { e.preventDefault(); return; }
      if (e.shiftKey && (document.activeElement === f[0] || document.activeElement === panel)) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    });
    document.documentElement.classList.add("mtgbox-open");
    document.body.appendChild(sc);
    box = b;
    // the transition starts from the drawn state: the class comes a frame later
    if (reduced()) sc.classList.add("in");
    else requestAnimationFrame(function () { sc.classList.add("in"); });
    b.focusFirst();
    if (!o.fromAddress && backCloses()) {
      // the menu's own Back still settling: the box's entry goes on once it has landed
      if (menuBackPending()) b.wantsPush = true; else push();
    }
    return b;
  }
  function methods(b, parts) {
    var panel = b.panel, h = parts.title, body = parts.body, note = parts.note, acts = parts.acts;
    b.body = body;
    b.acts = acts;
    b.locked = false;
    b.close = function () { closeBox(true); };
    b.lock = function (on) { b.locked = !!on; panel.setAttribute("data-locked", on ? "true" : "false"); };
    b.note = function (text, error) {
      note.hidden = !text;
      note.textContent = text || "";
      note.className = "notice boxnote" + (error ? " error" : " ok");
    };
    b.busy = function (on) {
      $$("button", acts).forEach(function (x) { x.disabled = !!on || x.hasAttribute("data-gate-off"); });
      panel.setAttribute("aria-busy", on ? "true" : "false");
    };
    b.setTitle = function (t) { h.textContent = t; };
    b.setBody = function (node) { body.textContent = ""; body.appendChild(node); };
    b.setActions = function (nodes) {
      acts.textContent = "";
      nodes.forEach(function (n) { if (n) acts.appendChild(n); });
    };
    b.focusFirst = function () {
      var f = $$(FIRST, panel).filter(function (x) { return x.offsetParent !== null && !x.closest("[hidden]"); })[0];
      if (!f) f = panel.querySelector(".bx");
      if (f) f.focus({ preventScroll: true });
    };
    return b;
  }
  function closeButton() {
    var x = el("button", "bx");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.title = "Close";
    x.appendChild(icon("x"));
    return x;
  }
  var titles = 0;
  /* o: {title, body (a node, or a function (box) -> node), actions (buttons; a button with
     data-cancel closes the box), wide (the export box), label, returnFocus, onClose} */
  function openBox(o) {
    o = o || {};
    var sc = el("div", "mtgbox-scrim");
    var panel = el("div", "mtgbox" + (o.wide ? " wide" : ""));
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    var head = el("div", "bh");
    var h = el("h2", null, o.title || "");
    h.id = "mtgbox-title" + (titles++ ? "-" + titles : "");
    panel.setAttribute("aria-labelledby", h.id);
    if (o.label) panel.setAttribute("aria-label", o.label);
    head.appendChild(h);
    head.appendChild(closeButton());
    panel.appendChild(head);
    var body = el("div", "body");
    panel.appendChild(body);
    var note = el("p", "notice error boxnote");
    note.setAttribute("role", "alert");
    note.hidden = true;
    panel.appendChild(note);
    var acts = el("div", "acts");
    panel.appendChild(acts);
    sc.appendChild(panel);
    var b = methods({ scrim: sc, panel: panel, back: o.returnFocus || document.activeElement, onClose: o.onClose },
      { title: h, body: body, note: note, acts: acts });
    b.setBody(typeof o.body === "function" ? o.body(b) : (o.body || el("div")));
    b.setActions(o.actions || []);
    return mount(b, o);
  }
  /* A box the page holds (ui.box): node, or its id. o: {returnFocus, onClose, fromAddress} */
  function openElement(which, o) {
    o = o || {};
    var node = typeof which === "string" ? document.getElementById(which) : which;
    if (!node) return null;
    if (box && box.panel === node) return box;
    var sc = el("div", "mtgbox-scrim");
    var home = { parent: node.parentNode, next: node.nextSibling };
    var h = node.querySelector(".bh h2") || node.querySelector("h2");
    var bx = node.querySelector(".bx");
    var closeHref = bx && bx.getAttribute ? bx.getAttribute("href") : null;
    if (!bx) {
      var head = node.querySelector(".bh");
      if (!head) { head = el("div", "bh"); if (h) head.appendChild(h); node.insertBefore(head, node.firstChild); }
      head.appendChild(closeButton());
    }
    var body = node.querySelector(".body"), acts = node.querySelector(".acts");
    if (!acts) { acts = el("div", "acts"); node.appendChild(acts); }
    var note = node.querySelector(".boxnote");
    if (!note) {
      note = el("p", "notice error boxnote");
      note.setAttribute("role", "alert");
      note.hidden = true;
      node.insertBefore(note, acts);
    }
    node.classList.add("mtgbox", "in-scrim");
    node.setAttribute("role", "dialog");
    node.setAttribute("aria-modal", "true");
    node.hidden = false;
    sc.appendChild(node);
    var b = methods({
      scrim: sc, panel: node, adopted: true, home: home, closeHref: closeHref, addressed: !!o.fromAddress,
      back: o.returnFocus || document.activeElement, onClose: o.onClose
    }, { title: h || el("h2"), body: body || node, note: note, acts: acts });
    return mount(b, o);
  }
  function boxButton(text, cls, onClick, o) {
    o = o || {};
    var b = el("button", cls || "", "");
    b.type = "button";
    if (o.icon) b.appendChild(icon(o.icon));
    b.appendChild(document.createTextNode((o.icon ? " " : "") + text));
    if (o.cancel) b.setAttribute("data-cancel", "");
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }
  /* The short "are you sure" box. o: {title, text or body (a node), ok (the button's words),
     danger (a solid red final button), icon, cancel (default "Cancel"), onOk(box) (may return a
     promise; the box waits, then closes unless it resolves to true), returnFocus, wide} */
  function confirm(o) {
    o = o || {};
    var okBtn = boxButton(o.ok || "OK", o.danger ? "danger" : "primary", function () {
      var out = o.onOk ? o.onOk(b) : null;
      if (out && typeof out.then === "function") {
        b.busy(true);
        out.then(function (keep) { if (keep !== true) b.close(); else b.busy(false); }, function () { b.busy(false); });
        return;
      }
      if (out !== true) b.close();
    }, { icon: o.icon });
    var b = openBox({
      title: o.title || "", wide: o.wide,
      body: o.body || el("p", null, o.text || ""),
      actions: [boxButton(o.cancel || "Cancel", "btn", null, { cancel: true }), okBtn],
      returnFocus: o.returnFocus, onClose: o.onClose
    });
    return b;
  }

  document.addEventListener("keydown", function (e) { if (box && e.key === "Escape" && !box.panel.contains(e.target)) closeBox(); });
  window.addEventListener("popstate", function () {
    if (ignorePop) { ignorePop = false; return; }
    if (menuBackPending()) {
      if (box && box.wantsPush) { box.wantsPush = false; push(); }
      return;
    }
    if (box && pushed) { pushed = false; closeBox(true, true); }
  });
  // the way in without a script of the page's own: a link or button with data-box-open
  document.addEventListener("click", function (e) {
    var opener = e.target && e.target.closest ? e.target.closest("[data-box-open]") : null;
    if (!opener || e.defaultPrevented) return;
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;  // a new tab: the page
    var node = document.getElementById(opener.getAttribute("data-box-open"));
    if (!node) return;  // not on this page: the link goes to the old address
    e.preventDefault();
    openElement(node, { returnFocus: opener });
  });
  // an old address: the page comes with the box drawn open
  function openAddressed() {
    var first = document.querySelector(".ui-box[data-open]");
    if (first) openElement(first, { fromAddress: true, returnFocus: document.querySelector("[data-box-open='" + first.id + "']") });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", openAddressed);
  else openAddressed();

  window.MtgBox = {
    open: openBox, openElement: openElement, confirm: confirm, button: boxButton,
    close: function () { closeBox(true); }, isOpen: function () { return !!box; },
    current: function () { return box; }
  };
})();
