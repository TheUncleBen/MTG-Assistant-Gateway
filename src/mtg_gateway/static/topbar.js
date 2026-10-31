/* The room kept above a focused or jumped-to element is the top bar's real height. The bar is
   sticky and is 50 px tall, but with large text and a mouse its links move to a second row
   (theme.py, the container queries on .topbar) and it grows; a fixed 60 px of scroll room would
   then leave a control that Tab or Shift+Tab reaches hidden under the bar (WCAG 2.4.11). The
   stylesheet keeps 60 px, or 9.5rem where the links wrap, when this script doesn't run. Loaded on every page.
   The room kept below works the same way: what stays at the foot of the window (the phone's tab
   bar, the waiting changes' Apply bar, the Guide's Back to top) grows with the text, so a fixed
   room hid a control under them at 200% text. The Guide's Back to top is counted even while it is
   hidden, since it shows as soon as the page scrolls. --tab-room is the phone's tab bar's height,
   which the Apply bar sits on. */
(function () {
  "use strict";
  var bar = document.querySelector(".topbar");
  if (!bar) return;
  var root = document.documentElement;
  var feet = document.querySelectorAll(".tabbar, .wbar:not(.inline), .gtop");
  function footRoom() {
    var room = 0;
    for (var i = 0; i < feet.length; i++) {
      var el = feet[i], hidden = el.classList.contains("gtop") && el.classList.contains("hide");
      if (hidden) el.classList.remove("hide");
      var style = getComputedStyle(el), box = el.getBoundingClientRect();
      if (style.display !== "none" && box.height) {
        // only what is pinned to the foot: the tab bar is a rail down the side on an open Fold
        if (style.position === "fixed" && box.top > window.innerHeight / 2)
          room = Math.max(room, window.innerHeight - box.top);
        else if (style.position === "sticky")
          room = Math.max(room, box.height + (parseFloat(style.bottom) || 0));
      }
      if (hidden) el.classList.add("hide");
    }
    return room;
  }
  var tabs = document.querySelector(".tabbar");
  // A property set on <html> restyles the whole page, so every measure is read first, and a
  // property is written only when its value changes: a write between two reads made the second
  // read restyle and lay out the whole page again (a 100-card deck page on a phone paid three
  // times per fit, and once more for the ResizeObserver's first call with nothing changed).
  var last = {};
  function put(name, value) {
    if (last[name] === value) return;
    last[name] = value;
    if (value === null) root.style.removeProperty(name);
    else root.style.setProperty(name, value);
  }
  function fit() {
    var high = Math.ceil(bar.getBoundingClientRect().height) + 10 + "px";
    // the waiting changes' Apply bar sits on the phone's tab bar, which grows with large text
    var tab = tabs && tabs.getBoundingClientRect();
    var tabRoom = tab && tab.height && tab.top > window.innerHeight / 2
      ? Math.floor(window.innerHeight - tab.top) + "px" : null;
    var room = footRoom();
    put("--bar-room", high);
    put("--tab-room", tabRoom);
    put("--foot-room", room ? Math.ceil(room) + 10 + "px" : null);
  }
  // The first measure waits for the first frame: read right away, while the other deferred
  // scripts are still changing the page, it would force a layout of the whole page that the
  // frame then does again (a 100-card deck page paid twice). The room is only scroll padding,
  // so a frame's wait shows nothing. Where there is a ResizeObserver its first call is that
  // measure: it comes after the frame's own layout, so it reads what is there; a measure of its
  // own at the start of the frame laid out the page once more (the deck editor's first frame
  // paid about 70 ms of its 290 at 4x for it).
  if (window.ResizeObserver) {
    var watch = new ResizeObserver(fit);
    watch.observe(bar);
    for (var i = 0; i < feet.length; i++) watch.observe(feet[i]);
  } else if (window.requestAnimationFrame) requestAnimationFrame(fit); else fit();
  window.addEventListener("resize", fit);
})();
/* A place's switch (theme.hub_switch) scrolls sideways on a narrow phone at large text: it opens
   scrolled to the page's own part. */
