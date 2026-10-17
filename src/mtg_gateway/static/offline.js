// The service worker's "You're offline" page (companion.offline_page). The worker (/sw.js) answers
// this file from memory, so it loads while the gateway can't be reached. The page loads its
// address again by itself
// 1. when the browser says it is back online (the online event), and
// 2. once the gateway answers a check made every few seconds: a plain GET of /healthz, which the
//    worker leaves alone (it only answers page loads), so a failed check never comes back as the
//    offline page.
// One reload at most per page: once it starts, nothing else is checked. A check waits for the one
// before it (each gives up after a few seconds), and none is made while the page is hidden. The
// page's Try again link keeps working as before.
// A page that keeps failing while /healthz answers would be loaded again every few
// seconds for good, so the automatic reloads of one address stop after TRIES in a row (counted in
// this tab's session storage); the page then stays, with its Try again link. Any other way of
// coming here (a link, Try again, a new address) starts the count afresh.
(function () {
  "use strict";
  var EVERY_MS = 5000;
  var CHECK_TIMEOUT_MS = 4000;
  var TRIES = 3;
  var KEY = "mtg-offline-reloads";
  var reloading = false;
  var timer = 0;

  function navType() {
    try {
      var nav = performance.getEntriesByType("navigation")[0];
      return nav ? nav.type : "";
    } catch (e) {
      return "";
    }
  }

  // the automatic reloads already made of this address in a row (0 unless this load is one)
  function triesSoFar() {
    try {
      var saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
      if (navType() === "reload" && saved && saved.href === location.href) return saved.n || 0;
      sessionStorage.removeItem(KEY);
    } catch (e) {}
    return 0;
  }

  var tries = triesSoFar();

  function reload() {
    if (reloading) return;
    reloading = true;
    clearTimeout(timer);
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ href: location.href, n: tries + 1 }));
    } catch (e) {}
    window.location.reload();
  }

  function later() {
    if (!reloading) timer = setTimeout(check, EVERY_MS);
  }

  function check() {
    if (reloading) return;
    if (document.hidden || typeof fetch !== "function") {
      later();
      return;
    }
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var stop = ctl ? setTimeout(function () { ctl.abort(); }, CHECK_TIMEOUT_MS) : 0;
    fetch("/healthz", { cache: "no-store", credentials: "omit", signal: ctl ? ctl.signal : undefined })
      .then(function (r) { if (r.ok) reload(); }, function () {})
      .then(function () {
        clearTimeout(stop);
        later();
      });
  }

  if (tries >= TRIES) return;  // the page kept failing: it waits for Try again
  window.addEventListener("online", reload);
  later();
})();
