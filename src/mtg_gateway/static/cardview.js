/* The card viewer every page shares: the card large, the whole card readable as text (every
   face: name, mana cost, type line, rules text with its symbols drawn, power and toughness or
   loyalty), the printing, prices and legality, and the actions the page offers. Pages pass the
   card's data. Two reads are the viewer's own, both same-origin and only for the open card: the
   card's links and back-face picture (/cards/api/text, when the page did not pass them) and,
   when the Rulings button is pressed, the rulings (/cards/api/rulings). Mana and rules symbols are the gateway's own
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

  var extras = Object.create(null);   // card name -> promise of {links, back_img}
  var rulingsFor = Object.create(null); // card name -> promise of the rulings list
  function getJSON(url) {
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
  }
  function extraFor(name) {
    if (!extras[name]) {
      extras[name] = getJSON("/cards/api/text?name=" + encodeURIComponent(name))
        .then(function (x) { return x.ok && x.d && x.d.card ? x.d.card : null; })
        .catch(function () { delete extras[name]; return null; });
    }
    return extras[name];
  }
  // EDHREC's page for a card, in the form Scryfall's own card data links to (edhrec.com/route/?cc=)
  function edhrecUrl(name) {
    return "https://edhrec.com/route/?cc=" + encodeURIComponent(name.split(" // ")[0]).replace(/%20/g, "+");
  }
  function outLink(text, href) {
    var a = el("a", "btn small", text + " ↗");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.setAttribute("aria-label", text + " (opens a new tab)");
    return a;
  }

  function ensure() {
    if (viewer) return viewer;
    viewer = el("div", "cardview");
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-modal", "true");
    viewer.setAttribute("aria-label", "Card");  // replaced by aria-labelledby once a card is shown
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
    var closeBtn = draw(v, card, actions);
    v.classList.add("open");
    document.documentElement.classList.add("cardview-open");
    if (!pushed) {
      try { history.pushState({ cardview: 1 }, ""); pushed = true; } catch (e) { pushed = false; }
    }
    closeBtn.focus();
  }
  /* Redraws the open viewer with fuller data (a page that reads the rules text after opening);
     focus stays where it was (on the close button when it was inside the viewer). */
  function update(card, actions) {
    if (!viewer || !viewer.classList.contains("open")) return;
    var inside = viewer.contains(document.activeElement);
    var closeBtn = draw(viewer, card, actions);
    if (inside) closeBtn.focus();
  }
  function draw(v, card, actions) {
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
    /* A double-faced card's back: from the page's data or the card's links read below; the
       button shows once the back picture is known. */
    var front = pic.tagName === "IMG" ? pic.src : "";
    var flip = el("button", "btn small flip", "Show back");
    flip.type = "button";
    flip.hidden = true;
    var flipped = false;
    flip.addEventListener("click", function () {
      flipped = !flipped;
      // a face that failed to load earlier left a name tile (feedback.js); the other face gets its chance
      pic.classList.remove("img-broken");
      var tile = pane.querySelector(".img-fallback");
      if (tile) tile.remove();
      pic.src = flipped ? flip.getAttribute("data-back") : front;
      pic.alt = (card.name || "") + (flipped ? " (back face)" : "");
      flip.textContent = flipped ? "Show front" : "Show back";
      flip.setAttribute("aria-pressed", flipped ? "true" : "false");
    });
    function offerBack(url) {
      if (!url || !front || flip.getAttribute("data-back")) return;
      flip.setAttribute("data-back", url);
      flip.setAttribute("aria-pressed", "false");
      flip.hidden = false;
    }
    pane.appendChild(flip);
    var side = el("div", "info");
    var head = el("div", "head");
    var title = el("h3", null, card.name || "");
    title.id = "cardview-title";
    v.setAttribute("aria-labelledby", "cardview-title");  // the dialog is named after the card
    if (card.mana) title.appendChild(mana(card.mana));
    head.appendChild(title);
    var closeBtn = el("button", "close icon-only");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.setAttribute("title", "Close");
    closeBtn.appendChild(el("span", "x", "×"));
    closeBtn.addEventListener("click", close);
    head.appendChild(closeBtn);
    var faces = card.faces && card.faces.length > 1 ? card.faces : [{ type: card.type, text: card.text, pt: card.pt, loyalty: card.loyalty, flavor: card.flavor }];
    var text = el("div", "cardtext");
    faces.forEach(function (f, i) {
      if (faces.length > 1 && i === faces.length - 1 && card.flavor && !f.flavor) f.flavor = card.flavor;
      text.appendChild(faceBlock(f, faces.length === 1));
    });
    if (!card.text && !(card.faces && card.faces.length)) text.appendChild(el("p", "muted", card.loading ? "Reading the card text…" : "No rules text available for this card."));
    side.appendChild(text);
    var dl = el("dl", "facts");
    var printing = [card.set, card.rarity ? titleCase(card.rarity) : "",
      card.finish && card.finish.toLowerCase() !== "normal" && card.finish.toLowerCase() !== "nonfoil" ? titleCase(card.finish) : ""].filter(Boolean).join(" · ");
    fact(dl, "Printing", printing);
    fact(dl, "Artist", card.artist);
    fact(dl, "Price", card.price ? "$" + card.price : "");
    fact(dl, "Salt", card.salt ? String(card.salt) : "");
    fact(dl, "EDHREC rank", card.rank ? Number(card.rank).toLocaleString() : "");
    if (card.gc) fact(dl, "Note", "Game changer", "gc");
    if (card.legal) {
      var chips = el("span", "chips");
      card.legal.split(",").filter(Boolean).forEach(function (f) { chips.appendChild(el("span", "chip", FORMATS[f] || titleCase(f))); });
      fact(dl, "Legal in", chips, "legal");
    }
    if (dl.childNodes.length) side.appendChild(dl);
    var more = el("div", "more");
    var rbtn = el("button", "btn small", "Rulings");
    rbtn.type = "button";
    rbtn.setAttribute("aria-expanded", "false");
    var rbox = el("div", "rulings");
    rbox.hidden = true;
    rbox.id = "cardview-rulings";
    rbtn.setAttribute("aria-controls", rbox.id);
    rbtn.addEventListener("click", function () {
      var opening = rbox.hidden || rbox.hasAttribute("data-failed");  // after a failure, a press retries
      rbox.removeAttribute("data-failed");
      rbox.hidden = !opening;
      rbtn.setAttribute("aria-expanded", opening ? "true" : "false");
      if (!opening || rbox.getAttribute("data-done")) return;
      rbox.textContent = "";
      rbox.appendChild(el("p", "muted", "Reading the rulings…"));
      var name = card.name || "";
      if (!rulingsFor[name]) {
        rulingsFor[name] = getJSON("/cards/api/rulings?name=" + encodeURIComponent(name))
          .then(function (x) { if (!x.ok) delete rulingsFor[name]; return x; })
          .catch(function () { delete rulingsFor[name]; return { ok: false, d: null }; });
      }
      rulingsFor[name].then(function (x) {
        rbox.textContent = "";
        if (!x.ok || !x.d || !x.d.ok) {
          rbox.appendChild(el("p", "notice error", "The rulings could not be read" + (x.d && x.d.message ? ": " + x.d.message : ": no connection") + ". Press Rulings again to retry."));
          rbox.setAttribute("data-failed", "1");
          return;
        }
        rbox.setAttribute("data-done", "1");
        var rows = x.d.rulings || [];
        if (!rows.length) { rbox.appendChild(el("p", "muted", "This card has no rulings.")); return; }
        var list = el("ul");
        rows.forEach(function (r) {
          var li = el("li");
          if (r.date) {
            var t = el("time", null, r.date);
            t.setAttribute("datetime", r.date);
            li.appendChild(t);
          }
          li.appendChild(symbolize(r.text || "", el("p")));
          if (r.source) li.appendChild(el("span", "src muted", r.source));
          list.appendChild(li);
        });
        rbox.appendChild(list);
      });
    });
    more.appendChild(rbtn);
    more.appendChild(outLink("EDHREC", card.links && card.links.edhrec || edhrecUrl(card.name || "")));
    function offerLinks(links) {
      if (links && links.tcgplayer && !more.querySelector("[data-shop]")) {
        var t = outLink("TCGplayer", links.tcgplayer);
        t.setAttribute("data-shop", "tcgplayer");
        more.appendChild(t);
      }
    }
    if (card.name) {
      side.appendChild(more);
      side.appendChild(rbox);
    }
    offerLinks(card.links);
    offerBack(card.back_img);
    if (card.name && (!card.links || (!card.back_img && card.faces && card.faces.length > 1))) {
      extraFor(card.name).then(function (x) {
        if (!x || !viewer || !viewer.classList.contains("open") || !v.contains(more)) return;
        offerLinks(x.links);
        offerBack(x.back_img);
      });
    }
    if (actions && actions.length) {
      var acts = el("div", "acts");
      actions.forEach(function (a) { acts.appendChild(a); });
      side.appendChild(acts);
    }
    box.appendChild(pane);
    box.appendChild(head);
    box.appendChild(side);
    v.appendChild(box);
    return closeBtn;
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
  window.MtgCardView = { open: open, update: update, close: close, fromElement: fromElement, mana: mana, symbolize: symbolize };
})();
