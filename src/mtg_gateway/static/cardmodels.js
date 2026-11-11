// The Card models page (card_models_view.py): the search by card name, a card's row opened by the
// page's address (#m-<card>: the card viewer and the report link to one card), Remove, which asks
// first with the shared confirm box and then submits the page's hidden form, and the actions the
// row's menu offers (static/menus.js calls window.MtgCardModels). Without this script the page does
// the same with its own confirm box (?remove=).
(function () {
  "use strict";
  var page = document.querySelector(".mm-page");
  if (!page) return;
  var q = page.querySelector("[data-models-filter]");
  var list = page.querySelector("[data-models-list]");
  var none = page.querySelector("[data-models-none]");
  var count = page.querySelector("[data-models-count]");

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

  // #m-<card>: that card's row, open and in view
  function openFromHash() {
    var id = decodeURIComponent((location.hash || "").slice(1));
    if (!/^m-/.test(id)) return;
    var li = document.getElementById(id);
    if (!li || !li.classList.contains("mm-item")) return;
    var d = li.querySelector("details");
    if (d) d.open = true;
    li.scrollIntoView({ block: "start" });
    var s = li.querySelector("summary");
    if (s) s.focus({ preventScroll: true });
  }
  openFromHash();
  window.addEventListener("hashchange", openFromHash);

  function remove(li, back) {
    var card = li.getAttribute("data-card");
    var rm = li.querySelector("a[data-remove-model]");
    var form = document.getElementById("mm-rm-f");
    var Box = window.MtgBox;
    if (!form || !Box || !Box.confirm) { location.href = rm.getAttribute("href"); return; }
    Box.confirm({
      title: "Remove the card model of “" + card + "”?",
      text: "Later solo tests play “" + card + "” without its effect again, until a new model is saved. The reports that already played it keep their numbers.",
      ok: "Remove",
      cancel: "Keep it",
      danger: true,
      icon: "trash",
      returnFocus: back || rm,
      onOk: function () {
        form.elements.card.value = card;
        if (form.requestSubmit) form.requestSubmit(); else form.submit();
        return true;
      }
    });
  }
  page.addEventListener("click", function (e) {
    var rm = e.target.closest ? e.target.closest("a[data-remove-model]") : null;
    if (!rm) return;
    e.preventDefault();
    remove(rm.closest("li.mm-item"), rm);
  });

  window.MtgCardModels = {
    toggle: function (li) {
      var d = li.querySelector("details");
      if (!d) return;
      d.open = !d.open;
      if (d.open) li.querySelector("summary").focus();
    },
    ask: function (li) {
      var btn = li.querySelector(".copybtn");
      if (!btn) return;
      var d = li.querySelector("details");
      if (d) d.open = true;  // the press's "Copied" is seen on the row's own button
      btn.click();
    },
    remove: remove
  };
})();
