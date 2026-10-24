"use strict";
// The New deck box (F4): the Cards tabs (Empty, Paste a list, From a file) show one source at a
// time, the tile preview follows the name, format, Private, Theorycrafted and the game type, the name is checked before
// the form goes, and Create deck reads "Creating…" while it does. Without scripts every field
// shows and the form posts as it always did.
(function () {
  var form = document.getElementById("ndf");
  if (!form) return;
  var $ = function (sel, root) { return (root || form).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || form).querySelectorAll(sel)); };
  var tabs = $$(".nd-tabs [role=tab]");
  var src = $(".nd-src"), pick = $(".nd-src .filepick"), area = $("#source"), readAs = $(".nd-as");
  var name = $("#name"), count = $(".nd-cnt"), nameErr = $("#nd-ne");
  var fmt = $("#format"), priv = $("input[name=private]"), theory = $("input[name=theorycrafted]"), game = $("#game");
  var tileName = $(".nd-pt"), tileFmt = $(".nd-pf"), tileN = $(".nd-pn"), tileIni = $(".ndtile .ini"), tileCards = $(".nd-pc");

  function show(key) {
    form.setAttribute("data-src", key);
    tabs.forEach(function (t) {
      var on = t.getAttribute("data-src") === key;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
    if (src) src.hidden = key === "empty";
    if (pick) pick.hidden = key !== "file";
    if (area) area.hidden = key === "file" && !area.value;  // the file's text shows once it is read
    if (readAs) readAs.hidden = key === "empty";
    if (key === "paste" && area) area.focus();
  }
  tabs.forEach(function (t) {
    t.addEventListener("click", function () { if (!t.disabled) show(t.getAttribute("data-src")); });
    t.addEventListener("keydown", function (e) {
      var i = tabs.indexOf(t), d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d) return;
      e.preventDefault();
      var n = tabs[(i + d + tabs.length) % tabs.length];
      if (n.disabled) n = tabs[(i + 2 * d + tabs.length) % tabs.length];
      n.focus();
      show(n.getAttribute("data-src"));
    });
  });
  show(form.getAttribute("data-src") || "empty");

  // the preview tile: name, format and Private as typed, and how many cards the list names
  function cards() {
    if (!area || form.getAttribute("data-src") === "empty") return 0;
    var n = 0;
    area.value.split(/\r?\n/).forEach(function (line) {
      line = line.trim();
      if (!line || line.charAt(0) === "#" || line.charAt(0) === "/" || /^(commander|sideboard|maybeboard|main(board)?|deck):?$/i.test(line)) return;
      var m = line.match(/^(\d+)\s*x?\s+\S/i);
      n += m ? Number(m[1]) : 1;
    });
    return n;
  }
  function preview() {
    var nm = name ? name.value.trim() : "";
    if (count) count.textContent = (name ? name.value.length : 0) + "/120";
    if (tileName) { tileName.textContent = nm || "Unnamed deck"; tileName.classList.toggle("muted", !nm); }
    if (tileIni) tileIni.textContent = (nm || "?").charAt(0).toUpperCase();
    if (tileFmt && fmt) {
      var label = fmt.options[fmt.selectedIndex] ? fmt.options[fmt.selectedIndex].text : "";
      // Theorycrafted and the game type (nothing for Any) follow their controls too
      var gameLabel = game && game.value !== "any" && game.options[game.selectedIndex] ? game.options[game.selectedIndex].text : "";
      tileFmt.textContent = label + " · " + (priv && priv.checked ? "Private" : "Public") +
        (theory && theory.checked ? " · Theorycrafted" : "") + (gameLabel ? " · " + gameLabel : "");
    }
    var n = cards();
    if (tileN) tileN.textContent = n + (n === 1 ? " card" : " cards");
    if (tileCards) {
      tileCards.hidden = !n;
      tileCards.textContent = n ? n + (n === 1 ? " card" : " cards") + " named in the list; Archidekt checks the names." : "";
    }
    if (nm && nameErr && !nameErr.hidden) { nameErr.hidden = true; name.removeAttribute("aria-invalid"); }
  }
  ["input", "change"].forEach(function (t) { form.addEventListener(t, preview); });
  preview();

  // the name is needed: said next to the box, not by the browser's own bubble
  form.addEventListener("submit", function (e) {
    if (name && !name.value.trim()) {
      e.preventDefault();
      if (nameErr) nameErr.hidden = false;
      name.setAttribute("aria-invalid", "true");
      name.focus();
      return;
    }
    if (form.getAttribute("data-src") === "empty" && area) area.value = "";
    var go = document.querySelector("[form=ndf][data-busy-label]");
    if (go && !go.hasAttribute("aria-busy")) {
      go.setAttribute("aria-busy", "true");
      // only the words change: the icon stays (the button has no .label span of its own)
      var node = null;
      for (var i = go.childNodes.length - 1; i >= 0; i--) if (go.childNodes[i].nodeType === 3 && go.childNodes[i].textContent.trim()) { node = go.childNodes[i]; break; }
      if (node) { go.dataset.busyRestore = node.textContent; node.textContent = " " + go.getAttribute("data-busy-label"); }
    }
  });
})();
