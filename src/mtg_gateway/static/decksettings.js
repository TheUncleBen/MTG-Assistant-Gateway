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

// Deck settings: each change is saved to Archidekt as it is made (owner decision C4, 0.7.46): a
// name typed, a switch flipped or a choice picked in Main settings or Categories goes to the
// page's own form address with json=1 (companion_settings.py answers JSON), one at a time in the
// order they were made. Saved says so with Undo, which puts the value back and saves that;
// refused, the control goes back to what is saved and the red note says why. A category change
// that needs a yes first (the commander, which cards count) asks in a box with the gateway's own
// words, then goes with confirmed=1. The Save buttons are for a page without scripts and are not
// shown. Leaving while a save is still out asks first.
(function () {
  "use strict";
  var main = document.getElementById("ds-main");
  if (!main || !window.fetch || !window.FormData || !window.URLSearchParams) return;
  var Menu = window.MtgMenu, Box = window.MtgBox;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  function say(text, opts) { if (Menu && Menu.toast) Menu.toast(text, opts); }
  function button(text, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.textContent = text;
    b.addEventListener("click", onClick);
    return b;
  }
  $$(".ds-save").forEach(function (n) { n.hidden = true; });
  var foot = $(".foot[data-instant]", main);
  if (foot) foot.textContent = foot.getAttribute("data-instant");
  var inFlight = 0, queue = Promise.resolve();
  window.addEventListener("beforeunload", function (ev) {
    if (inFlight) { ev.preventDefault(); ev.returnValue = ""; }
  });
  function post(form, extra) {
    var fd = new FormData(form);
    fd.set("json", "1");
    Object.keys(extra || {}).forEach(function (k) { fd.set(k, extra[k]); });
    inFlight++;
    return fetch(form.getAttribute("action").split("#")[0], {
      method: "POST", credentials: "same-origin", body: new URLSearchParams(fd),
      headers: { Accept: "application/json" }
    }).then(function (r) { return r.json(); }).then(function (d) { inFlight--; return d || { ok: false }; }, function () {
      inFlight--;
      return { ok: false, message: "No connection; nothing was changed." };
    });
  }
  function nothingToSave(d) { return /^Nothing to save/.test(d.message || ""); }

  // -- Main settings: one field at a time; the hidden was_ fields follow each save ---------------
  function fieldValue(c) { return c.type === "checkbox" ? c.checked : c.value; }
  function setField(c, v) {
    if (c.type === "checkbox") c.checked = v === true || v === "1";
    else c.value = v;
    if (window.MtgSelect && c.tagName === "SELECT" && window.MtgSelect.refresh) window.MtgSelect.refresh(c);
  }
  function mainField(c) { return c && c.name && !/^(csrf|was_|json)/.test(c.name) && c.type !== "hidden"; }
  var mainSaved = {};  // field name -> the value saved last, for the Undo
  $$("input[name], select[name], textarea[name]", main).forEach(function (c) { if (mainField(c)) mainSaved[c.name] = fieldValue(c); });
  function drawWas(was) {
    Object.keys(was).forEach(function (k) {
      var h = $("input[name='was_" + k + "']", main);
      if (h) h.value = was[k];
      var c = $("[name='" + k + "']:not([type=hidden])", main);
      if (!c || k === "description") return;
      if (c.tagName === "SELECT" && !$("option[value='" + was[k] + "']", c)) return;
      setField(c, was[k]);
      mainSaved[k] = fieldValue(c);
    });
  }
  function saveMain(c, prev) {
    var now = fieldValue(c);
    queue = queue.then(function () {
      return post(main).then(function (d) {
        if (d.ok || nothingToSave(d)) {
          if (d.was) drawWas(d.was);
          mainSaved[c.name] = now;
          if (!d.ok) return;  // back to what was saved: nothing to say
          say(d.message, { ok: true, actions: [button("Undo", function () { setField(c, prev); saveMain(c, now); })] });
          return;
        }
        setField(c, mainSaved[c.name]);
        say(d.message || "The change could not be saved.", { error: true });
      });
    });
  }
  main.addEventListener("change", function (e) {
    var c = e.target;
    if (!mainField(c)) return;
    saveMain(c, mainSaved[c.name]);
  });
  main.addEventListener("submit", function (e) { e.preventDefault(); });

  // -- Categories: a row's name or switch; the list is drawn again from the gateway's answer ----
  function catRow(c) { return c.closest ? c.closest("li[data-cat]") : null; }
  function catState(li) {
    var out = {};
    $$("input[name]", li).forEach(function (i) {
      if (/^was_/.test(i.name)) return;
      out[i.name] = i.type === "checkbox" ? i.checked : i.value;
    });
    return out;
  }
  function catWas(li) {
    var cid = li.getAttribute("data-cat"), out = {};
    $$("input[name^='was_']", li).forEach(function (h) {
      var key = h.name.slice(4);  // was_premier_12 -> premier_12
      if (key.slice(-cid.length - 1) !== "_" + cid) return;
      out[key] = key.indexOf("name_") === 0 ? h.value : h.value === "1";
    });
    return out;
  }
  function drawCat(li, state) {
    Object.keys(state).forEach(function (k) {
      var i = $("input[name='" + k + "']", li);
      if (i) setField(i, state[k]);
    });
  }
  function swapCats(html) {
    var box = document.createElement("div");
    box.innerHTML = html;
    var cats = document.getElementById("categories"), fresh = $("#categories", box) || box;
    if (!cats) return;
    ["ul.ds-cats", ".acts.ds-unused"].forEach(function (sel) {
      var now = $(sel, cats), next = $(sel, fresh);
      if (now && next) now.replaceWith(next);
      else if (now && !next) now.remove();
      else if (next && !now && sel === ".acts.ds-unused") { var acts = $("#catform .acts", cats); if (acts) acts.after(next); }
    });
    if (fresh.hasAttribute("data-names")) cats.setAttribute("data-names", fresh.getAttribute("data-names"));
    $$(".ds-save", cats).forEach(function (n) { n.hidden = true; });
  }
  function saveCats(cid, prev, extra) {
    queue = queue.then(function () {
      var form = document.getElementById("catform");
      var li = form && $("li[data-cat='" + cid + "']", form);
      if (!li) return null;
      var now = catState(li);
      return post(form, extra).then(function (d) {
        if (d.needs_confirm && Box && Box.confirm) {
          var yes = false;
          Box.confirm({
            title: d.title || "", text: d.text || "", ok: "Save categories", icon: "check",
            onOk: function () { yes = true; saveCats(cid, prev, { confirmed: "1" }); },
            onClose: function () { if (!yes) drawCat(li, prev); }
          });
          return;
        }
        if (d.ok || nothingToSave(d)) {
          if (d.html) swapCats(d.html);
          if (!d.ok) return;
          say(d.message, { ok: true, actions: [button("Undo", function () {
            var again = document.getElementById("catform"), row = again && $("li[data-cat='" + cid + "']", again);
            if (!row) return;
            var before = catWas(row);
            drawCat(row, prev);
            saveCats(cid, before);
          })] });
          return;
        }
        drawCat(li, prev);
        say(d.message || "The change could not be saved.", { error: true });
      });
    });
  }
  document.addEventListener("change", function (e) {
    var c = e.target, form = c && c.form;
    if (!form || form.id !== "catform" || !c.name || /^was_/.test(c.name)) return;
    var li = catRow(c);
    if (!li) return;
    saveCats(li.getAttribute("data-cat"), catWas(li));
  });
  // Enter in a name box: its change is saved (above); the form itself never goes, and the
  // confirm step menus.js keeps for a page that sends the form is not asked twice
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form && form.id === "catform") { e.preventDefault(); e.stopPropagation(); }
  }, true);
})();
