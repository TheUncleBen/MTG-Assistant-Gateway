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
  // The page's transitions may run once the shared stylesheet is in and has been applied with
  // them off (theme.py html.preload): a deferred script can run before a late stylesheet lands.
  (function () {
    var root = document.documentElement, link = document.querySelector("link[href^='/static/site.css']");
    function lift() {
      void document.body.offsetWidth; // the page's style with the stylesheet, transitions still off
      root.classList.remove("preload");
    }
    if (!link || link.sheet) lift();
    else {
      link.addEventListener("load", lift);
      link.addEventListener("error", lift);
    }
  })();
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
/* The phone's tab bar at large text: a tab's label that no longer fits its tab (text-only zoom, a
   phone's font scale) would be cut to "Assi…". The bar then shows the icons alone (.icons) and
   keeps every name for screen readers (theme.py). Each label is measured with its own font, not
   by its box, so the measure is the same whether the labels show or not; it is read again when
   the bar or a tab changes size (the text size changing) and when the window does. */
(function () {
  "use strict";
  var bar = document.querySelector(".tabbar");
  if (!bar) return;
  var tabs = Array.prototype.filter.call(bar.querySelectorAll(":scope > a, :scope > details.more > summary"),
    function (t) { return t.querySelector(".lbl"); });
  if (!tabs.length) return;
  var ctx = document.createElement("canvas").getContext("2d");
  function fits() {
    for (var i = 0; i < tabs.length; i++) {
      var tab = tabs[i], lbl = tab.querySelector(".lbl"), cs = getComputedStyle(lbl);
      // a label wraps between its words; its longest word must fit the tab whole
      if (cs.display === "none") continue;
      ctx.font = cs.fontStyle + " " + cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
      var room = tab.getBoundingClientRect().width - 4, words = lbl.textContent.split(/\s+/);
      for (var j = 0; j < words.length; j++)
        if (room > 0 && ctx.measureText(words[j]).width > room) return false;
    }
    return true;
  }
  function fit() {
    // measured with the labels shown (hidden ones all read as one line), in one frame: nothing
    // is painted between the two class changes
    var had = bar.classList.contains("icons");
    if (had) bar.classList.remove("icons");
    var icons = !fits();
    if (icons !== had || icons) bar.classList.toggle("icons", icons);
  }
  if (window.ResizeObserver) {
    var ro = new ResizeObserver(fit);
    ro.observe(bar);
    tabs.forEach(function (t) { ro.observe(t); });
    // a text size changed in place (a browser's text setting, no reload and no resize) changes
    // neither the bar's nor a tab's size, and the hidden labels keep theirs: a box 1em square in
    // the labels' font tells it (confirmation NA3: "HomeDecksBinderAssistMore")
    var em = document.createElement("span");
    em.className = "lbl-em";
    em.setAttribute("aria-hidden", "true");
    bar.appendChild(em);
    ro.observe(em);
  } else fit();
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
  // the quick-filter chip rows (ui.chips) scroll the same way, with the same faded edges (N12)
  var SEL = ".hubseg, .ui-chips";
  var navs = [];
  function edges(n) {
    var over = n.scrollWidth > n.clientWidth + 1;
    n.classList.toggle("more-start", over && n.scrollLeft > 1);
    n.classList.toggle("more-end", over && n.scrollLeft + n.clientWidth < n.scrollWidth - 1);
  }
  // a switch's parts are all as wide as its widest one, in rows that hold as many each (one apart
  // at most): as many a row as fit whole, then spread evenly over the rows needed, so no part sits
  // alone under a full row ("Card models" at 412 px) and none is cut (confirmation NA4, P-9)
  function balance(n) {
    if (!n.matches(".hubseg") || !n.getClientRects().length) return;
    var kids = Array.prototype.filter.call(n.children, function (k) {
      return k.getClientRects().length && getComputedStyle(k).position !== "absolute";
    });
    if (kids.length < 2) return;
    // the room: the line the switch sits on (its parent's width, less its own padding)
    var cs = getComputedStyle(n), ps = getComputedStyle(n.parentElement), gap = parseFloat(cs.columnGap) || 0;
    var room = Math.min(n.parentElement.clientWidth - parseFloat(ps.paddingLeft) - parseFloat(ps.paddingRight),
      document.documentElement.clientWidth) - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) -
      parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth), w = 0;
    // each part measured on one line at its own width
    n.classList.remove("seg-grid");
    n.classList.add("seg-measure");
    kids.forEach(function (k) { w = Math.max(w, k.getBoundingClientRect().width); });
    n.classList.remove("seg-measure");
    var len = kids.length;
    var cols = Math.max(1, Math.min(len, Math.floor((room + gap) / (w + gap) + 0.001)));
    cols = Math.ceil(len / Math.ceil(len / cols));
    // a last row of one part under fuller rows is a part alone: fewer a row until none is
    while (cols > 1 && len % cols === 1) cols--;
    n.style.setProperty("--seg-cols", String(cols));
    n.classList.add("seg-grid");
  }
  function show(n) {
    balance(n);
    var cur = n.querySelector("[aria-current=page], [aria-selected=true]");
    if (cur && n.scrollWidth > n.clientWidth + 1) {
      var r = n.getBoundingClientRect(), c = cur.getBoundingClientRect();
      if (c.right > r.right) n.scrollLeft += c.right - r.right + 8;
      else if (c.left < r.left) n.scrollLeft -= r.left - c.left + 8;
    }
    edges(n);
  }
  function watch(n) {
    if (navs.indexOf(n) >= 0) return;
    navs.push(n);
    n.addEventListener("scroll", function () { edges(n); }, { passive: true });
    show(n);
    // the phone's text size can change after this runs: follow the parts' own size
    if (window.ResizeObserver) {
      // in the next frame: the switch laid out again by show() is not measured in this one
      var queued = false;
      var ro = new ResizeObserver(function () {
        if (queued) return;
        queued = true;
        requestAnimationFrame(function () { queued = false; show(n); });
      });
      ro.observe(n);
      Array.prototype.forEach.call(n.children, function (k) { ro.observe(k); });
    }
  }
  function watchIn(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches(SEL)) watch(root);
    Array.prototype.forEach.call(root.querySelectorAll(SEL), watch);
  }
  watchIn(document.body);
  // a switch drawn by a page's own script after this ran (the Scan page's tabs, a box's tabs)
  // gets the same edges: watch what is added later
  if (window.MutationObserver && document.body) {
    new MutationObserver(function (muts) {
      muts.forEach(function (m) { Array.prototype.forEach.call(m.addedNodes, watchIn); });
    }).observe(document.body, { childList: true, subtree: true });
  }
  window.addEventListener("resize", function () { navs.forEach(show); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { navs.forEach(show); });
})();

