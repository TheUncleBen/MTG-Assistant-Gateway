/* The admin pages (admin.py, activity_view.py): the rows' menus and the small behaviours the pages
   need with scripts. On Users a member's row opens their side panel on a click and has a menu
   (right-click, press and hold, the ⋮ or Shift+F10) with Enable or Disable…, Revoke tokens and
   sessions, Unlink Archidekt, Activity and Delete data…; on Card models the menu has Show this
   member's models only, Open card and Remove…; on Activity it has Show this member only, Open this
   setting and Copy details. A Metrics row opens its details in place. The Delete data box's red
   button stays off until its tick is set. Without scripts every ⋮ is a link to the row's own page
   or box, and the rows' forms work as they are. */
(function () {
  "use strict";
  var Menu = window.MtgMenu;
  if (!Menu) return;
  var item = Menu.item, link = Menu.link, sep = Menu.sep;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  // a POST as the panel's forms send it (the form token from the page)
  function post(action, fields) {
    var f = document.createElement("form");
    f.method = "post";
    f.action = action;
    f.hidden = true;
    var all = { csrf: Menu.csrf() };
    Object.keys(fields).forEach(function (k) { all[k] = fields[k]; });
    Object.keys(all).forEach(function (k) {
      var i = document.createElement("input");
      i.type = "hidden"; i.name = k; i.value = all[k];
      f.appendChild(i);
    });
    document.body.appendChild(f);
    f.submit();
  }

  // -- Users ------------------------------------------------------------------------------------
  function userRowOf(node) {
    var tr = node && node.closest ? node.closest(".au .ui-table .tr.mem") : null;
    return tr;
  }
  function openUserMenu(tr, x, y) {
    var name = tr.getAttribute("data-name") || "this member";
    var act = tr.getAttribute("data-act");
    var you = tr.hasAttribute("data-you");
    var items = [];
    if (tr.hasAttribute("data-disabled")) items.push(item("Enable", "check", function () { post(act, { action: "enable" }); }));
    else if (!you) items.push(link("Disable…", "x", tr.getAttribute("data-box-disable"), { danger: true }));
    items.push(item("Revoke tokens and sessions", "lock", function () { post(act, { action: "revoke" }); }));
    if (tr.hasAttribute("data-linked")) items.push(item("Unlink Archidekt", "link", function () { post(act, { action: "unlink" }); }));
    items.push(link("Activity", "activity", tr.getAttribute("data-activity")));
    if (!you) { items.push(sep()); items.push(link("Delete data…", "trash", tr.getAttribute("data-box-delete"), { danger: true })); }
    Menu.open({ head: name, label: "Member: " + name, items: items, focusBack: $(".kebab", tr) || tr }, x, y);
  }
  // the whole row opens the member's panel (the name is the link without scripts)
  document.addEventListener("click", function (e) {
    var tr = userRowOf(e.target);
    if (!tr || e.defaultPrevented) return;
    if (e.target.closest("a, button, input, select, label")) return;
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    var href = tr.getAttribute("data-open");
    if (href) location.href = href;
  });

  // -- Card models ------------------------------------------------------------------------------
  function modelRowOf(node) {
    return node && node.closest ? node.closest(".amo .ui-table .tr.model") : null;
  }
  function openModelMenu(tr, x, y) {
    var card = tr.getAttribute("data-card") || "Card";
    var who = tr.getAttribute("data-who") || "this member";
    var items = [
      link("Show " + who + "'s models only", "user", tr.getAttribute("data-member")),
      link("Open card", "eye", tr.getAttribute("data-open")),
      sep(),
      link("Remove…", "trash", tr.getAttribute("data-remove"), { danger: true })
    ];
    Menu.open({ head: card, label: "Card model: " + card, items: items, focusBack: $(".kebab", tr) || tr }, x, y);
  }

  // -- Activity ---------------------------------------------------------------------------------
  function eventRowOf(node) {
    return node && node.closest ? node.closest(".act-admin .act-table .tr.ev") : null;
  }
  function openEventMenu(tr, x, y) {
    var name = $(".c-ev .en", tr) ? $(".c-ev .en", tr).textContent.replace(/\s+/g, " ").trim() : "Event";
    var items = [];
    $$(".more-body .ui-actions a", tr).forEach(function (a) {
      var text = a.textContent.trim();
      items.push(link(text, /setting/i.test(text) ? "settings" : "user", a.getAttribute("href")));
    });
    items.push(item("Copy details", "copy", function () {
      var lines = [name];
      $$(".more-body dt", tr).forEach(function (dt) {
        var dd = dt.nextElementSibling;
        lines.push(dt.textContent.trim() + ": " + (dd ? dd.textContent.replace(/\s+/g, " ").trim() : ""));
      });
      Menu.copyAndSay(lines.join("\n"), "Details copied.");
    }));
    Menu.open({ head: name, label: "Event: " + name, items: items, focusBack: $(".kebab", tr) || tr }, x, y);
  }

  // -- the ways in, for every kind at once ------------------------------------------------------
  var KINDS = [
    { of: userRowOf, open: openUserMenu },
    { of: modelRowOf, open: openModelMenu },
    { of: eventRowOf, open: openEventMenu }
  ];
  function itemOf(target) {
    if (!target || !target.closest) return null;
    if (target.closest(".ctxmenu, .mtgbox, .more-body")) return null;
    for (var i = 0; i < KINDS.length; i++) {
      var got = KINDS[i].of(target);
      if (got) { got.__admKind = KINDS[i]; return got; }
    }
    return null;
  }
  Menu.bind(document, {
    itemOf: itemOf,
    skip: "button, input, select, textarea, label, a:not(.kebab)",
    button: ".kebab",
    open: function (node, x, y) { node.__admKind.open(node, x, y); }
  });

  // -- Metrics: a row opens its details in place (without scripts the name reloads with ?open=)
  document.addEventListener("click", function (e) {
    var tr = e.target.closest && e.target.closest(".am .ui-table .tr.mrow");
    if (!tr || e.defaultPrevented) return;
    if (e.target.closest(".more-body")) return;
    var body = $(".more-body", tr), a = $(".title .t", tr);
    if (!body) return;
    e.preventDefault();
    var open = body.hidden;
    body.hidden = !open;
    tr.classList.toggle("open", open);
    if (a) a.setAttribute("aria-expanded", open ? "true" : "false");
  });

  // -- the Delete data box: its red button only once the tick is set -----------------------------
  function gate() {
    $$("[data-needs-tick]").forEach(function (b) {
      var form = b.form || (b.getAttribute("form") ? document.getElementById(b.getAttribute("form")) : null);
      var tick = form ? $("input[type=checkbox]", form) : null;
      if (!tick) return;
      var check = function () { b.disabled = !tick.checked; };
      tick.addEventListener("change", check);
      check();
    });
  }
  gate();
})();
