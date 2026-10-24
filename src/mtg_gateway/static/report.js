"use strict";
// Report page: the shared card menu (M9) on the card rows of "Cards Forge left out" (.rv-cards .tr[data-card])
// and on the card names of the solo test's groups (button.cardlink[data-card], which static/menus.js already
// serves). A row opens on right-click, press and hold, its ⋮ button, Enter or the ContextMenu key; "Open card"
// shows the card in the viewer (static/cardview.js) when the page has one, else the card's own search.
(function () {
  var Menu = window.MtgMenu, Card = window.MtgCardMenu;
  if (!Menu || !Card) return;
  var enc = encodeURIComponent;

  function rowOf(node) {
    var tr = node && node.closest ? node.closest(".rv-cards .tr[data-card]") : null;
    return tr && !tr.closest(".ctxmenu, .mtgbox, .cardview") ? tr : null;
  }
  function openCard(name, row) {
    var View = window.MtgCardView;
    if (View && View.open) {
      var sub = row.querySelector(".title .s");
      var base = { name: name, img: "", type: sub ? sub.textContent : "", loading: !!View.text };
      View.open(base, []);
      if (View.text) View.text(name).then(function (t) {
        var card = {};
        Object.keys(base).forEach(function (k) { card[k] = base[k]; });
        if (t) Object.keys(t).forEach(function (k) { if (t[k] || k === "faces") card[k] = t[k]; });
        delete card.loading;
        View.update(card, []);
      });
      return;
    }
    Menu.whenClosed(function () { location.href = "/cards?q=" + enc(name); });
  }
  function openRowMenu(row, x, y) {
    var name = row.getAttribute("data-card");
    Card.open({
      name: name,
      onOpen: function () { openCard(name, row); },
      addHref: "/cards?q=" + enc(name),
      collect: true,
      watch: Card.enabled() ? { watched: false, onWatch: function () { Card.watch({ name: name }, { was: false }).catch(function () {}); } } : null,
      focusBack: row
    }, x, y);
  }
  Menu.bind(document, {
    itemOf: rowOf,
    skip: "a",
    button: ".rv-cards .kebab",
    open: openRowMenu
  });
  // a tap or Enter on the row itself opens the card
  document.addEventListener("click", function (e) {
    var row = rowOf(e.target);
    if (!row || e.defaultPrevented || e.target.closest(".kebab, a, button")) return;
    e.preventDefault();
    openCard(row.getAttribute("data-card"), row);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var row = rowOf(e.target);
    if (!row || e.target !== row) return;
    e.preventDefault();
    openCard(row.getAttribute("data-card"), row);
  });
})();
