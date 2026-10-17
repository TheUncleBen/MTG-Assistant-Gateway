/* Deck page, deck list, search and collection helpers. The pages work without this file (every
   control is a form); this only makes them feel like Archidekt:
   - the toolbar's View as / Group by / Sort by selects apply on change, the local filter narrows
     the cards as you type, and card-name inputs autocomplete through the gateway's own Scryfall
     search;
   - on touch screens a tap fans a stack out and a tap on a card opens a viewer with the card
     large and what can be done with it (grid view, stacks, text rows alike);
   - a right-click, a press and hold, or Shift+F10 on a card opens the card's own menu (open it,
     one more or one fewer copy, move to a category, change its printing through the shared
     picker static/printpick.js, remove, edit) in place of the browser's;
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
    // the cards and each stack's cards are collected once, and again only after the view changed
    // (a save redraws cards in place), not on every keystroke (AUDIT F7)
    var items = null, stacks = null;
    var collect = function () {
      items = $$("[data-name]", cards);
      stacks = $$(".stack", cards).map(function (st) { return { el: st, cards: $$("[data-name]", st) }; });
    };
    if (window.MutationObserver) {
      new MutationObserver(function () { items = null; }).observe(cards, { childList: true, subtree: true });
    }
    var apply = function () {
      if (!items || !window.MutationObserver) collect();
      var needle = q.value.trim().toLowerCase();
      items.forEach(function (el) {
        el.style.display = !needle || el.getAttribute("data-name").indexOf(needle) >= 0 ? "" : "none";
      });
      stacks.forEach(function (st) {
        st.el.style.display = st.cards.some(function (el) { return el.style.display !== "none"; }) ? "" : "none";
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
        applyAnswer(d, changes, undoChanges, opts.replay, opts.note);
        return d;
      }
      if (d.ok && d.needs_confirm) {
        saving = false;
        return askConfirm(d.why, d.proposal_id).then(function (yes) {
          if (!yes) { var c = new Error("Nothing was changed."); c.cancelled = true; throw c; }
          return saveEdit(changes, undoChanges, { proposalId: d.proposal_id, replay: opts.replay, note: opts.note });
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

  // -- saves in the background: the card's edits from its menu, viewer and drags --------
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
  // card, what: whose note it is, so a save that finds nothing to send can take its own note
  // down (M6) and never another card's, an error or a question.
  var savingFor = null;  // {el, card, what}
  function savingNote(text, later, card, what) {
    if (toastEl && $(".msg", toastEl).textContent === text && !toastEl.classList.contains("error")) {
      savingFor = { el: toastEl, card: card, what: what };
      return;
    }
    if (later && toastEl && (toastEl.classList.contains("error") || toastEl.getAttribute("role") === "alertdialog")) return;
    savingFor = { el: toast(text, { sticky: true }), card: card, what: what };
  }
  function dropSavingNote(card, what) {
    if (savingFor && toastEl && savingFor.el === toastEl && savingFor.card === card && savingFor.what === what) dismissToast();
  }
  // M7: what Archidekt last confirmed for a card, per kind of edit ("Move", "Mana value"), while
  // edits of that kind wait or save. A refused save puts the card back to that state, not to the
  // unsaved one the edit was made over, and drops the card's later edits of that kind still
  // waiting their turn (made over a state Archidekt never took).
  var confirmed = [];  // {card, what, state, gen, open, queued}
  function confirmedOf(card, what) {
    return confirmed.filter(function (o) { return o.card === card && o.what === what; })[0] || null;
  }
  function holdConfirmed(card, what, current) {
    var c = confirmedOf(card, what);
    if (!c) { c = { card: card, what: what, state: current(), gen: 0, open: 0, queued: 0 }; confirmed.push(c); }
    c.open++;
    return c;
  }
  function releaseConfirmed(c) {
    if (--c.open > 0) return;
    var i = confirmed.indexOf(c);
    if (i >= 0) confirmed.splice(i, 1);
  }
  // N5 (review question 1, provisional): a refused edit's note says when later edits of the same
  // kind were dropped with it. c.queued counts this kind's own edits still to finish (a Removal
  // holds the card's Move state too, but is always sent); the refused one is among them here.
  function droppedNote(c, one, many) {
    var later = c.queued - 1;
    return later > 1 ? " " + many : later === 1 ? " " + one : "";
  }
  /* job.card: the card drawn changed; job.start(): {changes, inverse, saying} when its turn comes
     (null when nothing is left to send); job.saved(): after Archidekt took it; job.undo(): puts the
     card back and returns the note's end ("Cultivate is back in Ramp."); job.what: "Move" and so on. */
  function background(job) {
    bgCount++;
    bgCards.push(job.card);
    syncHist();
    var done = function () { bgCount--; if (job.done) job.done(); syncHist(); };
    bgQueue = bgQueue.then(idle).then(function () {
      bgCards.splice(bgCards.indexOf(job.card), 1);
      var plan = job.start();
      // nothing left to send (+ then − before the save went): its Saving note goes too (M6)
      if (!plan) { dropSavingNote(job.card, job.what); return null; }
      savingNote(plan.saying, true, job.card, job.what);
      return saveEdit(plan.changes, plan.inverse, { note: plan.note }).then(function () { if (job.saved) job.saved(); }, function (err) {
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
  function putBack(card, spot, fileOnly) {
    if (fileOnly) {
      SPOT_ATTRS.forEach(function (a, i) { if (spot.attrs[i] === null) card.removeAttribute(a); else card.setAttribute(a, spot.attrs[i]); });
      card.classList.toggle("side", spot.side);
      return;
    }
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
  // The card menu's "Mana value…" form: a whole number from 0 to 15, or Clear to use the card's own
  function manaValueForm(card, m) {
    var name = card.getAttribute("data-card") || "";
    var cur = card.getAttribute("data-mv");
    m.textContent = "";
    m.appendChild(el("div", "head", name));
    var form = el("form", "mvform");
    form.noValidate = true;  // our own message, in the menu, rather than the browser bubble
    var lab = el("label", "", "Custom mana value");
    var input = el("input", "");
    input.type = "number"; input.min = "0"; input.max = "15"; input.step = "1"; input.inputMode = "numeric";
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
      var held = holdConfirmed(card, "Mana value", function () { return cur; }), gen = held.gen;
      held.queued++;
      setManaValue(card, value);
      closeMenu();
      savingNote(saying, false, card, "Mana value");
      background({
        card: card, what: "Mana value",
        // an earlier one of these was refused: this one was made over a value Archidekt never had
        start: function () { return held.gen === gen ? { changes: [ch], inverse: [inv], saying: saying } : null; },
        saved: function () { held.state = value === null ? null : String(value); },
        undo: function () {
          held.gen++;
          var back = held.state;
          setManaValue(card, back);
          return name + " is back to " + (back === null ? "its own mana value" : back) + "." +
            droppedNote(held, "Your later mana value for it was not sent.", "Your later mana values for it were not sent.");
        },
        done: function () { held.queued--; releaseConfirmed(held); }
      });
    };
    form.addEventListener("submit", function (e) {
      stop(e);
      var v = input.value.trim();
      if (!/^\d{1,2}$/.test(v) || Number(v) > 15) { msg.className = "status err"; msg.textContent = "Enter a whole number from 0 to 15."; input.focus(); return; }
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
      if (r.set_code) showPrinting(card, { set: String(r.set_code).toUpperCase() + " " + (r.collector_number || ""), img: r.image || "", price: r.price });
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
      toast("Redone. Snapshot kept under History.", { actions: [histButton("Undo", undoLast), historyLink()] });
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
      if (menu || document.documentElement.classList.contains("cardview-open") || $(".deck-toast[role=alertdialog], .edhplay-dialog, .printpick.open")) return;
      var k = (ev.key || "").toLowerCase();
      if (k === "z" && !ev.shiftKey) { if (past.length) { ev.preventDefault(); undoLast(); } }
      else if ((k === "z" && ev.shiftKey) || (k === "y" && !ev.metaKey)) { if (future.length) { ev.preventDefault(); redoLast(); } }
    });
  }

  var refreshed = false;
  // note: what the Saved message says in place of the plain "Saved." (a printing change says
  // which printing, and when the finish had to change with it)
  function applyAnswer(d, changes, undoChanges, replay, note) {
    var saved = note || "Saved. Snapshot kept under History.";
    var names = namesOf(changes);
    drawRows(d.rows, names);
    drawStats(d);
    if (menu && !menuCard.isConnected) closeMenu();  // a menu open on a card the answer took off
    if (replay) {
      // an Undo or Redo step: the history says what was done; a stale read is read once more
      if (d.stale) setTimeout(function () {
        api(editUrl, { refresh: true, names: names }).then(function (r) { if (r.ok) { drawRows(r.rows, names); drawStats(r); if (menu && !menuCard.isConnected) closeMenu(); } }).catch(function () { /* the next view shows it */ });
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
          if (r.ok) { drawRows(r.rows, names); drawStats(r); if (menu && !menuCard.isConnected) closeMenu(); }
          toast(saved, { actions: actions });
        }).catch(function () { toast(saved, { actions: actions }); });
      }, 1500);
      return;
    }
    toast(saved, { actions: actions });
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
      // color tag, custom mana value and companion and flip flags, not just name and category. Its notes cannot come back:
      // Archidekt has no way for the gateway to write them (confirmNotes warns first).
      inv = { action: "add", card_name: name, quantity: qty };
      var cats = catsOf(card);
      if (cats.length) inv.category = cats[0];
      if (cats.length > 1) inv.categories = cats;
      var pr = (card.getAttribute("data-set") || "").trim().split(/\s+/);
      if (zone === "main" && pr.length === 2 && pr[0] && pr[1]) { inv.set_code = pr[0].toLowerCase(); inv.collector_number = pr[1]; }
      var fin = (card.getAttribute("data-finish") || "").toLowerCase();
      if (fin === "foil" || fin === "etched") inv.finish = fin;
      // a companion, or a card set to show its back, comes back as one
      if (card.hasAttribute("data-companion")) inv.companion = true;
      if (card.hasAttribute("data-flipped")) inv.flipped_default = true;
      var more = [inv];
      var tag = splitLabel(card.getAttribute("data-label") || "");
      if (tag.name) more.push({ action: "set_label", card_name: name, label: tag.name, color: tag.colour || undefined });
      var mv = card.getAttribute("data-mv");
      if (/^\d+$/.test(mv || "")) more.push({ action: "set_mana_value", card_name: name, mana_value: Number(mv) });
      if (zone === "side") more.forEach(function (m) { m.zone = "side"; });
      return more;
    } else if (ch.action === "set_category") {
      // a move files the card under that one category; its Undo files it back under every
      // category it had (in order, the primary first), as the Undo of a removal does
      var was = catsOf(card);
      inv = { action: "set_category", card_name: name, category: was[0] || stackOf(card) };
      if (was.length > 1) inv.categories = was;
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
    savingNote("Saving " + card.getAttribute("data-card") + ": " + copies(q) + "…", false, card, "Quantity");
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
      // where Archidekt has the card filed, kept through any move of it still waiting or saving
      // (M7): a refused move files it back there, and so does a refused removal
      var filed = holdConfirmed(card, "Move", function () { return { spot: spotOf(card), cat: byCategory ? stackOf(card) : catOf(card) }; });
      var near = card.closest(".stack");
      var saying = "Removing " + name + "…";
      card.remove();
      refreshStacks();
      closeMenu();
      closeViewer();
      if (!document.activeElement || document.activeElement === document.body) focusNear(near);
      savingNote(saying, false, card, "Removal");
      background({
        card: card, what: "Removal",
        start: function () {
          // the count to put back with an Undo is the one Archidekt has by now
          if (s) { setQty(card, s.saved); settleSteps(s); }
          var ch = changeFor(card, "remove");
          var inv = inverseFor(card, ch);
          return { changes: [ch], inverse: inv ? [].concat(inv) : null, saying: saying };
        },
        // kept for an Undo, which puts this element back (drawRows)
        saved: function () { var st = filed.state.spot.stack; keepRemoved(card, st ? st.getAttribute("data-group") : ""); },
        undo: function () {
          putBack(card, filed.state.spot);
          return name + " is back in " + (zoneOf(card) === "side" ? "the " + sideCat : "the deck") + ".";
        },
        done: function () { releaseConfirmed(filed); }
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
    var held = holdConfirmed(card, "Move", function () { return { spot: spotOf(card), cat: from }; }), gen = held.gen;
    held.queued++;
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
    var now = { spot: spotOf(card), cat: to };
    savingNote(saying, false, card, "Move");
    background({
      card: card, what: "Move",
      // an earlier move of this card was refused: this one was made from where Archidekt never had it
      start: function () { return held.gen === gen ? { changes: [ch], inverse: inv ? [inv] : null, saying: saying } : null; },
      saved: function () { held.state = now; },
      undo: function () {
        held.gen++;
        var back = held.state;
        // taken off the page meanwhile (a Remove waits its turn): only filed back; the removal's
        // own answer decides whether it shows again
        putBack(card, back.spot, !card.isConnected);
        return name + " is back in " + (back.cat || "its category") + "." +
          droppedNote(held, "Your later move of it was not sent.", "Your later moves of it were not sent.");
      },
      done: function () { held.queued--; releaseConfirmed(held); }
    });
  }
  function categoryChoices(card) {
    var cur = byCategory ? stackOf(card) : catOf(card);
    var names = deckCats.slice();
    if (byCategory) $$(".stack", cards).forEach(function (st) { var n = st.getAttribute("data-group"); if (names.indexOf(n) < 0) names.push(n); });
    if (names.indexOf(sideCat) < 0) names.push(sideCat);
    return names.map(function (n) { return { name: n, current: n === cur, side: excluded.indexOf(n) >= 0 }; });
  }

  // -- Change printing… (the shared picker, static/printpick.js): a pick shows at once and saves
  // in the background like the menu's other edits, as one set_printing change; Undo puts the old
  // printing back, and the old finish when the new printing is not made in it (P6).
  // The card's printing as the page shows it: data-set ("CMR 472"), the picture, the price and
  // the finish badge; pr: {set, img, price, finish}, each left as it is when not given.
  function showPrinting(card, pr) {
    var oldImg = card.getAttribute("data-img") || "";
    if (pr.set) {
      card.setAttribute("data-set", pr.set);
      $$(".set", card).forEach(function (s) {
        // the Text view shows the set code, the Table view the code and number ("SLD #1074", N15)
        s.textContent = s.textContent.indexOf(" ") >= 0 ? printLabel(pr.set) : pr.set.split(" ")[0];
        s.title = printLabel(pr.set);
      });
    }
    if (pr.img && pr.img !== oldImg) {
      card.setAttribute("data-img", pr.img);
      if (oldImg) $$("img", card).forEach(function (i) { if (i.getAttribute("src") === oldImg) i.src = pr.img; });
    }
    if (pr.price !== undefined) {
      var p = pr.price === null || isNaN(pr.price) ? null : Number(pr.price);
      if (p === null) card.removeAttribute("data-price"); else card.setAttribute("data-price", p.toFixed(2));
      var cell = $(".price", card);
      if (cell) cell.textContent = p === null ? "" : money(p);
    }
    if (pr.finish) setFinish(card, pr.finish);
  }
  function printingOf(card) {
    var price = parseFloat(card.getAttribute("data-price"));
    return { set: card.getAttribute("data-set") || "", img: card.getAttribute("data-img") || "",
      price: isNaN(price) ? null : price, finish: card.getAttribute("data-finish") || "Normal" };
  }
  function changePrinting(card) {
    var Pick = window.MtgPrintPick;
    if (!Pick || !card.isConnected) return;
    var pr = (card.getAttribute("data-set") || "").trim().split(/\s+/);
    Pick.open({
      name: card.getAttribute("data-card") || "",
      current: pr.length === 2 && pr[0] ? { set: pr[0].toLowerCase(), number: pr[1] } : null,
      finish: card.getAttribute("data-finish") || "Normal",
      returnFocus: card,
      onPick: function (p, info) { if (card.isConnected) savePrinting(card, p, info); }
    });
  }
  var FINISH_WORDS = { normal: "non-foil", foil: "foil", etched: "etched" };
  // "SLD 1075" (the page's data-set) as the picker writes it: "SLD #1075"
  function printLabel(set) { return String(set || "").trim().replace(/\s+/, " #"); }
  function savePrinting(card, p, info) {
    var name = card.getAttribute("data-card") || "";
    var was = printingOf(card);
    var wasPr = was.set.trim().split(/\s+/);
    var label = String(p.set).toUpperCase() + " " + p.collector_number;  // data-set, as the page draws it
    var said = printLabel(label);
    var ch = { action: "set_printing", card_name: name, set_code: String(p.set).toLowerCase(), collector_number: String(p.collector_number) };
    if (info.changed) ch.finish = info.finish;
    // Undo: the old printing (and finish, when this pick changed it). Another row of the card in
    // the deck in another printing or finish would be swapped by it too: no Undo then.
    var inv = wasPr.length === 2 && wasPr[0] ? { action: "set_printing", card_name: name, set_code: wasPr[0].toLowerCase(), collector_number: wasPr[1] } : null;
    if (inv && info.changed) inv.finish = was.finish.toLowerCase();
    if (inv && $$(".c, .row", cards).some(function (c) {
      return c !== card && zoneOf(c) === "main" && frontFace(c.getAttribute("data-card")) === frontFace(name) &&
        (c.getAttribute("data-set") !== was.set || c.getAttribute("data-finish") !== was.finish);
    })) inv = null;
    var fin = info.finish.charAt(0).toUpperCase() + info.finish.slice(1);
    var now = { set: label, img: p.image_normal || p.image_small || "", price: window.MtgPrintPick.priceOf(p, info.finish), finish: fin };
    var held = holdConfirmed(card, "Printing", function () { return was; }), gen = held.gen;
    held.queued++;
    var saying = "Changing " + name + " to " + said + "…";
    showPrinting(card, now);
    refreshStacks();
    savingNote(saying, false, card, "Printing");
    // P6: the Saved message says when the finish changed with the printing
    var note = "Saved. " + name + " is now " + said +
      (info.changed ? ", " + FINISH_WORDS[info.finish] + ": that printing is not made in " + FINISH_WORDS[info.from] : "") +
      ". Snapshot kept under History.";
    background({
      card: card, what: "Printing",
      // an earlier printing change of this card was refused: this one was made over a printing Archidekt never had
      start: function () { return held.gen === gen ? { changes: [ch], inverse: inv ? [inv] : null, saying: saying, note: note } : null; },
      saved: function () { held.state = now; },
      undo: function () {
        held.gen++;
        var back = held.state;
        showPrinting(card, back);
        refreshStacks();
        return name + " is back to " + printLabel(back.set) + "." +
          droppedNote(held, "Your later printing change was not sent.", "Your later printing changes were not sent.");
      },
      done: function () { held.queued--; releaseConfirmed(held); }
    });
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
        if (!open) return;
        fit(m);
        // the opened list in view inside the menu (which scrolls in a short window), from its
        // Move to item down as far as fits
        var end = sub.offsetTop + sub.offsetHeight - m.clientHeight;
        if (m.scrollTop < end) m.scrollTop = Math.min(moveBtn.offsetTop, end);
        var f = $("button:not([disabled])", sub);
        if (f) f.focus({ preventScroll: true });
      };
      moveBtn.addEventListener("click", function (e) { stop(e); toggleSub(sub.hidden); });
      moveBtn.addEventListener("keydown", function (e) { if (e.key === "ArrowRight") { stop(e); toggleSub(true); } });
      sub.addEventListener("keydown", function (e) { if (e.key === "ArrowLeft") { stop(e); toggleSub(false); moveBtn.focus(); } });
      m.appendChild(moveBtn);
      m.appendChild(sub);
      // the deck proper only: Archidekt's printing change is for the deck's own rows
      if (window.MtgPrintPick && zoneOf(card) === "main") m.appendChild(menuItem("Change printing…", "image", function () { closeMenu(); changePrinting(card); }));
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
    // measured at its own height, not at the cap an earlier fit put on it: a list opened inside
    // it (Move to) makes it taller, and it moves up to show that before it scrolls
    m.style.maxHeight = "";
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
    // The stack under the pointer fans out below its stack's box, over the top of the stack
    // directly below, and a native drag keeps it open: a drop there would land on the card's own
    // stack. The fan stays closed while a card is dragged (deckpage.py, .carddrag).
    cards.classList.add("carddrag");
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
    cards.classList.remove("carddrag");
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
})();

/* Play on EDHPlay: every press opens a small confirmation box; its "Copy and Open
   EDHPlay.com" copies the whole deck in EDHPlay's import format and opens edhplay.com (in the
   Android app, the phone's browser). The box asks the gateway for the list as it opens (the
   button's data-src, made by export_formats.to_edhplay) and the button turns on once it is here,
   so the copy can still be made inside that press, the way the app's WebView allows it; a list
   that can't be had shows why, with Try again. The two import steps show open until the member
   has launched once on this device, then sit folded in the same box (Ben, 01:36Z 10-14). The box
   closes with ×, Escape or a press outside it (Ben, 02:08Z 10-14: no Cancel). Sideboard cards left
   out to fit EDHPlay's 15-card limit are named in the box and in the note that follows. Without
   script the button is a plain link to EDHPlay. */
(function () {
  "use strict";
  var STEPS = ["On EDHPlay, go to My Decks, choose Add Deck, then open the new deck.",
    "Press Import, paste, then press Import & Replace."];
  var COPIED = "Deck list copied. On EDHPlay: My Decks, Add Deck, open the deck, Import, paste, then Import & Replace.";
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
  // 0.7.25 and 0.7.26 kept a first-time tip's "seen" mark here; the box replaced that tip
  try { localStorage.removeItem("mtg.edhplay.tip"); } catch (e) { /* private window */ }
  var STEPS_SEEN = "mtg.edhplay.steps";  // set once the member has launched from the box here
  function stepsSeen() {
    try { return localStorage.getItem(STEPS_SEEN) === "1"; } catch (e) { return false; }
  }
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
  // the list for the deck: {list, left_out, left_count}, or an Error whose message is shown.
  // The box shows only the three approved messages: the gateway's own reason goes to
  // the console, never on screen. Signed out, retrying can't help: err.signedOut.
  function fetchList(src) {
    return fetch(src, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.ok && d && d.ok && typeof d.list === "string") return d;
        if (r.status === 401) {
          var out = new Error("You're signed out. Reload the page to sign in.");
          out.signedOut = true;
          throw out;
        }
        if (window.console) console.warn("Play on EDHPlay: the deck list request failed (HTTP " + r.status + ")" + (d && d.message ? ": " + d.message : ""));
        throw new Error("The deck list couldn't be loaded.");
      });
    }, function () {
      throw new Error("The deck list couldn't be loaded: no connection.");
    });
  }
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
      var f = dialog.querySelectorAll("a[href],button:not([disabled]),summary");
      var first = f[0], last = f[f.length - 1];
      if (!dialog.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }
  function open(link) {
    close();
    // a note left from an earlier launch would sit above the box (L10): the box says it all again
    var old = document.querySelector(".edhplay-toast");
    if (old) old.remove();
    back = link;
    var data = null;  // the list, once the gateway has sent it
    dialog = make("div", "picker edhplay-dialog");
    var mine = dialog;
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "edhplay-title");
    dialog.addEventListener("click", function (e) { if (e.target === dialog) close(); });
    var box = make("div", "pickbox");
    var head = make("div", "head");
    var h = make("h2", "", "Play on EDHPlay");
    h.id = "edhplay-title";
    var x = make("button", "close icon-only", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.title = "Close";
    x.addEventListener("click", close);
    head.appendChild(h); head.appendChild(x);
    box.appendChild(head);
    var how = make("details", "disclosure edhplay-steps");
    how.open = !stepsSeen();
    how.appendChild(make("summary", "", "How to import"));
    var steps = make("ol", "steps");
    STEPS.forEach(function (s) { steps.appendChild(make("li", "", s)); });
    how.appendChild(steps);
    box.appendChild(how);
    var spot = make("div", "edhplay-state");  // the cut line, what to set on EDHPlay, or why the list isn't here
    spot.hidden = true;
    box.appendChild(spot);
    box.appendChild(make("p", "muted small", "The maybeboard and tokens aren't sent."));
    var acts = make("div", "acts");
    var go = make("a", "btn btn-primary", "Copy and Open EDHPlay.com");
    go.href = link.href;
    go.target = "_blank";
    go.rel = "noreferrer noopener";
    acts.appendChild(go);
    box.appendChild(acts);
    dialog.appendChild(box);
    document.body.appendChild(dialog);
    document.addEventListener("keydown", onKey, true);
    function waiting(on) {
      if (on) { go.setAttribute("aria-disabled", "true"); go.setAttribute("aria-busy", "true"); }
      else go.removeAttribute("aria-busy");
    }
    function load() {
      spot.textContent = "";
      spot.hidden = true;
      waiting(true);
      fetchList(link.getAttribute("data-src")).then(function (d) {
        if (dialog !== mine) return;  // closed, or opened again, meanwhile
        data = d;
        waiting(false);
        go.removeAttribute("aria-disabled");
        if (d.left_count) {
          spot.appendChild(make("p", "notice warn", cutLine(d.left_count, d.left_out)));
          spot.hidden = false;
        }
        if (d.no_commander) {  // nothing is marked, so EDHPlay sets no commander
          spot.appendChild(make("p", "notice", "Choose your commander on EDHPlay."));
          spot.hidden = false;
        }
        if (d.companion) {  // EDHPlay's import never sets a companion
          spot.appendChild(make("p", "notice", "Set your companion on EDHPlay."));
          spot.hidden = false;
        }
      }, function (err) {
        if (dialog !== mine) return;
        waiting(false);
        var p = make("p", "notice error");
        p.setAttribute("role", "alert");
        p.appendChild(make("span", "", err.message));
        if (!err.signedOut) {
          var again = make("button", "btn small", "Try again");
          again.type = "button";
          again.addEventListener("click", function () { load(); go.focus(); });
          p.appendChild(again);
        }
        spot.appendChild(p);
        spot.hidden = false;
      });
    }
    go.addEventListener("click", function (e) {
      if (!data) { e.preventDefault(); return; }  // the list is on its way, or couldn't be had
      // the copy happens inside this press; the link itself opens EDHPlay (nothing is prevented)
      var copied = copyNow(data.list);
      var cut = data.left_count || 0, leftOut = data.left_out || "";
      try { localStorage.setItem(STEPS_SEEN, "1"); } catch (err) { /* private window */ }
      close();
      copied.then(function (ok) {
        if (!ok) {
          note(["The deck list couldn't be copied here. Use More, then Export deck, to copy it."], "error");
          return;
        }
        var lines = [COPIED];
        if (cut) lines.push(cutLine(cut, leftOut));
        note(lines, cut ? "warn" : "");
      });
    });
    load();
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
