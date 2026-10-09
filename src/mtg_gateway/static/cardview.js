/* The card viewer every page shares: the card large, the whole card readable as text (every
   face: name, mana cost, type line, rules text with its symbols drawn, power and toughness or
   loyalty), the printing, prices and legality, and the actions the page offers. Pages pass the
   card's data; nothing here talks to the server. Mana and rules symbols are the gateway's own
   glyphs (mana.py puts the SVG sprite on every page; this draws the same discs). A dialog with
   a focus trap, Escape, a click on the backdrop and the phone's Back button all close it.
   Loaded after static/mana.js (on every page) and before deck.js, companion.js, compare.js and
   collection.js, which call window.MtgCardView. */
(function () {
  "use strict";
  var viewer = null;
  var lastFocus = null;
  var FORMATS = { commander: "Commander", paupercommander: "Pauper Commander", duel: "Duel Commander",
    oathbreaker: "Oathbreaker", standard: "Standard", pioneer: "Pioneer", modern: "Modern", legacy: "Legacy",
    vintage: "Vintage", pauper: "Pauper", brawl: "Brawl", standardbrawl: "Standard Brawl", historic: "Historic",
    historicbrawl: "Historic Brawl", alchemy: "Alchemy", timeless: "Timeless", explorer: "Explorer",
    gladiator: "Gladiator", premodern: "Premodern", oldschool: "Old School", penny: "Penny Dreadful",
    predh: "PreDH", future: "Future", canlander: "Canadian Highlander", "1v1": "1v1", tlr: "Timeless",
    competitivebrawl: "Competitive Brawl" };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  // static/mana.js is deferred at the end of the body, so it is read when a card opens, not now
  function mana(cost) { return window.MtgMana.mana(cost); }
  function symbolize(text, target) { return window.MtgMana.symbolize(text, target); }

  function ensure() {
    if (viewer) return viewer;
    viewer = el("div", "cardview");
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-modal", "true");
    viewer.setAttribute("aria-label", "Card");
    document.body.appendChild(viewer);
    viewer.addEventListener("click", function (e) { if (e.target === viewer) close(); });
    document.addEventListener("keydown", function (e) {
      if (!viewer.classList.contains("open")) return;
      if (e.key === "Escape") { e.preventDefault(); close(); return; }
      if (e.key === "Tab") {                                  // keep focus inside the dialog
        var f = viewer.querySelectorAll("a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex='-1'])");
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    return viewer;
  }
  /* Opening pushes a history entry so the phone's Back button (and the browser's) closes the
     viewer instead of leaving the page; closing from the page goes back over that entry. */
  var pushed = false;
  function hide() {
    if (!viewer) return;
    viewer.classList.remove("open");
    viewer.textContent = "";
    document.documentElement.classList.remove("cardview-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function close() {
    if (!viewer || !viewer.classList.contains("open")) return;
    if (pushed) { pushed = false; history.back(); return; }
    hide();
  }
  window.addEventListener("popstate", function () {
    if (history.state && history.state.cardview) return;
    if (viewer && viewer.classList.contains("open")) { pushed = false; hide(); }
  });

  function titleCase(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }
  function faceBlock(face, isOnly) {
    var block = el("div", "face");
    if (!isOnly) {
      var head = el("h4", null, face.name || "");
      if (face.mana) head.appendChild(mana(face.mana));
      block.appendChild(head);
    }
    var line = el("p", "typeline");
    if (face.type) line.appendChild(el("span", "type", face.type));
    if (face.pt) line.appendChild(el("span", "pt", face.pt));
    if (face.loyalty) line.appendChild(el("span", "pt", "Loyalty " + face.loyalty));
    if (line.childNodes.length) block.appendChild(line);
    if (face.text) block.appendChild(symbolize(face.text, el("p", "rules")));
    if (face.flavor) block.appendChild(el("p", "flavor", face.flavor));
    return block;
  }
  function fact(dl, term, value, cls) {
    if (!value) return;
    dl.appendChild(el("dt", null, term));
    var dd = el("dd", cls || null);
    if (typeof value === "string") dd.textContent = value; else dd.appendChild(value);
    dl.appendChild(dd);
  }
  /* card: {name, img, set, type, mana, text, pt, loyalty, finish, faces: [{name, mana, type, text, pt, loyalty}],
            rarity, price, artist, flavor, salt, rank, legal (comma list of formats), gc}
     actions: elements (buttons, links) shown under the text */
  function open(card, actions) {
    var v = ensure();
    lastFocus = document.activeElement;
    v.textContent = "";
    var box = el("div", "box");
    var pane = el("div", "pane");
    var pic;
    if (card.img) {
      pic = el("img");
      pic.src = card.img.replace("/small/", "/normal/");
      pic.alt = card.name || "";
      pic.decoding = "async";
    } else {
      pic = el("div", "ph", card.name || "");
    }
    pane.appendChild(pic);
    var side = el("div", "info");
    var head = el("div", "head");
    var title = el("h3", null, card.name || "");
    if (card.mana) title.appendChild(mana(card.mana));
    head.appendChild(title);
    var closeBtn = el("button", "close icon-only");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.appendChild(el("span", "x", "×"));
    closeBtn.addEventListener("click", close);
    head.appendChild(closeBtn);
    var faces = card.faces && card.faces.length > 1 ? card.faces : [{ type: card.type, text: card.text, pt: card.pt, loyalty: card.loyalty, flavor: card.flavor }];
    var text = el("div", "cardtext");
    faces.forEach(function (f, i) {
      if (faces.length > 1 && i === faces.length - 1 && card.flavor && !f.flavor) f.flavor = card.flavor;
      text.appendChild(faceBlock(f, faces.length === 1));
    });
    if (!card.text && !(card.faces && card.faces.length)) text.appendChild(el("p", "muted", "No rules text available for this card."));
    side.appendChild(text);
    var dl = el("dl", "facts");
    var printing = [card.set, card.rarity ? titleCase(card.rarity) : "",
      card.finish && card.finish.toLowerCase() !== "normal" && card.finish.toLowerCase() !== "nonfoil" ? titleCase(card.finish) : ""].filter(Boolean).join(" · ");
    fact(dl, "Printing", printing);
    fact(dl, "Artist", card.artist);
    fact(dl, "Price", card.price ? "$" + card.price : "");
    fact(dl, "Salt", card.salt ? String(card.salt) : "");
    fact(dl, "EDHREC rank", card.rank ? String(card.rank) : "");
    if (card.gc) fact(dl, "Note", "Game changer", "gc");
    if (card.legal) {
      var chips = el("span", "chips");
      card.legal.split(",").filter(Boolean).forEach(function (f) { chips.appendChild(el("span", "chip", FORMATS[f] || titleCase(f))); });
      fact(dl, "Legal in", chips, "legal");
    }
    if (dl.childNodes.length) side.appendChild(dl);
    if (actions && actions.length) {
      var acts = el("div", "acts");
      actions.forEach(function (a) { acts.appendChild(a); });
      side.appendChild(acts);
    }
    box.appendChild(pane);
    box.appendChild(head);
    box.appendChild(side);
    v.appendChild(box);
    v.classList.add("open");
    document.documentElement.classList.add("cardview-open");
    if (!pushed) {
      try { history.pushState({ cardview: 1 }, ""); pushed = true; } catch (e) { pushed = false; }
    }
    closeBtn.focus();
  }
  /* Reads the data-* attributes the server puts on card rows and image cards. */
  function fromElement(node) {
    var faces = [];
    try { faces = JSON.parse(node.getAttribute("data-faces") || "[]"); } catch (e) { faces = []; }
    return {
      name: node.getAttribute("data-card") || "",
      img: node.getAttribute("data-img") || "",
      set: node.getAttribute("data-set") || "",
      type: node.getAttribute("data-type") || "",
      mana: node.getAttribute("data-mana") || "",
      text: node.getAttribute("data-text") || "",
      pt: node.getAttribute("data-pt") || "",
      loyalty: node.getAttribute("data-loyalty") || "",
      finish: node.getAttribute("data-finish") || "",
      faces: faces,
      rarity: node.getAttribute("data-rarity") || "",
      price: node.getAttribute("data-price") || "",
      artist: node.getAttribute("data-artist") || "",
      flavor: node.getAttribute("data-flavor") || "",
      salt: node.getAttribute("data-salt") || "",
      rank: node.getAttribute("data-rank") || "",
      legal: node.getAttribute("data-legal") || "",
      gc: node.hasAttribute("data-gc")
    };
  }
  window.MtgCardView = { open: open, close: close, fromElement: fromElement, mana: mana, symbolize: symbolize };
})();
