/* Load more under History, Proposals and My activity (Standard 13): the button's own link is the
   next page; this reads it and adds its rows to the table named by the button's data-into, then
   takes that page's Load more (or its "All N shown.") in place of this one. A day or deck heading
   the new page starts with, which the table already ends under, is not drawn twice. Without this
   script the link opens the next page. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var busy = false;
  function heading(el) { return el && el.classList && (el.classList.contains("daygroup") || el.classList.contains("group")); }
  function lastHeading(table) {
    var kids = table.children;
    for (var i = kids.length - 1; i >= 0; i--) if (heading(kids[i])) return kids[i];
    return null;
  }
  function failed(a) {
    a.removeAttribute("aria-busy");
    a.removeAttribute("aria-disabled");
    var Menu = window.MtgMenu;
    if (Menu && Menu.toast) Menu.toast("No connection; nothing more could be read. Try again.", { error: true });
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
      .then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
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
      })
      .catch(function () { failed(a); })
      .then(function () { busy = false; });
  });
})();
