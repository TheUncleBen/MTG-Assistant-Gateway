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
  // A form marked data-live (My decks) applies its own changes in place (decks.js): sending it as
  // well would read the list twice for one change.
  $$("#viewform, #listform").forEach(function (form) {
    if (form.hasAttribute("data-live")) return;
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
  // A list row's hover picture (ul.rows .hover) is fixed to the screen and placed by its row as
  // the pointer arrives: each stack clips its overflow (deckpage.py, so a change to one row never
  // relays out the whole column view), which would clip the picture inside the stack.
  // The picture stays on screen (clamped to the viewport) and follows its row on scroll.
  var hoverRow = null;
  var placeHover = function () {
    var pic = hoverRow ? hoverRow.querySelector(".hover") : null;
    if (!pic) return;
    var r = hoverRow.getBoundingClientRect();
    var h = pic.offsetHeight || 312;
    pic.style.position = "fixed";
    pic.style.left = (r.left + 28) + "px";
    pic.style.top = Math.max(8, Math.min(r.top + 30, window.innerHeight - h - 8)) + "px";
  };
  cards.addEventListener("mouseover", function (e) {
    var row = e.target && e.target.closest ? e.target.closest("li.row") : null;
    hoverRow = row && cards.contains(row) ? row : null;
    placeHover();
  });
  cards.addEventListener("mouseleave", function () { hoverRow = null; });
  window.addEventListener("scroll", placeHover, { passive: true });
  // a phone, a touch screen in the rail range (to 1279.98 px, as theme.py draws the rail) or the app
  var phone = function () {
    return window.matchMedia("(max-width: 599.98px)").matches ||
      window.matchMedia("(max-width: 1279.98px) and ((pointer: coarse) or (hover: none))").matches ||
      document.body.classList.contains("app");
  };
  var enc = encodeURIComponent;
  // Mockup screen 3, for the owner: on a narrow phone the card menu's "More" group starts folded, so the
  // count, the main rows and Remove fit one screen; false shows it open, as on wider screens.
  var MORE_FOLDED_ON_PHONE = false;
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
    // a card menu's note (Pin card, Watch) may be up too: this one sits clear of it
    if (window.MtgMenu && window.MtgMenu.placeToast) window.MtgMenu.placeToast(toastEl);
    if (!opts.error && !opts.sticky) toastTimer = setTimeout(dismissToast, opts.ms || 9000);
    return toastEl;
  }

  // -- the save: the deck page's own edit endpoint (api.py), one proposal applied at once --------
  // through the shared MtgFetch (static/fetchjson.js): a session that ended rejects with
  // err.signedOut (the toast then offers Sign in, which comes back here) and a lost connection
  // with err.noConnection, each in the site's one wording
  var Fetch = window.MtgFetch;
  function api(path, body) {
    return Fetch.json(path, { method: "POST", body: JSON.stringify(body), csrf: CSRF });
  }
  function signInAction() { return Fetch.signInLink("btn"); }
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
    // the pop-up box every risky confirm uses (audit F-7: a toast with Save anyway focused): the
    // safe choice first and focused, the red button last; closing it any other way declines
    return new Promise(function (resolve) {
      var answered = false;
      window.MtgBox.confirm({
        title: "Save anyway?", text: why, ok: "Save anyway", danger: true, cancel: "Keep it", focus: "cancel",
        onOk: function () { answered = true; asking = null; resolve(true); },
        onClose: function () { if (!answered) { decline(); resolve(false); } }
      });
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
    // the page's view, grouping and sort: a card the answer adds comes drawn for them (quick add)
    var vf = $("#viewform");
    body.view = vf && vf.elements.view ? vf.elements.view.value : "";
    body.group = grouping || "category";
    body.sort = vf && vf.elements.sort ? vf.elements.sort.value : "name";
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
      failed.code = d.error || "";
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
    toast((err && err.message) || "No connection; nothing was changed.", { error: true, actions: err && err.signedOut ? [signInAction()] : [] });
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
      if (err && (err.cancelled || err.signedOut || err.noConnection)) throw err;
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
      toast("Not saved: " + whyNot(err) + ".", { error: true, actions: err && err.signedOut ? [signInAction()] : [] });
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
  // the menu's items for a custom card, in the same shape as a printed card's: the count in the
  // head, then Open card, Move to and Color tag…, Mana value… and Copy name under More, and the
  // removal last. No Printings…, Watch or Add to binder (a custom card has no printings or
  // price), and no Lock categories: Archidekt keeps no default categories for a custom card (the
  // endpoint says so, CUSTOM_LOCK, should a tool ask).
  function customItems(m, card, name) {
    var edits = own && deckId;
    menuHead(m, card, name, edits ? customStep : null);
    m.appendChild(menuItem("Open card", "eye", function () { closeMenu(); openViewer(card); }));
    if (!edits) {
      if (window.MtgMenu) m.appendChild(menuItem("Copy name", "copy", function () { closeMenu(); window.MtgMenu.copyAndSay(name, "Name copied."); }));
      return;
    }
    moveToItems(m, card, customMove);
    m.appendChild(menuItem("Color tag…", "tag", function () { customTagForm(card, m); }));
    var more = [menuItem("Mana value…", "edit", function () { manaValueForm(card, m); })];
    if (window.MtgMenu) more.push(menuItem("Copy name", "copy", function () { closeMenu(); window.MtgMenu.copyAndSay(name, "Name copied."); }));
    appendGroup(m, "More", more, MORE_FOLDED_ON_PHONE && narrow());
    m.appendChild(el("div", "sep"));
    m.appendChild(menuItem(removeLabel(card), "x", function () { customRemove(card); }, "danger"));
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
  var NO_RETRY = ["not_found", "unknown_cards", "invalid", "forbidden", "too_large", "not_linked"];
  // why a save did not go through, as the end of a sentence ("Add not saved: …")
  function whyNot(err) {
    if (err && err.signedOut) return "you're signed out";
    if (!err || err.noConnection || !err.message) return "no connection, nothing was changed";
    return err.message.replace(/\.$/, "");
  }
  function notSaved(plan, job, err, again) {
    var back = again ? "" : job.undo();
    if (err && err.cancelled) { dismissToast(); return; }
    if (err && err.answer) {
      // the card is back as it was; when the rows Archidekt shows now draw it the same, the
      // change was not taken (a refusal after the request went): the plain failure, with Try
      // again. Otherwise it was, or the page cannot tell, and the rows say it.
      var names = namesOf(plan.changes), before = lookOf(plan.changes);
      var took = landed(plan.changes, err.answer.rows);
      drawRows(err.answer.rows, names, addsOf(plan.changes));
      drawStats(err.answer);
      if (menu && !menuCard.isConnected) closeMenu();
      if (took === null ? lookOf(plan.changes) !== before : took) {
        toast("Archidekt didn't answer in time; this is what the deck shows now.", { warn: true, sticky: true });
        return;
      }
    }
    var said = job.what + " not saved: " + whyNot(err) + "." + (back ? " " + back : "");
    // signed out: Try again cannot help; Sign in comes back to this page
    if (err && err.signedOut) { toast(said, { error: true, actions: [signInAction()] }); return; }
    // a refusal of the change itself (no such card, a change the deck can't take) would fail the
    // same way again: no Try again then; a name that is no card gets a card search instead
    if (err && NO_RETRY.indexOf(err.code) >= 0) {
      var acts = [];
      var added = (plan.changes || []).filter(function (ch) { return ch.action === "add"; })[0];
      if (added && err.code === "not_found" || added && err.code === "unknown_cards") {
        var find = el("a", "btn", "Search cards");
        find.href = "/cards?q=" + enc(added.card_name);
        acts.push(find);
      }
      toast(said, { error: true, actions: acts });
      return;
    }
    var retry = el("button", "btn", "Try again");
    retry.type = "button";
    retry.addEventListener("click", function () { dismissToast(); sendAgain(plan, job); });
    toast(said, { error: true, actions: [retry] });
  }
  // How the cards a change names look on the page, in the fields that change sets: the same
  // before and after a draw means the draw did not take the change
  // Whether the rows Archidekt shows now carry the changes: true, false, or null when the row's
  // own fields say nothing sure about that kind of change (a companion or back-face switch)
  function landed(changes, rows) {
    var known = true, took = true;
    changes.forEach(function (ch) {
      var zone = ch.zone === "side" ? "side" : "main";
      var row = (rows || []).filter(function (r) {
        return frontFace(r.name) === frontFace(ch.card_name) && (ch.relation_id ? String(r.relation_id) === String(ch.relation_id) : r.zone === zone);
      })[0] || null;
      var low = function (v) { return String(v || "").toLowerCase(); };
      if (ch.action === "set_quantity") took = took && !!row && row.qty === ch.quantity;
      else if (ch.action === "remove") took = took && !row;
      else if (ch.action === "add") took = took && !!row;
      else if (ch.action === "set_category") took = took && !!row && (row.categories || [])[0] === ch.category;
      else if (ch.action === "set_finish") took = took && !!row && low(row.finish) === low(ch.finish);
      else if (ch.action === "set_printing") took = took && !!row && low(row.set_code) === low(ch.set_code) && String(row.collector_number) === String(ch.collector_number);
      else if (ch.action === "set_label") took = took && !!row && (row.label || "") === (ch.label || "");
      else if (ch.action === "set_mana_value") took = took && !!row && (row.mana_value === null || row.mana_value === undefined ? null : Number(row.mana_value)) === (ch.mana_value === null || ch.mana_value === undefined ? null : Number(ch.mana_value));
      else known = false;
    });
    return known ? took : null;
  }
  // the card fields each kind of change sets; an unknown kind compares them all
  var LOOK_ATTRS = ["data-qty", "data-zone", "data-cat", "data-allcats", "data-notes", "data-finish", "data-set",
    "data-label", "data-mv", "data-companion", "data-flipped"];
  var LOOK_OF = {
    remove: [], add: ["data-qty"], set_quantity: ["data-qty"], set_category: ["data-cat", "data-zone"],
    set_finish: ["data-finish"], set_printing: ["data-set", "data-finish"], set_label: ["data-label"],
    set_mana_value: ["data-mv"], set_companion: ["data-companion"], set_back_face: ["data-flipped"]
  };
  function lookOf(changes) {
    return changes.map(function (ch) {
      var face = frontFace(ch.card_name), attrs = LOOK_OF[ch.action] || LOOK_ATTRS;
      return $$(".c, .row", cards).filter(function (c) { return frontFace(c.getAttribute("data-card")) === face; })
        .map(function (c) { return [zoneOf(c)].concat(attrs.map(function (a) { return c.getAttribute(a); })).join("\u0001"); }).join("\n");
    }).join("\n\n");
  }
  // -- Quick add: one copy of the typed card, saved at once and drawn in place (owner decision
  // C4); its Undo takes the copy back out. An unknown name is refused in the red note; the page
  // never leaves. Without scripts the form opens the editor with the name filled in.
  // With no name, the box keeps the focus and says so, as a required field does (the browser's
  // own note at the field, static/suggest.js's needName, shared with the editor's Add), until
  // something is typed; the form itself is never held back by it.
  var quickForm = own ? $("form.quick") : null;
  function needName(input, need) { window.MtgSuggest.needName(input, need); }
  if (quickForm && $("#quick")) {
    quickForm.noValidate = true;
    $("#quick").addEventListener("input", function (e) { if (e.target.value.trim()) needName(e.target, false); });
    quickForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = $("#quick"), name = (input.value || "").trim();
      needName(input, !name);
      if (!name) {
        input.focus();
        if (input.reportValidity) input.reportValidity();
        return;
      }
      quickAdd(name, input);
    });
  }
  function quickAdd(name, input) {
    var face = frontFace(name);
    background({
      card: null, what: "Add",
      start: function () {
        var had = $$(".c, .row", cards).filter(function (c) { return zoneOf(c) === "main" && frontFace(c.getAttribute("data-card")) === face; });
        var inv;
        if (!had.length) inv = { action: "remove", card_name: name };
        else if (had.length === 1) inv = { action: "set_quantity", card_name: had[0].getAttribute("data-card"), quantity: qtyOf(had[0]) };
        // several rows of it in the deck: an Undo would count them all, so none is offered
        return {
          changes: [{ action: "add", card_name: name, quantity: 1 }], inverse: inv ? [inv] : [],
          saying: "Adding " + name + "…", note: "Added " + name + ". Snapshot kept under History."
        };
      },
      saved: function () {
        if ((input.value || "").trim() !== name) return;
        input.value = "";
        input.dispatchEvent(new Event("input", { bubbles: true }));  // the suggestion list closes
      },
      undo: function () { return ""; }
    });
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
    head.appendChild(el("div", "meta", "0 cards"));
    st.appendChild(head);
    var rows = cards.classList.contains("text") || cards.classList.contains("table") || cards.classList.contains("brewer");
    var list = rows ? el("ul", "plain rows") : el("div", "cards");
    var th = $(".rows > .th", cards);  // the Table view's column heads
    if (th) list.appendChild(th.cloneNode(true));
    st.appendChild(list);
    return st;
  }
  function ensureStack(name, any) {
    var st = stackByName(name);
    if (st || (!byCategory && !any)) return st;
    st = makeStack(name);
    // the maybeboard sits last, as on Archidekt; a new category goes before it. Under another
    // grouping (any: a quick add's new stack) it goes before the first stack of side cards.
    var side = byCategory ? excluded.map(stackByName).filter(Boolean)[0] : $$(".stack", cards).filter(function (x) {
      var first = $(".c, .row", x);
      return first && zoneOf(first) === "side";
    })[0];
    if (side && excluded.indexOf(name) < 0) cards.insertBefore(st, side); else cards.appendChild(st);
    return st;
  }
  // a card the answer brings that the page never had (a quick add): drawn from its own markup
  function newCard(r) {
    if (!r.html) return null;
    var t = document.createElement("template");
    t.innerHTML = r.html;
    var card = t.content.firstElementChild;
    if (!card) return null;
    if (canDrag) card.setAttribute("draggable", "true");
    return card;
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
  // The card menu's Finish list (Normal, Foil, Etched; owner decision C4): the badge changes at
  // once and the save follows; Undo puts the old finish back. The gateway refinishes every copy
  // of the card in the deck itself (set_finish), so the Undo is offered only when all of them
  // had the same finish.
  var FINISH_CHOICES = ["Normal", "Foil", "Etched"];
  function finishOf(card) {
    var f = (card.getAttribute("data-finish") || "Normal").toLowerCase();
    return f.charAt(0).toUpperCase() + f.slice(1);
  }
  function setFinishOf(card, to) {
    var name = card.getAttribute("data-card") || "", face = frontFace(name);
    var cur = finishOf(card);
    if (cur === to) return;
    var copies = $$(".c, .row", cards).filter(function (c) { return zoneOf(c) === "main" && frontFace(c.getAttribute("data-card")) === face; });
    var olds = {};
    copies.forEach(function (c) { olds[finishOf(c)] = 1; });
    var ch = changeFor(card, "set_finish");
    ch.finish = to.toLowerCase();
    var inv = Object.keys(olds).length === 1 ? { action: "set_finish", card_name: name, finish: cur.toLowerCase() } : null;
    if (inv && ch.relation_id) inv.relation_id = ch.relation_id;
    var saying = "Saving " + name + ": " + to.toLowerCase() + "…";
    var held = holdConfirmed(card, "Finish", function () { return cur; }), gen = held.gen;
    held.queued++;
    copies.forEach(function (c) { setFinish(c, to); });
    closeMenu();
    savingNote(saying, false, card, "Finish");
    background({
      card: card, what: "Finish",
      start: function () {
        return held.gen === gen ? { changes: [ch], inverse: inv ? [inv] : null, saying: saying, note: "Saved. " + name + " is " + to.toLowerCase() + " now. Snapshot kept under History." } : null;
      },
      saved: function () { held.state = to; },
      undo: function () {
        held.gen++;
        copies.forEach(function (c) { setFinish(c, held.state); });
        return name + " is " + held.state.toLowerCase() + " again." +
          droppedNote(held, "Your later finish change was not sent.", "Your later finish changes were not sent.");
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
      if (meta) meta.textContent = qty + (qty === 1 ? " card" : " cards") + (priced && price ? " · " + money(price) : "");
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
      // the phone's short count ("100 cards", deckpage.banner_html)
      else if (/^[\d,]+ cards?$/.test(t) && sp.classList.contains("sm") && st.card_count !== undefined) sp.textContent = window.MtgText ? window.MtgText.plural(st.card_count, "card") : st.card_count + " cards";
      else if (/^Est cost:/.test(t) && st.price_total !== undefined && st.price_total !== null) { var b = $("b", sp); if (b) setCost(b, st); }
      else if (/^Salt sum:/.test(t) && st.salt_total !== undefined && st.salt_total !== null) { var s2 = $("b", sp); if (s2) s2.textContent = st.salt_text || Number(st.salt_total).toFixed(1); }
    });
    // the deck's date, as Archidekt stamped this save (deckpage.updated_when)
    var when = $(".banner .row > span > time.ui-when");
    if (when && d.updated_html) {
      when.outerHTML = d.updated_html;
      if (window.MtgText && window.MtgText.refreshTimes) window.MtgText.refreshTimes();
    }
    var tiles = $$(".stats .tiles .tile");
    if (tiles[0] && st.card_count !== undefined) $("b", tiles[0]).textContent = String(st.card_count);
    if (tiles[1] && st.land_count !== undefined) $("b", tiles[1]).textContent = String(st.land_count);
    if (tiles[2] && st.price_total !== undefined && st.price_total !== null) setCost($("b", tiles[2]), st);
  }
  // the deck's cost as the page draws it on a load (deckpage.est_cost_html): a dash while no card
  // has a price, never "$0.00"
  // no card priced: a muted "unknown" with its tooltip, as deckpage.est_cost_html draws it (polish C7)
  function setCost(b, st) {
    if (!b) return;
    var cost = b.closest(".banner .cost");
    if (cost) cost.hidden = st.priced_cards === 0;  // deckpage.banner_html leaves an unknown cost out
    b.textContent = "";
    if (st.priced_cards === 0) {
      var u = document.createElement("span");
      u.className = "muted unpriced"; u.title = "No prices from Archidekt yet"; u.textContent = "unknown";
      b.appendChild(u);
    } else b.textContent = money(st.price_total);
  }
  // adds: the names this save added (a quick add): a row of one of them with no card on the page
  // is drawn from its markup; any other such row is a card a pending edit holds off the page
  function drawRows(rows, names, adds) {
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
    // rows with no card on the page: a card an Undo put back (its element was kept), or one
    // the answer brings drawn (a quick add), in the stack the answer names
    left.forEach(function (r) {
      var kept = removedCards[frontFace(r.name)];
      var was = kept && kept.shift();
      var card = was ? was.card : (adds && adds.indexOf(frontFace(r.name)) >= 0 ? newCard(r) : null);
      if (!card) return;
      var home = (r.categories && r.categories[0]) || (was && was.home);
      var st = (r.stack && (ensureStack(r.stack, true) || stackByName(r.stack)))
        || (home && (ensureStack(home) || stackByName(home))) || (was && stackByName(was.home)) || $(".stack", cards);
      if (!st) return;
      var list = $(".cards, .rows", st), vf = $("#viewform");
      var byName = !vf || !vf.elements.sort || vf.elements.sort.value === "name";
      // a new card takes its place in the alphabet when that is the sort; otherwise the end
      var next = byName && !was ? $$(".c, .row", list).filter(function (c) {
        return (c.getAttribute("data-name") || "") > (card.getAttribute("data-name") || "");
      })[0] : null;
      list.insertBefore(card, next || null);
      place(card, r);
    });
    refreshStacks();
  }
  function addsOf(changes) {
    return changes.filter(function (ch) { return ch.action === "add"; }).map(function (ch) { return frontFace(ch.card_name); });
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
    // saved, but the deck now breaks a rule it kept (a second copy in a singleton format): said,
    // with Undo, and the note stays until dismissed
    var warns = (d.warnings || []).filter(function (w) { return typeof w === "string" && w; });
    var how = warns.length ? { warn: true, sticky: true } : {};
    if (warns.length) saved += " " + warns.join(" ");
    var names = namesOf(changes);
    drawRows(d.rows, names, addsOf(changes));
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
          toast(saved, Object.assign({ actions: actions }, how));
        }).catch(function () { toast(saved, Object.assign({ actions: actions }, how)); });
      }, 1500);
      return;
    }
    toast(saved, Object.assign({ actions: actions }, how));
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
      var one = noted.length === 1, answered = false;
      var who = one ? "“" + noted[0].getAttribute("data-card") + "” has a card note" : noted.length + " of these cards have card notes";
      window.MtgBox.confirm({
        title: one ? "Remove the card and its note?" : "Remove the cards and their notes?",
        text: who + " on Archidekt. Removing deletes the note for good: Undo puts the card back without it (the gateway cannot write notes).",
        ok: "Remove anyway", danger: true, cancel: one ? "Keep it" : "Keep them", focus: "cancel",
        onOk: function () { answered = true; resolve(true); },
        onClose: function () { if (!answered) resolve(false); }
      });
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
      toast(d.message || (w.name || name) + " added to your watchlist.", { actions: [open, undo] });
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
    // the printing the card already has, in another finish (the detail's Finish choice): the
    // finish alone changes
    if (info.onlyFinish) { setFinishOf(card, info.finish.charAt(0).toUpperCase() + info.finish.slice(1)); return; }
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
      (info.changed && info.chosen ? ", " + FINISH_WORDS[info.finish] : "") +
      (info.changed && !info.chosen ? ", " + FINISH_WORDS[info.finish] + ": that printing is not made in " + FINISH_WORDS[info.from] : "") +
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
  // The viewer's action rows (mockup screen 3): [− n +] [Move to…] [Remove], then [Add to
  // binder] [Edit in deck editor]; its Printing row opens the Printings sheet (openPrintings),
  // and EDHREC, TCGplayer and Scryfall are the viewer's own pills. Remove is the red outline: a
  // solid red button is for a final confirm only (U10a).
  function openViewer(card) {
    if (!CardView) return;
    var name = card.getAttribute("data-card") || "";
    var data = CardView.fromElement(card);
    var prints = openPrintings(card);
    if (prints) data.onPrintings = prints;
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
      var move = el("button", "btn", "Move to…");
      move.type = "button";
      move.addEventListener("click", function () {
        closeViewer();
        setTimeout(function () { var r = card.getBoundingClientRect(); openMenu(card, r.left + r.width / 2, r.top + r.height / 2, { submenu: true }); }, 50);
      });
      acts.push(move);
      var rm = el("button", "btn btn-outline-danger", removeLabel(card));
      rm.type = "button";
      rm.addEventListener("click", function () { removeCard(card); });
      acts.push(rm);
      if (!isCustom(card)) {
        acts.push(el("span", "br"));  // the next actions on a row of their own
        var ownBtn = el("button", "btn", "Add to binder");
        ownBtn.type = "button";
        ownBtn.addEventListener("click", function () { addToBinder(ownBtn, name); });
        acts.push(ownBtn);
        var editLink = el("a", "btn", "Edit in deck editor");
        editLink.href = "/decks/" + enc(deckId) + "/edit#card-" + enc(name);
        acts.push(editLink);
      }
      CardView.open(data, acts);
      return;
    }
    if (isCustom(card)) { CardView.open(data, acts); return; }  // no binder row for a custom card
    var binderBtn = el("button", "btn", "Add to binder");
    binderBtn.type = "button";
    binderBtn.addEventListener("click", function () { addToBinder(binderBtn, name); });
    acts.push(binderBtn);
    CardView.open(data, acts);
  }
  // one copy into the binder, the button saying how it went
  function addToBinder(btn, name) {
    btn.disabled = true;
    btn.textContent = "Adding…";
    Fetch.answer("/collection/api/add", {
      body: JSON.stringify({ items: [{ name: name, quantity: 1 }], source: "manual" }), csrf: CSRF, what: "the card was not added"
    }).then(function (d) {
      if (d.ok && d.added && d.added.length) { btn.textContent = "Added to your binder"; return; }
      btn.disabled = false;
      btn.textContent = "Could not add: " + Fetch.problem(d, "the card was not added").text.replace(/\.$/, "");
      if (d.signedOut && btn.parentNode) btn.parentNode.insertBefore(signInAction(), btn.nextSibling);
    });
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
  var menu = null, menuCard = null, menuStack = null, menuFocusBack = false;
  var menuScrim = null;  // the dimmed page under the phone's sheet
  var Layers = window.MtgLayers, menuLayer = null;  // the open menu is a layer (static/layers.js)
  function closeMenu(fromBack) {
    if (!menu) return;
    var m = menu, card = menuCard, stack = menuStack, back = menuFocusBack, layer = menuLayer;
    menu = null; menuCard = null; menuStack = null; menuFocusBack = false; menuLayer = null;
    m.remove();
    if (menuScrim) { menuScrim.remove(); menuScrim = null; }
    $$(".deck-toast.under-menu").forEach(function (t) { t.classList.remove("under-menu"); });
    if (layer && !fromBack && Layers) {
      // going back over the layers' entry may put the page's scroll position back too (a menu
      // opened since stays)
      scrollGrace = Date.now() + 400;
      Layers.release(layer);
    }
    flushSteps();  // quantity steps made in it go to Archidekt now
    if (!back) return;
    if (card && card.isConnected) { card.focus(); return; }
    focusNear(stack);  // the card was removed
  }
  // true while the layers' own history.back() has not landed yet (static/layers.js)
  window.MtgDeckCardMenu = { backPending: function () { return !!(Layers && Layers.backPending()); } };
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
  // A group's name inside the menu (Elsewhere, More)
  function groupHead(text) { return el("div", "ghead", text); }
  // A group of items under its name; folded (``fold``) it is a row that opens the group, so the
  // phone's sheet shows the count, the main rows and Remove in one screen and the rarer items on
  // one more tap. The arrow keys reach the row; its items join the list once it is open.
  function appendGroup(m, name, nodes, fold) {
    if (!nodes.length) return;
    if (!fold) {
      m.appendChild(groupHead(name));
      nodes.forEach(function (n) { m.appendChild(n); });
      return;
    }
    var btn = el("button", "ghead fold", "");
    btn.type = "button";
    btn.setAttribute("role", "menuitem");
    btn.setAttribute("aria-haspopup", "true");
    btn.setAttribute("aria-expanded", "false");
    btn.appendChild(iconSlot());
    btn.appendChild(document.createTextNode(name));
    btn.appendChild(el("span", "chev", "›"));
    var grp = el("div", "grp");
    grp.setAttribute("role", "group");
    grp.setAttribute("aria-label", name);
    grp.hidden = true;
    nodes.forEach(function (n) { grp.appendChild(n); });
    btn.addEventListener("click", function (e) {
      stop(e);
      var open = grp.hidden;
      grp.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) return;
      fit(m);
      var end = grp.offsetTop + grp.offsetHeight - m.clientHeight;
      if (m.scrollTop < end) m.scrollTop = Math.min(btn.offsetTop, end);
      var f = $("[role=menuitem]:not([disabled]), [role=menuitemcheckbox]:not([disabled])", grp);
      if (f) f.focus({ preventScroll: true });
    });
    btn.addEventListener("keydown", function (e) { if (e.key === "ArrowRight" && grp.hidden) { stop(e); btn.click(); } });
    m.appendChild(btn);
    m.appendChild(grp);
  }
  // a phone (the narrow window, not the Fold or the app on a wide screen): the menu is a sheet
  // from the bottom over the dimmed page, as every other menu there (static/cardmenu.js)
  function narrow() { return window.matchMedia("(max-width: 599.98px)").matches; }
  // The menu's head: the card's name and, on your own deck, its count with − and + (the most
  // used action first, with no scrolling). The menu stays open for more steps; each shows at once
  // and saves in the background. ``step``: the function a press calls with the card and ±1.
  function menuHead(m, card, name, step) {
    var top = el("div", "headrow");
    var head = el("div", "head");
    head.appendChild(el("span", "nm", name));
    top.appendChild(head);
    if (step) {
      var row = el("div", "qtyrow");
      row.setAttribute("role", "group");
      row.setAttribute("aria-label", "Copies of " + name + " in the deck");
      var minus = el("button", "step", "−");
      minus.type = "button";
      var n = el("b", "n", String(qtyOf(card)));
      var plus = el("button", "step", "+");
      plus.type = "button";
      var relabel = function () {
        var q = qtyOf(card);
        n.textContent = String(q);
        minus.setAttribute("aria-label", q <= 1 ? "Remove the last copy of " + name : "One fewer " + name + ": " + (q - 1));
        plus.setAttribute("aria-label", "One more " + name + ": " + (q + 1));
      };
      relabel();
      qtyLabels.push({ el: n, card: card, show: relabel });
      minus.addEventListener("click", function (e) { stop(e); step(card, -1); });
      plus.addEventListener("click", function (e) { stop(e); step(card, 1); });
      row.appendChild(minus); row.appendChild(n); row.appendChild(plus);
      top.appendChild(row);
    }
    m.appendChild(top);
  }
  // Move to ›: the deck's categories (and "Not counted in the deck" ones) in a list that opens
  // inside the menu; ``mover`` moves the card (moveTo, or customMove for a custom card)
  function moveToItems(m, card, mover) {
    var moveBtn = menuItem("Move to", "swap", null, "more");
    moveBtn.setAttribute("aria-haspopup", "true");
    moveBtn.setAttribute("aria-expanded", "false");
    moveBtn.appendChild(el("span", "chev", "›"));
    var sub = el("div", "sub");
    sub.setAttribute("role", "group");
    sub.setAttribute("aria-label", "Categories");
    sub.hidden = true;
    categoryChoices(card).forEach(function (c) {
      var b = menuItem(c.name, c.side ? "eye-off" : "tag", function () { closeMenu(); mover(card, c.name); }, c.current ? "on" : "");
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
  }
  // Finish ›: Normal, Foil or Etched, the card's own marked; the gateway refinishes cards in the
  // deck itself, so a maybeboard or sideboard row has no Finish list
  function finishItems(card) {
    var frag = document.createDocumentFragment();
    var m = menu;
    var finBtn = menuItem("Finish", "layers", null, "finbtn");
    finBtn.setAttribute("aria-haspopup", "true");
    finBtn.setAttribute("aria-expanded", "false");
    finBtn.appendChild(el("span", "chev", "›"));
    var finSub = el("div", "finsub");
    finSub.setAttribute("role", "group");
    finSub.setAttribute("aria-label", "Finishes");
    finSub.hidden = true;
    var curFin = finishOf(card);
    FINISH_CHOICES.forEach(function (f) {
      var b = menuItem(f, f === curFin ? "check" : "", function () { setFinishOf(card, f); }, f === curFin ? "on" : "");
      if (f === curFin) { b.disabled = true; b.setAttribute("aria-current", "true"); }
      finSub.appendChild(b);
    });
    var toggleFin = function (open) {
      finSub.hidden = !open;
      finBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (!open) return;
      fit(m);
      var f = $("button:not([disabled])", finSub);
      if (f) f.focus({ preventScroll: true });
    };
    finBtn.addEventListener("click", function (e) { stop(e); toggleFin(finSub.hidden); });
    finBtn.addEventListener("keydown", function (e) { if (e.key === "ArrowRight") { stop(e); toggleFin(true); } });
    finSub.addEventListener("keydown", function (e) { if (e.key === "ArrowLeft") { stop(e); toggleFin(false); finBtn.focus(); } });
    frag.appendChild(finBtn);
    frag.appendChild(finSub);
    return frag;
  }
  // The one Printings… sheet (the viewer's Printing row opens it too): browse the card's
  // printings and watch one, and on your own deck put one in the deck (artVariants); with
  // watchlists off, on your own deck, the pick alone (changePrinting). Null where there is
  // neither (someone else's deck with watchlists off).
  function openPrintings(card) {
    if (!window.MtgPrintPick) return null;
    if (document.body.hasAttribute("data-watchlist")) return function () { artVariants(card); };
    if (own && deckId) return function () { changePrinting(card); };
    return null;
  }
  // A menu link (Add to deck…, Edit in deck editor, Open on Scryfall)
  function menuLink(text, ic, href) {
    var a = el("a", "", "");
    a.setAttribute("role", "menuitem");
    a.href = href;
    a.appendChild(icon(ic));
    a.appendChild(document.createTextNode(text));
    return a;
  }
  // Report card… (deck_extras.py): to Archidekt's moderators, on any deck the member can read;
  // not for the member's own custom card in their own deck (Archidekt's menu hides it there too)
  function reportItem(card, name) {
    if (!window.MtgDeckExtras || !document.getElementById("reportcard") || (own && isCustom(card))) return null;
    return menuItem("Report card…", "flag", function () { closeMenu(); window.MtgDeckExtras.open("reportcard", { card: name }); });
  }
  // The menu, in one standard shape (mockup screen 3): the card's name with its count in the
  // head; Open card, Printings…, Move to › and Color tag…; "Elsewhere" (the watchlist, the
  // binder, a pin, another deck); "More" (the card's switches, the editor, Copy name, Scryfall,
  // Report card…); then Remove from deck alone after a line, in red. On someone else's deck the
  // editing items are not offered.
  function openMenu(card, x, y, opts) {
    opts = opts || {};
    closeMenu();
    var name = card.getAttribute("data-card") || "";
    var m = el("div", "menu ctxmenu");
    m.setAttribute("role", "menu");
    m.setAttribute("aria-label", "Card: " + name);
    var sheet = narrow();
    if (sheet) m.classList.add("cs-menu");
    menu = m;
    var Shared = window.MtgMenu, CardMenu = window.MtgCardMenu;
    var edits = own && deckId;
    if (isCustom(card)) {
      customItems(m, card, name);
      var rep = reportItem(card, name);
      if (rep) m.appendChild(rep);
    } else {
      menuHead(m, card, name, edits ? stepQty : null);
      m.appendChild(menuItem("Open card", "eye", function () { closeMenu(); openViewer(card); }));
      var prints = openPrintings(card);
      if (prints) m.appendChild(menuItem("Printings…", "image", function () { closeMenu(); prints(); }));
      if (edits) {
        moveToItems(m, card, moveTo);
        if (window.MtgDeckTag) m.appendChild(menuItem("Color tag…", "tag", function () { closeMenu(); window.MtgDeckTag(card); }));
      }
      // Elsewhere: the card beyond this deck
      var elsewhere = [];
      if (document.body.hasAttribute("data-watchlist")) {
        if (CardMenu && CardMenu.isWatched(name)) {
          // watched already (any printing): the item opens its watchlist entry
          elsewhere.push(menuLink("On your watchlist", "check", "/watchlist?q=" + enc(name)));
        } else {
          // a refusal is said in its toast; the rejection it leaves is taken here (review 2, L5)
          elsewhere.push(menuItem("Watch this card", "bookmark", function () { closeMenu(); watchCard(card, null).catch(function () { /* said */ }); }));
        }
      }
      // one copy into the binder, as the viewer's Add to binder (the shared layer's call)
      if (Shared) elsewhere.push(menuItem("Add to binder", "collection", function () { closeMenu(); Shared.collect(name); }));
      if (Shared && Shared.pin) elsewhere.push(menuItem("Pin card", "pin", function () { closeMenu(); Shared.pin(name); }));
      elsewhere.push(menuLink(edits ? "Add to another deck…" : "Add to deck…", "plus", "/cards?q=" + enc(name)));
      appendGroup(m, "Elsewhere", elsewhere, false);
      // More: the card's own switches and the rest
      var more = [];
      if (edits) {
        more.push(menuItem("Mana value…", "edit", function () { manaValueForm(card, m); }));
        // Archidekt's card menu switches: on shows a check, as the Move to list's current category
        var comp = menuItem("Companion", card.hasAttribute("data-companion") ? "check" : "", function () { switchRow(card, "companion"); }, card.hasAttribute("data-companion") ? "on" : "");
        comp.setAttribute("role", "menuitemcheckbox");
        comp.setAttribute("aria-checked", card.hasAttribute("data-companion") ? "true" : "false");
        more.push(comp);
        if (name.indexOf("//") >= 0) {
          var back = menuItem("Show back face", card.hasAttribute("data-flipped") ? "check" : "", function () { switchRow(card, "back_face"); }, card.hasAttribute("data-flipped") ? "on" : "");
          back.setAttribute("role", "menuitemcheckbox");
          back.setAttribute("aria-checked", card.hasAttribute("data-flipped") ? "true" : "false");
          more.push(back);
        }
        if (zoneOf(card) === "main") more.push(finishItems(card));
        // Lock categories (deck_extras.py): the card's categories here become its default
        // categories on Archidekt
        if (window.MtgDeckExtras && document.getElementById("lock")) {
          more.push(menuItem("Lock categories", "lock", function () {
            var cats = catsOf(card);
            closeMenu();
            window.MtgDeckExtras.open("lock", { card: name, cats: cats });
          }));
        }
        more.push(menuLink("Edit in deck editor", "edit", "/decks/" + enc(deckId) + "/edit#card-" + enc(name)));
      }
      if (Shared) more.push(menuItem("Copy name", "copy", function () { closeMenu(); Shared.copyAndSay(name, "Name copied."); }));
      var scry = scryfallLink(name, "");
      scry.setAttribute("role", "menuitem");
      scry.insertBefore(icon("external"), scry.firstChild);
      more.push(scry);
      var report = reportItem(card, name);
      if (report) more.push(report);
      appendGroup(m, "More", more, MORE_FOLDED_ON_PHONE && narrow());
      if (edits) {
        m.appendChild(el("div", "sep"));
        m.appendChild(menuItem(removeLabel(card), "x", function () { removeCard(card); }, "danger"));
      }
    }
    m.addEventListener("keydown", function (e) {
      var items = $$(".qtyrow .step:not([disabled]), [role=menuitem]:not([disabled]), [role=menuitemcheckbox]:not([disabled])", m).filter(function (b) { return b.offsetParent !== null; });
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
    if (sheet) {
      menuScrim = el("div", "cs-scrim");
      document.body.appendChild(menuScrim);
    }
    document.body.appendChild(m);
    // a note already showing goes under the menu the member has just opened (a tall menu reaches
    // the corner it sits in); one that comes while the menu is open, such as a quantity step's
    // Undo, sits above it
    $$(".deck-toast").forEach(function (t) { t.classList.add("under-menu"); });
    m.style.left = x + "px";
    m.style.top = y + "px";
    fit(m);
    m.style.visibility = "";
    if (opts.submenu && own) { var mb = $(".more", m); if (mb) mb.click(); }
    // focus starts on Open card, the first row under the head (the head's − and + are reached
    // with the arrow keys)
    var first = $("[role=menuitem]:not([disabled]):not(.step)", m);
    if (first && !(opts.submenu && own)) first.focus();
    // the phone's Back closes the menu (static/layers.js)
    if (Layers) menuLayer = Layers.open({ name: "menu", close: function () { if (menu === m) closeMenu(true); } });
  }
  function fit(m) {
    if (m.classList.contains("cs-menu")) return;  // the phone's sheet sits at the bottom, its own height
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
  // a press outside the menu closes it; a press on a toast's button (Undo, Try again) does not,
  // since the menu's count steps keep it open while their saves are said
  document.addEventListener("pointerdown", function (e) {
    if (menu && !menu.contains(e.target) && !e.target.closest(".deck-toast")) closeMenu();
  }, true);
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
  var endPick = null;  // ends Select cards (the stacks are drawn again after a category change)
  var setPickMode = function () {};  // the View sheet's Select cards switch (a phone)
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
    undoBtnEl = el("button", "btn", "Undo");
    undoBtnEl.type = "button";
    undoBtnEl.title = "Undo the last change saved on this page (Ctrl+Z)";
    undoBtnEl.addEventListener("click", undoLast);
    redoBtnEl = el("button", "btn", "Redo");
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
      bar.classList.toggle("on", on);  // a phone shows the bar only while selecting
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
    endPick = function () { setMode(false); };
    all.addEventListener("click", function () {
      $$(".c, .row", cards).forEach(function (c) {
        if (c.offsetParent === null || isCustom(c)) return;  // hidden by the filter or a collapsed stack; a custom card changes from its own menu
        c.classList.add("picked");
        c.setAttribute("aria-pressed", "true");
      });
      refresh();
    });
    document.addEventListener("keydown", function (e) { if (picking && e.key === "Escape" && !document.querySelector(".confirmbar, .cardview.open, .deck-toast[role=alertdialog]")) setMode(false); });
    setPickMode = setMode;

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
        status.textContent = err && err.cancelled ? "" : "Could not save: " + whyNot(err) + ".";
        if (err && err.signedOut) { status.appendChild(document.createTextNode(" ")); status.appendChild(signInAction()); }
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

  // -- the toolbar on a phone (mockup screen 5) --------------------------------------------------
  // One row: Quick add with its +, View and ⋮. View opens a sheet with Layout, Card size (the
  // picture views), Group by, Sort by, Filter and a Select cards switch; each drives the server's
  // own control, hidden there (a layout, grouping or sort change reloads the page as it always
  // did, the filter applies as you type). ⋮ holds Add a package, Undo and Redo, which the phone
  // had lost. Wider screens keep the toolbar as it is (the CSS shows one or the other).
  (function () {
    var toolbar = $(".toolbar.panel"), controls = toolbar && $(".controls", toolbar), vf = $("#viewform");
    if (!toolbar || !controls || !vf || !window.MtgBox) return;
    var Menu = window.MtgMenu, Box = window.MtgBox;
    var viewBtn = el("button", "btn viewbtn", "");
    viewBtn.type = "button";
    viewBtn.setAttribute("aria-haspopup", "dialog");
    viewBtn.appendChild(icon("layers"));
    viewBtn.appendChild(document.createTextNode("View"));
    viewBtn.appendChild(el("span", "chev", "\u2304"));
    viewBtn.addEventListener("click", openViewSheet);
    controls.appendChild(viewBtn);
    var addpkg = $(".quickrow .addpkg", toolbar);
    if (own && deckId) {
      var moreBtn = el("button", "btn toolmore", "");
      moreBtn.type = "button";
      moreBtn.setAttribute("aria-haspopup", "menu");
      moreBtn.setAttribute("aria-label", "More: Add a package, Undo and Redo");
      moreBtn.appendChild(Menu ? Menu.icon("more") : icon("more"));  // the shared layer's ⋮
      moreBtn.addEventListener("click", function () {
        if (!Menu) return;
        var items = [];
        if (addpkg && !addpkg.hasAttribute("aria-disabled")) {
          items.push(Menu.item("Add a package…", "package", function () { Menu.whenClosed(function () { addpkg.click(); }); }));
        }
        items.push(Menu.item("Undo", "history", undoLast, { disabled: !undoBtnEl || undoBtnEl.disabled, title: "Nothing to undo" }));
        items.push(Menu.item("Redo", "history", redoLast, { disabled: !redoBtnEl || redoBtnEl.disabled, title: "Nothing to redo" }));
        var r = moreBtn.getBoundingClientRect();
        Menu.open({ head: "Deck tools", label: "Deck tools", items: items, focusBack: moreBtn }, r.left, r.bottom + 4);
      });
      controls.appendChild(moreBtn);
    }
    toolbar.classList.add("compact");
    // the one "hold a card to move it" line, above the cards, on a touch phone
    if (canDrag && touch && narrow()) {
      var hint = el("p", "movehint", "Hold a card to move it to another category.");
      cards.parentNode.insertBefore(hint, cards);
      cards.setAttribute("data-hint-top", "");
    }
    function mirror(sel, label) {
      var row = el("div", "vrow");
      var lab = el("label", null, label);
      var copy = el("select");
      copy.id = "vs-" + sel.name;
      lab.htmlFor = copy.id;
      $$("option", sel).forEach(function (o) {
        var c = el("option", null, o.textContent);
        c.value = o.value;
        copy.appendChild(c);
      });
      copy.value = sel.value;
      copy.addEventListener("change", function () {
        sel.value = copy.value;
        sel.dispatchEvent(new Event("change", { bubbles: true }));  // the form applies on change
      });
      row.appendChild(lab);
      row.appendChild(copy);
      return row;
    }
    function openViewSheet() {
      var body = el("div", "viewsheet");
      var view = vf.elements.view, group = vf.elements.group, sort = vf.elements.sort;
      if (view) body.appendChild(mirror(view, "Layout"));
      var sizes = $(".cardsize .sizeseg", vf);
      if (sizes) {
        var srow = el("div", "vrow");
        srow.appendChild(el("span", "lbl", "Card size"));
        var seg = el("span", "sizeseg");
        seg.setAttribute("role", "group");
        seg.setAttribute("aria-label", "Card size");
        $$("button", sizes).forEach(function (b) {
          var c = el("button", "", b.textContent);
          c.type = "button";
          c.setAttribute("aria-label", b.getAttribute("aria-label") || b.textContent);
          c.setAttribute("aria-pressed", b.getAttribute("aria-pressed") || "false");
          c.addEventListener("click", function () {
            b.click();  // static/prefs.js applies and keeps it
            $$("button", seg).forEach(function (x) { x.setAttribute("aria-pressed", x === c ? "true" : "false"); });
          });
          seg.appendChild(c);
        });
        srow.appendChild(seg);
        body.appendChild(srow);
      }
      if (group) body.appendChild(mirror(group, "Group by"));
      if (sort) body.appendChild(mirror(sort, "Sort by"));
      var q = $("#q", vf);
      if (q) {
        var frow = el("div", "vrow");
        var flab = el("label", null, "Filter");
        var fin = el("input");
        fin.type = "search";
        fin.id = "vs-q";
        flab.htmlFor = fin.id;
        fin.placeholder = q.placeholder || "eg: Sol Ring";
        fin.autocomplete = "off";
        fin.value = q.value;
        fin.addEventListener("input", function () {
          q.value = fin.value;
          q.dispatchEvent(new Event("input", { bubbles: true }));  // the live filter
        });
        frow.appendChild(flab);
        frow.appendChild(fin);
        body.appendChild(frow);
      }
      if (own && deckId) {
        var prow = el("div", "vrow");
        prow.appendChild(el("span", "lbl", "Select cards"));
        var sw = el("label", "switch");
        var swIn = el("input");
        swIn.type = "checkbox";
        swIn.checked = picking;
        swIn.setAttribute("aria-label", "Select cards");
        swIn.addEventListener("change", function () {
          setPickMode(swIn.checked);
          if (swIn.checked) Box.close();  // the select bar is under the sheet
        });
        sw.appendChild(swIn);
        sw.appendChild(el("span", "track"));
        prow.appendChild(sw);
        body.appendChild(prow);
      }
      var done = Box.button("Done", "btn", null, { cancel: true });
      Box.open({ title: "View", body: body, actions: [done], returnFocus: viewBtn });
    }
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

  // touch: press and hold a card for 350 ms and the card's menu opens under the finger, as every
  // other list's does (re-audit D09: it waited for the finger to lift). Slide the held card
  // instead (on the category grouping of your own deck) and the menu closes as the card moves
  // onto another category; let go without moving and the menu stays.
  // The copy under the finger moves by transform and the stack under it is looked up once a frame,
  // not on every touchmove; near the window's top or bottom edge the page scrolls on by itself.
  var hold = null, armed = null, touchCard = null, ghost = null, lastTarget = null, startX = 0, startY = 0;
  var touchX = 0, touchY = 0, ghostW = 0, touchFrame = 0;
  function armTouch(card, x, y) {
    armed = card;
    card.classList.add("held");
    if (navigator.vibrate) navigator.vibrate(20);
    // the menu opens now, where the finger is; the tap that ends the hold is swallowed
    if (!(menu && menuCard === card)) {
      swallowClick = true;
      var r = card.getBoundingClientRect();
      openMenu(card, x == null ? r.left + r.width / 2 : x, y == null ? r.top + r.height / 2 : y);
    }
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
    var hx = t.clientX, hy = t.clientY;
    hold = setTimeout(function () { armTouch(card, hx, hy); }, 350);
  }, { passive: true });
  cards.addEventListener("touchmove", function (e) {
    var t = e.touches[0];
    var moved = Math.abs(t.clientX - startX) > 8 || Math.abs(t.clientY - startY) > 8;
    if (!armed && !touchCard) { if (moved) clearTimeout(hold); return; }  // a plain scroll
    if (armed && !touchCard) {
      if (!moved) return;
      if (!canDrag) { armed.classList.remove("held"); armed = null; return; }  // sliding elsewhere: let the page scroll
      if (menu && menuCard === armed) closeMenu();  // the slide moves the card: the menu makes way
      else if (menu) return;
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
    // a hold the browser cancelled (its own drag took the touch): no tap follows, so the next
    // tap on a card must not be swallowed (the menu the hold opened stays)
    if (wasArmed && e.type === "touchcancel") swallowClick = false;
    if (wasArmed && e.type === "touchend") {
      // held still: the menu opened at the hold stays; the lift's tap is swallowed
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

  // -- categories on the deck page (mockup screen 4, review F8; the owner's T-191 note) ---------------
  // Each stack's ⋮ (and a right-click, a press and hold or Shift+F10 on its head) opens the
  // category's menu: Rename…, Add category…, Add from template… | Premier, In deck, In price as
  // tick rows | Edit cards in editor | Delete…, and "+ Add category" after the last stack opens
  // the same box as Add category…. Every change goes to Deck settings' own route
  // (/decks/{id}/categories with json=1&page=deck) with the category's baseline, as the settings
  // page's form sends it: the same check that the deck is still what the page showed, the same
  // questions (the commander, which cards count, a delete) in a box with the gateway's words, and
  // one proposal applied at once with its snapshot. The answer's stacks replace the page's and
  // the toast offers Undo: the inverse change, or after a delete the restore of its own snapshot
  // (undo-box.js). Without scripts the ⋮ is the drop-down to the editor and Deck settings, and
  // "+ Add category" opens Deck settings.
  // Mockup choices for the owner (each flips here): Undo in the toast, as on Deck settings, rather
  // than in the toolbar's card history (UNDO_IN_TOAST false would end that history instead);
  // the delete box preselects the biggest category the cards can go to (PRESELECT_BIGGEST).
  var UNDO_IN_TOAST = true, PRESELECT_BIGGEST = true;
  (function () {
    var Menu = window.MtgMenu, Box = window.MtgBox;
    if (!own || !deckId || !byCategory || !Menu || !Box || !window.fetch || !window.URLSearchParams) return;
    if (!cards.hasAttribute("data-catinfo")) return;
    var catUrl = "/decks/" + enc(deckId) + "/categories";
    var busy = 0;
    var HISTORY = " Snapshot kept under History.";
    var NO_RECORD = "This category has no record on Archidekt yet, so there is nothing to delete.";
    function q(name) { return "“" + name + "”"; }
    function info() { return readJson("data-catinfo", []); }
    function catNamed(name) { return info().filter(function (c) { return c.name === name; })[0] || null; }
    // the same category after the stacks were drawn again: by its record, else by its name
    function fresh(c, name) {
      var all = info();
      return (c.id !== null && c.id !== undefined ? all.filter(function (x) { return x.id === c.id; })[0] : null) ||
        all.filter(function (x) { return x.name === (name || c.name); })[0] || null;
    }
    window.addEventListener("beforeunload", function (ev) { if (busy) { ev.preventDefault(); ev.returnValue = ""; } });
    function post(fields) {
      var fd = new URLSearchParams();
      fd.set("csrf", CSRF); fd.set("json", "1"); fd.set("page", "deck");
      var vf = $("#viewform");
      fd.set("view", vf && vf.elements.view ? vf.elements.view.value : (cards.className.replace("deckview", "").trim().split(" ")[0] || "text"));
      fd.set("sort", vf && vf.elements.sort ? vf.elements.sort.value : "name");
      Object.keys(fields).forEach(function (k) {
        if (Array.isArray(fields[k])) fields[k].forEach(function (v) { fd.append(k, v); });
        else fd.set(k, fields[k]);
      });
      busy++;
      return Fetch.answer(catUrl, { method: "POST", body: fd }).then(function (d) { busy--; return d; });
    }
    // What Deck settings' form sends for one category: what it was when the page drew it (the
    // was_ fields) and what it is to be: its name, and each switch that is on. ``over``: the
    // change ({name} or one of the switches as true/false).
    function fieldsFor(c, over) {
      var k = c.key, out = {};
      out["was_name_" + k] = c.name;
      out["name_" + k] = over.name !== undefined ? over.name : c.name;
      ["premier", "not_in_deck", "not_in_price"].forEach(function (flag) {
        out["was_" + flag + "_" + k] = c[flag] ? "1" : "0";
        var on = over[flag] !== undefined ? over[flag] : c[flag];
        if (on) out[flag + "_" + k] = "1";
      });
      return out;
    }
    // The answer's stacks take the page's place: the cards' own attributes (the categories the
    // card menu's Move to lists, the ones not in the deck, the category data) follow, the ⋮
    // buttons are put back, and a filter typed in the toolbar applies again.
    function swap(html) {
      var tpl = document.createElement("template");
      tpl.innerHTML = html;
      var next = tpl.content.querySelector("#cards");
      if (!next) return;
      closeMenu();
      if (picking && endPick) endPick();
      ["data-cats", "data-excluded", "data-side", "data-catinfo", "data-drop", "data-grouping"].forEach(function (a) {
        if (next.hasAttribute(a)) cards.setAttribute(a, next.getAttribute(a)); else cards.removeAttribute(a);
      });
      cards.textContent = "";
      while (next.firstChild) cards.appendChild(next.firstChild);
      deckCats = readJson("data-cats", []);
      excluded = readJson("data-excluded", []);
      sideCat = cards.getAttribute("data-side") || sideCat;
      wire();
      var filter = $("#q");
      if (filter && filter.value) filter.dispatchEvent(new Event("input"));
    }
    function button(text, onClick, cls) {
      var b = el("button", cls || "btn", text);
      b.type = "button";
      b.addEventListener("click", onClick);
      return b;
    }
    function undoButton(fn) { return button("Undo", function () { dismissToast(); fn(); }); }
    function said(text, undo, opts) {
      opts = opts || {};
      var actions = undo ? [undo] : [];
      toast(text, { actions: actions, ms: opts.ms });
    }
    function failed(d, box) {
      var words = (d && d.message) || "The change could not be saved.";
      if (box) { box.busy(false); box.note(words, true); return; }
      toast(words, { error: true });
    }
    // A change that the gateway answers with a question (needs_confirm): the same box as Deck
    // settings, with the gateway's own title and sentence, then the same post with confirmed=1.
    // ``ok``: the final button's words; ``danger``: a delete's solid red one.
    function send(fields, o) {
      o = o || {};
      return post(fields).then(function (d) {
        if (d.needs_confirm) {
          return new Promise(function (resolve) {
            var yes = false;
            Box.confirm({
              title: d.title || "", text: d.text || "", ok: o.ok || "Save", icon: o.danger ? "trash" : "check", danger: !!o.danger,
              cancel: o.danger ? "Keep it" : "Cancel",
              onOk: function () { yes = true; fields.confirmed = "1"; resolve(send(fields, o)); },
              onClose: function () { if (!yes) resolve({ ok: false, cancelled: true }); }
            });
          });
        }
        return d;
      });
    }
    // -- a rename or a switch: one small change, Undo sends the inverse --------------------------
    function change(c, over, words, box) {
      var fields = fieldsFor(c, over);
      fields.action = "save";
      return send(fields, { ok: "Save" }).then(function (d) {
        if (d.cancelled) { if (box) box.close(); return; }
        if (!d.ok) { failed(d, box); return; }
        if (box) box.close();
        swap(d.cards_html);
        var after = fresh(c, over.name !== undefined ? over.name : c.name);
        var inverse = {};
        if (over.name !== undefined) inverse.name = c.name;
        ["premier", "not_in_deck", "not_in_price"].forEach(function (f) { if (over[f] !== undefined) inverse[f] = c[f]; });
        said(words.done + HISTORY, UNDO_IN_TOAST && after ? undoButton(function () { change(after, inverse, { done: words.undone, undone: words.done }); }) : null);
      });
    }
    function flip(c, flag) {
      var to = !c[flag], over = {};
      over[flag] = to;
      var words = {
        premier: [q(c.name) + " is now a Premier category.", q(c.name) + " is no longer a Premier category."],
        not_in_deck: ["Cards in " + q(c.name) + " no longer count in the deck.", "Cards in " + q(c.name) + " count in the deck again."],
        not_in_price: [q(c.name) + " no longer counts in the price.", q(c.name) + " counts in the price again."]
      }[flag];
      change(c, over, { done: to ? words[0] : words[1], undone: to ? words[1] : words[0] });
    }
    function field(labelText, input, id) {
      var wrap = el("div", "field");
      var lab = el("label", null, labelText);
      input.id = id;
      lab.htmlFor = id;
      wrap.appendChild(lab);
      wrap.appendChild(input);
      return wrap;
    }
    function textInput(value) {
      var i = el("input");
      i.type = "text";
      i.value = value || "";
      i.maxLength = 60;
      i.autocomplete = "off";
      i.required = true;
      return i;
    }
    function cancelButton(text) { return Box.button(text || "Cancel", "btn", null, { cancel: true }); }
    function cleanName(box, input, taken) {
      var name = input.value.replace(/\s+/g, " ").trim();
      if (!name) { box.note("Type a name.", true); input.focus(); return null; }
      if (name.indexOf(",") >= 0) { box.note("A category name can't have a comma.", true); input.focus(); return null; }
      if (info().some(function (c) { return c.name.toLowerCase() === name.toLowerCase() && c.name !== taken; })) {
        box.note("The deck already has a category named " + q(name) + ". Pick another name.", true);
        input.focus();
        return null;
      }
      return name;
    }
    function renameBox(c, back) {
      var form = el("form", "catform");
      var input = textInput(c.name);
      form.appendChild(field("Name", input, "catname"));
      var ok = Box.button("Rename", "primary", null, { icon: "check" });
      var box = Box.open({ title: "Rename " + q(c.name), body: form, actions: [cancelButton(), ok], returnFocus: back });
      var go = function (e) {
        if (e) e.preventDefault();
        var name = cleanName(box, input, c.name);
        if (!name) return;
        if (name === c.name) { box.close(); return; }
        box.busy(true);
        change(c, { name: name }, { done: "Renamed " + q(c.name) + " to " + q(name) + ".", undone: "Renamed " + q(name) + " back to " + q(c.name) + "." }, box);
      };
      ok.addEventListener("click", go);
      form.addEventListener("submit", go);
      input.focus();
      input.select();
    }
    // -- Add category…: a name and the three switches; Undo deletes it again ---------------------
    function check(labelText, on, name) {
      var lab = el("label", "sw");
      var c = el("input");
      c.type = "checkbox"; c.name = name; c.checked = !!on;
      lab.appendChild(c);
      lab.appendChild(document.createTextNode(" " + labelText));
      return lab;
    }
    function addBox(back) {
      var form = el("form", "catform");
      var input = textInput("");
      form.appendChild(field("Name", input, "catname"));
      var sw = el("div", "ds-sw");
      sw.setAttribute("role", "group");
      sw.setAttribute("aria-label", "Switches of the new category");
      var premier = check("Premier", false, "premier"), inDeck = check("In deck", true, "in_deck"), inPrice = check("In price", true, "in_price");
      sw.appendChild(premier); sw.appendChild(inDeck); sw.appendChild(inPrice);
      form.appendChild(sw);
      var ok = Box.button("Add category", "primary", null, { icon: "plus" });
      var box = Box.open({ title: "Add a category", body: form, actions: [cancelButton(), ok], returnFocus: back });
      var go = function (e) {
        if (e) e.preventDefault();
        var name = cleanName(box, input);
        if (!name) return;
        var fields = { action: "create", category: name };
        if (premier.firstChild.checked) fields.premier = "1";
        if (!inDeck.firstChild.checked) fields.not_in_deck = "1";
        if (!inPrice.firstChild.checked) fields.not_in_price = "1";
        box.busy(true);
        send(fields, { ok: "Add category" }).then(function (d) {
          if (d.cancelled) { box.close(); return; }
          if (!d.ok) { failed(d, box); return; }
          box.close();
          swap(d.cards_html);
          var made = catNamed(name);
          // an empty category has no stack of its own until a card is in it: one is drawn now,
          // so a card can be dragged (or moved from its menu) into it
          var st = ensureStack(name);
          if (st) focusNear(st);
          said("Added " + q(name) + ". Move cards into it from their menu, or drag them." + HISTORY,
            UNDO_IN_TOAST && made && made.record ? undoButton(function () { removeAgain(made); }) : null);
        });
      };
      ok.addEventListener("click", go);
      form.addEventListener("submit", go);
      input.focus();
    }
    // the Undo of an add: the empty category is deleted again (the member's own press is the yes)
    function removeAgain(c) {
      post({ action: "delete", delete: String(c.id), confirmed: "1" }).then(function (d) {
        if (!d.ok) { failed(d); return; }
        swap(d.cards_html);
        said("Removed " + q(c.name) + " again." + HISTORY);
      });
    }
    // -- Add from template…: the member's templates whose names the deck lacks, ticked ----------
    function templateBox(back) {
      var form = el("form", "catform tplform");
      var tree = el("div", "tree");
      var wait = el("p", "muted small loading", "Reading your templates from Archidekt…");
      wait.setAttribute("role", "status");
      tree.appendChild(wait);
      form.appendChild(el("p", null, "Each ticked template becomes a category with the template's name and switches; after that the two are not linked."));
      form.appendChild(tree);
      var ok = Box.button("Add categories", "primary", null, { icon: "plus" });
      ok.disabled = true;
      var box = Box.open({ title: "Add from a template", body: form, actions: [cancelButton(), ok], returnFocus: back });
      var picked = function () { return $$("input[name=template]:checked", form).map(function (i) { return i.value; }); };
      var count = function () {
        var n = picked().length;
        ok.lastChild.textContent = n === 0 ? " Add categories" : " Add " + n + (n === 1 ? " category" : " categories");
        ok.disabled = n === 0;
      };
      Menu.getJSON("/decks/api/templates").then(function (d) {
        tree.textContent = "";
        if (!d || !d.ok) { tree.appendChild(el("p", "muted small", (d && d.message) || "Your templates could not be read.")); return; }
        var have = info().map(function (c) { return c.name.toLowerCase(); });
        var free = (d.templates || []).filter(function (t) { return t.name && have.indexOf(t.name.toLowerCase()) < 0; });
        if (!free.length) { tree.appendChild(el("p", "muted small", "Every template's name is already a category in this deck.")); return; }
        free.forEach(function (t) {
          var lab = el("label", "sw");
          var c = el("input");
          c.type = "checkbox"; c.name = "template"; c.value = t.name;
          lab.appendChild(c);
          lab.appendChild(document.createTextNode(" " + t.name));
          tree.appendChild(lab);
        });
        box.focusFirst();
      }, function () { tree.textContent = ""; tree.appendChild(el("p", "muted small", "No connection; your templates could not be read.")); });
      form.addEventListener("change", count);
      var go = function (e) {
        if (e) e.preventDefault();
        var names = picked();
        if (!names.length) { box.note("Tick at least one template.", true); return; }
        box.busy(true);
        send({ action: "template", template: names }, { ok: "Add categories" }).then(function (d) {
          if (d.cancelled) { box.close(); return; }
          if (!d.ok) { failed(d, box); return; }
          box.close();
          swap(d.cards_html);
          names.forEach(function (n) { ensureStack(n); });
          said((names.length === 1 ? "Added " + q(names[0]) + "." : "Added " + names.length + " categories: " + names.join(", ") + ".") + HISTORY);
        });
      };
      ok.addEventListener("click", go);
      form.addEventListener("submit", go);
    }
    // -- Delete…: the red box; a category with cards names where they go first ------------------
    function targetsFor(c) {
      var others = info().filter(function (x) { return x.name !== c.name; });
      others.sort(function (a, b) { return b.cards - a.cards || a.name.localeCompare(b.name); });
      return others;
    }
    // Each choice for the cards is named for what it does to them (review 2, M6; confirmation
    // N1), from where they are now (c, the category being deleted): from a plain category they
    // stay in the deck, go out of it (Maybeboard, Sideboard) or become commanders (Commander, a
    // Premier category); from a board out of the deck they join it, or move to another board and
    // stay out; from a Premier category they stop being commanders unless they go to another.
    // "No category" leaves them where they are with none, listed by type, as Archidekt allows.
    // t: a category of targetsFor, or null for none.
    function targetWords(t, n, c) {
      var one = n === 1, cards = one ? "card" : "cards", stay = one ? "stays" : "stay", move = one ? "moves" : "move", join = one ? "joins" : "join";
      var out = !!(c && c.not_in_deck), cmdr = !!(c && (c.premier || c.commander)) && !out;
      var its = "Its " + n + " " + cards + " ", commanders = one ? "a commander" : "commanders", become = one ? "becomes" : "become";
      var ends = cmdr ? (one ? " and is no longer a commander." : " and are no longer commanders.") : ".";
      if (!t) {
        if (out) return { label: "No category (joins the deck)", sentence: its + join + " the deck with no category, listed by type." };
        return { label: "No category", sentence: its + stay + " in the deck with no category, listed by type" + ends };
      }
      if (t.premier || t.commander) {
        if (cmdr) return { label: t.name + " (" + (one ? "stays a commander" : "stay commanders") + ")", sentence: its + move + " to " + q(t.name) + " and " + stay + " " + commanders + "." };
        var makes = t.name + " (makes " + (one ? "it a commander" : "them commanders") + ")";
        if (out) return { label: makes, sentence: its + join + " the deck in " + q(t.name) + " and " + become + " " + commanders + "." };
        return { label: makes, sentence: its + move + " to " + q(t.name) + " and " + become + " " + commanders + "." };
      }
      if (t.not_in_deck) {
        if (out) return { label: t.name + " (still out of the deck)", sentence: its + move + " to " + q(t.name) + ", still out of the deck." };
        return { label: t.name + " (leaves the deck)", sentence: its + move + " to " + q(t.name) + ", out of the deck" + ends };
      }
      if (out) return { label: t.name + " (joins the deck)", sentence: its + join + " the deck, in " + q(t.name) + "." };
      return { label: t.name, sentence: its + stay + " in the deck, in " + q(t.name) + ends };
    }
    function deleteBox(c, back) {
      var body = el("div");
      var n = c.cards, select = null, sentence = null;
      if (n) {
        sentence = el("p", null, "");
        body.appendChild(sentence);
        select = el("select");
        var targets = targetsFor(c);
        // the biggest category like this one comes picked, as its cards then stay as they are: a
        // plain one (in the deck, not Premier) for a plain one, another board out of the deck for
        // a Maybeboard or Sideboard; with none "No category" comes picked, never Commander or a
        // board that changes whether they count (review 2, M6; N1); a Premier pick asks once more
        var plain = targets.filter(function (t) { return !t.premier && !t.commander && !t.not_in_deck === !c.not_in_deck; })[0] || null;
        var pick = PRESELECT_BIGGEST ? plain : null;
        var none = el("option", null, targetWords(null, n, c).label);
        none.value = "";
        none.selected = !pick;
        select.appendChild(none);
        targets.forEach(function (t) {
          var o = el("option", null, targetWords(t, n, c).label);
          o.value = t.name;
          if (pick && t.name === pick.name) o.selected = true;
          select.appendChild(o);
        });
        var say = function () { sentence.textContent = targetWords(catNamed(select.value), n, c).sentence; };
        select.addEventListener("change", say);
        say();
        body.appendChild(field("Move " + (n === 1 ? "it" : "them") + " to", select, "catmove"));
      }
      body.appendChild(el("p", "muted small", "Saved to Archidekt at once; a snapshot from just before is kept under History."));
      var ok = Box.button("Delete category", "danger", null, { icon: "trash" });
      var box = Box.open({ title: "Delete " + q(c.name) + "?", body: body, actions: [cancelButton("Keep it"), ok], returnFocus: back });
      ok.addEventListener("click", function () {
        var fields = { action: "delete", delete: String(c.id), move_ok: "1" };
        var to = select ? select.value : "";
        if (to) fields.move_cards_to = to; else if (select) fields.no_category = "1";
        box.busy(true);
        send(fields, { ok: "Delete category", danger: true }).then(function (d) {
          if (d.cancelled) { box.close(); return; }
          if (!d.ok) { failed(d, box); return; }
          box.close();
          swap(d.cards_html);
          var where = !n ? "" : to ? " Its " + (n === 1 ? "card is" : n + " cards are") + " in " + q(to) + "." : " Its " + (n === 1 ? "card has" : n + " cards have") + " no category now.";
          var words = "Deleted " + q(c.name) + "." + where + HISTORY;
          // the Undo is the restore of the delete's own snapshot (undo-box.js asks first)
          var undo = null;
          if (d.undo_html) {
            var holder = el("span");
            holder.innerHTML = d.undo_html;
            undo = holder.firstElementChild;
          }
          said(words, undo, { ms: 30000 });
        });
      });
      if (select) select.focus(); else box.focusFirst();
    }
    // -- the menu ------------------------------------------------------------------------------
    function tick(text, on, onClick) {
      var b = Menu.item(text, on ? "check" : "", onClick, { cls: on ? "on" : "" });
      if (!on) { var slot = el("span", "ic-slot"); slot.setAttribute("aria-hidden", "true"); b.insertBefore(slot, b.firstChild); }
      b.setAttribute("role", "menuitemcheckbox");
      b.setAttribute("aria-checked", on ? "true" : "false");
      return b;
    }
    function openCatMenu(head, x, y) {
      var stack = head.closest(".stack"), name = stack ? stack.getAttribute("data-group") : "";
      var c = catNamed(name), back = $(".kebab", head) || head;
      var items = [];
      if (c) items.push(Menu.item("Rename…", "edit", function () { renameBox(c, back); }));
      items.push(Menu.item("Add category…", "plus", function () { addBox(back); }));
      items.push(Menu.item("Add from template…", "decks", function () { templateBox(back); }));
      if (c) {
        items.push(Menu.sep());
        items.push(tick("Premier", c.premier, function () { flip(c, "premier"); }));
        items.push(tick("In deck", !c.not_in_deck, function () { flip(c, "not_in_deck"); }));
        items.push(tick("In price", !c.not_in_price, function () { flip(c, "not_in_price"); }));
      }
      items.push(Menu.sep());
      items.push(Menu.link("Edit cards in editor", "edit", "/decks/" + enc(deckId) + "/edit#cat-" + enc(name)));
      if (c) items.push(Menu.item("Delete…", "trash", function () { deleteBox(c, back); }, { danger: true, disabled: !c.record, title: NO_RECORD }));
      Menu.open({ head: name, label: "Category: " + name, items: items, focusBack: back }, x, y);
    }
    // the ⋮ in place of the no-script drop-down, and "+ Add category" as the box
    function wire() {
      $$(".stackhead details.catdd", cards).forEach(function (dd) {
        var st = dd.closest(".stack"), name = st ? st.getAttribute("data-group") : "";
        var k = Menu.kebab("Options for " + name);
        dd.replaceWith(k);
      });
      var add = $(".addcat", cards);
      if (add) add.addEventListener("click", function (e) {
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
        e.preventDefault();
        addBox(add);
      });
    }
    wire();
    Menu.bind(cards, {
      itemOf: function (n) { var h = n && n.closest ? n.closest(".stackhead") : null; return h && cards.contains(h) ? h : null; },
      skip: "a, .kebab",
      button: ".stackhead .kebab",
      open: openCatMenu
    });
  })();
  } // deck view

  // Stacks follow Archidekt's category order, the one order there is (owner decision C4).
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
    return window.MtgFetch.answer(src, { what: "the deck list could not be loaded" }).then(function (d) {
      if (d && d.ok && typeof d.list === "string") return d;
      if (d.signedOut || d.noConnection) { var out = new Error(d.message); out.signedOut = !!d.signedOut; throw out; }
      if (window.console) console.warn("Play on EDHPlay: the deck list request failed (HTTP " + d.status + ")" + (d && d.message ? ": " + d.message : ""));
      throw new Error("The deck list couldn't be loaded.");
    });
  }
  var dialog = null, back = null, dialogLayer = null, Layers = window.MtgLayers;
  // ``fromBack``: the Back key took the box's layer already (static/layers.js)
  function close(fromBack) {
    if (!dialog) return;
    dialog.remove();
    dialog = null;
    var layer = dialogLayer;
    dialogLayer = null;
    if (layer && fromBack !== true && Layers) Layers.release(layer);
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
    // the open box is a layer (static/layers.js): on a phone Back closes it, not the page
    if (Layers) dialogLayer = Layers.open({ name: "box", close: function () { if (dialog === mine) close(true); } });
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
        if (err.signedOut) p.appendChild(window.MtgFetch.signInLink("btn small"));
        else {
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
  // The deck menu, the same on every screen (the owner, 2026-10-25): what the banner's buttons do (Edit
  // deck, Clone deck, Test and play) is not repeated here, and the deck's proposals are the
  // banner's Proposals pill. Items that change the deck show on your own decks only, and not
  // while the deck is still being made (its page says so; the gateway would refuse them).
  // Mockup screen 2's choices, for the owner (each one flips here): Add a package… in this menu as well
  // as the toolbar's button (false: the button alone); the Registration PDF as a row of the
  // Export box (false: its own item here); one Contests… item that asks enter or withdraw
  // (false: the two items); Share second, as "Share" (false: third, "Share and print").
  var PACKAGE_IN_MENU = false, PDF_IN_EXPORT = true, ONE_CONTESTS_ITEM = true, SHARE_SECOND = true;
  function items() {
    var id = enc(d.id), out = [], edits = d.own && !d.making;
    if (edits) {
      out.push(head("This deck"));
      out.push(item("Rename…", "edit", renameDeck));
      out.push(item("Move to folder…", "folder", moveDeck));
      if (PACKAGE_IN_MENU && d.can && document.getElementById("package")) out.push(item("Add a package…", "package", extras("package")));
      out.push(link("Deck settings", "settings", "/decks/" + id + "/settings"));
      out.push(sep());
    }
    var shareItems = [head(SHARE_SECOND ? "Share" : "Share and print"), item("Share…", "send", share)];
    if (d.can) shareItems.push(item("Export…", "download", openBox("export")));
    if (!PDF_IN_EXPORT && d.can && document.getElementById("pdf")) shareItems.push(item("Registration PDF…", "pdf", extras("pdf")));
    shareItems.push(link("Open on Archidekt", "external", "https://archidekt.com/decks/" + id, { ext: true }));
    var look = [head("Look into it"), link("Deck stats", "report", "/decks/" + id + "#stats"), link("Deck reports", "stats", "/reports?deck_id=" + id)];
    look.push(link("History", "history", "/history?deck_id=" + id));
    if (d.can) look.push(item("Compare with…", "layers", openBox("compare")));
    if (Menu.enabled && Menu.enabled() && window.MtgDeckWatch) look.push(item("Watch deck price", "eye", function () { window.MtgDeckWatch(d.id); }));
    if (SHARE_SECOND) out = out.concat(shareItems, [sep()], look);
    else out = out.concat(look, [sep()], shareItems);
    var community = [];
    if (d.own && d.can && document.getElementById("help")) {
      community.push(item("Deck help…", "hand", extras("help")));
      if (ONE_CONTESTS_ITEM) community.push(item("Contests…", "trophy", contests));
      else {
        community.push(item("Enter in a contest…", "trophy", extras("contest")));
        community.push(item("Withdraw from a contest…", "trophy", extras("withdraw")));
      }
    }
    if (d.can && document.getElementById("report")) community.push(item("Report deck…", "flag", extras("report")));
    if (community.length) { out.push(sep()); out.push(head("Community")); out = out.concat(community); }
    if (edits) {
      out.push(sep());
      out.push(item("Delete deck…", "trash", openBox("delete"), { danger: true }));
    }
    return out;
  }
  // Contests…: one item; the box offers entering the deck in a contest or withdrawing an entry
  // (the two boxes of deck_extras.py), as the share box offers its ways
  function contests() {
    var rows = el("div", "sharebox");
    function row(text, ic, note, which) {
      var b = el("button", "btn sharerow", "");
      b.type = "button";
      b.appendChild(Menu.icon(ic));
      b.appendChild(el("span", "t", text));
      if (note) b.appendChild(el("small", "", note));
      b.addEventListener("click", function () {
        Box.close();
        setTimeout(extras(which), 60);
      });
      rows.appendChild(b);
    }
    row("Enter this deck in a contest…", "trophy", "with a summary of the deck", "contest");
    row("Withdraw from a contest…", "x", "takes the deck's entry back", "withdraw");
    Box.open({ title: "Contests: “" + d.name + "”", body: rows, actions: [cancelButton("Close")], returnFocus: btn });
  }
  // the Export box's Registration PDF row opens the PDF box through its own script (the fields
  // it fills), not the plain box-open way
  var regpdf = $("#export .regpdf");
  if (regpdf) regpdf.addEventListener("click", function (e) {
    if (!window.MtgDeckExtras || !document.getElementById("pdf")) return;  // the link's own address
    e.preventDefault();
    Box.close();
    setTimeout(extras("pdf"), 60);
  });

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
    var testLbl = el("span", "", "Test");
    testLbl.appendChild(el("span", "lg", " and play"));  // the long label, on wide screens
    testBtn.appendChild(testLbl);
    testDd.parentNode.insertBefore(testBtn, testDd);
    testBtn.addEventListener("click", function () {
      var id = enc(d.id), t = [head("On the gateway"), link("Goldfish", "layers", "/decks/" + id + "/goldfish")];
      if (d.can) t.push(item("Run test", "stats", submit("#fullf")));
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
