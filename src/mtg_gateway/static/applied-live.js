"use strict";
// The Applied page while a batch is still applying (the result card carries data-applied-live):
// the same page is asked for every 3 s and only the rows that changed are swapped in, so nothing
// reloads and the scroll position stays. It stops once the fresh card no longer says it is live,
// after a failed fetch, while the tab is hidden, or after an hour. Without scripts the page's
// <noscript> meta refresh reloads it instead.
(function () {
  var INTERVAL = 3000;
  var LIMIT = 3600 * 1000;
  var started = Date.now();
  var timer = null;

  function live() { return document.querySelector("#applied-result[data-applied-live]"); }

  function patch(target, nodes) {
    var old = target.childNodes;
    if (old.length !== nodes.length) {
      target.replaceChildren.apply(target, nodes.map(function (n) { return document.importNode(n, true); }));
      return;
    }
    for (var i = 0; i < nodes.length; i++) {
      if (!old[i].isEqualNode(nodes[i])) target.replaceChild(document.importNode(nodes[i], true), old[i]);
    }
  }

  function poll() {
    timer = null;
    var card = live();
    if (!card || Date.now() - started > LIMIT) return;
    if (document.visibilityState !== "visible") { schedule(); return; }
    fetch(location.href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" }, cache: "no-store" })
      .then(function (r) {
        if (!r.ok || r.redirected) throw new Error("gone");
        return r.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.getElementById("applied-result");
        if (!fresh) throw new Error("gone");
        patch(card, Array.prototype.slice.call(fresh.childNodes));
        if (doc.title) document.title = doc.title;
        if (!fresh.hasAttribute("data-applied-live")) { card.removeAttribute("data-applied-live"); return; }
        schedule();
      })
      .catch(function () { /* the next load of the page shows the rest */ });
  }

  function schedule() { if (!timer) timer = setTimeout(poll, INTERVAL); }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && live() && !timer) poll();
  });
  if (live()) schedule();
})();
