/* The card search page (/cards, cardsearch.py and cardsearch_view.py).

   Live results: every change to the filters asks /cards/api/search for the first page about
   400 ms after the last keystroke or tap (one request in flight; a newer change cancels the older
   one), puts the HTML it answers in place and writes the filters into the address, so Back, a
   reload and a shared link show the same search. The next page loads by itself when the end of
   the list comes into view, or with "Load more cards". The pickers (type, set, block, criteria)
   read their lists from /cards/api/options once, as soon as the page loads. Under 600 px the
   filters are a full-screen sheet (static/searchpanel.js, the panel the search pages share);
   "Show cards" closes it.

   A picture opens the shared card viewer (static/cardview.js) with an Add to deck action, and
   Add to deck opens a small dialog that picks one of the member's decks (the cached list behind
   /api/decks/mine), a quantity and a category (the deck's own, read once per deck), then saves
   through the deck page's own edit endpoint, POST /api/v1/decks/{id}/edit: the proposals path
   applied at once with its snapshot, a "save anyway" question when the hand-edit rule asks, the
   review page when writes are off. Nothing is written any other way. Loaded after cardview.js. */
(function () {
  "use strict";
  var form = document.getElementById("cs-form");
  var results = document.getElementById("cs-results");
  if (!form || !results) return;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var csrfInput = $("input[name=csrf]");
  var CSRF = csrfInput ? csrfInput.value : "";
  var linked = results.getAttribute("data-linked") === "1";
  var enc = encodeURIComponent;
  var LAST_DECK = "mtg.addToDeck";
  var DEBOUNCE_MS = 400;
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function icon(name) {
    var paths = {
      plus: "<path d='M12 5v14M5 12h14'/>",
      x: "<path d='M6 6l12 12M18 6L6 18'/>",
      external: "<path d='M14 4h6v6M20 4l-9 9'/><path d='M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5'/>"
    };
    var span = el("span");
    span.innerHTML = "<svg class='i' viewBox='0 0 24 24' aria-hidden='true'>" + (paths[name] || "") + "</svg>";
    return span.firstChild;
  }
  function api(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = CSRF;
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () { return { ok: false, status: r.status, message: "The gateway sent an answer this page could not read. Reload the page to see whether the change was made." }; });
    });
  }

  // -- the filters as an address: every non-empty field, in the form's order ---------------------
  function params() {
    var out = new URLSearchParams();
    var data = new FormData(form);
    data.forEach(function (value, key) {
      if (typeof value !== "string") return;
      var v = value.trim();
      if (!v) return;
      // a picker's own text box is for searching its list; its chips carry the values
      var box = form.elements[key];
      if (box && box.nodeType === 1 && box.closest && box.closest("[data-picker]") && box.type === "text") return;
      out.append(key, v);
    });
    // rows left empty send nothing at all (their selects always have a value)
    ["st:sto:stv", "pc:po:pv", "fs:f"].forEach(function (spec) {
      var keys = spec.split(":"), cols = keys.map(function (k) { return out.getAll(k); });
      keys.forEach(function (k) { out.delete(k); });
      var rows = $$("[data-row]", form).filter(function (r) { return r.querySelector("[name=" + keys[0] + "]"); });
      rows.forEach(function (r) {
        var vals = keys.map(function (k) { var f = r.querySelector("[name=" + k + "]"); return f ? f.value.trim() : ""; });
        if (vals[vals.length - 1]) keys.forEach(function (k, i) { out.append(k, vals[i]); });
      });
      void cols;
    });
    // the defaults stay out of the address
    if (out.get("cmp") === "ge" || !out.getAll("c").length) out.delete("cmp");
    if (out.get("idm") === "within" || !out.getAll("id").length) out.delete("idm");
    if (out.get("order") === "name") out.delete("order");
    if (out.get("dir") === "auto") out.delete("dir");
    var games = out.getAll("g").filter(function (g) { return g !== "any"; });
    out.delete("g");
    if (!(games.length === 1 && games[0] === "paper")) {
      if (games.length) games.forEach(function (g) { out.append("g", g); }); else out.append("g", "any");
    }
    return out;
  }
  function chosenCount(p) {
    var n = 0, seen = {};
    p.forEach(function (v, k) {
      if (["q", "order", "dir", "view", "cmp", "idm", "tany", "isany"].indexOf(k) >= 0) return;
      if (k === "sto" || k === "stv" || k === "po" || k === "pv" || k === "fs") return;
      if (k === "c" || k === "id" || k === "r" || k === "g") { if (seen[k]) return; seen[k] = 1; }
      n += 1;
    });
    return n;
  }

  // -- live results ----------------------------------------------------------------------------
  var countEl = $("#cs-count"), statusEl = $("#cs-status"), moreWrap = $("#cs-more");
  var moreBtn = moreWrap ? $("[data-more]", moreWrap) : null;
  var openBtn = $(".sp-open", form), badge = openBtn ? $(".n", openBtn) : null;
  // lastQs: the search last asked for (an unchanged form asks nothing again). shownQs: the search
  // whose tiles are on screen; "Load more" asks for its next page, never the form's newer text.
  var timer = null, ctrl = null, moreCtrl = null, page = 1, hasMore = false, loadingMore = false;
  var lastQs = null, shownQs = null;
  var startTpl = $("#cs-start"), startHtml = startTpl ? startTpl.innerHTML : null;
  function setStatus(text, isError) {
    statusEl.textContent = "";
    if (!text) return;
    var p = el("p", "notice" + (isError ? " error" : ""), text);
    statusEl.appendChild(p);
  }
  function setCount(total, chosen) {
    if (!countEl) return;
    if (!chosen) { countEl.textContent = "Cards"; return; }
    countEl.textContent = typeof total === "number" ? total.toLocaleString() + (total === 1 ? " card" : " cards") : "Cards";
  }
  function updateBadge(p) {
    if (!badge) return;
    var n = chosenCount(p);
    badge.textContent = n ? String(n) : "";
    var show = $(".sp-show", form);
    if (show) show.textContent = countEl && /\d/.test(countEl.textContent) ? "Show " + countEl.textContent : "Show cards";
  }
  function showMore(on) {
    hasMore = !!on;
    if (moreWrap) moreWrap.hidden = !on;
  }
  function moreDone() {
    loadingMore = false;
    if (moreBtn) { moreBtn.disabled = false; moreBtn.removeAttribute("aria-busy"); }
  }
  // a next page asked for the tiles on screen is not wanted once another search starts or lands
  function stopMore() {
    if (moreCtrl) moreCtrl.abort();
    moreCtrl = null;
    moreDone();
  }
  // a failed search: the same search can be asked again (Search, Enter), and tiles left from
  // another search are taken away rather than shown under the error as if they were its answer
  function failed(qs, text) {
    lastQs = null;
    stopMore();
    if (shownQs !== qs) {
      results.innerHTML = "";
      shownQs = null;
      setCount(null, true);
    }
    setStatus(text, true);
    showMore(false);
  }
  function run(immediate) {
    clearTimeout(timer);
    timer = setTimeout(search, immediate ? 0 : DEBOUNCE_MS);
  }
  function search() {
    var p = params();
    var qs = p.toString();
    updateBadge(p);
    if (qs === lastQs) return;
    lastQs = qs;
    // the view is remembered per person (a cookie), so a shared address shows the reader's own
    var addr = new URLSearchParams(qs);
    addr.delete("view");
    addr = addr.toString();
    try { history.replaceState(null, "", "/cards" + (addr ? "?" + addr : "")); } catch (e) { /* a sandboxed frame */ }
    if (ctrl) ctrl.abort();
    ctrl = window.AbortController ? new AbortController() : null;
    stopMore();
    results.setAttribute("aria-busy", "true");
    fetch("/cards/api/search?" + qs, { credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) { return r.json().then(function (d) { d.status = r.status; return d; }); })
      .then(function (d) {
        results.removeAttribute("aria-busy");
        page = 1;
        if (!d.ok) {
          failed(qs, d.message || "The cards could not be looked up.");
          return;
        }
        stopMore();
        shownQs = qs;
        setStatus("");
        if (d.empty) {
          if (startHtml !== null) results.innerHTML = startHtml;
          setCount(null, false);
          showMore(false);
        } else {
          results.innerHTML = d.html;
          setCount(d.total, true);
          showMore(d.has_more);
          nearEnd();
          if (d.warnings && d.warnings.length) setStatus(d.warnings.join(" "), false);
        }
        updateBadge(p);
      })
      .catch(function (e) {
        if (e && e.name === "AbortError") return;
        results.removeAttribute("aria-busy");
        failed(qs, "No connection; the cards could not be looked up.");
      });
  }
  function loadMore() {
    if (!hasMore || loadingMore || shownQs === null) return;
    loadingMore = true;
    if (moreBtn) { moreBtn.disabled = true; moreBtn.setAttribute("aria-busy", "true"); }
    var forQs = shownQs;
    var p = new URLSearchParams(forQs);
    p.set("page", String(page + 1));
    var mine = window.AbortController ? new AbortController() : null;
    moreCtrl = mine;
    fetch("/cards/api/search?" + p.toString(), { credentials: "same-origin", signal: mine ? mine.signal : undefined })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (forQs !== shownQs || (mine && moreCtrl !== mine)) return;  // a newer search is on screen
        moreCtrl = null;
        moreDone();
        if (!d.ok) { setStatus(d.message || "More cards could not be loaded.", true); return; }
        var list = results.querySelector(".cardgrid,.cardlist");
        if (!list) return;
        list.insertAdjacentHTML("beforeend", d.html);
        page = d.page;
        showMore(d.has_more);
        nearEnd();
      })
      .catch(function (e) {
        if ((e && e.name === "AbortError") || forQs !== shownQs || (mine && moreCtrl !== mine)) return;
        moreCtrl = null;
        moreDone();
        setStatus("No connection; more cards could not be loaded.", true);
      });
  }
  // the observer only speaks when the end comes into reach, so a page too short to push it
  // back out (a tall window, a short page) asks for the next one itself
  function nearEnd() {
    if (!hasMore || !moreWrap || !window.IntersectionObserver) return;
    requestAnimationFrame(function () {
      if (moreWrap.getBoundingClientRect().top < window.innerHeight + 600) loadMore();
    });
  }
  if (moreBtn) moreBtn.addEventListener("click", loadMore);
  if (moreWrap && window.IntersectionObserver) {
    new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) loadMore();
    }, { rootMargin: "600px 0px" }).observe(moreWrap);
  }
  hasMore = moreWrap ? !moreWrap.hidden : false;
  // the page's own first search: when it failed, the same search can be asked again
  if (!$(".notice.error", statusEl)) lastQs = shownQs = params().toString();
  updateBadge(params());

  form.addEventListener("submit", function (e) { e.preventDefault(); run(true); closePanel(); });
  // the sort, direction and view sit in the results bar at the panel's foot, inside the form
  form.addEventListener("input", function (e) {
    if (e.target.closest && e.target.closest("[data-picker]") && e.target.type === "text") return;
    run(e.target.type !== "text" && e.target.type !== "search");
  });
  form.addEventListener("change", function (e) {
    if (e.target.closest && e.target.closest("[data-picker]") && e.target.type === "text") return;
    run(e.target.type !== "text" && e.target.type !== "search");
  });

  // -- rows: stats, prices and formats; "Add" copies the last row, × removes one ----------------
  form.addEventListener("click", function (e) {
    var add = e.target.closest("[data-add-row]");
    if (add) {
      var set = add.closest("[data-rows]"), rows = $(".rows", set), max = +set.getAttribute("data-max") || 4;
      var last = rows.lastElementChild;
      if (!last || rows.children.length >= max) return;
      var copy = last.cloneNode(true);
      $$(".msel-btn", copy).forEach(function (b) { b.remove(); });
      $$("input", copy).forEach(function (i) { i.value = ""; });
      $$("select", copy).forEach(function (s, i) { s.selectedIndex = last.querySelectorAll("select")[i].selectedIndex; s.removeAttribute("data-msel"); });
      rows.appendChild(copy);
      add.hidden = rows.children.length >= max;
      var first = copy.querySelector("input,select");
      if (first) first.focus();
      return;
    }
    var rm = e.target.closest(".rm");
    if (rm) {
      var row = rm.closest("[data-row]"), holder = row.parentNode, setEl = row.closest("[data-rows]");
      if (holder.children.length > 1) row.remove();
      else $$("input", row).forEach(function (i) { i.value = ""; });
      var addBtn = setEl && $("[data-add-row]", setEl);
      if (addBtn) addBtn.hidden = false;
      run(true);
      return;
    }
    if (e.target.closest(".sp-clear")) { clearAll(); return; }
  });
  function clearAll() {
    $$("[data-picker] .chips", form).forEach(function (c) { c.textContent = ""; });
    $$("input[type=text],input[type=search]", form).forEach(function (i) { i.value = ""; });
    $$("input[type=checkbox]", form).forEach(function (i) { i.checked = i.name === "g" && i.value === "paper"; });
    $$("select", form).forEach(function (s) {
      if (s.name === "fs") s.value = "legal";
      else if (s.name === "cmp") s.value = "ge";
      else if (s.name === "idm") s.value = "within";
      else if (s.name === "pc") s.value = "usd";
      else if (s.name === "po") s.value = "le";
      else if (s.name === "st") s.value = "pow";
      else if (s.name === "sto") s.value = "ge";
      else s.selectedIndex = 0;
      if (window.MtgSelect) window.MtgSelect.refresh(s);
    });
    $$("[data-rows] .rows", form).forEach(function (rows) { while (rows.children.length > 1) rows.lastElementChild.remove(); });
    $$("[data-add-row]", form).forEach(function (b) { b.hidden = false; });
    $$(".pickm", form).forEach(function (d) { d.dispatchEvent(new Event("change", { bubbles: true })); });
    run(true);
  }

  // -- the phone sheet (static/searchpanel.js opens and closes it) -----------------------------
  var Panel = window.MtgSearchPanel;
  function closePanel() { if (Panel) Panel.closeSheet(); }

  // -- pickers: a box that searches a list, chosen values as chips ------------------------------
  // Read once, as soon as the page loads. Incomplete lists (Scryfall busy) are used as they are
  // and asked for again at most once a minute, when a picker is opened; never once per keystroke.
  // A failed read is told again for a few seconds rather than asked again at every keystroke.
  var options = null, optionsLoad = null, optionsRetryAt = 0, optionsErr = null;
  function loadOptions() {
    if (options && !(options.partial && Date.now() >= optionsRetryAt)) return Promise.resolve(options);
    if (!options && optionsErr && Date.now() < optionsRetryAt) return Promise.reject(optionsErr);
    if (optionsLoad) return optionsLoad;
    optionsLoad = fetch("/cards/api/options", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        optionsLoad = null;
        if (!d.ok) throw new Error(d.message || "The lists could not be read.");
        optionsErr = null;
        if (!options || !d.partial || options.partial) options = d;
        if (d.partial) optionsRetryAt = Date.now() + 60000;
        nameChips(d);
        return d;
      })
      .catch(function (e) { optionsLoad = null; optionsErr = e; optionsRetryAt = Date.now() + 5000; throw e; });
    return optionsLoad;
  }
  loadOptions().catch(function () { /* shown when a picker is opened */ });
  var SET_TYPES = { expansion: "Expansions", core: "Core sets", masters: "Masters", draft_innovation: "Draft innovation",
    commander: "Commander", funny: "Funny", promo: "Promos", token: "Tokens", memorabilia: "Memorabilia",
    box: "Box sets", duel_deck: "Duel decks", from_the_vault: "From the Vault", masterpiece: "Masterpiece",
    alchemy: "Alchemy", minigame: "Minigames", starter: "Starter", eternal: "Eternal", planechase: "Planechase",
    archenemy: "Archenemy", arsenal: "Arsenal", spellbook: "Spellbook", premium_deck: "Premium decks",
    treasure_chest: "Treasure chest", vanguard: "Vanguard" };
  function itemsFor(source, d) {
    if (source === "sets") return d.sets.map(function (s) { return { v: s.code, label: s.name, small: s.code.toUpperCase(), group: SET_TYPES[s.type] || "Other" }; });
    if (source === "blocks") return d.blocks.map(function (b) { return { v: b.code, label: b.name, small: b.code.toUpperCase() }; });
    if (source === "criteria") return d.criteria.map(function (c) { return { v: c.code, label: c.name }; });
    var out = [];
    d.types.forEach(function (g) { g.values.forEach(function (t) { out.push({ v: t.toLowerCase(), label: t, group: g.label }); }); });
    return out;
  }
  function nameChips(d) {
    $$("[data-picker]", form).forEach(function (pk) {
      var source = pk.getAttribute("data-source"), byV = {};
      itemsFor(source, d).forEach(function (it) { byV[it.v] = it; });
      $$(".chips li", pk).forEach(function (li) {
        var it = byV[li.getAttribute("data-v")];
        if (it) $(".nm", li).textContent = it.label + (it.small && source !== "criteria" ? " (" + it.small + ")" : "");
      });
    });
  }
  function fold(s) { return (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); }
  function addChip(pk, value, label) {
    var key = pk.getAttribute("data-picker"), signed = pk.hasAttribute("data-signed");
    var chips = $(".chips", pk);
    if ($$("li", chips).some(function (li) { return li.getAttribute("data-v") === value; })) return;
    var li = el("li"); li.setAttribute("data-v", value);
    if (signed) {
      var pol = el("button", "pol", "IS"); pol.type = "button"; pol.setAttribute("aria-pressed", "false");
      pol.title = "Switch between IS and NOT";
      li.appendChild(pol);
    }
    li.appendChild(el("span", "nm", label));
    var hid = el("input"); hid.type = "hidden"; hid.name = key; hid.value = value;
    li.appendChild(hid);
    var x = el("button", "x"); x.type = "button"; x.setAttribute("aria-label", "Remove " + label); x.appendChild(icon("x"));
    li.appendChild(x);
    chips.appendChild(li);
    run(true);
  }
  $$("[data-picker]", form).forEach(function (pk) {
    var box = $("input[type=text]", pk), list = $(".opts", pk), source = pk.getAttribute("data-source");
    box.removeAttribute("name");  // with scripts on, the chips carry the values
    var active = -1, shown = [];
    function close() { list.hidden = true; box.setAttribute("aria-expanded", "false"); box.removeAttribute("aria-activedescendant"); active = -1; }
    function render() {
      var q = fold(box.value.trim());
      list.textContent = "";
      if (!options) {
        list.appendChild(el("li", "none", "Loading…"));
        list.hidden = false; box.setAttribute("aria-expanded", "true");
        loadOptions().then(render, function (e) { list.textContent = ""; list.appendChild(el("li", "none", e.message || "The list could not be read.")); });
        return;
      }
      if (options.partial && Date.now() >= optionsRetryAt) loadOptions().then(function () { if (!list.hidden) render(); }, function () {});
      var chosen = {};
      $$(".chips li", pk).forEach(function (li) { chosen[li.getAttribute("data-v")] = 1; });
      var all = itemsFor(source, options).filter(function (it) { return !chosen[it.v]; });
      var starts = [], inside = [];
      all.forEach(function (it) {
        var hay = fold(it.label) + " " + fold(it.small || "");
        if (!q) { starts.push(it); return; }
        var at = hay.indexOf(q);
        if (at === 0 || fold(it.small || "") === q) starts.push(it);
        else if (at > 0) inside.push(it);
      });
      shown = starts.concat(inside).slice(0, 80);
      if (source === "types" && q && !shown.some(function (it) { return it.v === q; }) && /^[a-z0-9][a-z0-9.' -]{0,39}$/.test(q)) {
        shown.unshift({ v: q, label: box.value.trim(), group: "", typed: true });
      }
      var group = null;
      shown.forEach(function (it, i) {
        if (it.group && it.group !== group && !q) { group = it.group; list.appendChild(el("li", "grp", it.group)); }
        var li = el("li", "opt");
        li.id = "o-" + pk.getAttribute("data-picker") + "-" + i;
        li.setAttribute("role", "option");
        li.setAttribute("data-i", String(i));
        li.appendChild(el("span", null, it.typed ? "Use “" + it.label + "”" : it.label));
        if (it.small) li.appendChild(el("small", null, it.small));
        list.appendChild(li);
      });
      if (!shown.length) list.appendChild(el("li", "none", "Nothing matches."));
      list.hidden = false;
      box.setAttribute("aria-expanded", "true");
      active = -1;
    }
    function highlight(i) {
      var opts = $$("li.opt", list);
      if (!opts.length) return;
      active = (i + opts.length) % opts.length;
      opts.forEach(function (o, k) { o.setAttribute("aria-selected", k === active ? "true" : "false"); });
      box.setAttribute("aria-activedescendant", opts[active].id);
      opts[active].scrollIntoView({ block: "nearest" });
    }
    function pick(i) {
      var it = shown[i];
      if (!it) return;
      addChip(pk, it.v, it.label + (it.small && source !== "criteria" ? " (" + it.small + ")" : ""));
      box.value = "";
      close();
      box.focus();
    }
    box.addEventListener("focus", render);
    box.addEventListener("input", render);
    box.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (list.hidden) render(); highlight(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
      else if (e.key === "Enter") { e.preventDefault(); if (active >= 0) pick(active); else if (shown.length === 1 || (shown[0] && shown[0].typed)) pick(0); }
      else if (e.key === "Escape") { if (!list.hidden) { e.stopPropagation(); close(); } }
      else if (e.key === "Backspace" && !box.value) { var last = $(".chips li:last-child", pk); if (last) { last.remove(); run(true); } }
    });
    box.addEventListener("blur", function () { setTimeout(function () { if (!pk.contains(document.activeElement)) close(); }, 150); });
    list.addEventListener("mousedown", function (e) { e.preventDefault(); });
    list.addEventListener("click", function (e) { var li = e.target.closest("li.opt"); if (li) pick(+li.getAttribute("data-i")); });
    pk.addEventListener("click", function (e) {
      var x = e.target.closest(".chips .x");
      if (x) { x.closest("li").remove(); run(true); box.focus(); return; }
      var pol = e.target.closest(".chips .pol");
      if (pol) {
        var li = pol.closest("li"), hid = $("input[type=hidden]", li), not = !li.classList.contains("not");
        li.classList.toggle("not", not);
        pol.textContent = not ? "NOT" : "IS";
        pol.setAttribute("aria-pressed", not ? "true" : "false");
        hid.value = (not ? "-" : "") + li.getAttribute("data-v");
        run(true);
      }
    });
  });
  // chips the page came with show their full names once the lists are known (cheap after a day)
  if ($$("[data-picker] .chips li", form).length) loadOptions().catch(function () { /* codes stay */ });

  // -- toast: what was saved, with a link to the deck; errors stay until dismissed (the shared
  // one, static/cardmenu.js) ------------------------------------------------------------------
  var Menu = window.MtgCardMenu;
  function toast(text, opts) { Menu.toast(text, opts); }

  // -- the card viewer: the picture button's data attributes, plus Add to deck and Scryfall ------
  function viewerActions(node) {
    var acts = [];
    if (window.MtgCardView) {
      var add = el("button", "btn-primary");
      add.type = "button";
      add.appendChild(icon("plus"));
      add.appendChild(document.createTextNode(" Add to deck"));
      if (!linked) { add.disabled = true; add.title = "Link your Archidekt account on the Account page to add cards to a deck."; }
      add.addEventListener("click", function () {
        window.MtgCardView.close();
        var own = node.closest("li") && node.closest("li").querySelector("button[data-add]");
        openSheet(node.getAttribute("data-card"), own ? pinOf(own) : null);
      });
      acts.push(add);
    }
    var scry = node.getAttribute("data-scry");
    if (scry) {
      var a = el("a", "btn ext");
      a.href = scry; a.target = "_blank"; a.rel = "noreferrer noopener";
      a.appendChild(icon("external"));
      a.appendChild(document.createTextNode(" Scryfall"));
      acts.push(a);
    }
    return acts;
  }
  /* The rules text (every face), power and toughness, flavour, price and legality are read when a
     card is opened, once per card for the page's lifetime (the gateway keeps them a while too),
     and drawn into the open viewer when they arrive, if it still shows that card. */
  var texts = Object.create(null);  // card name -> promise of the text (no prototype keys)
  var showing = "";    // the card the viewer shows now
  function textFor(name) {
    if (texts[name]) return texts[name];
    // the viewer's own read of the same answer (it wants the links and the back face): one request
    texts[name] = window.MtgCardView && window.MtgCardView.text ? window.MtgCardView.text(name)
      : fetch("/cards/api/text?name=" + enc(name), { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { return d && d.ok && d.card ? d.card : null; })
        .catch(function () { delete texts[name]; return null; });
    return texts[name];
  }
  function merged(base, text) {
    var card = {};
    Object.keys(base).forEach(function (k) { card[k] = base[k]; });
    if (text) Object.keys(text).forEach(function (k) { if (text[k] || k === "faces") card[k] = text[k]; });
    return card;
  }
  function openViewer(pic) {
    var base = window.MtgCardView.fromElement(pic);
    var name = base.name;
    showing = name;
    var known = texts[name] && texts[name]._value;
    if (known !== undefined) { window.MtgCardView.open(merged(base, known), viewerActions(pic)); return; }
    base.loading = true;
    window.MtgCardView.open(base, viewerActions(pic));
    textFor(name).then(function (text) {
      if (texts[name]) texts[name]._value = text;
      if (showing !== name) return;
      delete base.loading;
      window.MtgCardView.update(merged(base, text), viewerActions(pic));
    });
  }
  results.addEventListener("click", function (e) {
    var pic = e.target.closest(".pic");
    var nameBtn = e.target.closest("[data-open]");
    if (nameBtn) pic = nameBtn.closest("li").querySelector(".pic");
    if (pic && window.MtgCardView) {
      e.preventDefault();
      openViewer(pic);
      return;
    }
    var add = e.target.closest("button[data-add]");
    if (add && !add.disabled) openSheet(add.getAttribute("data-add"), pinOf(add));
  });
  function pinOf(btn) {
    var set = btn.getAttribute("data-set-code"), num = btn.getAttribute("data-number");
    return set && num ? { set_code: set, collector_number: num } : null;
  }

  // -- Add to deck -----------------------------------------------------------------------------
  var note = null, sheet = null, lastFocus = null, deckSel, qtyIn, catSel, msg, saveBtn, title, cardName = "";
  var deckRows = null, deckLoad = null, cats = {};  // deck id -> categories, once per deck
  function ensureSheet() {
    if (sheet) return sheet;
    sheet = el("div", "addsheet");
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-labelledby", "addsheet-title");
    var box = el("form", "box");
    box.noValidate = true;
    var head = el("div", "head");
    title = el("h3"); title.id = "addsheet-title";
    head.appendChild(title);
    var x = el("button", "close icon-only");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.appendChild(el("span", "x", "×"));
    x.addEventListener("click", closeSheet);
    head.appendChild(x);
    box.appendChild(head);

    var f1 = el("div", "field");
    var l1 = el("label", null, "Deck"); l1.htmlFor = "ad-deck";
    deckSel = el("select"); deckSel.id = "ad-deck"; deckSel.name = "deck"; deckSel.required = true;
    f1.appendChild(l1); f1.appendChild(deckSel);
    box.appendChild(f1);

    var two = el("div", "two");
    var f2 = el("div", "field");
    var l2 = el("label", null, "Copies"); l2.htmlFor = "ad-qty";
    qtyIn = el("input"); qtyIn.id = "ad-qty"; qtyIn.name = "quantity"; qtyIn.type = "number";
    qtyIn.min = "1"; qtyIn.max = "99"; qtyIn.step = "1"; qtyIn.value = "1"; qtyIn.inputMode = "numeric";
    f2.appendChild(l2); f2.appendChild(qtyIn);
    var f3 = el("div", "field");
    var l3 = el("label", null, "Category"); l3.htmlFor = "ad-cat";
    catSel = el("select"); catSel.id = "ad-cat"; catSel.name = "category";
    f3.appendChild(l3); f3.appendChild(catSel);
    catSel.addEventListener("change", showNote);
    two.appendChild(f2); two.appendChild(f3);
    box.appendChild(two);

    note = el("p", "note"); note.hidden = true; note.setAttribute("aria-live", "polite");
    box.appendChild(note);
    msg = el("p", "msg"); msg.setAttribute("aria-live", "polite");
    box.appendChild(msg);
    var acts = el("div", "acts");
    var cancel = el("button", "", "Cancel"); cancel.type = "button";
    cancel.addEventListener("click", closeSheet);
    saveBtn = el("button", "btn-primary"); saveBtn.type = "submit";
    saveBtn.appendChild(icon("plus"));
    saveBtn.appendChild(document.createTextNode(" Add"));
    acts.appendChild(cancel); acts.appendChild(saveBtn);
    box.appendChild(acts);
    box.addEventListener("submit", function (e) { e.preventDefault(); save(); });
    sheet.appendChild(box);
    document.body.appendChild(sheet);
    sheet.addEventListener("click", function (e) { if (e.target === sheet) closeSheet(); });
    document.addEventListener("keydown", function (e) {
      if (!sheet.classList.contains("open")) return;
      if (window.MtgSelect && window.MtgSelect.isOpen()) return;   // the themed list handles its own keys
      if (e.key === "Escape") { e.preventDefault(); closeSheet(); return; }
      if (e.key === "Tab") {
        var f = sheet.querySelectorAll("button:not([disabled]),input,select,[tabindex]:not([tabindex='-1'])");
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    deckSel.addEventListener("change", function () { remember(deckSel.value); loadCats(deckSel.value); });
    return sheet;
  }
  function remember(id) { try { localStorage.setItem(LAST_DECK, id); } catch (e) { /* private window */ } }
  function remembered() { try { return localStorage.getItem(LAST_DECK) || ""; } catch (e) { return ""; } }
  function setMsg(text, isError) { msg.textContent = text || ""; msg.className = "msg" + (isError ? " err" : ""); }
  function option(value, label) { var o = el("option", null, label); o.value = value; return o; }
  function fillDecks(rows) {
    deckSel.textContent = "";
    if (!rows.length) { deckSel.appendChild(option("", "You have no decks yet")); deckSel.disabled = true; return; }
    deckSel.disabled = false;
    var last = remembered();
    rows.forEach(function (d) {
      var label = d.name + (d.format_name ? " · " + d.format_name : "");
      var o = option(String(d.id), label);
      if (String(d.id) === last) o.selected = true;
      deckSel.appendChild(o);
    });
    if (window.MtgSelect) window.MtgSelect.refresh(deckSel);
    loadCats(deckSel.value);
  }
  function loadDecks() {
    if (deckRows) { fillDecks(deckRows); return Promise.resolve(); }
    if (deckLoad) return deckLoad;
    deckSel.textContent = "";
    deckSel.appendChild(option("", "Reading your decks…"));
    deckSel.disabled = true;
    if (window.MtgSelect) window.MtgSelect.refresh(deckSel);
    deckLoad = api("GET", "/api/decks/mine").then(function (d) {
      deckLoad = null;
      if (!d.ok) { setMsg(d.message || "Your deck list could not be read.", true); return; }
      deckRows = (d.decks || []).slice().sort(function (a, b) { return (b.updated_at || "") < (a.updated_at || "") ? -1 : 1; });
      fillDecks(deckRows);
    }).catch(function () { deckLoad = null; setMsg("No connection; your decks could not be read.", true); });
    return deckLoad;
  }
  var catDeck = null;  // the deck whose categories the select shows
  function fillCats(list, id) {
    var keep = id && id === catDeck ? catSel.value : "";  // a refill of the same deck keeps the choice
    catDeck = id || null;
    catSel.textContent = "";
    catSel.appendChild(option("", "The deck's default"));
    (list || []).forEach(function (c) {
      if (c && c !== "Maybeboard") { var o = option(c, c); if (c === keep) o.selected = true; catSel.appendChild(o); }
    });
    if (window.MtgSelect) window.MtgSelect.refresh(catSel);
    showNote();
  }
  var outside = {};  // deck id -> its categories whose rows sit outside the deck
  var catLoads = {};  // deck id -> true while its categories are being read, so one read per deck
  function loadCats(id) {
    if (!id) { fillCats([]); return; }
    if (cats[id]) { fillCats(cats[id], id); return; }
    fillCats([], id);
    if (catLoads[id]) return;
    catLoads[id] = true;
    api("GET", "/api/v1/decks/" + enc(id) + "?cards=0").then(function (d) {
      delete catLoads[id];
      if (d.ok && d.categories) {
        cats[id] = d.categories;
        outside[id] = d.outside_categories || [];
        if (deckSel.value === id) fillCats(cats[id], id);
      }
    }).catch(function () { delete catLoads[id]; /* the default category still works */ });
  }
  var cardPin = null;  // the printing shown on the tile, when the search is about printings
  function openSheet(name, pin) {
    if (!linked || !name) return;
    ensureSheet();
    cardName = name;
    cardPin = pin || null;
    title.textContent = "";
    title.appendChild(el("span", null, "Add " + name));
    showNote();
    qtyIn.value = "1";
    setMsg("");
    saveBtn.disabled = false;
    saveBtn.removeAttribute("aria-busy");
    lastFocus = document.activeElement;
    sheet.classList.add("open");
    document.documentElement.classList.add("cardview-open");
    loadDecks();
    if (window.MtgSelect) window.MtgSelect.enhance(sheet);
    setTimeout(function () { var first = $(".msel-btn", sheet) || deckSel; first.focus(); }, 0);
  }
  function closeSheet() {
    if (!sheet) return;
    sheet.classList.remove("open");
    document.documentElement.classList.remove("cardview-open");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  // a category whose rows sit outside the deck (Sideboard, Considering...) puts the card in the
  // maybeboard or sideboard; a picked printing goes there too (since 0.7.33)
  function sideCat() { return !!catSel.value && (outside[catDeck] || []).indexOf(catSel.value) >= 0; }
  function showNote() {
    if (!note) return;
    var parts = [];
    if (cardPin) {
      parts.push("This printing: " + cardPin.set_code.toUpperCase() + " #" + cardPin.collector_number + ".");
    }
    var lang = form.elements.lang ? form.elements.lang.value : "";
    if (lang && lang !== "en") parts.push("Archidekt decks keep no card language, so the deck shows it in English.");
    note.textContent = parts.join(" ");
    note.hidden = !note.textContent;
  }
  function changes() {
    var qty = Math.max(1, Math.min(99, parseInt(qtyIn.value, 10) || 1));
    var ch = { action: "add", card_name: cardName, quantity: qty };
    if (catSel.value) ch.category = catSel.value;
    if (sideCat()) ch.zone = "side";
    if (cardPin) { ch.set_code = cardPin.set_code; ch.collector_number = cardPin.collector_number; }
    return [ch];
  }
  function deckName(id) {
    var row = (deckRows || []).filter(function (d) { return String(d.id) === String(id); })[0];
    return row ? row.name : "the deck";
  }
  function busy(on) {
    saveBtn.disabled = on;
    if (on) saveBtn.setAttribute("aria-busy", "true"); else saveBtn.removeAttribute("aria-busy");
  }
  function save(body) {
    var id = deckSel.value;
    if (!id) { setMsg("Pick a deck first.", true); return; }
    busy(true);
    setMsg("Saving…");
    var sent = changes();
    api("POST", "/api/v1/decks/" + enc(id) + "/edit", body || { changes: sent, confirmed: false }).then(function (d) {
      if (d.ok && d.applied) {
        busy(false);
        closeSheet();
        var open = el("a", "btn", "Open deck");
        open.href = "/decks/" + enc(id);
        toast("Added " + sent[0].quantity + " × " + cardName + " to " + deckName(id) + ".", { actions: [open] });
        return;
      }
      if (d.ok && d.needs_confirm) {
        // the hand-edit rule asks first; No rejects the proposal made for the question
        busy(false);
        setMsg(d.why + " Save anyway?", false);
        var yes = el("button", "btn-primary", "Save anyway"); yes.type = "button";
        var no = el("button", "", "No"); no.type = "button";
        msg.appendChild(document.createTextNode(" "));
        msg.appendChild(yes); msg.appendChild(document.createTextNode(" ")); msg.appendChild(no);
        yes.addEventListener("click", function () { save({ proposal_id: d.proposal_id, version: d.version }); });
        no.addEventListener("click", function () {
          api("POST", "/api/v1/proposals/" + enc(d.proposal_id) + "/reject", {}).catch(function () {});
          setMsg("Nothing was changed.");
        });
        yes.focus();
        return;
      }
      if (d.ok && d.status === 202 && d.proposal_id) {
        busy(false);
        closeSheet();
        var see = el("a", "btn", "Review page");
        see.href = "/proposals/" + enc(d.proposal_id);
        toast("Still saving on Archidekt; the review page shows how far it got.", { actions: [see], ms: 15000 });
        return;
      }
      if (d.ok && d.proposal_id) { location.href = "/proposals/" + enc(d.proposal_id); return; }  // writes are off: kept for review
      busy(false);
      setMsg(d.message || "The card could not be added.", true);
    }).catch(function () { busy(false); setMsg("No connection; nothing was changed.", true); });
  }
  // -- Watch: the Watch button on each result, the card menu and Art variants… -------------
  // Only with watchlists on: the server draws the Watch buttons and the page hint then. A watch
  // goes through the shared static/cardmenu.js (a printing search sends the shown printing), as
  // do the menu and its toast with Undo.
  var norm = Menu.norm;
  function setWatched(name, on) {
    $$(".wbtn[data-watch]", results).forEach(function (b) {
      var n = b.getAttribute("data-watch");
      if (norm(n) === norm(name)) Menu.drawButton(b, n, on);
    });
  }
  function isWatched(name) {
    return $$(".wbtn.on[data-watch]", results).some(function (b) { return norm(b.getAttribute("data-watch")) === norm(name); });
  }
  // what: {name, set_code, collector_number, finish}; resolves with the watch, rejects on a refusal
  function watch(what, btn) {
    return Menu.watch(what, { btn: btn, was: isWatched(what.name), onState: function (on) { setWatched(what.name, on); } });
  }
  function watchOf(btn) {
    var p = pinOf(btn) || {};
    return { name: btn.getAttribute("data-watch"), set_code: p.set_code, collector_number: p.collector_number };
  }
  results.addEventListener("click", function (e) {
    var b = e.target.closest(".wbtn[data-watch]");
    if (!b || b.disabled) return;
    e.preventDefault();
    e.stopPropagation();
    if (b.classList.contains("on")) {
      var n = b.getAttribute("data-watch");
      toast(n + " is on your watchlist.", { ok: true, actions: [Menu.listLink(n)] });
      return;
    }
    watch(watchOf(b), b).catch(function () { /* the toast said why */ });
  }, true);

  // the card menu: right-click, press and hold, the Menu key or Shift+F10 on a result
  function openMenu(item, x, y) {
    var pic = item.querySelector(".pic");
    if (!pic) return;
    var name = pic.getAttribute("data-card") || "";
    var wbtn = item.querySelector(".wbtn[data-watch]");
    var addBtn = item.querySelector("button[data-add]");
    Menu.open({
      name: name,
      onOpen: function () { openViewer(pic); },
      watch: wbtn ? {
        watched: wbtn.classList.contains("on"),
        onWatch: function () { watch(watchOf(wbtn), wbtn).catch(function () {}); },
        onArt: function () {
          Menu.artVariants(pic.getAttribute("data-card") || "", {
            returnFocus: pic, onState: function (on) { setWatched(name, on); }, was: function () { return isWatched(name); }
          });
        }
      } : null,
      onAdd: function () { openSheet(name, addBtn ? pinOf(addBtn) : null); },
      addDisabled: linked ? "" : "Link your Archidekt account on the Account page to add cards to a deck.",
      collect: true,
      scry: pic.getAttribute("data-scry"),
      focusBack: pic
    }, x, y);
  }
  function itemOf(target) {
    var li = target && target.closest ? target.closest("li") : null;
    return li && results.contains(li) && !li.classList.contains("hd") && li.querySelector(".pic") ? li : null;
  }
  Menu.bind(results, { itemOf: itemOf, skip: ".wbtn, button[data-add]", button: ".kebab", open: openMenu });
  // the ⋮ of a tile (by its name) and of a row (with its Watch and Add): the same menu
  function kebabs() {
    if (!window.MtgMenu) return;
    Array.prototype.slice.call(results.querySelectorAll("li .pic[data-card]")).forEach(function (pic) {
      var li = pic.closest("li");
      if (!li || li.querySelector(".kebab")) return;
      var k = window.MtgMenu.kebab("Menu for " + (pic.getAttribute("data-card") || "this card"));
      k.classList.add("inline");
      var slot = li.querySelector(".nm, .nmc");
      if (slot) slot.appendChild(k); else li.appendChild(k);
    });
  }
  kebabs();
  new MutationObserver(kebabs).observe(results, { childList: true });
})();
