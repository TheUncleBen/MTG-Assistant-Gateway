"use strict";
// Deck settings, parity release 2: the Add template box (companion_settings.template_box_html)
// reads the member's category templates when it opens (/decks/api/templates), leaves out the
// names the deck already has (#categories[data-names]) and counts the ticks into its button
// ("Add 2 categories"). Without scripts the Add template… link opens the page with the box drawn
// open and the list read by the gateway.
(function () {
  var box = document.getElementById("tplbox");
  if (!box || !window.MtgBox) return;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var tree = $(".tree", box), go = $(".acts button[type=submit]", box);
  var iconTpl = $("template.tpl-icon", box);
  var section = document.getElementById("categories");
  var noneLeft = box.getAttribute("data-none-left") || "Every template's name is already a category in this deck.";

  function haveNames() {
    var out = [];
    try { out = JSON.parse(section && section.getAttribute("data-names") || "[]"); } catch (e) { out = []; }
    // names typed into the boxes but not saved count too: the deck would refuse a duplicate
    $$("#categories input[name^='name_']").forEach(function (i) { if (i.value.trim()) out.push(i.value.trim()); });
    return out.map(function (n) { return n.toLowerCase(); });
  }
  function count() {
    var n = $$("input[name=template]:checked", box).length;
    if (!go) return;
    var last = go.lastChild;
    // nothing ticked: "Add categories" (the button stays off), never a count it does not have
    var text = n === 0 ? " Add categories" : " Add " + n + (n === 1 ? " category" : " categories");
    if (last && last.nodeType === 3) last.textContent = text; else go.appendChild(document.createTextNode(text));
    go.disabled = !$$("input[name=template]", box).length || n === 0;
  }
  function p(cls, text) { var e = document.createElement("p"); e.className = cls; e.textContent = text; return e; }
  function draw(templates) {
    var have = haveNames();
    tree.textContent = "";
    var free = templates.filter(function (t) { return have.indexOf(t.name.toLowerCase()) < 0; });
    if (!free.length) { tree.appendChild(p("muted small", noneLeft)); count(); return; }
    free.forEach(function (t) {
      var lab = document.createElement("label");
      var c = document.createElement("input");
      c.type = "checkbox"; c.name = "template"; c.value = t.name;
      lab.appendChild(c);
      if (iconTpl) lab.appendChild(iconTpl.content.cloneNode(true));
      lab.appendChild(document.createTextNode(" " + t.name));
      tree.appendChild(lab);
    });
    count();
  }
  function load() {
    if (box.__loaded || !tree) return;
    box.__loaded = true;
    tree.textContent = "";
    var wait = p("muted small loading", "Reading your templates from Archidekt…");
    wait.setAttribute("role", "status");
    tree.appendChild(wait);
    if (go) go.disabled = true;
    fetch("/decks/api/templates", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { box.__loaded = false; tree.textContent = ""; tree.appendChild(p("muted small", d.message || "Your templates could not be read.")); return; }
        draw(d.templates || []);
      }, function () { box.__loaded = false; tree.textContent = ""; tree.appendChild(p("muted small", "No connection; your templates could not be read.")); });
  }
  box.addEventListener("change", function (e) { if (e.target && e.target.name === "template") count(); });
  document.addEventListener("click", function (e) {
    var opener = e.target && e.target.closest ? e.target.closest("[data-box-open='tplbox']") : null;
    if (!opener || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    var n = $(".boxnote", box);
    if (n) { n.hidden = true; n.textContent = ""; }
    if ($$("input[name=template]", box).length) count(); else load();
  }, true);
  if (box.hasAttribute("data-open") || $$("input[name=template]", box).length) { box.__loaded = true; count(); }
  // the form goes with the page's own check first: a press with nothing ticked says so in the box
  var form = document.getElementById("tplbox-f");
  if (form) form.addEventListener("submit", function (e) {
    if ($$("input[name=template]:checked", box).length) return;
    e.preventDefault();
    var b = window.MtgBox.current();
    if (b && b.panel === box) b.note("Tick at least one template.", true);
  });
})();
