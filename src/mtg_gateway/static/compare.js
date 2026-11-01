// Compare page: My decks is read from /api/decks/mine when the page was drawn before the deck
// list was (a cold start), and a card name that either deck holds opens the shared card viewer.
(function () {
  "use strict";
  var box = document.querySelector(".cmp-mine [data-pick-src]");
  if (box && typeof fetch === "function") {
    var head = document.querySelector(".cmp-mine [data-pick-head]");
    var done = function () { box.classList.remove("skeleton"); box.removeAttribute("aria-busy"); };
    fetch(box.getAttribute("data-pick-src"), { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(String(r.status))); })
      .then(function (data) {
        if (!data || !data.ok || typeof data.html !== "string") throw new Error("bad answer");
        done();
        if (data.html) { box.outerHTML = data.html; return; } // the chips, as the page draws them
        if (head) head.remove();  // no other deck to pick: nothing, as the page would show
        box.remove();
      })
      .catch(function () {
        done();
        box.textContent = "";
        var p = document.createElement("p");
        p.className = "ui-hint";
        p.textContent = "Your decks could not be read from Archidekt just now. ";
        var again = document.createElement("button");
        again.type = "button";
        again.className = "btn";
        again.textContent = "Try again";
        again.addEventListener("click", function () { window.location.reload(); });
        p.appendChild(again);
        box.appendChild(p);
      });
  }
  var CardView = window.MtgCardView;
  if (!CardView) return;
  document.addEventListener("click", function (e) {
    var node = e.target.closest ? e.target.closest(".cardlink[data-card]") : null;
    if (!node) return;
    e.preventDefault();
    // Scryfall is the viewer's own pill, with EDHREC and TCGplayer
    CardView.open(CardView.fromElement(node), []);
  });
})();
