/* Preconstructed decks (/precons). Works without this file: the name box and the Set drop-down
   send the form, Show all is the whole set, Load more sets is a link. With it: a set's Show all
   appears only when a row of its tiles is really hidden at this width; the name box narrows the
   list as you type (the server answers from /precons/api/sets); Load more appends the next sets.
   The shared searchpanel.js sends the form when a drop-down or the view changes. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var form = $("#searchform");
  var main = $("main");
  if (!form || !main) return;

  function columns(list) {
    if (list.classList.contains("list")) return 3;
    var cols = getComputedStyle(list).gridTemplateColumns.split(" ").filter(Boolean).length;
    return cols || 1;
  }
  // Show all only when a row is hidden: more tiles than the row holds
  function fit() {
    $$(".pre-grp").forEach(function (g) {
      var list = $("ul.decklist", g), b = $(".pre-all", g);
      if (!list || !b) return;
      var shown = $$("li:not([hidden])", list).length;
      b.hidden = !(g.classList.contains("open") || shown > columns(list));
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".pre-all") : null;
    if (!b) return;
    var g = b.closest(".pre-grp");
    var open = !g.classList.contains("open");
    g.classList.toggle("open", open);
    b.setAttribute("aria-expanded", open ? "true" : "false");
    b.textContent = open ? "Show fewer" : "Show all " + (b.getAttribute("data-n") || "");
    if (!open) g.scrollIntoView({ block: "nearest" });
  });
  window.addEventListener("resize", fit);
  fit();

  // the name box narrows the list as you type: the server answers with the groups that match
  var q = $("#pre-q", form), count = $("#pre-count"), timer = null, ctrl = null;
  function area() { return $(".pre-grp") ? $(".pre-grp").parentNode : main; }
  function replace(d) {
    var old = $$(".pre-grp, .pre-none, .loadmore", main);
    var where = old.length ? old[0] : null;
    var tmp = document.createElement("div");
    tmp.innerHTML = d.html || "";
    old.forEach(function (n) { n.remove(); });
    var parent = where ? area() : main;
    while (tmp.firstChild) parent.appendChild(tmp.firstChild);
    if (count && d.count) count.textContent = d.count;
    fit();
  }
  function params() {
    var out = new URLSearchParams(new FormData(form));
    out.delete("sets"); out.delete("offset");
    return out;
  }
  function refetch() {
    if (ctrl) ctrl.abort();
    ctrl = window.AbortController ? new AbortController() : null;
    var p = params();
    fetch("/precons/api/sets?" + p.toString(), { credentials: "same-origin", headers: { Accept: "application/json" }, signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        main.removeAttribute("aria-busy");
        if (!d || !d.ok) return;
        replace(d);
        var url = "/precons" + (p.toString() ? "?" + p.toString() : "");
        if (window.history && history.replaceState) history.replaceState(null, "", url);
      })
      .catch(function (e) {
        if (!e || e.name !== "AbortError") main.removeAttribute("aria-busy");
        /* the next keystroke, or Enter, asks again */
      });
  }
  if (q) q.addEventListener("input", function () {
    clearTimeout(timer);
    // the pending look at once (the sets dim, theme.py); the ask waits for the pause in typing
    main.setAttribute("aria-busy", "true");
    timer = setTimeout(refetch, 250);
  });

  // Load more sets (or decks): the next ones, appended
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".loadmore [data-load-more]") : null;
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    var wrap = a.closest(".loadmore");
    var href = a.getAttribute("href").replace(/^\/precons(\?|$)/, "/precons/api/sets$1");
    a.setAttribute("aria-busy", "true");
    fetch(href, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) throw new Error("failed");
        var tmp = document.createElement("div");
        tmp.innerHTML = d.html || "";
        var more = $(".loadmore", tmp);
        if (more) more.remove();
        var before = wrap;
        var first = tmp.firstElementChild;
        // the sets already on the page stay; only the new ones (after the ones shown) come in
        var have = $$(".pre-grp", main).length;
        var groups = $$(".pre-grp", tmp);
        if (groups.length > have) {
          groups.slice(have).forEach(function (g) { before.parentNode.insertBefore(g, before); });
        } else if (first && first.matches("section.pre-grp")) {
          // sorted by name: one grid, the next decks
          var list = $(".pre-grp ul.decklist", main), add = $("ul.decklist", first);
          if (list && add) $$("li", add).forEach(function (li) { list.appendChild(li); });
        }
        if (more) wrap.parentNode.replaceChild(more, wrap); else wrap.remove();
        fit();
      })
      .catch(function () {
        a.removeAttribute("aria-busy");
        var p = $("p", wrap);
        if (!p) { p = document.createElement("p"); p.className = "muted"; wrap.appendChild(p); }
        p.textContent = "The next sets could not be loaded. Try again.";
      });
  });
})();
