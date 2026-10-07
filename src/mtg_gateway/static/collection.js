/* Collection page (the member's Archidekt Collection). Works without this file (every control is a form); with it the plus, minus and
   remove buttons update a row in place through the JSON API instead of reloading, and the filter
   selects apply on change. The shared deck.js handles the autocomplete of the add box. */
(function () {
  "use strict";
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var csrfInput = document.querySelector("input[name=csrf]");
  var CSRF = csrfInput ? csrfInput.value : "";
  var list = document.getElementById("cards");
  var totals = document.getElementById("totals");

  function say(text) {
    var live = document.getElementById("live");
    if (live) live.textContent = text;
  }
  function showTotals(delta) {
    if (!totals) return;
    var b = totals.querySelector("b");
    if (b) b.textContent = Math.max(0, (parseInt(b.textContent, 10) || 0) + delta);
  }
  function request(method, url, body) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (d) { d.status = r.status; return d; }); });
  }

  if (list) {
    list.addEventListener("click", function (e) {
      var btn = e.target.closest("button[name=action]");
      if (!btn) return;
      var form = btn.form;
      var item = btn.closest("[data-id]");
      if (!form || !item) return;
      var id = item.getAttribute("data-id");
      var out = form.querySelector("output");
      var current = out ? parseInt(out.textContent, 10) || 0 : 0;
      var action = btn.value;
      e.preventDefault();
      var p;
      if (action === "remove") p = request("DELETE", "/collection/api/rows/" + encodeURIComponent(id));
      else if (action === "inc") p = request("POST", "/collection/api/rows/" + encodeURIComponent(id), { quantity: current + 1 });
      else if (action === "dec") p = request("POST", "/collection/api/rows/" + encodeURIComponent(id), { quantity: Math.max(0, current - 1) });
      else { form.submit(); return; }
      $$("button", item).forEach(function (b) { b.disabled = true; });
      p.then(function (d) {
        if (!d.ok) { say(d.message || "That did not work."); $$("button", item).forEach(function (b) { b.disabled = false; }); return; }
        var name = item.querySelector(".name") ? item.querySelector(".name").textContent : "Card";
        if (action === "remove" || !d.row || d.row.quantity === 0) {
          showTotals(-1);
          item.remove();
          say(name + " removed from your collection.");
          if (list.children.length === 0) location.reload();
          return;
        }
        $$("output", item).forEach(function (o) { o.textContent = d.row.quantity; });
        $$(".qty:not(form)", item).forEach(function (q) { q.textContent = d.row.quantity; });
        $$("button", item).forEach(function (b) { b.disabled = false; });
        say(name + ": " + d.row.quantity + (d.row.quantity === 1 ? " copy" : " copies"));
      }).catch(function () {
        say("No connection; reloading the page.");
        form.submit();
      });
    });
  }

  // the row details menu saves through the JSON API and updates the row's badges in place
  if (list) {
    list.addEventListener("submit", function (e) {
      var form = e.target.closest("form.detailsform");
      if (!form) return;
      e.preventDefault();
      var id = form.getAttribute("data-id");
      var item = form.closest("[data-id]");
      var btn = form.querySelector("button");
      var body = {
        finish: form.elements.finish.value,
        condition: form.elements.condition.value,
        language: form.elements.language.value,
        purchase_price: form.elements.purchase_price.value
      };
      btn.disabled = true;
      request("POST", "/collection/api/rows/" + encodeURIComponent(id), body).then(function (d) {
        btn.disabled = false;
        if (!d.ok) { say(d.message || "That did not work."); return; }
        var row = d.row || {};
        var cond = item ? item.querySelector(".cond") : null;
        if (cond) { if (row.condition) cond.textContent = row.condition; else cond.remove(); }
        var fin = item ? item.querySelector(".finish") : null;
        var label = row.finish === "foil" ? "Foil" : row.finish === "etched" ? "Etched" : "";
        if (fin) { if (label) { fin.textContent = label.charAt(0); fin.title = label; } else fin.remove(); }
        var dd = form.closest("details");
        if (dd) dd.removeAttribute("open");
        say((row.name || "Card") + ": details saved.");
      }).catch(function () { btn.disabled = false; say("No connection; try again."); });
    });
  }

  var listform = document.getElementById("listform");
  if (listform) {
    $$("select", listform).forEach(function (sel) {
      sel.addEventListener("change", function () { listform.requestSubmit ? listform.requestSubmit() : listform.submit(); });
    });
  }
})();
