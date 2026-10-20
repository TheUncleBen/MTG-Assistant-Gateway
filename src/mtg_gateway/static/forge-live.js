"use strict";
// Report page: while the Forge games are queued or running (the Forge section carries data-forge-live and
// the report's id), ask the JSON status route (/api/v1/reports/<id>/forge) every 5 seconds and swap
// in the new Forge card and status, until the run ends; then the page is fetched once more so the
// Markdown copy matches. No polling while the tab is hidden; it stops for good after a failed fetch
// or after five hours (the gateway gives a run up after four).
(function () {
  var INTERVAL = 5000;  // each finished game shows within about 8 s (the gateway asks Forge every 3 s)
  var LIMIT = 5 * 3600 * 1000;
  var started = Date.now();
  var timer = null;

  function live() { return document.querySelector(".forge[data-forge-live]"); }

  function imported(nodes) {
    return Array.prototype.map.call(nodes, function (n) { return document.importNode(n, true); });
  }

  // Only what changed is replaced: an unchanged poll leaves every node as it is, and a changed one
  // swaps just the parts that differ, so the aria-live card announces only the news (a finished
  // game), never the whole card every 5 seconds.
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

  function swapCard(html) {
    var doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
    var fresh = doc.querySelector(".forge");
    var card = live();
    if (!fresh || !card) return false;
    // The card itself stays (it is the aria-live region); the live mark goes once the run has ended.
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

  // Once the run has ended, the Markdown copy (built from the whole report) and the Play more games
  // button under the card are read from the page.
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
        var still = swapCard(String(data.forge_html || ""));
        swapStatus(data.status_html);
        if (still) schedule(); else refreshMarkdown();
      })
      .catch(function () { /* signed out or offline: leave the page as it is */ });
  }

  function schedule() { if (!timer) timer = setTimeout(tick, INTERVAL); }

  document.addEventListener("visibilitychange", function () { if (!document.hidden && live()) schedule(); });
  if (live()) schedule();
})();
