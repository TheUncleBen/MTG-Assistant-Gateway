/* My lists (/lists, lists_page.py): the rows' menus (the ⋮, a right-click, a press and hold) and
   the boxes the page holds, filled from the row they were opened for; the pins' copies and notes
   saved in place; the custom card's picture chosen, cropped here and sent with its crop box; Load
   more and the search as you type. Everything works without this file: the ⋮ is then a plain
   drop-down of links to the old addresses, which draw the box open on the page.
   Needs static/cardmenu.js (window.MtgMenu) and static/box.js (window.MtgBox); static/printpick.js
   for Change printing…. */
(function () {
  "use strict";
  var Menu = window.MtgMenu, Box = window.MtgBox;
  var root = document.querySelector(".lp[data-tab]");
  if (!root || !Menu || !Box) return;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var el = Menu.el, icon = Menu.icon, toast = Menu.toast;
  var tab = root.getAttribute("data-tab");
  var NO_CONNECTION = "No connection; nothing was changed.";

  // -- the rows ------------------------------------------------------------------------------------
  function rowOf(target) {
    if (!target || !target.closest) return null;
    if (target.closest(".ctxmenu, .mtgbox, .printpick, details.rowmenu .menu")) return null;
    return target.closest(".lp .ui-table .tr[data-row]");
  }
  function dataOf(tr) {
    try { return JSON.parse(tr.getAttribute("data-row") || "{}"); } catch (e) { return {}; }
  }
  function nameOf(tr) { return tr.getAttribute("data-name") || ""; }
  function kebabOf(tr) { return $(".kebab", tr); }

  // (a form's .action is its action= input here, so the attribute is read instead)
  function postForm(path, fields) {
    var body = new URLSearchParams();
    Object.keys(fields).forEach(function (k) { if (fields[k] !== undefined && fields[k] !== null) body.append(k, fields[k]); });
    if (!fields.csrf) body.append("csrf", Menu.csrf());
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: body.toString()
    }).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () {
        return { ok: false, status: r.status, message: "The gateway sent an answer this page could not read. Reload the page to see whether the change was made." };
      });
    });
  }
  function said(d, fallback) {
    if (d.verified === false) toast(d.notice || fallback, { error: true });
    else toast(d.notice || fallback, { ok: true, ms: 5000 });
  }

  // -- the boxes -----------------------------------------------------------------------------------
  function fill(text, name) { return (text || "").replace("{name}", name); }
  function setButtonText(btn, text) {
    if (!btn) return;
    var last = btn.lastChild;
    if (last && last.nodeType === 3) last.textContent = (btn.firstChild !== last ? " " : "") + text;
    else btn.appendChild(document.createTextNode(text));
  }
  function retitle(box, mode, name) {
    var t = box.getAttribute(mode === "edit" ? "data-t-edit" : mode === "new" ? "data-t-new" : "data-t");
    var h = $(".bh h2", box);
    if (t && h) h.textContent = fill(t, name);
    var ok = box.getAttribute(mode === "edit" ? "data-ok-edit" : mode === "new" ? "data-ok-new" : "data-ok");
    var btns = $$(".acts button[type=submit]", box);
    if (ok) setButtonText(btns[btns.length - 1], fill(ok, name));
  }
  function setValue(box, name, value) {
    var i = $("[name='" + name + "']", box);
    if (i) i.value = value == null ? "" : String(value);
    return i;
  }
  function countOf(field) {
    var id = field.getAttribute("data-count"), c = id ? document.getElementById(id) : null;
    if (c) c.textContent = field.value.length + " / " + (field.getAttribute("maxlength") || "");
  }
  function clearNote(box) {
    var n = $(".boxnote", box);
    if (n) { n.hidden = true; n.textContent = ""; }
  }
  var fillers = {
    "tpl-box": function (box, mode, d) {
      var editing = mode === "edit";
      setValue(box, "action", editing ? "edit" : "create");
      setValue(box, "was_name", editing ? d.name : "");
      setValue(box, "name", editing ? d.name : "");
      ["in_deck", "in_price", "premier"].forEach(function (k) {
        var c = $("[name='" + k + "']", box);
        if (c) c.checked = editing ? !!d[k] : k !== "premier";
      });
      $$("[data-edit-only]", box).forEach(function (x) { x.hidden = !editing; });
    },
    "tpl-del": function (box, mode, d) {
      setValue(box, "was_name", d.name);
      var n = $("[data-name]", box);
      if (n) n.textContent = d.name;
    },
    "def-box": function (box, mode, d) {
      setValue(box, "card", d.name);
      var cats = d.categories || [];
      $$("input[name=cat]", box).forEach(function (c) { c.checked = cats.indexOf(c.value) >= 0; });
      $$("input[name=main]", box).forEach(function (r) { r.checked = cats.length > 0 && r.value === cats[0]; });
    },
    "def-clear": function (box, mode, d) { setValue(box, "card", d.name); },
    "tag-box": function (box, mode, d) {
      var editing = mode === "edit";
      setValue(box, "action", editing ? "edit" : "create");
      setValue(box, "was_name", editing ? d.name : "");
      setValue(box, "name", editing ? d.name : "");
      var color = editing && d.color ? d.color : (box.getAttribute("data-default-color") || "#fa890d");
      var hex = $("#tag-h", box), pick = $("#tag-c", box);
      if (hex) hex.value = color;
      if (pick) pick.value = color;
      tagPreview(box);
      $$("[data-edit-only]", box).forEach(function (x) { x.hidden = !editing; });
    },
    "tag-del": function (box, mode, d) { setValue(box, "was_name", d.name); },
    "pin-rm": function (box, mode, d) { setValue(box, "pin_id", d.id); },
    "pin-all": function () {},
    "cc-box": function (box, mode, d) {
      var editing = mode === "edit";
      setValue(box, "action", editing ? "edit" : "create");
      setValue(box, "was_name", editing ? d.name : "");
      var keys = { n: "Name", m: "ManaCost", t: "Types", st: "SubTypes", su: "SuperTypes", x: "Text", p: "Power", tg: "Toughness", l: "Loyalty", a: "ArtistName" };
      [["f", "front"], ["b", "back"]].forEach(function (pf) {
        Object.keys(keys).forEach(function (k) {
          setValue(box, pf[0] + "_" + k, editing ? (d[pf[1] + keys[k]] || "") : "");
        });
        var crop = $(".lp-crop[data-crop='" + pf[0] + "']", box);
        if (crop) resetCrop(crop, editing && !!d[pf[1] + "ImageUrl"]);
      });
      setValue(box, "set_code", editing ? (d.setCode || "") : "");
      setValue(box, "collector_number", editing ? (d.collectorNumber || "") : "");
      var back = $("#cc-back", box);
      if (back) back.checked = editing && !!d.hasBack;
      backFace(box);
      showFace(box, "f");
      var err = $("#cc-err", box);
      if (err) { err.hidden = true; err.textContent = ""; }
      box.__ccName = editing ? d.name : "";
      $$(".lp-pick", box).forEach(function (b) {
        b.disabled = !editing;
        if (editing) b.removeAttribute("title"); else b.title = "Create the card first";
      });
      $$(".lp-img > div > p.muted:not(.lp-pichelp)", box).forEach(function (p) { p.hidden = editing; });
    },
    "cc-del": function (box, mode, d) {
      setValue(box, "was_name", d.name);
      var gate = $("[data-gate]", box);
      if (gate) { gate.checked = false; gateButton(box, false); }
    }
  };
  function openBox(id, mode, tr, back) {
    var box = document.getElementById(id);
    if (!box) return;
    var d = tr ? dataOf(tr) : {};
    var name = tr ? nameOf(tr) : "";
    clearNote(box);
    if (fillers[id]) fillers[id](box, mode, d);
    retitle(box, mode, name);
    $$("[data-count]", box).forEach(countOf);
    Box.openElement(box, { returnFocus: back || (tr ? kebabOf(tr) : null) });
  }
  // the New buttons: the box is emptied before box.js opens it (this runs first, in capture)
  document.addEventListener("click", function (e) {
    var opener = e.target && e.target.closest ? e.target.closest("[data-box-open]") : null;
    if (!opener || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    var box = document.getElementById(opener.getAttribute("data-box-open"));
    if (!box || !fillers[box.id] || opener.closest(".tr")) return;
    clearNote(box);
    fillers[box.id](box, "new", {});
    retitle(box, "new", "");
    $$("[data-count]", box).forEach(countOf);
  }, true);

  // -- the menu ------------------------------------------------------------------------------------
  function openRowMenu(tr, x, y) {
    var links = $$("details.rowmenu .menu > *", tr);
    var items = [];
    links.forEach(function (a) {
      if (a.classList.contains("head")) return;
      if (a.classList.contains("sep")) { items.push(Menu.sep()); return; }
      var text = a.textContent.replace(/\s+/g, " ").trim(), ic = a.getAttribute("data-icon") || "";
      var danger = a.classList.contains("danger");
      if (a.hasAttribute("data-box")) {
        items.push(Menu.item(text, ic, function () { openBox(a.getAttribute("data-box"), a.getAttribute("data-mode") || "", tr); }, { danger: danger }));
      } else if (a.hasAttribute("data-printing")) {
        items.push(Menu.item(text, ic, function () { changePrinting(tr); }, { danger: danger }));
      } else {
        items.push(Menu.link(text, ic, a.getAttribute("href"), { danger: danger }));
      }
    });
    Menu.open({ head: nameOf(tr), label: "Menu for " + nameOf(tr), items: items, focusBack: kebabOf(tr) }, x, y);
  }
  Menu.bind(root, {
    itemOf: rowOf,
    skip: "button, input, select, textarea, summary, a, form, label",
    button: ".kebab",
    open: openRowMenu
  });

  // -- counters, the edit-only notes, the gated delete -----------------------------------------------
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (t && t.hasAttribute && t.hasAttribute("data-count")) countOf(t);
  });
  function gateButton(box, on) {
    $$("[data-gated]", box).forEach(function (b) {
      b.disabled = !on;
      if (on) b.removeAttribute("data-gate-off"); else b.setAttribute("data-gate-off", "");
    });
  }
  $$("[data-gate]").forEach(function (c) {
    var box = c.closest(".ui-box");
    gateButton(box, c.checked);
    c.addEventListener("change", function () { gateButton(box, c.checked); });
  });
  // one press per form: the button goes busy until the page comes back
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f || !f.closest || !f.closest(".lp-box") || f.hasAttribute("data-fetches")) return;
    var box = f.closest(".ui-box");
    $$(".acts button[type=submit]", box).forEach(function (b) { b.disabled = true; b.setAttribute("aria-busy", "true"); });
  });

  // -- Color tags: the picker, the hex and the preview ------------------------------------------------
  function tagPreview(box) {
    var hex = $("#tag-h", box), pick = $("#tag-c", box), pv = $("#tag-pv", box), name = $("#tag-n", box);
    if (!hex || !pv) return;
    var v = hex.value.trim();
    var ok = /^#[0-9a-fA-F]{6}$/.test(v);
    if (ok) {
      if (pick) pick.value = v.toLowerCase();
      pv.style.borderColor = v; pv.style.color = v;
      var sw = $(".lp-swatch", pv);
      if (sw) sw.style.background = v;
    }
    var nm = $(".nm", pv);
    if (nm) nm.textContent = (name && name.value.trim()) || "Preview";
  }
  var tagBox = $("#tag-box");
  if (tagBox) {
    var hexIn = $("#tag-h", tagBox), pickIn = $("#tag-c", tagBox);
    if (pickIn) pickIn.addEventListener("input", function () { if (hexIn) hexIn.value = pickIn.value; tagPreview(tagBox); });
    if (hexIn) hexIn.addEventListener("input", function () { tagPreview(tagBox); });
    var tagName = $("#tag-n", tagBox);
    if (tagName) tagName.addEventListener("input", function () { tagPreview(tagBox); });
    tagPreview(tagBox);
  }

  // -- Default categories: ticks and the main one -------------------------------------------------------
  var defBox = $("#def-box");
  if (defBox) {
    defBox.addEventListener("change", function (e) {
      var t = e.target;
      if (t.name === "main") {
        var c = $("input[name=cat][value='" + CSS.escape(t.value) + "']", defBox);
        if (c) c.checked = true;
      } else if (t.name === "cat") {
        var mains = $$("input[name=main]", defBox);
        var main = mains.filter(function (r) { return r.checked; })[0];
        var ticked = $$("input[name=cat]", defBox).filter(function (c) { return c.checked; });
        if (!t.checked && main && main.value === t.value) main.checked = false;
        main = mains.filter(function (r) { return r.checked; })[0];
        if (!main && ticked.length) {
          var r = mains.filter(function (x) { return x.value === ticked[0].value; })[0];
          if (r) r.checked = true;
        }
      }
    });
    var defForm = $("#def-f", defBox);
    if (defForm) defForm.addEventListener("submit", function (e) {
      if ($$("input[name=cat]", defBox).some(function (c) { return c.checked; })) return;
      e.preventDefault();
      var note = $(".boxnote", defBox);
      if (note) { note.hidden = false; note.className = "notice error boxnote"; note.textContent = "Tick at least one category, or use Clear… to remove them all."; }
    });
  }
  // the search narrows the rows on the page as you type (the whole list is on the page or a Load
  // more away)
  function filterRows(q) {
    q = q.trim().toLowerCase();
    var n = 0;
    $$(".lp .ui-table .tr[data-q]").forEach(function (tr) {
      var on = !q || tr.getAttribute("data-q").indexOf(q) >= 0;
      tr.hidden = !on;
      if (on) n++;
    });
    var status = $("[data-filter-count]");
    if (status) status.textContent = q ? n + " shown." : "";
  }
  var searchIn = $(".lp .ui-tools input[type=search]");
  if (searchIn && tab === "defaults") {
    searchIn.addEventListener("input", function () { filterRows(searchIn.value); });
    if (searchIn.value) filterRows(searchIn.value);
  }
  // Custom cards: Archidekt searches; the rows come back as the next page's do
  var searchTimer = null;
  if (searchIn && tab === "custom") {
    var form = searchIn.closest("form");
    var run = function () {
      var q = searchIn.value.trim();
      var href = "/lists/custom" + (q ? "?q=" + encodeURIComponent(q) : "");
      loadRows(href, true).then(function () {
        try { history.replaceState(null, "", href); } catch (e) { /* no history */ }
      });
    };
    searchIn.addEventListener("input", function () { clearTimeout(searchTimer); searchTimer = setTimeout(run, 400); });
    if (form) form.addEventListener("submit", function (e) { e.preventDefault(); clearTimeout(searchTimer); run(); });
  }

  // -- Load more -------------------------------------------------------------------------------------
  function loadRows(href, replace) {
    var table = $(".lp .ui-table"), wrap = $(".lp .loadmore"), a = wrap ? $("[data-load-more]", wrap) : null;
    if (!table) return Promise.resolve();
    if (a) { a.setAttribute("aria-busy", "true"); a.setAttribute("aria-disabled", "true"); }
    return Menu.getJSON(href).then(function (d) {
      if (!d.ok) throw new Error(d.message || "");
      if (replace) $$(".tr:not(.th)", table).forEach(function (tr) { tr.remove(); });
      var tmp = el("div");
      tmp.innerHTML = d.html;
      var first = null;
      while (tmp.firstChild) { if (!first && tmp.firstChild.nodeType === 1) first = tmp.firstChild; table.appendChild(tmp.firstChild); }
      if (!$(".tr:not(.th)", table)) {
        var empty = el("div", "tr lp-empty");
        empty.appendChild(el("span", "muted", replace ? "No custom card matches that name." : "Nothing more."));
        table.appendChild(empty);
      }
      if (!wrap) { wrap = el("div", "loadmore"); table.insertAdjacentElement("afterend", wrap); }
      wrap.textContent = "";
      if (d.has_more) {
        a = el("a", "btn btn-plain", d.label);
        a.href = d.next; a.rel = "next"; a.setAttribute("data-load-more", "");
        wrap.appendChild(a);
      } else if (d.total) {
        wrap.appendChild(el("p", "muted", d.done));
      }
      var count = $(".list-head .ui-sub");
      if (count && d.total != null) count.textContent = d.total.toLocaleString();
      if (!replace && first) { var k = $(".kebab", first); if (k) k.focus({ preventScroll: true }); }
    }).catch(function () {
      if (a) { a.removeAttribute("aria-busy"); a.removeAttribute("aria-disabled"); }
      toast("No connection; the rows could not be read. Try again.", { error: true });
    });
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".lp .loadmore [data-load-more]") : null;
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    if (a.getAttribute("aria-disabled") === "true") return;
    loadRows(a.getAttribute("href"), false);
  });

  // -- Pinned cards: copies and notes saved in place ----------------------------------------------------
  function pinChanged(tr, patch) {
    var d = dataOf(tr);
    Object.keys(patch).forEach(function (k) { d[k] = patch[k]; });
    tr.setAttribute("data-row", JSON.stringify(d));
    return d;
  }
  function drawCopies(tr, q) {
    var step = $(".lp-step", tr);
    if (!step) return;
    var n = $(".n", step), bs = $$("button", step);
    if (n) n.textContent = q;
    if (bs[0]) {
      bs[0].value = String(q - 1);
      if (q <= 1) { bs[0].setAttribute("data-removes", ""); bs[0].title = "Below 1 removes the pin"; }
      else { bs[0].removeAttribute("data-removes"); bs[0].title = "One fewer"; }
    }
    if (bs[1]) { bs[1].value = String(q + 1); bs[1].disabled = q >= 100; }
  }
  root.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".lp-step button") : null;
    if (!b) return;
    e.preventDefault();
    var tr = b.closest(".tr"), form = b.closest("form");
    if (b.hasAttribute("data-removes")) { openBox("pin-rm", "", tr, b); return; }
    var want = parseInt(b.value, 10);
    if (!(want >= 1)) return;
    $$("button", form).forEach(function (x) { x.disabled = true; });
    form.setAttribute("aria-busy", "true");
    postForm(form.getAttribute("action"), { action: "edit", pin_id: $("[name=pin_id]", form).value, quantity: want }).then(function (d) {
      form.removeAttribute("aria-busy");
      $$("button", form).forEach(function (x) { x.disabled = false; });
      if (!d.ok) { drawCopies(tr, dataOf(tr).quantity); toast(d.message || "Archidekt refused the change.", { error: true }); return; }
      pinChanged(tr, { quantity: want });
      drawCopies(tr, want);
      said(d, "Saved on Archidekt.");
    }, function () {
      form.removeAttribute("aria-busy");
      $$("button", form).forEach(function (x) { x.disabled = false; });
      drawCopies(tr, dataOf(tr).quantity);
      toast(NO_CONNECTION, { error: true });
    });
  });
  $$(".lp-note textarea[data-saves]").forEach(function (ta) {
    var form = ta.closest("form"), tr = ta.closest(".tr");
    var saveNote = function () {
      var was = dataOf(tr).notes || "";
      if (ta.value === was) return;
      var text = ta.value;
      ta.setAttribute("aria-busy", "true");
      postForm(form.getAttribute("action"), { action: "edit", pin_id: $("[name=pin_id]", form).value, notes: text }).then(function (d) {
        ta.removeAttribute("aria-busy");
        if (!d.ok) { toast(d.message || "Archidekt refused the note.", { error: true }); return; }
        pinChanged(tr, { notes: text });
        said(d, "Saved on Archidekt.");
      }, function () { ta.removeAttribute("aria-busy"); toast(NO_CONNECTION, { error: true }); });
    };
    ta.addEventListener("blur", saveNote);
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ta.blur(); }
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); saveNote(); });
  });
  function changePrinting(tr) {
    var Pick = window.MtgPrintPick;
    var d = dataOf(tr);
    if (!Pick) { location.href = "/cards?q=" + encodeURIComponent(d.name); return; }
    var finish = String(d.finish || "normal").toLowerCase();
    Pick.open({
      name: d.name,
      current: d.set_code ? { set: d.set_code, number: d.collector_number } : null,
      finish: finish === "normal" ? "normal" : finish,
      returnFocus: kebabOf(tr),
      pickLabel: "Use this printing",
      onPick: function (p, info) {
        var form = $(".lp-step", tr);
        var fin = info && info.finish ? info.finish : finish;
        var ark = { normal: "Normal", foil: "Foil", etched: "Etched" }[fin] || "Normal";
        return postForm("/lists/pins", {
          action: "edit", pin_id: d.id, set_code: p.set, collector_number: p.collector_number, finish: ark
        }).then(function (r) {
          if (!r.ok) { toast(r.message || "Archidekt refused the printing.", { error: true }); return false; }
          said(r, "Saved on Archidekt.");
          pinChanged(tr, { set_code: p.set, collector_number: p.collector_number, set_name: p.set_name || "", finish: ark });
          var s = $(".title .s", tr);
          if (s) s.textContent = (p.set_name ? p.set_name + " (" + String(p.set).toUpperCase() + ") " : String(p.set).toUpperCase() + " ") + p.collector_number + (ark !== "Normal" ? " · " + ark : "");
          var img = $(".lp-thumb", tr);
          if (img && img.tagName === "IMG" && p.uid) img.src = img.src.replace(/\/[^/]+\.(jpg|png)(\?|$)/, "/" + p.uid + ".jpg$2");
          if (form) form.focus && form.focus();
          return true;
        }, function () { toast(NO_CONNECTION, { error: true }); return false; });
      }
    });
  }

  // -- Custom cards: faces, the two checks and the picture -------------------------------------------
  function showFace(box, p) {
    $$(".lp-faces [role=tab]", box).forEach(function (t) {
      var on = t.getAttribute("data-face") === p;
      t.setAttribute("aria-selected", on ? "true" : "false");
      var pane = document.getElementById(t.getAttribute("aria-controls"));
      if (pane) pane.hidden = !on;
    });
  }
  function backFace(box) {
    var back = $("#cc-back", box), t = $(".lp-faces [data-face=b]", box);
    if (!t) return;
    t.disabled = !(back && back.checked);
    if (t.disabled && t.getAttribute("aria-selected") === "true") showFace(box, "f");
  }
  var ccBox = $("#cc-box");
  if (ccBox) {
    ccBox.addEventListener("click", function (e) {
      var t = e.target.closest ? e.target.closest(".lp-faces [role=tab]") : null;
      if (t && !t.disabled) { showFace(ccBox, t.getAttribute("data-face")); return; }
      var pick = e.target.closest ? e.target.closest(".lp-pick") : null;
      if (pick && !pick.disabled) { e.preventDefault(); choosePicture(ccBox, pick); }
    });
    var backSw = $("#cc-back", ccBox);
    if (backSw) backSw.addEventListener("change", function () { backFace(ccBox); if (backSw.checked) showFace(ccBox, "b"); });
    var ccForm = $("#cc-f", ccBox);
    if (ccForm) ccForm.addEventListener("submit", function (e) {
      var n = $("#f-n", ccBox), t = $("#f-t", ccBox), err = $("#cc-err", ccBox);
      var bad = !n.value.trim() ? "Custom cards need at least a front name." : !t.value.trim() ? "Give the card at least one type." : "";
      if (!bad) { err.hidden = true; return; }
      e.preventDefault();
      e.stopImmediatePropagation();
      showFace(ccBox, "f");
      err.hidden = false;
      err.textContent = bad;
      (!n.value.trim() ? n : t).focus();
    }, true);
    backFace(ccBox);
    $$(".lp-pick", ccBox).forEach(function (b) {
      var has = $(".lp-crop", b.closest(".lp-img"));
      var editing = $("[name=action]", ccBox).value === "edit";
      b.disabled = !editing;
      if (has) resetCrop(has, has.textContent.trim() === "Picture set");
    });
    ccBox.__ccName = $("[name=was_name]", ccBox).value;
  }
  // The picture: chosen here, shown over the card-shaped frame, dragged and sized to the crop, then
  // sent as the file itself with the crop box in its pixels (the gateway redraws it).
  var MAX_BYTES = 1500000;
  function resetCrop(crop, has) {
    if (crop.__url) { try { URL.revokeObjectURL(crop.__url); } catch (e) { /* gone */ } crop.__url = null; }
    crop.textContent = has ? "Picture set" : "No picture yet";
    crop.classList.remove("has");
    crop.__img = null;
    var ui = crop.parentNode.querySelector(".lp-cropper");
    if (ui) ui.remove();
  }
  function choosePicture(box, btn) {
    var input = el("input");
    input.type = "file";
    input.accept = "image/jpeg,image/png,image/webp";
    input.className = "lp-file";
    input.addEventListener("change", function () {
      var f = input.files && input.files[0];
      input.remove();
      if (!f) return;
      if (["image/jpeg", "image/png", "image/webp"].indexOf(f.type) < 0) { toast("Choose a JPEG, PNG or WebP picture.", { error: true }); return; }
      if (f.size > MAX_BYTES) { toast("The picture is larger than 1.5 MB, Archidekt's limit. Choose a smaller one.", { error: true }); return; }
      startCrop(box, btn, f);
    });
    box.appendChild(input);
    input.click();
  }
  function startCrop(box, btn, file) {
    var p = btn.getAttribute("data-for") ? btn.getAttribute("data-for").charAt(0) : (btn.closest("#cc-face-b") ? "b" : "f");
    var face = p === "b" ? "back" : "front";
    var crop = $(".lp-crop[data-crop='" + p + "']", box);
    if (!crop) return;
    resetCrop(crop, false);
    var url = URL.createObjectURL(file);
    crop.__url = url;
    var img = el("img");
    img.alt = "";
    img.draggable = false;
    var st = { s: 1, min: 1, ox: 0, oy: 0, W: 0, H: 0, w: 0, h: 0 };
    function place() {
      var W = crop.clientWidth, H = crop.clientHeight;
      st.W = W; st.H = H;
      st.ox = Math.min(0, Math.max(W - st.w * st.s, st.ox));
      st.oy = Math.min(0, Math.max(H - st.h * st.s, st.oy));
      img.style.width = (st.w * st.s) + "px";
      img.style.height = (st.h * st.s) + "px";
      img.style.transform = "translate(" + st.ox + "px," + st.oy + "px)";
    }
    img.addEventListener("load", function () {
      st.w = img.naturalWidth; st.h = img.naturalHeight;
      if (!st.w || !st.h) { toast("That picture could not be read.", { error: true }); resetCrop(crop, false); return; }
      crop.textContent = "";
      crop.classList.add("has");
      crop.appendChild(img);
      var W = crop.clientWidth, H = crop.clientHeight;
      st.min = Math.max(W / st.w, H / st.h);
      st.s = st.min;
      st.ox = (W - st.w * st.s) / 2;
      st.oy = (H - st.h * st.s) / 2;
      place();
      zoom.min = "1"; zoom.max = "300"; zoom.value = "1";
      ui.hidden = false;
    });
    img.addEventListener("error", function () { toast("That picture could not be read.", { error: true }); resetCrop(crop, false); });
    // the controls under the frame
    var ui = el("div", "lp-cropper");
    ui.hidden = true;
    var zl = el("label", "lp-zoom");
    zl.appendChild(el("span", "small", "Size"));
    var zoom = el("input");
    zoom.type = "range";
    zoom.setAttribute("aria-label", "Picture size");
    zl.appendChild(zoom);
    ui.appendChild(zl);
    ui.appendChild(el("p", "muted small", "Drag the picture to place it in the frame."));
    var acts = el("div", "acts");
    var use = Box.button("Use this picture", "primary", null, { icon: "check" });
    var drop = Box.button("Choose another…", "btn", function () { resetCrop(crop, false); choosePicture(box, btn); });
    acts.appendChild(drop);
    acts.appendChild(use);
    ui.appendChild(acts);
    crop.insertAdjacentElement("afterend", ui);
    zoom.addEventListener("input", function () {
      var cx = st.W / 2, cy = st.H / 2;
      var ns = st.min * (1 + (parseInt(zoom.value, 10) - 1) / 100);
      st.ox = cx - (cx - st.ox) * ns / st.s;
      st.oy = cy - (cy - st.oy) * ns / st.s;
      st.s = ns;
      place();
    });
    var drag = null;
    crop.addEventListener("pointerdown", function (e) {
      if (!crop.__img) return;
      drag = { x: e.clientX, y: e.clientY, ox: st.ox, oy: st.oy };
      try { crop.setPointerCapture(e.pointerId); } catch (err) { /* no capture */ }
      e.preventDefault();
    });
    crop.addEventListener("pointermove", function (e) {
      if (!drag) return;
      st.ox = drag.ox + (e.clientX - drag.x);
      st.oy = drag.oy + (e.clientY - drag.y);
      place();
    });
    ["pointerup", "pointercancel"].forEach(function (t) { crop.addEventListener(t, function () { drag = null; }); });
    crop.addEventListener("keydown", function (e) {
      if (!crop.__img) return;
      var step = 10, moved = true;
      if (e.key === "ArrowLeft") st.ox += step; else if (e.key === "ArrowRight") st.ox -= step;
      else if (e.key === "ArrowUp") st.oy += step; else if (e.key === "ArrowDown") st.oy -= step;
      else moved = false;
      if (moved) { e.preventDefault(); place(); }
    });
    crop.tabIndex = 0;
    crop.setAttribute("aria-label", "The picture in its frame: drag or use the arrow keys to place it");
    crop.__img = img;
    use.addEventListener("click", function () {
      var name = box.__ccName || $("[name=was_name]", box).value;
      if (!name) { toast("Create the card first, then choose its picture.", { error: true }); return; }
      var l = Math.max(0, Math.round(-st.ox / st.s)), t = Math.max(0, Math.round(-st.oy / st.s));
      var w = Math.min(st.w - l, Math.round(st.W / st.s)), h = Math.min(st.h - t, Math.round(st.H / st.s));
      var fd = new FormData();
      fd.append("csrf", Menu.csrf());
      fd.append("name", name);
      fd.append("face", face);
      fd.append("crop", l + "," + t + "," + w + "," + h);
      fd.append("picture", file, file.name || "picture");
      use.disabled = true; drop.disabled = true;
      use.setAttribute("aria-busy", "true");
      fetch("/lists/custom/picture", { method: "POST", credentials: "same-origin", headers: { Accept: "application/json" }, body: fd })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          use.disabled = false; drop.disabled = false; use.removeAttribute("aria-busy");
          if (!d.ok) { toast(d.message || "Archidekt refused the picture.", { error: true }); return; }
          resetCrop(crop, true);
          said(d, "Picture saved on Archidekt and read back.");
          var tr = $$(".lp .ui-table .tr[data-row]").filter(function (x) { return nameOf(x) === name; })[0];
          if (tr) { var patch = {}; patch[face + "ImageUrl"] = "set"; pinChanged(tr, patch); }
          btn.focus();
        }, function () {
          use.disabled = false; drop.disabled = false; use.removeAttribute("aria-busy");
          toast(NO_CONNECTION, { error: true });
        });
    });
    img.src = url;
  }
})();
