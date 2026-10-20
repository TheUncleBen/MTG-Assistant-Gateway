/* Fills the deck-list placeholders and filters My decks in the browser.
   Home and My decks render at once when the member's deck list is still being read from Archidekt
   (a cold start): the decks area carries data-decks-src, the address of /api/decks/mine with the
   page's own filters, and the answer brings the HTML the page would have rendered itself (one
   renderer, server side: the folder's breadcrumb, its Folders and Decks areas) plus the count for
   the results bar. On My decks the filter panel (static/searchpanel.js) asks the same address as
   its boxes change: the name as you type, a colour or a format as you pick it, the sort, its
   direction and the view at once; the list area is replaced and the address follows, so a reload
   or a shared link shows the same. Without this script the placeholder stays and the panel's
   Filter button sends the form; a reload shows the list. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var boxes = Array.prototype.slice.call(document.querySelectorAll("[data-decks-src]"));
  if (typeof fetch !== "function") return;

  function failed(box) {
    box.removeAttribute("aria-busy");
    box.textContent = "";
    var p = document.createElement("p");
    p.className = "notice error";
    p.textContent = "Your decks could not be loaded. ";
    var a = document.createElement("a");
    a.href = window.location.pathname + window.location.search;
    a.textContent = "Reload";
    p.appendChild(a);
    box.appendChild(p);
  }

  function fillToolbar(data) {
    var total = $("#decks-total");
    if (!total) return;
    if (typeof data.count_text === "string") total.textContent = data.count_text;
    else if (typeof data.total === "number") total.textContent = data.total + (data.total === 1 ? " deck" : " decks");
    total.hidden = data.total === null; // the list couldn't be read: no count
  }

  // Every load of a box is numbered, the page's own first load included, and only the latest
  // one may draw: a slow answer landing after a newer one (the matches for "ur" after those for
  // "urza", or the unfiltered first load after a colour pick) is dropped, and its request is
  // cut off where the browser can do that.
  var seq = 0, inflight = null;
  function load(box, src) {
    var mine = ++seq;
    if (inflight) { try { inflight.abort(); } catch (e) { /* an older browser */ } }
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    inflight = ctl;
    var opts = { credentials: "same-origin", headers: { Accept: "application/json" } };
    if (ctl) opts.signal = ctl.signal;
    return fetch(src, opts)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(String(r.status))); })
      .then(function (data) {
        if (!data || typeof data.html !== "string") throw new Error("bad answer");
        if (mine !== seq) return null;  // a newer load is on its way: this one says nothing
        box.removeAttribute("aria-busy");
        box.innerHTML = data.html; // rendered by the gateway, as the page itself would have
        fillToolbar(data);
        box.dispatchEvent(new CustomEvent("decks:loaded", { bubbles: true, detail: data }));
        return data;
      }, function (err) {
        if (mine !== seq) return null;  // cut off by a newer load, or too late to matter
        throw err;
      });
  }

  boxes.forEach(function (box) {
    load(box, box.getAttribute("data-decks-src")).catch(function () { failed(box); });
  });

  // -- My decks: the filter panel refreshes the list in place --------------------------------------
  var form = $("#listform[data-live]"), list = $("#decks-list");
  if (!form || !list) return;
  var timer = null;
  function params() {
    var fd = new FormData(form), out = new URLSearchParams();
    fd.forEach(function (v, k) { if (String(v) !== "") out.append(k, String(v)); });
    return out;
  }
  function refresh() {
    var p = params();
    var api = new URLSearchParams(p);
    api.set("shape", "list");
    list.setAttribute("aria-busy", "true");
    load(list, "/api/decks/mine?" + api.toString())
      .then(function (data) {
        if (!data) return;  // an older load: a newer one draws and sets the address
        var q = p.toString();
        history.replaceState(null, "", "/decks" + (q ? "?" + q : ""));
      })
      .catch(function () { failed(list); });
  }
  function soon() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 250);
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearTimeout(timer);
    refresh();
    if (window.MtgSearchPanel) window.MtgSearchPanel.closeSheet();
  });
  form.addEventListener("input", function (e) {
    var t = e.target;
    if (t && t.type === "search") soon();
  });
  form.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || t.closest(".sortcw")) return; // the phone's combined sort sets the real boxes, which change
    if (t.type === "search") return;
    if (t.type === "checkbox" || t.type === "radio" || t.tagName === "SELECT") { clearTimeout(timer); refresh(); }
  });
})();
