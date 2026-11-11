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
  // alone under a full row ("Card models" at 412 px) and none is cut (confirmation NA4, P-9).
  // Where equal parts would need a second row but the parts fit one at their own widths, they keep
  // to one row (NC1)
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
    n.classList.remove("seg-grid", "seg-row");
    // each part measured without the padding a wrapped label's centring gave it (below): New deck's
    // "Paste a list" kept 110 px of it, so the parts no longer fitted one row and went one a row,
    // then fitted again, by turns
    kids.forEach(function (k) {
      if (!k.hasAttribute("data-fit-pad")) return;
      k.style.removeProperty("padding-left");
      k.style.removeProperty("padding-right");
      k.removeAttribute("data-fit-pad");
    });
    n.classList.add("seg-measure");
    var sum = 0;
    kids.forEach(function (k) {
      var kw = k.getBoundingClientRect().width;
      w = Math.max(w, kw);
      sum += kw;
    });
    n.classList.remove("seg-measure");
    var len = kids.length;
    // parts that fit the line side by side at their own widths stay on one row, each as wide as
    // its words and the row's room shared out (NC1: the Binder's Cards, Lists and Watchlist went
    // one a row at 320 px, each as wide as "Watchlist 2", and the Scan page's tabs two by two)
    if (len * w + (len - 1) * gap > room + 0.5 && sum + (len - 1) * gap <= room + 0.5) {
      n.classList.add("seg-row");
      return;
    }
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
      // and the room it has: the line it sits on can widen with no change to its own size (New
      // deck's Empty, Paste a list and From a file stayed one a row at 900 px and 200% text)
      if (n.parentElement) ro.observe(n.parentElement);
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
  // text cut on purpose (an ellipsis, a clipped box) is already held in its place
  function clipped(e, top) {
    for (; e && e !== top; e = e.parentElement) {
      var c = getComputedStyle(e);
      if (c.textOverflow === "ellipsis" || c.overflowX !== "visible") return true;
    }
    return false;
  }
  function widestWord(el) {
    var w = 0, tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT), n, m, re = /\S+/g;
    while ((n = tw.nextNode())) {
      if (clipped(n.parentElement, el)) continue;
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
    // a heading that holds its own controls keeps them at their full size
    if (!h.getClientRects().length || h.querySelector("button, summary, a, input, select")) return;
    var cs = getComputedStyle(h);
    var room = h.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    var w = widestWord(h);
    if (room > 0 && w > room + 0.5) h.style.zoom = String(Math.floor(room / w * 1000) / 1000);
  }
  function lineRights(b) {
    var tops = {}, right = -Infinity, left = Infinity, lines = 0;
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
        if (rs[i].left < left) left = rs[i].left;
      }
    }
    return { lines: lines, right: right, left: left };
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
    var cs = getComputedStyle(b);
    // a button laid out from its start (a menu's rows) keeps its content there
    if (!/center/.test(cs.display.indexOf("flex") >= 0 ? cs.justifyContent : cs.textAlign)) return;
    var got = lineRights(b);
    if (got.lines < 2) return;
    var pl = parseFloat(cs.paddingLeft), pr = parseFloat(cs.paddingRight), bl = parseFloat(cs.borderLeftWidth);
    var brw = parseFloat(cs.borderRightWidth);
    // the room left of the icon and label less the room right of them (the icon before the label
    // or after it)
    function off(at) {
      var br = b.getBoundingClientRect(), sr = svg.getBoundingClientRect();
      return br.right - brw - Math.max(at.right, sr.right) - (Math.min(at.left, sr.left) - br.left - bl);
    }
    // the padding on both sides grows by half the difference at a time, in a few rounds: how far
    // the label moves with it depends on how its lines sit in their room (centred, they stay put
    // and the icon alone moves; P-13: "Upload a picture…" kept 58 px left and 90 px right when the
    // padding took the whole difference in one step, computed as if the lines moved with it).
    // A step that changes how the label wraps is taken back, and the rounds end there
    // (a step that leaves them no nearer the middle is taken back too), and a smaller one tried
    // instead ("HTML page" at 320 px and 200% text: half the difference wrapped it on 3 lines)
    var add = 0, d = Math.abs(off(got)), step = Math.max(1, Math.floor(d / 2));
    for (var round = 0; round < 12 && d > 2 && step >= 1; round++) {
      b.setAttribute("data-fit-pad", "");
      b.style.paddingLeft = pl + add + step + "px";
      b.style.paddingRight = pr + add + step + "px";
      var now = lineRights(b), nd = Math.abs(off(now));
      if (now.lines !== got.lines || nd >= d) { step = Math.floor(step / 2); continue; }
      add += step;
      d = nd;
      step = Math.max(1, Math.floor(d / 2));
    }
    if (add) {
      b.style.paddingLeft = pl + add + "px";
      b.style.paddingRight = pr + add + "px";
    } else {
      b.style.removeProperty("padding-left");
      b.style.removeProperty("padding-right");
      b.removeAttribute("data-fit-pad");
    }
  }
  // a chart's labels along its axis (and the values over its columns) that run into each other at
  // a large text size: every other one is left out, the first and last kept (AD1)
  function thin(svg, sel) {
    var ts = svg.querySelectorAll(sel), i;
    for (i = 0; i < ts.length; i++) ts[i].removeAttribute("display");
    var near = function (a, b) { return b.left < a.right + 2; };
    // every 1st, 2nd, 3rd … label and the last one; the one before the last is left out when it is
    // too near the last (T9 beside T10)
    for (var step = 2; step <= ts.length; step++) {
      var shown = [], hit = false;
      for (i = 0; i < ts.length; i++) {
        if (i % (step - 1) && i !== ts.length - 1) ts[i].setAttribute("display", "none");
        else { ts[i].removeAttribute("display"); shown.push([ts[i], ts[i].getBoundingClientRect()]); }
      }
      var n = shown.length;
      if (n > 2 && near(shown[n - 2][1], shown[n - 1][1])) {
        shown[n - 2][0].setAttribute("display", "none");
        shown.splice(n - 2, 1);
      }
      for (i = 1; i < shown.length; i++) if (near(shown[i - 1][1], shown[i][1])) { hit = true; break; }
      if (!hit) return;
    }
    // no room for even the first and the last side by side: the first alone
    for (i = 1; i < ts.length; i++) ts[i].setAttribute("display", "none");
  }
  // a chart's labels at the size of the words around it (the chart's own font size), not the
  // chart's: the chart is drawn in its own units and
  // scaled to its width, so at 200% text its labels were 36 px beside 32 px words, ran out of its
  // left edge ("00%") and into each other ("0%" over "25%" and "T1") (NC2). The chart's box is
  // widened to hold its labels whole (the plot gives way), a y label is left out where the next
  // gridline's is too near, and the turn labels and values are thinned as before (AD1)
  function fitChart(svg) {
    if (!svg.getClientRects().length) return;
    if (!svg.hasAttribute("data-vb")) svg.setAttribute("data-vb", svg.getAttribute("viewBox") || "");
    var o = svg.getAttribute("data-vb").split(/[\s,]+/).map(Number);
    if (o.length !== 4 || !(o[2] > 0)) return;
    // the labels at the size of the chart's words, at every width (AF2: 11.8 px beside 14 px words
    // at 900 px, from .chart svg's .786rem)
    var want = parseFloat(getComputedStyle(svg.closest(".chart") || svg.parentNode).fontSize);
    var wrap = svg.parentNode.classList.contains("chart-x") ? svg.parentNode : null;
    var props = ["width", "max-width", "max-height"], j;
    if (wrap) for (j = 0; j < props.length; j++) svg.style.removeProperty(props[j]);
    sizeChart(svg, o, want);
    // a column chart keeps every column's value and turn: where they have no room side by side it
    // is drawn wider and scrolls sideways, as the deck's mana curve does (AF1: at 412 px and 200%
    // the 20, 17 and 5 columns had neither)
    if (!svg.querySelector("rect.col")) return;
    for (var round = 0; round < 3; round++) {
      var r = crowded(svg);
      if (!(r > 1.005)) return;
      if (!wrap) {
        wrap = document.createElement("div");
        wrap.className = "chart-x";
        svg.parentNode.insertBefore(wrap, svg);
        wrap.appendChild(svg);
      }
      var sc = svg.getScreenCTM(), vbw = svg.viewBox.baseVal.width;
      svg.style.setProperty("max-width", "none", "important");
      svg.style.setProperty("max-height", "none", "important");
      // its drawing that much wider (its box also holds its padding)
      var bw = svg.getBoundingClientRect().width;
      svg.style.setProperty("width", Math.ceil(bw + vbw * sc.a * (r - 1) + 1) + "px", "important");
      sizeChart(svg, o, want);
    }
  }
  // how much wider a column chart must be drawn for its values, and its turns, to stand side by
  // side with room between them (1 or less: they do)
  function crowded(svg) {
    var need = 1, groups = ["text.v", "text.x"], g, i;
    for (g = 0; g < groups.length; g++) {
      var ts = svg.querySelectorAll(groups[g]), was = [], rs = [];
      for (i = 0; i < ts.length; i++) { was.push(ts[i].getAttribute("display")); ts[i].removeAttribute("display"); }
      for (i = 0; i < ts.length; i++) rs.push(ts[i].getBoundingClientRect());
      for (i = 0; i < ts.length; i++) if (was[i] !== null) ts[i].setAttribute("display", was[i]);
      rs.sort(function (a, b) { return a.left + a.right - b.left - b.right; });
      for (i = 1; i < rs.length; i++) {
        var apart = (rs[i].left + rs[i].right - rs[i - 1].left - rs[i - 1].right) / 2;
        if (apart > 0) need = Math.max(need, (rs[i - 1].width / 2 + rs[i].width / 2 + 4) / apart);
      }
    }
    return need;
  }
  function sizeChart(svg, o, want) {
    var ts = svg.querySelectorAll("text"), ys = svg.querySelectorAll("text.y"), i, k;
    for (i = 0; i < ts.length; i++) ts[i].removeAttribute("display");
    // the gridlines' spacing in the chart's units (the y labels sit on them)
    var gap = ys.length > 1 ? Math.abs(ys[1].getAttribute("y") - ys[0].getAttribute("y")) : Infinity;
    var vb = o.slice();
    svg.setAttribute("viewBox", vb.join(" "));
    // the size in the chart's units depends on its box, and its box on the labels' size: a few
    // rounds settle it (each one widens the box less)
    for (var round = 0; round < 6; round++) {
      // the chart's real drawn scale: a capped height can make it smaller than its width says
      var sc = svg.getScreenCTM();
      if (!sc || !(sc.a > 0)) return;
      var f = want / sc.a;
      for (i = 0; i < ts.length; i++) ts[i].style.setProperty("font-size", f + "px", "important");
      // every 1st, 2nd or 4th y label, as many as have a line's room between them
      var every = 1;
      while (every < 4 && every * gap < f * 1.25 + 2) every *= 2;
      for (i = 0; i < ys.length; i++) {
        if (i % every && i !== ys.length - 1) ys[i].setAttribute("display", "none");
        else ys[i].removeAttribute("display");
      }
      if (every === 4 && ys.length === 5) ys[2].setAttribute("display", "none");
      var b = svg.getBBox(), pad = 1;
      var x0 = Math.min(o[0], b.x - pad), y0 = Math.min(o[1], b.y - pad);
      var x1 = Math.max(o[0] + o[2], b.x + b.width + pad), y1 = Math.max(o[1] + o[3], b.y + b.height + pad);
      var nb = [x0, y0, x1 - x0, y1 - y0];
      for (k = 0; k < 4 && Math.abs(nb[k] - vb[k]) < 0.25; k++);
      vb = nb;
      svg.setAttribute("viewBox", vb.map(function (v) { return Math.round(v * 100) / 100; }).join(" "));
      if (k === 4) break;
    }
    // the last box's own scale, should the rounds have run out before the box stopped moving
    sc = svg.getScreenCTM();
    if (k !== 4 && sc && sc.a > 0)
      for (i = 0; i < ts.length; i++) ts[i].style.setProperty("font-size", want / sc.a + "px", "important");
    thin(svg, "text.x");
    thin(svg, "text.v");
  }
  // a text box's hint: the longest of its hints (ui.hint_attrs: data-ph lists the shorter ones)
  // that fits the box at its size, the shortest where none does (P-5, P-19)
  var ctx = document.createElement("canvas").getContext("2d");
  var hintRO = window.ResizeObserver ? new ResizeObserver(function (es) {
    for (var i = 0; i < es.length; i++) fitHint(es[i].target);
  }) : null;
  function fitHint(el) {
    if (!el.hasAttribute("data-ph-full")) el.setAttribute("data-ph-full", el.getAttribute("placeholder") || "");
    var hints = [el.getAttribute("data-ph-full")].concat(el.getAttribute("data-ph").split("|"));
    if (!el.getClientRects().length) return;
    var cs = getComputedStyle(el);
    ctx.font = cs.fontStyle + " " + cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
    var room = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight), pick = hints[hints.length - 1];
    for (var i = 0; i < hints.length; i++) if (ctx.measureText(hints[i]).width <= room) { pick = hints[i]; break; }
    if (el.getAttribute("placeholder") !== pick) el.setAttribute("placeholder", pick);
  }
  function fitIn(root) {
    var ph = root.querySelectorAll("input[data-ph]"), k;
    for (k = 0; k < ph.length; k++) {
      fitHint(ph[k]);
      // shown later (a sheet, a box) or resized: its own size tells
      if (hintRO) hintRO.observe(ph[k]);
    }
    // the tab bar's counts kept at their 12 px cap when a text-only zoom scales it (NA5, theme.py)
    var nbs = root.querySelectorAll(".tabbar > a .nb, .tabbar > a.bell .n");
    for (k = 0; k < nbs.length; k++) {
      nbs[k].style.removeProperty("transform");
      var fs = parseFloat(getComputedStyle(nbs[k]).fontSize);
      if (fs > 12.5) {
        nbs[k].style.transformOrigin = "left top";
        nbs[k].style.transform = "scale(" + Math.round(1200 / fs) / 100 + ")";
      }
    }
    var cs2 = root.querySelectorAll(".chart svg");
    for (k = 0; k < cs2.length; k++) { fitChart(cs2[k]); seen(cs2[k]); }
    var hs = root.querySelectorAll("h1, h2, h3"), i;
    for (i = 0; i < hs.length; i++) fitHeading(hs[i]);
    var bs = root.querySelectorAll("button, .btn");
    for (i = 0; i < bs.length; i++) if (bs[i].querySelector(":scope > svg")) { centre(bs[i]); seen(bs[i]); }
  }
  // a chart or an icon button whose width changed since the page was fitted is fitted again: one
  // hidden then (a box or a sheet already in the page and shown later, a folded section opened),
  // or narrowed by the page's scroll bar coming in after it (the crawl's P-13 check: "Search all
  // decks" in the filter sheet, 28 px left and 58 px right; "HTML page" fitted on one line and
  // wrapped on two once the report's charts had grown). Its own padding, set by the fit, leaves
  // its width as the fit before left it, so a fit does not start another
  var widths = new WeakMap();
  var widthRO = window.ResizeObserver ? new ResizeObserver(function (es) {
    for (var i = 0; i < es.length; i++) {
      var el = es[i].target, bb = es[i].borderBoxSize, before = widths.get(el);
      var w = bb && bb[0] ? bb[0].inlineSize : el.getBoundingClientRect().width;
      widths.set(el, w);
      if (w > 0 && Math.abs(w - before) > 0.5) later(el.parentElement || el);
    }
  }) : null;
  function seen(el) {
    if (!widthRO || widths.has(el)) return;
    widths.set(el, el.getBoundingClientRect().width);
    widthRO.observe(el, { box: "border-box" });
  }
  // the next frame fits the whole page (a resize, a text size) or only what was drawn since (a
  // box, a sheet, a list's next rows), so opening a box does not measure the page under it again
  var queued = false, roots = [], whole = false;
  function later(root) {
    if (root && !whole) { if (roots.indexOf(root) < 0) roots.push(root); } else whole = true;
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () {
      var them = whole ? [document.body] : roots;
      queued = false; roots = []; whole = false;
      for (var i = 0; i < them.length; i++) if (them[i].isConnected) fitIn(them[i]);
    });
  }
  fitIn(document.body);
  window.addEventListener("resize", function () { later(); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { later(); });
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
      var FIT = "h1, h2, h3, button, .btn, input[data-ph], .chart", fitNow = [];
      for (var i = 0; i < muts.length; i++) {
        for (var j = 0; j < muts[i].addedNodes.length; j++) {
          var n = muts[i].addedNodes[j];
          if (n.nodeType !== 1 || !(n.matches(FIT) || n.querySelector(FIT))) continue;
          // the added part with what holds it (a button fits within its own box), in this task:
          // a box's content fetched and put in was shown a frame off centre (the crawl's P-13 check
          // measured "Delete this deck" as it came in, 28 px left and 77 px right)
          var at = n.parentElement || n;
          if (fitNow.indexOf(at) < 0) fitNow.push(at);
          // a hint fits from its first frame (a box's field drawn after its answer came)
          var ph = n.matches("input[data-ph]") ? [n] : n.querySelectorAll("input[data-ph]");
          for (var k = 0; k < ph.length; k++) { fitHint(ph[k]); if (hintRO) hintRO.observe(ph[k]); }
        }
      }
      for (i = 0; i < fitNow.length; i++) if (fitNow[i].isConnected) fitIn(fitNow[i]);
    }).observe(document.body, { childList: true, subtree: true });
  }
})();

