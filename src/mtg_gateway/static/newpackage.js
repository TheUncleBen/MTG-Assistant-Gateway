"use strict";
// The New package box on My decks (parity release 2, deck_extras.new_package_box_html): a live
// count under the name, the two required fields checked before the form goes (said in the box, not
// by the browser's own bubble), and Create package reads "Creating…" while it does. Without
// scripts the form posts as it always did and the gateway says the same things.
(function () {
  var form = document.getElementById("npf");
  if (!form) return;
  var box = form.closest(".ui-box");
  var name = form.querySelector("#np-name"), kw = form.querySelector("#np-kw");
  var count = document.getElementById("np-name-c");
  function say(text) {
    var b = window.MtgBox && window.MtgBox.current();
    if (b && b.panel === box) { b.note(text, true); return; }
    var n = box.querySelector(".boxnote");
    if (n) { n.hidden = !text; n.textContent = text; }
  }
  function recount() { if (count && name) count.textContent = name.value.length + "/120"; }  // as New deck's
  if (name) name.addEventListener("input", recount);
  recount();
  form.addEventListener("submit", function (e) {
    var bad = name && !name.value.trim() ? "Give the package a name." : kw && !kw.value.trim() ? "Give it at least one keyword." : "";
    if (bad) {
      e.preventDefault();
      say(bad);
      (name && !name.value.trim() ? name : kw).focus();
      return;
    }
    say("");
    var go = document.querySelector("[form=npf][data-busy-label]");
    if (go && !go.hasAttribute("aria-busy")) {
      go.setAttribute("aria-busy", "true");
      go.disabled = true;
      var node = null;
      for (var i = go.childNodes.length - 1; i >= 0; i--) if (go.childNodes[i].nodeType === 3 && go.childNodes[i].textContent.trim()) { node = go.childNodes[i]; break; }
      if (node) node.textContent = " " + go.getAttribute("data-busy-label");
    }
  });
})();
