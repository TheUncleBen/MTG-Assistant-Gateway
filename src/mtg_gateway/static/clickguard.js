// Double-clickjacking guard for the consent and proposal-review pages (see clickguard.py).
// Buttons marked data-guard start disabled. They are enabled only once this page has been
// visible and focused for data-dwell ms AND the person moved the pointer or pressed a key here
// at least data-settle ms ago. Losing focus or visibility disables them and restarts both clocks,
// so a click that lands right after a hostile page swaps this window in hits a disabled button.
// On a touch screen the touch that unlocks the page never acts, whatever the timing: the click
// it produces is swallowed, and the next tap approves.
(function () {
  "use strict";
  var forms = document.querySelectorAll("form.guarded");
  if (!forms.length) return;
  var buttons = document.querySelectorAll("form.guarded button[data-guard]");
  var hints = document.querySelectorAll("form.guarded .guard-hint");
  var dwell = parseInt(forms[0].getAttribute("data-dwell"), 10) || 800;
  var settle = parseInt(forms[0].getAttribute("data-settle"), 10) || 300;
  var since = null; // when the page last became visible and focused
  var touched = null; // first pointer movement or key press since then
  var touchSeq = 0; // counts touches (touchstart events) on this page
  var swallowSeq = -1; // the touch whose click is swallowed: the one that unlocked the page
  var unlockPending = false; // the unlocking event was a touch whose touchstart is still to come

  function active() {
    return document.visibilityState === "visible" && document.hasFocus();
  }

  function set(enabled) {
    for (var i = 0; i < buttons.length; i++) buttons[i].disabled = !enabled;
    for (var j = 0; j < hints.length; j++) hints[j].hidden = enabled;
  }

  function lock() {
    since = null;
    touched = null;
    set(false);
  }

  function check() {
    if (!active()) {
      lock();
      return;
    }
    var now = performance.now();
    if (since === null) since = now;
    set(touched !== null && now - since >= dwell && now - touched >= settle);
  }

  function interacted(e) {
    if (!active()) return;
    if (since === null) since = performance.now();
    if (touched === null) {
      touched = performance.now();
      unlockPending = e.type === "touchstart" || (e.type === "pointerdown" && e.pointerType === "touch");
    }
    if (e.type === "touchstart") {
      touchSeq += 1;
      if (unlockPending) {
        swallowSeq = touchSeq;
        unlockPending = false;
      }
    }
  }

  // A touch or press counts too (phones have no hover), but only as the start of the settle
  // clock. The click that the unlocking touch produces is swallowed below, so on a phone the
  // first tap unlocks and the second one acts, however slowly the browser delivers the click.
  ["mousemove", "pointermove", "pointerdown", "touchstart", "touchmove", "wheel", "keydown"].forEach(function (type) {
    document.addEventListener(type, interacted, { capture: true, passive: true });
  });
  document.addEventListener(
    "click",
    function (e) {
      if (swallowSeq < 0 || swallowSeq !== touchSeq) return;
      var button = e.target instanceof Element ? e.target.closest("form.guarded button[data-guard]") : null;
      if (!button) return;
      swallowSeq = -1;
      e.preventDefault();
      e.stopImmediatePropagation();
    },
    true
  );
  window.addEventListener("blur", lock);
  window.addEventListener("pagehide", lock);
  window.addEventListener("focus", check);
  document.addEventListener("visibilitychange", check);
  lock();
  check();
  setInterval(check, 100);
})();
