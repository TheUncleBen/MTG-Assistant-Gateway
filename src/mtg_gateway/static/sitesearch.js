/* The top bar's search box on small screens. Under 600 px the box is folded away behind the
   magnifier (an ordinary link to /search without script); a tap opens the box under the bar and
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
})();
