"use strict";
// Report page: while the Forge games are queued or running (the card carries data-forge-live),
// fetch this page again every 15 seconds and swap in the new Forge card and Markdown copy, until
// the run ends. No polling while the tab is hidden; it stops for good after a failed fetch or
// after five hours (the gateway gives a run up after four).
(function () {
  var INTERVAL = 15000;
  var LIMIT = 5 * 3600 * 1000;
  var started = Date.now();
  var timer = null;

  function live() { return document.querySelector(".card.forge[data-forge-live]"); }

  function swap(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var fresh = doc.querySelector(".card.forge");
    var card = live();
    if (!fresh || !card) return false;
    // The card itself stays (it is the aria-live region, so screen readers announce the change);
    // only its contents are swapped, and the live mark goes once the run has ended.
    card.replaceChildren.apply(card, Array.prototype.map.call(fresh.childNodes, function (n) {
      return document.importNode(n, true);
    }));
    if (!fresh.hasAttribute("data-forge-live")) card.removeAttribute("data-forge-live");
    var md = doc.getElementById("rep-md");
    var area = document.getElementById("rep-md");
    if (md && area) area.value = md.value;
    return fresh.hasAttribute("data-forge-live");
  }

  function tick() {
    timer = null;
    if (!live() || Date.now() - started > LIMIT) return;
    if (document.hidden) return;  // resumed by visibilitychange
    fetch(location.href, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.text(); })
      .then(function (html) { if (swap(html)) schedule(); })
      .catch(function () { /* signed out or offline: leave the page as it is */ });
  }

  function schedule() { if (!timer) timer = setTimeout(tick, INTERVAL); }

  document.addEventListener("visibilitychange", function () { if (!document.hidden && live()) schedule(); });
  if (live()) schedule();
})();
