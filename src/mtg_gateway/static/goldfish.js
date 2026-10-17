/* Goldfish by hand (/decks/<id>/goldfish): the deck shuffled in this browser, an opening hand of
   seven with London mulligans (shuffle the hand back, draw seven, then put one card on the bottom
   per mulligan; in a Commander game the first mulligan is free), then draw, play a card to the
   battlefield, graveyard or exile, tap and untap, and next turn (untap and draw; the game is on the
   play, so turn 1 has no draw). A Commander deck starts with its commander in the command zone.
   The deck comes from the page (#goldfish-data); nothing is fetched or sent and nothing is kept.
   Selecting a card opens the shared card viewer (static/cardview.js) with what can be done with it,
   so mouse, touch and keyboard all work the same way. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var dataNode = $("#goldfish-data");
  if (!dataNode) return;
  var deck;
  try { deck = JSON.parse(dataNode.textContent); } catch (e) { return; }
  var CardView = window.MtgCardView;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function randomBelow(n) {
    if (window.crypto && window.crypto.getRandomValues) {
      var buf = new Uint32Array(1), limit = Math.floor(4294967296 / n) * n;
      do { window.crypto.getRandomValues(buf); } while (buf[0] >= limit);
      return buf[0] % n;
    }
    return Math.floor(Math.random() * n);
  }
  function shuffle(list) {
    for (var i = list.length - 1; i > 0; i--) {
      var j = randomBelow(i + 1), t = list[i];
      list[i] = list[j]; list[j] = t;
    }
    return list;
  }

  // Every physical copy gets its own id ("c1", "c2", ...); zones hold ids, top of library first.
  var cards = {}, all = [], commanders = [], next = 1;
  function add(card, isCommander) {
    var id = "c" + next++;
    cards[id] = { id: id, card: card, commander: !!isCommander };
    return id;
  }
  (deck.commanders || []).forEach(function (c) { commanders.push(add(c, true)); });
  (deck.library || []).forEach(function (c) {
    for (var i = 0; i < (c.qty || 1); i++) all.push(add(c, false));
  });
  var isCommanderGame = commanders.length > 0;

  var g;  // the game: zones, turn, phase ("mulligan", "bottom" or "play"), mulligans, tapped ids
  function newGame() {
    g = {
      library: shuffle(all.slice()), hand: [], battlefield: [], graveyard: [], exile: [],
      command: commanders.slice(), tapped: {}, turn: 0, phase: "mulligan", mulligans: 0, picked: {},
      // for the guide: a land played this turn, and a tap or a spell this game
      landTurn: 0, acted: false
    };
    draw(7);
  }
  function draw(n) {
    var got = [];
    for (var i = 0; i < n && g.library.length; i++) {
      var id = g.library.shift();
      g.hand.push(id);
      got.push(id);
    }
    return got;
  }
  // London mulligan: cards to put on the bottom when keeping (a Commander game's first is free)
  function toBottom() { return Math.max(0, g.mulligans - (isCommanderGame && g.mulligans > 0 ? 1 : 0)); }
  function zoneOf(id) {
    var names = ["hand", "battlefield", "graveyard", "exile", "command", "library"];
    for (var i = 0; i < names.length; i++) if (g[names[i]].indexOf(id) >= 0) return names[i];
    return null;
  }
  function moveTo(id, zone, bottom) {
    var from = zoneOf(id);
    if (zone === "battlefield" && from !== "battlefield") {
      if (cards[id].card.land) g.landTurn = g.turn; else g.acted = true;
    }
    if (from) g[from].splice(g[from].indexOf(id), 1);
    delete g.tapped[id];
    if (zone === "library" && !bottom) g.library.unshift(id);
    else g[zone].push(id);
  }
  function nameOf(id) { return cards[id].card.name; }

  // -- drawing the page ----------------------------------------------------------------------------
  var live = $("#gf-live");
  function say(text) {
    // set after a tick so a repeated sentence is announced again
    live.textContent = "";
    setTimeout(function () { live.textContent = text; }, 30);
  }
  function cardButton(id, zone) {
    var c = cards[id], card = c.card;
    var b = el("button", "gf-card");
    b.type = "button";
    b.setAttribute("data-id", id);
    b.setAttribute("data-card", card.name);
    var label = card.name + (c.commander ? ", commander" : "");
    if (g.tapped[id]) { b.classList.add("tapped"); label += ", tapped"; }
    if (g.phase === "bottom" && zone === "hand") {
      b.setAttribute("aria-pressed", g.picked[id] ? "true" : "false");
      if (g.picked[id]) b.appendChild(el("span", "tag", "Bottom"));
    }
    b.setAttribute("aria-label", label);
    b.title = card.name;
    if (card.img) {
      var img = el("img");
      img.src = card.img;
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      b.insertBefore(img, b.firstChild);
    } else {
      var ph = el("span", "ph");
      ph.appendChild(el("span", "nm", card.name));
      ph.appendChild(el("span", "ty", card.type || ""));
      b.insertBefore(ph, b.firstChild);
    }
    return b;
  }
  function fill(zone, rows, empty) {
    var box = $("#gf-" + zone);
    if (!box) return;
    $$(".gf-row", box).forEach(function (r) { r.remove(); });
    var ids = rows.reduce(function (a, r) { return a.concat(r); }, []);
    $(".gf-n", box).textContent = "(" + ids.length + ")";
    rows.forEach(function (row, i) {
      if (!row.length && i > 0) return;
      var r = el("div", "gf-row");
      if (!row.length) r.appendChild(el("p", "gf-empty", empty));
      row.forEach(function (id) { r.appendChild(cardButton(id, zone)); });
      box.appendChild(r);
    });
  }
  function show(act, on) { var b = $(".gf-controls [data-act='" + act + "']"); if (b) b.hidden = !on; }
  function render(focusId) {
    var lands = g.battlefield.filter(function (id) { return cards[id].card.land; });
    var others = g.battlefield.filter(function (id) { return !cards[id].card.land; });
    fill("command", [g.command], "Empty.");
    fill("battlefield", [others, lands], "Nothing on the battlefield yet.");
    fill("hand", [g.hand], "No cards in hand.");
    fill("graveyard", [g.graveyard], "Empty.");
    fill("exile", [g.exile], "Empty.");
    var status = (g.turn ? "Turn " + g.turn : "Opening hand") + " · Library " + g.library.length +
      " · Hand " + g.hand.length + " · Graveyard " + g.graveyard.length + " · Exile " + g.exile.length;
    $("#gf-status").textContent = status;
    var hint = "";
    var picked = Object.keys(g.picked).length, need = toBottom();
    if (g.phase === "mulligan") {
      hint = "Keep this hand, or take a mulligan: the hand goes back, the library is shuffled and you draw " +
        "seven again" + (isCommanderGame && g.mulligans === 0 ? " (the first mulligan is free)." : ".");
      if (g.mulligans) hint += " Mulligans so far: " + g.mulligans + "; keeping puts " + plural(need, "card") + " on the bottom.";
    } else if (g.phase === "bottom") {
      hint = "Choose " + plural(need, "card") + " to put on the bottom of your library (" + picked + " chosen).";
    } else {
      hint = g.library.length ? "Select a card to play, tap or move it." : "The library is empty.";
    }
    $("#gf-hint").textContent = hint;
    guideStep();
    show("keep", g.phase === "mulligan");
    show("mulligan", g.phase === "mulligan" && g.mulligans < 7);
    show("bottom", g.phase === "bottom");
    var bottomBtn = $(".gf-controls [data-act='bottom']");
    bottomBtn.textContent = "Put " + plural(need, "card") + " on the bottom";
    bottomBtn.disabled = picked !== need;
    show("draw", g.phase === "play");
    show("next", g.phase === "play");
    show("untap", g.phase === "play");
    $(".gf-controls [data-act='draw']").disabled = !g.library.length;
    if (focusId) {
      var node = $(".gf-card[data-id='" + focusId + "']");
      if (node) node.focus();
    }
  }

  // -- the guide: the step the game is at, and hiding it for the account ---------------------------
  function guideStep() {
    var steps = $$("#gf-guide .gf-steps li");
    if (!steps.length) return;
    var now;
    if (g.phase === "mulligan") now = "hand";
    else if (g.phase === "bottom") now = "bottom";
    else if (g.landTurn !== g.turn && g.turn < 3) now = "land";
    else if (!g.acted && g.turn < 3) now = "cast";
    else if (g.turn < 3) now = "next";
    else now = "again";
    var reached = false;
    steps.forEach(function (li) {
      var key = li.getAttribute("data-step");
      if (key === now) { li.setAttribute("aria-current", "step"); reached = true; li.classList.remove("done"); }
      else {
        li.removeAttribute("aria-current");
        // a step before the current one is done (the bottom step only when a mulligan needed it)
        li.classList.toggle("done", !reached && !(key === "bottom" && !g.mulligans));
      }
    });
  }
  var csrfInput = $("input[name=csrf]");
  function setGuide(hidden) {
    var panel = $("#gf-guide"), showBtn = $(".gf-controls [data-guide='show']");
    if (!panel || !showBtn) return;
    panel.hidden = hidden;
    showBtn.hidden = !hidden;
    (hidden ? showBtn : $("[data-guide='hide']", panel)).focus();
    say(hidden ? "Guide hidden. How to play shows it again." : "Guide shown.");
    // remembered for the account (account_prefs); without a session token it lasts for this page only
    if (!csrfInput) return;
    fetch("/goldfish/guide", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfInput.value, Accept: "application/json" },
      body: JSON.stringify({ hidden: hidden })
    }).catch(function () { /* offline: shown or hidden for this page only */ });
  }
  // phones: the step the game is at, or all six
  var allBtn = $("#gf-guide .gf-allsteps"), stepList = $("#gf-steps");
  if (allBtn && stepList) {
    allBtn.addEventListener("click", function () {
      var all = !stepList.classList.contains("all");
      stepList.classList.toggle("all", all);
      allBtn.setAttribute("aria-expanded", all ? "true" : "false");
      allBtn.textContent = allBtn.getAttribute(all ? "data-current" : "data-all");
    });
  }
  $$("[data-guide]").forEach(function (b) {
    b.addEventListener("click", function () { setGuide(b.getAttribute("data-guide") === "hide"); });
  });

  // -- the game's buttons ----------------------------------------------------------------------------
  function startTurn() {
    g.turn += 1;
    g.tapped = {};
    if (g.turn === 1) return [];  // on the play: no draw on turn 1
    return draw(1);
  }
  var actions = {
    keep: function () {
      if (toBottom() > 0) { g.phase = "bottom"; g.picked = {}; say("Choose " + plural(toBottom(), "card") + " to put on the bottom."); return; }
      g.phase = "play";
      startTurn();
      say("Kept " + plural(g.hand.length, "card") + ". Turn 1.");
    },
    mulligan: function () {
      g.library = shuffle(g.library.concat(g.hand));
      g.hand = [];
      g.mulligans += 1;
      draw(7);
      say("Mulligan " + g.mulligans + ": seven new cards.");
    },
    bottom: function () {
      var ids = g.hand.filter(function (id) { return g.picked[id]; });
      if (ids.length !== toBottom()) return;
      ids.forEach(function (id) { moveTo(id, "library", true); });
      g.picked = {};
      g.phase = "play";
      startTurn();
      say("Put " + plural(ids.length, "card") + " on the bottom; kept " + plural(g.hand.length, "card") + ". Turn 1.");
    },
    draw: function () {
      var got = draw(1);
      say(got.length ? "Drew " + nameOf(got[0]) + "." : "The library is empty.");
    },
    next: function () {
      var got = startTurn();
      say("Turn " + g.turn + ": everything untapped" + (got.length ? ", drew " + nameOf(got[0]) + "." : g.library.length ? "." : ", and the library is empty."));
    },
    untap: function () { g.tapped = {}; say("Everything untapped."); },
    reset: function () { newGame(); say("New game: shuffled and drew seven."); }
  };
  $$(".gf-controls [data-act]").forEach(function (b) {
    b.addEventListener("click", function () {
      actions[b.getAttribute("data-act")]();
      render(null);
      // keep the keyboard in the controls when the pressed button is now hidden or disabled
      if (b.hidden || b.disabled) {
        var first = $(".gf-controls button:not([hidden]):not([disabled])");
        if (first) first.focus();
      }
    });
  });

  // -- a card: the viewer with what can be done with it -------------------------------------------
  var ZONE_WORDS = { battlefield: "the battlefield", graveyard: "the graveyard", exile: "exile", hand: "your hand", command: "the command zone" };
  function afterViewer(fn) {
    // The viewer closes through history.back() (so the phone's Back button closes it too); run the
    // move once it has closed, so the focus it gives back lands on the moved card.
    var done = false;
    function run() { if (done) return; done = true; window.removeEventListener("popstate", onPop); fn(); }
    function onPop() { setTimeout(run, 0); }
    if (CardView) CardView.close();
    if ($(".cardview.open")) {
      window.addEventListener("popstate", onPop);
      setTimeout(run, 500);
    } else {
      run();
    }
  }
  function actionButton(label, fn) {
    var b = el("button", "btn", label);
    b.type = "button";
    b.addEventListener("click", function () { afterViewer(fn); });
    return b;
  }
  function moveAction(id, label, zone, bottom) {
    return actionButton(label, function () {
      moveTo(id, zone, bottom);
      if (zone === "library") say(nameOf(id) + " put on the " + (bottom ? "bottom" : "top") + " of the library.");
      else say(nameOf(id) + " moved to " + ZONE_WORDS[zone] + ".");
      render(zone === "library" ? null : id);
      if (zone === "library") { var h = $("#gf-hand-h"); if (h) { h.tabIndex = -1; h.focus(); } }
    });
  }
  function cardActions(id) {
    var zone = zoneOf(id), c = cards[id], acts = [];
    if (g.phase !== "play") return acts;
    if (zone === "battlefield") {
      acts.push(actionButton(g.tapped[id] ? "Untap" : "Tap", function () {
        if (g.tapped[id]) delete g.tapped[id]; else { g.tapped[id] = true; g.acted = true; }
        say(nameOf(id) + (g.tapped[id] ? " tapped." : " untapped."));
        render(id);
      }));
    } else {
      acts.push(moveAction(id, zone === "command" ? "Cast to the battlefield" : "Play to the battlefield", "battlefield"));
    }
    if (zone !== "hand") acts.push(moveAction(id, "Return to hand", "hand"));
    if (zone !== "graveyard") acts.push(moveAction(id, "Put in the graveyard", "graveyard"));
    if (zone !== "exile") acts.push(moveAction(id, "Exile", "exile"));
    if (c.commander && zone !== "command") acts.push(moveAction(id, "Return to the command zone", "command"));
    if (zone === "hand") {
      acts.push(moveAction(id, "Put on top of the library", "library", false));
      acts.push(moveAction(id, "Put on the bottom of the library", "library", true));
    }
    return acts;
  }
  document.addEventListener("click", function (e) {
    var node = e.target.closest ? e.target.closest(".gf-card[data-id]") : null;
    if (!node) return;
    var id = node.getAttribute("data-id");
    if (g.phase === "bottom") {
      if (zoneOf(id) !== "hand") return;
      if (g.picked[id]) delete g.picked[id];
      else if (Object.keys(g.picked).length < toBottom()) g.picked[id] = true;
      else { say("Already " + plural(toBottom(), "card") + " chosen; unselect one first."); return; }
      say(nameOf(id) + (g.picked[id] ? " goes on the bottom." : " stays in hand."));
      render(id);
      return;
    }
    if (!CardView) return;
    var card = cards[id].card;
    CardView.open({
      name: card.name, img: card.img || "", set: card.set || "", type: card.type || "",
      mana: card.mana || "", text: card.text || "", pt: card.pt || "", loyalty: card.loyalty || "",
      // links: none, so the viewer does not look the card up (only a two-faced card's back is read)
      faces: card.faces || [], links: []
    }, cardActions(id));
  });

  newGame();
  render(null);
})();
