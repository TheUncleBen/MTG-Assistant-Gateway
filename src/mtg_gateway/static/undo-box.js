"use strict";
// Undo (the review page and the Applied page): the Undo button's form asks the gateway what Undo
// would put back and shows the answer in a box (the "Undo this change?" page's card: the deck,
// the warning when more than the apply goes back, the list), with Not now and the one Undo button.
// The button unlocks a moment after the box opens, as the page's click guard would. Anything
// unexpected (no scripts, a refused fetch, a page without the card) leaves the form to submit as
// it always did, so the "Undo this change?" page stays the answer.
(function () {
  var UNLOCK_MS = 1100; // past the gateway's minimum form age (clickguard.MIN_FORM_AGE, 0.75 s)

  function strip(card) {
    // the fetched card without the page's own buttons, hint and script: the box has its own
    ["form.guarded .choice, form.guarded .guard-hint, noscript, script, a.btn", "p:empty"].forEach(function (sel) {
      var gone = card.querySelectorAll(sel);
      for (var i = 0; i < gone.length; i++) gone[i].parentNode.removeChild(gone[i]);
    });
    var h = card.querySelector("h2");
    if (h) { var p = document.createElement("p"); p.className = "deck"; p.innerHTML = "<strong></strong>"; p.firstChild.textContent = h.textContent; h.parentNode.replaceChild(p, h); }
    card.removeAttribute("id");
    card.className = "undo-look";
    return card;
  }

  function open(form) {
    var body = new URLSearchParams(new FormData(form));
    var opener = form.querySelector("button");
    var busyBefore = opener && opener.disabled;
    if (opener) opener.disabled = true;
    // the attribute: form.action is the hidden <input name="action"> of this form
    fetch(form.getAttribute("action") || location.href, {
      method: "POST", body: body, credentials: "same-origin",
      headers: { "X-Requested-With": "fetch", "Content-Type": "application/x-www-form-urlencoded" }
    }).then(function (r) {
      if (!r.ok || r.redirected) throw new Error("no preview");
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var card = doc.getElementById("undo-confirm");
      if (!card || !window.MtgBox) throw new Error("no preview");
      var node = document.importNode(strip(card), true);
      var guarded = node.querySelector("form.guarded");
      var actions = [window.MtgBox.button(guarded ? "Not now" : "Close", "btn", null, { cancel: true })];
      if (guarded) {
        var ok = window.MtgBox.button("Undo: put the deck back", "primary", function () {
          var act = document.createElement("input");
          act.type = "hidden"; act.name = "action"; act.value = "undo";
          guarded.appendChild(act);
          ok.disabled = true;
          guarded.submit();
        });
        ok.disabled = true;
        ok.setAttribute("data-gate-off", "");
        setTimeout(function () { ok.disabled = false; ok.removeAttribute("data-gate-off"); }, UNLOCK_MS);
        actions.push(ok);
      }
      window.MtgBox.open({ title: "Undo this change?", body: node, actions: actions, wide: true, returnFocus: opener });
    }).catch(function () {
      form.removeAttribute("data-undo-box");
      form.submit();
    }).finally(function () {
      if (opener) opener.disabled = !!busyBefore;
    });
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-undo-box")) return;
    if (!window.fetch || !window.DOMParser || !window.MtgBox) return;
    e.preventDefault();
    open(form);
  });
})();
