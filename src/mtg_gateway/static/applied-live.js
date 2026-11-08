"use strict";
// A page while a change is still applying: each element that carries data-applied-live and an id
// (the Applied page's result card; the review page's panel body and its list row) is asked for
// again every 3 s, by fetching the same page, and only the children that changed are swapped in,
// so nothing reloads and the scroll position stays. An element stops once its fresh copy no
// longer says it is live; the polling stops when none is left, after a failed fetch, while the
// tab is hidden, or after an hour. An outcome with a guarded form loads the page again instead.
// Without scripts the page's <noscript> meta refresh reloads it.
(function () {
  var INTERVAL = 3000;
  var LIMIT = 3600 * 1000;
  var started = Date.now();
  var timer = null;

  function lives() { return Array.prototype.slice.call(document.querySelectorAll("[data-applied-live][id]")); }

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
    var cards = lives();
    if (!cards.length || Date.now() - started > LIMIT) return;
    if (document.visibilityState !== "visible") { schedule(); return; }
    fetch(location.href, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" }, cache: "no-store" })
      .then(function (r) {
        if (!r.ok || r.redirected) throw new Error("gone");
        return r.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        // A guarded form (Try again, Propose the rest) is unlocked by the click guard as the page
        // loads, so an outcome that brings one loads the page instead of swapping it in.
        if (cards.some(function (card) {
          var fresh = doc.getElementById(card.id);
          return fresh && fresh.querySelector("form.guarded");
        })) { location.reload(); return; }
        var found = 0;
        cards.forEach(function (card) {
          var fresh = doc.getElementById(card.id);
          if (!fresh) { card.removeAttribute("data-applied-live"); return; }
          found++;
          patch(card, Array.prototype.slice.call(fresh.childNodes));
          if (!fresh.hasAttribute("data-applied-live")) card.removeAttribute("data-applied-live");
        });
        if (!found) throw new Error("gone");
        if (doc.title) document.title = doc.title;
        if (lives().length) schedule();
      })
      .catch(function () { /* the next load of the page shows the rest */ });
  }

  function schedule() { if (!timer) timer = setTimeout(poll, INTERVAL); }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && lives().length && !timer) poll();
  });
  if (lives().length) schedule();
})();
