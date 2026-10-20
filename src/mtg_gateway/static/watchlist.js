"use strict";
// The Watchlist page: "Details" opens one row in place (the others close; without a script the
// link opens the row through the address), the range buttons swap the chart between the last 30
// days and the full history, and a card's name opens the card viewer. A card's full history comes
// from the existing /watchlist/api/card (a year at most, nothing asked of Scryfall); a deck's is in
// its row. The chart is drawn as watchlist_page.chart_html draws it on the server.
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
    if (box.hasAttribute("data-full")) {
      try { return Promise.resolve(JSON.parse(box.getAttribute("data-full"))); } catch (e) { return Promise.reject(e); }
    }
    var name = box.getAttribute("data-card"), wid = box.getAttribute("data-watch");
    return fetch("/watchlist/api/card?name=" + encodeURIComponent(name), {
      credentials: "same-origin", headers: { Accept: "application/json" }
    })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (d) {
        var w = (d.watches || []).filter(function (x) { return x.watch_id === wid; })[0];
        if (!w) throw new Error("gone");
        return (w.history || []).filter(function (p) { return p.price !== null && p.price !== undefined; })
          .map(function (p) { return [p.day, p.price]; });
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

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var more = e.target.closest(".watch-page .w-t .more");
    if (more) { if (toggle(more)) e.preventDefault(); return; }
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
