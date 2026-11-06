/* The top bar's search box on small screens. Under 600 px, and from 600 to 899 px with a mouse
   (where the text links fill the bar), the box is folded away behind the magnifier (an ordinary link to /search without script); a tap opens the box under the bar and
   focuses it, a second tap or Escape in an empty box folds it again. Where the box is already
   in the bar the magnifier is hidden by the stylesheet. The suggestions themselves are
   static/suggest.js (data-suggest-site). Loaded on every page; does nothing when signed out. */
(function () {
  "use strict";
  var btn = document.querySelector(".topbar .searchbtn");
  var form = document.querySelector(".topbar .topsearch");
  if (!btn || !form) return;
  var input = form.querySelector("input");
  var body = document.body;
  // the unfolded box is a layer (static/layers.js): on a phone Back folds it, not the page
  var Layers = window.MtgLayers, layer = null;
  function isOpen() { return body.classList.contains("search-open"); }
  function open() {
    body.classList.add("search-open");
    btn.setAttribute("aria-expanded", "true");
    if (!layer && Layers) layer = Layers.open({ name: "topsearch", close: function () { layer = null; close(true); } });
    setTimeout(function () { input.focus(); }, 0);
  }
  function close(refocus) {
    body.classList.remove("search-open");
    btn.setAttribute("aria-expanded", "false");
    var l = layer;
    layer = null;
    if (l && Layers) Layers.release(l);
    if (refocus) btn.focus();
  }
  btn.setAttribute("role", "button");
  btn.setAttribute("aria-expanded", "false");
  btn.setAttribute("aria-controls", form.id);
  btn.setAttribute("aria-label", "Search cards and decks");
  btn.addEventListener("click", function (e) {
    e.preventDefault();
    if (isOpen()) { close(true); return; }
    // the box is in the bar already (a wider window): just focus it
    if (form.offsetParent !== null) { input.focus(); return; }
    open();
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && isOpen() && !input.value) { e.preventDefault(); close(true); }
  });
  // Escape with the focus elsewhere on the page folds the empty box too, as long as nothing is
  // open over it (the top layer takes Escape: static/layers.js)
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || e.defaultPrevented || !isOpen() || input.value) return;
    var t = Layers && Layers.top();
    if (t && t.name !== "topsearch") return;
    e.preventDefault();
    close(true);
  });
  // the window grown past the width the bar folds its box at (its fold button gone): the box is in the bar on its
  // own, so the unfolded state (and its layer) ends, with nothing to show for it
  window.addEventListener("resize", function () { if (isOpen() && layer && !btn.getClientRects().length) close(false); });

  // -- desktop shortcuts (QOL-18, 0.7.46 audit QG-15): / or Ctrl+K (Cmd+K) focuses the search,
  // ? lists the shortcuts; none fires while typing or while a box or menu is open
  function typing(t) {
    if (!t || !t.tagName) return false;
    var tag = t.tagName.toLowerCase();
    // a ticked box or a pressed button is not typing: / and ? still work from it (final audit)
    if (tag === "input") return !/^(checkbox|radio|button|submit|reset|file|range|color)$/i.test(t.type || "text");
    return tag === "textarea" || tag === "select" || t.isContentEditable;
  }
  function layerOpen() {
    // a box, a menu, a sheet, a list or the card viewer open: the keys belong to it, not to the
    // header's search (audit F-6: / and ? acted under an open layer). Every layer is known to
    // static/layers.js; the markup is asked too, for a page whose script is still loading
    if (Layers && Layers.isOpen("topsearch")) return true;
    var shown = document.querySelectorAll(
      ".mtgbox[role=dialog], .ctxmenu[role=menu], dialog[open], .cardview.open, [aria-modal=true], " +
      "details.dd[open], details.pickm[open], .msel-list, .printpick.open, .addsheet.open");
    for (var i = 0; i < shown.length; i++) if (shown[i].getClientRects().length) return true;
    return false;
  }
  function focusSearch() {
    if (form.offsetParent !== null) { input.focus(); input.select(); return; }
    open();
  }
  function shortcuts() {
    var Box = window.MtgBox;
    if (!Box) return;
    var rows = [
      ["/", "Search cards and decks (Ctrl+K too)"],
      ["Esc", "Close the open menu or box"],
      ["Enter", "Open the highlighted suggestion, or the card whose exact name you typed; other text searches decks"],
      ["Shift+F10", "Open a card's menu on a deck page (the Menu key too)"],
      ["Ctrl+Z, Ctrl+Shift+Z", "Undo and redo a deck change on a deck page"],
      ["?", "This list"]
    ];
    var dl = document.createElement("dl");
    dl.className = "keys";
    rows.forEach(function (r) {
      // each key with its words in a row of their own (theme: dl.keys > div)
      var row = document.createElement("div"), dt = document.createElement("dt"),
        kbd = document.createElement("kbd"), dd = document.createElement("dd");
      kbd.textContent = r[0]; dt.appendChild(kbd); dd.textContent = r[1];
      row.appendChild(dt); row.appendChild(dd); dl.appendChild(row);
    });
    Box.open({ title: "Keyboard shortcuts", body: dl, actions: [Box.button("Close", "btn", null, { cancel: true })], returnFocus: document.activeElement });
  }
  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.altKey || typing(e.target) || layerOpen()) return;
    var k = e.key;
    if ((k === "k" || k === "K") && (e.ctrlKey || e.metaKey) && !e.shiftKey) { e.preventDefault(); focusSearch(); return; }
    if (e.ctrlKey || e.metaKey) return;
    if (k === "/") { e.preventDefault(); focusSearch(); return; }
    if (k === "?") { e.preventDefault(); shortcuts(); }
  });
})();
