/* The Guide (guide.py): the live search list under the box (guide.py's /guide/search.json), the
   old one-page addresses (/guide#topic) sent to the topic's page, and the "Back to top" button on
   a long topic. Loaded with defer under script-src 'self'; without it the search is a plain form
   and every link works, so everything here only adds. */
(function () {
  "use strict";
  // /guide#how-approve from before the split: the topic's own page (a heading inside a topic
  // keeps its anchor there)
  var anchors = document.getElementById("guide-anchors");
  if (anchors && location.hash.length > 1) {
    var map = {};
    try { map = JSON.parse(anchors.textContent || "{}") || {}; } catch (e) { map = {}; }
    var id = location.hash.slice(1), owner = map[id];
    if (owner) location.replace("/guide/" + owner + (owner === id ? "" : "#" + id));
  }

  var input = document.getElementById("guide-q");
  var status = document.getElementById("guide-status");
  var results = document.getElementById("guide-results");
  if (input && status && results && window.fetch) {
    var src = input.getAttribute("data-search") || "/guide/search.json";
    var timer = null, seq = 0;
    // the results page's own list (server drawn) gives way to the live one while you type
    var fixed = document.querySelector(".gstatic");
    function clear() {
      results.hidden = true;
      results.textContent = "";
      status.textContent = "";
      if (fixed) fixed.hidden = false;
    }
    function row(h) {
      var li = document.createElement("li"), a = document.createElement("a");
      a.href = h.href;
      var t = document.createElement("span"); t.className = "t"; t.textContent = h.title;
      var w = document.createElement("span"); w.className = "where"; w.textContent = h.part;
      var snip = document.createElement("span"); snip.className = "snip";
      var at = h.word ? h.snippet.toLowerCase().indexOf(h.word) : -1;
      if (at < 0) snip.textContent = h.snippet;
      else {
        snip.appendChild(document.createTextNode(h.snippet.slice(0, at)));
        var mark = document.createElement("mark"); mark.textContent = h.snippet.slice(at, at + h.word.length);
        snip.appendChild(mark);
        snip.appendChild(document.createTextNode(h.snippet.slice(at + h.word.length)));
      }
      a.appendChild(t); a.appendChild(w); a.appendChild(snip);
      li.appendChild(a);
      return li;
    }
    function show(d) {
      results.textContent = "";
      var hits = d && d.hits ? d.hits : [];
      hits.forEach(function (h) { results.appendChild(row(h)); });
      results.hidden = !hits.length;
      if (fixed) fixed.hidden = true;
      status.textContent = hits.length
        ? hits.length + " topic" + (hits.length === 1 ? "" : "s") + " match"
        : "Nothing in the guide matches that.";
    }
    function lookup() {
      var q = input.value.trim();
      if (!q) { clear(); return; }
      var mine = ++seq;
      fetch(src + "?q=" + encodeURIComponent(q), { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (mine === seq && d && d.ok) show(d); })
        .catch(function () { /* the form's own Search still works */ });
    }
    input.addEventListener("input", function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(lookup, 180);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { input.value = ""; clear(); }
      if (e.key === "ArrowDown" && !results.hidden) {
        var first = results.querySelector("a");
        if (first) { e.preventDefault(); first.focus(); }
      }
    });
    results.addEventListener("keydown", function (e) {
      var links = Array.prototype.slice.call(results.querySelectorAll("a"));
      var i = links.indexOf(document.activeElement);
      if (e.key === "ArrowDown" && i >= 0 && i + 1 < links.length) { e.preventDefault(); links[i + 1].focus(); }
      if (e.key === "ArrowUp" && i > 0) { e.preventDefault(); links[i - 1].focus(); }
      if (e.key === "ArrowUp" && i === 0) { e.preventDefault(); input.focus(); }
      if (e.key === "Escape") { input.focus(); input.value = ""; clear(); }
    });
  }

  // The contents beside a topic: open on a wide screen, folded under their button on a phone
  // (guide.py renders them open, since a style sheet cannot open a closed details element)
  var drawer = document.querySelector("details.gnav");
  if (drawer && window.matchMedia) {
    var wide = window.matchMedia("(min-width: 900px)");
    function fit() { drawer.open = wide.matches; }
    fit();
    if (wide.addEventListener) wide.addEventListener("change", fit);
  }

  // Back to top once the reader is a screen down a topic
  var totop = document.querySelector(".gtop");
  if (totop) {
    var seen = false;
    function onScroll() {
      var want = window.scrollY > window.innerHeight;
      if (want !== seen) { seen = want; totop.classList.toggle("hide", !want); }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
})();
