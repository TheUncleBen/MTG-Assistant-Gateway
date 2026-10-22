// My card models (card_models_view.py): the box's search by card name, the card name's click
// (the card's own search page; the shared card menu comes from menus.js), and Remove, which asks
// first with the shared confirm box and then submits the box's hidden form. Without this script
// the page /history/models does the same with its own confirm box.
(function () {
  "use strict";
  var box = document.getElementById("my-models");
  if (!box) return;
  var q = box.querySelector("[data-models-filter]");
  var list = box.querySelector("[data-models-list]");
  var none = box.querySelector("[data-models-none]");
  var count = box.querySelector("[data-models-count]");

  function filter() {
    if (!q || !list) return;
    var want = q.value.trim().toLowerCase(), shown = 0;
    var rows = list.querySelectorAll("li[data-name]");
    for (var i = 0; i < rows.length; i++) {
      var hit = !want || rows[i].getAttribute("data-name").indexOf(want) >= 0;
      rows[i].hidden = !hit;
      if (hit) shown++;
    }
    if (none) none.hidden = shown > 0;
    if (count) count.textContent = want ? shown + (shown === 1 ? " card matches" : " cards match") : "";
  }
  if (q) q.addEventListener("input", filter);

  box.addEventListener("click", function (e) {
    var name = e.target.closest ? e.target.closest(".mm-list button.cardlink[data-href]") : null;
    if (name) { location.href = name.getAttribute("data-href"); return; }
    var rm = e.target.closest ? e.target.closest("a[data-remove-model]") : null;
    if (!rm) return;
    e.preventDefault();
    var card = rm.getAttribute("data-remove-model");
    var form = document.getElementById("mm-rm-f");
    var Box = window.MtgBox;
    if (!form || !Box || !Box.confirm) { location.href = rm.getAttribute("href"); return; }
    Box.confirm({
      title: "Remove the saved description of “" + card + "”?",
      text: "Later solo tests play “" + card + "” without its effect again, until a new description is saved. The reports that already played it keep their numbers.",
      ok: "Remove",
      cancel: "Keep it",
      danger: true,
      icon: "trash",
      returnFocus: rm,
      onOk: function () {
        form.elements.card.value = card;
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
        return true;
      },
      onClose: function () {
        // the list box closed when the confirm opened: back to it
        if (document.body.contains(rm) && Box.openElement) Box.openElement(box, { returnFocus: rm });
      }
    });
  });
})();
