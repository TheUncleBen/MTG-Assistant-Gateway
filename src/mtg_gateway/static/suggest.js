/* Typed suggestions as a themed, accessible combobox (WAI-ARIA combobox with a listbox popup),
   replacing the browser's own <datalist> list. Any <input data-suggest="cards"> gets one: the
   gateway's /scan/api/search answers from its in-memory card-name catalog, so each list costs one
   quick same-origin request; stale answers never overwrite newer ones (every request is
   abortable and numbered), repeated text is answered from a small memory, and a longer text
   whose shorter form already came back complete (fewer than the limit) is narrowed locally with
   no request at all. Keyboard: Down/Up move, Enter picks, Escape closes, Tab leaves. Picking
   fills the box and fires a "suggest:pick" event; with data-suggest-submit the form is sent too
   (the deck editor adds the card at once). Loaded on every page; does nothing without inputs. */
(function () {
  "use strict";
  var LIMIT = 20;
  var MIN = 2;
  var DELAY = 90;
  var memory = {};      // query -> names (this page's lifetime)
  var cards = {};       // folded name -> card summary (rich lists: mana, type, picture)
  var complete = {};    // query -> true when the answer had fewer than LIMIT names
  var counter = 0;

  function fold(s) {
    // the same folding as scan/names.py: hyphens, apostrophes and commas count as spaces
    s = s.toLowerCase();
    try { s = s.normalize("NFKD").replace(/[\u0300-\u036f]/g, ""); } catch (e) { /* old engine */ }
    return s.replace(/[-'\u2019,]/g, " ").replace(/\s+/g, " ").trim();
  }
  function rank(f, q) {
    // 0 = starts with, 1 = a word starts with, 2 = inside a word, -1 = absent (as names.py)
    var at = f.indexOf(q);
    if (at < 0) return -1;
    if (at === 0) return 0;
    while (at > 0 && /[a-z0-9]/.test(f.charAt(at - 1))) {
      at = f.indexOf(q, at + 1);
      if (at < 0) return 2;
    }
    return 1;
  }
  function narrow(names, q) {
    var starts = [], words = [], inside = [];
    names.forEach(function (n) {
      var where = rank(fold(n), q);
      if (where === 0) starts.push(n); else if (where === 1) words.push(n); else if (where === 2) inside.push(n);
    });
    return starts.concat(words, inside).slice(0, LIMIT);
  }
  function highlight(li, name, q) {
    // fold without collapsing spaces, so the offsets line up with the name character for character
    var f = name.toLowerCase();
    try { f = f.normalize("NFKD").replace(/[\u0300-\u036f]/g, ""); } catch (e) { /* old engine */ }
    f = f.replace(/[-'\u2019,]/g, " ");
    var at = f.indexOf(q);
    if (at < 0 || f.length !== name.length) { li.textContent = name; return; }
    li.appendChild(document.createTextNode(name.slice(0, at)));
    var m = document.createElement("mark");
    m.textContent = name.slice(at, at + q.length);
    li.appendChild(m);
    li.appendChild(document.createTextNode(name.slice(at + q.length)));
  }

  function attach(input) {
    if (input.getAttribute("data-suggest-ready")) return;
    input.setAttribute("data-suggest-ready", "1");
    var wrap = document.createElement("span");
    wrap.className = "suggest";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var id = (input.id || "sg" + (++counter)) + "-list";
    var list = document.createElement("ul");
    list.className = "suggest-list";
    list.id = id;
    list.setAttribute("role", "listbox");
    list.hidden = true;
    wrap.appendChild(list);
    var status = document.createElement("span");
    status.className = "sr-only";
    status.setAttribute("aria-live", "polite");
    wrap.appendChild(status);
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", id);
    input.setAttribute("aria-haspopup", "listbox");
    input.autocomplete = "off";
    input.removeAttribute("list");

    var items = [], active = -1, timer = null, inflight = null, seq = 0, shownFor = "";
    var rich = input.hasAttribute("data-suggest-rich"), peeking = null;
    // data-suggest="static": the choices come with the page (data-options: a JSON list of
    // labels, or of {l: label, v: value}); picking fills the label, the form reads it by name.
    var fixed = null;
    if (input.getAttribute("data-suggest") === "static") {
      try { fixed = JSON.parse(input.getAttribute("data-options") || "[]").map(function (o) { return typeof o === "string" ? o : o.l; }); }
      catch (e) { fixed = []; }
    }

    function close() {
      list.hidden = true;
      list.textContent = "";
      items = [];
      active = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }
    function setActive(i) {
      if (active >= 0 && items[active]) items[active].setAttribute("aria-selected", "false");
      active = i;
      if (i < 0 || !items[i]) { input.removeAttribute("aria-activedescendant"); return; }
      items[i].setAttribute("aria-selected", "true");
      input.setAttribute("aria-activedescendant", items[i].id);
      if (items[i].scrollIntoView) items[i].scrollIntoView({ block: "nearest" });
    }
    /* Rich rows: the names show at once; one request then brings mana cost, type line and a small
       picture for the names not seen before, and each row is decorated in place. */
    function decorate(li, c) {
      if (!c || li.querySelector(".rich")) return;
      var row = document.createElement("span");
      row.className = "rich";
      if (c.image_small) {
        var img = document.createElement("img");
        img.src = c.image_small; img.alt = ""; img.loading = "lazy"; img.className = "thumb";
        row.appendChild(img);
      }
      var text = document.createElement("span");
      text.className = "txt";
      var nm = document.createElement("span");
      nm.className = "nm";
      while (li.firstChild) nm.appendChild(li.firstChild);
      if (c.mana_cost && window.MtgMana) nm.appendChild(window.MtgMana.mana(c.mana_cost));
      text.appendChild(nm);
      if (c.type_line) {
        var ty = document.createElement("span");
        ty.className = "ty"; ty.textContent = c.type_line;
        text.appendChild(ty);
      }
      row.appendChild(text);
      li.appendChild(row);
    }
    function peek(names) {
      var missing = names.filter(function (n) { return !(fold(n) in cards); });
      names.forEach(function (n, i) { if (cards[fold(n)] && items[i]) decorate(items[i], cards[fold(n)]); });
      if (!missing.length) return;
      if (peeking) peeking.abort();
      var ctrl = typeof AbortController === "function" ? new AbortController() : null;
      peeking = ctrl;
      var shown = shownFor;
      fetch("/scan/api/peek?names=" + encodeURIComponent(missing.join("|")), { credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined })
        .then(function (r) { return r.ok ? r.json() : { cards: [] }; })
        .then(function (d) {
          (d.cards || []).forEach(function (c) { if (c && c.name) cards[fold(c.name)] = c; });
          missing.forEach(function (n) { if (!(fold(n) in cards)) cards[fold(n)] = null; });
          if (shownFor !== shown || list.hidden) return;
          items.forEach(function (li) { decorate(li, cards[fold(li.getAttribute("data-name"))]); });
        })
        .catch(function () {});
    }
    function pick(i) {
      var name = items[i] ? items[i].getAttribute("data-name") : "";
      if (!name) return;
      input.value = name;
      close();
      input.dispatchEvent(new CustomEvent("suggest:pick", { bubbles: true, detail: { name: name, card: cards[fold(name)] || null } }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      if (input.hasAttribute("data-suggest-submit") && input.form) submit(input.form);
    }
    function submit(form) {
      if (form.requestSubmit) { form.requestSubmit(); return; }
      // older engines: run the form's own submit handlers; only navigate when none took over
      var ev = new Event("submit", { bubbles: true, cancelable: true });
      if (form.dispatchEvent(ev)) form.submit();
    }
    function show(names, q) {
      if (document.activeElement !== input) return;
      list.textContent = "";
      items = [];
      active = -1;
      if (!names.length) {
        var none = document.createElement("li");
        none.className = "none";
        none.textContent = "No card with that name";
        list.appendChild(none);
        status.textContent = "No card with that name";
      } else {
        names.forEach(function (n, i) {
          var li = document.createElement("li");
          li.id = id + "-" + i;
          li.setAttribute("role", "option");
          li.setAttribute("aria-selected", "false");
          li.setAttribute("data-name", n);
          highlight(li, n, q);
          li.addEventListener("mousedown", function (e) { e.preventDefault(); });
          li.addEventListener("click", function () { pick(i); });
          list.appendChild(li);
          items.push(li);
        });
        status.textContent = names.length + " suggestion" + (names.length === 1 ? "" : "s");
      }
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      shownFor = q;
      if (rich && names.length) peek(names);
    }
    function lookup() {
      var raw = input.value, q = fold(raw);
      if (q.length < MIN) { close(); if (inflight) inflight.abort(); return; }
      if (fixed) { show(narrow(fixed, q), q); return; }
      if (memory[q]) { show(memory[q], q); return; }
      // a shorter text whose answer was complete narrows locally: no request
      for (var n = q.length - 1; n >= MIN; n--) {
        var shorter = q.slice(0, n);
        if (complete[shorter]) {
          memory[q] = narrow(memory[shorter], q);
          complete[q] = true;
          show(memory[q], q);
          return;
        }
      }
      if (inflight) inflight.abort();
      var ctrl = typeof AbortController === "function" ? new AbortController() : null;
      inflight = ctrl;
      var mine = ++seq;
      fetch("/scan/api/search?q=" + encodeURIComponent(raw.trim()), { credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined })
        .then(function (r) {
          // signed out, busy or Scryfall down is not "no such card": close and remember nothing
          if (!r.ok) { if (mine === seq) close(); return null; }
          return r.json();
        })
        .then(function (d) {
          if (!d || mine !== seq) return;      // failed, or a newer request is out
          var names = (d.names || []).slice(0, LIMIT);
          memory[q] = names;
          if (names.length < LIMIT) complete[q] = true;
          if (fold(input.value) === q) show(names, q);
        })
        .catch(function () { /* aborted or offline: keep what is shown */ });
    }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = fold(input.value);
      if (fixed || memory[q]) { lookup(); return; }   // known text: no wait at all
      timer = setTimeout(lookup, DELAY);
    });
    input.addEventListener("focus", function () { if (fold(input.value).length >= MIN && !items.length) lookup(); });
    input.addEventListener("blur", function () { setTimeout(close, 0); });
    input.addEventListener("keydown", function (e) {
      if (list.hidden) {
        if (e.key === "ArrowDown" && fold(input.value).length >= MIN) { e.preventDefault(); lookup(); }
        return;
      }
      if (e.key === "ArrowDown") { e.preventDefault(); if (items.length) setActive((active + 1) % items.length); }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (items.length) setActive((active - 1 + items.length) % items.length); }
      else if (e.key === "Enter") { if (active >= 0) { e.preventDefault(); pick(active); } else if (items.length && input.hasAttribute("data-suggest-submit") && shownFor === fold(input.value)) { e.preventDefault(); pick(0); } else close(); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "Tab") { close(); }
    });
    // Enter with the list closed submits as usual; the form's own handlers decide.
  }

  function scan(root) {
    var inputs = (root || document).querySelectorAll("input[data-suggest]");
    for (var i = 0; i < inputs.length; i++) attach(inputs[i]);
  }
  scan(document);
  window.MtgSuggest = { attach: attach, scan: scan, narrow: narrow, fold: fold };
})();
