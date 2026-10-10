/* Deck page, deck list, search and collection helpers. The pages work without this file (every
   control is a form); this only makes them feel like Archidekt:
   - the toolbar's View as / Group by / Sort by selects apply on change, the local filter narrows
     the cards as you type, and card-name inputs autocomplete through the gateway's own Scryfall
     search;
   - on touch screens a tap fans a stack out and a tap on a card opens a viewer with the card
     large and what can be done with it (grid view, stacks, text rows alike);
   - a right-click, a press and hold, or Shift+F10 on a card opens the card's own menu (open it,
     one more or one fewer copy, move to a category, remove, edit) in place of the browser's;
   - on the member's own deck the menu, the viewer and a drag between categories save through
     the deck page's edit endpoint (one proposal applied at once, with a snapshot); the answer
     redraws the touched cards, the Legality chip and the Deck checks in place, with Undo. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var csrfInput = $("input[name=csrf]");
  var CSRF = csrfInput ? csrfInput.value : "";
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // -- forms that apply on change ----------------------------------------------------------
  $$("#viewform, #listform, #searchform").forEach(function (form) {
    $$("select", form).forEach(function (sel) {
      sel.addEventListener("change", function () { form.requestSubmit ? form.requestSubmit() : form.submit(); });
    });
  });

  // -- Probability of draw (hypergeometric, like Archidekt's stats tab) --------------------------
  var oddsBox = $("#odds");
  var oddsData = $("#odds-data");
  if (oddsBox && oddsData) {
    var odds;
    try { odds = JSON.parse(oddsData.textContent); } catch (e) { odds = null; }
    if (odds && odds.size) {
      var lf = [0];
      for (var i = 1; i <= odds.size; i++) lf.push(lf[i - 1] + Math.log(i));
      var lchoose = function (a, b) { return b < 0 || b > a ? -Infinity : lf[a] - lf[b] - lf[a - b]; };
      var pExact = function (N, K, n, k) {
        if (k > K || k > n || n - k > N - K) return 0;
        return Math.exp(lchoose(K, k) + lchoose(N - K, n - k) - lchoose(N, n));
      };
      var pAtLeast = function (N, K, n, k) {
        var p = 0;
        for (var j = k; j <= Math.min(K, n); j++) p += pExact(N, K, n, j);
        return p;
      };
      var oddsForm = $(".oddsform", oddsBox);
      var tbody = $("tbody", oddsBox);
      var renderOdds = function () {
        var mode = oddsForm.elements.mode.value;
        var k = Math.max(0, parseInt(oddsForm.elements.k.value, 10) || 0);
        var n = Math.min(odds.size, Math.max(1, parseInt(oddsForm.elements.n.value, 10) || 7));
        var group = odds.groups[oddsForm.elements.by.value] || {};
        tbody.textContent = "";
        Object.keys(group).forEach(function (name) {
          var K = group[name];
          var p = mode === "exact" ? pExact(odds.size, K, n, k) : pAtLeast(odds.size, K, n, k);
          var tr = el("tr");
          tr.appendChild(el("td", null, name));
          tr.appendChild(el("td", null, String(K)));
          tr.appendChild(el("td", null, (p >= 0.995 && p < 1 ? ">99" : Math.round(p * 100)) + "%"));
          tbody.appendChild(tr);
        });
      };
      oddsForm.addEventListener("input", renderOdds);
      oddsForm.addEventListener("change", renderOdds);
      renderOdds();
    }
  }

  // -- live local filter over the rendered cards (rows and image cards carry data-name) ----------
  var q = $("#q");
  var cards = $("#cards");
  if (q && cards) {
    var items = $$("[data-name]", cards);
    var stacks = $$(".stack", cards);
    var apply = function () {
      var needle = q.value.trim().toLowerCase();
      items.forEach(function (el) {
        el.style.display = !needle || el.getAttribute("data-name").indexOf(needle) >= 0 ? "" : "none";
      });
      stacks.forEach(function (st) {
        st.style.display = $$("[data-name]", st).some(function (el) { return el.style.display !== "none"; }) ? "" : "none";
      });
    };
    q.addEventListener("input", apply);
    if (q.form) q.form.addEventListener("submit", function (ev) { if (document.activeElement === q) { ev.preventDefault(); apply(); } });
  }

  // (card-name suggestions: static/suggest.js, on every input[data-suggest])

  // -- deck page only (the social block further down runs on every deck, own or not) -------
  if (cards && cards.classList.contains("deckview")) {
  var deckId = cards.getAttribute("data-deck");
  var own = cards.hasAttribute("data-own");
  var canDrag = cards.hasAttribute("data-drop");
  var byCategory = cards.getAttribute("data-grouping") === "category";
  var touch = window.matchMedia("(hover: none)").matches;
  var phone = function () {
    return window.matchMedia("(max-width: 599.98px)").matches ||
      window.matchMedia("(max-width: 899.98px) and ((pointer: coarse) or (hover: none))").matches ||
      document.body.classList.contains("app");
  };
  var enc = encodeURIComponent;
  function readJson(attr, fallback) { try { return JSON.parse(cards.getAttribute(attr)) || fallback; } catch (e) { return fallback; } }
  var deckCats = readJson("data-cats", []);
  var sideCat = cards.getAttribute("data-side") || "Maybeboard";
  var excluded = readJson("data-excluded", []);
  function frontFace(name) { return (name || "").split(" // ")[0].trim().toLowerCase(); }
  function qtyOf(card) { return parseInt(card.getAttribute("data-qty"), 10) || 0; }
  function zoneOf(card) { return card.getAttribute("data-zone") === "side" ? "side" : "main"; }
  function catOf(card) { return card.getAttribute("data-cat") || ""; }
  function stackOf(card) { var st = card.closest(".stack"); return st ? st.getAttribute("data-group") : ""; }
  function money(n) { return "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function stop(e) { e.preventDefault(); e.stopPropagation(); }

  // -- toast: what was saved, with Undo; errors stay until dismissed ---------------------------
  var toastEl = null, toastTimer = null;
  function dismissToast() { if (toastEl) toastEl.remove(); toastEl = null; clearTimeout(toastTimer); }
  function toast(text, opts) {
    opts = opts || {};
    dismissToast();
    toastEl = el("div", "toast deck-toast" + (opts.error ? " error" : "") + (opts.warn ? " warn" : ""));
    toastEl.setAttribute("role", opts.error ? "alert" : opts.dialog ? "alertdialog" : "status");
    var msg = el("span", "msg", text);
    toastEl.appendChild(msg);
    var acts = el("span", "acts");
    (opts.actions || []).forEach(function (a) { acts.appendChild(a); });
    toastEl.appendChild(acts);
    var x = el("button", "close icon-only", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", function () { dismissToast(); if (opts.onDismiss) opts.onDismiss(); });
    toastEl.appendChild(x);
    document.body.appendChild(toastEl);
    if (!opts.error && !opts.sticky) toastTimer = setTimeout(dismissToast, opts.ms || 9000);
    return toastEl;
  }

  // -- the save: the deck page's own edit endpoint (api.py), one proposal applied at once --------
  function api(path, body) {
    return fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (d) { d.status = r.status; return d; }); });
  }
  var editUrl = "/api/v1/decks/" + enc(deckId) + "/edit";
  var saving = false;
  function askConfirm(why, pid) {
    return new Promise(function (resolve) {
      var yes = el("button", "btn-primary", "Save anyway");
      yes.type = "button";
      var no = el("button", "", "Cancel");
      no.type = "button";
      var done = function (v) { dismissToast(); resolve(v); };
      var declined = false;
      var decline = function () {
        // the proposal made for the check is not wanted (Cancel, Escape or the toast closed):
        // reject it so Proposals stays clean and it does not count toward the pending cap
        if (declined) return;
        declined = true;
        api("/api/v1/proposals/" + enc(pid) + "/reject", {}).catch(function () { /* a leftover pending proposal is harmless */ });
      };
      yes.addEventListener("click", function () { done(true); });
      no.addEventListener("click", function () { decline(); done(false); });
      toast(why + " Save anyway?", { warn: true, dialog: true, sticky: true, actions: [yes, no], onDismiss: function () { decline(); resolve(false); } });
      yes.focus();
    });
  }
  /* changes: the proposal's changes; undo: the inverse changes (null for none, as for an undo
     itself). Resolves with the endpoint's answer once applied and drawn; rejects with an Error whose
     message is shown, or with {cancelled: true} when the person said no to the confirmation. */
  function saveEdit(changes, undoChanges, opts) {
    opts = opts || {};
    if (saving) return Promise.reject(new Error("Another change is still saving; one moment."));
    saving = true;
    var body = opts.proposalId ? { proposal_id: opts.proposalId } : { changes: changes, confirmed: opts.confirmed === true };
    return api(editUrl, body).then(function (d) {
      if (d.ok && d.applied) {
        saving = false;
        applyAnswer(d, changes, undoChanges);
        return d;
      }
      if (d.ok && d.needs_confirm) {
        saving = false;
        return askConfirm(d.why, d.proposal_id).then(function (yes) {
          if (!yes) { var c = new Error("Nothing was changed."); c.cancelled = true; throw c; }
          return saveEdit(changes, undoChanges, { proposalId: d.proposal_id });
        });
      }
      saving = false;
      if (d.ok && d.status === 202 && d.proposal_id) {
        var see = el("a", "btn", "Review page");
        see.href = "/proposals/" + enc(d.proposal_id);
        toast("Still saving on Archidekt; the review page shows how far it got.", { sticky: true, actions: [see] });
        return d;
      }
      if (d.ok && d.proposal_id) { location.href = "/proposals/" + enc(d.proposal_id); return d; }  // writes are off: kept for review
      throw new Error(d.message || "The change could not be saved.");
    }).catch(function (err) {
      saving = false;
      if (!err || !err.cancelled) throw err instanceof Error ? err : new Error("No connection; nothing was changed.");
      throw err;
    });
  }
  function showError(err) {
    if (err && err.cancelled) return;
    toast((err && err.message) || "No connection; nothing was changed.", { error: true });
  }

  // -- drawing the answer in place: the touched cards, the stack totals, the chip and the panels --
  var removedCards = {}; // front face -> [elements taken off the page], put back by an Undo
  function stackByName(name) {
    return $$(".stack", cards).filter(function (st) { return st.getAttribute("data-group") === name; })[0] || null;
  }
  function ensureStack(name) {
    var st = stackByName(name);
    if (st || !byCategory) return st;
    st = el("section", "stack");
    st.setAttribute("data-group", name);
    var head = el("div", "stackhead");
    var h4 = el("h4");
    h4.appendChild(el("span", "title", name));
    head.appendChild(h4);
    head.appendChild(el("div", "meta", "Qty: 0"));
    st.appendChild(head);
    st.appendChild(cards.classList.contains("text") || cards.classList.contains("table") ? el("ul", "plain rows") : el("div", "cards"));
    // the maybeboard sits last, as on Archidekt; a new category goes before it
    var side = excluded.map(stackByName).filter(Boolean)[0];
    if (side && excluded.indexOf(name) < 0) cards.insertBefore(st, side); else cards.appendChild(st);
    return st;
  }
  function setQty(card, qty) {
    card.setAttribute("data-qty", String(qty));
    var q = $(".qty, .q", card);
    if (q) q.textContent = String(qty);
  }
  // the finish badge Archidekt shows beside the name (F, E); none for a normal card
  function setFinish(card, finish) {
    card.setAttribute("data-finish", finish);
    var badge = $(".finish", card);
    if (finish === "Normal") { if (badge) badge.remove(); return; }
    if (!badge) {
      var name = $(".name", card);
      if (!name) return;
      badge = el("span", "finish");
      name.parentNode.insertBefore(badge, name.nextSibling);
    }
    badge.title = finish;
    badge.textContent = finish.charAt(0);
  }
  // Archidekt's colour tag ("Have,#37d67a"): the dot before the name (text rows) or in the
  // picture's corner (image cards), named for screen readers
  function splitLabel(label) {
    var i = (label || "").lastIndexOf(",");
    var name = (i < 0 ? label || "" : label.slice(0, i)).trim();
    var colour = i < 0 ? "" : label.slice(i + 1).trim();
    if (/^#[0-9a-fA-F]{3}$/.test(colour)) colour = "#" + colour.slice(1).replace(/./g, function (c) { return c + c; });  // #fff
    return { name: name, colour: /^#[0-9a-fA-F]{6}$/.test(colour) ? colour.toLowerCase() : "" };
  }
  function setLabel(card, label) {
    var t = splitLabel(label);
    var dot = $(".tagdot", card);
    if (!t.name) { card.removeAttribute("data-label"); if (dot) dot.remove(); return; }
    card.setAttribute("data-label", label);
    if (!dot) {
      dot = el("span", "tagdot");
      var n = $(".n", card);
      if (n) n.insertBefore(dot, n.firstChild); else card.appendChild(dot);
    }
    dot.title = "Tag: " + t.name;
    dot.style.background = t.colour || "";
    dot.textContent = "";
    dot.appendChild(el("span", "sr-only", "tag " + t.name));
  }
  function refreshStacks() {
    $$(".stack", cards).forEach(function (st) {
      var items = $$(".c, .row", st);
      if (!items.length) { st.remove(); return; }
      var qty = 0, price = 0, priced = false;
      items.forEach(function (c) {
        var n = qtyOf(c);
        qty += n;
        var p = parseFloat(c.getAttribute("data-price"));
        if (!isNaN(p)) { price += p * n; priced = true; }
      });
      var meta = $(".stackhead .meta", st);
      if (meta) meta.textContent = "Qty: " + qty + (priced && price ? " · Price: " + money(price) : "");
    });
  }
  function swapHtml(selector, html, insertInto, before) {
    var old = $(selector);
    if (old) {
      if (html) old.outerHTML = html; else old.remove();
      return;
    }
    if (!html) return;
    var holder = $(insertInto);
    if (!holder) return;
    var tpl = document.createElement("template");
    tpl.innerHTML = html;
    var anchor = before ? $(before, holder) : null;
    holder.insertBefore(tpl.content, anchor);
  }
  function drawStats(d) {
    var st = d.stats || {};
    if (d.banner_html !== undefined) swapHtml(".banner .legal", d.banner_html, ".banner .row:nth-of-type(2)", ".banner .row:nth-of-type(2) > span:nth-child(2)");
    if (d.checks_html !== undefined) swapHtml(".stats .checks", d.checks_html, ".stats .side", ".legality, .bracket");
    if (d.legality_html !== undefined) swapHtml(".stats .legality", d.legality_html, ".stats .side", ".bracket");
    $$(".banner .row > span").forEach(function (sp) {
      var t = sp.textContent;
      if (/^Size:/.test(t) && st.card_count !== undefined) sp.textContent = "Size: " + st.card_count;
      else if (/distinct cards?$/.test(t) && st.distinct !== undefined) sp.textContent = window.MtgText ? window.MtgText.plural(st.distinct, "distinct card") : st.distinct + " distinct cards";
      else if (/^Est cost:/.test(t) && st.price_total !== undefined && st.price_total !== null) { var b = $("b", sp); if (b) b.textContent = money(st.price_total); }
      else if (/^Salt sum:/.test(t) && st.salt_total !== undefined && st.salt_total !== null) { var s2 = $("b", sp); if (s2) s2.textContent = String(st.salt_total); }
    });
    var tiles = $$(".stats .tiles .tile");
    if (tiles[0] && st.card_count !== undefined) $("b", tiles[0]).textContent = String(st.card_count);
    if (tiles[1] && st.land_count !== undefined) $("b", tiles[1]).textContent = String(st.land_count);
    if (tiles[2] && st.price_total !== undefined && st.price_total !== null) $("b", tiles[2]).textContent = money(st.price_total);
  }
  function drawRows(rows, names) {
    var left = (rows || []).slice();
    var take = function (card) {
      var rel = card.getAttribute("data-rel");
      var i = -1;
      if (rel) left.some(function (r, k) { if (String(r.relation_id) === rel) { i = k; return true; } return false; });
      if (i < 0) left.some(function (r, k) {
        if (frontFace(r.name) === frontFace(card.getAttribute("data-card")) && r.zone === zoneOf(card)) { i = k; return true; }
        return false;
      });
      if (i < 0) left.some(function (r, k) { if (frontFace(r.name) === frontFace(card.getAttribute("data-card"))) { i = k; return true; } return false; });
      return i < 0 ? null : left.splice(i, 1)[0];
    };
    var place = function (card, r) {
      setQty(card, r.qty);
      card.setAttribute("data-zone", r.zone);
      if (r.finish) setFinish(card, r.finish);
      if (r.label !== undefined) setLabel(card, r.label);
      var cat = (r.categories && r.categories[0]) || "";
      card.setAttribute("data-cat", cat);
      if (r.relation_id !== null && r.relation_id !== undefined) card.setAttribute("data-rel", String(r.relation_id));
      card.classList.toggle("side", r.zone === "side" && card.classList.contains("row"));
      if (byCategory && cat && stackOf(card) !== cat) {
        var st = ensureStack(cat);
        if (st) $(".cards, .rows", st).appendChild(card);
      }
    };
    names.forEach(function (name) {
      $$(".c, .row", cards).filter(function (c) { return frontFace(c.getAttribute("data-card")) === name; }).forEach(function (card) {
        var r = take(card);
        if (r) { place(card, r); return; }
        (removedCards[name] = removedCards[name] || []).push(card);
        card.remove();
      });
    });
    // rows with no card on the page: a card an Undo put back (its element was kept)
    left.forEach(function (r) {
      var kept = removedCards[frontFace(r.name)];
      var card = kept && kept.shift();
      if (!card) return;
      var st = ensureStack((r.categories && r.categories[0]) || stackOf(card)) || stackByName(stackOf(card)) || $(".stack", cards);
      if (!st) return;
      $(".cards, .rows", st).appendChild(card);
      place(card, r);
    });
    refreshStacks();
  }
  function namesOf(changes) {
    var seen = {};
    return changes.map(function (ch) { return frontFace(ch.card_name); }).filter(function (n) { if (seen[n]) return false; seen[n] = true; return true; });
  }
  var refreshed = false;
  function applyAnswer(d, changes, undoChanges) {
    var names = namesOf(changes);
    drawRows(d.rows, names);
    drawStats(d);
    if (menu) closeMenu();
    var actions = [];
    if (undoChanges && undoChanges.length) {
      var undo = el("button", "btn", "Undo");
      undo.type = "button";
      undo.addEventListener("click", function () {
        undo.disabled = true;
        undo.textContent = "Undoing…";
        saveEdit(undoChanges, null).then(function () { toast("Undone. Both steps are under History."); }).catch(showError);
      });
      actions.push(undo);
    }
    var hist = el("a", "", "History");
    hist.href = "/history?deck_id=" + enc(deckId);
    actions.push(hist);
    if (d.stale && !refreshed) {
      // Archidekt's read still showed the old rows: read once more in a moment, then draw that
      refreshed = true;
      toast("Saved. Refreshing…", { sticky: true, actions: actions });
      setTimeout(function () {
        api(editUrl, { refresh: true, names: names }).then(function (r) {
          if (r.ok) { drawRows(r.rows, names); drawStats(r); }
          toast("Saved. Snapshot kept under History", { actions: actions });
        }).catch(function () { toast("Saved. Snapshot kept under History", { actions: actions }); });
      }, 1500);
      return;
    }
    toast("Saved. Snapshot kept under History", { actions: actions });
  }

  // -- the edits a card offers (menu and viewer alike) ----------------------------------------
  function changeFor(card, action, value) {
    var name = card.getAttribute("data-card");
    var zone = zoneOf(card);
    var ch = { action: action, card_name: name };
    if (zone === "side") ch.zone = "side";
    if (action === "set_quantity") ch.quantity = value;
    if (action === "set_category") ch.category = value;
    return ch;
  }
  // Another row of the same card in a zone: a set_category or set_quantity names the card and
  // the zone, so it would move or count both rows. The menu's own change is fine (the person
  // sees the rows); an Undo built from it would not be, so none is offered then.
  function twinIn(card, zone) {
    var face = frontFace(card.getAttribute("data-card"));
    return $$(".c, .row", cards).some(function (c) { return c !== card && zoneOf(c) === zone && frontFace(c.getAttribute("data-card")) === face; });
  }
  function inverseFor(card, ch) {
    var name = card.getAttribute("data-card");
    var zone = zoneOf(card);
    var qty = qtyOf(card);
    var inv;
    if (ch.action === "set_quantity") inv = { action: "set_quantity", card_name: name, quantity: qty };
    else if (ch.action === "remove") {
      inv = { action: "add", card_name: name, quantity: qty };
      var cat = catOf(card);
      if (cat) inv.category = cat;
    } else if (ch.action === "set_category") {
      inv = { action: "set_category", card_name: name, category: catOf(card) || stackOf(card) };
      // after the move the row sits in the zone its new category puts it in
      zone = excluded.indexOf(ch.category) >= 0 ? "side" : "main";
      if (twinIn(card, zone)) return null;
    } else return null;
    if (zone === "side") inv.zone = "side";
    return inv;
  }
  function edit(card, action, value) {
    var ch = changeFor(card, action, value);
    var inv = inverseFor(card, ch);
    return saveEdit([ch], inv ? [inv] : null);
  }
  function stepQty(card, delta) {
    var q = qtyOf(card) + delta;
    if (q <= 0) return edit(card, "remove");
    return edit(card, "set_quantity", q);
  }
  function categoryChoices(card) {
    var cur = byCategory ? stackOf(card) : catOf(card);
    var names = deckCats.slice();
    if (byCategory) $$(".stack", cards).forEach(function (st) { var n = st.getAttribute("data-group"); if (names.indexOf(n) < 0) names.push(n); });
    if (names.indexOf(sideCat) < 0) names.push(sideCat);
    return names.map(function (n) { return { name: n, current: n === cur, side: excluded.indexOf(n) >= 0 }; });
  }

  // -- card viewer (static/cardview.js shows the whole card; this adds the deck's actions) -------
  var CardView = window.MtgCardView;
  function closeViewer() { if (CardView) CardView.close(); }
  function openViewer(card) {
    if (!CardView) return;
    var name = card.getAttribute("data-card") || "";
    var acts = [];
    if (own && deckId) {
      var ctl = el("span", "qtyctl");
      ctl.setAttribute("role", "group");
      ctl.setAttribute("aria-label", "Copies in the deck");
      var minus = el("button", "btn", "−");
      minus.type = "button";
      var n = el("b", "n", String(qtyOf(card)));
      n.setAttribute("aria-live", "polite");
      var plus = el("button", "btn", "+");
      plus.type = "button";
      var relabel = function () {
        var q = qtyOf(card);
        n.textContent = String(q);
        minus.setAttribute("aria-label", q <= 1 ? "Remove the last copy" : "One fewer: " + (q - 1));
        plus.setAttribute("aria-label", "One more: " + (q + 1));
      };
      relabel();
      var busy = function (on) { minus.disabled = plus.disabled = on; ctl.classList.toggle("busy", on); };
      var step = function (delta) {
        busy(true);
        stepQty(card, delta).then(function () {
          busy(false);
          if (!card.isConnected) { closeViewer(); return; }
          relabel();
        }).catch(function (err) { busy(false); showError(err); });
      };
      minus.addEventListener("click", function () { step(-1); });
      plus.addEventListener("click", function () { step(1); });
      ctl.appendChild(minus); ctl.appendChild(n); ctl.appendChild(plus);
      acts.push(ctl);
      var rm = el("button", "btn btn-danger", "Remove from deck");
      rm.type = "button";
      rm.addEventListener("click", function () {
        rm.disabled = true;
        edit(card, "remove").then(function () { closeViewer(); }).catch(function (err) { rm.disabled = false; showError(err); });
      });
      acts.push(rm);
      var move = el("button", "btn", "Move to…");
      move.type = "button";
      move.addEventListener("click", function () {
        closeViewer();
        setTimeout(function () { var r = card.getBoundingClientRect(); openMenu(card, r.left + r.width / 2, r.top + r.height / 2, { submenu: true }); }, 50);
      });
      acts.push(move);
      var editLink = el("a", "btn", "Edit in deck editor");
      editLink.href = "/decks/" + enc(deckId) + "/edit#card-" + enc(name);
      acts.push(editLink);
    }
    var ownBtn = el("button", "btn", "I own this card");
    ownBtn.type = "button";
    ownBtn.addEventListener("click", function () {
      ownBtn.disabled = true;
      ownBtn.textContent = "Adding…";
      fetch("/collection/api/add", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
        body: JSON.stringify({ items: [{ name: name, quantity: 1 }], source: "manual" })
      }).then(function (r) { return r.json(); }).then(function (d) {
        ownBtn.textContent = d.ok && d.added && d.added.length ? "Added to your collection" : "Could not add (" + ((d && d.message) || "lookup failed") + ")";
      }).catch(function () { ownBtn.textContent = "Could not add: no connection"; });
    });
    acts.push(ownBtn);
    acts.push(scryfallLink(name));
    CardView.open(CardView.fromElement(card), acts);
  }
  function scryfallLink(name, cls) {
    var scry = el("a", cls || "btn", "Open on Scryfall");
    scry.href = "https://scryfall.com/search?q=" + encodeURIComponent("!\"" + name + "\"");
    scry.target = "_blank";
    scry.rel = "noopener noreferrer";
    return scry;
  }

  // -- card menu: right-click, press and hold, Shift+F10 or the Menu key on a focused card -------
  // A themed menu (the shared .menu styling) in place of the browser's; Escape, a click or tap
  // outside, scrolling and the phone's Back button close it, and focus goes back to the card.
  var menu = null, menuCard = null, menuStack = null, menuPushed = false, menuFocusBack = false, ignorePop = false;
  function closeMenu(fromHistory) {
    if (!menu) return;
    var m = menu, card = menuCard, stack = menuStack, back = menuFocusBack;
    menu = null; menuCard = null; menuStack = null; menuFocusBack = false;
    m.remove();
    if (menuPushed && !fromHistory) {
      // going back over the menu's entry fires popstate later; that one is ours, not a Back press
      menuPushed = false;
      ignorePop = true;
      try { history.back(); } catch (e) { ignorePop = false; }
    }
    menuPushed = false;
    if (!back) return;
    if (card && card.isConnected) { card.focus(); return; }
    // the card was removed: the next card of its stack, else any card, keeps the keyboard on the page
    var next = (stack && stack.isConnected && $(".c, .row", stack)) || $(".c, .row", cards);
    if (next) next.focus();
  }
  function menuItem(text, ic, onClick, cls) {
    var b = el("button", cls || "", "");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    if (ic) b.appendChild(icon(ic));
    b.appendChild(document.createTextNode(text));
    if (onClick) b.addEventListener("click", function (e) { stop(e); onClick(); });
    return b;
  }
  // the gateway's own icons, rendered by the server into a template under #cards (theme.icon)
  function icon(name) {
    var tpl = $("template.icons", cards);
    var src = tpl ? tpl.content.querySelector("[data-ic='" + name + "'] svg") : null;
    return src ? src.cloneNode(true) : document.createTextNode("");
  }
  function openMenu(card, x, y, opts) {
    opts = opts || {};
    closeMenu();
    var name = card.getAttribute("data-card") || "";
    var m = el("div", "menu ctxmenu");
    m.setAttribute("role", "menu");
    m.setAttribute("aria-label", "Card: " + name);
    var head = el("div", "head", name);
    m.appendChild(head);
    m.appendChild(menuItem("Open card", "eye", function () { closeMenu(); openViewer(card); }));
    if (own && deckId) {
      var row = el("div", "item qtyrow");
      row.setAttribute("role", "none");
      row.appendChild(el("span", "lbl", "Quantity"));
      var minus = el("button", "step", "−");
      minus.type = "button";
      minus.setAttribute("role", "menuitem");
      var n = el("b", "n", String(qtyOf(card)));
      var plus = el("button", "step", "+");
      plus.type = "button";
      plus.setAttribute("role", "menuitem");
      var relabel = function () {
        var q = qtyOf(card);
        n.textContent = String(q);
        minus.setAttribute("aria-label", q <= 1 ? "Remove the last copy of " + name : "One fewer " + name + ": " + (q - 1));
        plus.setAttribute("aria-label", "One more " + name + ": " + (q + 1));
      };
      relabel();
      var stepping = function (delta) {
        minus.disabled = plus.disabled = true;
        m.classList.add("busy");
        stepQty(card, delta).then(function () { /* applyAnswer closed the menu */ }).catch(function (err) {
          if (menu === m) { minus.disabled = plus.disabled = false; m.classList.remove("busy"); relabel(); }
          showError(err);
        });
      };
      minus.addEventListener("click", function (e) { stop(e); stepping(-1); });
      plus.addEventListener("click", function (e) { stop(e); stepping(1); });
      row.appendChild(minus); row.appendChild(n); row.appendChild(plus);
      m.appendChild(row);
      var moveBtn = menuItem("Move to", "swap", null, "more");
      moveBtn.setAttribute("aria-haspopup", "true");
      moveBtn.setAttribute("aria-expanded", "false");
      moveBtn.appendChild(el("span", "chev", "›"));
      var sub = el("div", "sub");
      sub.setAttribute("role", "group");
      sub.setAttribute("aria-label", "Categories");
      sub.hidden = true;
      categoryChoices(card).forEach(function (c) {
        var b = menuItem(c.name, c.side ? "eye-off" : "tag", function () {
          m.classList.add("busy");
          edit(card, "set_category", c.name).catch(function (err) { if (menu === m) m.classList.remove("busy"); showError(err); });
        }, c.current ? "on" : "");
        if (c.side) b.title = "Not counted in the deck";
        if (c.current) { b.disabled = true; b.setAttribute("aria-current", "true"); }
        sub.appendChild(b);
      });
      var toggleSub = function (open) {
        sub.hidden = !open;
        moveBtn.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) { var f = $("button:not([disabled])", sub); if (f) f.focus(); fit(m); }
      };
      moveBtn.addEventListener("click", function (e) { stop(e); toggleSub(sub.hidden); });
      moveBtn.addEventListener("keydown", function (e) { if (e.key === "ArrowRight") { stop(e); toggleSub(true); } });
      sub.addEventListener("keydown", function (e) { if (e.key === "ArrowLeft") { stop(e); toggleSub(false); moveBtn.focus(); } });
      m.appendChild(moveBtn);
      m.appendChild(sub);
      if (window.MtgDeckTag) m.appendChild(menuItem("Colour tag…", "tag", function () { closeMenu(); window.MtgDeckTag(card); }));
      m.appendChild(menuItem("Remove from deck", "x", function () {
        m.classList.add("busy");
        edit(card, "remove").catch(function (err) { if (menu === m) m.classList.remove("busy"); showError(err); });
      }, "danger"));
      m.appendChild(el("div", "sep"));
      var editLink = el("a", "", "");
      editLink.setAttribute("role", "menuitem");
      editLink.href = "/decks/" + enc(deckId) + "/edit#card-" + enc(name);
      editLink.appendChild(icon("edit"));
      editLink.appendChild(document.createTextNode("Edit in deck editor"));
      m.appendChild(editLink);
    } else {
      var scry = scryfallLink(name, "");
      scry.setAttribute("role", "menuitem");
      scry.insertBefore(icon("external"), scry.firstChild);
      m.appendChild(scry);
    }
    m.addEventListener("keydown", function (e) {
      var items = $$("[role=menuitem]:not([disabled])", m).filter(function (b) { return b.offsetParent !== null; });
      var i = items.indexOf(document.activeElement);
      if (e.key === "Escape") { stop(e); closeMenu(); return; }
      if (e.key === "ArrowDown") { stop(e); (items[i + 1] || items[0]).focus(); }
      else if (e.key === "ArrowUp") { stop(e); (items[i - 1] || items[items.length - 1]).focus(); }
      else if (e.key === "Home") { stop(e); items[0].focus(); }
      else if (e.key === "End") { stop(e); items[items.length - 1].focus(); }
      else if (e.key === "Tab") { closeMenu(); }
    });
    m.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    menu = m;
    menuCard = card;
    menuStack = card.closest(".stack");
    menuFocusBack = true;
    menuScroll = [window.scrollX, window.scrollY];
    m.style.visibility = "hidden";
    document.body.appendChild(m);
    m.style.left = x + "px";
    m.style.top = y + "px";
    fit(m);
    m.style.visibility = "";
    if (opts.submenu && own) { var mb = $(".more", m); if (mb) mb.click(); }
    var first = $("[role=menuitem]:not([disabled])", m);
    if (first && !(opts.submenu && own)) first.focus();
    if (phone()) { try { history.pushState({ menu: 1, card: 1 }, ""); menuPushed = true; } catch (e) { menuPushed = false; } }
  }
  function fit(m) {
    // keep the whole menu inside the window: never a horizontal scrollbar, never off the bottom
    var pad = 8;
    var w = m.offsetWidth, h = m.offsetHeight;
    var x = parseFloat(m.style.left) || 0, y = parseFloat(m.style.top) || 0;
    if (x + w + pad > window.innerWidth) x = Math.max(pad, window.innerWidth - w - pad);
    if (y + h + pad > window.innerHeight) y = Math.max(pad, window.innerHeight - h - pad);
    m.style.left = x + "px";
    m.style.top = y + "px";
    m.style.maxHeight = (window.innerHeight - y - pad) + "px";
  }
  cards.addEventListener("contextmenu", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card || !cards.contains(card)) return;
    e.preventDefault();  // never the browser's image menu over a card
    if (picking || (menu && menuCard === card)) return;  // a touch hold already opened it
    openMenu(card, e.clientX, e.clientY);
  });
  cards.addEventListener("keydown", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card) return;
    if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) {
      stop(e);
      if (picking) return;
      var r = card.getBoundingClientRect();
      openMenu(card, r.left + Math.min(r.width / 2, 40), r.top + Math.min(r.height / 2, 40));
    }
  });
  document.addEventListener("pointerdown", function (e) { if (menu && !menu.contains(e.target)) closeMenu(); }, true);
  document.addEventListener("keydown", function (e) { if (menu && e.key === "Escape" && !menu.contains(e.target)) { closeMenu(); } });
  // the page scrolling away closes the menu (a list inside it may scroll; a scroll that ended
  // just before the menu opened reports itself a frame later and does not count)
  var menuScroll = [0, 0], scrollGrace = 0;
  window.addEventListener("scroll", function (e) {
    if (!menu || (e.target instanceof Element && menu.contains(e.target))) return;
    if (Date.now() < scrollGrace) { menuScroll = [window.scrollX, window.scrollY]; return; }
    if (Math.abs(window.scrollX - menuScroll[0]) > 4 || Math.abs(window.scrollY - menuScroll[1]) > 4) closeMenu();
  }, { capture: true, passive: true });
  window.addEventListener("resize", function () { if (menu) closeMenu(); });
  window.addEventListener("popstate", function () {
    if (ignorePop) {
      // going back may put the page's scroll position back too (a menu opened since stays)
      ignorePop = false;
      scrollGrace = Date.now() + 400;
      menuScroll = [window.scrollX, window.scrollY];
      return;
    }
    if (menu) closeMenu(true);
  });

  // -- stacks and grid: tap to fan out, tap a card to open it ----------------------------------
  var isStacks = cards.classList.contains("stacks");
  var dragged = null;
  var swallowClick = false;
  cards.addEventListener("click", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card || !cards.contains(card) || dragged || swallowClick) return;
    if (picking) { e.preventDefault(); togglePick(card); return; }
    var fan = card.closest(".cards");
    if (isStacks && touch && fan && !fan.classList.contains("fanned")) {
      // first tap on a collapsed stack fans it out; cards behind the top one were not visible yet
      $$(".cards.fanned", cards).forEach(function (f) { f.classList.remove("fanned"); });
      fan.classList.add("fanned");
      return;
    }
    openViewer(card);
  });
  cards.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches && e.target.matches(".c, .row")) {
      e.preventDefault();
      if (picking) togglePick(e.target); else openViewer(e.target);
    }
  });

  // -- own deck: select several cards, then move, remove or refinish them in one save -----------
  // The member's own hand edit, like the menu's: one proposal applied with its snapshot, with an
  // Undo when every step can be put back. The assistant never reaches this path.
  var picking = false;
  var togglePick = function () {};
  if (own && deckId) (function () {
    var bar = el("div", "bulkbar");
    bar.setAttribute("role", "region");
    bar.setAttribute("aria-label", "Select cards");
    var start = el("button", "btn", "Select cards");
    start.type = "button";
    start.setAttribute("aria-pressed", "false");
    var tools = el("div", "bulktools");
    tools.hidden = true;
    var count = el("span", "count", "");
    count.setAttribute("aria-live", "polite");
    var all = el("button", "btn-ghost small", "Select all shown");
    all.type = "button";
    var moveSel = el("select");
    moveSel.setAttribute("aria-label", "Move the selected cards to a category");
    var finishSel = el("select");
    finishSel.setAttribute("aria-label", "Set the finish of the selected cards");
    [["", "Set finish…"], ["normal", "Normal"], ["foil", "Foil"], ["etched", "Etched"]].forEach(function (o) {
      var opt = el("option", null, o[1]);
      opt.value = o[0];
      finishSel.appendChild(opt);
    });
    var tagBtn = el("button", "btn", "Colour tag…");
    tagBtn.type = "button";
    tagBtn.setAttribute("aria-expanded", "false");
    var rm = el("button", "btn danger", "Remove");
    rm.type = "button";
    // the colour tag form: a name (the deck's own tags offered), a colour, Apply or Take off
    var tagForm = el("div", "tagform");
    tagForm.hidden = true;
    var tagName = el("input");
    tagName.type = "text";
    tagName.maxLength = 40;
    tagName.placeholder = "Tag name, e.g. Have";
    tagName.setAttribute("aria-label", "Tag name");
    tagName.setAttribute("list", "deck-tags");
    var known = el("datalist");
    known.id = "deck-tags";
    var tagColour = el("input");
    tagColour.type = "color";
    tagColour.value = "#37d67a";
    tagColour.setAttribute("aria-label", "Tag colour");
    var tagApply = el("button", "btn-primary", "Apply tag");
    tagApply.type = "button";
    var tagOff = el("button", "btn-ghost small", "Take tag off");
    tagOff.type = "button";
    [tagName, known, tagColour, tagApply, tagOff].forEach(function (n) { tagForm.appendChild(n); });
    var done = el("button", "btn-ghost small", "Cancel");
    done.type = "button";
    var status = el("p", "status", "");
    status.setAttribute("role", "status");
    [count, all, moveSel, finishSel, tagBtn, rm, done].forEach(function (n) { tools.appendChild(n); });
    bar.appendChild(start);
    bar.appendChild(tools);
    bar.appendChild(tagForm);
    bar.appendChild(status);
    cards.parentNode.insertBefore(bar, cards);

    function picked() { return $$(".c.picked, .row.picked", cards); }
    // the themed dropdowns (static/select.js) redraw their label and state from the real selects
    function syncSelects() {
      if (window.MtgSelect) [moveSel, finishSel].forEach(function (c) { window.MtgSelect.refresh(c); });
    }
    function fillMoves() {
      moveSel.textContent = "";
      var first = el("option", null, "Move to…");
      first.value = "";
      moveSel.appendChild(first);
      var seen = {};
      deckCats.concat($$(".stack", cards).map(function (st) { return byCategory ? st.getAttribute("data-group") : ""; }), [sideCat])
        .forEach(function (n) {
          if (!n || seen[n]) return;
          seen[n] = true;
          var opt = el("option", null, n);
          opt.value = n;
          moveSel.appendChild(opt);
        });
    }
    function refresh() {
      var n = picked().length;
      count.textContent = n === 1 ? "1 card selected" : n + " cards selected";
      [moveSel, finishSel, tagBtn, rm, tagApply, tagOff].forEach(function (c) { c.disabled = n === 0 || saving; });
      syncSelects();
    }
    function setMode(on) {
      picking = on;
      cards.classList.toggle("picking", on);
      start.setAttribute("aria-pressed", on ? "true" : "false");
      start.hidden = on;
      tools.hidden = !on;
      showTagForm(false);
      $$(".c, .row", cards).forEach(function (c) {
        c.classList.remove("picked");
        if (on) c.setAttribute("aria-pressed", "false"); else c.removeAttribute("aria-pressed");
      });
      if (on) { closeViewer(); if (menu) closeMenu(); fillMoves(); refresh(); all.focus(); } else start.focus();
      status.textContent = "";
    }
    togglePick = function (card) {
      var on = !card.classList.contains("picked");
      card.classList.toggle("picked", on);
      card.setAttribute("aria-pressed", on ? "true" : "false");
      refresh();
    };
    start.addEventListener("click", function () { setMode(true); });
    done.addEventListener("click", function () { setMode(false); });
    all.addEventListener("click", function () {
      $$(".c, .row", cards).forEach(function (c) {
        if (c.offsetParent === null) return;  // hidden by the filter or a collapsed stack
        c.classList.add("picked");
        c.setAttribute("aria-pressed", "true");
      });
      refresh();
    });
    document.addEventListener("keydown", function (e) { if (picking && e.key === "Escape" && !document.querySelector(".confirmbar, .cardview.open, .deck-toast[role=alertdialog]")) setMode(false); });

    // One change per card name and zone: two rows of a card in one zone are one Archidekt row
    // set as far as a set_category or a remove is concerned.
    function unique(list, keyOf) {
      var seen = {};
      return list.filter(function (c) { var k = keyOf(c); if (seen[k]) return false; seen[k] = true; return true; });
    }
    function run(changes, inverses, what) {
      if (!changes.length) return;
      status.className = "status";
      status.textContent = "Saving to Archidekt…";
      refresh();
      [moveSel, finishSel, tagBtn, rm, all, tagApply, tagOff].forEach(function (c) { c.disabled = true; });
      syncSelects();
      saveEdit(changes, inverses).then(function (d) {
        setMode(false);
        // a change still saving (or kept for review) says so in its own message
        status.textContent = d && d.applied ? what : "";
      }).catch(function (err) {
        all.disabled = false;
        status.className = "status notice error";
        status.textContent = err && err.cancelled ? "" : "Could not save: " + ((err && err.message) || "no connection");
        refresh();
      });
    }
    function byNameZone(c) { return frontFace(c.getAttribute("data-card")) + "|" + zoneOf(c); }
    function showTagForm(open) {
      tagForm.hidden = !open;
      tagBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) return;
      known.textContent = "";
      var seen = {};
      $$("[data-label]", cards).forEach(function (c) {
        var t = splitLabel(c.getAttribute("data-label"));
        if (!t.name || seen[t.name]) return;
        seen[t.name] = t.colour || "#656565";
        var o = el("option");
        o.value = t.name;
        known.appendChild(o);
      });
      tagName.oninput = function () { if (seen[tagName.value.trim()]) tagColour.value = seen[tagName.value.trim()]; };
      tagName.focus();
    }
    tagBtn.addEventListener("click", function () { showTagForm(tagForm.hidden); });
    // One set_label per card name and zone; the Undo puts back each one's old tag, and is only
    // offered when every row of that card in that zone had the same one.
    function tagChanges(name, colour) {
      var list = unique(picked(), byNameZone);
      var changes = [], inverses = [];
      list.forEach(function (c) {
        var ch = { action: "set_label", card_name: c.getAttribute("data-card"), label: name };
        if (name) ch.color = colour;
        if (zoneOf(c) === "side") ch.zone = "side";
        changes.push(ch);
        if (!inverses) return;
        var olds = {};
        $$(".c, .row", cards).forEach(function (o) {
          if (byNameZone(o) === byNameZone(c)) olds[o.getAttribute("data-label") || ""] = 1;
        });
        var keys = Object.keys(olds);
        if (keys.length !== 1) { inverses = null; return; }
        var old = splitLabel(keys[0]);
        var inv = { action: "set_label", card_name: ch.card_name, label: old.name };
        if (old.name && old.colour) inv.color = old.colour;
        if (ch.zone) inv.zone = "side";
        inverses.push(inv);
      });
      // a card already carrying exactly this tag is nothing to change (the gateway would refuse)
      var keep = changes.map(function (ch, i) {
        var c = list[i], cur = splitLabel(c.getAttribute("data-label") || "");
        return !(cur.name === name && (!name || cur.colour === (colour || "").toLowerCase()));
      });
      return {
        changes: changes.filter(function (_, i) { return keep[i]; }),
        inverses: inverses && inverses.filter(function (_, i) { return keep[i]; })
      };
    }
    tagApply.addEventListener("click", function () {
      var name = tagName.value.trim();
      if (!name || name.indexOf(",") >= 0) {
        status.className = "status notice error";
        status.textContent = "A tag needs a name, without commas.";
        tagName.focus();
        return;
      }
      var t = tagChanges(name, tagColour.value);
      if (!t.changes.length) { status.className = "status"; status.textContent = "Those cards already have that tag."; return; }
      run(t.changes, t.inverses, "Tagged " + name + ".");
    });
    tagOff.addEventListener("click", function () {
      var t = tagChanges("", "");
      if (!t.changes.length) { status.className = "status"; status.textContent = "Those cards have no tag."; return; }
      run(t.changes, t.inverses, "Tag taken off.");
    });
    window.MtgDeckTag = function (card) {
      if (!picking) setMode(true);
      if (!card.classList.contains("picked")) togglePick(card);
      showTagForm(true);
    };
    rm.addEventListener("click", function () {
      var list = unique(picked(), byNameZone);
      var changes = [], inverses = [];
      list.forEach(function (c) {
        var ch = changeFor(c, "remove");
        changes.push(ch);
        // a remove takes every row of the card in that zone; with two rows (two printings or
        // categories) an Undo could not put both back as they were, so none is offered
        if (twinIn(c, zoneOf(c))) inverses = null;
        if (inverses) { var inv = inverseFor(c, ch); if (inv) inverses.push(inv); else inverses = null; }
      });
      run(changes, inverses, window.MtgText ? window.MtgText.plural(list.length, "card") + " removed." : "Removed.");
    });
    moveSel.addEventListener("change", function () {
      var to = moveSel.value;
      moveSel.value = "";
      syncSelects();
      if (!to) return;
      var list = unique(picked().filter(function (c) { return (byCategory ? stackOf(c) : catOf(c)) !== to; }), byNameZone);
      if (!list.length) { status.className = "status"; status.textContent = "Those cards are already in " + to + "."; return; }
      var changes = [], inverses = [];
      list.forEach(function (c) {
        var ch = changeFor(c, "set_category", to);
        changes.push(ch);
        if (inverses) { var inv = inverseFor(c, ch); if (inv) inverses.push(inv); else inverses = null; }
      });
      run(changes, inverses, "Moved to " + to + ".");
    });
    // A finish is set on every copy of a card name (one change per name). The Undo puts each
    // name's old finish back, and is only offered when all its copies had the same one.
    finishSel.addEventListener("change", function () {
      var to = finishSel.value;
      finishSel.value = "";
      syncSelects();
      if (!to) return;
      // the gateway changes the finish of cards in the deck itself; maybeboard rows keep theirs
      var list = unique(picked().filter(function (c) { return zoneOf(c) === "main"; }), function (c) { return frontFace(c.getAttribute("data-card")); });
      if (!list.length) { status.className = "status"; status.textContent = "A finish can be set on cards in the deck itself, not on the maybeboard."; return; }
      var changes = [], inverses = [];
      list.forEach(function (c) {
        var name = c.getAttribute("data-card");
        changes.push({ action: "set_finish", card_name: name, finish: to });
        if (!inverses) return;
        var olds = {};
        $$(".c, .row", cards).forEach(function (o) {
          if (zoneOf(o) === "main" && frontFace(o.getAttribute("data-card")) === frontFace(name)) olds[(o.getAttribute("data-finish") || "Normal").toLowerCase()] = 1;
        });
        var keys = Object.keys(olds);
        if (keys.length === 1) inverses.push({ action: "set_finish", card_name: name, finish: keys[0] });
        else inverses = null;
      });
      run(changes, inverses, "Finish set to " + to + ".");
    });
  })();

  // -- own deck: drag cards between categories; the drops are saved in one go ------------------
  var moveCardFn = null;  // set below when this grouping takes drops
  if (own && deckId && canDrag) {
  var moves = {}; // relation id (or name) -> {card, name, from, to}
  var bar = document.createElement("div");
  bar.className = "movebar";
  bar.setAttribute("role", "region");
  bar.setAttribute("aria-label", "Pending category moves");
  var review = el("button", "btn-primary", "Save moves");
  review.type = "button";
  var undo = el("button", null, "Undo all");
  undo.type = "button";
  var count = el("span", "count", "");
  var status = el("p", "status", "");
  status.setAttribute("role", "status");
  bar.appendChild(review);
  bar.appendChild(undo);
  bar.appendChild(count);
  bar.appendChild(status);
  cards.parentNode.insertBefore(bar, cards.nextSibling);

  function refreshBar() {
    var n = Object.keys(moves).length;
    bar.classList.toggle("show", n > 0);
    count.textContent = n === 1 ? "1 card moved" : n + " cards moved";
    review.disabled = n === 0;
    if (!n) status.textContent = "";
  }
  function keyOf(card) { return card.getAttribute("data-rel") || (card.getAttribute("data-card") + "|" + zoneOf(card)); }
  moveCardFn = moveCard;
  function moveCard(card, target) {
    var from = card.closest(".stack");
    if (!target || target === from) return;
    var key = keyOf(card);
    var original = moves[key] ? moves[key].from : from.getAttribute("data-group");
    var to = target.getAttribute("data-group");
    $(".cards, .rows", target).appendChild(card);
    if (to === original) delete moves[key];
    else moves[key] = { card: card, name: card.getAttribute("data-card"), from: original, to: to };
    refreshBar();
  }
  // Undo all puts every pending card back where it was; nothing was sent yet
  undo.addEventListener("click", function () {
    Object.keys(moves).forEach(function (k) {
      var mv = moves[k];
      var st = stackByName(mv.from) || ensureStack(mv.from);
      if (st) $(".cards, .rows", st).appendChild(mv.card);
    });
    moves = {};
    refreshStacks();
    refreshBar();
  });
  // The member's own moves are saved to Archidekt at once (one proposal, applied with its
  // snapshot); the assistant never reaches this path. A side row names its zone so the move
  // targets the maybeboard row and not a copy of the same card in the deck.
  function moveChanges() {
    return Object.keys(moves).map(function (k) {
      var mv = moves[k];
      var ch = { action: "set_category", card_name: mv.name, category: mv.to };
      if (zoneOf(mv.card) === "side") ch.zone = "side";
      return ch;
    });
  }
  function moveInverse() {
    var out = [];
    var ok = Object.keys(moves).every(function (k) {
      var mv = moves[k];
      var zone = excluded.indexOf(mv.to) >= 0 ? "side" : "main";
      if (twinIn(mv.card, zone)) return false;
      var ch = { action: "set_category", card_name: mv.name, category: mv.from };
      if (zone === "side") ch.zone = "side";
      out.push(ch);
      return true;
    });
    return ok ? out : null;
  }
  review.addEventListener("click", function () {
    var changes = moveChanges();
    if (!changes.length) return;
    review.disabled = true;
    undo.disabled = true;
    status.className = "status";
    status.textContent = "Saving to Archidekt…";
    saveEdit(changes, moveInverse()).then(function () {
      moves = {};
      undo.disabled = false;
      status.textContent = "";
      refreshBar();
    }).catch(function (err) {
      review.disabled = false;
      undo.disabled = false;
      status.className = "status notice error";
      status.textContent = err && err.cancelled ? "" : "Could not save: " + ((err && err.message) || "no connection");
    });
  });

  // mouse drag (native drag and drop)
  $$(".c, .row", cards).forEach(function (c) { c.setAttribute("draggable", "true"); });
  cards.addEventListener("dragstart", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card) return;
    if (picking) { e.preventDefault(); return; }
    closeMenu();
    dragged = card;
    card.classList.add("dragging");
    try { e.dataTransfer.setData("text/plain", card.getAttribute("data-card")); e.dataTransfer.effectAllowed = "move"; } catch (err) { /* older browsers */ }
  });
  cards.addEventListener("dragover", function (e) {
    var st = e.target.closest(".stack");
    if (!dragged || !st) return;
    e.preventDefault();
    $$(".stack.dropping", cards).forEach(function (s) { if (s !== st) s.classList.remove("dropping"); });
    st.classList.add("dropping");
  });
  cards.addEventListener("dragleave", function (e) {
    var st = e.target.closest(".stack");
    if (st && !st.contains(e.relatedTarget)) st.classList.remove("dropping");
  });
  cards.addEventListener("drop", function (e) {
    var st = e.target.closest(".stack");
    if (!dragged || !st) return;
    e.preventDefault();
    st.classList.remove("dropping");
    moveCard(dragged, st);
  });
  cards.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    $$(".stack.dropping", cards).forEach(function (s) { s.classList.remove("dropping"); });
    setTimeout(function () { dragged = null; }, 0);  // swallow the click that follows a drop
  });
  } // droppable

  // touch: press and hold a card for 350 ms. Let go without moving and the card's menu opens;
  // slide it (on the category grouping of your own deck) and it moves onto another category.
  var hold = null, armed = null, touchCard = null, ghost = null, lastTarget = null, startX = 0, startY = 0;
  function armTouch(card) {
    armed = card;
    card.classList.add("held");
    if (navigator.vibrate) navigator.vibrate(20);
  }
  function startTouchDrag(t) {
    touchCard = armed;
    dragged = touchCard;
    touchCard.classList.add("dragging");
    ghost = touchCard.cloneNode(true);
    ghost.classList.remove("dragging", "held");
    ghost.style.cssText = "position:fixed;z-index:70;width:" + touchCard.offsetWidth + "px;pointer-events:none;opacity:.9;left:" + (t.clientX - touchCard.offsetWidth / 2) + "px;top:" + (t.clientY - 40) + "px;margin:0";
    document.body.appendChild(ghost);
  }
  cards.addEventListener("touchstart", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card || e.touches.length !== 1 || picking) return;
    var t = e.touches[0];
    startX = t.clientX; startY = t.clientY;
    clearTimeout(hold);
    hold = setTimeout(function () { armTouch(card); }, 350);
  }, { passive: true });
  cards.addEventListener("touchmove", function (e) {
    var t = e.touches[0];
    var moved = Math.abs(t.clientX - startX) > 8 || Math.abs(t.clientY - startY) > 8;
    if (!armed && !touchCard) { if (moved) clearTimeout(hold); return; }  // a plain scroll
    if (armed && !touchCard) {
      if (!moved || menu) return;
      if (!canDrag) { armed.classList.remove("held"); armed = null; return; }  // sliding elsewhere: let the page scroll
      startTouchDrag(t);
    }
    e.preventDefault();
    ghost.style.left = (t.clientX - touchCard.offsetWidth / 2) + "px";
    ghost.style.top = (t.clientY - 40) + "px";
    var under = document.elementFromPoint(t.clientX, t.clientY);
    var st = under ? under.closest(".stack") : null;
    if (lastTarget && lastTarget !== st) lastTarget.classList.remove("dropping");
    if (st) st.classList.add("dropping");
    lastTarget = st;
  }, { passive: false });
  function endTouch(e) {
    clearTimeout(hold);
    var wasArmed = armed;
    if (wasArmed) wasArmed.classList.remove("held");
    armed = null;
    if (touchCard) {
      if (lastTarget) { lastTarget.classList.remove("dropping"); if (moveCardFn) moveCardFn(touchCard, lastTarget); }
      touchCard.classList.remove("dragging", "held");
      if (ghost) ghost.remove();
      ghost = null; lastTarget = null; touchCard = null;
      setTimeout(function () { dragged = null; }, 0);
      return;
    }
    if (wasArmed && e.type === "touchend") {
      // held still: the card's menu, where the finger was
      if (e.cancelable) e.preventDefault();
      swallowClick = true;
      setTimeout(function () { swallowClick = false; }, 400);
      if (!(menu && menuCard === wasArmed)) {
        var t = e.changedTouches && e.changedTouches[0];
        var r = wasArmed.getBoundingClientRect();
        openMenu(wasArmed, t ? t.clientX : r.left + r.width / 2, t ? t.clientY : r.top + r.height / 2);
      }
    }
  }
  cards.addEventListener("touchend", endTouch);
  cards.addEventListener("touchcancel", endTouch);
  } // deck view

  // -- stack order: drag a stack's handle (mouse, touch or pen), or focus it and press the arrow
  // keys, to put the stacks in your own order. Archidekt keeps no stack order of its own that the
  // gateway could find, so the order is remembered in this browser, per deck and per grouping.
  if (cards && cards.classList.contains("deckview")) (function () {
    var key = "mtg-stack-order:" + cards.getAttribute("data-deck") + ":" + (cards.getAttribute("data-grouping") || "");
    function stacksNow() { return $$(".stack", cards); }
    function nameOf(st) { return st.getAttribute("data-group") || ""; }
    function save() {
      try { localStorage.setItem(key, JSON.stringify(stacksNow().map(nameOf))); } catch (e) { /* private window */ }
      showReset();
    }
    function saved() {
      try { var v = JSON.parse(localStorage.getItem(key) || "null"); return Array.isArray(v) ? v : null; } catch (e) { return null; }
    }
    var reset = null;
    function showReset() {
      if (!reset) {
        reset = el("button", "btn-ghost small stack-reset", "Reset stack order");
        reset.type = "button";
        reset.addEventListener("click", function () {
          try { localStorage.removeItem(key); } catch (e) { /* nothing kept */ }
          var later = stacksNow().filter(function (st) { return original.indexOf(st) < 0; });
          original.concat(later).forEach(function (st) { if (st.parentNode === cards) cards.appendChild(st); });
          reset.hidden = true;
          var g = $(".stackgrip", cards);
          if (g) g.focus();  // the button that had focus is gone
          announce("Stacks are back in the deck's own order.");
        });
        cards.parentNode.insertBefore(reset, cards.nextSibling);
      }
      reset.hidden = false;
    }
    var live = el("p", "sr-only");
    live.setAttribute("aria-live", "polite");
    cards.parentNode.insertBefore(live, cards);
    function announce(t) { live.textContent = t; }
    var original = stacksNow();
    var order = saved();
    if (order) {
      var rank = {};
      order.forEach(function (n, i) { rank[n] = i; });
      original.slice().sort(function (a, b) {
        var ra = nameOf(a) in rank ? rank[nameOf(a)] : 1e6 + original.indexOf(a);
        var rb = nameOf(b) in rank ? rank[nameOf(b)] : 1e6 + original.indexOf(b);
        return ra - rb;
      }).forEach(function (st) { cards.appendChild(st); });
      showReset();
    }
    function addHandle(st) {
      var head = $(".stackhead h2, .stackhead h4", st);
      if (!head || $(".stackgrip", head)) return;
      var grip = el("button", "stackgrip icon-only btn-ghost", "⠿");
      grip.type = "button";
      grip.setAttribute("aria-label", "Move the stack " + nameOf(st) + " (drag, or use the arrow keys)");
      grip.title = "Drag to reorder; arrow keys move it too";
      head.insertBefore(grip, head.firstChild);
      grip.addEventListener("keydown", function (e) {
        var list = stacksNow(), i = list.indexOf(st);
        if ((e.key === "ArrowUp" || e.key === "ArrowLeft") && i > 0) cards.insertBefore(st, list[i - 1]);
        else if ((e.key === "ArrowDown" || e.key === "ArrowRight") && i < list.length - 1) cards.insertBefore(list[i + 1], st);
        else return;
        e.preventDefault();
        grip.focus();
        save();
        announce(nameOf(st) + ": position " + (stacksNow().indexOf(st) + 1) + " of " + list.length);
      });
      grip.addEventListener("pointerdown", function (e) {
        if (e.button !== 0) return;
        e.preventDefault();
        grip.setPointerCapture(e.pointerId);
        st.classList.add("moving");
        var moved = false;
        function over(ev) {
          var under = document.elementFromPoint(ev.clientX, ev.clientY);
          var target = under && under.closest ? under.closest(".stack") : null;
          if (!target || target === st || target.parentNode !== cards) return;
          var r = target.getBoundingClientRect();
          // before the target when the pointer is in its first half (top half, or left half in a row)
          var horizontal = getComputedStyle(cards).display === "grid" && r.width < cards.clientWidth * 0.9;
          var first = horizontal ? ev.clientX < r.left + r.width / 2 : ev.clientY < r.top + r.height / 2;
          cards.insertBefore(st, first ? target : target.nextSibling);
          moved = true;
        }
        function end() {
          grip.removeEventListener("pointermove", over);
          grip.removeEventListener("pointerup", end);
          grip.removeEventListener("pointercancel", end);
          st.classList.remove("moving");
          if (moved) { save(); announce(nameOf(st) + " moved."); }
        }
        grip.addEventListener("pointermove", over);
        grip.addEventListener("pointerup", end);
        grip.addEventListener("pointercancel", end);
      });
    }
    original.forEach(addHandle);
    // a stack the page adds later (a card moved into a new category) gets a handle too
    new MutationObserver(function () { stacksNow().forEach(addHandle); }).observe(cards, { childList: true });
  })();

  // -- Archidekt social actions: like, bookmark, follow, comments ------------------------------------
  // Each is the person's own click: a confirmation chip appears first, then the gateway sends the
  // action to Archidekt under their linked session (/social/api). Nothing here is reachable by an
  // assistant.
  function socialRequest(method, url, body) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (d) { d.status = r.status; return d; }); });
  }
  function socialProblem(d) {
    if (d && (d.error === "not_linked" || d.error === "auth")) {
      var a = el("a", "", "Link your Archidekt account");
      a.href = "/account";
      var frag = document.createDocumentFragment();
      frag.appendChild(a);
      frag.appendChild(document.createTextNode(" to like, bookmark, follow or comment."));
      return frag;
    }
    return document.createTextNode((d && d.message) || "That did not work; try again.");
  }
  function confirmChip(btn, question, yesLabel, onYes) {
    var old = btn.parentNode.querySelector(".confirm");
    if (old) old.dismiss(); // one question at a time; the other button comes back
    var chip = el("span", "confirm");
    chip.setAttribute("role", "group");
    chip.appendChild(el("span", "", question)); // in a span, so a long name in it can wrap (.confirm > span)
    var yes = el("button", "btn-primary", yesLabel);
    yes.type = "button";
    var no = el("button", "", "No");
    no.type = "button";
    chip.appendChild(yes);
    chip.appendChild(no);
    btn.hidden = true;
    btn.parentNode.insertBefore(chip, btn.nextSibling);
    var restore = function () { chip.remove(); btn.hidden = false; btn.focus(); };
    chip.dismiss = function () { chip.remove(); btn.hidden = false; };
    no.addEventListener("click", restore);
    chip.addEventListener("keydown", function (e) { if (e.key === "Escape") restore(); });
    yes.addEventListener("click", function () { chip.remove(); btn.hidden = false; onYes(); });
    yes.focus();
  }
  var social = $(".banner .social");
  if (social) {
    var noteEl = null;
    var note = function (content) {
      if (noteEl) noteEl.remove();
      noteEl = el("p", "note");
      if (typeof content === "string") noteEl.textContent = content; else noteEl.appendChild(content);
      social.appendChild(noteEl);
    };
    var deck = social.getAttribute("data-deck");
    var setLabel = function (btn, text) { var sp = btn.querySelector("span"); if (sp) sp.textContent = text; };
    var busy = function (btn, on) { btn.disabled = on; };
    var likeBtn = $("[data-social=vote]", social);
    if (likeBtn) {
      likeBtn.addEventListener("click", function () {
        var liked = likeBtn.getAttribute("data-state") === "1";
        confirmChip(likeBtn, liked ? "Remove your like?" : "Like this deck on Archidekt?", liked ? "Remove" : "Like", function () {
          busy(likeBtn, true);
          socialRequest("POST", "/social/api/decks/" + encodeURIComponent(deck) + "/vote", { vote: liked ? "none" : "up" }).then(function (d) {
            busy(likeBtn, false);
            if (!d.ok) { note(socialProblem(d)); return; }
            likeBtn.setAttribute("data-state", String(d.vote));
            likeBtn.classList.toggle("on", d.vote === 1);
            likeBtn.setAttribute("aria-pressed", d.vote === 1 ? "true" : "false");
            $(".n", likeBtn).textContent = d.points;
            setLabel(likeBtn, d.vote === 1 ? "Liked" : "Like");
            if (noteEl) noteEl.remove();
          }).catch(function () { busy(likeBtn, false); note("No connection."); });
        });
      });
    }
    var markBtn = $("[data-social=bookmark]", social);
    if (markBtn) {
      markBtn.addEventListener("click", function () {
        var on = markBtn.getAttribute("data-state") === "1";
        confirmChip(markBtn, on ? "Remove the bookmark?" : "Bookmark this deck on Archidekt?", on ? "Remove" : "Bookmark", function () {
          busy(markBtn, true);
          socialRequest("POST", "/social/api/decks/" + encodeURIComponent(deck) + "/bookmark", { on: !on }).then(function (d) {
            busy(markBtn, false);
            if (!d.ok) { note(socialProblem(d)); return; }
            markBtn.setAttribute("data-state", d.bookmarked ? "1" : "0");
            markBtn.classList.toggle("on", d.bookmarked);
            markBtn.setAttribute("aria-pressed", d.bookmarked ? "true" : "false");
            setLabel(markBtn, d.bookmarked ? "Bookmarked" : "Bookmark");
            if (noteEl) noteEl.remove();
          }).catch(function () { busy(markBtn, false); note("No connection."); });
        });
      });
    }
  }
  // Follow buttons live in the deck banner and on user pages alike.
  $$("[data-social=follow]").forEach(function (btn) {
    var user = btn.getAttribute("data-user");
    var name = btn.getAttribute("data-name") || "this user";
    var holder = btn.closest(".social") || btn.parentNode;
    var say = function (content) {
      var old = holder.querySelector(".note");
      if (old) old.remove();
      var p = el("p", "note");
      if (typeof content === "string") p.textContent = content; else p.appendChild(content);
      holder.appendChild(p);
    };
    var paint = function (following) {
      btn.setAttribute("data-state", following ? "1" : "0");
      btn.classList.toggle("on", following);
      btn.setAttribute("aria-pressed", following ? "true" : "false");
      var sp = btn.querySelector("span");
      if (sp) sp.textContent = (following ? "Following " : "Follow ") + name;
    };
    socialRequest("GET", "/social/api/users/" + encodeURIComponent(user) + "/follow").then(function (d) {
      if (d.ok && d.self) { btn.hidden = true; return; }
      if (d.ok) paint(d.following);
    }).catch(function () {});
    btn.addEventListener("click", function () {
      var on = btn.getAttribute("data-state") === "1";
      confirmChip(btn, on ? "Stop following " + name + "?" : "Follow " + name + " on Archidekt?", on ? "Unfollow" : "Follow", function () {
        btn.disabled = true;
        socialRequest("POST", "/social/api/users/" + encodeURIComponent(user) + "/follow", { on: !on }).then(function (d) {
          btn.disabled = false;
          if (!d.ok) { say(socialProblem(d)); return; }
          paint(d.following);
          var old = holder.querySelector(".note");
          if (old) old.remove();
        }).catch(function () { btn.disabled = false; say("No connection."); });
      });
    });
  });
  // The comment thread loads when its panel comes into view; posting asks first.
  var commentsPanel = $("#comments");
  if (commentsPanel) {
    var thread = $(".thread", commentsPanel);
    var countEl = $("[data-count]", commentsPanel);
    var form = $("form.newcomment", commentsPanel);
    var textarea = $("textarea", form);
    var replyTo = $(".replyto", form);
    var parentId = null;
    var me = null;  // the member's Archidekt user id, from the thread load; own comments get Edit and Delete
    var deckUrl = "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments";
    var when = function (iso) {  // one date format across the site (feedback.js)
      if (window.MtgText) return window.MtgText.when(iso);
      var d = new Date(iso);
      return isNaN(d) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    };
    var render = function (c) {
      var box = el("article", "cmt");
      box.setAttribute("data-id", c.id);
      var who = el("div", "who");
      var b = el("b", "", c.owner.username || "someone");
      who.appendChild(b);
      who.appendChild(document.createTextNode(" · " + when(c.created_at) + (c.edited_at ? " · edited" : "")));
      var pts = el("span", "pts");
      var showPoints = function () { pts.textContent = c.points ? " · " + c.points + (c.points === 1 || c.points === -1 ? " point" : " points") : ""; };
      showPoints();
      who.appendChild(pts);
      box.appendChild(who);
      var p = el("p", "", c.text);
      box.appendChild(p);
      var acts = el("div", "acts");
      var reply = el("button", "btn-ghost", "Reply");
      reply.type = "button";
      reply.addEventListener("click", function () {
        parentId = c.id;
        replyTo.hidden = false;
        replyTo.textContent = "";
        replyTo.appendChild(document.createTextNode("Replying to " + (c.owner.username || "this comment") + " "));
        var cancel = el("button", "", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", function () { parentId = null; replyTo.hidden = true; });
        replyTo.appendChild(cancel);
        textarea.focus();
      });
      acts.appendChild(reply);
      // votes on other people's comments (Archidekt's comment vote), asked first like the deck's Like
      if (me !== null && c.id && !(c.owner && c.owner.id === me)) {
        var voteBtn = function (up) {
          var btn = el("button", "btn-ghost vote", up ? "▲ Up" : "▼ Down");
          btn.type = "button";
          var mine = function () { return c.user_vote === (up ? 1 : 2); };
          var label = function () {
            btn.setAttribute("aria-pressed", mine() ? "true" : "false");
            btn.setAttribute("aria-label", (mine() ? "Take back your " : "Vote ") + (up ? "up" : "down") + ": comment by " + (c.owner.username || "someone"));
          };
          label();
          btn.addEventListener("click", function () {
            var undoing = mine();
            confirmChip(btn, undoing ? "Take back your vote?" : (up ? "Vote this comment up on Archidekt?" : "Vote this comment down on Archidekt?"), undoing ? "Take back" : "Vote", function () {
              btn.disabled = true;
              socialRequest("POST", deckUrl + "/" + encodeURIComponent(c.id) + "/vote", { vote: undoing ? "none" : (up ? "up" : "down") }).then(function (d) {
                btn.disabled = false;
                if (!d.ok) { var n = el("p", "notice error"); n.appendChild(socialProblem(d)); acts.appendChild(n); setTimeout(function () { n.remove(); }, 8000); return; }
                c.user_vote = d.vote; c.points = d.points;
                showPoints();
                Array.prototype.forEach.call(acts.querySelectorAll("button.vote"), function (x) { if (x.relabel) x.relabel(); });
              }).catch(function () { btn.disabled = false; });
            });
          });
          btn.relabel = label;
          return btn;
        };
        acts.appendChild(voteBtn(true));
        acts.appendChild(voteBtn(false));
      }
      if (me !== null && c.owner && c.owner.id === me) {
        var edit = el("button", "btn-ghost", "Edit");
        edit.type = "button";
        edit.addEventListener("click", function () {
          if ($("form.editcomment", box)) return;
          var f = el("form", "editcomment");
          var ta = document.createElement("textarea");
          ta.value = c.text; ta.rows = 3; ta.maxLength = 2000; ta.setAttribute("aria-label", "Edit your comment");
          var save = el("button", "btn-primary", "Save");
          var cancel = el("button", "", "Cancel"); cancel.type = "button";
          var note = el("span", "muted small", "");
          f.appendChild(ta); f.appendChild(save); f.appendChild(cancel); f.appendChild(note);
          cancel.addEventListener("click", function () { f.remove(); p.hidden = false; });
          f.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var text = ta.value.trim();
            if (!text) { ta.focus(); return; }
            save.disabled = true; note.textContent = "Saving…";
            socialRequest("PATCH", deckUrl + "/" + encodeURIComponent(c.id), { text: text }).then(function (d) {
              if (!d.ok) { save.disabled = false; note.textContent = ""; note.appendChild(socialProblem(d)); return; }
              c.text = d.comment.text; p.textContent = c.text; p.hidden = false; f.remove();
              if (!/edited/.test(who.textContent)) who.appendChild(document.createTextNode(" · edited"));
            }).catch(function () { save.disabled = false; note.textContent = "Network error; nothing was changed."; });
          });
          p.hidden = true;
          box.insertBefore(f, acts);
          ta.focus();
        });
        acts.appendChild(edit);
        var del = el("button", "btn-ghost del", "Delete");
        del.type = "button";
        del.addEventListener("click", function () {
          if ($(".confirmbar", box)) return;
          var bar = el("div", "confirmbar notice warn");
          bar.setAttribute("role", "alertdialog");
          bar.appendChild(document.createTextNode("Delete this comment on Archidekt? "));
          var yes = el("button", "btn-danger", "Delete"); yes.type = "button";
          var no = el("button", "", "Keep"); no.type = "button";
          bar.appendChild(yes); bar.appendChild(no);
          no.addEventListener("click", function () { bar.remove(); });
          yes.addEventListener("click", function () {
            yes.disabled = true;
            socialRequest("DELETE", deckUrl + "/" + encodeURIComponent(c.id)).then(function (d) {
              if (!d.ok) { yes.disabled = false; bar.textContent = ""; bar.appendChild(socialProblem(d)); bar.appendChild(no); return; }
              box.remove();
              if (countEl) countEl.textContent = d.count === 1 ? "1 comment" : d.count + " comments";
              if (!$(".cmt", thread)) thread.appendChild(el("p", "muted", "No comments yet. Be the first."));
            }).catch(function () { yes.disabled = false; bar.textContent = "Network error; nothing was changed."; });
          });
          box.insertBefore(bar, acts.nextSibling);
          yes.focus();
        });
        acts.appendChild(del);
      }
      box.appendChild(acts);
      if (c.replies && c.replies.length) {
        var kids = el("div", "replies");
        c.replies.forEach(function (k) { kids.appendChild(render(k)); });
        box.appendChild(kids);
      }
      return box;
    };
    var loaded = false;
    var failed = function (message) {
      // say what happened and offer another try (a silent empty thread looked like "no comments")
      loaded = false;
      thread.textContent = "";
      var p = el("p", "notice error");
      p.setAttribute("role", "alert");
      p.appendChild(document.createTextNode(message + " "));
      var retry = el("button", "btn mini-inline", "Try again");
      retry.type = "button";
      retry.addEventListener("click", load);
      p.appendChild(retry);
      thread.appendChild(p);
    };
    var load = function () {
      if (loaded) return;
      loaded = true;
      thread.textContent = "";
      var loading = el("p", "muted", "Loading comments…");
      loading.setAttribute("role", "status");
      thread.appendChild(loading);
      socialRequest("GET", thread.getAttribute("data-src")).then(function (d) {
        if (!d.ok) { thread.textContent = ""; var p = el("p", "muted"); p.appendChild(socialProblem(d)); thread.appendChild(p); return; }
        thread.textContent = "";
        me = typeof d.me === "number" ? d.me : null;
        if (countEl) countEl.textContent = d.count === 1 ? "1 comment" : d.count + " comments";
        if (!d.comments.length) { thread.appendChild(el("p", "muted", "No comments yet. Be the first.")); return; }
        d.comments.forEach(function (c) { thread.appendChild(render(c)); });
        if (d.has_more) {
          var more = el("a", "muted small", "More comments on Archidekt");
          more.href = "https://archidekt.com/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck"));
          more.target = "_blank"; more.rel = "noopener noreferrer";
          thread.appendChild(more);
        }
      }).catch(function () { failed("The comments could not be loaded: no connection."); });
    };
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        if (entries.some(function (e) { return e.isIntersecting; })) { load(); io.disconnect(); }
      }, { rootMargin: "200px" });
      io.observe(commentsPanel);
    } else { load(); }
    $$("[data-social=comments]").forEach(function (a) { a.addEventListener("click", load); });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = textarea.value.trim();
      if (!text) { textarea.focus(); return; }
      var old = $(".confirmbar", form);
      if (old) old.remove();
      var bar = el("div", "confirmbar");
      bar.appendChild(document.createTextNode("Post this comment publicly on Archidekt?"));
      var yes = el("button", "btn-primary", "Post");
      yes.type = "button";
      var no = el("button", "", "Cancel");
      no.type = "button";
      bar.appendChild(yes); bar.appendChild(no);
      form.insertBefore(bar, form.querySelector("button[type=submit]"));
      no.addEventListener("click", function () { bar.remove(); textarea.focus(); });
      yes.addEventListener("click", function () {
        yes.disabled = true;
        socialRequest("POST", "/social/api/decks/" + encodeURIComponent(commentsPanel.getAttribute("data-deck")) + "/comments", { text: text, parent: parentId }).then(function (d) {
          bar.remove();
          if (!d.ok) { var p = el("p", "notice error"); p.appendChild(socialProblem(d)); form.insertBefore(p, form.firstChild); return; }
          var old = $(".notice", form); if (old) old.remove();
          var node = render(d.comment);
          var parentBox = parentId ? $("[data-id='" + parentId + "']", thread) : null;
          if (parentBox) {
            var kids = $(".replies", parentBox) || parentBox.appendChild(el("div", "replies"));
            kids.appendChild(node);
          } else {
            var empty = $("p.muted", thread); if (empty && /No comments yet/.test(empty.textContent)) empty.remove();
            thread.insertBefore(node, thread.firstChild);
          }
          textarea.value = ""; parentId = null; replyTo.hidden = true;
          if (countEl) { var n = (parseInt(countEl.textContent, 10) || 0) + 1; countEl.textContent = n === 1 ? "1 comment" : n + " comments"; }
        }).catch(function () {
          bar.remove();
          var old = $(".notice", form); if (old) old.remove();
          var p = el("p", "notice error", "The comment was not posted: no connection. Your text is still in the box.");
          p.setAttribute("role", "alert");
          form.insertBefore(p, form.firstChild);
        });
      });
    });
  }
})();
