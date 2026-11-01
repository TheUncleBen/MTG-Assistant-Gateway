"use strict";
// Home and a deck's page: while a test is still going, its running mark (data-running-live, with the
// reports it shows) is asked for again every 5 seconds at /history/running: the step it is on, the
// games Forge has played and the time so far. Once those tests have ended the mark says so, with the
// way to the report, and the page stops asking. Only what changed is redrawn, so the spinner keeps
// turning and the deck page's live words are announced only when the step or a game changes. No
// asking while the tab is hidden; it stops after a failed fetch or after five hours.
(function () {
  var INTERVAL = 5000;
  var LIMIT = 5 * 3600 * 1000;
  var started = Date.now();
  var timer = null;

  function mark() { return document.querySelector("[data-running-live]"); }

  function sameShell(a, b) {
    if (a.nodeType !== 1 || b.nodeType !== 1 || a.tagName !== b.tagName) return false;
    if (a.attributes.length !== b.attributes.length) return false;
    for (var i = 0; i < b.attributes.length; i++) {
      if (a.getAttribute(b.attributes[i].name) !== b.attributes[i].value) return false;
    }
    return true;
  }

  function patch(target, nodes) {
    var old = target.childNodes;
    if (old.length !== nodes.length) {
      target.replaceChildren.apply(target, nodes.map(function (n) { return document.importNode(n, true); }));
      return;
    }
    for (var i = 0; i < nodes.length; i++) {
      if (old[i].isEqualNode(nodes[i])) continue;
      if (sameShell(old[i], nodes[i])) patch(old[i], Array.prototype.slice.call(nodes[i].childNodes));
      else target.replaceChild(document.importNode(nodes[i], true), old[i]);
    }
  }

  function tick() {
    timer = null;
    var el = mark();
    if (!el || Date.now() - started > LIMIT || document.hidden) return;
    var q = "shape=" + encodeURIComponent(el.getAttribute("data-running-live") || "home") +
      "&ids=" + encodeURIComponent(el.getAttribute("data-ids") || "");
    var deck = el.getAttribute("data-deck-id");
    if (deck) q += "&deck_id=" + encodeURIComponent(deck);
    fetch("/history/running?" + q, {
      credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" }
    })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (data) {
        if (!data || data.ok !== true || typeof data.html !== "string") return;
        var doc = new DOMParser().parseFromString("<div>" + data.html + "</div>", "text/html");
        var fresh = doc.body.firstChild.firstElementChild;
        if (!fresh) { el.remove(); return; }
        if (sameShell(el, fresh)) patch(el, Array.prototype.slice.call(fresh.childNodes));
        else el.replaceWith(document.importNode(fresh, true));
        if (data.live) schedule();
      })
      .catch(function () { /* signed out or offline: leave the mark as it is */ });
  }

  function schedule() { if (!timer) timer = setTimeout(tick, INTERVAL); }

  document.addEventListener("visibilitychange", function () { if (!document.hidden && mark()) schedule(); });
  if (mark()) schedule();
})();
