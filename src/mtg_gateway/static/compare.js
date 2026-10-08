// Compare page: a card name that either deck holds opens the shared card viewer.
(function () {
  "use strict";
  var CardView = window.MtgCardView;
  if (!CardView) return;
  document.addEventListener("click", function (e) {
    var node = e.target.closest ? e.target.closest(".cardlink[data-card]") : null;
    if (!node) return;
    e.preventDefault();
    var name = node.getAttribute("data-card") || "";
    var scry = document.createElement("a");
    scry.className = "btn";
    scry.textContent = "Open on Scryfall";
    scry.href = "https://scryfall.com/search?q=" + encodeURIComponent("!\"" + name + "\"");
    scry.target = "_blank";
    scry.rel = "noopener noreferrer";
    CardView.open(CardView.fromElement(node), [scry]);
  });
})();
