/* Deck page, deck list, search and collection helpers. The pages work without this file (every
   control is a form); this only makes them feel like Archidekt:
   - the toolbar's View as / Group by / Sort by selects apply on change, the local filter narrows
     the cards as you type, and card-name inputs autocomplete through the gateway's own Scryfall
     search;
   - on touch screens a tap fans a stack out and a tap on a card opens a viewer with the card
     large and what can be done with it (grid view, stacks, text rows alike);
   - a right-click, a press and hold, or Shift+F10 on a card opens the card's own menu (open it,
     one more or one fewer copy, move to a category, remove, edit) in place of the browser's;
   - on the member's own deck the menu, the viewer, Select cards and a drag between categories
     save through the deck page's edit endpoint (one proposal applied at once, with a snapshot);
     the answer redraws the touched cards, the Legality chip and the Deck checks in place. One
     Undo and Redo history covers every one of those saves (the buttons, the toast's Undo, and
     the editor's keys: Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y, Cmd on a Mac). */
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
  // While something is dragged: how far to scroll the page this frame when the pointer or finger
  // rests near the window's top or bottom edge (faster the closer it is), so a far stack is reachable;
  // never past the ends of `box` (the cards), where there is nothing left to drop on. Null when the
  // pointer is not in the zone; 0 is a frame too short for a whole pixel. The speed is per second,
  // not per frame, so a 120 Hz phone or a busy one scrolls as fast as a 60 Hz screen does.
  var EDGE_SPEED = 1200, edgeAt = 0, edgeRest = 0;  // px/s at the very edge; last step's time; part pixel
  function edgeStep(y, box) {
    var zone = Math.min(96, window.innerHeight * 0.15), h = window.innerHeight;
    var d = y < zone ? -(zone - y) : y > h - zone ? y - (h - zone) : 0;
    if (d && box) {
      var r = box.getBoundingClientRect();
      if ((d < 0 && r.top >= zone) || (d > 0 && r.bottom <= h - zone)) d = 0;
    }
    var now = performance.now(), ms = edgeAt && now - edgeAt < 100 ? now - edgeAt : 1000 / 60;
    edgeAt = d ? now : 0;
    if (!d) { edgeRest = 0; return null; }
    var px = Math.max(-1, Math.min(1, d / zone)) * EDGE_SPEED * ms / 1000 + edgeRest;
    var step = px < 0 ? Math.ceil(px) : Math.floor(px);
    edgeRest = px - step;
    return step;
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
  var grouping = cards.getAttribute("data-grouping");
  var byCategory = grouping === "category";
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
  // every category of the row, in order (data-allcats when it has more than one)
  function catsOf(card) {
    var all = null;
    try { all = JSON.parse(card.getAttribute("data-allcats") || "null"); } catch (e) { all = null; }
    if (Array.isArray(all) && all.length) return all.filter(function (c) { return typeof c === "string" && c; });
    return catOf(card) ? [catOf(card)] : [];
  }
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
  /* changes: the proposal's changes; undo: the inverse changes (null when the change cannot be
     put back). opts.replay marks an Undo or Redo step, which the history itself records. Resolves
     with the endpoint's answer once applied and drawn; rejects with an Error whose message is
     shown, or with {cancelled: true} when the person said no to the confirmation. */
  function saveEdit(changes, undoChanges, opts) {
    opts = opts || {};
    if (saving) return Promise.reject(new Error("Another change is still saving; one moment."));
    saving = true;
    var body = opts.proposalId ? { proposal_id: opts.proposalId } : { changes: changes, confirmed: opts.confirmed === true };
    return api(editUrl, body).then(function (d) {
      if (d.ok && d.applied) {
        saving = false;
        applyAnswer(d, changes, undoChanges, opts.replay);
        return d;
      }
      if (d.ok && d.needs_confirm) {
        saving = false;
        return askConfirm(d.why, d.proposal_id).then(function (yes) {
          if (!yes) { var c = new Error("Nothing was changed."); c.cancelled = true; throw c; }
          return saveEdit(changes, undoChanges, { proposalId: d.proposal_id, replay: opts.replay });
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

  // -- saves in the background (T-159): the card's edits from its menu, viewer and drags --------
  // The edit is drawn on the page at once and Archidekt saves it behind, one save at a time in
  // the order they were made: a Saving note until it answers, then Saved with Undo (applyAnswer).
  // A save that does not go through puts the card back as it was and says why in a red note. A
  // card with an edit still waiting its turn keeps what it shows when an earlier answer is drawn
  // (drawRows), so nothing jumps back and forth.
  var bgQueue = Promise.resolve(), bgCount = 0, bgCards = [];
  var qtySteps = [];  // a card's quantity steps not yet saved (stepQty): {card, shown, saved, timer, queued, gone}
  function bgBusy() { return bgCount > 0 || qtySteps.length > 0; }
  function waits(card) {
    return bgCards.indexOf(card) >= 0 || qtySteps.some(function (s) { return s.card === card && s.timer; });
  }
  function idle() {
    // a save made elsewhere on the page (selected cards, Undo, Redo) finishes first
    return new Promise(function (resolve) {
      (function check() { if (saving || histBusy) setTimeout(check, 100); else resolve(); })();
    });
  }
  // the Saving note; one that already says this stays as it is (no flicker, no second
  // announcement). later: a save whose turn came, which leaves an error or a question up.
  function savingNote(text, later) {
    if (toastEl && $(".msg", toastEl).textContent === text && !toastEl.classList.contains("error")) return;
    if (later && toastEl && (toastEl.classList.contains("error") || toastEl.getAttribute("role") === "alertdialog")) return;
    toast(text, { sticky: true });
  }
  /* job.card: the card drawn changed; job.start(): {changes, inverse, saying} when its turn comes
     (null when nothing is left to send); job.saved(): after Archidekt took it; job.undo(): puts the
     card back and returns the note's end ("Cultivate is back in Ramp."); job.what: "Move" and so on. */
  function background(job) {
    bgCount++;
    bgCards.push(job.card);
    syncHist();
    var done = function () { bgCount--; syncHist(); };
    bgQueue = bgQueue.then(idle).then(function () {
      bgCards.splice(bgCards.indexOf(job.card), 1);
      var plan = job.start();
      if (!plan) return null;
      savingNote(plan.saying, true);
      return saveEdit(plan.changes, plan.inverse).then(function () { if (job.saved) job.saved(); }, function (err) {
        var back = job.undo();
        if (err && err.cancelled) { dismissToast(); return; }
        var why = ((err && err.message) || "no connection").replace(/\.$/, "");
        toast(job.what + " not saved: " + why + ". " + back, { error: true });
      });
    }).then(done, done);
  }
  // Where a card sits and how it is filed, to put it back there when its save does not go through
  // (its stack too, when the card was the last one in it and the stack went with it).
  var SPOT_ATTRS = ["data-cat", "data-allcats", "data-zone"];
  function spotOf(card) {
    var st = card.closest(".stack");
    return {
      list: card.parentNode, next: card.nextSibling, stack: st, stackNext: st && st.nextSibling,
      attrs: SPOT_ATTRS.map(function (a) { return card.getAttribute(a); }), side: card.classList.contains("side")
    };
  }
  function putBack(card, spot) {
    var st = spot.stack, list = spot.list;
    if (st && !st.isConnected) {
      var same = stackByName(st.getAttribute("data-group"));
      if (same) st = same;
      else cards.insertBefore(st, spot.stackNext && spot.stackNext.parentNode === cards ? spot.stackNext : null);
    }
    if (st) list = $(".cards, .rows", st);
    var focused = document.activeElement === card;
    if (list) list.insertBefore(card, spot.next && spot.next.parentNode === list ? spot.next : null);
    if (focused) card.focus();
    SPOT_ATTRS.forEach(function (a, i) { if (spot.attrs[i] === null) card.removeAttribute(a); else card.setAttribute(a, spot.attrs[i]); });
    card.classList.toggle("side", spot.side);
    refreshStacks();
  }
  // the keyboard stays on the page when the focused card leaves it: the next card of its stack
  function focusNear(stack) {
    var next = (stack && stack.isConnected && $(".c, .row", stack)) || $(".c, .row", cards);
    if (next) next.focus();
  }

  // -- drawing the answer in place: the touched cards, the stack totals, the chip and the panels --
  // front face -> [{card, home}]: elements taken off the page and the stack they were in, put
  // back there by an Undo (a card filed under no category has no category of its own to go by)
  var removedCards = {};
  function keepRemoved(card, home) {
    var face = frontFace(card.getAttribute("data-card"));
    (removedCards[face] = removedCards[face] || []).push({ card: card, home: home });
  }
  function stackByName(name) {
    return $$(".stack", cards).filter(function (st) { return st.getAttribute("data-group") === name; })[0] || null;
  }
  // a stack made on the page, drawn as the server draws one (deckpage.cards_html)
  function makeStack(name) {
    var st = el("section", "stack");
    st.setAttribute("data-group", name);
    var head = el("div", "stackhead");
    var h2 = el("h2");
    var title = el("span", "title", name);
    title.title = name;
    h2.appendChild(title);
    head.appendChild(h2);
    head.appendChild(el("div", "meta", "Qty: 0"));
    st.appendChild(head);
    var rows = cards.classList.contains("text") || cards.classList.contains("table") || cards.classList.contains("brewer");
    var list = rows ? el("ul", "plain rows") : el("div", "cards");
    var th = $(".rows > .th", cards);  // the Table view's column heads
    if (th) list.appendChild(th.cloneNode(true));
    st.appendChild(list);
    return st;
  }
  function ensureStack(name) {
    var st = stackByName(name);
    if (st || !byCategory) return st;
    st = makeStack(name);
    // the maybeboard sits last, as on Archidekt; a new category goes before it
    var side = excluded.map(stackByName).filter(Boolean)[0];
    if (side && excluded.indexOf(name) < 0) cards.insertBefore(st, side); else cards.appendChild(st);
    return st;
  }
  // the count shown for a card in the open menu or viewer, kept in step with the card's own
  var qtyLabels = [];  // {el, card, show}
  function setQty(card, qty) {
    card.setAttribute("data-qty", String(qty));
    var q = $(".qty, .q", card);
    if (q) q.textContent = String(qty);
    qtyLabels = qtyLabels.filter(function (l) {
      if (!l.el.isConnected) return false;
      if (l.card === card) l.show();
      return true;
    });
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
  // Archidekt's custom mana value: the deck's own override of a card's mana value (data-mv). The
  // Table view's MV column shows it, and grouped by Mana value the card moves to its stack.
  function setManaValue(card, mv) {
    if (mv === null || mv === undefined || mv === "") card.removeAttribute("data-mv");
    else card.setAttribute("data-mv", String(mv));
    var custom = card.getAttribute("data-mv"), printed = card.getAttribute("data-cmc");
    var now = custom !== null ? custom : printed;  // as the deck counts it (deck_stats.mana_value)
    var cell = $(".mv", card);
    if (cell) {
      cell.textContent = now === null ? "" : String(Number(now));
      if (custom !== null && printed !== null) cell.title = "Custom mana value; printed " + Number(printed);
      else cell.removeAttribute("title");
    }
    var from = stackOf(card);
    if (grouping !== "mv" || !from || from === "Land") return;  // a land is grouped as one whatever its value
    var to = now === null ? "No mana value" : Number(now) + " mana value";  // deckpage.mv_group
    if (to === from) return;
    var focused = document.activeElement === card;
    $(".cards, .rows", mvStack(to)).appendChild(card);
    if (focused) card.focus();
    refreshStacks();
  }
  // the Mana value grouping's stack, made in its place in the order when there is none
  function mvStack(name) {
    var st = stackByName(name);
    if (st) return st;
    var rank = function (n) { return n === "Land" ? 1e6 : n === "No mana value" ? 2e6 : parseFloat(n); };
    st = makeStack(name);
    var after = $$(".stack", cards).filter(function (o) { return rank(o.getAttribute("data-group")) > rank(name); })[0];
    cards.insertBefore(st, after || null);
    return st;
  }
  // The card menu's "Mana value…" form: a whole number from 0 to 20, or Clear to use the card's own
  function manaValueForm(card, m) {
    var name = card.getAttribute("data-card") || "";
    var cur = card.getAttribute("data-mv");
    m.textContent = "";
    m.appendChild(el("div", "head", name));
    var form = el("form", "mvform");
    form.noValidate = true;  // our own message, in the menu, rather than the browser bubble
    var lab = el("label", "", "Custom mana value");
    var input = el("input", "");
    input.type = "number"; input.min = "0"; input.max = "20"; input.step = "1"; input.inputMode = "numeric";
    input.id = "mv-input"; lab.htmlFor = "mv-input";
    input.value = cur === null ? "" : cur;
    var hint = el("p", "hint", "Archidekt's mana value override for this card, in this deck only.");
    var msg = el("p", "status", "");
    msg.setAttribute("role", "status");
    var save = el("button", "btn primary", "Save"); save.type = "submit";
    var clear = el("button", "btn", "Clear"); clear.type = "button";
    var cancel = el("button", "btn", "Cancel"); cancel.type = "button";
    var acts = el("div", "acts");
    acts.appendChild(save); if (cur !== null) acts.appendChild(clear); acts.appendChild(cancel);
    input.addEventListener("input", function () { msg.className = "status"; msg.textContent = ""; });
    form.appendChild(lab); form.appendChild(input); form.appendChild(hint); form.appendChild(acts); form.appendChild(msg);
    m.appendChild(form);
    // The form closes at once and the card shows the new value while Archidekt saves it (a save
    // in the background, as the menu's other edits are)
    var send = function (value) {
      var ch = { action: "set_mana_value", card_name: name, mana_value: value, zone: card.getAttribute("data-zone") || "main" };
      var inv = { action: "set_mana_value", card_name: name, mana_value: cur === null ? null : Number(cur), zone: ch.zone };
      var saying = "Saving the mana value of " + name + "…";
      setManaValue(card, value);
      closeMenu();
      savingNote(saying);
      background({
        card: card, what: "Mana value",
        start: function () { return { changes: [ch], inverse: [inv], saying: saying }; },
        undo: function () {
          setManaValue(card, cur);
          return name + " is back to " + (cur === null ? "its own mana value" : cur) + ".";
        }
      });
    };
    form.addEventListener("submit", function (e) {
      stop(e);
      var v = input.value.trim();
      if (!/^\d{1,2}$/.test(v) || Number(v) > 20) { msg.className = "status err"; msg.textContent = "Enter a whole number from 0 to 20."; input.focus(); return; }
      if (cur !== null && Number(cur) === Number(v)) { closeMenu(); return; }
      send(Number(v));
    });
    clear.addEventListener("click", function (e) { stop(e); send(null); });
    cancel.addEventListener("click", function (e) { stop(e); closeMenu(); });
    fit(m);
    input.focus();
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
      if (r.mana_value !== undefined) setManaValue(card, r.mana_value);
      var cat = (r.categories && r.categories[0]) || "";
      card.setAttribute("data-cat", cat);
      if (r.categories && r.categories.length > 1) card.setAttribute("data-allcats", JSON.stringify(r.categories));
      else card.removeAttribute("data-allcats");
      if (r.notes !== undefined) { if (r.notes) card.setAttribute("data-notes", r.notes); else card.removeAttribute("data-notes"); }
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
        if (waits(card)) return;  // an edit of it still to be saved: it shows that, and its answer follows
        if (r) { place(card, r); return; }
        keepRemoved(card, stackOf(card));
        card.remove();
      });
    });
    // rows with no card on the page: a card an Undo put back (its element was kept)
    left.forEach(function (r) {
      var kept = removedCards[frontFace(r.name)];
      var was = kept && kept.shift();
      if (!was) return;
      var card = was.card, home = (r.categories && r.categories[0]) || was.home;
      var st = (home && (ensureStack(home) || stackByName(home))) || stackByName(was.home) || $(".stack", cards);
      if (!st) return;
      $(".cards, .rows", st).appendChild(card);
      place(card, r);
    });
    refreshStacks();
  }
  function namesOf(changes) {
    var seen = Object.create(null);
    return changes.map(function (ch) { return frontFace(ch.card_name); }).filter(function (n) { if (seen[n]) return false; seen[n] = true; return true; });
  }
  // An Undo that puts a removed row back also puts back its color tag and custom mana value. The
  // gateway tags only a card already in the deck, and takes one of those per card per save, so
  // the add goes first and each tag change follows in its own save (at most three in all).
  function undoSteps(list) {
    var steps = [[]];
    list.forEach(function (ch) {
      if (ch.action !== "set_label" && ch.action !== "set_mana_value") { steps[0].push(ch); return; }
      var key = frontFace(ch.card_name) + (ch.zone === "side" ? " side" : "");
      for (var i = 1; i < steps.length; i++) {
        if (!steps[i].some(function (o) { return frontFace(o.card_name) + (o.zone === "side" ? " side" : "") === key; })) { steps[i].push(ch); return; }
      }
      steps.push([ch]);
    });
    return steps.filter(function (s) { return s.length; });
  }
  // -- Undo and Redo: one history for every save made on this page -----------------------------
  // Each save that can be put back is an entry {changes, inverse}. Undo sends the inverse (in the
  // steps undoSteps makes), Redo sends the changes again; both are saves of their own, with their
  // own snapshot under History. A save that cannot be put back ends the history before it.
  var past = [], future = [], histBusy = false;
  var undoBtnEl = null, redoBtnEl = null;
  function syncHist() {
    // an edit still saving in the background goes first; Undo and Redo wait for it
    if (undoBtnEl) undoBtnEl.disabled = histBusy || bgBusy() || !past.length;
    if (redoBtnEl) redoBtnEl.disabled = histBusy || bgBusy() || !future.length;
  }
  function record(changes, inverse) {
    future = [];
    if (inverse && inverse.length) {
      past.push({ changes: changes, inverse: inverse });
      if (past.length > 50) past.shift();
    } else past = [];
    syncHist();
  }
  function historyLink() {
    var hist = el("a", "", "History");
    hist.href = "/history?deck_id=" + enc(deckId);
    return hist;
  }
  function histButton(label, fn) {
    var b = el("button", "btn", label);
    b.type = "button";
    b.addEventListener("click", function () { if (histBusy || saving || bgBusy()) return; b.disabled = true; fn(); });
    return b;
  }
  function undoLast() {
    if (histBusy || saving || bgBusy() || !past.length) return;
    var entry = past.pop(), done = 0;
    histBusy = true;
    syncHist();
    undoSteps(entry.inverse).reduce(function (p, step) {
      return p.then(function () { return saveEdit(step, null, { replay: true }).then(function () { done++; }); });
    }, Promise.resolve()).then(function () {
      histBusy = false;
      future.push(entry);
      syncHist();
      toast("Undone. Snapshots are under History.", { actions: [histButton("Redo", redoLast), historyLink()] });
    }).catch(function (err) {
      histBusy = false;
      // nothing saved: the entry stays; part of it saved: the deck is between two states
      if (!done) past.push(entry); else { past = []; future = []; }
      syncHist();
      showError(err);
    });
  }
  function redoLast() {
    if (histBusy || saving || bgBusy() || !future.length) return;
    var entry = future.pop();
    histBusy = true;
    syncHist();
    saveEdit(entry.changes, null, { replay: true }).then(function () {
      histBusy = false;
      past.push(entry);
      syncHist();
      toast("Redone. Snapshot kept under History", { actions: [histButton("Undo", undoLast), historyLink()] });
    }).catch(function (err) {
      histBusy = false;
      future.push(entry);
      syncHist();
      showError(err);
    });
  }
  if (own && deckId) {
    // the editor's keys: Ctrl+Z, Ctrl+Shift+Z or Ctrl+Y (Cmd on a Mac), never while typing in a
    // field, behind the card menu, viewer or EDHPlay dialog, or while a question is up
    document.addEventListener("keydown", function (ev) {
      if (!(ev.ctrlKey || ev.metaKey) || ev.altKey) return;
      var t = ev.target, tag = t && t.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
      if (menu || document.documentElement.classList.contains("cardview-open") || $(".deck-toast[role=alertdialog], .edhplay-dialog")) return;
      var k = (ev.key || "").toLowerCase();
      if (k === "z" && !ev.shiftKey) { if (past.length) { ev.preventDefault(); undoLast(); } }
      else if ((k === "z" && ev.shiftKey) || (k === "y" && !ev.metaKey)) { if (future.length) { ev.preventDefault(); redoLast(); } }
    });
  }

  var refreshed = false;
  function applyAnswer(d, changes, undoChanges, replay) {
    var names = namesOf(changes);
    drawRows(d.rows, names);
    drawStats(d);
    if (menu && !menuCard.isConnected) closeMenu();  // a menu open on a card the answer took off
    if (replay) {
      // an Undo or Redo step: the history says what was done; a stale read is read once more
      if (d.stale) setTimeout(function () {
        api(editUrl, { refresh: true, names: names }).then(function (r) { if (r.ok) { drawRows(r.rows, names); drawStats(r); } }).catch(function () { /* the next view shows it */ });
      }, 1500);
      return;
    }
    record(changes, undoChanges);
    var actions = [];
    // the toast's Undo is the history's Undo: the same as the Undo button and Ctrl+Z
    if (past.length) actions.push(histButton("Undo", undoLast));
    actions.push(historyLink());
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
      // put the row back as it was: every one of its categories (in order, the primary first),
      // its printing (the deck proper only; a maybeboard add takes no pinned printing), finish,
      // color tag and custom mana value, not just name and category. Its notes cannot come back:
      // Archidekt has no way for the gateway to write them (confirmNotes warns first).
      inv = { action: "add", card_name: name, quantity: qty };
      var cats = catsOf(card);
      if (cats.length) inv.category = cats[0];
      if (cats.length > 1) inv.categories = cats;
      var pr = (card.getAttribute("data-set") || "").trim().split(/\s+/);
      if (zone === "main" && pr.length === 2 && pr[0] && pr[1]) { inv.set_code = pr[0].toLowerCase(); inv.collector_number = pr[1]; }
      var fin = (card.getAttribute("data-finish") || "").toLowerCase();
      if (fin === "foil" || fin === "etched") inv.finish = fin;
      var more = [inv];
      var tag = splitLabel(card.getAttribute("data-label") || "");
      if (tag.name) more.push({ action: "set_label", card_name: name, label: tag.name, color: tag.colour || undefined });
      var mv = card.getAttribute("data-mv");
      if (/^\d+$/.test(mv || "")) more.push({ action: "set_mana_value", card_name: name, mana_value: Number(mv) });
      if (zone === "side") more.forEach(function (m) { m.zone = "side"; });
      return more;
    } else if (ch.action === "set_category") {
      // a move files the card under that one category; a card filed under several could not
      // get all of them back by a move, so it has no Undo
      if (catsOf(card).length > 1) return null;
      inv = { action: "set_category", card_name: name, category: catOf(card) || stackOf(card) };
      // after the move the row sits in the zone its new category puts it in
      zone = excluded.indexOf(ch.category) >= 0 ? "side" : "main";
      if (twinIn(card, zone)) return null;
    } else return null;
    if (zone === "side") inv.zone = "side";
    return inv;
  }
  // Before a card with notes is removed: Archidekt keeps a card's notes on its row, and the
  // gateway has no way to write them, so neither an Undo nor anything else here brings them back.
  function confirmNotes(list) {
    var seen = Object.create(null);
    var noted = list.filter(function (c) {
      var n = c.getAttribute("data-card");
      if (!c.getAttribute("data-notes") || seen[n]) return false;
      seen[n] = true;
      return true;
    });
    if (!noted.length) return Promise.resolve(true);
    return new Promise(function (resolve) {
      var yes = el("button", "btn-danger", "Remove anyway");
      yes.type = "button";
      var no = el("button", "", "Cancel");
      no.type = "button";
      var done = function (v) { dismissToast(); resolve(v); };
      yes.addEventListener("click", function () { done(true); });
      no.addEventListener("click", function () { done(false); });
      var who = noted.length === 1 ? noted[0].getAttribute("data-card") + " has a card note" : noted.length + " of these cards have card notes";
      toast(who + " on Archidekt. Removing deletes the note for good: Undo puts the card back without it (the gateway cannot write notes).",
        { warn: true, dialog: true, sticky: true, actions: [yes, no], onDismiss: function () { resolve(false); } });
      no.focus();
    });
  }
  // Quantity steps (the menu's and the viewer's − and +): the count changes at once and a burst
  // of steps is one save of the count it ends on, sent once the presses pause (or the menu
  // closes); a step made while that save is out follows it in a save of its own. The last copy's
  // − removes the card.
  function copies(n) { return n === 1 ? "1 copy" : n + " copies"; }
  function stepOf(card) { return qtySteps.filter(function (s) { return s.card === card; })[0] || null; }
  function stepQty(card, delta) {
    var s = stepOf(card);
    var q = (s ? s.shown : qtyOf(card)) + delta;
    if (q <= 0) { removeCard(card); return; }
    if (!s) { s = { card: card, shown: q, saved: qtyOf(card), timer: 0, queued: false }; qtySteps.push(s); }
    s.shown = q;
    setQty(card, q);
    refreshStacks();
    savingNote("Saving " + card.getAttribute("data-card") + ": " + copies(q) + "…");
    syncHist();
    if (!s.queued) { clearTimeout(s.timer); s.timer = setTimeout(function () { sendSteps(s); }, 400); }
  }
  function flushSteps() { qtySteps.forEach(function (s) { if (s.timer) sendSteps(s); }); }
  function settleSteps(s) {
    if (s.timer || s.queued) return;
    var i = qtySteps.indexOf(s);
    if (i >= 0) qtySteps.splice(i, 1);
    syncHist();
  }
  function sendSteps(s) {
    clearTimeout(s.timer);
    s.timer = 0;
    s.queued = true;
    var card = s.card, name = card.getAttribute("data-card"), sent = 0;
    background({
      card: card, what: "Quantity",
      start: function () {
        s.queued = false;
        if (s.gone || s.shown === s.saved) { settleSteps(s); return null; }
        sent = s.shown;
        var ch = changeFor(card, "set_quantity", sent);
        var inv = { action: "set_quantity", card_name: name, quantity: s.saved };
        if (ch.zone) inv.zone = ch.zone;
        return { changes: [ch], inverse: [inv], saying: "Saving " + name + ": " + copies(sent) + "…" };
      },
      saved: function () {
        s.saved = sent;
        if (!s.gone) { setQty(card, s.shown); refreshStacks(); }
        settleSteps(s);
      },
      undo: function () {
        s.shown = s.saved;
        if (!s.gone) { setQty(card, s.saved); refreshStacks(); }
        settleSteps(s);
        return name + " is back to " + copies(s.saved) + ".";
      }
    });
  }
  // Remove from deck: the card leaves the page at once (after the question a card note asks) and
  // comes back in its place when the save does not go through.
  function removeCard(card) {
    var name = card.getAttribute("data-card");
    confirmNotes([card]).then(function (yes) {
      if (!yes) return;
      var s = stepOf(card);
      if (s) { clearTimeout(s.timer); s.timer = 0; s.gone = true; }
      var spot = spotOf(card), side = zoneOf(card) === "side";
      var ch = changeFor(card, "remove");
      var saying = "Removing " + name + "…";
      card.remove();
      refreshStacks();
      closeMenu();
      closeViewer();
      if (!document.activeElement || document.activeElement === document.body) focusNear(spot.stack);
      savingNote(saying);
      background({
        card: card, what: "Removal",
        start: function () {
          // the count to put back with an Undo is the one Archidekt has by now
          if (s) { setQty(card, s.saved); settleSteps(s); }
          var inv = inverseFor(card, ch);
          return { changes: [ch], inverse: inv ? [].concat(inv) : null, saying: saying };
        },
        // kept for an Undo, which puts this element back (drawRows)
        saved: function () { keepRemoved(card, spot.stack ? spot.stack.getAttribute("data-group") : ""); },
        undo: function () { putBack(card, spot); return name + " is back in " + (side ? "the " + sideCat : "the deck") + "."; }
      });
    });
  }
  // Move to (and a drop on another stack): the card is filed under its new category at once, in
  // that stack when the page is grouped by category, and goes back when the save does not go through.
  function moveTo(card, to, target) {
    var name = card.getAttribute("data-card");
    var from = byCategory ? stackOf(card) : catOf(card);
    var ch = changeFor(card, "set_category", to);
    var inv = inverseFor(card, ch);  // from where the card is now, before it moves
    var spot = spotOf(card);
    var saying = "Moving " + name + " to " + to + "…";
    // filed as Archidekt files it: a category left out of the deck is the maybeboard (drawRows)
    var zone = excluded.indexOf(to) >= 0 ? "side" : "main";
    card.setAttribute("data-cat", to);
    card.removeAttribute("data-allcats");
    card.setAttribute("data-zone", zone);
    card.classList.toggle("side", zone === "side" && card.classList.contains("row"));
    target = target || (byCategory ? ensureStack(to) : null);
    var focused = document.activeElement === card;
    if (target) $(".cards, .rows", target).appendChild(card);
    if (focused) card.focus();  // moving the element took the focus off it
    refreshStacks();
    savingNote(saying);
    background({
      card: card, what: "Move",
      start: function () { return { changes: [ch], inverse: inv ? [inv] : null, saying: saying }; },
      undo: function () { putBack(card, spot); return name + " is back in " + (from || "its category") + "."; }
    });
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
      qtyLabels.push({ el: n, card: card, show: relabel });
      minus.addEventListener("click", function () { stepQty(card, -1); });
      plus.addEventListener("click", function () { stepQty(card, 1); });
      ctl.appendChild(minus); ctl.appendChild(n); ctl.appendChild(plus);
      acts.push(ctl);
      var rm = el("button", "btn btn-danger", "Remove from deck");
      rm.type = "button";
      rm.addEventListener("click", function () { removeCard(card); });
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
    flushSteps();  // quantity steps made in it go to Archidekt now
    if (!back) return;
    if (card && card.isConnected) { card.focus(); return; }
    focusNear(stack);  // the card was removed
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
      // the menu stays open for more steps; each shows at once and saves in the background
      qtyLabels.push({ el: n, card: card, show: relabel });
      minus.addEventListener("click", function (e) { stop(e); stepQty(card, -1); });
      plus.addEventListener("click", function (e) { stop(e); stepQty(card, 1); });
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
        var b = menuItem(c.name, c.side ? "eye-off" : "tag", function () { closeMenu(); moveTo(card, c.name); }, c.current ? "on" : "");
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
      if (window.MtgDeckTag) m.appendChild(menuItem("Color tag…", "tag", function () { closeMenu(); window.MtgDeckTag(card); }));
      m.appendChild(menuItem("Mana value…", "edit", function () { manaValueForm(card, m); }));
      m.appendChild(menuItem("Remove from deck", "x", function () { removeCard(card); }, "danger"));
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
    // a finger still holding the card: Android's own long press must not open the menu under it,
    // or the slide that follows scrolls the page in place of moving the card (letting go opens it)
    if (armed === card || touchCard) return;
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
    var tagBtn = el("button", "btn", "Color tag…");
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
    tagColour.setAttribute("aria-label", "Tag color");
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
    // Undo and Redo for every save made on this page (the history above), with the editor's keys
    var hist = el("span", "histtools");
    hist.setAttribute("role", "group");
    hist.setAttribute("aria-label", "Undo and Redo");
    undoBtnEl = el("button", "btn-ghost small", "Undo");
    undoBtnEl.type = "button";
    undoBtnEl.title = "Undo the last change saved on this page (Ctrl+Z)";
    undoBtnEl.addEventListener("click", undoLast);
    redoBtnEl = el("button", "btn-ghost small", "Redo");
    redoBtnEl.type = "button";
    redoBtnEl.title = "Redo the change you undid (Ctrl+Shift+Z or Ctrl+Y)";
    redoBtnEl.addEventListener("click", redoLast);
    hist.appendChild(undoBtnEl);
    hist.appendChild(redoBtnEl);
    syncHist();
    bar.appendChild(start);
    bar.appendChild(hist);
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
      var seen = Object.create(null);
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
      var seen = Object.create(null);
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
      var seen = Object.create(null);
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
        if (inverses) { var inv = inverseFor(c, ch); inverses = inv ? inverses.concat(inv) : null; }
      });
      confirmNotes(list).then(function (yes) {
        if (yes) run(changes, inverses, window.MtgText ? window.MtgText.plural(list.length, "card") + " removed." : "Removed.");
      });
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

  // -- own deck: drag cards between categories; each drop is saved to Archidekt at once ---------
  // The same save as the card menu's "Move to" (one proposal applied with its snapshot, the
  // member's own hand edit; the assistant never reaches this path), with the page's Undo and
  // Redo. A side row names its zone so the move targets the maybeboard row and not a copy of
  // the same card in the deck.
  var moveCardFn = null;  // set below when this grouping takes drops
  if (own && deckId && canDrag) {
  moveCardFn = moveCard;
  function moveCard(card, target) {
    var from = card.closest(".stack");
    if (!target || !from || target === from) return;
    moveTo(card, target.getAttribute("data-group"), target);  // shown where it was dropped while it saves
  }

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
  // The copy under the finger moves by transform and the stack under it is looked up once a frame,
  // not on every touchmove; near the window's top or bottom edge the page scrolls on by itself.
  var hold = null, armed = null, touchCard = null, ghost = null, lastTarget = null, startX = 0, startY = 0;
  var touchX = 0, touchY = 0, ghostW = 0, touchFrame = 0;
  function armTouch(card) {
    armed = card;
    card.classList.add("held");
    if (navigator.vibrate) navigator.vibrate(20);
  }
  function startTouchDrag(t) {
    touchCard = armed;
    dragged = touchCard;
    touchCard.classList.add("dragging");
    ghostW = touchCard.offsetWidth;
    // the copy sits in a .deckview of its own (and a text row in its list) to keep the card's look
    var copy = touchCard.cloneNode(true);
    copy.classList.remove("dragging", "held");
    ghost = el("div", "deckview dragghost");
    ghost.setAttribute("aria-hidden", "true");
    ghost.style.width = ghostW + "px";
    if (copy.classList.contains("row")) {
      var list = el("ul", "plain rows");
      list.appendChild(copy);
      copy = list;
    }
    ghost.appendChild(copy);
    document.body.appendChild(ghost);
    touchX = t.clientX; touchY = t.clientY;
    touchTick();
  }
  function touchTick() {
    touchFrame = 0;
    if (!touchCard) return;
    var dy = edgeStep(touchY, cards);
    if (dy) window.scrollBy(0, dy);
    ghost.style.transform = "translate3d(" + Math.round(touchX - ghostW / 2) + "px," + Math.round(touchY - 40) + "px,0)";
    var under = document.elementFromPoint(touchX, touchY);
    var st = under ? under.closest(".stack") : null;
    if (st && !cards.contains(st)) st = null;
    if (st !== lastTarget) {
      if (lastTarget) lastTarget.classList.remove("dropping");
      if (st) st.classList.add("dropping");
      lastTarget = st;
    }
    if (dy !== null) touchFrame = requestAnimationFrame(touchTick);  // a resting finger keeps the page scrolling
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
    touchX = t.clientX; touchY = t.clientY;
    if (!touchFrame) touchFrame = requestAnimationFrame(touchTick);
  }, { passive: false });
  function endTouch(e) {
    clearTimeout(hold);
    var wasArmed = armed;
    if (wasArmed) wasArmed.classList.remove("held");
    armed = null;
    if (touchFrame) {
      // the last slide was not drawn yet: find the stack under the finger before dropping
      cancelAnimationFrame(touchFrame);
      touchFrame = 0;
      if (touchCard) { touchTick(); cancelAnimationFrame(touchFrame); touchFrame = 0; }
    }
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
  // gateway could find, so the order is remembered in this browser, per deck and per grouping:
  // on this device only (it is not a deck save, has no Undo, and the handle and Reset say so).
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
        reset = el("button", "btn-ghost small stack-reset", "Reset stack order (this device only)");
        reset.type = "button";
        reset.title = "Your stack order is kept in this browser on this device only, not on Archidekt; " +
          "this puts the deck's own order back";
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
      var rank = Object.create(null);
      order.forEach(function (n, i) { rank[n] = i; });
      original.slice().sort(function (a, b) {
        var ra = nameOf(a) in rank ? rank[nameOf(a)] : 1e6 + original.indexOf(a);
        var rb = nameOf(b) in rank ? rank[nameOf(b)] : 1e6 + original.indexOf(b);
        return ra - rb;
      }).forEach(function (st) { cards.appendChild(st); });
      showReset();
    }
    function addHandle(st) {
      var head = $(".stackhead h2", st);
      if (!head || $(".stackgrip", head)) return;
      var grip = el("button", "stackgrip icon-only btn-ghost", "⠿");
      grip.type = "button";
      grip.setAttribute("aria-label", "Move the stack " + nameOf(st) + " (drag, or use the arrow keys; " +
        "the order is kept on this device only)");
      grip.title = "Drag to reorder (kept on this device only); arrow keys move it too";
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
        if (e.button !== 0 || drag) return;
        e.preventDefault();
        try { grip.setPointerCapture(e.pointerId); } catch (err) { /* the pointer is gone already */ }
        drag = { st: st, grip: grip, id: e.pointerId, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, on: false, spot: null,
          finger: e.pointerType !== "mouse" };
        grip.addEventListener("pointermove", dragMove);
        grip.addEventListener("pointerup", dragUp);
        grip.addEventListener("pointercancel", dragCancel);
        grip.addEventListener("lostpointercapture", dragCancel);
      });
    }
    // A pointer drag leaves every stack where it is until it is let go: a bar shows where the
    // stack will land and a label with its name follows the pointer. Nothing reflows under the
    // pointer meanwhile (the text and Brewer views' columns would reshuffle on every step), the
    // landing spot is looked up once a frame, and near the window's top or bottom edge the page
    // scrolls on by itself. Escape puts the stack back where it was.
    var drag = null, dropBar = null, dragTag = null, dragFrame = 0;
    function dragMove(ev) {
      if (!drag || ev.pointerId !== drag.id) return;
      drag.x = ev.clientX; drag.y = ev.clientY;
      if (!drag.on) {
        if (Math.abs(drag.x - drag.x0) + Math.abs(drag.y - drag.y0) < 6) return;  // a click, not a drag yet
        drag.on = true;
        var r = drag.st.getBoundingClientRect();
        drag.horizontal = getComputedStyle(cards).display === "grid" && r.width < cards.clientWidth * 0.9;
        drag.st.classList.add("moving");
        cards.classList.add("sorting");
        dropBar = el("div", "stackdrop");
        dropBar.setAttribute("aria-hidden", "true");
        dragTag = el("div", "stacktag", nameOf(drag.st));
        dragTag.setAttribute("aria-hidden", "true");
        document.body.appendChild(dropBar);
        document.body.appendChild(dragTag);
        drag.tagW = dragTag.offsetWidth;
      }
      if (!dragFrame) dragFrame = requestAnimationFrame(dragTick);
    }
    function dragTick() {
      dragFrame = 0;
      if (!drag || !drag.on) return;
      var dy = edgeStep(drag.y, cards);
      if (dy) window.scrollBy(0, dy);
      // the name sits beside a mouse pointer, and above a finger, where the hand does not cover it
      var tx = Math.max(8, Math.min(drag.x + 16, window.innerWidth - drag.tagW - 8));
      dragTag.style.transform = "translate3d(" + Math.round(tx) + "px," + Math.round(drag.y + (drag.finger ? -56 : 12)) + "px,0)";
      findSpot();
      drawBar();
      if (dy !== null) dragFrame = requestAnimationFrame(dragTick);  // a resting pointer keeps the page scrolling
    }
    function findSpot() {
      // the stack under the pointer, or the nearest one when it is in a gap, in the page margin or
      // over a bar fixed to the window (the phone's bottom bar): measured, so nothing covers it
      var target = null, r = null, best = Infinity;
      stacksNow().forEach(function (s) {
        var b = s.getBoundingClientRect();
        var dx = Math.max(b.left - drag.x, 0, drag.x - b.right), dy = Math.max(b.top - drag.y, 0, drag.y - b.bottom);
        if (dx * dx + dy * dy < best) { best = dx * dx + dy * dy; target = s; r = b; }
      });
      if (!target) return;
      // before the target in its left half (a row of stacks), or above its header line (a tall
      // stack's middle may be far off screen; its header is where the eye is)
      var first = drag.horizontal ? drag.x < r.left + r.width / 2 : drag.y < r.top + Math.min(r.height / 2, 60);
      var ref = first ? target : target.nextElementSibling;
      drag.spot = target === drag.st || ref === drag.st || ref === drag.st.nextElementSibling
        ? null : { target: target, ref: ref, first: first };
    }
    function drawBar() {
      // a 1 px box placed and stretched by transform alone: drawing it never lays the page out
      var s = dropBar.style;
      if (!drag.spot) { s.opacity = "0"; return; }
      var r = drag.spot.target.getBoundingClientRect(), x, y, w, h;
      if (drag.horizontal) {
        x = (drag.spot.first ? r.left - 10 : r.right + 6); y = r.top; w = 4; h = r.height;
      } else {
        x = r.left; y = (drag.spot.first ? r.top - 8 : r.bottom + 4); w = r.width; h = 4;
      }
      s.opacity = "1";
      s.transform = "translate3d(" + Math.round(x) + "px," + Math.round(y) + "px,0) scale(" + Math.round(w) + "," + Math.round(h) + ")";
    }
    function dragEnd(commit) {
      var d = drag;
      if (!d) return;
      drag = null;
      if (dragFrame) { cancelAnimationFrame(dragFrame); dragFrame = 0; }
      d.grip.removeEventListener("pointermove", dragMove);
      d.grip.removeEventListener("pointerup", dragUp);
      d.grip.removeEventListener("pointercancel", dragCancel);
      d.grip.removeEventListener("lostpointercapture", dragCancel);
      try { d.grip.releasePointerCapture(d.id); } catch (err) { /* released already */ }
      if (!d.on) return;
      d.st.classList.remove("moving");
      cards.classList.remove("sorting");
      if (dropBar) dropBar.remove();
      if (dragTag) dragTag.remove();
      dropBar = dragTag = null;
      if (commit && d.spot) {
        cards.insertBefore(d.st, d.spot.ref);
        save();
        var list = stacksNow();
        announce(nameOf(d.st) + ": position " + (list.indexOf(d.st) + 1) + " of " + list.length);
      } else if (!commit) {
        announce(nameOf(d.st) + " stays where it was.");
      }
    }
    function dragUp(ev) {
      if (!drag || ev.pointerId !== drag.id) return;
      if (drag.on) { drag.x = ev.clientX; drag.y = ev.clientY; findSpot(); }  // where it was let go
      dragEnd(true);
    }
    function dragCancel(ev) { if (drag && ev.pointerId === drag.id) dragEnd(false); }
    document.addEventListener("keydown", function (e) {
      if (drag && drag.on && e.key === "Escape") { e.preventDefault(); dragEnd(false); }
    });
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

