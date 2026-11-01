"use strict";
// The Watchlist page: "Details" opens one row in place (the others close; without a script the
// link opens the row through the address), the range buttons swap the chart between the last 30
// days and the full history, and a card's name opens the card viewer. A card's full history comes
// from /watchlist/api/card and a deck's from /watchlist/api/deck (a year at most, nothing asked of
// Scryfall), read once per row. The chart is drawn as watchlist_page.chart_html draws it on the server.
// Load more asks the page for just the rows it lacks (the button's own link with part= and from=,
// watchlist_page.watchlist_fragment_html) and adds them under the ones shown; without a script
// the link is the longer page.
(function () {
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function money(v, cur) {
    var s = Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (cur === "eur" ? "€" : "$") + s;
  }
  function today() { return new Date().toISOString().slice(0, 10); }
  function dayText(day) {
    if (!day) return "";
    var t = today();
    if (day === t) return "today";
    var p = day.split("-");
    var s = MONTHS[Number(p[1]) - 1] + " " + Number(p[2]);
    return p[0] === t.slice(0, 4) ? s : s + ", " + p[0];
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function chart(points, cur, label) {
    // points: [[day, price], ...] oldest first, priced only
    if (points.length < 2) return el("p", "ui-hint", "The chart shows once there are two days of prices.");
    var values = points.map(function (p) { return Number(p[1]); });
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values), span = (hi - lo) || 1;
    var w = 600, h = 160, step = w / (values.length - 1);
    var coords = values.map(function (v, i) {
      var y = hi === lo ? h / 2 : h - 6 - (v - lo) / span * (h - 12);
      return (i * step).toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var box = el("div", "pc");
    var ys = el("div", "pc-y");
    ys.setAttribute("aria-hidden", "true");
    (hi !== lo ? [hi, (hi + lo) / 2, lo] : [hi]).forEach(function (y) { ys.appendChild(el("span", "", money(y, cur))); });
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "pcl");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label + ": " + money(values[0], cur) + " on " + points[0][0] + ", " +
      money(values[values.length - 1], cur) + " on " + points[points.length - 1][0] + ".");
    var line = document.createElementNS(NS, "polyline");
    line.setAttribute("points", coords);
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(line);
    var xs = el("div", "pc-x");
    xs.setAttribute("aria-hidden", "true");
    var n = points.length, picks = [];
    for (var i = 0; i < 5 && i < n; i++) {
      var k = n >= 5 ? Math.round(i * (n - 1) / 4) : i;
      if (picks.indexOf(k) < 0) picks.push(k);
    }
    picks.forEach(function (k) { xs.appendChild(el("span", "", dayText(points[k][0]))); });
    box.appendChild(ys);
    box.appendChild(svg);
    box.appendChild(xs);
    return box;
  }

  function table(points, cur, label) {
    var d = el("details", "rv-det wx-tbl");
    d.appendChild(el("summary", "", "Price history table"));
    var wrap = el("div", "tbl");
    wrap.setAttribute("role", "region");
    wrap.setAttribute("aria-label", label + ", newest first");
    wrap.tabIndex = 0;
    var t = el("table");
    var head = el("thead"), hr = el("tr");
    ["Day", "Price"].forEach(function (x) { var th = el("th", "", x); th.scope = "col"; hr.appendChild(th); });
    head.appendChild(hr);
    var body = el("tbody");
    points.slice().reverse().forEach(function (p) {
      var tr = el("tr"), th = el("th", "", p[0]);
      th.scope = "row";
      tr.appendChild(th);
      tr.appendChild(el("td", "", money(p[1], cur)));
      body.appendChild(tr);
    });
    t.appendChild(head);
    t.appendChild(body);
    wrap.appendChild(t);
    d.appendChild(wrap);
    return d;
  }

  function fullPoints(box) {
    if (box.__full) return Promise.resolve(box.__full);
    var get = function (url) {
      return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); });
    };
    var keep = function (points) { box.__full = points; return points; };
    if (box.hasAttribute("data-deck")) {
      return get("/watchlist/api/deck?deck_id=" + encodeURIComponent(box.getAttribute("data-deck")))
        .then(function (d) { return keep((d.history || []).map(function (p) { return [p.day, p.price]; })); });
    }
    var name = box.getAttribute("data-card"), wid = box.getAttribute("data-watch");
    return fetch("/watchlist/api/card?name=" + encodeURIComponent(name), {
      credentials: "same-origin", headers: { Accept: "application/json" }
    })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (d) {
        var w = (d.watches || []).filter(function (x) { return x.watch_id === wid; })[0];
        if (!w) throw new Error("gone");
        return keep((w.history || []).filter(function (p) { return p.price !== null && p.price !== undefined; })
          .map(function (p) { return [p.day, p.price]; }));
      });
  }

  function setRange(btn) {
    var box = btn.closest(".wx-chart");
    if (!box) return;
    var draw = box.querySelector(".wx-draw"), tbl = box.querySelector(".wx-table");
    if (!box.__short) box.__short = [draw.innerHTML, tbl.innerHTML];
    var head = box.querySelector(".wx-h h3");
    var label = (head && head.textContent) || "";
    if (!box.__heading) box.__heading = label;
    box.querySelectorAll(".seg button").forEach(function (b) { b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
    var cur = box.getAttribute("data-currency") || "usd";
    var what = box.getAttribute("data-card") || box.__heading;
    if (btn.getAttribute("data-range") !== "full") {
      draw.innerHTML = box.__short[0];
      tbl.innerHTML = box.__short[1];
      if (head) head.textContent = box.__heading;
      return;
    }
    fullPoints(box).then(function (points) {
      if (btn.getAttribute("aria-pressed") !== "true") return;  // another range was chosen meanwhile
      draw.replaceChildren(chart(points, cur, what));
      tbl.replaceChildren(table(points, cur, what));
      if (head) head.textContent = box.__heading.replace("last 30 days", "full history");
    }).catch(function () {
      draw.replaceChildren(el("p", "ui-hint", "The full history could not be read. Reload the page and try again."));
    });
  }

  function toggle(link) {
    var row = link.closest(".tr");
    var panel = document.getElementById(link.getAttribute("aria-controls"));
    if (!row || !panel) return false;
    var opening = panel.hidden;
    document.querySelectorAll(".watch-page .w-t .more[aria-expanded=true]").forEach(function (other) {
      if (other === link) return;
      var p = document.getElementById(other.getAttribute("aria-controls"));
      if (p) p.hidden = true;
      other.setAttribute("aria-expanded", "false");
      other.textContent = "Details";
      var r = other.closest(".tr");
      if (r) r.classList.remove("open");
    });
    panel.hidden = !opening;
    link.setAttribute("aria-expanded", opening ? "true" : "false");
    link.textContent = opening ? "Close" : "Details";
    row.classList.toggle("open", opening);
    return true;
  }

  // -- the selection and the bulk bar ------------------------------------------------------------
  // A tick on any row shows the bar (its count, Price alert…, Move to list…, Stop watching,
  // Cancel); the rows' tick boxes and the boxes' fields belong to the one bulk form, so a press
  // sends the selected cards with the box's values. Select ticks every card row on screen, or
  // clears them all; a list head's tick box does the same for its own cards.
  function boxes() { return Array.prototype.slice.call(document.querySelectorAll(".watch-page .w-t .chk input[name=watch_id]")); }
  function ticked() { return boxes().filter(function (b) { return b.checked; }); }
  function rowsOfList(head) {
    var out = [], node = head.nextElementSibling;
    while (node && !node.classList.contains("wgrp")) {
      if (node.id && node.id.indexOf("wr-") === 0) out.push(node);
      node = node.nextElementSibling;
    }
    return out;
  }
  function redraw() {
    var n = ticked().length, bar = document.querySelector(".watch-page .ui-bar");
    boxes().forEach(function (b) { var tr = b.closest(".tr"); if (tr) tr.classList.toggle("sel", b.checked); });
    document.querySelectorAll(".watch-page .w-t .wgrp").forEach(function (head) {
      var rows = rowsOfList(head), on = rows.filter(function (r) { return r.querySelector(".chk input").checked; }).length;
      var all = head.querySelector(".w-grpsel");
      if (all) { all.checked = rows.length > 0 && on === rows.length; all.indeterminate = on > 0 && on < rows.length; }
    });
    if (bar) {
      bar.hidden = n === 0;
      bar.querySelector(".count").textContent = n + " selected";
    }
    var sel = document.querySelector(".watch-page .w-select");
    if (sel) sel.setAttribute("aria-pressed", n > 0 && n === boxes().length ? "true" : "false");
    var stop = document.querySelector("#w-stop [data-stop-count]");
    if (stop) stop.textContent = n === 1 ? "The selected card goes" : "The " + n + " selected cards go";
    var word = document.querySelector("#w-stop p");
    if (word) word.childNodes[1].textContent = (n === 1 ? " from your watchlist with its price history." : " from your watchlist with their price history.") + " Your Binder is not touched.";
  }
  function stopNow() {
    var form = document.getElementById("w-bulk");
    if (!form) return;
    var doing = form.querySelector("input[name=do]");
    if (!doing) { doing = document.createElement("input"); doing.type = "hidden"; doing.name = "do"; form.appendChild(doing); }
    doing.value = "remove";
    var bar = document.querySelector(".watch-page .ui-bar");
    if (bar) bar.querySelectorAll("a, button").forEach(function (b) { b.setAttribute("aria-disabled", "true"); });
    form.submit();
  }
  function setAll(on, which) {
    (which || boxes()).forEach(function (b) { b.checked = on; });
    redraw();
  }
  // the page draws Select hidden: it needs this script
  var selectBtn = document.querySelector(".watch-page .w-select");
  if (selectBtn) selectBtn.hidden = false;
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || !t.closest || !t.closest(".watch-page .w-t")) return;
    if (t.classList.contains("w-grpsel")) {
      var head = t.closest(".wgrp");
      setAll(t.checked, rowsOfList(head).map(function (r) { return r.querySelector(".chk input"); }));
      return;
    }
    if (t.name === "watch_id") redraw();
  });
  // a list's Rename and Delete boxes, filled from the head they were opened for: its ⋮ is a
  // script menu (static/menus.js, which keeps the menu's history entry and the box's apart);
  // without scripts the head's own drop-down of links opens the page with the box drawn open
  function openListBox(which, head) {
    var Box = window.MtgBox, name = head ? head.getAttribute("data-list") : "";
    var box = document.getElementById(which);
    if (!box || !Box || !Box.openElement) return false;
    box.querySelectorAll("[data-list-name]").forEach(function (i) { i.value = name; });
    var word = box.querySelector("[data-list-word]"), input = box.querySelector("input[name=new_name]");
    if (word) word.textContent = "“" + name + "”";
    if (input) input.value = name;
    Box.openElement(box, { returnFocus: head ? head.querySelector("summary") : null });
    return true;
  }
  // one row's ⋮ › Move to list…: that row alone is selected, then the Move box opens
  window.MtgWatchlist = {
    selectOnly: function (tr) {
      setAll(false);
      var b = tr && tr.querySelector(".chk input[name=watch_id]");
      if (b) { b.checked = true; redraw(); }
    },
    open: function (id, back) {
      var Box = window.MtgBox;
      var node = document.getElementById(id);
      if (Box && Box.openElement && node) Box.openElement(node, { returnFocus: back });
    },
    openListBox: openListBox
  };

  // -- Load more: the rows the list lacks, added in place ------------------------------------------
  var loading = false;
  function loadMore(link) {
    var wrap = link.closest(".loadmore");
    var table = wrap && wrap.previousElementSibling;
    if (!table || !table.classList.contains("w-t") || typeof fetch !== "function") return false;
    if (loading) return true;
    var part = /decks/i.test(table.getAttribute("aria-label") || "") ? "decks" : "cards";
    var shown = table.querySelectorAll(".tr[id^='wr-']").length;
    var url = link.href.split("#")[0] + (link.href.indexOf("?") < 0 ? "?" : "&") +
      "part=" + part + "&from=" + shown;
    loading = true;
    link.setAttribute("aria-busy", "true");
    link.setAttribute("aria-disabled", "true");
    fetch(url, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
      .then(function (html) {
        var box = document.createElement("div");
        box.innerHTML = html;
        var rows = box.querySelector(".w-rows"), next = box.querySelector(".loadmore");
        if (!rows || !next) throw new Error("no rows");
        var first = rows.firstElementChild;
        while (rows.firstChild) table.appendChild(rows.firstChild);
        wrap.replaceWith(next);
        document.dispatchEvent(new CustomEvent("rows:loaded", { bubbles: true }));  // the rows' ⋮
        var focus = first && first.querySelector(".more");
        if (focus) focus.focus({ preventScroll: true });
        if (first) first.scrollIntoView({ block: "nearest" });
      })
      .catch(function () {
        link.removeAttribute("aria-busy");
        link.removeAttribute("aria-disabled");
        var Menu = window.MtgMenu;
        if (Menu && Menu.toast) Menu.toast("No connection; nothing more could be read. Try again.", { error: true });
      })
      .then(function () { loading = false; });
    return true;
  }

  // Stop watching goes at once: the notice on the page that follows offers Undo for a while
  // (0.7.46 audit QG-09); without scripts the link opens the box that asks first. Seen before
  // the shared box opener (box.js), which would open that box.
  document.addEventListener("click", function (e) {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var stop = e.target.closest ? e.target.closest(".watch-page .ui-bar a[data-box-open='w-stop']") : null;
    if (!stop || ticked().length === 0) return;
    e.preventDefault();
    e.stopPropagation();
    stopNow();
  }, true);
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var more = e.target.closest(".watch-page .w-t .more");
    if (more) { if (toggle(more)) e.preventDefault(); return; }
    var grow = e.target.closest(".watch-page .loadmore a[data-load-more]");
    if (grow) { if (loadMore(grow)) e.preventDefault(); return; }
    var sel = e.target.closest(".watch-page .w-select");
    if (sel) { setAll(sel.getAttribute("aria-pressed") !== "true"); return; }
    var clear = e.target.closest(".watch-page .ui-bar .w-clear");
    if (clear) { setAll(false); return; }
    var bulk = e.target.closest(".watch-page .ui-bar a[data-box-open]");
    if (bulk && ticked().length === 0) {
      e.preventDefault();
      var Menu = window.MtgMenu;
      if (Menu && Menu.toast) Menu.toast("Tick a card first.", { error: true });
      return;
    }

    var range = e.target.closest(".watch-page .seg button[data-range]");
    if (range) { setRange(range); return; }
    var name = e.target.closest(".watch-page a.wcard");
    if (name && window.MtgCardView) {
      e.preventDefault();
      window.MtgCardView.open(window.MtgCardView.fromElement(name), []);
    }
  });
  // the Details link is a button for a screen reader: Space presses it too
  document.addEventListener("keydown", function (e) {
    if (e.key !== " ") return;
    var more = e.target.closest && e.target.closest(".watch-page .w-t .more");
    if (more && toggle(more)) e.preventDefault();
  });
})();
