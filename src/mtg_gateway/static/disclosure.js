// The Archidekt link disclosure on the Account page (link_disclosure.py). The server renders
// the full detail open and the tick box usable, so with scripts off nothing is hidden and nobody
// is locked out. With scripts on, this folds the detail and keeps the tick box disabled until
// the member opens it; opening it once unlocks the box for good. The server still refuses a link
// without the tick.
(function () {
  "use strict";
  var detail = document.querySelector("details[data-must-open]");
  var box = document.querySelector("input[name=accept_risk]");
  if (!detail || !box) return;
  var hint = document.getElementById("accept-risk-hint");

  function unlock() {
    box.disabled = false;
    if (hint) hint.hidden = true;
    detail.removeEventListener("toggle", onToggle);
  }

  function onToggle() {
    if (detail.open) unlock();
  }

  detail.open = false;
  box.checked = false;
  box.disabled = true;
  if (hint) hint.hidden = false;
  // Added after folding: the toggle event that folding queues sees open === false and is ignored.
  detail.addEventListener("toggle", onToggle);
})();
