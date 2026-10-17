/* The printing picker every page shares (the deck page's card menu, the deck editor's row menu
   and its add-a-card printings): one card's printings from Scryfall through the gateway
   (/scan/api/prints), as large tiles with the set's name and code, the collector number, the
   year, the finishes and the price. A search over set name or code, filters (finish, frame,
   border), a sort (newest, oldest, cheapest), and "Load more" past Scryfall's page of 175. The
   default list is the released paper printings in English; a switch shows the digital-only and
   preview printings too, each labelled. Hovering a tile (or focusing it) shows the whole card in
   the preview pane beside the list on a wide screen; a tap or click on a tile shows it there (a
   sheet over the list on a narrower screen) with "Use this printing". The page decides what a
   pick does (save at once, queue it, add a card): opts.onPick(printing, info).
   A dialog with a focus trap; Escape, the close button and a click on the backdrop close it.
   Loaded before deck.js and companion.js, which call window.MtgPrintPick. */
(function () {
  "use strict";
  var FINISH_NAMES = { nonfoil: "Non-foil", foil: "Foil", etched: "Etched" };
  var FINISHES = [["", "Any finish"], ["nonfoil", "Non-foil"], ["foil", "Foil"], ["etched", "Etched"]];
  var FRAMES = [["", "Any frame"], ["1993", "1993 frame"], ["1997", "1997 frame"], ["2003", "2003 frame"], ["2015", "2015 frame"], ["future", "Future frame"]];
  var BORDERS = [["", "Any border"], ["black", "Black border"], ["white", "White border"], ["borderless", "Borderless"], ["silver", "Silver border"], ["gold", "Gold border"]];
  var SORTS = [["newest", "Newest first"], ["oldest", "Oldest first"], ["cheapest", "Cheapest (USD)"]];
  var PRICE_KEY = { nonfoil: "usd", foil: "usd_foil", etched: "usd_etched" };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
  function getJSON(url) {
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok || !d || d.ok === false) throw new Error((d && d.message) || (r.status === 401 ? "you are signed out" : "the gateway did not answer"));
          return d;
        });
      });
  }

  // -- finishes: the deck's words (Normal, Foil, Etched) and Scryfall's (nonfoil, foil, etched) --
  function scryFinish(f) {
    f = String(f || "").toLowerCase();
    return f === "foil" || f === "etched" ? f : "nonfoil";
  }
  function deckFinish(f) { return f === "nonfoil" ? "normal" : f; }
  /* The finish a printing would be in when the card moves onto it: the card's own finish
     when the printing is made in it, else non-foil when it is (as the gateway's own fallback,
     archidekt.finish_modifier), else the printing's first finish. */
  function finishOn(p, current) {
    var have = (p && p.finishes) || [];
    var want = scryFinish(current);
    if (!have.length || have.indexOf(want) >= 0) return { finish: want, changed: false };
    return { finish: have.indexOf("nonfoil") >= 0 ? "nonfoil" : have[0], changed: true };
  }
  function priceOf(p, finish) {
    var v = p && p.prices && p.prices[PRICE_KEY[finish]];
    var n = v === undefined || v === null || v === "" ? NaN : Number(v);
    return isNaN(n) ? null : n;
  }
  function money(n) { return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function samePrint(p, ref) {
    return !!(ref && p && String(p.set || "").toLowerCase() === String(ref.set || "").toLowerCase() &&
      String(p.collector_number) === String(ref.number));
  }
  function code(p) { return String(p.set || "").toUpperCase() + " #" + (p.collector_number || ""); }
  function year(p) { return String(p.released_at || "").slice(0, 4); }
  function preview(p) { return !!p.unreleased; }
  function digital(p) { return p.paper === false && !p.unreleased; }
  function english(p) { return !p.lang || p.lang === "en"; }
  function flagText(p) { return preview(p) ? "Preview" : digital(p) ? "Digital only" : ""; }

  // -- the data: a card's oracle id by name (once), and its printings a page at a time ----------
  var oracleIds = Object.create(null);
  var pages = Object.create(null);
  function oracleFor(name) {
    var key = name.toLowerCase();
    if (!oracleIds[key]) {
      oracleIds[key] = getJSON("/scan/api/peek?names=" + encodeURIComponent(name)).then(function (d) {
        var face = key.split(" // ")[0];
        var hit = (d.cards || []).filter(function (c) {
          var n = String(c.name || "").toLowerCase();
          return n === key || n.split(" // ")[0] === face;
        })[0] || (d.cards || [])[0];
        if (!hit || !hit.oracle_id) throw new Error("Scryfall does not know " + name);
        return hit.oracle_id;
      });
      oracleIds[key].catch(function () { delete oracleIds[key]; });
    }
    return oracleIds[key];
  }
  function pageFor(oid, params, page) {
    var q = "oracle_id=" + encodeURIComponent(oid) + (page > 1 ? "&page=" + page : "");
    ["finish", "frame", "border", "order"].forEach(function (k) {
      if (params && params[k] && !(k === "order" && params[k] === "newest")) q += "&" + k + "=" + encodeURIComponent(params[k]);
    });
    if (!pages[q]) {
      pages[q] = getJSON("/scan/api/prints?" + q);
      pages[q].catch(function () { delete pages[q]; });
    }
    return pages[q];
  }
  // one printing by set and number: the card's own, when a card with more printings than a page
  // hasn't listed it
  function oneFor(oid, ref) {
    var q = "oracle_id=" + encodeURIComponent(oid) + "&set=" + encodeURIComponent(ref.set) + "&number=" + encodeURIComponent(ref.number);
    if (!pages[q]) {
      pages[q] = getJSON("/scan/api/prints?" + q);
      pages[q].catch(function () { delete pages[q]; });
    }
    return pages[q];
  }
  // the default list: released paper printings in English, newest first as Scryfall gives them
  function defaults(list) { return list.filter(function (p) { return english(p) && !preview(p) && !digital(p); }); }

  // -- one tile: the picture, the set's name, code and number, the year, finishes and price ----
  function chips(p, cls) {
    var box = el("span", cls || "ppchips");
    (p.finishes || []).forEach(function (f) { box.appendChild(el("span", "ppchip", FINISH_NAMES[f] || f)); });
    var flag = flagText(p);
    if (flag) box.appendChild(el("span", "ppchip ppflag", flag));
    return box;
  }
  function tile(p, o) {
    o = o || {};
    var b = el("button", "pptile" + (o.compact ? " compact" : ""));
    b.type = "button";
    var img = p.image_normal || p.image_small;
    var pic = el("span", "ppimg");
    if (img) {
      var i = el("img");
      i.src = o.compact ? (p.image_small || img) : img;
      i.alt = "";
      i.loading = "lazy";
      i.decoding = "async";
      pic.appendChild(i);
    } else pic.appendChild(el("span", "ph", p.name || ""));
    b.appendChild(pic);
    var cap = el("span", "ppcap");
    if (!o.compact) cap.appendChild(el("span", "ppsetname", p.set_name || String(p.set || "").toUpperCase()));
    // a small tile has room for the set and number only; its name says the year
    cap.appendChild(el("span", "ppcode", code(p) + (year(p) && !o.compact ? " · " + year(p) : "")));
    if (!o.compact) {
      cap.appendChild(chips(p));
      var price = priceOf(p, o.finish || "nonfoil");
      cap.appendChild(el("span", "ppprice" + (price === null ? " none" : ""), price === null ? "No price" : money(price)));
    }
    b.appendChild(cap);
    var words = [p.set_name || "", code(p), year(p)];
    if (!o.compact) {
      words.push((p.finishes || []).map(function (f) { return FINISH_NAMES[f] || f; }).join(", "));
      var pr = priceOf(p, o.finish || "nonfoil");
      if (pr !== null) words.push(money(pr));
      if (flagText(p)) words.push(flagText(p));
    }
    if (o.mark) {
      b.appendChild(el("span", "ppmark", o.mark));
      words.push(o.mark);
    }
    b.setAttribute("aria-label", words.filter(Boolean).join(", "));
    if (o.compact) b.title = (p.set_name || "") + " " + code(p);
    return b;
  }

  // -- the dialog ---------------------------------------------------------------------------
  var dlg = null, st = null, lastFocus = null;
  var wideQuery = window.matchMedia ? window.matchMedia("(min-width: 900px)") : null;
  var hoverQuery = window.matchMedia ? window.matchMedia("(hover: hover) and (pointer: fine)") : null;
  function wide() { return !!(wideQuery && wideQuery.matches); }

  function isOpen() { return !!(dlg && dlg.classList.contains("open")); }
  function close() {
    if (!isOpen()) return;
    dlg.classList.remove("open");
    dlg.textContent = "";
    document.documentElement.classList.remove("printpick-open");
    st = null;
    var back = lastFocus;
    lastFocus = null;
    if (back && back.isConnected && back.focus) back.focus();
  }
  function focusables() {
    return Array.prototype.filter.call(
      dlg.querySelectorAll("a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex='-1'])"),
      function (e) { return e.offsetParent !== null && !e.closest("[hidden]") && !e.matches("select.msel-native"); }
    );
  }
  function ensure() {
    if (dlg) return dlg;
    dlg = el("div", "printpick");
    dlg.setAttribute("role", "dialog");
    dlg.setAttribute("aria-modal", "true");
    dlg.setAttribute("aria-labelledby", "pp-title");
    document.body.appendChild(dlg);
    dlg.addEventListener("click", function (e) { if (e.target === dlg) close(); });
    document.addEventListener("keydown", function (e) {
      if (!isOpen()) return;
      if (document.querySelector(".msel-list")) return;  // a themed select's list is open: it takes the keys
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        if (st && st.box.classList.contains("previewing") && !wide()) { hidePreview(); return; }
        close();
        return;
      }
      if (e.key === "Tab") {  // keep focus inside the dialog
        var f = focusables();
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (!dlg.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
        else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }, true);
    // turned or resized past 900px: the preview pane sits beside the list, never over it
    if (wideQuery && wideQuery.addEventListener) wideQuery.addEventListener("change", function () {
      if (!st) return;
      if (wide()) {
        st.box.classList.remove("previewing");
        st.tools.inert = false;
        st.scroll.inert = false;
        showPreview(st.pinned);
      }
    });
    return dlg;
  }

  function select(label, options, value, onchange) {
    var s = el("select");
    s.setAttribute("aria-label", label);
    options.forEach(function (o) {
      var opt = el("option", "", o[1]);
      opt.value = o[0];
      if (o[0] === value) opt.selected = true;
      s.appendChild(opt);
    });
    s.addEventListener("change", function () { onchange(s.value); });
    return s;
  }

  /* opts: {name, current: {set, number} | null (the deck's printing, marked Current), pending:
     {set, number} | null (a pick not saved yet, marked Picked), finish: the card's finish
     ("normal" / "foil" / "etched", or Archidekt's Normal...), pickLabel, onPick(printing, info)}
     info: {finish: "normal" | "foil" | "etched", changed: bool, from: the finish it had} */
  function open(opts) {
    if (!opts || !opts.name || typeof opts.onPick !== "function") return;
    ensure();
    if (isOpen()) close();
    lastFocus = opts.returnFocus || document.activeElement;
    dlg.textContent = "";
    st = {
      opts: opts, oid: null, list: [], page: 0, hasMore: false, total: 0, complete: false, loading: false,
      params: { finish: "", frame: "", border: "", order: "newest" }, q: "", all: false, pinned: null, seq: 0,
      mine: Object.create(null), mineAsked: false,
      finish: scryFinish(opts.finish)
    };
    var box = el("div", "ppbox");
    st.box = box;
    // head: the card's name and the close button
    var head = el("div", "pphead");
    var h2 = el("h2", "", "Printings of " + opts.name);
    h2.id = "pp-title";
    var closeBtn = el("button", "close icon-only", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.title = "Close";
    closeBtn.addEventListener("click", close);
    head.appendChild(h2);
    head.appendChild(closeBtn);
    box.appendChild(head);
    // tools: the search, then the filters (folded behind a button on a phone)
    var tools = el("div", "pptools");
    var search = el("input");
    search.type = "search";
    search.id = "pp-q";
    search.placeholder = "Search set name or code";
    search.setAttribute("aria-label", "Search set name or code");
    search.autocomplete = "off";
    search.setAttribute("enterkeyhint", "search");
    search.addEventListener("input", function () { st.q = search.value.trim().toLowerCase(); draw(); });
    var searchRow = el("div", "ppsearch");
    searchRow.appendChild(search);
    var filtBtn = el("button", "ppfiltbtn", "Filters");
    filtBtn.type = "button";
    filtBtn.setAttribute("aria-expanded", "false");
    filtBtn.setAttribute("aria-controls", "pp-filters");
    searchRow.appendChild(filtBtn);
    // card size S / M / L (0.7.29): the same switch as next to "View as"; static/prefs.js applies
    // and saves it
    var sizes = el("span", "sizeseg");
    sizes.setAttribute("role", "group");
    sizes.setAttribute("aria-label", "Card size");
    var now = document.documentElement.getAttribute("data-card-size") || "m";
    [["s", "Small"], ["m", "Medium"], ["l", "Large"]].forEach(function (pair) {
      var b = el("button", "", pair[0].toUpperCase());
      b.type = "button";
      b.setAttribute("data-card-size-set", pair[0]);
      b.setAttribute("aria-pressed", pair[0] === now ? "true" : "false");
      b.setAttribute("aria-label", pair[1] + " cards");
      b.title = pair[1] + " cards";
      sizes.appendChild(b);
    });
    if (window.MtgPrefs) searchRow.appendChild(sizes);
    tools.appendChild(searchRow);
    var filters = el("div", "ppfilters");
    filters.id = "pp-filters";
    filters.setAttribute("role", "group");
    filters.setAttribute("aria-label", "Filters");
    filtBtn.addEventListener("click", function () {
      var on = !filters.classList.contains("open");
      filters.classList.toggle("open", on);
      filtBtn.setAttribute("aria-expanded", on ? "true" : "false");
    });
    var setParam = function (k) { return function (v) { st.params[k] = v; refilter(); }; };
    filters.appendChild(select("Finish", FINISHES, "", setParam("finish")));
    filters.appendChild(select("Frame", FRAMES, "", setParam("frame")));
    filters.appendChild(select("Border", BORDERS, "", setParam("border")));
    filters.appendChild(select("Sort", SORTS, "newest", setParam("order")));
    var sw = el("label", "switch ppall");
    var swIn = el("input");
    swIn.type = "checkbox";
    swIn.addEventListener("change", function () { st.all = swIn.checked; draw(); });
    sw.appendChild(swIn);
    sw.appendChild(el("span", "track"));
    sw.appendChild(document.createTextNode("Include digital-only and preview printings"));
    filters.appendChild(sw);
    tools.appendChild(filters);
    box.appendChild(tools);
    st.tools = tools;
    st.filtBtn = filtBtn;
    st.search = search;
    st.selects = filters.querySelectorAll("select");
    st.switchIn = swIn;
    // the list and the preview
    var status = el("p", "ppstatus");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    box.appendChild(status);
    st.status = status;
    var main = el("div", "ppmain");
    var scroll = el("div", "ppscroll");
    var grid = el("ul", "ppgrid");
    grid.setAttribute("aria-label", "Printings");
    scroll.appendChild(grid);
    var empty = el("div", "ppempty");
    empty.hidden = true;
    scroll.appendChild(empty);
    var moreRow = el("div", "ppmore");
    var more = el("button", "btn", "Load more printings");
    more.type = "button";
    more.addEventListener("click", function () { load(false); });
    moreRow.appendChild(more);
    moreRow.hidden = true;
    scroll.appendChild(moreRow);
    main.appendChild(scroll);
    var pv = el("aside", "pppreview");
    pv.setAttribute("aria-label", "Preview");
    main.appendChild(pv);
    box.appendChild(main);
    st.grid = grid; st.empty = empty; st.more = more; st.moreRow = moreRow; st.pv = pv; st.scroll = scroll;
    grid.addEventListener("mouseleave", function () { if (wide()) showPreview(st.pinned); });
    dlg.appendChild(box);
    dlg.classList.add("open");
    document.documentElement.classList.add("printpick-open");
    if (window.MtgSelect && window.MtgSelect.enhance) window.MtgSelect.enhance(filters);
    closeBtn.focus();
    status.textContent = "Looking up printings…";
    var seq = ++st.seq;
    var mine = st;
    (opts.oracleId ? Promise.resolve(opts.oracleId) : oracleFor(opts.name)).then(function (oid) {
      if (st !== mine || seq !== st.seq) return;
      st.oid = oid;
      load(true);
    }).catch(function (err) { if (st === mine) failed(err); });
  }

  // A filter or sort changed: with every printing here already (one page holds them all) the list
  // is redrawn at once; a card with more printings than a page is asked for again, filtered and
  // sorted by Scryfall over all of its printings.
  function refilter() {
    if (!st) return;
    if (st.complete || !st.oid) { draw(); return; }
    load(true);
  }
  function load(reset) {
    if (!st || !st.oid || (st.loading && !reset)) return;
    var mine = st, seq = ++st.seq;
    var params = st.complete ? {} : st.params;
    var page = reset ? 1 : st.page + 1;
    st.loading = true;
    st.more.disabled = true;
    st.more.setAttribute("aria-busy", "true");
    st.more.textContent = "Loading…";
    if (reset) st.status.textContent = "Looking up printings…";
    pageFor(st.oid, params, page).then(function (d) {
      if (st !== mine || seq !== st.seq) return;
      st.loading = false;
      var cards = (d.cards || []).filter(function (c) { return c && c.set; });
      if (reset) st.list = [];
      // the same printing twice (a page that moved under us) is listed once
      var seen = Object.create(null);
      st.list.forEach(function (c) { seen[c.set + "#" + c.collector_number] = true; });
      cards.forEach(function (c) { if (!seen[c.set + "#" + c.collector_number]) st.list.push(c); });
      keepMine(cards);
      st.page = page;
      st.hasMore = !!d.has_more;
      st.total = typeof d.total_cards === "number" ? d.total_cards : st.list.length;
      var plain = !st.params.finish && !st.params.frame && !st.params.border && st.params.order === "newest";
      if (page === 1 && !st.hasMore && plain) st.complete = true;
      if (page === 1 && !st.pinned) st.pinned = startPick();
      draw();
      if (page === 1 && st.hasMore) askMine();
    }).catch(function (err) {
      if (st !== mine || seq !== st.seq) return;
      st.loading = false;
      failed(err);
    });
  }
  function failed(err) {
    st.status.textContent = "";
    st.status.className = "ppstatus notice error";
    st.status.appendChild(document.createTextNode("Printings could not be loaded: " + ((err && err.message) || "no connection") + ". "));
    var again = el("button", "btn small", "Try again");
    again.type = "button";
    again.addEventListener("click", function () {
      st.status.className = "ppstatus";
      if (st.oid) load(true); else open(st.opts);
    });
    st.status.appendChild(again);
    st.more.disabled = false;
    st.more.removeAttribute("aria-busy");
    st.more.textContent = "Load more printings";
  }
  /* The card's own printings (current and picked) always show, whatever the filters, the
     typed search or the page: each one seen on any page is kept, and a card with more printings
     than a page that hasn't listed it asks for it once by set and number. */
  function mineRefs() { return [st.opts.current, st.opts.pending].filter(function (r) { return r && r.set && r.number; }); }
  function keepMine(cards) {
    mineRefs().forEach(function (r) {
      cards.forEach(function (c) { if (samePrint(c, r)) st.mine[c.set + "#" + c.collector_number] = c; });
    });
  }
  function askMine() {
    if (st.mineAsked || !st.oid) return;
    var missing = mineRefs().filter(function (r) {
      return !Object.keys(st.mine).some(function (k) { return samePrint(st.mine[k], r); });
    });
    if (!missing.length) return;
    st.mineAsked = true;
    var mine = st;
    missing.forEach(function (r) {
      oneFor(st.oid, r).then(function (d) {
        if (st !== mine) return;
        keepMine(d.cards || []);
        if (!st.pinned) st.pinned = startPick();
        draw();
      }).catch(function () { /* the list still works without it */ });
    });
  }
  // the preview starts on the card's own printing (or the pick waiting), else on the first
  function startPick() {
    var o = st.opts;
    return st.list.concat(extraMine()).filter(function (p) { return samePrint(p, o.pending) || samePrint(p, o.current); })[0] || null;
  }

  function isMine(p) { return samePrint(p, st.opts.current) || samePrint(p, st.opts.pending); }
  // the card's own printings kept from other pages and filters that this list doesn't hold
  function extraMine() {
    var have = Object.create(null);
    st.list.forEach(function (c) { have[c.set + "#" + c.collector_number] = true; });
    return Object.keys(st.mine).filter(function (k) { return !have[k]; }).map(function (k) { return st.mine[k]; });
  }
  // what the search and filters ask for; the card's own printings show either way
  function matches(p) { return isMine(p) || wanted(p); }
  function wanted(p) {
    if (!english(p)) return false;
    if (!st.all && (preview(p) || digital(p))) return false;
    if (st.complete) {
      if (st.params.finish && (p.finishes || []).indexOf(st.params.finish) < 0) return false;
      if (st.params.frame && String(p.frame || "") !== st.params.frame) return false;
      if (st.params.border && String(p.border || "") !== st.params.border) return false;
    }
    if (st.q) {
      var hay = (String(p.set_name || "") + " " + String(p.set || "")).toLowerCase();
      if (hay.indexOf(st.q) < 0) return false;
    }
    return true;
  }
  function effFinish(p) { return st.params.finish && (p.finishes || []).indexOf(st.params.finish) >= 0 ? st.params.finish : finishOn(p, st.finish).finish; }
  function sorted(list) {
    var order = st.params.order;
    var num = function (p) { var n = parseInt(p.collector_number, 10); return isNaN(n) ? 1e9 : n; };
    var byDate = function (a, b) {
      var x = String(a.released_at || ""), y = String(b.released_at || "");
      if (x !== y) return order === "oldest" ? (x < y ? -1 : 1) : (x < y ? 1 : -1);
      return num(a) - num(b);
    };
    return list.slice().sort(function (a, b) {
      if (order === "cheapest") {
        var pa = priceOf(a, effFinish(a)), pb = priceOf(b, effFinish(b));
        if (pa === null && pb !== null) return 1;
        if (pb === null && pa !== null) return -1;
        if (pa !== null && pb !== null && pa !== pb) return pa - pb;
      }
      return byDate(a, b);
    });
  }
  function markOf(p) {
    var o = st.opts;
    if (samePrint(p, o.pending)) return "Picked";
    if (samePrint(p, o.current)) return "Current";
    return "";
  }
  function draw() {
    if (!st) return;
    var extra = extraMine();
    var listed = st.list.filter(matches);
    // the printings that match, and the card's own shown only because it is the card's: one the
    // search or filters leave out here, or one not in the pages read (asked for by itself, or
    // left out of Scryfall's filtered list), which "Showing n of m" doesn't count either. Those
    // come after the matches, each group in the chosen order.
    var filtered = st.q || st.params.finish || st.params.frame || st.params.border;
    var ownOnly = sorted(listed.filter(function (p) { return !wanted(p); }).concat(extra));
    var shown = sorted(listed.filter(wanted)).concat(ownOnly);
    var hits = shown.length - ownOnly.length;
    var hidden = st.all ? 0 : st.list.filter(function (p) {
      return english(p) && (preview(p) || digital(p)) && !samePrint(p, st.opts.current) && !samePrint(p, st.opts.pending);
    }).length;
    st.grid.textContent = "";
    shown.forEach(function (p) {
      var li = el("li");
      var mark = markOf(p);
      var b = tile(p, { mark: mark, finish: effFinish(p) });
      if (mark) b.classList.add(mark === "Current" ? "current" : "picked");
      if (st.pinned && samePrint(p, { set: st.pinned.set, number: st.pinned.collector_number })) b.classList.add("on");
      b.addEventListener("click", function () { pin(p, b); });
      b.addEventListener("mouseenter", function () { if (wide() && hoverQuery && hoverQuery.matches) showPreview(p); });
      b.addEventListener("focus", function () { if (wide()) showPreview(p); });
      li.appendChild(b);
      st.grid.appendChild(li);
    });
    // what the list says
    var english_n = st.list.filter(function (p) { return english(p) && (st.all || !(preview(p) || digital(p))); }).length;
    var words;
    // the box below says it when filters leave nothing: the count line then says nothing more
    if (!st.list.length) words = filtered || st.all ? "" : "Scryfall lists no printing of this card.";
    else if (st.complete) words = filtered ? hits + " of " + plural(english_n, "printing") + (hits === 1 ? " matches." : " match.") : plural(hits, "printing") + ".";
    else words = (st.q ? plural(hits, "match", "matches") + ". " : "") + "Showing " + st.list.length + " of " + plural(st.total, "printing") + ".";
    if (ownOnly.length) words += (words ? " " : "") + ownWords(ownOnly, !hits);
    if (hidden) words += (words ? " " : "") + plural(hidden, "digital-only or preview printing") + " hidden.";
    // with no tile at all, the count line says what the box says, for screen readers only
    var readOnly = !words && !shown.length;
    if (readOnly) words = "No printing matches these filters.";
    st.status.className = readOnly ? "ppstatus sr-only" : "ppstatus";
    st.status.textContent = words;
    // nothing to show: say why, with a way back
    var stuck = !hits && (st.list.length > 0 || !!filtered || st.all);
    st.empty.textContent = "";
    st.empty.hidden = !stuck;
    if (stuck) {
      st.empty.appendChild(el("p", "", "No printing matches these filters."));
      var clear = el("button", "btn", "Clear filters");
      clear.type = "button";
      clear.addEventListener("click", clearFilters);
      st.empty.appendChild(clear);
    }
    st.moreRow.hidden = !st.hasMore;
    st.more.disabled = false;
    st.more.removeAttribute("aria-busy");
    st.more.textContent = "Load more printings";
    // the filter button counts what is set (phones)
    var n = ["finish", "frame", "border"].filter(function (k) { return st.params[k]; }).length + (st.all ? 1 : 0) + (st.params.order !== "newest" ? 1 : 0);
    st.filtBtn.textContent = n ? "Filters (" + n + ")" : "Filters";
    if (wide()) showPreview(st.pinned || shown[0] || null);
  }
  // the card's own printing shown although the search or filters leave it out; alone, when
  // nothing else matches
  function ownWords(own, alone) {
    var cur = own.some(function (p) { return samePrint(p, st.opts.current); });
    var pick = own.some(function (p) { return samePrint(p, st.opts.pending); });
    if (alone) {
      if (cur && pick) return "Only the card's current and picked printings are shown.";
      return pick ? "Only the picked printing is shown." : "Only the card's current printing is shown.";
    }
    if (cur && pick) return "The card's current and picked printings are shown too.";
    return pick ? "The picked printing is shown too." : "The card's current printing is shown too.";
  }
  function clearFilters() {
    st.q = ""; st.search.value = "";
    st.params = { finish: "", frame: "", border: "", order: "newest" };
    st.all = false; st.switchIn.checked = false;
    Array.prototype.forEach.call(st.selects, function (s, i) {
      s.value = i === 3 ? "newest" : "";
      if (window.MtgSelect) window.MtgSelect.refresh(s);
    });
    refilter();
    st.search.focus();
  }

  // the preview: the whole card large, its facts, and the pick
  function pin(p, b) {
    st.pinned = p;
    Array.prototype.forEach.call(st.grid.querySelectorAll(".pptile.on"), function (t) { t.classList.remove("on"); });
    if (b) b.classList.add("on");
    st.pinnedTile = b || null;
    showPreview(p);
    if (!wide()) {
      // the sheet covers the list: what is under it is out of reach (Tab, a screen reader)
      st.box.classList.add("previewing");
      st.tools.inert = true;
      st.scroll.inert = true;
    }
    var use = st.pv.querySelector(".ppuse");
    if (use && !use.disabled) use.focus();
    else { var back = st.pv.querySelector(".ppback"); if (back && !wide()) back.focus(); }
  }
  function hidePreview() {
    st.box.classList.remove("previewing");
    st.tools.inert = false;
    st.scroll.inert = false;
    if (st.pinnedTile && st.pinnedTile.isConnected) st.pinnedTile.focus();
  }
  function showPreview(p) {
    var pv = st.pv;
    if (pv.dataset.shows === (p ? p.set + "#" + p.collector_number : "") && pv.childNodes.length) return;
    pv.dataset.shows = p ? p.set + "#" + p.collector_number : "";
    pv.textContent = "";
    if (!p) {
      pv.appendChild(el("p", "muted pphint", "Point at a printing, or tap one, to see the whole card here."));
      return;
    }
    var o = st.opts;
    var fit = finishOn(p, st.finish);
    var pic = el("div", "ppbig");
    var src = p.image_normal || p.image_small;
    if (src) { var i = el("img"); i.src = src; i.alt = (p.name || o.name) + ", " + (p.set_name || "") + " printing"; pic.appendChild(i); }
    else pic.appendChild(el("span", "ph", p.name || o.name));
    pv.appendChild(pic);
    var info = el("div", "ppinfo");
    info.appendChild(el("h3", "", p.set_name || String(p.set || "").toUpperCase()));
    var line = code(p);
    if (p.released_at) line += " · " + (preview(p) ? "Out " : "Released ") + p.released_at;
    info.appendChild(el("p", "ppline", line));
    var cs = chips(p);
    var mark = markOf(p);
    if (mark) cs.insertBefore(el("span", "ppchip ppmine", mark === "Current" ? "In the deck now" : "Picked, not saved yet"), cs.firstChild);
    info.appendChild(cs);
    var prices = el("dl", "ppprices");
    (p.finishes && p.finishes.length ? p.finishes : ["nonfoil"]).forEach(function (f) {
      prices.appendChild(el("dt", "", FINISH_NAMES[f] || f));
      var v = priceOf(p, f);
      prices.appendChild(el("dd", "", v === null ? "No price" : money(v)));
    });
    info.appendChild(prices);
    var same = samePrint(p, o.pending || o.current);
    if (fit.changed && !same) {
      info.appendChild(el("p", "ppnote", "Not made in " + FINISH_NAMES[st.finish].toLowerCase() + ": the card becomes " +
        FINISH_NAMES[fit.finish].toLowerCase() + " with this printing."));
    }
    var acts = el("div", "ppacts");
    var use = el("button", "btn-primary ppuse", same ? (samePrint(p, o.pending) ? "Already picked" : "Already in the deck") : (o.pickLabel || "Use this printing"));
    use.type = "button";
    use.disabled = same;
    use.addEventListener("click", function () {
      var opts = st.opts, from = deckFinish(st.finish);
      close();
      opts.onPick(p, { finish: deckFinish(fit.finish), changed: fit.changed, from: from });
    });
    acts.appendChild(use);
    var back = el("button", "btn ppback", "Back to printings");
    back.type = "button";
    back.addEventListener("click", hidePreview);
    acts.appendChild(back);
    info.appendChild(acts);
    pv.appendChild(info);
  }

  window.MtgPrintPick = {
    open: open,
    close: close,
    isOpen: isOpen,
    tile: tile,
    defaults: defaults,
    oracleFor: oracleFor,
    pageFor: pageFor,
    finishOn: function (p, current) { var f = finishOn(p, current); return { finish: deckFinish(f.finish), changed: f.changed }; },
    priceOf: function (p, finish) { return priceOf(p, scryFinish(finish)); }
  };
})();
