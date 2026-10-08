/* The card viewer every page shares: the card large, and the whole card readable as text (every
   face: name, mana cost, type line, rules text, power and toughness or loyalty), plus the printing
   and finish. Pages pass the card's data and the actions they offer; nothing here talks to the
   server. Loaded before deck.js, companion.js and collection.js, which call window.MtgCardView. */
(function () {
  "use strict";
  var viewer = null;
  var lastFocus = null;
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function mana(cost) {
    // {2}{G}{U} -> the same pips the pages draw server-side (mana_html); hybrid shows both colours
    var wrap = el("span", "mana");
    wrap.setAttribute("aria-label", "mana cost " + cost);
    var re = /\{([^}]+)\}/g, m;
    while ((m = re.exec(cost))) {
      var parts = m[1].toUpperCase().split("/").filter(function (p) { return p !== "P"; });
      var colours = parts.filter(function (p) { return "WUBRG".indexOf(p) >= 0; });
      var pip;
      if (colours.length >= 2) {
        pip = el("i", "pip pip-" + colours[0] + " hy");
        pip.setAttribute("data-b", colours[1]);
      } else if (colours.length === 1) {
        pip = el("i", "pip pip-" + colours[0]);
      } else if (parts[0] === "C") {
        pip = el("i", "pip pip-C");
      } else {
        pip = el("i", "pip pip-g", parts[0] || m[1]);
      }
      wrap.appendChild(pip);
    }
    return wrap;
  }
  function ensure() {
    if (viewer) return viewer;
    viewer = el("div", "cardview");
    viewer.setAttribute("role", "dialog");
    viewer.setAttribute("aria-modal", "true");
    viewer.setAttribute("aria-label", "Card");
    document.body.appendChild(viewer);
    viewer.addEventListener("click", function (e) { if (e.target === viewer) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && viewer.classList.contains("open")) close(); });
    return viewer;
  }
  function close() {
    if (!viewer) return;
    viewer.classList.remove("open");
    viewer.textContent = "";
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  function faceBlock(face, isOnly) {
    var block = el("div", "face");
    if (!isOnly) {
      var head = el("h4", null, face.name || "");
      if (face.mana) head.appendChild(mana(face.mana));
      block.appendChild(head);
    }
    var line = [face.type, face.pt ? face.pt : "", face.loyalty ? "Loyalty " + face.loyalty : ""].filter(Boolean).join(" · ");
    if (line) block.appendChild(el("p", "meta", line));
    if (face.text) block.appendChild(el("p", "rules", face.text));
    return block;
  }
  /* card: {name, img, set, type, mana, text, pt, loyalty, finish, faces: [{name, mana, type, text, pt, loyalty}],
            rarity, price, artist, flavor, salt, rank, legal (comma list of formats), gc}
     actions: elements (buttons, links) shown under the text */
  function open(card, actions) {
    var v = ensure();
    lastFocus = document.activeElement;
    v.textContent = "";
    var box = el("div", "box");
    var pic;
    if (card.img) {
      pic = el("img");
      pic.src = card.img.replace("/small/", "/normal/");
      pic.alt = card.name || "";
    } else {
      pic = el("div", "ph", card.name || "");
    }
    var side = el("div", "info");
    var title = el("h3", null, card.name || "");
    if (card.mana) title.appendChild(mana(card.mana));
    side.appendChild(title);
    var faces = card.faces && card.faces.length > 1 ? card.faces : [{ type: card.type, text: card.text, pt: card.pt, loyalty: card.loyalty }];
    var text = el("div", "cardtext");
    faces.forEach(function (f) { text.appendChild(faceBlock(f, faces.length === 1)); });
    if (!card.text && !(card.faces && card.faces.length)) text.appendChild(el("p", "meta muted", "No rules text available for this card."));
    side.appendChild(text);
    if (card.flavor) text.appendChild(el("p", "flavor", card.flavor));
    var printing = [card.set, card.rarity ? card.rarity.charAt(0).toUpperCase() + card.rarity.slice(1) : "",
      card.finish && card.finish.toLowerCase() !== "normal" && card.finish.toLowerCase() !== "nonfoil" ? card.finish.charAt(0).toUpperCase() + card.finish.slice(1) : "",
      card.artist ? "Art: " + card.artist : ""].filter(Boolean).join(" · ");
    if (printing) side.appendChild(el("p", "meta printing", printing));
    var facts = [card.price ? "$" + card.price : "", card.salt ? "Salt " + card.salt : "",
      card.rank ? "EDHREC rank " + card.rank : "", card.gc ? "Game changer" : ""].filter(Boolean).join(" · ");
    if (facts) side.appendChild(el("p", "meta facts", facts));
    if (card.legal) {
      var legal = el("p", "meta legal");
      legal.appendChild(el("b", null, "Legal in: "));
      legal.appendChild(document.createTextNode(card.legal.split(",").filter(Boolean).join(", ")));
      side.appendChild(legal);
    }
    if (actions && actions.length) {
      var acts = el("div", "acts");
      actions.forEach(function (a) { acts.appendChild(a); });
      side.appendChild(acts);
    }
    var closeBtn = el("button", "btn close", "Close");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.addEventListener("click", close);
    box.appendChild(pic);
    box.appendChild(side);
    box.appendChild(closeBtn);
    v.appendChild(box);
    v.classList.add("open");
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
  window.MtgCardView = { open: open, close: close, fromElement: fromElement, mana: mana };
})();
