/* Load more (Standard 13), the shared part: window.MtgLoadMore, and the rows under History,
   Proposals and My activity.

   - Every Load more on the gateway (this script's own, deck search's and a profile's in
     static/search.js, the Binder's in static/collection.js, the forum's in static/forum.js, the
     precons' in static/precons.js) tells of each page it added with MtgLoadMore.loaded(), and of
     one it could not read with MtgLoadMore.failed(x), which says so in the one wording
     (static/fetchjson.js: signed out, no connection, or a plain "nothing more could be loaded")
     as a toast, or returns the words for a line of the page's own (audit F-5: five wordings).
   - The pages added are kept on the history entry (audit F-4: Back, Forward or a reload showed
     the first page again), with where the page was scrolled to: a page loaded again on that
     entry presses its Load more that many times, then scrolls back to where it was. A list drawn
     anew (a search, a filter) starts over (MtgLoadMore.reset()).
   - This script's own rows: the button's own link is the next page; this reads it and adds its
     rows to the table named by the button's data-into, then takes that page's Load more (or its
     "All N shown.") in place of this one. A day or deck heading the new page starts with, which
     the table already ends under, is not drawn twice. Without this script the link opens the
     next page. */
(function () {
  "use strict";
  if (window.MtgLoadMore) return;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var BUTTON = ".loadmore [data-load-more], #s-more [data-more]";
  var MORE_FAILED = "nothing more could be loaded";

  // -- the pages added, on the history entry ----------------------------------------------------
  var count = 0, waiting = null, scrollTimer = null;
  function state() { return (history.state && typeof history.state === "object") ? history.state : {}; }
  function save(extra) {
    var s = state(), next = {};
    Object.keys(s).forEach(function (k) { next[k] = s[k]; });
    next.mtgmore = count;
    if (extra) Object.keys(extra).forEach(function (k) { next[k] = extra[k]; });
    try { history.replaceState(next, "", location.href); } catch (e) { /* the entry stays */ }
  }
  function loaded() {
    count += 1;
    save();
    if (waiting) { var go = waiting; waiting = null; go(); }
  }
  // the one wording for a page that could not be read (static/fetchjson.js); shown as a toast
  // where the menus' toast is on the page, else returned for the page's own line
  function failed(x, opts) {
    waiting = null;
    var Fetch = window.MtgFetch, Menu = window.MtgMenu;
    var said = Fetch ? Fetch.problem(x, MORE_FAILED) : { text: "No connection; " + MORE_FAILED + ".", actions: [] };
    if (!/Try again\.$/.test(said.text)) said.text += " Try again.";
    if (Menu && Menu.toast && !(opts && opts.inline)) Menu.toast(said.text, { error: true, actions: said.actions });
    return said;
  }
  function reset() { count = 0; save(); }
  // a page loaded on an entry with pages added: its Load more is pressed that many times, each
  // press once the one before has landed, then the page scrolls to where it was
  function again() {
    var want = state().mtgmore || 0, top = state().mtgscroll;
    count = 0;
    function step() {
      if (count >= want) { if (typeof top === "number") window.scrollTo(0, top); return; }
      var b = $(BUTTON);
      if (!b) return;
      waiting = step;
      b.click();
    }
    if (want > 0) step();
  }
  window.addEventListener("scroll", function () {
    if (!count) return;
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function () { save({ mtgscroll: window.scrollY }); }, 250);
  }, { passive: true });
  window.MtgLoadMore = { loaded: loaded, failed: failed, reset: reset, count: function () { return count; } };

  // -- the rows under History, Proposals and My activity ----------------------------------------
  var busy = false;
  function heading(el) { return el && el.classList && (el.classList.contains("daygroup") || el.classList.contains("group")); }
  function lastHeading(table) {
    var kids = table.children;
    for (var i = kids.length - 1; i >= 0; i--) if (heading(kids[i])) return kids[i];
    return null;
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".loadmore [data-load-more][data-into]") : null;
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button || typeof fetch !== "function") return;
    var into = a.getAttribute("data-into"), table = $(into), wrap = a.closest(".loadmore");
    if (!table || !wrap) return;
    e.preventDefault();
    if (busy) return;
    busy = true;
    a.setAttribute("aria-busy", "true");
    a.setAttribute("aria-disabled", "true");
    fetch(a.href, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject({ status: r.status }); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var src = $(into, doc), next = $(".loadmore", doc);
        if (!src && !next) throw new Error("no rows");
        var rows = src ? Array.prototype.slice.call(src.children).filter(function (r) { return !r.classList.contains("th"); }) : [];
        var last = lastHeading(table);
        var plain = function (el) { return el.textContent.replace(/\s*·\s*[\d,]+\s*$/, "").trim(); };
        if (rows.length && heading(rows[0]) && last && plain(last) === plain(rows[0])) rows.shift();
        var first = null;
        rows.forEach(function (r) { var own = document.importNode(r, true); if (!first) first = own; table.appendChild(own); });
        if (next) wrap.replaceWith(document.importNode(next, true)); else wrap.remove();
        document.dispatchEvent(new CustomEvent("rows:loaded", { bubbles: true }));
        var focus = first && (first.querySelector("a, button") || null);
        if (focus) focus.focus({ preventScroll: true });
        if (first) first.scrollIntoView({ block: "nearest" });
        loaded();
      })
      .catch(function (err) {
        a.removeAttribute("aria-busy");
        a.removeAttribute("aria-disabled");
        // an answer with a status (signed out, an error page), no answer at all, or a page
        // without the rows (nothing of the browser's own words reaches the screen)
        failed(err && err.status ? err : err instanceof TypeError && window.MtgFetch ? window.MtgFetch.noConnectionError(MORE_FAILED) : null);
      })
      .then(function () { busy = false; });
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", again); else again();
})();