/* Play on EDHPlay (R-154, T-161): the press opens a small dialog with the three steps of an
   import on EDHPlay; its "Copy and launch EDHPlay" link copies the whole deck in EDHPlay's import
   format (the data-list text, made by export_formats.to_edhplay) and opens edhplay.com in a new
   tab (in the Android app, the phone's browser). The copy is made inside that press, the way the
   app's WebView allows it. Sideboard cards left out to fit EDHPlay's 15-card limit are named in
   the dialog and in the note that follows. Without script the button is a plain link to EDHPlay. */
(function () {
  "use strict";
  function copyNow(text) {
    // execCommand first: it works inside the press in the app's WebView, where the async
    // clipboard API may be refused; the async API is the fallback for browsers that dropped it.
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    area.remove();
    if (ok) return Promise.resolve(true);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return false; });
    }
    return Promise.resolve(false);
  }
  // 0.7.25 and 0.7.26 kept a first-time tip's "seen" mark here; the dialog says it all now
  try { localStorage.removeItem("mtg.edhplay.tip"); } catch (e) { /* private window */ }
  function make(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }
  function cutLine(cut, leftOut) {
    return (cut === 1 ? "1 sideboard card is" : cut + " sideboard cards are")
      + " left out to fit EDHPlay's 15-card limit: " + leftOut + ".";
  }
  // lines: the note's text, one per line; kind: "", "warn" or "error" (those stay until closed)
  function note(lines, kind) {
    var old = document.querySelector(".edhplay-toast");
    if (old) old.remove();
    var t = make("div", "toast deck-toast edhplay-toast" + (kind ? " " + kind : ""));
    t.setAttribute("role", kind === "error" ? "alert" : "status");
    var msg = make("div", "msg");
    lines.forEach(function (line) { msg.appendChild(make("span", "", line)); });
    t.appendChild(msg);
    var x = make("button", "close icon-only", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", function () { t.remove(); });
    t.appendChild(x);
    document.body.appendChild(t);
    if (!kind) setTimeout(function () { t.remove(); }, 8000);
  }
  var inApp = / MTGAssistant\//.test(navigator.userAgent || "");
  var dialog = null, back = null;
  function close() {
    if (!dialog) return;
    dialog.remove();
    dialog = null;
    document.removeEventListener("keydown", onKey, true);
    if (back && back.isConnected) back.focus();
    back = null;
  }
  function onKey(e) {
    if (!dialog) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (e.key === "Tab") {  // focus stays inside the dialog
      var f = dialog.querySelectorAll("a[href],button:not([disabled])");
      var first = f[0], last = f[f.length - 1];
      if (!dialog.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }
  function open(link) {
    close();
    back = link;
    var list = link.getAttribute("data-list") || "";
    var leftOut = link.getAttribute("data-left-out") || "";
    var cut = parseInt(link.getAttribute("data-left-count") || "0", 10) || 0;
    dialog = make("div", "picker edhplay-dialog");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "edhplay-title");
    dialog.addEventListener("click", function (e) { if (e.target === dialog) close(); });
    var box = make("div", "pickbox");
    var head = make("div", "head");
    var h = make("h2", "", "Copy and launch EDHPlay");
    h.id = "edhplay-title";
    var x = make("button", "close icon-only", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.title = "Close";
    x.addEventListener("click", close);
    head.appendChild(h); head.appendChild(x);
    box.appendChild(head);
    var steps = make("ol", "steps");
    [
      "Copy and launch copies your deck and opens EDHPlay " + (inApp ? "in your phone's browser." : "in a new tab."),
      "On EDHPlay, choose Create Deck, then Import.",
      "Paste, then press Import & Replace (it sets your commander)."
    ].forEach(function (s) { steps.appendChild(make("li", "", s)); });
    box.appendChild(steps);
    if (cut) box.appendChild(make("p", "notice warn", cutLine(cut, leftOut)));
    box.appendChild(make("p", "muted small", "The maybeboard and tokens aren't sent."));
    var acts = make("div", "acts");
    var go = make("a", "btn btn-primary", "Copy and launch EDHPlay");
    go.href = link.href;
    go.target = "_blank";
    go.rel = "noreferrer noopener";
    var cancel = make("button", "btn", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", close);
    acts.appendChild(go); acts.appendChild(cancel);
    box.appendChild(acts);
    dialog.appendChild(box);
    document.body.appendChild(dialog);
    document.addEventListener("keydown", onKey, true);
    go.addEventListener("click", function () {
      // the copy happens inside this press; the link itself opens EDHPlay (nothing is prevented)
      var copied = copyNow(list);
      close();
      copied.then(function (ok) {
        if (!ok) {
          note(["The deck list couldn't be copied here. Use More, then Export deck, to copy it."], "error");
          return;
        }
        var lines = ["Deck list copied. On EDHPlay: Create Deck, then Import, paste and Import & Replace."];
        if (cut) lines.push(cutLine(cut, leftOut));
        note(lines, cut ? "warn" : "");
      });
    });
    go.focus();
  }
  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("a[data-edhplay]");
    // a Ctrl, Cmd or Shift press opens EDHPlay straight away, as a plain link
    if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    e.preventDefault();
    open(link);
  });
})();
