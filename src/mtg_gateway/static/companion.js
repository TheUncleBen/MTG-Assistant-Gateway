/* Deck editor for the companion pages. Builds a change list in the browser and hands it to
   /api/v1/proposals with apply:true: the member's own save is their approval, so the gateway
   applies it at once (snapshot first); a big removal comes back as needs_confirm and is asked
   about here before it is applied. No framework, no build step.
   The page passes its data in <script id="editor-config" type="application/json">.

   One proposal holds count changes (add / remove / set_quantity), category changes
   (set_category / set_commander) and printing changes (set_finish / set_printing). The gateway
   refuses two kinds of change on the same card in one proposal, so when a card has both, the
   count change goes first and the row says the rest waits for the next proposal. */
(function () {
  "use strict";
  var cfg = JSON.parse(document.getElementById("editor-config").textContent);
  var root = document.getElementById("editor");
  if (!root) return;
  var MAX = cfg.maxChanges || 40;

  // state: key -> row of an existing card. The key is the lower-case name for a row of the deck
  // proper and "side:" + name for a maybeboard / sideboard row: the two zones count separately,
  // so one card may have a row in each. Side rows take count and category changes (zone "side");
  // finish and printing changes are for the deck proper.
  var rows = {};
  function zoneOf(c) { return c.in_deck === false || c.zone === "side" ? "side" : "main"; }
  function keyFor(name, zone) { return (zone === "side" ? "side:" : "") + name.toLowerCase(); }
  cfg.cards.forEach(function (c) {
    var zone = zoneOf(c);
    var key = keyFor(c.name, zone);
    if (!rows[key]) {
      rows[key] = {
        name: c.name, zone: zone, before: 0, after: 0, categories: c.categories || [], autoCategory: c.auto_category || "", setCategory: null,
        finish: (c.modifier || "Normal").toLowerCase(), setFinish: null, printing: null,
        set: c.set_code || "", number: c.collector_number || "", image: c.image || null, mana: c.mana_cost || "",
        price: c.price,
        type: c.type_line || "", text: c.oracle_text || "", pt: c.pt || "", loyalty: c.loyalty || "", faces: c.faces || []
      };
    }
    rows[key].before += c.quantity;
    rows[key].after += c.quantity;
  });
  var added = {}; // key -> {name, zone, quantity, category, set_code, collector_number, foil}
  /* the row's thumbnail (or its placeholder) opens the shared viewer with the whole card */
  function thumb(r, name) {
    var node = r.image ? el("img", { class: "thumb", src: r.image, alt: "", loading: "lazy" }) : el("span", { class: "thumb ph" });
    if (!window.MtgCardView) return node;
    var btn = el("button", { type: "button", class: "thumbbtn", "aria-label": "Show " + name, onclick: function () {
      var finish = r.setFinish || r.finish;
      window.MtgCardView.open({ name: name, img: r.image || "", set: r.set ? r.set.toUpperCase() + " " + r.number : "",
        type: r.type, mana: r.mana, text: r.text, pt: r.pt, loyalty: r.loyalty, finish: finish, faces: r.faces }, []);
    } }, [node]);
    return btn;
  }
  var rowEls = {}; // lower name -> the <li> of an existing card
  var history = []; // snapshots for Undo

  function snapshot() {
    var s = {};
    Object.keys(rows).forEach(function (k) {
      var r = rows[k];
      s[k] = { after: r.after, setCategory: r.setCategory, setFinish: r.setFinish, printing: r.printing };
    });
    return { rows: s, added: JSON.parse(JSON.stringify(added)) };
  }
  function mutate(fn) {
    history.push(snapshot());
    if (history.length > 100) history.shift();
    fn();
    render();
  }
  function undo() {
    var s = history.pop();
    if (!s) return;
    Object.keys(s.rows).forEach(function (k) {
      var r = rows[k], v = s.rows[k];
      r.after = v.after; r.setCategory = v.setCategory; r.setFinish = v.setFinish; r.printing = v.printing;
    });
    added = s.added;
    render();
  }

  var known = {}; // lower name -> card summary from the suggestion list (picture, mana, type)
  function addCard(name, qty, category, printing, finish, zone) {
    zone = zone === "side" ? "side" : "main";
    var key = keyFor(name, zone);
    if (rows[key]) {
      rows[key].after = Math.min(99, rows[key].after + qty);
    } else if (added[key]) {
      added[key].quantity = Math.min(99, added[key].quantity + qty);
    } else {
      added[key] = { name: name, zone: zone, quantity: qty, category: category || null };
      var k = known[name.toLowerCase()];
      if (k) { added[key].image = k.image_small || null; added[key].mana = k.mana_cost || ""; added[key].type = k.type_line || ""; }
      // a pinned printing or a foil goes to the deck proper only (the gateway refuses them for zone side)
      if (zone === "main" && printing && printing.set_code && printing.collector_number) {
        added[key].set_code = printing.set_code;
        added[key].collector_number = printing.collector_number;
      }
      if (zone === "main" && ((printing && printing.foil === true) || finish === "foil")) added[key].foil = true;
    }
  }
  (cfg.prefill || []).forEach(function (ch) {
    if (ch.action === "add") addCard(ch.card_name, ch.quantity || 1, ch.category || null, ch);
  });

  // what each existing row contributes, and what has to wait (count wins over category wins over printing)
  function shownCategory(r) { return r.categories[0] || r.autoCategory || ""; }
  function rowChange(r) {
    var out = { change: null, waiting: [] };
    if (r.after !== r.before) {
      out.change = r.after === 0 ? { action: "remove", card_name: r.name } : { action: "set_quantity", card_name: r.name, quantity: r.after };
      if (r.zone === "side") out.change.zone = "side";
      if (r.setCategory && r.setCategory !== shownCategory(r)) out.waiting.push("category");
      if (r.printing || (r.setFinish && r.setFinish !== r.finish)) out.waiting.push("printing");
      return out;
    }
    if (r.setCategory && r.setCategory !== shownCategory(r)) {
      out.change = r.setCategory === "Commander" && r.zone !== "side" ? { action: "set_commander", card_name: r.name } : { action: "set_category", card_name: r.name, category: r.setCategory };
      if (r.zone === "side") out.change.zone = "side";
      if (r.printing || (r.setFinish && r.setFinish !== r.finish)) out.waiting.push("printing");
      return out;
    }
    if (r.printing) {
      out.change = { action: "set_printing", card_name: r.name, set_code: r.printing.set_code, collector_number: r.printing.collector_number };
      if (r.setFinish && r.setFinish !== r.finish) out.change.finish = r.setFinish;
    } else if (r.setFinish && r.setFinish !== r.finish) {
      out.change = { action: "set_finish", card_name: r.name, finish: r.setFinish };
    }
    return out;
  }

  function changes() {
    var out = [];
    Object.keys(rows).forEach(function (k) {
      var ch = rowChange(rows[k]).change;
      if (ch) out.push(ch);
    });
    Object.keys(added).forEach(function (k) {
      var a = added[k];
      var ch = { action: "add", card_name: a.name, quantity: a.quantity };
      if (a.zone === "side") ch.zone = "side";
      if (a.category) ch.category = a.category;
      if (a.set_code) { ch.set_code = a.set_code; ch.collector_number = a.collector_number; }
      if (a.foil) ch.foil = true;
      out.push(ch);
    });
    return out;
  }

  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k];
      else if (k.indexOf("on") === 0) e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) e.appendChild(c); });
    return e;
  }
  function svg(name) {
    var tpl = document.getElementById("icon-" + name);
    if (tpl) return tpl.content.firstElementChild.cloneNode(true);
    return el("span", { text: { minus: "−", plus: "+", more: "⋯", x: "×" }[name] || "" });
  }

  function qtyControls(get, set) {
    var input = el("input", { type: "number", min: "0", max: "99", value: String(get()), "aria-label": "quantity",
      onchange: function () { var v = parseInt(input.value, 10); if (isNaN(v)) v = get(); set(Math.max(0, Math.min(99, v))); } });
    return el("span", { class: "qty" }, [
      el("button", { type: "button", class: "mini", "aria-label": "one fewer", onclick: function () { set(Math.max(0, get() - 1)); } }, [svg("minus")]),
      input,
      el("button", { type: "button", class: "mini", "aria-label": "one more", onclick: function () { set(Math.min(99, get() + 1)); } }, [svg("plus")])
    ]);
  }

  function categorySelect(current, onchange, allowAuto, side) {
    var sel = el("select", { "aria-label": "category" });
    var cats = cfg.categories.slice();
    if (side) cats = cats.filter(function (c) { return c !== "Commander"; });  // a commander is a deck-proper row
    else if (cats.indexOf("Commander") < 0) cats.unshift("Commander");
    if (current && cats.indexOf(current) < 0) cats.unshift(current);
    if (allowAuto) sel.appendChild(el("option", { value: "", text: "Auto" }));
    cats.forEach(function (c) {
      var o = el("option", { value: c, text: c });
      if (c === current) o.selected = true;
      sel.appendChild(o);
    });
    sel.appendChild(el("option", { value: "\u0000new", text: "New category…" }));
    sel.addEventListener("change", function () {
      if (sel.value === "\u0000new") {
        var name = (window.prompt("New category name") || "").trim().slice(0, 60);
        if (!name) { sel.value = current || ""; if (window.MtgSelect) window.MtgSelect.refresh(sel); return; }
        if (cfg.categories.indexOf(name) < 0) cfg.categories.push(name);
        onchange(name);
        return;
      }
      onchange(sel.value);
    });
    return el("span", { class: "sel" }, [sel]);
  }

  function finishSelect(current, onchange) {
    var sel = el("select", { "aria-label": "finish", onchange: function () { onchange(sel.value); } });
    ["normal", "foil", "etched"].forEach(function (f) {
      var o = el("option", { value: f, text: f.charAt(0).toUpperCase() + f.slice(1) });
      if (f === current) o.selected = true;
      sel.appendChild(o);
    });
    return el("span", { class: "sel" }, [sel]);
  }

  function describe(ch) {
    var side = ch.zone === "side" ? " · " + (cfg.sideCategory || "maybeboard").toLowerCase() : "";
    switch (ch.action) {
      case "add": return "+" + ch.quantity + (ch.set_code ? " (" + ch.set_code.toUpperCase() + " " + ch.collector_number + ")" : "") + (ch.foil ? " foil" : "") + side;
      case "remove": return "remove" + side;
      case "set_quantity": return "→ " + ch.quantity + side;
      case "set_commander": return "commander";
      case "set_category": return "→ " + ch.category;
      case "set_finish": return "→ " + ch.finish;
      case "set_printing": return "→ " + ch.set_code.toUpperCase() + " " + ch.collector_number + (ch.finish ? " " + ch.finish : "");
    }
    return "";
  }

  function render() {
    var list = changes();
    var pending = root.querySelector(".pending");
    pending.textContent = "";
    if (!list.length) {
      pending.appendChild(el("p", { class: "muted", text: "No changes yet. Adjust a quantity, pick a category, change a finish or printing, or add a card." }));
    } else {
      var ul = el("ul", { class: "plain changes" });
      list.forEach(function (ch) {
        var cls = ch.action === "add" ? "add" : ch.action === "remove" ? "del" : "chg";
        ul.appendChild(el("li", { class: cls }, [
          el("span", { class: "act", text: ch.action.replace(/_/g, " ") }),
          el("span", { class: "name", text: ch.card_name }),
          el("span", { class: "qty", text: describe(ch) })
        ]));
      });
      pending.appendChild(ul);
    }
    var n = list.length;
    root.querySelector(".pendingbox .n").textContent = String(n);
    root.querySelector(".count").textContent = n ? n + " change" + (n === 1 ? "" : "s") + " pending" : "No changes yet";
    root.querySelector("button.review .label").textContent = n ? "Save " + n + " change" + (n === 1 ? "" : "s") : "Save changes";
    root.querySelector("button.review").disabled = !n || n > MAX;
    root.querySelector("button.undo").disabled = !history.length;
    root.querySelector(".limit").textContent = n > MAX ? "At most " + MAX + " changes can be saved in one go; save these first." : "";
    // added cards block
    var addedBox = root.querySelector(".added");
    addedBox.textContent = "";
    Object.keys(added).forEach(function (k) {
      var a = added[k];
      var nameEl = el("span", { class: "name", text: a.name });
      if (a.mana && window.MtgMana) nameEl.appendChild(window.MtgMana.mana(a.mana));
      addedBox.appendChild(el("li", { class: "erow new" }, [
        a.image ? el("img", { class: "thumb", src: a.image, alt: "", loading: "lazy" }) : el("span", { class: "thumb ph" }, [svg("plus")]),
        el("span", { class: "main" }, [
          nameEl,
          el("span", { class: "meta", text: [a.type || "", a.set_code ? a.set_code.toUpperCase() + " " + a.collector_number : "", a.foil ? "foil" : "", a.zone === "side" ? "new " + (cfg.sideCategory || "maybeboard").toLowerCase() + " card" : "new card"].filter(Boolean).join(" · ") })
        ]),
        qtyControls(function () { return a.quantity; }, function (v) { mutate(function () { if (v === 0) delete added[k]; else a.quantity = v; }); }),
        a.zone === "side" ? el("span", { class: "s muted small", text: cfg.sideCategory || "Maybeboard" }) : categorySelect(a.category || "", function (v) { mutate(function () { a.category = v || null; }); }, true),
        el("button", { type: "button", class: "mini remove", "aria-label": "remove " + a.name, onclick: function () { mutate(function () { delete added[k]; }); } }, [svg("x")])
      ]));
    });
    // existing rows: update in place
    Object.keys(rows).forEach(function (k) {
      var r = rows[k];
      var li = rowEls[k];
      if (!li) return;
      var input = li.querySelector(".qty input");
      if (input && document.activeElement !== input) input.value = String(r.after);
      var rc = rowChange(r);
      li.classList.toggle("changed", !!rc.change);
      li.classList.toggle("removed", r.after === 0);
      var note = li.querySelector(".note");
      var parts = [];
      if (r.printing) parts.push("printing → " + r.printing.set_code.toUpperCase() + " " + r.printing.collector_number);
      if (r.setFinish && r.setFinish !== r.finish) parts.push("finish → " + r.setFinish);
      if (rc.waiting.length) parts.push("the " + rc.waiting.join(" and ") + " change waits for the next save");
      note.textContent = parts.join(" · ");
    });
  }

  // printing picker: one card's printings from Scryfall through the gateway
  var picker = root.querySelector(".picker");
  picker.setAttribute("role", "dialog");
  picker.setAttribute("aria-modal", "true");
  picker.setAttribute("aria-label", "Printings");
  picker.addEventListener("click", function (e) { if (e.target === picker) closePicker(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !picker.hidden) { e.preventDefault(); closePicker(); } });
  var pickerFocus = null;
  function openPicker(r) {
    pickerFocus = document.activeElement;
    picker.hidden = false;
    picker.textContent = "";
    var closeBtn = el("button", { type: "button", class: "close icon-only", "aria-label": "Close", onclick: closePicker }, [el("span", { class: "x", text: "\u00d7" })]);
    var box = el("div", { class: "pickbox" }, [
      el("div", { class: "head" }, [
        el("h2", { text: "Printings of " + r.name }),
        closeBtn
      ]),
      el("p", { class: "muted small status", text: "Looking up printings…" }),
      el("div", { class: "prints" })
    ]);
    picker.appendChild(box);
    closeBtn.focus();
    var status = box.querySelector(".status");
    fetch("/scan/api/resolve", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": cfg.csrf },
      body: JSON.stringify({ cards: [{ name: r.name, quantity: 1 }] })
    })
      .then(function (res) { return res.json(); })
      .then(function (d) {
        var card = d && d.cards && d.cards[0] && d.cards[0].card;
        if (!card || !card.oracle_id) throw new Error(d && d.message ? d.message : "card not found");
        return fetch("/scan/api/prints?oracle_id=" + encodeURIComponent(card.oracle_id), { credentials: "same-origin" }).then(function (res) { return res.json(); });
      })
      .then(function (d) {
        var list = (d && d.cards) || [];
        if (!list.length) throw new Error("no printings found");
        status.textContent = list.length + " printing" + (list.length === 1 ? "" : "s") + (d.has_more ? " (newest shown)" : "") + ". Tap one to use it.";
        var grid = box.querySelector(".prints");
        list.forEach(function (p) {
          var current = p.set && p.set.toLowerCase() === r.set.toLowerCase() && String(p.collector_number) === String(r.number);
          var b = el("button", { type: "button", class: "print" + (current ? " current" : ""), title: (p.set_name || "") + " " + (p.collector_number || ""),
            onclick: function () {
              mutate(function () { r.printing = current ? null : { set_code: p.set, collector_number: String(p.collector_number) }; });
              closePicker();
            } }, [
            p.image_small ? el("img", { src: p.image_small, alt: "", loading: "lazy" }) : el("span", { class: "ph", text: p.name }),
            el("span", { class: "cap", text: (p.set || "").toUpperCase() + " " + (p.collector_number || "") + (current ? " · current" : "") })
          ]);
          grid.appendChild(b);
        });
      })
      .catch(function (e) { status.textContent = "Printings could not be loaded: " + (e && e.message ? e.message : "network error"); status.className = "notice error"; });
  }
  function closePicker() {
    picker.hidden = true;
    picker.textContent = "";
    if (pickerFocus && pickerFocus.focus) pickerFocus.focus();
  }

  // existing cards, by category
  var existing = root.querySelector(".existing");
  cfg.groups.forEach(function (g) {
    var det = el("details", { class: "panel cat", open: "" }, [
      el("summary", {}, [el("span", { class: "cname", text: g.name }), el("b", { text: String(g.total) })])
    ]);
    var ul = el("ul", { class: "erows" });
    g.cards.forEach(function (c) {
      var zone = zoneOf(c);
      var key = keyFor(c.name, zone);
      var r = rows[key];
      if (zone === "side") {
        if (rowEls[key]) return;  // the same side card listed twice: one row
        var sideLi = el("li", { class: "erow side", "data-row": key }, [
          thumb(r, c.name),
          el("span", { class: "main" }, [
            el("span", { class: "name", text: c.name }),
            el("span", { class: "meta", text: (r.set ? r.set.toUpperCase() + " " + r.number : "") + (r.finish !== "normal" ? " · " + r.finish : "") + " · not in the deck's count" }),
            el("span", { class: "note muted small" })
          ]),
          qtyControls(function () { return r.after; }, function (v) { mutate(function () { r.after = v; }); }),
          categorySelect(r.setCategory || shownCategory(r), function (v) { mutate(function () { r.setCategory = v; }); }, false, true),
          el("button", { type: "button", class: "mini remove", "aria-label": "remove " + c.name, onclick: function () { mutate(function () { r.after = 0; }); } }, [svg("x")])
        ]);
        rowEls[key] = sideLi;
        ul.appendChild(sideLi);
        return;
      }
      if (rowEls[key]) {  // the same card in two categories: one state, the first row carries the controls
        ul.appendChild(el("li", { class: "erow side" }, [
          el("span", { class: "thumb ph" }),
          el("span", { class: "main" }, [el("span", { class: "name", text: c.quantity + " " + c.name }), el("span", { class: "meta", text: "also under " + g.name + "; see the row above" })])
        ]));
        return;
      }
      var more = el("details", { class: "dd rowmenu" }, [
        el("summary", { class: "mini", "aria-label": "more for " + c.name }, [svg("more")]),
        el("div", { class: "menu" }, [
          el("label", { class: "field" }, [el("span", { text: "Finish" }), finishSelect(r.setFinish || r.finish, function (v) { mutate(function () { r.setFinish = v === r.finish ? null : v; }); })]),
          el("button", { type: "button", onclick: function () { more.removeAttribute("open"); openPicker(r); } }, [svg("swap"), el("span", { text: " Change printing" })]),
          el("button", { type: "button", onclick: function () { more.removeAttribute("open"); mutate(function () { r.after = 0; }); } }, [svg("x"), el("span", { text: " Remove from deck" })])
        ])
      ]);
      var nameEl = el("span", { class: "name", text: c.name });
      if (r.mana && window.MtgMana) nameEl.appendChild(window.MtgMana.mana(r.mana));
      var li = el("li", { class: "erow" + (c.in_deck ? "" : " side"), "data-row": key }, [
        thumb(r, c.name),
        el("span", { class: "main" }, [
          nameEl,
          el("span", { class: "meta", text: (r.set ? r.set.toUpperCase() + " " + r.number : "") + (r.finish !== "normal" ? " · " + r.finish : "") + (r.price != null ? " · $" + Number(r.price).toFixed(2) : "") }),
          el("span", { class: "note muted small" })
        ]),
        qtyControls(function () { return r.after; }, function (v) { mutate(function () { r.after = v; }); }),
        cfg.canCategorise ? categorySelect(r.setCategory || shownCategory(r), function (v) { mutate(function () { r.setCategory = v; }); }, false) : el("span", { class: "s", text: shownCategory(r) }),
        more
      ]);
      rowEls[key] = li;
      ul.appendChild(li);
    });
    det.appendChild(ul);
    existing.appendChild(det);
  });

  // add a card: one search bar (static/suggest.js lists the names; Enter picks and submits here).
  // Chips above it say where the card goes; "3 sol ring" adds three; the printings of the
  // highlighted name show beside the list on wide screens, and a click on one adds that printing.
  var addForm = root.querySelector("form.addcard");
  var input = root.querySelector("input[name=card]");
  var catIn = root.querySelector("input[name=addcat]"), zoneIn = root.querySelector("input[name=addzone]");
  var foilIn = root.querySelector("input[name=foil]");
  var addStatus = root.querySelector(".addstatus");
  input.addEventListener("suggest:pick", function (ev) {
    if (ev.detail && ev.detail.card) known[ev.detail.name.toLowerCase()] = ev.detail.card;
  });
  Array.prototype.forEach.call(root.querySelectorAll(".targets .tchip"), function (chip) {
    chip.addEventListener("click", function () {
      Array.prototype.forEach.call(root.querySelectorAll(".targets .tchip"), function (c) {
        c.classList.toggle("on", c === chip);
        c.setAttribute("aria-pressed", c === chip ? "true" : "false");
      });
      catIn.value = chip.getAttribute("data-cat") || "";
      zoneIn.value = chip.getAttribute("data-zone") || "main";
      input.focus();
    });
  });
  function splitQty(raw) {
    var m = /^\s*(\d{1,2})\s*[xX]?\s+(.+)$/.exec(raw);
    return m ? { qty: Math.max(1, Math.min(99, parseInt(m[1], 10))), name: m[2].trim() } : { qty: 1, name: raw.trim() };
  }
  var lastSetKey = "mtg-lastset-" + cfg.deckId;
  function lastSet() { try { return localStorage.getItem(lastSetKey) || ""; } catch (e) { return ""; } }
  function rememberSet(code) { try { localStorage.setItem(lastSetKey, code); } catch (e) { /* private window */ } }
  function addTyped(raw, printing, pickedName) {
    var parts = splitQty(raw);
    if (pickedName) parts.name = pickedName;  // a printing was clicked: that card, with the typed count
    if (!parts.name) return;
    var zone = zoneIn.value === "side" ? "side" : "main";
    var finish = foilIn && foilIn.checked ? "foil" : null;
    mutate(function () { addCard(parts.name, parts.qty, catIn.value || null, printing || null, finish, zone); });
    var where = zone === "side" ? cfg.sideCategory || "Maybeboard" : (catIn.value || "the deck");
    var print = printing && printing.set_code ? " (" + printing.set_code.toUpperCase() + " " + printing.collector_number + ")" : "";
    if (addStatus) addStatus.textContent = "Added " + parts.qty + " × " + parts.name + print + " to " + where + ". Type the next card, or save.";
    input.value = "";
    hidePrints();
    input.focus();
  }
  addForm.addEventListener("submit", function (ev) {
    ev.preventDefault();
    addTyped(input.value, pendingPrint());
  });

  // printings beside the suggestion list (wide screens): the highlighted card's printings as
  // pictures; the set used last on this deck is pre-selected and goes with Enter; a click adds
  var printsBox = root.querySelector(".addprints");
  var printsFor = "", printsCache = {}, printsSeq = 0, chosenPrint = null;
  function wideEnough() { return window.matchMedia && window.matchMedia("(min-width: 900px)").matches; }
  function hidePrints() { if (printsBox) { printsBox.hidden = true; printsBox.textContent = ""; } printsFor = ""; chosenPrint = null; }
  function pendingPrint() { return chosenPrint; }
  function showPrints(name, list, hasMore) {
    printsBox.textContent = "";
    chosenPrint = null;
    var remembered = lastSet().toLowerCase();
    var head = el("div", { class: "head" }, [
      el("b", { text: name }),
      el("span", { class: "muted small", text: list.length + " printing" + (list.length === 1 ? "" : "s") + (hasMore ? ", newest shown" : "") })
    ]);
    printsBox.appendChild(head);
    var grid = el("div", { class: "pgrid" });
    list.slice(0, 12).forEach(function (p) {
      var printing = { set_code: p.set, collector_number: String(p.collector_number) };
      var pre = remembered && p.set && p.set.toLowerCase() === remembered;
      if (pre && !chosenPrint) chosenPrint = printing;
      var b = el("button", { type: "button", class: "print" + (pre ? " current" : ""), tabindex: "-1",
        title: (p.set_name || "") + " " + (p.collector_number || "") + ": click to add this printing",
        onclick: function () { rememberSet(p.set || ""); addTyped(input.value, printing, name); } }, [
        p.image_small ? el("img", { src: p.image_small, alt: "", loading: "lazy" }) : el("span", { class: "ph", text: p.name }),
        el("span", { class: "cap", text: (p.set || "").toUpperCase() + " " + (p.collector_number || "") })
      ]);
      grid.appendChild(b);
    });
    printsBox.appendChild(grid);
    printsBox.appendChild(el("p", { class: "muted small hint", text: chosenPrint ? "Enter adds the " + chosenPrint.set_code.toUpperCase() + " printing (used last here); click another to add that one." : "Enter adds the newest printing; click a picture to add that one." }));
    printsBox.hidden = false;
  }
  input.addEventListener("suggest:active", function (ev) {
    if (!printsBox || !wideEnough()) return;
    var d = ev.detail || {};
    if (!d.name || !d.card || !d.card.oracle_id) { if (d.name !== printsFor) hidePrints(); return; }
    if (d.name === printsFor) return;
    printsFor = d.name;
    var mine = ++printsSeq;
    var cached = printsCache[d.card.oracle_id];
    if (cached) { showPrints(d.name, cached.cards, cached.has_more); return; }
    printsBox.hidden = false;
    printsBox.textContent = "";
    printsBox.appendChild(el("p", { class: "muted small", text: "Printings of " + d.name + "…" }));
    fetch("/scan/api/prints?oracle_id=" + encodeURIComponent(d.card.oracle_id), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (res) {
        if (!res || !res.cards || !res.cards.length) { if (mine === printsSeq) hidePrints(); return; }
        printsCache[d.card.oracle_id] = res;
        if (mine === printsSeq && printsFor === d.name) showPrints(d.name, res.cards, res.has_more);
      })
      .catch(function () { if (mine === printsSeq) hidePrints(); });
  });
  input.addEventListener("suggest:close", function () { setTimeout(function () { if (document.activeElement !== input || !input.value) hidePrints(); }, 150); });
  printsBox.addEventListener("mousedown", function (e) { e.preventDefault(); });  // keep the focus in the box

  // paste a list: "2 Lightning Bolt" per line, names checked through the gateway's card lookup,
  // then added like typed cards (to the zone picked in the add form)
  var pasteForm = root.querySelector("form.pastelist");
  if (pasteForm) pasteForm.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var area = pasteForm.querySelector("textarea");
    var status = pasteForm.querySelector(".pastestatus");
    var items = [];
    area.value.split(/\r?\n/).forEach(function (line) {
      line = line.trim();
      if (!line || /^(deck|sideboard|maybeboard|commander|companion)\s*:?$/i.test(line) || line.charAt(0) === "#") return;
      var m = /^(\d{1,2})\s*[xX]?\s+(.+)$/.exec(line) || /^(.+?)\s+[xX]?(\d{1,2})$/.exec(line);
      var qty = 1, name = line;
      if (m) { if (/^\d/.test(m[1])) { qty = parseInt(m[1], 10); name = m[2]; } else { name = m[1]; qty = parseInt(m[2], 10); } }
      name = name.replace(/\s*\([A-Za-z0-9]{2,6}\)\s*[A-Za-z0-9★†-]*\s*$/, "").replace(/\s*\*F\*\s*$/i, "").trim();
      if (name && items.length < 200) items.push({ name: name, quantity: Math.max(1, Math.min(99, qty)) });
    });
    if (!items.length) { status.textContent = "Nothing to add: paste one card per line."; return; }
    var btn = pasteForm.querySelector("button");
    btn.disabled = true;
    status.textContent = "Checking " + items.length + " name" + (items.length === 1 ? "" : "s") + "…";
    fetch("/scan/api/resolve", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": cfg.csrf },
      body: JSON.stringify({ cards: items })
    })
      .then(function (res) { return res.json(); })
      .then(function (d) {
        var zone = zoneIn.value === "side" ? "side" : "main";
        var missed = [];
        var found = 0;
        mutate(function () {
          (d.cards || []).forEach(function (it, i) {
            if (it.card && it.card.name) { addCard(it.card.name, it.quantity || items[i].quantity || 1, null, null, null, zone); found += it.quantity || 1; }
            else missed.push((it.input && it.input.name) || items[i].name);
          });
        });
        area.value = missed.join("\n");
        status.textContent = found + " card" + (found === 1 ? "" : "s") + " added to the pending changes" + (missed.length ? "; " + missed.length + " name" + (missed.length === 1 ? " was" : "s were") + " not recognised and stay in the box." : ".");
        btn.disabled = false;
      })
      .catch(function () { status.textContent = "The names could not be checked (network error); nothing was added."; btn.disabled = false; });
  });
  root.querySelector("button.undo").addEventListener("click", undo);

  // menus close on outside click / Escape
  document.addEventListener("click", function (ev) {
    Array.prototype.forEach.call(root.querySelectorAll("details.dd[open]"), function (d) { if (!d.contains(ev.target)) d.removeAttribute("open"); });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      closePicker();
      Array.prototype.forEach.call(root.querySelectorAll("details.dd[open]"), function (d) { d.removeAttribute("open"); });
    }
  });
  window.addEventListener("beforeunload", function (ev) {
    if (changes().length && !root.dataset.leaving) { ev.preventDefault(); ev.returnValue = ""; }
  });
  // Back from the page that a discard went to may restore this one from memory: guard it again.
  window.addEventListener("pageshow", function (ev) { if (ev.persisted) delete root.dataset.leaving; });

  // Leaving with unsaved changes through the page itself (Close editor, a tab, a menu link, a
  // form such as "Load" a scan) asks here, in the save bar, instead of the browser's generic
  // "Leave site?" box: Discard goes on without that box, Keep editing stays, Save saves first.
  // The browser's box is kept for what the page cannot see (closing the tab, reload, Back).
  function askToLeave(go) {
    var old = root.querySelector(".confirmbar");
    if (old) old.remove();
    var n = changes().length;
    var bar = el("div", { class: "confirmbar leavebar notice warn", role: "alertdialog", "aria-labelledby": "leave-q" });
    bar.appendChild(el("span", { id: "leave-q", text: "You have " + n + " unsaved change" + (n === 1 ? "" : "s") + ". Discard " + (n === 1 ? "it" : "them") + "?" }));
    var discard = el("button", { type: "button", class: "discard", text: "Discard changes" });
    var keep = el("button", { type: "button", class: "btn-primary keep", text: "Keep editing" });
    var saveNow = el("button", { type: "button", class: "savefirst", text: "Save changes" });
    bar.appendChild(keep); bar.appendChild(discard); bar.appendChild(saveNow);
    root.querySelector(".editbar").appendChild(bar);
    function close() { bar.remove(); document.removeEventListener("keydown", onKey, true); }
    function onKey(e) { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } }
    document.addEventListener("keydown", onKey, true);
    keep.addEventListener("click", close);
    discard.addEventListener("click", function () { close(); root.dataset.leaving = "1"; go(); });
    saveNow.addEventListener("click", function () { close(); save(false); });
    keep.focus();
  }
  function pending() { return changes().length && !root.dataset.leaving; }
  document.addEventListener("click", function (ev) {
    if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    var a = ev.target instanceof Element ? ev.target.closest("a[href]") : null;
    if (!a || a.hasAttribute("download") || (a.target && a.target !== "_self") || !pending()) return;
    var url = new URL(a.href, window.location.href);
    if (url.origin !== window.location.origin) return;
    if (url.hash && url.pathname === window.location.pathname && url.search === window.location.search) return;
    ev.preventDefault();
    askToLeave(function () { window.location.href = url.href; });
  });
  // capture phase, ahead of feedback.js's busy state; the editor's own forms are handled in place
  window.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!(form instanceof HTMLFormElement) || form.matches("form.addcard, form.pastelist")) return;
    if ((form.getAttribute("target") || "_self") !== "_self" || !pending()) return;
    ev.preventDefault();
    ev.stopPropagation();
    var by = ev.submitter || null;
    askToLeave(function () { if (form.requestSubmit) form.requestSubmit(by); else form.submit(); });
  }, true);

  // save: one proposal, applied at once (apply:true); a big removal asks first. The tick box says
  // whether Archidekt also gets a backup copy (the gateway's snapshot is kept either way).
  var backupBox = root.querySelector("input[name=archidekt_backup]");
  function save(confirmed) {
    var btn = root.querySelector("button.review");
    btn.disabled = true;
    var status = root.querySelector(".status");
    status.textContent = confirmed ? "Saving to Archidekt…" : "Saving to Archidekt…";
    status.className = "status";
    var old = root.querySelector(".confirmbar");
    if (old) old.remove();
    fetch("/api/v1/proposals", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": cfg.csrf },
      body: JSON.stringify({ kind: "edit", deck_id: cfg.deckId, changes: changes(), apply: true, confirmed: confirmed === true,
        archidekt_backup: !(backupBox && !backupBox.checked) })
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        var d = res.d || {};
        if (res.ok && d.applied) {
          root.dataset.leaving = "1";
          window.location.href = "/decks/" + encodeURIComponent(cfg.deckId) + "?ok=saved";
        } else if (res.ok && d.needs_confirm) {
          // the proposal exists but nothing was sent: ask, then apply that same proposal
          status.textContent = "";
          var bar = el("div", { class: "confirmbar notice warn", role: "alertdialog" });
          bar.appendChild(el("span", { text: d.why + " Save anyway?" }));
          var yes = el("button", { type: "button", class: "btn-primary", text: "Save anyway" });
          var no = el("button", { type: "button", text: "Keep editing" });
          bar.appendChild(yes); bar.appendChild(no);
          root.querySelector(".editbar").appendChild(bar);
          yes.addEventListener("click", function () {
            yes.disabled = true;
            fetch("/api/v1/proposals/" + encodeURIComponent(d.proposal_id) + "/apply", {
              method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": cfg.csrf }
            }).then(function (r) { return r.json(); }).then(function (a) {
              if (a.ok) { root.dataset.leaving = "1"; window.location.href = "/decks/" + encodeURIComponent(cfg.deckId) + "?ok=saved"; return; }
              bar.remove(); status.textContent = a.message || "Archidekt refused the change; nothing was saved."; status.className = "status notice error"; btn.disabled = false;
            }).catch(function () { bar.remove(); status.textContent = "Network error; nothing was changed."; status.className = "status notice error"; btn.disabled = false; });
          });
          no.addEventListener("click", function () {
            bar.remove(); btn.disabled = false;
            // the proposal made for the check is not wanted: reject it so Proposals stays clean
            fetch("/api/v1/proposals/" + encodeURIComponent(d.proposal_id) + "/reject", {
              method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": cfg.csrf }
            }).catch(function () { /* a leftover pending proposal is harmless */ });
          });
          yes.focus();
        } else if (res.ok && d.proposal_id) {
          // writes are off on this gateway: the proposal is kept for later
          root.dataset.leaving = "1";
          window.location.href = "/proposals/" + encodeURIComponent(d.proposal_id);
        } else {
          status.textContent = d.message || "The changes could not be saved.";
          status.className = "status notice error";
          btn.disabled = false;
        }
      })
      .catch(function () {
        status.textContent = "Network error; nothing was changed.";
        status.className = "status notice error";
        btn.disabled = false;
      });
  }
  root.querySelector("button.review").addEventListener("click", function () { save(false); });

  render();
})();
