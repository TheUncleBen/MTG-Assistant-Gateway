"use strict";
// Export page: the Copy buttons put the matching list on the clipboard. Works in the app's
// WebView and in browsers; falls back to selecting the text when the clipboard is refused.
(function () {
  // Only the label text changes, so the button keeps its icon; the result is also announced
  // through the live region next to the button (aria-describedby).
  function flash(btn, text) {
    var label = btn.querySelector(".label") || btn;
    var old = label.textContent;
    label.textContent = text;
    btn.disabled = true;
    var live = document.getElementById(btn.getAttribute("data-copy") + "-status");
    if (live) live.textContent = text;
    setTimeout(function () { label.textContent = old; btn.disabled = false; if (live) live.textContent = ""; }, 1500);
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copybtn");
    if (!btn) return;
    var area = document.getElementById(btn.getAttribute("data-copy"));
    if (!area) return;
    var done = function () { flash(btn, "Copied"); };
    var fail = function () { area.focus(); area.select(); flash(btn, "Select and copy"); };
    // execCommand first, inside the press: it works in the app's WebView, where the async
    // clipboard may be refused (the same order as Play on EDHPlay in deck.js); the async API is
    // the fallback for browsers that dropped execCommand.
    var temp = document.createElement("textarea");
    temp.value = area.value;
    temp.setAttribute("readonly", "");
    temp.style.position = "fixed";
    temp.style.top = "-1000px";
    document.body.appendChild(temp);
    temp.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    temp.remove();
    btn.focus();
    if (ok) {
      done();
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(area.value).then(done, fail);
    } else {
      fail();
    }
  });
})();