/* Pull to refresh in the Android app (0.7.46, screen 9; every page since 0.7.47). The app's pull
   layer knows nothing of the page's layout, so the page says, at each touch, whether that touch may
   start a pull: not while the page is scrolled, not while a box, sheet, viewer or menu is open, not
   from a part of the page that scrolls on its own (the deck editor's panes, a printings sheet, a
   strip of chips) or can be dragged (the editor's cards), not from a text field, not while a box
   or drop-down on the page holds something the member changed and has not sent (a pull loads the
   page again, which would throw it away), and never on a page that holds state of its own and says
   so with data-no-pull on its root or body (a goldfish game). The app keeps the answer only until
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
  function track(e) {
    var el = e.target;
    if (el && /^(TEXTAREA|INPUT|SELECT)$/.test(el.tagName) && typed.indexOf(el) < 0) typed.push(el);
  }
  document.addEventListener("input", track, true);
  document.addEventListener("change", track, true);
  function changedChoice(sel) {
    for (var i = 0; i < sel.options.length; i++) if (sel.options[i].selected !== sel.options[i].defaultSelected) return true;
    return false;
  }
  document.addEventListener("submit", function (e) {
    typed = typed.filter(function (el) { return el.form !== e.target; });
  }, true);
  function unsent() {
    typed = typed.filter(function (el) { return el.isConnected; });
    return typed.some(function (el) {
      if (el.tagName === "SELECT") return changedChoice(el);
      if (el.type === "checkbox" || el.type === "radio") return el.checked !== el.defaultChecked;
      return el.value !== el.defaultValue;
    });
  }
  // Work a page script holds that a pull would lose (the deck editor's staged changes, a save on
  // its way): each such script adds a function to window.mtgHolds that says true while it does.
  function holding() {
    var holds = window.mtgHolds || [];
    for (var i = 0; i < holds.length; i++) {
      try { if (holds[i]()) return true; } catch (err) { return true; }
    }
    return false;
  }
  function mayPull(target) {
    if (window.scrollY > 0 || boxOpen() || unsent()) return false;
    if (document.documentElement.hasAttribute("data-no-pull") || document.body.hasAttribute("data-no-pull")) return false;
    if (holding()) return false;
    var el = target && target.nodeType === 1 ? target : null;
    if (el && el.closest("input, textarea, select, [contenteditable=''], [contenteditable=true], [draggable=true]")) return false;
    return !scrollsItself(el);
  }
  window.mtgMayPull = mayPull; // the tests ask it directly
  document.addEventListener("touchstart", function (e) {
    var ok = mayPull(e.target);
    try { app.pullAllowed(ok); } catch (err) { /* an older app without the hook */ }
  }, { capture: true, passive: true });
})();
