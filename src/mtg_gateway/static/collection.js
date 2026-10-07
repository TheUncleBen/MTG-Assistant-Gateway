/* Collection page. Works without this file (every control is a form); with it the plus, minus and
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
  function showTotals(t) {
    if (!totals || !t) return;
    var bs = totals.querySelectorAll("b");
    if (bs[0]) bs[0].textContent = t.cards;
    if (bs[1]) bs[1].textContent = t.distinct;
    if (bs[2]) bs[2].textContent = t.sets ? t.sets.length : bs[2].textContent;
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
        showTotals(d.totals);
        var name = item.querySelector(".name") ? item.querySelector(".name").textContent : "Card";
        if (action === "remove" || !d.row || d.row.quantity === 0) {
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

  var listform = document.getElementById("listform");
  if (listform) {
    $$("select", listform).forEach(function (sel) {
      sel.addEventListener("change", function () { listform.requestSubmit ? listform.requestSubmit() : listform.submit(); });
    });
  }
})();