/* Fitting to the text (confirmation P-1, P-13), again whenever the window, the text size or the
   page changes:
   - a heading word wider than the heading's whole line (a phone's font scale: "Preconstructed"
     424 px in a 288 px line) broke bare; the heading is zoomed down until its widest word fits;
   - a button with an icon whose label wraps kept its lines against the icon but sat left of
     centre (the label's lines take the whole width left): the room its lines leave is split
     between the button's two sides. */
(function () {
  "use strict";
  function widestWord(el) {
    var w = 0, tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT), n, m, re = /\S+/g;
    while ((n = tw.nextNode())) {
      re.lastIndex = 0;
      while ((m = re.exec(n.textContent))) {
        var r = document.createRange(), sum = 0, rs;
        r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
        rs = r.getClientRects();
        for (var i = 0; i < rs.length; i++) sum += rs[i].width;
        if (sum > w) w = sum;
      }
    }
    return w;
  }
  function fitHeading(h) {
    if (h.style.zoom) h.style.removeProperty("zoom");
    if (!h.getClientRects().length) return;
    var cs = getComputedStyle(h);
    var room = h.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    var w = widestWord(h);
    if (room > 0 && w > room + 0.5) h.style.zoom = String(Math.floor(room / w * 1000) / 1000);
  }
  function lineRights(b) {
    var tops = {}, right = -Infinity, lines = 0;
    for (var c = b.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 1 && (c.tagName !== "SPAN" || c.classList.contains("sr-only"))) continue;
      if (c.nodeType !== 1 && c.nodeType !== 3) continue;
      var r = document.createRange();
      r.selectNodeContents(c);
      var rs = r.getClientRects();
      for (var i = 0; i < rs.length; i++) {
        if (!rs[i].width) continue;
        var t = Math.round(rs[i].top);
        if (!tops[t]) { tops[t] = 1; lines++; }
        if (rs[i].right > right) right = rs[i].right;
      }
    }
    return { lines: lines, right: right };
  }
  function centre(b) {
    if (b.hasAttribute("data-fit-pad")) {
      b.style.removeProperty("padding-left");
      b.style.removeProperty("padding-right");
      b.removeAttribute("data-fit-pad");
    }
    var svg = b.querySelector(":scope > svg");
    if (!svg || !b.getClientRects().length || !svg.getBoundingClientRect().width ||
      getComputedStyle(svg).position === "absolute") return;
    var got = lineRights(b);
    if (got.lines < 2) return;
    var cs = getComputedStyle(b), br = b.getBoundingClientRect();
    var pl = parseFloat(cs.paddingLeft), pr = parseFloat(cs.paddingRight);
    var left = svg.getBoundingClientRect().left - br.left - parseFloat(cs.borderLeftWidth) - pl;
    var slack = br.right - parseFloat(cs.borderRightWidth) - pr - got.right - left;
    if (slack < 4) return;
    var half = Math.floor(slack / 2) - 1;
    b.setAttribute("data-fit-pad", "");
    b.style.paddingLeft = pl + half + "px";
    b.style.paddingRight = pr + half + "px";
  }
  // a chart's labels along its axis (and the values over its columns) that run into each other at
  // a large text size: every other one is left out, the first and last kept (AD1)
  function thin(svg, sel) {
    var ts = svg.querySelectorAll(sel), i;
    for (i = 0; i < ts.length; i++) ts[i].removeAttribute("display");
    for (var step = 2; step < ts.length; step++) {
      var shown = [], hit = false;
      for (i = 0; i < ts.length; i++) {
        if (i % (step - 1) && i !== ts.length - 1) ts[i].setAttribute("display", "none");
        else { ts[i].removeAttribute("display"); shown.push(ts[i].getBoundingClientRect()); }
      }
      for (i = 1; i < shown.length; i++) if (shown[i].left < shown[i - 1].right + 2) { hit = true; break; }
      if (!hit) return;
    }
  }
  function fitIn(root) {
    var cs2 = root.querySelectorAll(".chart svg"), k;
    for (k = 0; k < cs2.length; k++) { thin(cs2[k], "text.x"); thin(cs2[k], "text.v"); }
    var hs = root.querySelectorAll("h1, h2, h3"), i;
    for (i = 0; i < hs.length; i++) fitHeading(hs[i]);
    var bs = root.querySelectorAll("button, .btn");
    for (i = 0; i < bs.length; i++) if (bs[i].querySelector(":scope > svg")) centre(bs[i]);
  }
  var queued = false;
  function later() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; fitIn(document.body); });
  }
  fitIn(document.body);
  window.addEventListener("resize", later);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(later);
  if (window.ResizeObserver) {
    // a text size changed in place (no reload, no resize): a box 1em square tells it
    var em = document.createElement("span");
    em.setAttribute("aria-hidden", "true");
    em.style.cssText = "position:absolute;left:-9999px;top:0;width:1em;height:1em;visibility:hidden";
    document.body.appendChild(em);
    var first = true;
    new ResizeObserver(function () { if (first) { first = false; return; } later(); }).observe(em);
  }
  // a box, a sheet or a list drawn after this ran
  if (window.MutationObserver) {
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        for (var j = 0; j < muts[i].addedNodes.length; j++) {
          var n = muts[i].addedNodes[j];
          if (n.nodeType === 1 && (n.matches("h1, h2, h3, button, .btn") || n.querySelector("h1, h2, h3, button, .btn"))) {
            later();
            return;
          }
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
})();

/* Pull to refresh in the Android app (0.7.46, screen 9). The app's pull layer knows nothing of the
   page's layout, so the page says, at each touch, whether that touch may start a pull: not while
   the page is scrolled, not while a box, sheet, viewer or menu is open, not from a part of the page
   that scrolls on its own (the deck editor's panes, a printings sheet, a strip of chips), and not
   from a text field, and not while a box on the page holds words the member typed and has not sent
   (a pull loads the page again, which would throw them away). The app keeps the answer only until
   the next touch. */
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
  // Boxes the member typed in: only typing marks one (a page script that fills a box fires no
  // input event), and sending its form, or typing it back to what the page came with, clears it.
  var typed = [];
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT") && typed.indexOf(el) < 0) typed.push(el);
  }, true);
  document.addEventListener("submit", function (e) {
    typed = typed.filter(function (el) { return el.form !== e.target; });
  }, true);
  function unsent() {
    typed = typed.filter(function (el) { return el.isConnected; });
    return typed.some(function (el) {
      if (el.type === "checkbox" || el.type === "radio") return el.checked !== el.defaultChecked;
      return el.value !== el.defaultValue;
    });
  }
  function mayPull(target) {
    if (window.scrollY > 0 || boxOpen() || unsent()) return false;
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
