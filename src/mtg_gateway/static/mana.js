/* Mana and rules-text symbols as the gateway's own discs, the same markup mana.py renders
   server-side (the SVG sprite with the glyphs is on every page). Loaded on every page; used by
   the card viewer, the suggestion lists and the deck editor. */
(function () {
  "use strict";
  var COLOURS = "WUBRG";
  var GLYPHS = "WUBRGCTQSEP";
  var WORDS = { W: "white", U: "blue", B: "black", R: "red", G: "green", C: "colorless", T: "tap", Q: "untap",
    S: "snow", E: "energy", P: "Phyrexian" };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function svgUse(id) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#ms-" + id);
    svg.appendChild(use);
    return svg;
  }
  function describe(sym) {
    return sym.toUpperCase().split("/").map(function (p) { return WORDS[p] || p; }).join(" or ");
  }
  /* One symbol as a disc. */
  function pip(sym, small) {
    var raw = sym.trim();
    var parts = raw.toUpperCase().split("/");
    var colours = parts.filter(function (p) { return COLOURS.indexOf(p) >= 0; });
    var phy = parts.indexOf("P") >= 0;
    var i = el("i", "pip" + (small ? " sm" : ""));
    i.setAttribute("role", "img");
    i.setAttribute("aria-label", describe(raw));
    if (colours.length) {
      i.classList.add("pip-" + colours[0]);
      if (colours.length >= 2) { i.classList.add("hy"); i.setAttribute("data-b", colours[1]); }
      if (colours.length === 1 && COLOURS.indexOf(parts[0]) < 0 && !phy) {
        i.appendChild(el("b", null, parts[0]));            // {2/W}
      } else {
        i.appendChild(svgUse(phy ? "P" : colours[0]));
      }
      return i;
    }
    var key = parts[0];
    if (key.length === 1 && GLYPHS.indexOf(key) >= 0) {
      i.classList.add("pip-" + key);
      i.appendChild(svgUse(key));
      return i;
    }
    var text = raw.length > 3 ? raw.slice(0, 3) : raw;
    i.classList.add("pip-g");
    if (text.length > 1) i.classList.add("big");
    i.appendChild(el("b", null, text));
    return i;
  }
  /* A mana cost ({2}{G}{U}) as discs; "A // B" costs show both. */
  function mana(cost) {
    var wrap = el("span", "mana");
    var re = /\{([^}]+)\}|\/\//g, m;
    while ((m = re.exec(cost || ""))) {
      if (m[0] === "//") wrap.appendChild(el("span", "sep", "//")); else wrap.appendChild(pip(m[1], false));
    }
    return wrap;
  }
  /* Rules text with every {symbol} drawn; newlines kept (the element uses white-space: pre-line). */
  function symbolize(text, target) {
    var re = /\{([^}]+)\}/g, m, pos = 0;
    text = text || "";
    while ((m = re.exec(text))) {
      if (m.index > pos) target.appendChild(document.createTextNode(text.slice(pos, m.index)));
      target.appendChild(pip(m[1], true));
      pos = m.index + m[0].length;
    }
    if (pos < text.length) target.appendChild(document.createTextNode(text.slice(pos)));
    return target;
  }
  window.MtgMana = { pip: pip, mana: mana, symbolize: symbolize, describe: describe };
})();
