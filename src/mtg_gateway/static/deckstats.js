/* Deck page statistics: Archidekt's extra stats controls. The server draws every variant (curve by
   None / Type / Category, colours as Bar or Pie, production from all cards or lands only); this
   shows the chosen one without a reload, remembers the choice in this browser, and lets a click on
   a chart part (a curve bar or segment, a colour slice, a legend entry, a quantity row) show just
   those cards in the deck list above, with a "Show all cards" button to go back. */
(function () {
  "use strict";
  var stats = document.getElementById("stats");
  if (!stats) return;
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var KEY = "mtg.deckstats";
  var prefs = {};
  try { prefs = JSON.parse(window.localStorage.getItem(KEY) || "{}") || {}; } catch (e) { prefs = {}; }
  function save() {
    try { window.localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) { /* private window: not kept */ }
  }

  // -- the toggles -------------------------------------------------------------------------
  function show(box, attr, value) {
    $$("[" + attr + "]", box).forEach(function (el) { el.hidden = el.getAttribute(attr) !== value; });
  }
  function apply(set, value) {
    if (set === "curve") {
      var cbox = stats.querySelector(".curvebox");
      if (!cbox || !cbox.querySelector("[data-curve='" + value + "']")) return;
      show(cbox, "data-curve", value);
    } else if (set === "chart") {
      var box = stats.querySelector(".colourbox");
      if (!box || (value !== "bar" && value !== "pie")) return;
      show(box, "data-chart", value);
    } else if (set === "lands") {
      var lbox = stats.querySelector(".colourbox");
      if (!lbox) return;
      show(lbox, "data-src", value ? "lands" : "all");
    }
    $$("button[data-set='" + set + "']", stats).forEach(function (b) {
      var on = set === "lands" ? !!value : b.getAttribute("data-value") === value;
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    prefs[set] = value;
  }
  $$(".statctl", stats).forEach(function (ctl) { ctl.hidden = false; });
  ["curve", "chart", "lands"].forEach(function (set) { if (prefs[set] !== undefined) apply(set, prefs[set]); });
  stats.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-set]");
    if (!b || !stats.contains(b)) return;
    var set = b.getAttribute("data-set");
    apply(set, set === "lands" ? b.getAttribute("aria-pressed") !== "true" : b.getAttribute("data-value"));
    save();
  });

  // -- a click on a chart part focuses its cards in the deck list ---------------------------
  var cards = document.getElementById("cards");
  if (!cards) return;
  var q = document.getElementById("q");
  var note = null;
  var current = null;
  function unmark() {
    $$("[data-focus].on", stats).forEach(function (el) {
      el.classList.remove("on");
      if (el.tagName === "BUTTON") el.removeAttribute("aria-pressed");
    });
  }
  function clear(keepDisplay) {
    unmark();
    current = null;
    if (note) { note.remove(); note = null; }
    if (keepDisplay) return;
    $$("[data-name], .stack", cards).forEach(function (el) { el.style.display = ""; });
  }
  function focusOn(el) {
    var names;
    try { names = JSON.parse(el.getAttribute("data-focus")) || []; } catch (err) { names = []; }
    var want = {};
    names.forEach(function (n) { want[n] = true; });
    if (q) q.value = "";
    var shown = 0;
    $$("[data-name]", cards).forEach(function (item) {
      var hit = !!want[item.getAttribute("data-name")];
      item.style.display = hit ? "" : "none";
      if (hit) shown++;
    });
    $$(".stack", cards).forEach(function (st) {
      st.style.display = $$("[data-name]", st).some(function (x) { return x.style.display !== "none"; }) ? "" : "none";
    });
    unmark();
    el.classList.add("on");
    if (el.tagName === "BUTTON") el.setAttribute("aria-pressed", "true");
    current = el;
    if (!note) {
      note = document.createElement("div");
      note.className = "statfocus";
      note.setAttribute("role", "status");
      var text = document.createElement("span");
      var all = document.createElement("button");
      all.type = "button";
      all.textContent = "Show all cards";
      all.addEventListener("click", function () {
        var back = current;
        clear();
        if (back && back.focus) back.focus();
      });
      note.appendChild(text);
      note.appendChild(all);
      cards.parentNode.insertBefore(note, cards);
    }
    var label = el.getAttribute("data-focus-label") || "";
    note.firstChild.textContent = shown ?
      "Showing " + names.length + (names.length === 1 ? " card" : " cards") + ": " + label :
      "No card in this view for " + label;
    note.scrollIntoView({ block: "nearest" });
  }
  stats.addEventListener("click", function (e) {
    var el = e.target.closest("[data-focus]");
    if (!el || !stats.contains(el) || el.disabled) return;
    if (el === current) { clear(); return; }
    focusOn(el);
  });
  // typing in the deck's own filter takes over from a stats focus
  if (q) q.addEventListener("input", function () { if (current) clear(true); });
})();
