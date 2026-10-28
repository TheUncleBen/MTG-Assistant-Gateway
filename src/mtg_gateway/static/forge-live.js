"use strict";
// Report page: while a Forge run is queued or running (the Forge section, or the solo test played by
// Forge, carries data-forge-live and the report's id), ask the JSON status route (/api/v1/reports/<id>/forge)
// every 5 seconds and swap in the new section(s) and status, until every run has ended; then the page is
// fetched once more so the Markdown copy matches. While the quick solo test still runs on the research
// service (its section carries data-research; the report is stored before it is done), the page asks
// every 2 seconds and is read again whole once that section is no longer live: the test fills most of
// the page. No polling while the tab is hidden; it stops for good after a failed fetch or after five
// hours (the gateway gives a run up after four).
(function () {
  var INTERVAL = 5000;  // each finished game shows within about 8 s (the gateway asks Forge every 3 s)
  var RESEARCH_INTERVAL = 2000;
  var LIMIT = 5 * 3600 * 1000;
  var started = Date.now();
  var timer = null;
  // the live sections and the JSON field each is redrawn from
  var PARTS = [
    { sel: ".forge", field: "forge_html" },
    { sel: "#rv-solo-forge", field: "forge_solo_html" }
  ];

  function live() { return document.querySelector("[data-forge-live]"); }

  function imported(nodes) {
    return Array.prototype.map.call(nodes, function (n) { return document.importNode(n, true); });
  }

  // Only what changed is replaced: an unchanged poll leaves every node as it is, and a changed one
  // swaps just the parts that differ, so the aria-live section announces only the news (a finished
  // game), never the whole section every 5 seconds.
  function patch(target, nodes) {
    var old = target.childNodes;
    if (old.length !== nodes.length) {
      target.replaceChildren.apply(target, imported(nodes));
      return;
    }
    for (var i = 0; i < nodes.length; i++) {
      if (!old[i].isEqualNode(nodes[i])) target.replaceChild(document.importNode(nodes[i], true), old[i]);
    }
  }

  // One section redrawn from its fresh markup; true while that section is still live.
  function swapSection(sel, html) {
    var card = document.querySelector(sel + "[data-forge-live]");
    if (!card || typeof html !== "string" || !html) return false;
    var doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
    var fresh = doc.querySelector(sel);
    if (!fresh) return false;
    // The section itself stays (it is the aria-live region); the live mark goes once the run has ended.
    patch(card, Array.prototype.slice.call(fresh.childNodes));
    if (!fresh.hasAttribute("data-forge-live")) card.removeAttribute("data-forge-live");
    return fresh.hasAttribute("data-forge-live");
  }

  function swapStatus(html) {
    var shown = document.getElementById("rep-status");
    if (!shown || typeof html !== "string") return;
    var doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
    patch(shown, Array.prototype.slice.call(doc.body.firstChild.childNodes));
  }

  // Once every run has ended, the Markdown copy (built from the whole report) and the Play more games
  // button under the Forge section are read from the page.
  function refreshMarkdown() {
    var area = document.getElementById("rep-md");
    var more = document.getElementById("forge-more");
    if (!area && !more) return;
    fetch(location.href, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var md = doc.getElementById("rep-md");
        if (area && md) area.value = md.value;
        var button = doc.getElementById("forge-more");
        if (more && button) more.replaceWith(document.importNode(button, true));
      })
      .catch(function () { /* the copy keeps the earlier text */ });
  }

  function tick() {
    timer = null;
    var card = live();
    if (!card || Date.now() - started > LIMIT) return;
    if (document.hidden) return;  // resumed by visibilitychange
    var rid = card.getAttribute("data-report-id");
    if (!rid) return;
    fetch("/api/v1/reports/" + encodeURIComponent(rid) + "/forge", {
      credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }
    })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (data) {
        if (!data || data.ok !== true) return;
        if (document.querySelector("[data-research]")) {
          if (data.research === "running") { schedule(); return; }
          location.reload();
          return;
        }
        var still = false;
        PARTS.forEach(function (p) { if (swapSection(p.sel, data[p.field])) still = true; });
        swapStatus(data.status_html);
        if (still || live()) schedule(); else refreshMarkdown();
      })
      .catch(function () { /* signed out or offline: leave the page as it is */ });
  }

  function schedule() {
    if (!timer) timer = setTimeout(tick, document.querySelector("[data-research]") ? RESEARCH_INTERVAL : INTERVAL);
  }

  document.addEventListener("visibilitychange", function () { if (!document.hidden && live()) schedule(); });
  if (live()) schedule();

  // Head to head (a Forge pair's view, data-pair-live with both reports' ids): the page is read again
  // whole when either deck's games move on, until both runs have ended.
  var pair = document.querySelector("[data-pair-live]");
  if (pair) {
    var ids = [pair.getAttribute("data-pair-0"), pair.getAttribute("data-pair-1")].filter(Boolean);
    var seen = null;
    var pairTick = function () {
      if (document.hidden || Date.now() - started > LIMIT) { setTimeout(pairTick, INTERVAL); return; }
      Promise.all(ids.map(function (rid) {
        return fetch("/api/v1/reports/" + encodeURIComponent(rid) + "/forge", {
          credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }
        }).then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); });
      })).then(function (all) {
        var sig = all.map(function (d) {
          var f = (d && d.forge) || {};
          return [d && d.state, f.games_done || 0].join(":");
        }).join("|");
        if (seen !== null && sig !== seen) { location.reload(); return; }
        seen = sig;
        if (all.some(function (d) { return d && d.live; })) setTimeout(pairTick, INTERVAL);
      }).catch(function () { /* signed out or offline: leave the page as it is */ });
    };
    setTimeout(pairTick, INTERVAL);
  }
})();
