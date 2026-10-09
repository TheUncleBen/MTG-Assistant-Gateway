/* Fills the deck-list placeholders. Home and My decks render at once when the member's deck list
   is still being read from Archidekt (a cold start): the decks area carries data-decks-src, the
   address of /api/decks/mine with the page's own filters, and the answer brings the HTML the page
   would have rendered itself (one renderer, server side) plus the total and the folder names for
   the toolbar. Without this script the placeholder stays; a reload shows the list. */
(function () {
  "use strict";
  var boxes = Array.prototype.slice.call(document.querySelectorAll("[data-decks-src]"));
  if (!boxes.length || typeof fetch !== "function") return;

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
    var total = document.getElementById("decks-total");
    if (total && typeof data.total === "number") total.textContent = "Total decks: " + data.total;
    var field = document.getElementById("folder-field");
    var sel = document.getElementById("f-folder");
    if (sel && Array.isArray(data.folders)) {
      var current = sel.value;
      while (sel.options.length > 1) sel.remove(1);
      data.folders.forEach(function (name) {
        var opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        if (name === current) opt.selected = true;
        sel.appendChild(opt);
      });
      if (field) {
        if (data.folders.length) field.removeAttribute("hidden"); else field.setAttribute("hidden", "");
      }
    }
  }

  boxes.forEach(function (box) {
    var src = box.getAttribute("data-decks-src");
    fetch(src, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(String(r.status))); })
      .then(function (data) {
        if (!data || typeof data.html !== "string") throw new Error("bad answer");
        box.removeAttribute("aria-busy");
        box.innerHTML = data.html; // rendered by the gateway, as the page itself would have
        fillToolbar(data);
        box.dispatchEvent(new CustomEvent("decks:loaded", { bubbles: true, detail: data }));
      })
      .catch(function () { failed(box); });
  });
})();
