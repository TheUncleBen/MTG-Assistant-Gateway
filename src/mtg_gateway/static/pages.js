// The small pages and the Account page (smallpage.py, pages.py).
// - A Back button ([data-history-back]) goes back one page when the browser has one; without
//   scripts, or on a page opened directly, its link goes to the page it names.
// - A box's final button ([data-enabled-by=<tick box id>]) stays disabled until that tick box is
//   ticked (the Delete my data box). Without scripts the tick box's `required` stops the form.
(function () {
  "use strict";
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("[data-history-back]") : null;
    if (!a || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    if (window.history.length > 1) {
      e.preventDefault();
      window.history.back();
    }
  });
  function gate(btn) {
    var box = document.getElementById(btn.getAttribute("data-enabled-by"));
    if (!box) return;
    var sync = function () { btn.disabled = !box.checked; };
    box.addEventListener("change", sync);
    sync();
  }
  var gated = document.querySelectorAll("[data-enabled-by]");
  for (var i = 0; i < gated.length; i++) gate(gated[i]);
})();
