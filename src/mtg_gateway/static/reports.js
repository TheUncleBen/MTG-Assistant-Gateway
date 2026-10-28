/* Reports: Load more reads the next page (the button's own link) and adds its rows under the
   ones shown, then takes that page's Load more (or its "All N reports shown.") in place of this
   one. Without this script the link opens the next page. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var busy = false;
  function failed(a) {
    a.removeAttribute("aria-busy");
    a.removeAttribute("aria-disabled");
    var Menu = window.MtgMenu;
    if (Menu && Menu.toast) Menu.toast("No connection; the reports could not be read. Try again.", { error: true });
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".reports-page .loadmore [data-load-more]") : null;
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button || typeof fetch !== "function") return;
    e.preventDefault();
    if (busy) return;
    var table = $(".reports-page .rp-t"), wrap = a.closest(".loadmore");
    if (!table || !wrap) return;
    busy = true;
    a.setAttribute("aria-busy", "true");
    a.setAttribute("aria-disabled", "true");
    fetch(a.href, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var rows = $$(".reports-page .rp-t .tr:not(.th)", doc), next = $(".reports-page .loadmore", doc);
        if (!rows.length && !next) throw new Error("no rows");
        var first = null;
        rows.forEach(function (tr) { var own = document.importNode(tr, true); if (!first) first = own; table.appendChild(own); });
        if (next) wrap.replaceWith(document.importNode(next, true)); else wrap.remove();
        document.dispatchEvent(new CustomEvent("rows:loaded", { bubbles: true }));
        var k = first && $(".kebab", first);
        if (k) k.focus({ preventScroll: true }); else if (first) first.scrollIntoView({ block: "nearest" });
      })
      .catch(function () { failed(a); })
      .then(function () { busy = false; });
  });
})();
