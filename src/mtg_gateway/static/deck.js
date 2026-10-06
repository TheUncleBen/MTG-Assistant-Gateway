/* Deck page and deck list helpers. The pages work without this file (every control is a form);
   this only makes them feel like Archidekt: the toolbar's View as / Group by / Sort by selects
   apply on change, the local filter narrows the cards as you type, Quick add autocompletes card
   names through the gateway's own Scryfall search, and an open menu closes on an outside click. */
(function () {
  "use strict";
  var form = document.getElementById("viewform") || document.getElementById("listform");
  if (form) {
    Array.prototype.forEach.call(form.querySelectorAll("select"), function (sel) {
      sel.addEventListener("change", function () { form.submit(); });
    });
  }
  // live local filter over the rendered cards (rows and image cards carry data-name)
  var q = document.getElementById("q");
  var cards = document.getElementById("cards");
  if (q && cards) {
    var items = cards.querySelectorAll("[data-name]");
    var stacks = cards.querySelectorAll(".stack");
    var apply = function () {
      var needle = q.value.trim().toLowerCase();
      Array.prototype.forEach.call(items, function (el) {
        var hit = !needle || el.getAttribute("data-name").indexOf(needle) >= 0;
        el.style.display = hit ? "" : "none";
      });
      Array.prototype.forEach.call(stacks, function (st) {
        var any = Array.prototype.some.call(st.querySelectorAll("[data-name]"), function (el) { return el.style.display !== "none"; });
        st.style.display = any ? "" : "none";
      });
    };
    q.addEventListener("input", apply);
    q.form && q.form.addEventListener("submit", function (ev) { if (document.activeElement === q) { ev.preventDefault(); apply(); } });
  }
  // quick add: card name suggestions
  var quick = document.getElementById("quick");
  var list = document.getElementById("cardnames");
  if (quick && list) {
    var timer = null;
    quick.addEventListener("input", function () {
      clearTimeout(timer);
      var v = quick.value.trim();
      if (v.length < 3) return;
      timer = setTimeout(function () {
        fetch("/scan/api/search?q=" + encodeURIComponent(v), { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : { names: [] }; })
          .then(function (d) {
            list.textContent = "";
            (d.names || []).slice(0, 12).forEach(function (n) {
              var o = document.createElement("option");
              o.value = n;
              list.appendChild(o);
            });
          })
          .catch(function () {});
      }, 250);
    });
  }
  // menus: one open at a time, close on outside click or Escape
  document.addEventListener("click", function (ev) {
    Array.prototype.forEach.call(document.querySelectorAll("details.dd[open]"), function (d) {
      if (!d.contains(ev.target)) d.removeAttribute("open");
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      Array.prototype.forEach.call(document.querySelectorAll("details.dd[open]"), function (d) { d.removeAttribute("open"); });
    }
  });
})();
