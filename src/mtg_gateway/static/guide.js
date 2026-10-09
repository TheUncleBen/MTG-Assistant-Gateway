/* The Guide page (guide.py): search over the sections, the contents rail's current entry, the
   phone drawer and the "Back to top" button. Loaded with defer under script-src 'self'; without
   it the whole guide is visible and every link works, so everything here only adds. */
(function () {
  "use strict";
  var root = document.querySelector(".guide");
  if (!root) return;
  var input = document.getElementById("guide-q");
  var status = document.getElementById("guide-status");
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
  function apply() {
    var q = fold(input.value).trim();
    var words = q ? q.split(/\s+/) : [];
    var shown = 0;
    sections.forEach(function (sec, i) {
      var ok = words.every(function (w) { return texts[i].indexOf(w) !== -1; });
      sec.hidden = !ok;
      var li = navItem(sec.id);
      if (li) li.hidden = !ok;
      if (ok) shown++;
    });
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
            " match “" + input.value.trim() + "”.";
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
      }
    });
    input.addEventListener("search", apply); // the box's own clear button
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
      if (!narrow.matches && drawer && drawer.open) {
        // keep the current entry in view inside the sticky rail
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
    a.addEventListener("click", function () { if (narrow.matches && drawer) drawer.removeAttribute("open"); });
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
