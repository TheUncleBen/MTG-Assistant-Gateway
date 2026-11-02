/* Select on My decks (0.7.46 audit QG-17; re-audit N03): Select opens the select mode: the
   tiles' tick boxes show (hidden otherwise), nothing is ticked, and the bar shows (its count,
   Select all, Move to folder…, Visibility…, Cancel); the tick boxes and the boxes' fields belong
   to the one hidden form (#decks-bulk), so a press sends the ticked decks with the box's choice.
   Select all ticks every deck on screen, or clears them all once they all are; Cancel, or Select
   again, closes the mode. The list is redrawn by decks.js as the filters change: the ticks are
   read live, so a redraw simply starts the selection afresh. Without this script there is no
   Select button, the tick boxes always show, and the boxes open as pages of their own. */
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
  function tell(text) { var live = $("#live"); if (live) live.textContent = text; }
  function selecting() { return document.body.classList.contains("decks-selecting"); }
  function redraw() {
    var n = ticked().length, b = bar(), all = boxes();
    all.forEach(function (x) { var li = x.closest("li"); if (li) li.classList.toggle("sel", x.checked); });
    if (b) {
      b.hidden = !selecting();
      var c = $(".count", b);
      if (c) c.textContent = n + " selected";
      var everyOne = $(".decks-all", b);
      if (everyOne) everyOne.textContent = n > 0 && n === all.length ? "Select none" : "Select all";
    }
    var sel = $(".decks-select");
    if (sel) sel.setAttribute("aria-pressed", selecting() ? "true" : "false");
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
  function mode(on) {
    document.body.classList.toggle("decks-selecting", on);
    if (!on) setAll(false); else redraw();
    tell(on ? "Select is on: tick the decks, then pick an action." : "Select is off.");
  }
  var selectBtn = $(".decks-select");
  if (selectBtn) selectBtn.hidden = false;
  if (selectBtn) document.body.classList.add("decks-selectable");
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.name === "deck_id" && t.closest && t.closest("#decks-list")) redraw();
  });
  var list = $("#decks-list");
  if (list) list.addEventListener("decks:loaded", redraw);
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var sel = e.target.closest ? e.target.closest(".decks-select") : null;
    if (sel) { mode(!selecting()); return; }
    var b = e.target.closest ? e.target.closest(".ui-bar[aria-label='Selected decks']") : null;
    if (!b) return;
    if (e.target.closest(".decks-clear")) { mode(false); if (selectBtn) selectBtn.focus(); return; }
    if (e.target.closest(".decks-all")) { var all = boxes(); setAll(!(all.length && all.every(function (x) { return x.checked; }))); return; }
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
