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

  // card name -> promise of the card's /cards/api/text answer (links, back_img and the rules text);
  // shared with the card search page (MtgCardView.text), so one opening reads it once
  var extras = Object.create(null);
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
  // a printing as every screen writes it: "SLD 1074" (the pages' data-set) reads "SLD #1074"
  function printLabel(set) { return String(set || "").trim().replace(/^([A-Za-z0-9]{2,6}) +(\S+)$/, "$1 #$2"); }
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
            rarity, price, artist, flavor, salt, rank, legal (comma list of formats), gc, mv, notes,
            custom (one of the member's custom cards: no rulings, prices or links, which are of
            printed cards), qty, cat, label (its count, category and colour tag in this deck)}
     actions: elements (buttons, links) shown under the text */
  // the name tile where the picture would be: the name and the type line, readable
  function nameTile(card, cls) {
    var tile = el("div", cls || "ph");
    tile.appendChild(el("b", "nm", card.name || ""));
    if (card.type) tile.appendChild(el("span", "ty", card.type));
    if (card.custom) tile.appendChild(el("span", "custom-badge", "Custom"));
    return tile;
  }
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
      // a picture that fails to load: the name and the type line in its place (feedback.js puts
      // a name-only tile after it first; this one replaces that)
      pic.addEventListener("error", function () {
        var was = pane.querySelector(".img-fallback");
        if (was) was.remove();
        pic.classList.add("img-broken");
        var tile = nameTile(card, "ph img-fallback");
        tile.setAttribute("role", "img");
        tile.setAttribute("aria-label", (card.name || "") + " (image unavailable)");
        pic.insertAdjacentElement("afterend", tile);
      });
    } else {
      pic = nameTile(card);
    }
    pane.appendChild(pic);
    /* A double-faced card's back: from the page's data or the card's links read below; the
       button shows once the back picture is known. */
    var front = pic.tagName === "IMG" ? pic.src : "";
    var flip = el("button", "btn small flip", "Show back");
    flip.type = "button";
    flip.hidden = true;
    var flipped = false;
    function showFace(back) {
      flipped = back;
      // a face that failed to load earlier left a name tile (feedback.js); the other face gets its chance
      pic.classList.remove("img-broken");
      var tile = pane.querySelector(".img-fallback");
      if (tile) tile.remove();
      pic.src = flipped ? flip.getAttribute("data-back") : front;
      pic.alt = (card.name || "") + (flipped ? " (back face)" : "");
      flip.textContent = flipped ? "Show front" : "Show back";
      flip.setAttribute("aria-pressed", flipped ? "true" : "false");
    }
    flip.addEventListener("click", function () { showFace(!flipped); });
    function offerBack(url) {
      if (!url || !front || flip.getAttribute("data-back")) return;
      flip.setAttribute("data-back", url);
      flip.setAttribute("aria-pressed", "false");
      flip.hidden = false;
      // a deck row with Show back face on opens on its back, as the page draws it; Show front is there
      if (card.flipped) showFace(true);
    }
    pane.appendChild(flip);
    var side = el("div", "info");
    var head = el("div", "head");
    var title = el("h3", null, card.name || "");
    title.id = "cardview-title";
    v.setAttribute("aria-labelledby", "cardview-title");  // the dialog is named after the card
    if (card.mana) title.appendChild(mana(card.mana));
    if (card.custom) {
      var mark = el("span", "custom-badge", "Custom");
      mark.title = "A custom card, made on Archidekt";
      title.appendChild(mark);
    }
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
    if (!card.text && !(card.faces && card.faces.length)) text.appendChild(el("p", "muted", card.loading ? "Reading the card text…" : (card.custom ? "This custom card has no rules text." : "No rules text available for this card.")));
    side.appendChild(text);
    var dl = el("dl", "facts");
    var printing = [printLabel(card.set), card.rarity ? titleCase(card.rarity) : "",
      card.finish && card.finish.toLowerCase() !== "normal" && card.finish.toLowerCase() !== "nonfoil" ? titleCase(card.finish) : ""].filter(Boolean).join(" · ");
    fact(dl, "Printing", printing);
    fact(dl, "Artist", card.artist);
    fact(dl, "Price", card.price && !isNaN(Number(card.price)) ? money(card.price, "usd") : "");
    fact(dl, "Salt", card.salt ? String(card.salt) : "");
    fact(dl, "EDHREC rank", card.rank ? Number(card.rank).toLocaleString() : "");
    if (card.gc) fact(dl, "Note", "Game changer", "gc");
    fact(dl, "Custom mana value", card.mv);
    fact(dl, "Card notes", card.notes, "notes");  // Archidekt's per-card notes, shown read-only
    if (card.custom) {
      // what the deck knows of the member's custom card: its place in this deck and its tag
      var inDeck = card.qty ? (String(card.qty) + (String(card.qty) === "1" ? " copy" : " copies")) : "";
      if (card.cat) inDeck += (inDeck ? " · " : "") + card.cat;
      fact(dl, "In this deck", inDeck);
      fact(dl, "Color tag", card.label ? String(card.label).split(",")[0] : "");
      // only what the gateway knows: no Scryfall card, and deck_stats leaves custom cards out of its price
      fact(dl, "Note", "Custom card, made on Archidekt: not on Scryfall, and not counted in the deck's price here.");
    }
    if (card.legal) {
      var chips = el("span", "chips");
      card.legal.split(",").filter(Boolean).forEach(function (f) { chips.appendChild(el("span", "chip", FORMATS[f] || titleCase(f))); });
      fact(dl, "Legal in", chips, "legal");
    }
    if (dl.childNodes.length) side.appendChild(dl);
    var more = el("div", "more");
    if (card.custom) {
      // no rulings, prices or card links: those are of printed cards
      if (actions && actions.length) {
        var cacts = el("div", "acts");
        actions.forEach(function (a) { cacts.appendChild(a); });
        side.appendChild(cacts);
      }
      box.appendChild(pane);
      box.appendChild(head);
      box.appendChild(side);
      v.appendChild(box);
      return closeBtn;
    }
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
    // Price history and Watch this card only when the gateway has watchlists on (theme.py marks <body>)
    var pbtn = document.body.hasAttribute("data-watchlist") ? priceButton(card) : null;
    if (pbtn) more.appendChild(pbtn);
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
      if (pbtn) side.appendChild(pbtn.box);
    }
    offerLinks(card.links);
    offerBack(card.back_img || card.back);
    if (card.name && (!card.links || (!card.back_img && !card.back && card.faces && card.faces.length > 1))) {
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
  /* Price history: the member's own watches of this card (/watchlist/api/card), the daily prices
     the gateway stored, newest first; a card not watched yet gets a Watch button. Nothing here
     asks Scryfall; watching does once (the same as the Watchlist page). */
  function money(v, cur) {
    if (v === null || v === undefined) return "no price";
    return (cur === "eur" ? "€" : "$") + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function priceButton(card) {
    var btn = el("button", "btn small", "Price history");
    btn.type = "button";
    btn.setAttribute("aria-expanded", "false");
    var box = el("div", "rulings prices");
    box.hidden = true;
    box.id = "cardview-prices";
    btn.setAttribute("aria-controls", box.id);
    btn.box = box;
    var name = card.name || "";
    var fin = (card.finish || "").toLowerCase();
    fin = fin === "foil" || fin === "etched" ? fin : "nonfoil";
    function show(watches) {
      box.textContent = "";
      if (!watches.length) {
        box.appendChild(el("p", "muted", "This card is not on your watchlist. Watch it to keep its price once a day."));
        var csrf = document.querySelector("input[name=csrf]");
        if (!csrf) return;
        var w = el("button", "btn small", "Watch this card");
        w.type = "button";
        w.addEventListener("click", function () {
          w.disabled = true;
          fetch("/watchlist/api/watch", { method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf.value, Accept: "application/json" },
            body: JSON.stringify({ name: name, finish: fin }) })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d && d.ok) { show([d.watch]); return; }
              w.disabled = false;
              box.appendChild(el("p", "notice error", (d && d.message) || "The card could not be watched."));
            })
            .catch(function () { w.disabled = false; box.appendChild(el("p", "notice error", "No connection.")); });
        });
        box.appendChild(w);
        return;
      }
      watches.forEach(function (v) {
        var where = printLabel([(v.set || "").toUpperCase(), v.collector_number || ""].filter(Boolean).join(" ")) || "any printing";
        box.appendChild(el("p", null, where + " · " + v.finish + ": " + money(v.price, v.currency) + (v.day ? " on " + v.day : "")));
        var rows = (v.history || []).filter(function (h) { return h.price !== null; }).reverse().slice(0, 30);
        if (!rows.length) { box.appendChild(el("p", "muted", "No price yet; it comes with the next daily check.")); return; }
        var table = el("table");
        var head = el("tr");
        head.appendChild(el("th", null, "Day"));
        head.appendChild(el("th", null, "Price"));
        head.childNodes.forEach(function (th) { th.setAttribute("scope", "col"); });
        table.appendChild(head);
        rows.forEach(function (h) {
          var tr = el("tr");
          var th = el("th", null, h.day);
          th.setAttribute("scope", "row");
          tr.appendChild(th);
          tr.appendChild(el("td", null, money(h.price, v.currency)));
          table.appendChild(tr);
        });
        box.appendChild(table);
      });
      var a = el("a", null, "Open the watchlist");
      a.href = "/watchlist";
      box.appendChild(a);
    }
    btn.addEventListener("click", function () {
      var opening = box.hidden;
      box.hidden = !opening;
      btn.setAttribute("aria-expanded", opening ? "true" : "false");
      if (!opening) return;
      box.textContent = "";
      box.appendChild(el("p", "muted", "Reading the price history…"));
      getJSON("/watchlist/api/card?name=" + encodeURIComponent(name)).then(function (x) {
        if (!x.ok || !x.d || !x.d.ok) {
          box.textContent = "";
          box.appendChild(el("p", "notice error", "The price history could not be read. Press Price history again to retry."));
          return;
        }
        show(x.d.watches || []);
      }).catch(function () {
        box.textContent = "";
        box.appendChild(el("p", "notice error", "No connection. Press Price history again to retry."));
      });
    });
    return btn;
  }
  /* Reads the data-* attributes the server puts on card rows and image cards. */
  function fromElement(node) {
    var faces = [];
    try { faces = JSON.parse(node.getAttribute("data-faces") || "[]"); } catch (e) { faces = []; }
    return {
      name: node.getAttribute("data-card") || "",
      img: node.getAttribute("data-img") || "",
      // the back face's picture: a two-faced card's (Show back face) or a custom card's, through
      // the gateway
      back_img: node.getAttribute("data-back") || "",
      flipped: node.hasAttribute("data-flipped"),
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
      gc: node.hasAttribute("data-gc"),
      mv: node.getAttribute("data-mv") || "",
      notes: node.getAttribute("data-notes") || "",
      custom: node.hasAttribute("data-custom"),
      qty: node.getAttribute("data-qty") || "",
      cat: node.getAttribute("data-cat") || "",
      label: node.getAttribute("data-label") || ""
    };
  }
  window.MtgCardView = { text: extraFor, open: open, update: update, close: close, fromElement: fromElement, mana: mana, symbolize: symbolize };
})();
