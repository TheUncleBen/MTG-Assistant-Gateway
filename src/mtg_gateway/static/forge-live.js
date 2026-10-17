"use strict";
// Report page: while the Forge games are queued or running (the card carries data-forge-live and
// the report's id), ask the JSON status route (/api/v1/reports/<id>/forge) every 5 seconds and swap
// in the new Forge card and status, until the run ends; then the page is fetched once more so the
// Markdown copy matches. No polling while the tab is hidden; it stops for good after a failed fetch
// or after five hours (the gateway gives a run up after four).
(function () {
  var INTERVAL = 5000;  // each finished game shows within about 8 s (the gateway asks Forge every 3 s)
  var LIMIT = 5 * 3600 * 1000;
  var started = Date.now();
  var timer = null;

  function live() { return document.querySelector(".card.forge[data-forge-live]"); }

  function swapCard(html) {
    var doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
    var fresh = doc.querySelector(".card.forge");
    var card = live();
    if (!fresh || !card) return false;
    // The card itself stays (it is the aria-live region, so screen readers announce the change);
    // only its contents are swapped, and the live mark goes once the run has ended.
    card.replaceChildren.apply(card, Array.prototype.map.call(fresh.childNodes, function (n) {
      return document.importNode(n, true);
    }));
    if (!fresh.hasAttribute("data-forge-live")) card.removeAttribute("data-forge-live");
    return fresh.hasAttribute("data-forge-live");
  }

  function swapStatus(html) {
    var shown = document.getElementById("rep-status");
    if (!shown || typeof html !== "string") return;
    var doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
    shown.replaceChildren.apply(shown, Array.prototype.map.call(doc.body.firstChild.childNodes, function (n) {
      return document.importNode(n, true);
    }));
  }

  // Once the run has ended, the Markdown copy (built from the whole report) is read from the page.
  function refreshMarkdown() {
    var area = document.getElementById("rep-md");
    if (!area) return;
    fetch(location.href, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
      .then(function (html) {
        var md = new DOMParser().parseFromString(html, "text/html").getElementById("rep-md");
        if (md) area.value = md.value;
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
