/* Deck page, deck list, search and collection helpers. The pages work without this file (every
   control is a form); this only makes them feel like Archidekt:
   - the toolbar's View as / Group by / Sort by selects apply on change, the local filter narrows
     the cards as you type, and card-name inputs autocomplete through the gateway's own Scryfall
     search;
   - on touch screens a tap fans a stack out and a tap on a card opens a viewer with the card
     large and what can be done with it (grid view, stacks, text rows alike);
   - on the member's own deck, cards can be dragged between categories (mouse, or press and hold
     on touch); the moves become one proposal, applied from the review page like every edit. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var csrfInput = $("input[name=csrf]");
  var CSRF = csrfInput ? csrfInput.value : "";

  // -- forms that apply on change ----------------------------------------------------------
  $$("#viewform, #listform, #searchform").forEach(function (form) {
    $$("select", form).forEach(function (sel) {
      sel.addEventListener("change", function () { form.requestSubmit ? form.requestSubmit() : form.submit(); });
    });
  });

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

  // -- card-name autocomplete (Quick add, the search form's commander, the collection's add box) -
  var names = $("#cardnames");
  if (names) {
    var timer = null;
    $$("input[list=cardnames]").forEach(function (input) {
      input.addEventListener("input", function () {
        clearTimeout(timer);
        var v = input.value.trim();
        if (v.length < 3) return;
        timer = setTimeout(function () {
          fetch("/scan/api/search?q=" + encodeURIComponent(v), { credentials: "same-origin" })
            .then(function (r) { return r.ok ? r.json() : { names: [] }; })
            .then(function (d) {
              names.textContent = "";
              (d.names || []).slice(0, 12).forEach(function (n) {
                var o = document.createElement("option");
                o.value = n;
                names.appendChild(o);
              });
            })
            .catch(function () {});
        }, 250);
      });
    });
  }

  if (!cards || !cards.classList.contains("deckview")) return;
  var deckId = cards.getAttribute("data-deck");
  var own = cards.hasAttribute("data-own");
  var touch = window.matchMedia("(hover: none)").matches;

  // -- card viewer -----------------------------------------------------------------------------
  var viewer = document.createElement("div");
  viewer.className = "cardview";
  viewer.setAttribute("role", "dialog");
  viewer.setAttribute("aria-modal", "true");
  viewer.setAttribute("aria-label", "Card");
  document.body.appendChild(viewer);
  var lastFocus = null;
  function closeViewer() {
    viewer.classList.remove("open");
    viewer.textContent = "";
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function openViewer(card) {
    lastFocus = document.activeElement;
    viewer.textContent = "";
    var box = el("div", "box");
    var name = card.getAttribute("data-card") || "";
    var img = card.getAttribute("data-img");
    var pic;
    if (img) {
      pic = el("img");
      pic.src = img.replace("/small/", "/normal/");
      pic.alt = name;
    } else {
      pic = el("div", "ph", name);
    }
    var side = el("div");
    side.appendChild(el("h3", null, name));
    var meta = [card.getAttribute("data-set"), card.getAttribute("data-type")].filter(Boolean).join(" · ");
    side.appendChild(el("p", "meta", meta));
    var acts = el("div", "acts");
    var group = card.closest(".stack");
    if (own && deckId) {
      var edit = el("a", "btn btn-primary", "Edit in deck editor");
      edit.href = "/decks/" + encodeURIComponent(deckId) + "/edit#card-" + encodeURIComponent(name);
      acts.appendChild(edit);
      if (group && cards.hasAttribute("data-own")) {
        var move = el("button", "btn", "Move to another category…");
        move.type = "button";
        move.addEventListener("click", function () { closeViewer(); pickCategory(card); });
        acts.appendChild(move);
      }
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
    acts.appendChild(ownBtn);
    var scry = el("a", "btn", "Open on Scryfall");
    scry.href = "https://scryfall.com/search?q=" + encodeURIComponent("!\"" + name + "\"");
    scry.target = "_blank";
    scry.rel = "noopener noreferrer";
    acts.appendChild(scry);
    side.appendChild(acts);
    var close = el("button", "btn close", "Close");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    close.addEventListener("click", closeViewer);
    box.appendChild(pic);
    box.appendChild(side);
    box.appendChild(close);
    viewer.appendChild(box);
    viewer.classList.add("open");
    close.focus();
  }
  viewer.addEventListener("click", function (e) { if (e.target === viewer) closeViewer(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && viewer.classList.contains("open")) closeViewer(); });

  // -- stacks and grid: tap to fan out, tap a card to open it ----------------------------------
  var isStacks = cards.classList.contains("stacks");
  cards.addEventListener("click", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card || !cards.contains(card) || dragged) return;
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
    if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("c")) {
      e.preventDefault();
      openViewer(e.target);
    }
  });

  // -- own deck: drag cards between categories, moves become one proposal ---------------------
  if (!own || !deckId) return;
  var moves = {}; // card name -> {from, to}
  var bar = document.createElement("div");
  bar.className = "movebar";
  bar.setAttribute("role", "region");
  bar.setAttribute("aria-label", "Pending category moves");
  var review = el("button", "btn-primary", "Review moves");
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
  }
  function moveCard(card, target) {
    var from = card.closest(".stack");
    if (!target || target === from) return;
    var name = card.getAttribute("data-card");
    var original = moves[name] ? moves[name].from : from.getAttribute("data-group");
    var to = target.getAttribute("data-group");
    $(".cards, .rows", target).appendChild(card);
    if (to === original) delete moves[name];
    else moves[name] = { from: original, to: to };
    refreshBar();
  }
  undo.addEventListener("click", function () { location.reload(); });
  review.addEventListener("click", function () {
    var changes = Object.keys(moves).map(function (name) { return { action: "set_category", card_name: name, categories: [moves[name].to] }; });
    if (!changes.length) return;
    review.disabled = true;
    status.textContent = "Making the proposal…";
    fetch("/api/v1/proposals", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify({ kind: "edit", deck_id: deckId, changes: changes })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.review_url) { location.href = d.review_url; return; }
      if (d.ok && d.proposal_id) { location.href = "/proposals/" + encodeURIComponent(d.proposal_id); return; }
      status.textContent = "Could not make the proposal: " + ((d && d.message) || "unknown error");
      review.disabled = false;
    }).catch(function () { status.textContent = "Could not make the proposal: no connection"; review.disabled = false; });
  });

  function pickCategory(card) {
    var groups = $$(".stack", cards).map(function (st) { return st.getAttribute("data-group"); });
    var from = card.closest(".stack").getAttribute("data-group");
    var choice = window.prompt("Move “" + card.getAttribute("data-card") + "” from " + from + " to which category?\n\n" + groups.join("\n"));
    if (!choice) return;
    var target = $$(".stack", cards).filter(function (st) { return st.getAttribute("data-group").toLowerCase() === choice.trim().toLowerCase(); })[0];
    if (target) moveCard(card, target);
  }

  // mouse drag (native drag and drop)
  var dragged = null;
  $$(".c, .row", cards).forEach(function (c) { c.setAttribute("draggable", "true"); });
  cards.addEventListener("dragstart", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card) return;
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
    var was = dragged;
    dragged = null;
    if (was) setTimeout(function () { /* swallow the click that follows a drop */ }, 0);
  });

  // touch drag: press and hold a card for 350 ms, then slide it onto another category
  var hold = null, touchCard = null, ghost = null, lastTarget = null;
  cards.addEventListener("touchstart", function (e) {
    var card = e.target.closest(".c, .row");
    if (!card || e.touches.length !== 1) return;
    var t = e.touches[0];
    hold = setTimeout(function () {
      touchCard = card;
      dragged = card;
      card.classList.add("dragging");
      ghost = card.cloneNode(true);
      ghost.classList.remove("dragging");
      ghost.style.cssText = "position:fixed;z-index:70;width:" + card.offsetWidth + "px;pointer-events:none;opacity:.9;left:" + (t.clientX - card.offsetWidth / 2) + "px;top:" + (t.clientY - 40) + "px;margin:0";
      document.body.appendChild(ghost);
      if (navigator.vibrate) navigator.vibrate(20);
    }, 350);
  }, { passive: true });
  cards.addEventListener("touchmove", function (e) {
    if (!touchCard) { clearTimeout(hold); return; }
    e.preventDefault();
    var t = e.touches[0];
    ghost.style.left = (t.clientX - touchCard.offsetWidth / 2) + "px";
    ghost.style.top = (t.clientY - 40) + "px";
    var under = document.elementFromPoint(t.clientX, t.clientY);
    var st = under ? under.closest(".stack") : null;
    if (lastTarget && lastTarget !== st) lastTarget.classList.remove("dropping");
    if (st) st.classList.add("dropping");
    lastTarget = st;
  }, { passive: false });
  function endTouch() {
    clearTimeout(hold);
    if (!touchCard) return;
    if (lastTarget) { lastTarget.classList.remove("dropping"); moveCard(touchCard, lastTarget); }
    touchCard.classList.remove("dragging");
    if (ghost) ghost.remove();
    ghost = null; lastTarget = null; touchCard = null;
    setTimeout(function () { dragged = null; }, 0);
  }
  cards.addEventListener("touchend", endTouch);
  cards.addEventListener("touchcancel", endTouch);
})();