(function () {
  "use strict";
  var seg = document.querySelector(".hubseg"), cur = seg && seg.querySelector("[aria-current=page]");
  if (!cur || seg.scrollWidth <= seg.clientWidth) return;
  var a = seg.getBoundingClientRect(), b = cur.getBoundingClientRect();
  seg.scrollLeft += b.left - a.left - (a.width - b.width) / 2;
})();

/* A place's switch (theme.hub_switch: Binder, Assistant) wider than the screen, as with large text
   on a phone, scrolls inside. The part naming this page is scrolled into full view, and a faded
   edge (.more-start, .more-end) shows that more parts lie that way. */
(function () {
  "use strict";
  var navs = document.querySelectorAll(".hubseg");
  if (!navs.length) return;
  function edges(n) {
    var over = n.scrollWidth > n.clientWidth + 1;
    n.classList.toggle("more-start", over && n.scrollLeft > 1);
    n.classList.toggle("more-end", over && n.scrollLeft + n.clientWidth < n.scrollWidth - 1);
  }
  function show(n) {
    var cur = n.querySelector("[aria-current=page]");
    if (cur && n.scrollWidth > n.clientWidth + 1) {
      var r = n.getBoundingClientRect(), c = cur.getBoundingClientRect();
      if (c.right > r.right) n.scrollLeft += c.right - r.right + 8;
      else if (c.left < r.left) n.scrollLeft -= r.left - c.left + 8;
    }
    edges(n);
  }
  Array.prototype.forEach.call(navs, function (n) {
    n.addEventListener("scroll", function () { edges(n); }, { passive: true });
    show(n);
    // the phone's text size can change after this runs: follow the parts' own size
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(function () { show(n); });
      ro.observe(n);
      Array.prototype.forEach.call(n.children, function (k) { ro.observe(k); });
    }
  });
  window.addEventListener("resize", function () { Array.prototype.forEach.call(navs, show); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { Array.prototype.forEach.call(navs, show); });
})();

/* Pull to refresh in the Android app (0.7.46, screen 9). The app's pull layer knows nothing of the
   page's layout, so the page says, at each touch, whether that touch may start a pull: not while
   the page is scrolled, not while a box, sheet, viewer or menu is open, not from a part of the page
   that scrolls on its own (the deck editor's panes, a printings sheet, a strip of chips), and not
   from a text field. The app keeps the answer only until the next touch. */
(function () {
  "use strict";
  var app = window.MtgNative;
  if (!app || typeof app.pullAllowed !== "function") return;
  function scrollsItself(el) {
    for (; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
      var s = getComputedStyle(el), y = s.overflowY, x = s.overflowX;
      if ((y === "auto" || y === "scroll") && el.scrollHeight > el.clientHeight) return true;
      if ((x === "auto" || x === "scroll") && el.scrollWidth > el.clientWidth) return true;
    }
    return false;
  }
  function boxOpen() {
    // every box, sheet, viewer and picker locks the page's scrolling while it is open
    if (getComputedStyle(document.documentElement).overflow === "hidden") return true;
    if (getComputedStyle(document.body).overflow === "hidden") return true;
    return !!document.querySelector(
      "dialog[open], .ctxmenu, details.dd[open], .msel-list:not([hidden]), .suggest-list:not([hidden])");
  }
  function mayPull(target) {
    if (window.scrollY > 0 || boxOpen()) return false;
    var el = target && target.nodeType === 1 ? target : null;
    if (el && el.closest("input, textarea, select, [contenteditable=''], [contenteditable=true]")) return false;
    return !scrollsItself(el);
  }
  window.mtgMayPull = mayPull; // the tests ask it directly
  document.addEventListener("touchstart", function (e) {
    var ok = mayPull(e.target);
    try { app.pullAllowed(ok); } catch (err) { /* an older app without the hook */ }
  }, { capture: true, passive: true });
})();
