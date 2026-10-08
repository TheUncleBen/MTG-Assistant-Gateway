"use strict";
// Export page: the Copy buttons put the matching list on the clipboard. Works in the app's
// WebView and in browsers; falls back to selecting the text when the clipboard is refused.
(function () {
  function flash(btn, text) {
    var old = btn.textContent;
    btn.textContent = text;
    btn.disabled = true;
    setTimeout(function () { btn.textContent = old; btn.disabled = false; }, 1500);
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copybtn");
    if (!btn) return;
    var area = document.getElementById(btn.getAttribute("data-copy"));
    if (!area) return;
    var done = function () { flash(btn, "Copied"); };
    var fail = function () { area.focus(); area.select(); flash(btn, "Select and copy"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(area.value).then(done, fail);
    } else {
      fail();
    }
  });
})();
