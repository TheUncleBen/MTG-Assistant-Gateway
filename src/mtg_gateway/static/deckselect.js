/* Select on My decks (0.7.46 audit QG-17): a tick on any deck tile shows the bar (its count,
   Move to folder…, Visibility…, Cancel); the tiles' tick boxes and the boxes' fields belong to
   the one hidden form (#decks-bulk), so a press sends the ticked decks with the box's choice.
   Select ticks every deck on screen, or clears them all. The list is redrawn by decks.js as the
   filters change: the ticks are read live, so a redraw simply starts the selection afresh.
   Without this script there is no Select button; the boxes open as pages of their own. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var form = $("#decks-bulk");
  if (!form) return;
  function boxes() { return $$("#decks-list .decklist .deck .chk input[name=deck_id]"); }
  function ticked() { return boxes().filter(function (b) { return b.checked; }); }
  function bar() { return $(".ui-bar[aria-label='Selected decks']"); }
  function say(text) {
    var Menu = window.MtgMenu;
    if (Menu && Menu.toast) Menu.toast(text, { error: true });
    else { var live = $("#live"); if (live) live.textContent = text; }
  }
  function redraw() {
    var n = ticked().length, b = bar();
    boxes().forEach(function (x) { var li = x.closest("li"); if (li) li.classList.toggle("sel", x.checked); });
    if (b) {
      b.hidden = n === 0;
      var c = $(".count", b);
      if (c) c.textContent = n + " selected";
    }
    var sel = $(".decks-select"), all = boxes();
    if (sel) sel.setAttribute("aria-pressed", n > 0 && n === all.length ? "true" : "false");
    $$("[data-bulk-count]").forEach(function (w) { w.textContent = n === 1 ? "The selected deck" : "The " + n + " selected decks"; });
    $$("#decks-bulk-move p, #decks-bulk-vis p").forEach(function (w) {
      var t = w.childNodes[1];
      if (t && t.nodeType === 3) t.textContent = t.textContent.replace(/^ (go|goes|get|gets) /, function (m, v) {
        var one = n === 1;
        return " " + (v.indexOf("go") === 0 ? (one ? "goes" : "go") : (one ? "gets" : "get")) + " ";
      });
    });
  }
  function setAll(on) { boxes().forEach(function (b) { b.checked = on; }); redraw(); }
  var selectBtn = $(".decks-select");
  if (selectBtn) selectBtn.hidden = false;
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.name === "deck_id" && t.closest && t.closest("#decks-list")) redraw();
  });
  var list = $("#decks-list");
  if (list) list.addEventListener("decks:loaded", redraw);
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var sel = e.target.closest ? e.target.closest(".decks-select") : null;
    if (sel) { setAll(sel.getAttribute("aria-pressed") !== "true"); return; }
    var b = e.target.closest ? e.target.closest(".ui-bar[aria-label='Selected decks']") : null;
    if (!b) return;
    if (e.target.closest(".decks-clear")) { setAll(false); return; }
    var opener = e.target.closest("a[data-box-open]");
    if (opener && ticked().length === 0) { e.preventDefault(); say("Tick a deck first."); }
  }, true);
  form.addEventListener("submit", function (e) {
    if (ticked().length === 0) { e.preventDefault(); say("Tick a deck first."); return; }
    var b = bar();
    if (b) { b.setAttribute("aria-busy", "true"); $$("a, button", b).forEach(function (x) { x.setAttribute("aria-disabled", "true"); }); }
  });
  redraw();
})();
