// Double-clickjacking guard for the consent and proposal-review pages and the Proposals bar's
// Apply box (see clickguard.py).
// Buttons marked data-guard start disabled. They are enabled only once this page has been
// visible and focused for data-dwell ms AND the person moved the pointer or pressed a key here
// at least data-settle ms ago. Losing focus or visibility disables them and restarts both clocks,
// so a click that lands right after a hostile page swaps this window in hits a disabled button.
// On a touch screen the touch that unlocks the page never acts, whatever the timing: the click
// it produces is swallowed, and the next tap approves.
(function () {
  "use strict";
  if (window.MtgGuard) return;  // loaded twice (a page with a guarded form and a list): once is enough
  // A box opened over a list (the Proposals bar's Apply, static/proposals.js) brings its guarded
  // button later: it calls MtgGuard.restart() as it opens, so the clocks start again from there.
  // The buttons and hints are looked up on every check for that reason.
  var forms = document.querySelectorAll("form.guarded");
  var dwell = (forms.length && parseInt(forms[0].getAttribute("data-dwell"), 10)) || 800;
  var settle = (forms.length && parseInt(forms[0].getAttribute("data-settle"), 10)) || 300;
  var since = null; // when the page last became visible and focused
  var touched = null; // first pointer movement or key press since then
  var touchSeq = 0; // counts touches (touchstart events) on this page
  var swallowSeq = -1; // the touch whose click is swallowed: the one that unlocked the page
  var unlockPending = false; // the unlocking event was a touch whose touchstart is still to come
  var GUARDED = "form.guarded button[data-guard], .ui-box button[data-guard]";

  function active() {
    return document.visibilityState === "visible" && document.hasFocus();
  }

  // A form marked data-tab keeps its locked buttons in the Tab order (aria-disabled), so a fast
  // keyboard user does not skip past them; their press is refused below until they unlock.
  function tabbed(b) {
    var f = b.form || b.closest("form.guarded");  // a box's button names its form (form='…')
    return !!f && f.matches("form.guarded[data-tab]");
  }

  function locked(b) {
    return tabbed(b) ? b.getAttribute("aria-disabled") === "true" : b.disabled;
  }

  function set(enabled) {
    var buttons = document.querySelectorAll(GUARDED);
    var hints = document.querySelectorAll("form.guarded .guard-hint, .ui-box .guard-hint");
    for (var i = 0; i < buttons.length; i++) {
      if (tabbed(buttons[i])) {
        if (enabled) buttons[i].removeAttribute("aria-disabled");
        else buttons[i].setAttribute("aria-disabled", "true");
      } else {
        buttons[i].disabled = !enabled;
      }
    }
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
  // A tap or click on a locked button lands on the button row (theme.py gives a disabled guarded
  // button pointer-events:none): without this the browser would move focus to the nearest
  // focusable ancestor, <main tabindex=-1>, and scroll the page to it, so the first tap on a phone
  // looked like a jump. The tap still unlocks the page (interacted() above saw its touchstart).
  document.addEventListener(
    "mousedown",
    function (e) {
      var row = e.target instanceof Element ? e.target.closest("form.guarded .choice, .ui-box .acts") : null;
      if (!row) return;
      var control = e.target.closest("button, a[href], input, select, textarea");
      if (!control || (control.matches("button[data-guard]") && locked(control))) e.preventDefault();
    },
    true
  );
  document.addEventListener(
    "click",
    function (e) {
      var button = e.target instanceof Element ? e.target.closest(GUARDED) : null;
      if (button && locked(button)) {
        // a locked button that stays in the Tab order: refused, as a disabled one would be
        e.preventDefault();
        e.stopImmediatePropagation();
        return;
      }
      if (swallowSeq < 0 || swallowSeq !== touchSeq) return;
      if (!button) return;
      swallowSeq = -1;
      e.preventDefault();
      e.stopImmediatePropagation();
    },
    true
  );
  // Enter in a field submits through the first button without a click on it: refused while locked.
  document.addEventListener(
    "submit",
    function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement) || !form.matches("form.guarded[data-tab]")) return;
      var by = e.submitter || form.querySelector("button[data-guard]");
      if (by && locked(by)) e.preventDefault();
    },
    true
  );
  window.addEventListener("blur", lock);
  window.addEventListener("pagehide", lock);
  window.addEventListener("focus", check);
  document.addEventListener("visibilitychange", check);
  window.MtgGuard = {
    // a guarded box has just opened: locked again until it has been seen and used (dwell, settle)
    restart: function () {
      var f = document.querySelectorAll("form.guarded[data-dwell]");
      if (f.length) {
        dwell = parseInt(f[f.length - 1].getAttribute("data-dwell"), 10) || dwell;
        settle = parseInt(f[f.length - 1].getAttribute("data-settle"), 10) || settle;
      }
      lock();
      check();
    }
  };
  lock();
  check();
  setInterval(check, 100);
})();
