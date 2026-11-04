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
  function isOpen() { return body.classList.contains("search-open"); }
  function open() {
    body.classList.add("search-open");
    btn.setAttribute("aria-expanded", "true");
    setTimeout(function () { input.focus(); }, 0);
  }
  function close(refocus) {
    body.classList.remove("search-open");
    btn.setAttribute("aria-expanded", "false");
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
    // a box, a menu or the card viewer open: the keys belong to it, not to the header's search
    return !!document.querySelector(
      ".mtgbox[role=dialog]:not([hidden]), .ctxmenu[role=menu], dialog[open], .cardview.open");
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
      var dt = document.createElement("dt"), kbd = document.createElement("kbd"), dd = document.createElement("dd");
      kbd.textContent = r[0]; dt.appendChild(kbd); dd.textContent = r[1];
      dl.appendChild(dt); dl.appendChild(dd);
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
