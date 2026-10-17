/* The room kept above a focused or jumped-to element is the top bar's real height. The bar is
   sticky and is 50 px tall, but with large text and a mouse its links move to a second row
   (theme.py, the container queries on .topbar) and it grows; a fixed 60 px of scroll room would
   then leave a control that Tab or Shift+Tab reaches hidden under the bar (WCAG 2.4.11). The
   stylesheet keeps 9.5rem when this script doesn't run. Loaded on every page. */
(function () {
  "use strict";
  var bar = document.querySelector(".topbar");
  if (!bar) return;
  var root = document.documentElement;
  function fit() {
    root.style.setProperty("--bar-room", Math.ceil(bar.getBoundingClientRect().height) + 10 + "px");
  }
  fit();
  if (window.ResizeObserver) new ResizeObserver(fit).observe(bar);
  else window.addEventListener("resize", fit);
})();
