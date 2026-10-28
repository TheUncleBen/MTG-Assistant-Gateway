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
  // A new deck whose cards are still going in: ask every few seconds whether it is done, and
  // reload once it is (the page then comes back with its edits on).
  var making = $("p.notice.making[data-made-poll]");
  if (making) {
    (function ask() {
      setTimeout(function () {
        fetch(making.getAttribute("data-made-poll"), { credentials: "same-origin", headers: { Accept: "application/json" } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (d && d.made) location.reload(); else ask(); })
          .catch(ask);
      }, 3000);
    })();
  }
  // A save still running on the deck when the page was drawn (a slow one, from this tab before
  // a reload or from another): ask now and then whether it has ended, then load the page again
  // so the rows show what Archidekt took.
  var busy = $("p.notice.saving[data-busy]");
  if (busy) {
    (function ask() {
      setTimeout(function () {
        fetch(busy.getAttribute("data-busy"), { credentials: "same-origin", headers: { Accept: "application/json" } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            if (d && d.busy === false) { location.reload(); return; }
            if (d && d.changes) busy.lastChild.textContent = " Saving " + (d.changes === 1 ? "1 change" : d.changes + " changes") + "…";
            ask();
          })
          .catch(ask);
      }, 2000);
    })();
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
  $$("#viewform, #listform").forEach(function (form) {
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
    // (a save redraws cards in place), not on every keystroke
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
  // a phone, a touch screen in the rail range (to 1279.98 px, as theme.py draws the rail) or the app
  var phone = function () {
    return window.matchMedia("(max-width: 599.98px)").matches ||
      window.matchMedia("(max-width: 1279.98px) and ((pointer: coarse) or (hover: none))").matches ||
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
  // A side row (Maybeboard, Optional Artwork, any category left out of the deck) leaves that
  // category, not the deck: its Remove says which.
  function removeLabel(card) {
    if (zoneOf(card) !== "side") return "Remove from deck";
    return "Remove from \u201c" + (catsOf(card)[0] || sideCat) + "\u201d";
  }
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
  // -- a change big enough to ask first (modes.hand_edit_confirm): the endpoint made the proposal
  // and waits for the answer. A removal asks in the shared box, which ends on the solid red button;
  // a commander question keeps the page's own dialog strip. The proposal made for the check is
  // not wanted when the answer is no (Keep them, Cancel, Escape, the × or the scrim): it is
  // rejected, so Proposals stays clean and it does not count toward the pending cap. One still
  // unanswered when the page is left (a reload, Back, the tab closed) is rejected on the way out,
  // or it would wait in Proposals and the deck's waiting-changes strip. That leaving request is
  // dropped where a service worker controls the page (Chromium), so the proposal is noted for
  // the next page too (MtgMenu.oweReject, static/cardmenu.js), which rejects it and drops its
  // row from the strip.
  var asking = null;  // the proposal id of the question on screen
  function rejectProposal(pid, leaving) {
    var o = { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF }, body: "{}" };
    if (leaving) {
      o.keepalive = true;  // the request outlives the page
      if (window.MtgMenu && window.MtgMenu.oweReject) window.MtgMenu.oweReject(pid);
    }
    try { fetch("/api/v1/proposals/" + enc(pid) + "/reject", o).catch(function () { /* a leftover pending proposal is harmless */ }); } catch (e) { /* no fetch */ }
  }
  window.addEventListener("pagehide", function () { if (asking) { rejectProposal(asking, true); asking = null; } });
  /* why: the endpoint's reason; pid: the proposal it made; about: {name, cat, custom} when the
     page knows the one card the question is about (a custom card's menu), else null */
  function askConfirm(why, pid, about) {
    asking = pid;
    var declined = false;
    var decline = function () {
      if (declined) return;
      declined = true;
      asking = null;
      rejectProposal(pid);
    };
    var removal = /^This removes (\d+) (different )?cards/.exec(why || "");
    if (removal && window.MtgBox) {
      return new Promise(function (resolve) {
        var n = removal[1], what = n + (removal[2] ? " different cards" : n === "1" ? " card" : " cards");
        var copies = n + (n === "1" ? " copy" : " copies");
        var body = el("div");
        body.appendChild(el("p", "", about && about.cat ? "They come out of “" + about.cat + "”." : "They come out of the deck."));
        if (about && about.custom) body.appendChild(el("p", "muted small", "Restoring a snapshot doesn't put custom cards back."));
        var answered = false;
        window.MtgBox.confirm({
          title: about && about.name ? "Remove " + copies + " of “" + about.name + "”?" : "Remove " + what + "?",
          body: body, ok: "Remove " + what, danger: true, cancel: "Keep them",
          onOk: function () { answered = true; asking = null; resolve(true); },
          onClose: function () { if (!answered) { decline(); resolve(false); } }
        });
      });
    }
    return new Promise(function (resolve) {
      var yes = el("button", "btn-primary", "Save anyway");
      yes.type = "button";
      var no = el("button", "", "Cancel");
      no.type = "button";
      var done = function (v) { dismissToast(); resolve(v); };
      yes.addEventListener("click", function () { asking = null; done(true); });
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
    var body = opts.proposalId ? { proposal_id: opts.proposalId, version: opts.version } : { changes: changes, confirmed: opts.confirmed === true };
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
          return saveEdit(changes, undoChanges, { proposalId: d.proposal_id, version: d.version, replay: opts.replay, note: opts.note });
        });
      }
      saving = false;
      // Archidekt is slow and the save carries on behind: the page stays on the deck, keeps its
      // Saving note and draws the row once the save ends (0.7.46 audit D-07)
      if (d.ok && d.status === 202 && d.proposal_id) return awaitApply(d.proposal_id, changes, undoChanges, opts);
      if (d.ok && d.proposal_id) { location.href = "/proposals/" + enc(d.proposal_id); return d; }  // writes are off: kept for review
      var failed = new Error(d.message || "The change could not be saved.");
      // Archidekt never answered, so the write may have landed: the answer carries what the deck
      // shows now, and the caller draws that instead of putting the card back (D-04)
      if (d.may_have_changed && d.rows) failed.answer = d;
      throw failed;
    }).catch(function (err) {
      saving = false;
      if (!err || !err.cancelled) throw err instanceof Error ? err : new Error("No connection; nothing was changed.");
      throw err;
    });
  }
  // A save Archidekt took longer than the gateway waits for: the proposal is asked about now and
  // then until it ends; then the touched rows are read again and drawn, Saved with Undo as any
  // other save (or the failure, with what the deck shows now).
  function awaitApply(pid, changes, undoChanges, opts) {
    var names = namesOf(changes);
    return new Promise(function (resolve, reject) {
      var refresh = function (p) {
        api(editUrl, { refresh: true, names: names }).then(function (r) {
          if (p.state === "applied") {
            r.applied = true; r.proposal_id = pid; r.snapshot_id = p.snapshot_id;
            applyAnswer(r, changes, undoChanges, opts.replay, opts.note);
            resolve(r);
            return;
          }
          var failed = new Error((p.result && p.result.detail) || "The change could not be saved.");
          if (r.ok && r.rows) failed.answer = r;
          reject(failed);
        }, function () { reject(new Error("No connection; the deck page shows what was saved.")); });
      };
      (function look() {
        setTimeout(function () {
          fetch("/api/v1/proposals/" + enc(pid), { credentials: "same-origin", headers: { Accept: "application/json" } })
            .then(function (r) { return r.json(); })
            .then(function (p) {
              if (!p || !p.ok) { reject(new Error("No connection; the deck page shows what was saved.")); return; }
              if (p.state === "applying" || p.state === "pending") { look(); return; }
              refresh(p);
            }, look);
        }, 2000);
      })();
    });
  }
  function showError(err) {
    if (err && err.cancelled) return;
    toast((err && err.message) || "No connection; nothing was changed.", { error: true });
  }

  // -- custom cards (deckpage.custom_card_row): the member's own custom cards in the deck ------
  // Their count, category and removal save through the deck's custom card endpoint (api.py
  // custom_cards_edit; decks.propose_custom_cards), one change at a time after the page's other
  // saves. The card changes on the page at once and goes back when the save does not go through.
  // Archidekt keeps no snapshot of these rows (decks.py), so there is no History step: a removal
  // offers its own Undo, which adds the card back.
  var customUrl = "/api/v1/decks/" + enc(deckId) + "/custom_cards";
  var customQueue = Promise.resolve();
  var customSteps = [];  // {card, shown, saved, timer}
  var CUSTOM_LOCK = "Not for custom cards (Archidekt doesn't keep default categories for them).";
  var CUSTOM_PICK = "Custom cards are changed one at a time, from their own menu.";
  function isCustom(card) { return !!card && card.hasAttribute("data-custom-id"); }
  function customIdOf(card) { return parseInt(card.getAttribute("data-custom-id"), 10); }
  function customPost(changes, pid, version, about) {
    var body = pid ? { proposal_id: pid, version: version } : { changes: changes, confirmed: false };
    return api(customUrl, body).then(function (d) {
      if (d.ok && d.applied) return d;
      if (d.ok && d.needs_confirm) {
        return askConfirm(d.why, d.proposal_id, about).then(function (yes) {
          if (!yes) { var c = new Error("Nothing was changed."); c.cancelled = true; throw c; }
          return customPost(changes, d.proposal_id, d.version, about);
        });
      }
      if (d.ok && d.status === 202 && d.proposal_id) {
        var see = el("a", "btn", "Review page");
        see.href = "/proposals/" + enc(d.proposal_id);
        toast("Still saving on Archidekt; the review page shows how far it got.", { sticky: true, actions: [see] });
        return d;
      }
      if (d.ok && d.proposal_id) { location.href = "/proposals/" + enc(d.proposal_id); return d; }  // writes are off: kept for review
      throw new Error(d.message || "The change could not be saved.");
    }, function (err) {
      if (err && err.cancelled) throw err;
      throw new Error("No connection; nothing was changed.");
    });
  }
  // the deck's custom rows as Archidekt has them now: each card's count and category, and off the
  // page when the row is gone
  function customDraw(d) {
    var rows = d.custom_rows;
    if (!rows) return;
    $$(".c[data-custom-id], .row[data-custom-id]", cards).forEach(function (card) {
      var id = card.getAttribute("data-custom-id");
      var r = rows.filter(function (x) { return String(x.custom_card_id) === id; })[0];
      if (!r) { card.remove(); return; }
      setQty(card, r.qty);
      var cat = (r.categories && r.categories[0]) || "";
      if (r.relation_id !== null && r.relation_id !== undefined) card.setAttribute("data-rel", String(r.relation_id));
      if (cat) {
        card.setAttribute("data-cat", cat);
        var zone = excluded.indexOf(cat) >= 0 ? "side" : "main";
        card.setAttribute("data-zone", zone);
        card.classList.toggle("side", zone === "side" && card.classList.contains("row"));
      }
      if (r.categories && r.categories.length > 1) card.setAttribute("data-allcats", JSON.stringify(r.categories));
      else card.removeAttribute("data-allcats");
      if (r.label !== undefined) setLabel(card, r.label || "");
      if (r.mana_value !== undefined) setManaValue(card, r.mana_value);
      if (byCategory && cat && stackOf(card) !== cat) {
        var st = ensureStack(cat);
        if (st) $(".cards, .rows", st).appendChild(card);
      }
    });
    refreshStacks();
    drawStats(d);
    if (menu && !menuCard.isConnected) closeMenu();
  }
  // Color tag… for a custom card: a form in the menu (the deck's tags offered), saved as the
  // card's label through the custom card endpoint; Take tag off clears it
  function customTagForm(card, m) {
    var name = card.getAttribute("data-card") || "";
    var was = card.getAttribute("data-label") || "";
    var cur = splitLabel(was);
    m.textContent = "";
    m.appendChild(el("div", "head", name));
    var form = el("form", "mvform");
    form.noValidate = true;
    var lab = el("label", "", "Tag name");
    var input = el("input", "");
    input.type = "text"; input.maxLength = 40; input.placeholder = "e.g. Have"; input.id = "ct-input";
    input.setAttribute("list", "deck-tags-custom");
    lab.htmlFor = "ct-input";
    input.value = cur.name;
    var known = el("datalist");
    known.id = "deck-tags-custom";
    var seen = Object.create(null);
    $$("[data-label]", cards).forEach(function (c) {
      var t = splitLabel(c.getAttribute("data-label"));
      if (!t.name || seen[t.name]) return;
      seen[t.name] = t.colour || "#656565";
      var o = el("option"); o.value = t.name; known.appendChild(o);
    });
    var colLab = el("label", "", "Color");
    var colour = el("input", "");
    colour.type = "color"; colour.id = "ct-colour"; colLab.htmlFor = "ct-colour";
    colour.value = cur.colour || "#37d67a";
    input.addEventListener("input", function () { msg.className = "status"; msg.textContent = ""; if (seen[input.value.trim()]) colour.value = seen[input.value.trim()]; });
    var hint = el("p", "hint", "Archidekt's color tag on this card, in this deck only.");
    var msg = el("p", "status", "");
    msg.setAttribute("role", "status");
    var save = el("button", "btn primary", "Apply tag"); save.type = "submit";
    var off = el("button", "btn", "Take tag off"); off.type = "button";
    var cancel = el("button", "btn", "Cancel"); cancel.type = "button";
    var acts = el("div", "acts");
    acts.appendChild(save); if (cur.name) acts.appendChild(off); acts.appendChild(cancel);
    [lab, input, known, colLab, colour, hint, acts, msg].forEach(function (n) { form.appendChild(n); });
    m.appendChild(form);
    var send = function (label) {
      setLabel(card, label);
      closeMenu();
      var t = splitLabel(label);
      customSave(card, [{ action: "set_quantity", custom_card_id: customIdOf(card), label: label }],
        (t.name ? "Tagging " + name + " " + t.name + "…" : "Taking the tag off " + name + "…"),
        function () { setLabel(card, was); }
      ).then(function (d) { if (d.applied) toast(t.name ? "Tagged " + name + " " + t.name + "." : "Tag taken off " + name + "."); }, function () { /* told */ });
    };
    form.addEventListener("submit", function (e) {
      stop(e);
      var v = input.value.trim();
      if (!v || v.indexOf(",") >= 0) { msg.className = "status err"; msg.textContent = "A tag needs a name, without commas."; input.focus(); return; }
      var label = v + "," + colour.value.toLowerCase();
      if (label === was.toLowerCase()) { closeMenu(); return; }
      send(label);
    });
    off.addEventListener("click", function (e) { stop(e); send(""); });
    cancel.addEventListener("click", function (e) { stop(e); closeMenu(); });
    form.addEventListener("keydown", function (e) { if (e.key === "Escape") { stop(e); closeMenu(); } e.stopPropagation(); });
    fit(m);
    input.focus();
    input.select();
  }
  // one custom card save at a time, after the page's other saves; back(): puts the card back when
  // the save does not go through. Resolves with the answer; rejects after the note is shown.
  function customSave(card, changes, saying, back) {
    savingNote(saying, false, card, "Custom");
    // the card the question (a big removal) is about: its name and the category it leaves
    var about = { name: card.getAttribute("data-card") || "", cat: catOf(card), custom: true };
    var run = function () { return idle().then(function () { return customPost(changes, null, null, about); }); };
    var p = customQueue.then(run, run);
    customQueue = p.then(function () {}, function () {});
    return p.then(function (d) {
      dropSavingNote(card, "Custom");
      if (d.applied) customDraw(d);
      return d;
    }, function (err) {
      dropSavingNote(card, "Custom");
      if (back) back();
      if (err && err.cancelled) { dismissToast(); throw err; }
      toast("Not saved: " + ((err && err.message) || "no connection").replace(/\.$/, "") + ".", { error: true });
      throw err;
    });
  }
  function customStep(card, delta) {
    var name = card.getAttribute("data-card");
    var s = customSteps.filter(function (x) { return x.card === card; })[0];
    var q = (s ? s.shown : qtyOf(card)) + delta;
    if (q <= 0) { customRemove(card); return; }
    if (q > 99) return;
    if (!s) { s = { card: card, shown: q, saved: qtyOf(card), timer: 0 }; customSteps.push(s); }
    s.shown = q;
    setQty(card, q);
    refreshStacks();
    savingNote("Saving " + name + ": " + copies(q) + "…", false, card, "Custom");
    clearTimeout(s.timer);
    s.timer = setTimeout(function () {
      var i = customSteps.indexOf(s);
      if (i >= 0) customSteps.splice(i, 1);
      if (s.shown === s.saved || !card.isConnected) { dropSavingNote(card, "Custom"); return; }
      var sent = s.shown, was = s.saved;
      customSave(card, [{ action: "set_quantity", custom_card_id: customIdOf(card), quantity: sent }],
        "Saving " + name + ": " + copies(sent) + "…",
        function () { if (card.isConnected) { setQty(card, was); refreshStacks(); } }
      ).then(function (d) { if (d.applied) toast("Saved " + name + ": " + copies(sent) + "."); }, function () { /* told */ });
    }, 400);
  }
  function customMove(card, to, target) {
    var name = card.getAttribute("data-card");
    var spot = spotOf(card);
    var zone = excluded.indexOf(to) >= 0 ? "side" : "main";
    card.setAttribute("data-cat", to);
    card.removeAttribute("data-allcats");
    card.setAttribute("data-zone", zone);
    card.classList.toggle("side", zone === "side" && card.classList.contains("row"));
    target = target || (byCategory ? ensureStack(to) : null);
    var focused = document.activeElement === card;
    if (target) $(".cards, .rows", target).appendChild(card);
    if (focused) card.focus();
    refreshStacks();
    customSave(card, [{ action: "set_quantity", custom_card_id: customIdOf(card), quantity: qtyOf(card), category: to }],
      "Moving " + name + " to " + to + "…",
      function () { putBack(card, spot); }
    ).then(function (d) { if (d.applied) toast("Moved " + name + " to " + to + "."); }, function () { /* told */ });
  }
  function customRemove(card) {
    var name = card.getAttribute("data-card"), id = customIdOf(card);
    var s = customSteps.filter(function (x) { return x.card === card; })[0];
    if (s) { clearTimeout(s.timer); customSteps.splice(customSteps.indexOf(s), 1); }
    var spot = spotOf(card), qty = s ? s.saved : qtyOf(card), cat = catOf(card);
    var near = card.closest(".stack");
    card.remove();
    refreshStacks();
    closeMenu();
    closeViewer();
    if (!document.activeElement || document.activeElement === document.body) focusNear(near);
    customSave(card, [{ action: "remove", custom_card_id: id }], "Removing " + name + "…",
      function () { putBack(card, spot); setQty(card, qty); refreshStacks(); }
    ).then(function (d) {
      if (!d.applied) return;
      // Undo: the card goes back into the deck with the count and category it had
      var undo = el("button", "btn", "Undo");
      undo.type = "button";
      undo.addEventListener("click", function () {
        undo.disabled = true;
        var ch = { action: "add", custom_card_id: id, quantity: qty };
        if (cat) ch.category = cat;
        customSave(card, [ch], "Adding " + name + " back…", null).then(function (r) {
          if (!r.applied) return;
          var st = (cat && byCategory && (ensureStack(cat) || stackByName(cat))) || (spot.stack && spot.stack.isConnected ? spot.stack : null) || $(".stack", cards);
          if (st) { $(".cards, .rows", st).appendChild(card); putBack(card, spot, true); }
          customDraw(r);
          toast(name + " is back in " + (cat || "the deck") + ".");
        }, function () { undo.disabled = false; });
      });
      toast("Removed " + name + ".", { actions: [undo] });
    }, function () { /* told */ });
  }
  // the menu's items for a custom card: its count, Move to and removal, Copy name; Lock personal
  // categories is shown but not offered (Archidekt keeps no default categories for them)
  function customItems(m, card, name) {
    m.appendChild(menuItem("Open card", "eye", function () { closeMenu(); openViewer(card); }));
    if (!(own && deckId)) {
      if (window.MtgMenu) m.appendChild(menuItem("Copy name", "copy", function () { closeMenu(); window.MtgMenu.copyAndSay(name, "Name copied."); }));
      return;
    }
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
    qtyLabels.push({ el: n, card: card, show: relabel });
    minus.addEventListener("click", function (e) { stop(e); customStep(card, -1); });
    plus.addEventListener("click", function (e) { stop(e); customStep(card, 1); });
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
      var b = menuItem(c.name, c.side ? "eye-off" : "tag", function () { closeMenu(); customMove(card, c.name); }, c.current ? "on" : "");
      if (c.side) b.title = "Not counted in the deck";
      if (c.current) { b.disabled = true; b.setAttribute("aria-current", "true"); }
      sub.appendChild(b);
    });
    var toggleSub = function (open) {
      sub.hidden = !open;
      moveBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) return;
      fit(m);
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
    // Archidekt's card extras for a custom card in your own deck: the color tag and the mana value
    m.appendChild(menuItem("Color tag…", "tag", function () { customTagForm(card, m); }));
    m.appendChild(menuItem("Mana value…", "edit", function () { manaValueForm(card, m); }));
    m.appendChild(menuItem(removeLabel(card), "x", function () { customRemove(card); }, "danger"));
    // no Lock personal categories: Archidekt keeps no default categories for a custom card
    // (the endpoint says so, CUSTOM_LOCK, should a tool ask)
    if (window.MtgMenu) {
      m.appendChild(el("div", "sep"));
      m.appendChild(menuItem("Copy name", "copy", function () { closeMenu(); window.MtgMenu.copyAndSay(name, "Name copied."); }));
    }
  }

  // A custom card's picture comes through the gateway's own route (deckpage.custom_picture_url)
  // and answers 404 when the card has none: the broken picture goes, the name-and-type
  // placeholder stays, and the viewer draws its name tile instead (no data-img).
  function pictureGone(img) {
    var card = img.closest(".c.custom, .row.custom");
    if (!card) return;
    var tile = img.nextElementSibling;  // the name tile feedback.js put after it: the placeholder has the name
    if (tile && tile.classList.contains("img-fallback")) tile.remove();
    if (img.classList.contains("art")) img.replaceWith(el("span", "art ph"));
    else img.remove();
    if (!$("img", card)) card.removeAttribute("data-img");
  }
  cards.addEventListener("error", function (e) {
    var t = e.target;
    if (t && t.tagName === "IMG" && t.closest(".c.custom, .row.custom")) pictureGone(t);
  }, true);
  $$(".c.custom img, .row.custom img", cards).forEach(function (img) {
    // failed before this script ran (a lazy picture not fetched yet has no currentSrc)
    if (img.complete && img.naturalWidth === 0 && img.currentSrc) pictureGone(img);
  });

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
  // down and never another card's, an error or a question.
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
  // What Archidekt last confirmed for a card, per kind of edit ("Move", "Mana value"), while
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
  // A refused edit's note says when later edits of the same
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
      // nothing left to send (+ then − before the save went): its Saving note goes too
      if (!plan) { dropSavingNote(job.card, job.what); return null; }
      savingNote(plan.saying, true, job.card, job.what);
      return saveEdit(plan.changes, plan.inverse, { note: plan.note }).then(function () { if (job.saved) job.saved(); }, function (err) {
        notSaved(plan, job, err, false);
      });
    }).then(done, done);
  }
  /* A save that did not go through. The card goes back as Archidekt last had it, and the red
     note says why, with Try again, which sends the same change once more (0.7.46 audit D-05).
     When Archidekt never answered, the write may have landed: nothing is put back; the rows the
     answer carries (what the deck shows now) are drawn instead (D-04). again: a Try again's own
     failure, after which there is nothing more to put back. */
  function notSaved(plan, job, err, again) {
    var back = again ? "" : job.undo();
    if (err && err.cancelled) { dismissToast(); return; }
    if (err && err.answer) {
      // the card is back as it was; when the rows Archidekt shows now draw it the same, the
      // change was not taken (a refusal after the request went): the plain failure, with Try
      // again. Otherwise it was, or the page cannot tell, and the rows say it.
      var names = namesOf(plan.changes), before = lookOf(names);
      drawRows(err.answer.rows, names);
      drawStats(err.answer);
      if (menu && !menuCard.isConnected) closeMenu();
      if (lookOf(names) !== before) {
        toast("Archidekt didn't answer; this is what the deck shows now.", { warn: true, sticky: true });
        return;
      }
    }
    var why = ((err && err.message) || "no connection").replace(/\.$/, "");
    var retry = el("button", "btn", "Try again");
    retry.type = "button";
    retry.addEventListener("click", function () { dismissToast(); sendAgain(plan, job); });
    toast(job.what + " not saved: " + why + "." + (back ? " " + back : ""), { error: true, actions: [retry] });
  }
  // How the cards of these names look on the page (their markup and the stack each sits in):
  // the same before and after a draw means the draw changed nothing about them
  function lookOf(names) {
    return $$(".c, .row", cards).filter(function (c) { return names.indexOf(frontFace(c.getAttribute("data-card"))) >= 0; })
      .map(function (c) { return stackOf(c) + "\n" + c.outerHTML; }).join("\n\n");
  }
  // Try again: the same change, sent once more in its turn; the answer draws the rows as any
  // save's does (Saved with Undo), and its failure says why, with Try again once more.
  function sendAgain(plan, job) {
    bgCount++;
    bgCards.push(job.card);
    syncHist();
    var done = function () { bgCount--; syncHist(); };
    bgQueue = bgQueue.then(idle).then(function () {
      bgCards.splice(bgCards.indexOf(job.card), 1);
      savingNote(plan.saying, true, job.card, job.what);
      return saveEdit(plan.changes, plan.inverse, { note: plan.note }).then(null, function (err) { notSaved(plan, job, err, true); });
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
  // The card menu's Companion and Show back face switches (Archidekt's companion and
  // flippedDefault, set_companion and set_back_face): saved in the background like the menu's other
  // edits, the card showing the new state at once and put back if Archidekt refuses it
  var SWITCHES = {
    companion: { attr: "data-companion", action: "set_companion", field: "companion", what: "Companion" },
    back_face: { attr: "data-flipped", action: "set_back_face", field: "back_face", what: "Show back face" }
  };
  // The companion's mark follows the switch (and Undo): the pink corner on a grid tile, with its
  // words for a screen reader; the word itself on a list row (deckpage.COMPANION_CORNER and
  // COMPANION_MARK draw the same on the server)
  function companionMark(card, on) {
    var tile = card.classList.contains("c");
    var mark = card.querySelector(tile ? ".corner.comp" : ".compmark");
    if (on && !mark) {
      if (tile) {
        mark = el("span", "corner comp");
        mark.setAttribute("role", "img");
        mark.setAttribute("aria-label", "Companion");
        mark.title = "Companion";
        card.appendChild(mark);
      } else {
        // the word goes right after the name cell, where the server draws it (under the name)
        mark = el("span", "compmark", "Companion");
        var n = card.querySelector(".n");
        if (n) n.insertAdjacentElement("afterend", mark); else card.appendChild(mark);
      }
    } else if (!on && mark) mark.remove();
  }
  // Show back face swaps the card's pictures (the tile, the list row's hover preview) between its
  // front (data-img) and its back (data-back, when the card has one), as the page draws them
  function showFace(card, back) {
    var front = card.getAttribute("data-img"), rear = card.getAttribute("data-back");
    if (!front || !rear) return;
    $$("img", card).forEach(function (i) {
      var src = i.getAttribute("src");
      if (src === (back ? front : rear)) i.src = back ? rear : front;
    });
  }
  function switchRow(card, kind) {
    var sw = SWITCHES[kind], name = card.getAttribute("data-card") || "";
    var cur = card.hasAttribute(sw.attr), want = !cur;
    var set = function (on) {
      if (on) card.setAttribute(sw.attr, "1"); else card.removeAttribute(sw.attr);
      if (kind === "companion") companionMark(card, on);
      if (kind === "back_face") showFace(card, on);
    };
    var ch = { action: sw.action, card_name: name, zone: card.getAttribute("data-zone") || "main" };
    var inv = { action: sw.action, card_name: name, zone: ch.zone };
    ch[sw.field] = want; inv[sw.field] = cur;
    var saying = "Saving " + sw.what.toLowerCase() + " for " + name + "…";
    var held = holdConfirmed(card, sw.what, function () { return cur; }), gen = held.gen;
    held.queued++;
    set(want);
    closeMenu();
    savingNote(saying, false, card, sw.what);
    background({
      card: card, what: sw.what,
      start: function () { return held.gen === gen ? { changes: [ch], inverse: [inv], saying: saying } : null; },
      saved: function () { held.state = want; },
      undo: function () {
        held.gen++;
        set(held.state);
        return name + ": " + sw.what.toLowerCase() + " is " + (held.state ? "on" : "off") + " again." +
          droppedNote(held, "Your later change of it was not sent.", "Your later changes of it were not sent.");
      },
      done: function () { held.queued--; releaseConfirmed(held); }
    });
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
    input.id = "mv-input"; lab.htmlFor = "mv-input"; input.autocomplete = "off";
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
      if (isCustom(card)) {
        setManaValue(card, value);
        closeMenu();
        customSave(card, [{ action: "set_quantity", custom_card_id: customIdOf(card), mana_value: value }],
          "Saving the mana value of " + name + "…",
          function () { setManaValue(card, cur === null ? null : Number(cur)); }
        ).then(function (d) { if (d.applied) toast(value === null ? name + " uses its own mana value again." : "Mana value of " + name + " saved: " + value + "."); }, function () { /* told */ });
        return;
      }
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
      if (["set_label", "set_mana_value", "set_companion", "set_back_face"].indexOf(ch.action) < 0) { steps[0].push(ch); return; }
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
    var rel = rowRel(card);
    if (rel) ch.relation_id = rel;
    return ch;
  }
  // A card with another row in the same zone (in the Maybeboard and in "Optional Artwork", say):
  // a remove, a count, a move or a printing names this row by its relation id, so that row only
  // changes. A card's one row in a zone is named by the card and the zone, as before.
  function rowRel(card) {
    var rel = Number(card.getAttribute("data-rel"));
    return rel > 0 && rel % 1 === 0 && twinIn(card, zoneOf(card)) ? rel : null;
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
      // its printing, finish,
      // color tag, custom mana value and companion and flip flags, not just name and category. Its notes cannot come back:
      // Archidekt has no way for the gateway to write them (confirmNotes warns first).
      inv = { action: "add", card_name: name, quantity: qty };
      var cats = catsOf(card);
      if (cats.length) inv.category = cats[0];
      if (cats.length > 1) inv.categories = cats;
      var pr = (card.getAttribute("data-set") || "").trim().split(/\s+/);
      if (pr.length === 2 && pr[0] && pr[1]) { inv.set_code = pr[0].toLowerCase(); inv.collector_number = pr[1]; }
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
      if (!ch.relation_id && twinIn(card, zone)) return null;
    } else return null;
    if (zone === "side") inv.zone = "side";
    // a count or a move keeps the row's relation id: its Undo names the same row
    if (ch.relation_id) inv.relation_id = ch.relation_id;
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
    if (isCustom(card)) { customStep(card, delta); return; }
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
        if (ch.relation_id) inv.relation_id = ch.relation_id;
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
    if (isCustom(card)) { customRemove(card); return; }
    var name = card.getAttribute("data-card");
    confirmNotes([card]).then(function (yes) {
      if (!yes) return;
      var s = stepOf(card);
      if (s) { clearTimeout(s.timer); s.timer = 0; s.gone = true; }
      // where Archidekt has the card filed, kept through any move of it still waiting or saving
      // a refused move files it back there, and so does a refused removal
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
          // the row's own category ("Optional Artwork"), not just the page's side category
          var own = catsOf(card)[0] || sideCat;
          var where = zoneOf(card) !== "side" ? "the deck" : /^(maybeboard|sideboard)$/i.test(own) ? "the " + own : "“" + own + "”";
          return name + " is back in " + where + ".";
        },
        done: function () { releaseConfirmed(filed); }
      });
    });
  }
  // Move to (and a drop on another stack): the card is filed under its new category at once, in
  // that stack when the page is grouped by category, and goes back when the save does not go through.
  function moveTo(card, to, target) {
    if (isCustom(card)) { customMove(card, to, target); return; }
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
  // printing back, and the old finish when the new printing is not made in it.
  // The card's printing as the page shows it: data-set ("CMR 472"), the picture, the price and
  // the finish badge; pr: {set, img, price, finish}, each left as it is when not given.
  function showPrinting(card, pr) {
    var oldImg = card.getAttribute("data-img") || "";
    if (pr.set) {
      card.setAttribute("data-set", pr.set);
      $$(".set", card).forEach(function (s) {
        // the Text view shows the set code, the Table view the code and number ("SLD #1074")
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
  // -- Watch this card / Art variants…: the watchlist's own JSON calls; Undo stops the watch
  // again by its id. The deck is not changed.
  function scryFinishOf(card) {
    var f = (card.getAttribute("data-finish") || "").toLowerCase();
    return f === "foil" || f === "etched" ? f : "nonfoil";
  }
  function watchCard(card, printing, finish) {
    var name = card.getAttribute("data-card") || "";
    var body = { name: name, finish: finish || scryFinishOf(card) };
    if (printing) { body.set_code = printing.set; body.collector_number = printing.collector_number; }
    var CardMenu = window.MtgCardMenu, was = !!(CardMenu && CardMenu.isWatched(name));
    return api("/watchlist/api/watch", body).then(function (d) {
      if (!d.ok) { toast(d.message || "The card could not be watched.", { error: true }); throw new Error("refused"); }
      var w = d.watch || {};
      if (CardMenu) CardMenu.markWatched(w.name || name, true);
      var open = el("a", "", "Open the watchlist");
      open.href = "/watchlist?q=" + enc(w.name || name);
      if (!w.created) { toast((w.name || name) + " is on your watchlist.", { actions: [open] }); return w; }
      var undo = el("button", "", "Undo");
      undo.type = "button";
      undo.addEventListener("click", function () {
        undo.disabled = true;
        api("/watchlist/api/unwatch", { watch_id: w.watch_id }).then(function (u) {
          if (!u.ok && u.status !== 404) { toast(u.message || "The watch could not be undone.", { error: true }); return; }
          if (CardMenu && !was) CardMenu.markWatched(w.name || name, false);
          toast((w.name || name) + " is no longer on your watchlist.");
        }).catch(function () { toast("No connection; the card is still watched.", { error: true }); });
      });
      toast((w.name || name) + " added to your watchlist.", { actions: [open, undo] });
      return w;
    }, function (err) { toast("No connection; the card was not watched.", { error: true }); throw err; });
  }
  function artVariants(card) {
    var Pick = window.MtgPrintPick;
    if (!Pick || !card.isConnected) return;
    var pr = (card.getAttribute("data-set") || "").trim().split(/\s+/);
    var opts = {
      name: card.getAttribute("data-card") || "",
      current: pr.length === 2 && pr[0] ? { set: pr[0].toLowerCase(), number: pr[1] } : null,
      finish: card.getAttribute("data-finish") || "Normal",
      returnFocus: card,
      onWatch: function (p, finish) { return watchCard(card, p, finish); }
    };
    // on the member's own deck the same picker can also put the printing in the deck
    if (own && deckId) opts.onPick = function (p, info) { if (card.isConnected) savePrinting(card, p, info); };
    Pick.open(opts);
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
    var zone = zoneOf(card), rel = rowRel(card);
    if (zone === "side") ch.zone = "side";
    if (rel) ch.relation_id = rel;
    // Undo: the old printing (and finish, when this pick changed it), for the card in the row's
    // zone (the new printing is a new row). Another row of the card there in another printing or
    // finish would be swapped by it too, and a maybeboard or sideboard change takes one row: no
    // Undo then.
    var inv = wasPr.length === 2 && wasPr[0] ? { action: "set_printing", card_name: name, set_code: wasPr[0].toLowerCase(), collector_number: wasPr[1] } : null;
    if (inv && info.changed) inv.finish = was.finish.toLowerCase();
    if (inv && zone === "side") inv.zone = "side";
    if (inv && $$(".c, .row", cards).some(function (c) {
      return c !== card && zoneOf(c) === zone && frontFace(c.getAttribute("data-card")) === frontFace(name) &&
        (zone === "side" || c.getAttribute("data-set") !== was.set || c.getAttribute("data-finish") !== was.finish);
    })) inv = null;
    var fin = info.finish.charAt(0).toUpperCase() + info.finish.slice(1);
    var now = { set: label, img: p.image_normal || p.image_small || "", price: window.MtgPrintPick.priceOf(p, info.finish), finish: fin };
    var held = holdConfirmed(card, "Printing", function () { return was; }), gen = held.gen;
    held.queued++;
    var saying = "Changing " + name + " to " + said + "…";
    showPrinting(card, now);
    refreshStacks();
    savingNote(saying, false, card, "Printing");
    // the Saved message says when the finish changed with the printing
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
      var rm = el("button", "btn btn-danger", removeLabel(card));
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
      if (!isCustom(card)) {
        var editLink = el("a", "btn", "Edit in deck editor");
        editLink.href = "/decks/" + enc(deckId) + "/edit#card-" + enc(name);
        acts.push(editLink);
      }
    }
    if (isCustom(card)) { CardView.open(CardView.fromElement(card), acts); return; }  // no collection or Scryfall row for a custom card
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
  // true while the menu's own history.back() has not landed yet: a box the menu opened (Lock
  // personal categories, Report card…) must not take that popstate for a Back press (static/box.js)
  window.MtgDeckCardMenu = { backPending: function () { return ignorePop; } };
  function menuItem(text, ic, onClick, cls) {
    var b = el("button", cls || "", "");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.appendChild(ic ? icon(ic) : iconSlot());
    b.appendChild(document.createTextNode(text));
    if (onClick) b.addEventListener("click", function (e) { stop(e); onClick(); });
    return b;
  }
  // the gateway's own icons, rendered by the server into a template under #cards (theme.icon); a
  // row without one keeps an empty slot of the same width, so every row's text lines up
  function iconSlot() {
    var s = el("span", "ic-slot");
    s.setAttribute("aria-hidden", "true");
    return s;
  }
  function icon(name) {
    var tpl = $("template.icons", cards);
    var src = tpl ? tpl.content.querySelector("[data-ic='" + name + "'] svg") : null;
    return src ? src.cloneNode(true) : iconSlot();
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
    if (isCustom(card)) { customItems(m, card, name); } else {
    m.appendChild(menuItem("Open card", "eye", function () { closeMenu(); openViewer(card); }));
    if (document.body.hasAttribute("data-watchlist")) {
      // watch the card's price, or browse its printings and watch one (the picker's browse mode)
      var CardMenu = window.MtgCardMenu;
      if (CardMenu && CardMenu.isWatched(name)) {
        // watched already (any printing): the item opens its watchlist entry
        var onList = el("a", "", "");
        onList.href = "/watchlist?q=" + enc(name);
        onList.setAttribute("role", "menuitem");
        onList.appendChild(icon("check"));
        onList.appendChild(document.createTextNode("On your watchlist"));
        m.appendChild(onList);
      } else {
        m.appendChild(menuItem("Watch this card", "bookmark", function () { closeMenu(); watchCard(card, null); }));
      }
      if (window.MtgPrintPick) m.appendChild(menuItem("Art variants…", "image", function () { closeMenu(); artVariants(card); }));
    }
    // one copy into the collection, as the viewer's "I own this card" (the shared layer's call)
    var Shared = window.MtgMenu;
    if (Shared) m.appendChild(menuItem("Add to collection", "collection", function () { closeMenu(); Shared.collect(name); }));
    if (Shared && Shared.pin) m.appendChild(menuItem("Pin card", "pin", function () { closeMenu(); Shared.pin(name); }));
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
      // every row: a row of a card that has other rows (a maybeboard row, say) changes on its own
      if (window.MtgPrintPick) m.appendChild(menuItem("Change printing…", "image", function () { closeMenu(); changePrinting(card); }));
      if (window.MtgDeckTag) m.appendChild(menuItem("Color tag…", "tag", function () { closeMenu(); window.MtgDeckTag(card); }));
      m.appendChild(menuItem("Mana value…", "edit", function () { manaValueForm(card, m); }));
      // Archidekt's card menu switches: on shows a check, as the Move to list's current category
      var comp = menuItem("Companion", card.hasAttribute("data-companion") ? "check" : "", function () { switchRow(card, "companion"); }, card.hasAttribute("data-companion") ? "on" : "");
      comp.setAttribute("role", "menuitemcheckbox");
      comp.setAttribute("aria-checked", card.hasAttribute("data-companion") ? "true" : "false");
      m.appendChild(comp);
      if ((card.getAttribute("data-card") || "").indexOf("//") >= 0) {
        var back = menuItem("Show back face", card.hasAttribute("data-flipped") ? "check" : "", function () { switchRow(card, "back_face"); }, card.hasAttribute("data-flipped") ? "on" : "");
        back.setAttribute("role", "menuitemcheckbox");
        back.setAttribute("aria-checked", card.hasAttribute("data-flipped") ? "true" : "false");
        m.appendChild(back);
      }
      m.appendChild(menuItem(removeLabel(card), "x", function () { removeCard(card); }, "danger"));
      m.appendChild(el("div", "sep"));
      // Lock personal categories (deck_extras.py): the card's categories here become its default
      // categories on Archidekt; not for a custom card, which has none there
      if (window.MtgDeckExtras && document.getElementById("lock") && !card.hasAttribute("data-custom")) {
        m.appendChild(menuItem("Lock personal categories", "lock", function () {
          var cats = catsOf(card);
          closeMenu();
          window.MtgDeckExtras.open("lock", { card: name, cats: cats });
        }));
      }
      // the shared card menu's items on your own deck too (F6, M9): Add to deck… and Open on Scryfall
      var addLink = el("a", "", "");
      addLink.setAttribute("role", "menuitem");
      addLink.href = "/cards?q=" + enc(name);
      addLink.appendChild(icon("plus"));
      addLink.appendChild(document.createTextNode("Add to deck…"));
      m.appendChild(addLink);
      if (Shared) m.appendChild(menuItem("Copy name", "copy", function () { closeMenu(); Shared.copyAndSay(name, "Name copied."); }));
      var ownScry = scryfallLink(name, "");
      ownScry.setAttribute("role", "menuitem");
      ownScry.insertBefore(icon("external"), ownScry.firstChild);
      m.appendChild(ownScry);
      var editLink = el("a", "", "");
      editLink.setAttribute("role", "menuitem");
      editLink.href = "/decks/" + enc(deckId) + "/edit#card-" + enc(name);
      editLink.appendChild(icon("edit"));
      editLink.appendChild(document.createTextNode("Edit in deck editor"));
      m.appendChild(editLink);
    } else {
      if (Shared) m.appendChild(menuItem("Copy name", "copy", function () { closeMenu(); Shared.copyAndSay(name, "Name copied."); }));
      var scry = scryfallLink(name, "");
      scry.setAttribute("role", "menuitem");
      scry.insertBefore(icon("external"), scry.firstChild);
      m.appendChild(scry);
    }
    }
    // Report card… (deck_extras.py): to Archidekt's moderators, on any deck the member can read;
    // not for the member's own custom card in their own deck (Archidekt's menu hides it there too)
    if (window.MtgDeckExtras && document.getElementById("reportcard") && !(own && isCustom(card))) {
      m.appendChild(menuItem("Report card…", "flag", function () { closeMenu(); window.MtgDeckExtras.open("reportcard", { card: name }); }));
    }
    m.addEventListener("keydown", function (e) {
      var items = $$("[role=menuitem]:not([disabled]), [role=menuitemcheckbox]:not([disabled])", m).filter(function (b) { return b.offsetParent !== null; });
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
    clearStaleDrag();
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
  // A mouse drag the browser never finished (no dragend): the card no longer looks lifted and taps
  // on the cards work again. The touch drag below keeps its own state and is never cleared here.
  function clearStaleDrag() {
    if (!dragged || dragged !== nativeDrag) return;
    dragged.classList.remove("dragging");
    cards.classList.remove("carddrag");
    $$(".stack.dropping", cards).forEach(function (s) { s.classList.remove("dropping"); });
    dragged = null; nativeDrag = null;
  }
  var nativeDrag = null;
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
      if (isCustom(card)) { toast(CUSTOM_PICK); return; }
      var on = !card.classList.contains("picked");
      card.classList.toggle("picked", on);
      card.setAttribute("aria-pressed", on ? "true" : "false");
      refresh();
    };
    start.addEventListener("click", function () { setMode(true); });
    done.addEventListener("click", function () { setMode(false); });
    all.addEventListener("click", function () {
      $$(".c, .row", cards).forEach(function (c) {
        if (c.offsetParent === null || isCustom(c)) return;  // hidden by the filter or a collapsed stack; a custom card changes from its own menu
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
    // a remove or a move of a row a card shares its zone with names that row (rowRel): one each
    function byRow(c) { return byNameZone(c) + "|" + (rowRel(c) || ""); }
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
      var list = unique(picked(), byRow);
      var changes = [], inverses = [];
      list.forEach(function (c) {
        var ch = changeFor(c, "remove");
        changes.push(ch);
        // with two rows of the card in that zone (two printings or categories) an Undo could
        // not put them back as they were, so none is offered
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
      var list = unique(picked().filter(function (c) { return (byCategory ? stackOf(c) : catOf(c)) !== to; }), byRow);
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
  // A finger never starts the browser's own drag: Chrome on Android starts one from a press and
  // hold on a draggable card, and when the finger lets go without moving it opens the card's menu
  // but sends no dragend, so the card stayed faded and every later tap on a card was refused as
  // the end of a drop. A touch press and hold is the touch code's below (the menu, or slide to
  // move), so the card is not draggable under a finger or a pen and a drag one starts is refused.
  $$(".c, .row", cards).forEach(function (c) { c.setAttribute("draggable", "true"); });
  var touchPress = false;
  cards.addEventListener("pointerdown", function (e) {
    touchPress = e.pointerType !== "mouse";  // a finger or a pen (an S Pen press and hold too)
    clearStaleDrag();
    var card = e.target.closest(".c, .row");
    if (card) card.setAttribute("draggable", touchPress ? "false" : "true");
  }, true);
  cards.addEventListener("dragstart", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card) return;
    if (picking || touchPress) { e.preventDefault(); return; }
    closeMenu();
    dragged = card;
    nativeDrag = card;
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
    nativeDrag = null;
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
    // a hold whose touch never ended (no touchend or touchcancel came) is over now
    if (armed && armed !== card && !touchCard) { armed.classList.remove("held"); armed = null; }
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
  // gateway could find, so the order is kept on the member's account (0.7.29, prefs.js), per deck
  // and per grouping: it is not a deck save and has no Undo; Reset puts the deck's order back.
  if (cards && cards.classList.contains("deckview")) (function () {
    var prefs = window.MtgPrefs;
    var key = "stack:" + cards.getAttribute("data-deck") + ":" + (cards.getAttribute("data-grouping") || "");
    function stacksNow() { return $$(".stack", cards); }
    function nameOf(st) { return st.getAttribute("data-group") || ""; }
    function save() {
      if (prefs) prefs.set(key, stacksNow().map(nameOf));
      showReset();
    }
    function saved() {
      var v = prefs ? prefs.get(key) : null;
      return Array.isArray(v) ? v : null;
    }
    var reset = null;
    function showReset() {
      if (!reset) {
        reset = el("button", "btn-ghost small stack-reset", "Reset stack order");
        reset.type = "button";
        reset.title = "Your stack order is kept with your account, not on Archidekt; " +
          "this puts the deck's own order back";
        reset.addEventListener("click", function () {
          if (prefs) prefs.set(key, null);
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
        "the order is kept with your account)");
      grip.title = "Drag to reorder (kept with your account); arrow keys move it too";
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
   has launched once (kept on the account from 0.7.29), then sit folded in the same box. The box
   closes with ×, Escape or a press outside it (there is no Cancel button). Sideboard cards left
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
  // set once the member has launched from the box; kept on the account (0.7.29, prefs.js)
  var STEPS_SEEN = "edhplay.steps";
  function stepsSeen() { return !!(window.MtgPrefs && window.MtgPrefs.get(STEPS_SEEN) === true); }
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
    // a note left from an earlier launch would sit above the box: the box says it all again
    var old = document.querySelector(".edhplay-toast");
    if (old) old.remove();
    // focus goes back to what was pressed: the link, or the Test and play button whose menu
    // pressed it (the link itself is in the drop-down the button replaces)
    back = link.getClientRects().length ? link : (document.querySelector(".banner .decktestbtn") || link);
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
      if (window.MtgPrefs && !stepsSeen()) window.MtgPrefs.set(STEPS_SEEN, true);
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

// -- the deck menu and the deck page's boxes (M9, F3) ------------------------------------------
// The banner's ⋮ More button, a right-click anywhere on the banner or a press and hold opens the
// shared deck menu (cardmenu.js's engine): the same items as a tile on My decks plus the old
// More's page-only items. Rename… and Move to folder… are boxes here (the JSON routes of
// companion_folders.py); Clone…, Export…, Compare with… and Delete deck… adopt the boxes the
// page holds (ui.box). Without scripts the <details> drop-down and the real links stay.
(function () {
  "use strict";
  var Menu = window.MtgMenu, Box = window.MtgBox;
  var banner = document.querySelector(".banner[data-deck-id]");
  if (!Menu || !Box || !banner) return;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var el = Menu.el, item = Menu.item, link = Menu.link, sep = Menu.sep, enc = encodeURIComponent;
  var d = {
    id: banner.getAttribute("data-deck-id"),
    name: banner.getAttribute("data-deck-name") || "Unnamed deck",
    own: banner.getAttribute("data-own") === "1",
    can: banner.getAttribute("data-can") === "1",
    forge: banner.getAttribute("data-forge") === "1",
    // a new deck whose cards are still going in (decks.being_made): no edits until it is done
    making: banner.getAttribute("data-making") === "1"
  };
  var more = $("details.deckmore", banner);
  var btn = el("button", "btn deckmenu", "");
  btn.type = "button";
  btn.setAttribute("aria-haspopup", "menu");
  btn.setAttribute("aria-label", "More: the deck menu");
  btn.appendChild(Menu.icon("more"));
  btn.appendChild(document.createTextNode(" More"));
  if (more) more.parentNode.insertBefore(btn, more); else $(".actions", banner) && $(".actions", banner).appendChild(btn);
  banner.setAttribute("data-menu-ready", "");

  function openBox(id) { return function () { Box.openElement(id, { returnFocus: btn }); }; }
  function submit(sel) {
    return function () {
      var f = $(sel, banner);
      if (!f) return;
      Menu.whenClosed(function () { if (f.requestSubmit) f.requestSubmit(); else f.submit(); });
    };
  }
  function go(href) { Menu.whenClosed(function () { location.assign(href); }); }
  function cancelButton(text) { return Box.button(text || "Cancel", "btn", null, { cancel: true }); }
  function failed(box, r) { box.busy(false); box.note((r && r.message) || "The gateway could not be reached. Check your connection and try again.", true); }

  function renameDeck() {
    var input = el("input");
    input.type = "text"; input.value = d.name; input.autocomplete = "off"; input.maxLength = 200; input.id = "dk-name";
    var wrap = el("div", "field");
    var lab = el("label", null, "New name"); lab.htmlFor = "dk-name";
    wrap.appendChild(lab); wrap.appendChild(input);
    var ok = Box.button("Rename", "primary", function () {
      var name = input.value.trim();
      if (!name) { box.note("Type a name.", true); input.focus(); return; }
      box.busy(true);
      Menu.post("/decks/api/decks/" + enc(d.id) + "/rename", { name: name }).then(function (r) {
        if (!r.ok) return failed(box, r);
        go("/decks/" + enc(d.id) + "?ok=saved");
      }, function () { failed(box); });
    }, { icon: "check" });
    var box = Box.open({ title: "Rename deck", body: wrap, actions: [cancelButton(), ok], returnFocus: btn });
    input.select();
  }
  function moveDeck() {
    var p = el("p", "muted loading", "Reading from Archidekt…");
    p.setAttribute("role", "status");
    var box = Box.open({ title: "Move “" + d.name + "”", body: p, actions: [cancelButton()], returnFocus: btn });
    Menu.getJSON("/decks/api/folders").then(function (info) {
      if (!info.ok) return failed(box, info);
      var sel = el("select"); sel.id = "dk-folder";
      (info.folders || []).forEach(function (f) {
        var o = el("option", null, (f.depth ? " ".repeat(f.depth) : "") + (f.name || ""));
        o.value = String(f.id);
        sel.appendChild(o);
      });
      var wrap = el("div", "field");
      var lab = el("label", null, "Folder"); lab.htmlFor = "dk-folder";
      wrap.appendChild(lab); wrap.appendChild(sel);
      var ok = Box.button("Move", "primary", function () {
        box.busy(true);
        Menu.post("/decks/api/decks/" + enc(d.id) + "/move", { folder_id: sel.value }).then(function (r) {
          if (!r.ok) return failed(box, r);
          go("/decks/" + enc(d.id) + "?ok=moved");
        }, function () { failed(box); });
      }, { icon: "folder" });
      box.setBody(wrap);
      box.setActions([cancelButton(), ok]);
      box.focusFirst();
    }, function () { failed(box); });
  }

  // A group's name in a menu (This deck, Look into it, ...)
  function head(text) { return el("div", "ghead", text); }
  // The deck menu, the same on every screen (Ben, 2026-10-25): what the banner's buttons do (Edit
  // deck, Clone deck, Test and play) is not repeated here, and the deck's proposals are the
  // banner's Proposals pill. Items that change the deck show on your own decks only, and not
  // while the deck is still being made (its page says so; the gateway would refuse them).
  function items() {
    var id = enc(d.id), out = [], edits = d.own && !d.making;
    if (edits) {
      out.push(head("This deck"));
      out.push(item("Rename…", "edit", renameDeck));
      out.push(item("Move to folder…", "folder", moveDeck));
      if (d.can && document.getElementById("package")) out.push(item("Add a package…", "package", extras("package")));
      out.push(link("Deck settings", "settings", "/decks/" + id + "/settings"));
      out.push(sep());
    }
    out.push(head("Look into it"));
    out.push(link("Deck stats", "report", "/decks/" + id + "#stats"));
    out.push(link("Deck reports", "stats", "/reports?deck_id=" + id));
    if (d.can) out.push(item("Compare with…", "layers", openBox("compare")));
    out.push(link("History and snapshots", "history", "/history?deck_id=" + id));
    if (Menu.enabled && Menu.enabled() && window.MtgDeckWatch) out.push(item("Watch deck price", "eye", function () { window.MtgDeckWatch(d.id); }));
    out.push(sep());
    out.push(head("Share and print"));
    out.push(item("Share…", "send", share));
    if (d.can) out.push(item("Export…", "download", openBox("export")));
    if (d.can && document.getElementById("pdf")) out.push(item("Registration PDF…", "pdf", extras("pdf")));
    out.push(link("Open on Archidekt", "external", "https://archidekt.com/decks/" + id, { ext: true }));
    var community = [];
    if (d.own && d.can && document.getElementById("help")) {
      community.push(item("Request deck help…", "hand", extras("help")));
      community.push(item("Enter in a contest…", "trophy", extras("contest")));
      community.push(item("Withdraw from a contest…", "trophy", extras("withdraw")));
    }
    if (d.can && document.getElementById("report")) community.push(item("Report deck…", "flag", extras("report")));
    if (community.length) { out.push(sep()); out.push(head("Community")); out = out.concat(community); }
    if (edits) {
      out.push(sep());
      out.push(item("Delete deck…", "trash", openBox("delete"), { danger: true }));
    }
    return out;
  }

  // Share: in the app, the phone's own share sheet (MainActivity's MtgNative.share, with Open on
  // Archidekt, Copy gateway link and Copy deck list as its own actions); in a browser, a box with
  // the deck's links and list, and the system's share sheet where the browser has one.
  function deckList() {
    return fetch("/decks/" + enc(d.id) + "/edhplay.json", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (j) { return j && j.ok && typeof j.list === "string" ? j.list : ""; }, function () { return ""; });
  }
  function share() {
    var gw = location.origin + "/decks/" + enc(d.id), ar = "https://archidekt.com/decks/" + enc(d.id);
    var N = window.MtgNative;
    if (N && typeof N.share === "function") {
      deckList().then(function (list) { N.share(d.name, ar, gw, list); });
      return;
    }
    var rows = el("div", "sharebox");
    function row(text, ic, note, run) {
      var b = el("button", "btn sharerow", "");
      b.type = "button";
      b.appendChild(Menu.icon(ic));
      b.appendChild(el("span", "t", text));
      if (note) b.appendChild(el("small", "", note));
      b.addEventListener("click", run);
      rows.appendChild(b);
    }
    if (navigator.share) row("Share…", "send", "", function () {
      navigator.share({ title: d.name, text: d.name, url: ar }).catch(function () { /* closed */ });
    });
    row("Copy gateway link", "link", "for members", function () { Menu.copyAndSay(gw, "Gateway link copied."); });
    row("Copy Archidekt link", "link", "anyone can open it", function () { Menu.copyAndSay(ar, "Archidekt link copied."); });
    row("Copy deck list", "copy", "EDHPlay and most sites", function () {
      deckList().then(function (list) {
        if (list) Menu.copyAndSay(list, "Deck list copied.");
        else Menu.toast("The deck list couldn't be loaded. Use Export instead.");
      });
    });
    Box.open({ title: "Share “" + d.name + "”", body: rows, actions: [cancelButton("Close")], returnFocus: btn });
  }

  // Test and play: the banner's drop-down becomes the shared menu (a sheet on a phone)
  var testDd = $("details.decktest", banner);
  var testBtn = null;
  if (testDd) {
    testBtn = el("button", "btn decktestbtn", "");
    testBtn.type = "button";
    testBtn.setAttribute("aria-haspopup", "menu");
    testBtn.appendChild(Menu.icon("play"));
    testBtn.appendChild(document.createTextNode(" Test and play"));
    testDd.parentNode.insertBefore(testBtn, testDd);
    testBtn.addEventListener("click", function () {
      var id = enc(d.id), t = [head("On the gateway"), link("Goldfish", "layers", "/decks/" + id + "/goldfish")];
      if (d.can) t.push(item("Run full test", "stats", submit("#fullf")));
      if (d.forge) t.push(item("Solo test only", "stats", submit("#solof")));
      t.push(sep());
      t.push(head("On other sites"));
      t.push(link("Playtest on Archidekt", "play", "https://archidekt.com/playtester-v2/" + id, { ext: true }));
      var edh = $("a[data-edhplay]", testDd);
      if (edh) t.push(item("Play on EDHPlay", "play", function () { Menu.whenClosed(function () { edh.click(); }); }));
      var r = testBtn.getBoundingClientRect();
      Menu.open({ head: "Test and play", label: "Test and play: " + d.name, items: t, focusBack: testBtn }, r.left, r.bottom + 4);
    });
  }

  function extras(which) {
    return function () {
      var X = window.MtgDeckExtras;
      if (X) X.open(which, { returnFocus: btn });
      else go("/decks/" + enc(d.id) + "/" + ({ pdf: "registration", report: "report-to-archidekt" }[which] || which));
    };
  }
  function open(_it, x, y) {
    Menu.open({ head: d.name, label: "Deck: " + d.name, items: items(), focusBack: btn }, x, y);
  }
  Menu.bind(banner, {
    itemOf: function (n) { return n && n.closest ? n.closest(".banner[data-deck-id]") : null; },
    skip: "a,button,form,input,select,textarea",
    button: ".deckmenu",
    open: open
  });

  // Export box: the tabs show one pane at a time (every pane shows without scripts)
  var xb = $("#export");
  if (xb) xb.addEventListener("click", function (e) {
    var t = e.target.closest("[role=tab][data-tab]");
    if (!t) return;
    $$("[role=tab]", xb).forEach(function (o) { o.setAttribute("aria-selected", o === t ? "true" : "false"); });
    $$(".xpane", xb).forEach(function (p) { p.hidden = p.getAttribute("data-pane") !== t.getAttribute("data-tab"); });
  });
  // Delete box: the solid red button waits for the deck's name
  var typed = $("#delete #typed"), del = $("#delete .acts button.danger");
  if (typed && del) {
    var gate = function () { del.disabled = typed.value.trim().toLowerCase() !== (typed.getAttribute("data-typed") || "").trim().toLowerCase(); };
    typed.addEventListener("input", gate);
    gate();
  }
  // Compare box: your other decks are offered as you type (the gateway reads a deck ID or name)
  var cw = $("#compare #cmp-with");
  if (cw && d.can) {
    var listed = false;
    cw.addEventListener("focus", function () {
      if (listed) return;
      listed = true;
      Menu.getJSON("/api/decks/mine").then(function (mine) {
        var dl = el("datalist"); dl.id = "cmp-mine";
        ((mine && mine.decks) || []).forEach(function (x) {
          if (String(x.id) === String(d.id)) return;
          var o = el("option", null, x.name || "Unnamed deck");
          o.value = String(x.id);
          dl.appendChild(o);
        });
        cw.parentNode.appendChild(dl);
        cw.setAttribute("list", "cmp-mine");
      }, function () { /* the box still takes a name, link or ID */ });
    });
  }
})();
