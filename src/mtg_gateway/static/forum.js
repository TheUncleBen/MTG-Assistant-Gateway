/* The Forum pages (forum_page.py): Load more on a section's thread table (a button that appends
   the next page's rows; without scripts the same link opens the next page), and the thread rows'
   menu by right-click, press and hold, or the ContextMenu key (R-228: static/cardmenu.js).
   The thread page's replies are the deck page's comment panel (static/social.js). */
(function () {
  "use strict";
  var Menu = window.MtgMenu;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  var toast = Menu ? Menu.toast : function (t) { window.alert(t); };

  // -- Load more ---------------------------------------------------------------------------------
  function loadRows(a) {
    var table = $(".fm .fm-threads"), wrap = a.closest(".loadmore");
    if (!table || !Menu) return;
    a.setAttribute("aria-busy", "true");
    a.setAttribute("aria-disabled", "true");
    Menu.getJSON(a.getAttribute("href")).then(function (d) {
      if (!d.ok) throw new Error(d.message || "");
      var tmp = el("div"), first = null;
      tmp.innerHTML = d.html;
      while (tmp.firstChild) {
        if (!first && tmp.firstChild.nodeType === 1) first = tmp.firstChild;
        table.appendChild(tmp.firstChild);
      }
      wrap.textContent = "";
      if (d.has_more) {
        var next = el("a", "btn btn-plain", d.label);
        next.href = d.next; next.rel = "next"; next.setAttribute("data-load-more", "");
        wrap.appendChild(next);
      } else {
        wrap.appendChild(el("p", "muted", d.done));
      }
      if (first && first.focus) first.focus({ preventScroll: true });
    }).catch(function () {
      a.removeAttribute("aria-busy");
      a.removeAttribute("aria-disabled");
      toast("No connection; the threads could not be read. Try again.", { error: true });
    });
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".fm .loadmore [data-load-more]") : null;
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    if (a.getAttribute("aria-disabled") === "true") return;
    loadRows(a);
  });

  // -- the thread rows' menu -----------------------------------------------------------------------
  var table = $(".fm .fm-threads");
  if (table && Menu) {
    var rowOf = function (t) { return t && t.closest ? t.closest("a.tr[data-id]") : null; };
    Menu.bind(table, {
      itemOf: rowOf,
      skip: "button, input, select, textarea, summary, form, label",
      open: function (row, x, y) {
        var title = ($(".t", row) || row).textContent.trim();
        var href = row.getAttribute("href"), id = row.getAttribute("data-id");
        var items = [
          Menu.link("Open thread", "comment", href),
          Menu.link("Open in a new tab", "external", href, { ext: true }),
          Menu.item("Copy link", "link", function () {
            Menu.copyAndSay(location.origin + href, "Copied the thread's link.");
          }),
          Menu.link("Open on Archidekt", "external", "https://archidekt.com/forum/thread/" + id, { ext: true })
        ];
        Menu.open({ head: title, label: "Menu for " + title, items: items, focusBack: row }, x, y);
      }
    });
  }
})();
