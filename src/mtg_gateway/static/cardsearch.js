/* The card results page (/cards, cardsearch.py): a picture opens the shared card viewer
   (static/cardview.js) with an Add to deck action, and Add to deck opens a small dialog that
   picks one of the member's decks (the cached list behind /api/decks/mine), a quantity and a
   category (the deck's own, read once per deck), then saves through the deck page's own edit
   endpoint, POST /api/v1/decks/{id}/edit: the proposals path applied at once with its snapshot,
   a "save anyway" question when the hand-edit rule asks, the review page when writes are off.
   Nothing is written any other way. Loaded after cardview.js; does nothing without #cardgrid. */
(function () {
  "use strict";
  var grid = document.getElementById("cardgrid");
  if (!grid) return;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var csrfInput = $("input[name=csrf]");
  var CSRF = csrfInput ? csrfInput.value : "";
  var linked = grid.hasAttribute("data-linked");
  var enc = encodeURIComponent;
  var LAST_DECK = "mtg.addToDeck";
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function icon(name) {
    var paths = {
      plus: "<path d='M12 5v14M5 12h14'/>",
      external: "<path d='M14 4h6v6M20 4l-9 9'/><path d='M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5'/>"
    };
    var span = el("span");
    span.innerHTML = "<svg class='i' viewBox='0 0 24 24' aria-hidden='true'>" + (paths[name] || "") + "</svg>";
    return span.firstChild;
  }
  function api(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = CSRF;
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () { return { ok: false, status: r.status, message: "The gateway answered oddly; nothing was changed." }; });
    });
  }

  // -- toast: what was saved, with a link to the deck; errors stay until dismissed ---------------
  var toastEl = null, toastTimer = null;
  function dismissToast() { if (toastEl) toastEl.remove(); toastEl = null; clearTimeout(toastTimer); }
  function toast(text, opts) {
    opts = opts || {};
    dismissToast();
    toastEl = el("div", "toast cards-toast" + (opts.error ? " error" : ""));
    toastEl.setAttribute("role", opts.error ? "alert" : "status");
    toastEl.appendChild(el("span", "msg", text));
    (opts.actions || []).forEach(function (a) { toastEl.appendChild(a); });
    var x = el("button", "close icon-only", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", dismissToast);
    toastEl.appendChild(x);
    document.body.appendChild(toastEl);
    if (!opts.error) toastTimer = setTimeout(dismissToast, opts.ms || 9000);
  }

  // -- the card viewer: the picture button's data attributes, plus Add to deck and Scryfall ------
  function viewerActions(node) {
    var acts = [];
    if (window.MtgCardView) {
      var add = el("button", "btn-primary");
      add.type = "button";
      add.appendChild(icon("plus"));
      add.appendChild(document.createTextNode(" Add to deck"));
      if (!linked) { add.disabled = true; add.title = "Link your Archidekt account on the Account page to add cards to a deck."; }
      add.addEventListener("click", function () { window.MtgCardView.close(); openSheet(node.getAttribute("data-card")); });
      acts.push(add);
    }
    var scry = node.getAttribute("data-scry");
    if (scry) {
      var a = el("a", "btn ext");
      a.href = scry; a.target = "_blank"; a.rel = "noreferrer noopener";
      a.appendChild(icon("external"));
      a.appendChild(document.createTextNode(" Scryfall"));
      acts.push(a);
    }
    return acts;
  }
  /* The rules text (every face), power and toughness, flavour, price and legality are read when a
     card is opened, once per card for the page's lifetime (the gateway keeps them a while too),
     and drawn into the open viewer when they arrive, if it still shows that card. */
  var texts = {};      // card name -> promise of the text, or null when it could not be read
  var showing = "";    // the card the viewer shows now
  function textFor(name) {
    if (texts[name]) return texts[name];
    texts[name] = fetch("/cards/api/text?name=" + enc(name), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { return d && d.ok && d.card ? d.card : null; })
      .catch(function () { delete texts[name]; return null; });
    return texts[name];
  }
  function merged(base, text) {
    var card = {};
    Object.keys(base).forEach(function (k) { card[k] = base[k]; });
    if (text) Object.keys(text).forEach(function (k) { if (text[k] || k === "faces") card[k] = text[k]; });
    return card;
  }
  function openViewer(pic) {
    var base = window.MtgCardView.fromElement(pic);
    var name = base.name;
    showing = name;
    var known = texts[name] && texts[name]._value;
    if (known !== undefined) { window.MtgCardView.open(merged(base, known), viewerActions(pic)); return; }
    base.loading = true;
    window.MtgCardView.open(base, viewerActions(pic));
    textFor(name).then(function (text) {
      if (texts[name]) texts[name]._value = text;
      if (showing !== name) return;
      delete base.loading;
      window.MtgCardView.update(merged(base, text), viewerActions(pic));
    });
  }
  grid.addEventListener("click", function (e) {
    var pic = e.target.closest(".pic");
    if (pic && window.MtgCardView) {
      e.preventDefault();
      openViewer(pic);
      return;
    }
    var add = e.target.closest("button[data-add]");
    if (add && !add.disabled) openSheet(add.getAttribute("data-add"));
  });

  // -- Add to deck -----------------------------------------------------------------------------
  var sheet = null, lastFocus = null, deckSel, qtyIn, catSel, msg, saveBtn, title, cardName = "";
  var deckRows = null, deckLoad = null, cats = {};  // deck id -> categories, once per deck
  function ensureSheet() {
    if (sheet) return sheet;
    sheet = el("div", "addsheet");
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-labelledby", "addsheet-title");
    var box = el("form", "box");
    box.noValidate = true;
    var head = el("div", "head");
    title = el("h3"); title.id = "addsheet-title";
    head.appendChild(title);
    var x = el("button", "close icon-only");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.appendChild(el("span", "x", "×"));
    x.addEventListener("click", closeSheet);
    head.appendChild(x);
    box.appendChild(head);

    var f1 = el("div", "field");
    var l1 = el("label", null, "Deck"); l1.htmlFor = "ad-deck";
    deckSel = el("select"); deckSel.id = "ad-deck"; deckSel.name = "deck"; deckSel.required = true;
    f1.appendChild(l1); f1.appendChild(deckSel);
    box.appendChild(f1);

    var two = el("div", "two");
    var f2 = el("div", "field");
    var l2 = el("label", null, "Copies"); l2.htmlFor = "ad-qty";
    qtyIn = el("input"); qtyIn.id = "ad-qty"; qtyIn.name = "quantity"; qtyIn.type = "number";
    qtyIn.min = "1"; qtyIn.max = "99"; qtyIn.step = "1"; qtyIn.value = "1"; qtyIn.inputMode = "numeric";
    f2.appendChild(l2); f2.appendChild(qtyIn);
    var f3 = el("div", "field");
    var l3 = el("label", null, "Category"); l3.htmlFor = "ad-cat";
    catSel = el("select"); catSel.id = "ad-cat"; catSel.name = "category";
    f3.appendChild(l3); f3.appendChild(catSel);
    two.appendChild(f2); two.appendChild(f3);
    box.appendChild(two);

    msg = el("p", "msg"); msg.setAttribute("aria-live", "polite");
    box.appendChild(msg);
    var acts = el("div", "acts");
    var cancel = el("button", "", "Cancel"); cancel.type = "button";
    cancel.addEventListener("click", closeSheet);
    saveBtn = el("button", "btn-primary"); saveBtn.type = "submit";
    saveBtn.appendChild(icon("plus"));
    saveBtn.appendChild(document.createTextNode(" Add"));
    acts.appendChild(cancel); acts.appendChild(saveBtn);
    box.appendChild(acts);
    box.addEventListener("submit", function (e) { e.preventDefault(); save(); });
    sheet.appendChild(box);
    document.body.appendChild(sheet);
    sheet.addEventListener("click", function (e) { if (e.target === sheet) closeSheet(); });
    document.addEventListener("keydown", function (e) {
      if (!sheet.classList.contains("open")) return;
      if (window.MtgSelect && window.MtgSelect.isOpen()) return;   // the themed list handles its own keys
      if (e.key === "Escape") { e.preventDefault(); closeSheet(); return; }
      if (e.key === "Tab") {
        var f = sheet.querySelectorAll("button:not([disabled]),input,select,[tabindex]:not([tabindex='-1'])");
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    deckSel.addEventListener("change", function () { remember(deckSel.value); loadCats(deckSel.value); });
    return sheet;
  }
  function remember(id) { try { localStorage.setItem(LAST_DECK, id); } catch (e) { /* private window */ } }
  function remembered() { try { return localStorage.getItem(LAST_DECK) || ""; } catch (e) { return ""; } }
  function setMsg(text, isError) { msg.textContent = text || ""; msg.className = "msg" + (isError ? " err" : ""); }
  function option(value, label) { var o = el("option", null, label); o.value = value; return o; }
  function fillDecks(rows) {
    deckSel.textContent = "";
    if (!rows.length) { deckSel.appendChild(option("", "You have no decks yet")); deckSel.disabled = true; return; }
    deckSel.disabled = false;
    var last = remembered();
    rows.forEach(function (d) {
      var label = d.name + (d.format_name ? " · " + d.format_name : "");
      var o = option(String(d.id), label);
      if (String(d.id) === last) o.selected = true;
      deckSel.appendChild(o);
    });
    if (window.MtgSelect) window.MtgSelect.refresh(deckSel);
    loadCats(deckSel.value);
  }
  function loadDecks() {
    if (deckRows) { fillDecks(deckRows); return Promise.resolve(); }
    if (deckLoad) return deckLoad;
    deckSel.textContent = "";
    deckSel.appendChild(option("", "Reading your decks…"));
    deckSel.disabled = true;
    if (window.MtgSelect) window.MtgSelect.refresh(deckSel);
    deckLoad = api("GET", "/api/decks/mine").then(function (d) {
      deckLoad = null;
      if (!d.ok) { setMsg(d.message || "Your deck list could not be read.", true); return; }
      deckRows = (d.decks || []).slice().sort(function (a, b) { return (b.updated_at || "") < (a.updated_at || "") ? -1 : 1; });
      fillDecks(deckRows);
    }).catch(function () { deckLoad = null; setMsg("No connection; your decks could not be read.", true); });
    return deckLoad;
  }
  function fillCats(list) {
    catSel.textContent = "";
    catSel.appendChild(option("", "The deck's default"));
    (list || []).forEach(function (c) { if (c && c !== "Maybeboard") catSel.appendChild(option(c, c)); });
    if (window.MtgSelect) window.MtgSelect.refresh(catSel);
  }
  function loadCats(id) {
    if (!id) { fillCats([]); return; }
    if (cats[id]) { fillCats(cats[id]); return; }
    fillCats([]);
    api("GET", "/api/v1/decks/" + enc(id) + "?cards=0").then(function (d) {
      if (d.ok && d.categories) { cats[id] = d.categories; if (deckSel.value === id) fillCats(cats[id]); }
    }).catch(function () { /* the default category still works */ });
  }
  function openSheet(name) {
    if (!linked || !name) return;
    ensureSheet();
    cardName = name;
    title.textContent = "";
    title.appendChild(el("span", null, "Add " + name));
    qtyIn.value = "1";
    setMsg("");
    saveBtn.disabled = false;
    saveBtn.removeAttribute("aria-busy");
    lastFocus = document.activeElement;
    sheet.classList.add("open");
    document.documentElement.classList.add("cardview-open");
    loadDecks();
    if (window.MtgSelect) window.MtgSelect.enhance(sheet);
    setTimeout(function () { var first = $(".msel-btn", sheet) || deckSel; first.focus(); }, 0);
  }
  function closeSheet() {
    if (!sheet) return;
    sheet.classList.remove("open");
    document.documentElement.classList.remove("cardview-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function changes() {
    var qty = Math.max(1, Math.min(99, parseInt(qtyIn.value, 10) || 1));
    var ch = { action: "add", card_name: cardName, quantity: qty };
    if (catSel.value) ch.category = catSel.value;
    return [ch];
  }
  function deckName(id) {
    var row = (deckRows || []).filter(function (d) { return String(d.id) === String(id); })[0];
    return row ? row.name : "the deck";
  }
  function busy(on) {
    saveBtn.disabled = on;
    if (on) saveBtn.setAttribute("aria-busy", "true"); else saveBtn.removeAttribute("aria-busy");
  }
  function save(body) {
    var id = deckSel.value;
    if (!id) { setMsg("Pick a deck first.", true); return; }
    busy(true);
    setMsg("Saving…");
    var sent = changes();
    api("POST", "/api/v1/decks/" + enc(id) + "/edit", body || { changes: sent, confirmed: false }).then(function (d) {
      if (d.ok && d.applied) {
        busy(false);
        closeSheet();
        var open = el("a", "btn", "Open deck");
        open.href = "/decks/" + enc(id);
        toast("Added " + sent[0].quantity + " × " + cardName + " to " + deckName(id) + ".", { actions: [open] });
        return;
      }
      if (d.ok && d.needs_confirm) {
        // the hand-edit rule asks first; No rejects the proposal made for the question
        busy(false);
        setMsg(d.why + " Save anyway?", false);
        var yes = el("button", "btn-primary", "Save anyway"); yes.type = "button";
        var no = el("button", "", "No"); no.type = "button";
        msg.appendChild(document.createTextNode(" "));
        msg.appendChild(yes); msg.appendChild(document.createTextNode(" ")); msg.appendChild(no);
        yes.addEventListener("click", function () { save({ proposal_id: d.proposal_id }); });
        no.addEventListener("click", function () {
          api("POST", "/api/v1/proposals/" + enc(d.proposal_id) + "/reject", {}).catch(function () {});
          setMsg("Nothing was changed.");
        });
        yes.focus();
        return;
      }
      if (d.ok && d.status === 202 && d.proposal_id) {
        busy(false);
        closeSheet();
        var see = el("a", "btn", "Review page");
        see.href = "/proposals/" + enc(d.proposal_id);
        toast("Still saving on Archidekt; the review page shows how far it got.", { actions: [see], ms: 15000 });
        return;
      }
      if (d.ok && d.proposal_id) { location.href = "/proposals/" + enc(d.proposal_id); return; }  // writes are off: kept for review
      busy(false);
      setMsg(d.message || "The card could not be added.", true);
    }).catch(function () { busy(false); setMsg("No connection; nothing was changed.", true); });
  }
})();
