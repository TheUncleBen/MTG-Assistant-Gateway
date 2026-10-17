/* The Guide page (guide.py): search over the sections with a list of links to the ones that
   match, the contents rail's current entry, the phone drawer and the "Back to top" button. Loaded with defer under script-src 'self'; without
   it the whole guide is visible and every link works, so everything here only adds. */
(function () {
  "use strict";
  var root = document.querySelector(".guide");
  if (!root) return;
  var input = document.getElementById("guide-q");
  var status = document.getElementById("guide-status");
  var results = document.getElementById("guide-results");
  var searchBox = root.querySelector(".gsearch");
  var drawer = root.querySelector("details.gnav");
  var nomatch = root.querySelector(".nomatch");
  var totop = document.querySelector(".gtop");
  var sections = Array.prototype.slice.call(root.querySelectorAll(".gsec"));
  var parts = Array.prototype.slice.call(root.querySelectorAll(".gpart"));
  var links = Array.prototype.slice.call(root.querySelectorAll(".gnav a[href^='#']"));
  var narrow = window.matchMedia("(max-width: 899px)");

  function linkFor(id) {
    for (var i = 0; i < links.length; i++) {
      if (links[i].getAttribute("href") === "#" + id) return links[i];
    }
    return null;
  }
  function navItem(id) {
    var a = linkFor(id);
    return a ? a.parentNode : null;
  }
  function fold(s) {
    return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  // -- search ---------------------------------------------------------------------------------
  var texts = sections.map(function (sec) {
    return fold((sec.getAttribute("data-title") || "") + " " + sec.textContent);
  });
  // A section's body text (its heading left out) with whitespace collapsed, folded one character
  // at a time so a match found in the folded copy maps back onto the text as written.
  var bodies = [];
  function body(i) {
    if (bodies[i]) return bodies[i];
    // the text nodes in order, a space between two that sit in different blocks (a list item,
    // a table cell, a paragraph) and none from the section's heading or the "#" anchor links
    var raw = "", lastBlock = null;
    var walker = document.createTreeWalker(sections[i], NodeFilter.SHOW_TEXT);
    for (var node = walker.nextNode(); node; node = walker.nextNode()) {
      var el = node.parentNode;
      if (el.closest("h3, .anchor")) continue;
      var block = el.closest("p, li, td, th, h4, div, dt, dd, pre, figcaption, section");
      if (lastBlock && block !== lastBlock) raw += " ";
      raw += node.nodeValue;
      lastBlock = block;
    }
    raw = raw.replace(/\s+/g, " ").trim();
    var folded = "", at = [];
    for (var k = 0; k < raw.length; k++) {
      var f = fold(raw.charAt(k));
      for (var m = 0; m < f.length; m++) at.push(k);
      folded += f;
    }
    bodies[i] = { raw: raw, folded: folded, at: at };
    return bodies[i];
  }
  // A line of text around the first place one of the words appears, the word itself marked.
  function snippet(i, words) {
    var b = body(i);
    for (var w = 0; w < words.length; w++) {
      var pos = b.folded.indexOf(words[w]);
      if (pos === -1) continue;
      var from = b.at[pos], to = b.at[pos + words[w].length - 1] + 1;
      var start = Math.max(0, from - 40), end = Math.min(b.raw.length, to + 70);
      if (start > 0) { var sp = b.raw.indexOf(" ", start); if (sp !== -1 && sp < from) start = sp + 1; }
      if (end < b.raw.length) { var ep = b.raw.lastIndexOf(" ", end); if (ep > to) end = ep; }
      var out = document.createElement("span");
      out.className = "snip";
      out.appendChild(document.createTextNode((start > 0 ? "…" : "") + b.raw.slice(start, from)));
      var mk = document.createElement("mark");
      mk.textContent = b.raw.slice(from, to);
      out.appendChild(mk);
      out.appendChild(document.createTextNode(b.raw.slice(to, end) + (end < b.raw.length ? "…" : "")));
      return out;
    }
    return null;
  }
  function closeDrawer() {
    if (narrow.matches && drawer) drawer.removeAttribute("open");
  }
  function apply() {
    var q = fold(input.value).trim();
    var words = q ? q.split(/\s+/) : [];
    var shown = 0;
    if (results) results.textContent = "";
    sections.forEach(function (sec, i) {
      var ok = words.every(function (w) { return texts[i].indexOf(w) !== -1; });
      sec.hidden = !ok;
      var li = navItem(sec.id);
      if (li) li.hidden = !ok;
      if (ok) shown++;
      if (ok && words.length && results) {
        // one link per matching section: the jump works like the rail's own links
        var item = document.createElement("li");
        var a = document.createElement("a");
        a.href = "#" + sec.id;
        var t = document.createElement("span");
        t.className = "t";
        t.textContent = sec.getAttribute("data-title") || sec.id;
        a.appendChild(t);
        var sn = snippet(i, words);
        if (sn) a.appendChild(sn);
        a.addEventListener("click", closeDrawer);
        item.appendChild(a);
        results.appendChild(item);
      }
    });
    if (results) results.hidden = !results.firstChild;
    parts.forEach(function (part) {
      var any = Array.prototype.some.call(part.querySelectorAll(".gsec"), function (s) { return !s.hidden; });
      part.hidden = !any;
      var li = navItem(part.id);
      if (li) li.hidden = !any;
    });
    if (nomatch) nomatch.hidden = !(words.length && shown === 0);
    if (status) {
      status.textContent = !words.length
        ? ""
        : shown === 0
          ? "No section matches “" + input.value.trim() + "”."
          : shown + " of " + sections.length + " section" + (sections.length === 1 ? "" : "s") +
            " match “" + input.value.trim() + "”. Pick one below to go to it.";
    }
    highlight();
  }
  if (input && searchBox) {
    searchBox.hidden = false;
    input.addEventListener("input", apply);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (input.value) { input.value = ""; apply(); }
      } else if ((e.key === "Enter" || e.key === "ArrowDown") && results && results.firstChild) {
        // Enter goes straight to the first match; Down moves into the list of matches
        e.preventDefault();
        var first = results.querySelector("a");
        if (e.key === "Enter") first.click(); else first.focus();
      }
    });
    input.addEventListener("search", apply); // the box's own clear button
  }
  // In the list of matches: Down and Up move through it (Up from the first goes back to the
  // box), Home and End go to its ends, Escape goes back to the box with the search kept.
  if (input && results) {
    results.addEventListener("keydown", function (e) {
      var all = Array.prototype.slice.call(results.querySelectorAll("a"));
      var at = all.indexOf(document.activeElement);
      if (at === -1) return;
      var to = null;
      if (e.key === "ArrowDown") to = all[Math.min(at + 1, all.length - 1)];
      else if (e.key === "ArrowUp") to = at === 0 ? input : all[at - 1];
      else if (e.key === "Home") to = all[0];
      else if (e.key === "End") to = all[all.length - 1];
      else if (e.key === "Escape") to = input;
      if (!to) return;
      e.preventDefault();  // the keys move the focus, not the page
      to.focus();
    });
  }

  // -- current entry in the rail ----------------------------------------------------------------
  var current = null;
  function highlight() {
    // The section being read: the one under a line a little below the top bar (where an anchor
    // jump puts a heading), else the last one that starts above it, else the first.
    var line = 76 + window.innerHeight * 0.12;
    var active = null;
    var first = null;
    var above = null;
    for (var i = 0; i < sections.length; i++) {
      var sec = sections[i];
      if (sec.hidden) continue;
      if (!first) first = sec;
      var r = sec.getBoundingClientRect();
      if (r.top <= line && r.bottom > line) { active = sec; break; }
      if (r.top <= line) above = sec;
      if (r.top > line) break;
    }
    if (!active) active = above || first;
    // at the very bottom of the page the last visible section is the one being read
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) {
      for (var j = sections.length - 1; j >= 0; j--) { if (!sections[j].hidden) { active = sections[j]; break; } }
    }
    var id = active ? active.id : null;
    if (id === current) return;
    current = id;
    links.forEach(function (a) {
      a.removeAttribute("aria-current");
      a.classList.remove("in");
    });
    if (!active) return;
    var a = linkFor(id);
    if (a) {
      a.setAttribute("aria-current", "true");
      var part = active.closest(".gpart");
      var pa = part ? linkFor(part.id) : null;
      if (pa) pa.classList.add("in");
      if (!narrow.matches && drawer && drawer.open && !(results && !results.hidden)) {
        // keep the current entry in view inside the sticky rail (not while a search lists its
        // matches above the rail: those stay where the reader is looking)
        var side = root.querySelector(".gside");
        var r = a.getBoundingClientRect(), s = side.getBoundingClientRect();
        if (r.top < s.top || r.bottom > s.bottom) a.scrollIntoView({ block: "nearest" });
      }
    }
  }
  var pending = false;
  function onScroll() {
    if (pending) return;
    pending = true;
    window.requestAnimationFrame(function () { pending = false; highlight(); showTop(); });
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function () { onScroll(); }, { rootMargin: "-35% 0px -60% 0px" });
    sections.forEach(function (s) { io.observe(s); });
  }

  // -- the phone drawer ---------------------------------------------------------------------------
  function fitDrawer() {
    if (!drawer) return;
    if (narrow.matches) drawer.removeAttribute("open"); else drawer.setAttribute("open", "");
  }
  fitDrawer();
  if (narrow.addEventListener) narrow.addEventListener("change", fitDrawer);
  else if (narrow.addListener) narrow.addListener(fitDrawer);
  links.forEach(function (a) {
    a.addEventListener("click", closeDrawer);
  });

  // -- back to top ------------------------------------------------------------------------------------
  function showTop() {
    if (totop) totop.classList.toggle("hide", window.scrollY < 600);
  }
  if (totop) {
    // the link's own jump to #top does the scrolling (it works without script too); afterwards the
    // search box takes focus so a keyboard reader lands at the start of the page
    totop.addEventListener("click", function () {
      window.setTimeout(function () {
        var focusTarget = input && !searchBox.hidden ? input : links[0];
        if (focusTarget) focusTarget.focus({ preventScroll: true });
        showTop();
      }, 0);
    });
  }
  highlight();
  showTop();
})();
