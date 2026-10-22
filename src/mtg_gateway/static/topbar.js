/* The room kept above a focused or jumped-to element is the top bar's real height. The bar is
   sticky and is 50 px tall, but with large text and a mouse its links move to a second row
   (theme.py, the container queries on .topbar) and it grows; a fixed 60 px of scroll room would
   then leave a control that Tab or Shift+Tab reaches hidden under the bar (WCAG 2.4.11). The
   stylesheet keeps 60 px, or 9.5rem where the links wrap, when this script doesn't run. Loaded on every page.
   The room kept below works the same way: what stays at the foot of the window (the phone's tab
   bar, the waiting changes' Apply bar, the Guide's Back to top) grows with the text, so a fixed
   room hid a control under them at 200% text. The Guide's Back to top is counted even while it is
   hidden, since it shows as soon as the page scrolls. --tab-room is the phone's tab bar's height,
   which the Apply bar sits on. */
(function () {
  "use strict";
  var bar = document.querySelector(".topbar");
  if (!bar) return;
  var root = document.documentElement;
  var feet = document.querySelectorAll(".tabbar, .wbar:not(.inline), .gtop");
  function footRoom() {
    var room = 0;
    for (var i = 0; i < feet.length; i++) {
      var el = feet[i], hidden = el.classList.contains("gtop") && el.classList.contains("hide");
      if (hidden) el.classList.remove("hide");
      var style = getComputedStyle(el), box = el.getBoundingClientRect();
      if (style.display !== "none" && box.height) {
        // only what is pinned to the foot: the tab bar is a rail down the side on an open Fold
        if (style.position === "fixed" && box.top > window.innerHeight / 2)
          room = Math.max(room, window.innerHeight - box.top);
        else if (style.position === "sticky")
          room = Math.max(room, box.height + (parseFloat(style.bottom) || 0));
      }
      if (hidden) el.classList.add("hide");
    }
    return room;
  }
  var tabs = document.querySelector(".tabbar");
  function fit() {
    root.style.setProperty("--bar-room", Math.ceil(bar.getBoundingClientRect().height) + 10 + "px");
    // the waiting changes' Apply bar sits on the phone's tab bar, which grows with large text
    var tab = tabs && tabs.getBoundingClientRect();
    if (tab && tab.height && tab.top > window.innerHeight / 2)
      root.style.setProperty("--tab-room", Math.floor(window.innerHeight - tab.top) + "px");
    else root.style.removeProperty("--tab-room");
    var room = footRoom();
    if (room) root.style.setProperty("--foot-room", Math.ceil(room) + 10 + "px");
    else root.style.removeProperty("--foot-room");
  }
  // The first measure waits for the first frame: read right away, while the other deferred
  // scripts are still changing the page, it would force a layout of the whole page that the
  // frame then does again (a 100-card deck page paid twice). The room is only scroll padding,
  // so a frame's wait shows nothing.
  if (window.requestAnimationFrame) requestAnimationFrame(fit); else fit();
  if (window.ResizeObserver) {
    var watch = new ResizeObserver(fit);
    watch.observe(bar);
    for (var i = 0; i < feet.length; i++) watch.observe(feet[i]);
  }
  window.addEventListener("resize", fit);
})();
