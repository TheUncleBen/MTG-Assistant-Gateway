/* Fills the deck-list placeholders and filters My decks in the browser.
   Home and My decks render at once when the member's deck list is still being read from Archidekt
   (a cold start): the decks area carries data-decks-src, the address of /api/decks/mine with the
   page's own filters, and the answer brings the HTML the page would have rendered itself (one
   renderer, server side: the folder's breadcrumb, its Folders and Decks areas) plus the count for
   the results bar. On My decks the filter panel (static/searchpanel.js) asks the same address as
   its boxes change: the name as you type, a colour or a format as you pick it, the sort, its
   direction and the view at once; the list area is replaced and the address follows, so a reload
   or a shared link shows the same. Without this script the placeholder stays and the panel's
   Filter button sends the form; a reload shows the list. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var boxes = Array.prototype.slice.call(document.querySelectorAll("[data-decks-src]"));
  if (typeof fetch !== "function") return;

  function failed(box) {
    box.removeAttribute("aria-busy");
    box.textContent = "";
    var p = document.createElement("p");
    p.className = "notice error";
    p.textContent = "Your decks could not be loaded. ";
    var a = document.createElement("a");
    a.href = window.location.pathname + window.location.search;
    a.textContent = "Reload";
    p.appendChild(a);
    box.appendChild(p);
  }

  function fillToolbar(data) {
    var total = $("#decks-total");
    if (!total) return;
    if (typeof data.count_text === "string") total.textContent = data.count_text;
    else if (typeof data.total === "number") total.textContent = data.total + (data.total === 1 ? " deck" : " decks");
    total.hidden = data.total === null; // the list couldn't be read: no count
  }

  // Every load of a box is numbered, the page's own first load included, and only the latest
  // one may draw: a slow answer landing after a newer one (the matches for "ur" after those for
  // "urza", or the unfiltered first load after a colour pick) is dropped, and its request is
  // cut off where the browser can do that.
  var seq = 0, inflight = null;
  function load(box, src) {
    var mine = ++seq;
    if (inflight) { try { inflight.abort(); } catch (e) { /* an older browser */ } }
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    inflight = ctl;
    var opts = { credentials: "same-origin", headers: { Accept: "application/json" } };
    if (ctl) opts.signal = ctl.signal;
    return fetch(src, opts)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(String(r.status))); })
      .then(function (data) {
        if (!data || typeof data.html !== "string") throw new Error("bad answer");
        if (mine !== seq) return null;  // a newer load is on its way: this one says nothing
        box.removeAttribute("aria-busy");
        box.innerHTML = data.html; // rendered by the gateway, as the page itself would have
        fillToolbar(data);
        box.dispatchEvent(new CustomEvent("decks:loaded", { bubbles: true, detail: data }));
        return data;
      }, function (err) {
        if (mine !== seq) return null;  // cut off by a newer load, or too late to matter
        throw err;
      });
  }

  boxes.forEach(function (box) {
    load(box, box.getAttribute("data-decks-src")).catch(function () { failed(box); });
  });

  // -- My decks: the filter panel refreshes the list in place --------------------------------------
  var form = $("#listform[data-live]"), list = $("#decks-list");
  if (!form || !list) return;
  var timer = null;

  // -- My decks: which decks are card packages, and the Show filter ----------------------------------
  // Read once from the Packages page's own listing (/decks/api/packages?whose=mine, every page up
  // to four) only after the list is drawn, so the deck list's cold load stays one Archidekt
  // listing. A package's tile then carries the package icon in place of its initial and a
  // Package label with its budget and formats; the Show filter (Decks and packages, Decks only,
  // Packages only) applies in place as it changes, never asking the gateway again. A package the
  // deck listing left out is drawn from this read at the top level. When the read fails, Show
  // stays usable for Decks and packages and one muted line says packages couldn't be told apart.
  // Archidekt busy or unreachable (429 or 503 with Retry-After): the read is sent once more after
  // that wait, when the wait is short, before the line says so
  var pkgs = null, pkgFailed = false, pkgAsked = false, pkgRetried = false, PKG_PAGES = 4, PKG_WAIT_MAX = 120;
  var NOT_TOLD = "Packages couldn't be told apart from decks just now; every deck is shown.";
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function pkgIcon() {
    if (window.MtgMenu && window.MtgMenu.icon) return window.MtgMenu.icon("package");
    return el("span", null, "P");
  }
  // My Decks (decks only) or Packages (packages only), the switch at the top of the page (the owner,
  // 2026-10-25; its value rides in the form)
  function showMode() {
    var s = form.querySelector("input[name=show]");
    return s && s.value === "packages" ? "packages" : "decks";
  }
  function pkgNote() {
    var n = document.getElementById("pk-note");
    if (!n) {
      n = el("p", "muted small", NOT_TOLD);
      n.id = "pk-note";
      n.hidden = true;
      n.setAttribute("role", "status");
      list.parentNode.insertBefore(n, list);
    }
    return n;
  }
  function readPackages() {
    if (pkgAsked) return;
    pkgAsked = true;
    var found = {}, page = 1;
    function one() {
      return fetch("/decks/api/packages?whose=mine&page=" + page, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) {
          var wait = parseInt(r.headers.get("Retry-After") || "", 10);
          if ((r.status === 429 || r.status === 503) && !pkgRetried && wait > 0 && wait <= PKG_WAIT_MAX) {
            pkgRetried = true;
            return new Promise(function (go) { setTimeout(go, wait * 1000); }).then(one);
          }
          return r.json().then(answered);
        });
    }
    function answered(d) {
      if (!d || !d.ok) throw new Error("packages");
      (d.packages || []).forEach(function (p) { found[String(p.id)] = p; });
      if (d.more && page < PKG_PAGES) { page += 1; return one(); }
      return null;
    }
    one().then(function () { pkgs = found; window.MtgPackages = found; mark(); },
      function () { pkgFailed = true; mark(); });
  }
  function pkgLabel(pk) {
    var lb = el("span", "ui-lbl grey");
    lb.appendChild(pkgIcon());
    lb.appendChild(document.createTextNode("Package"));
    return lb;
  }
  // the detail line as the mockup draws it: the Package label and the budget; the formats are
  // on the tile's title and on Deck settings
  function pkgLine(fmt, pk) {
    fmt.textContent = "";
    fmt.appendChild(pkgLabel(pk));
    if (pk.budget) fmt.appendChild(document.createTextNode(" · " + pk.budget));
    var bits = [pk.budget, pk.formats].filter(Boolean);
    fmt.title = "Package" + (bits.length ? " · " + bits.join(" · ") : "");
  }
  function decorate(li, pk) {
    li.classList.add("pkg");
    li.setAttribute("data-package", "1");
    var ini = li.querySelector(".thumb .ini");
    if (ini) { ini.textContent = ""; ini.appendChild(pkgIcon()); }
    var fmt = li.querySelector(".info .fmt");
    if (fmt) pkgLine(fmt, pk);
  }
  // a package the deck listing left out: a tile of its own, from the packages read
  function extraTile(pk) {
    var li = el("li", "deck pkg pkg-extra");
    li.setAttribute("data-deck-id", String(pk.id));
    li.setAttribute("data-deck-name", pk.name);
    li.setAttribute("data-own", "1");
    li.setAttribute("data-package", "1");
    var a = el("a");
    a.href = "/decks/" + encodeURIComponent(String(pk.id));
    var thumb = el("span", "thumb noart"), ini = el("span", "ini");
    ini.appendChild(pkgIcon());
    thumb.appendChild(ini);
    a.appendChild(thumb);
    var info = el("span", "info plain"), text = el("span", "text");
    var name = el("span", "name", pk.name); name.title = pk.name;
    var fmt = el("span", "fmt");
    pkgLine(fmt, pk);
    text.appendChild(name); text.appendChild(fmt);
    info.appendChild(text);
    a.appendChild(info);
    li.appendChild(a);
    if (window.MtgMenu && window.MtgMenu.kebab) li.appendChild(window.MtgMenu.kebab("Menu for " + pk.name));
    return li;
  }
  function decksArea() { return list.querySelector(".fv-area[aria-labelledby='fv-decks']"); }
  function extrasList() {
    var ul = list.querySelector("ul.decklist");
    if (ul) return ul;
    var area = decksArea();
    if (!area) return null;
    var empty = area.querySelector(".empty");
    if (empty) empty.hidden = true;
    var view = (form.querySelector("input[name=view]:checked") || { value: "grid" }).value;
    ul = el("ul", "plain decklist " + (view === "list" ? "list" : "grid"));
    area.appendChild(ul);
    return ul;
  }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function mark() {
    var mode = showMode(), note = pkgNote();
    note.hidden = !pkgFailed;
    if (pkgFailed) mode = "";
    var shownPk = 0, shownDk = 0;
    Array.prototype.slice.call(list.querySelectorAll("li.deck[data-deck-id]")).forEach(function (li) {
      var id = li.getAttribute("data-deck-id"), pk = pkgs && pkgs[id];
      if (pk && !li.hasAttribute("data-package")) decorate(li, pk);
      var isPk = li.hasAttribute("data-package");
      var off = mode === "decks" ? isPk : mode === "packages" ? !isPk : false;
      li.classList.toggle("sh-off", off);
      if (!off) { if (isPk) shownPk += 1; else shownDk += 1; }
    });
    var top = !form.querySelector("input[name=folder]");
    if (pkgs && top && mode !== "decks") {
      Object.keys(pkgs).forEach(function (id) {
        if (list.querySelector("li.deck[data-deck-id='" + id + "']")) return;
        var ul = extrasList();
        if (!ul) return;
        ul.appendChild(extraTile(pkgs[id]));
        shownPk += 1;
      });
    }
    var total = $("#decks-total"), head = list.querySelector("#fv-decks");
    // the one empty state of the Packages tab, under the Packages heading (where the decks
    // would be, after the folders); the deck list's own ("This folder is empty.") is off meanwhile
    var empty = $("#pk-empty"), none = list.querySelector("#fv-decks ~ p.empty");
    if (mode === "packages" && !shownPk && !empty) {
      empty = el("p", "empty", "No packages here yet. New package makes one.");
      empty.id = "pk-empty";
      empty.setAttribute("role", "status");
      if (head) head.parentNode.insertBefore(empty, head.nextSibling);
      else list.parentNode.insertBefore(empty, list);
    }
    if (empty) empty.hidden = mode !== "packages" || shownPk > 0;
    if (none) none.hidden = mode === "packages";
    if (mode === "packages") {
      if (total) total.textContent = plural(shownPk, "package");
      if (head) head.textContent = "Packages";  // the results bar carries the count
    } else if (mode === "decks") {
      // every deck, as Home counts them, and how many sit in this level's folders (polish C5)
      var inFolders = 0;
      Array.prototype.forEach.call(list.querySelectorAll("li.folder[data-decks]"), function (f) {
        inFolders += parseInt(f.getAttribute("data-decks"), 10) || 0;
      });
      if (total) total.textContent = plural(shownDk + inFolders, "deck") + (inFolders ? " · " + inFolders + " in folders" : "");
      if (head) head.textContent = "Decks";
    }
    list.dispatchEvent(new CustomEvent("decks:marked", { bubbles: true }));
  }
  // through the panel (static/searchpanel.js address): the entry keeps its state, the sheet's
  // while it is open (audit N1: a null state wiped the sheet's entry)
  function setAddress() {
    var q = params().toString(), url = "/decks" + (q ? "?" + q : "");
    var P = window.MtgSearchPanel;
    if (P && P.address) P.address(url); else history.replaceState(history.state, "", url);
  }
  list.addEventListener("decks:loaded", function () { if (pkgs || pkgFailed) mark(); else readPackages(); });
  if (!list.hasAttribute("data-decks-src")) setTimeout(readPackages, 0); // the list came drawn
  function params() {
    var fd = new FormData(form), out = new URLSearchParams();
    fd.forEach(function (v, k) { if (String(v) !== "") out.append(k, String(v)); });
    return out;
  }
  function refresh() {
    var p = params();
    var api = new URLSearchParams(p);
    api.set("shape", "list");
    list.setAttribute("aria-busy", "true");
    load(list, "/api/decks/mine?" + api.toString())
      .then(function (data) {
        if (!data) return;  // an older load: a newer one draws and sets the address
        setAddress();
      })
      .catch(function () { failed(list); });
  }
  function soon() {
    clearTimeout(timer);
    // the pending look at once (the list dims, theme.py); the ask itself waits for the pause in typing
    list.setAttribute("aria-busy", "true");
    timer = setTimeout(refresh, 250);
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    clearTimeout(timer);
    refresh();
    if (window.MtgSearchPanel) window.MtgSearchPanel.closeSheet();
  });

  // -- My Decks or Packages: the switch at the top works in place ----------------------------------
  // Its links are whole pages (a reload or a shared link shows the same side); with scripts a
  // press draws the other side at once, without a page load: the form's show value, the heading,
  // the filter box's words, the page's orange button and the address follow it (the browser's
  // Back comes back the same way), and mark() shows the packages or the decks.
  var seg = $(".hubhead .hubseg");
  function isPackages(href) { return /[?&]show=packages(?:&|$)/.test(href || ""); }
  function applyShow(packages, href) {
    var s = form.querySelector("input[name=show]");
    if (packages && !s) {
      s = document.createElement("input");
      s.type = "hidden";
      s.name = "show";
      form.appendChild(s);
    }
    if (s) { if (packages) s.value = "packages"; else s.parentNode.removeChild(s); }
    Array.prototype.forEach.call(seg.querySelectorAll("a"), function (a) {
      if (isPackages(a.getAttribute("href")) === packages) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    var word = packages ? "Packages" : "My Decks";
    var h1 = $(".hubhead h1");
    if (h1) h1.textContent = word;
    document.title = document.title.replace(/^(My Decks|Packages)/, word);
    var newDeck = $(".hubhead .acts a[href='/decks/new']"), newPkg = $(".hubhead .acts a[href='/decks/new-package']");
    if (newDeck) newDeck.classList.toggle("btn-primary", !packages);
    if (newPkg) newPkg.classList.toggle("btn-primary", packages);
    var q = $("#q", form), lab = form.querySelector("label[for=q]"), show = form.querySelector(".sp-show");
    if (q) q.placeholder = packages ? "Package name" : "Deck name";
    if (lab) lab.textContent = packages ? "Filter package name" : "Filter deck name";
    if (show) show.textContent = packages ? "Show packages" : "Show decks";
    if (href !== null) history.pushState({ show: packages ? "packages" : "" }, "", href);
    if (pkgs || pkgFailed) mark(); else readPackages();
  }
  if (seg) {
    seg.addEventListener("click", function (ev) {
      var a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
      if (!a || !seg.contains(a) || ev.defaultPrevented || ev.button !== 0) return;
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;  // a new tab is the member's
      ev.preventDefault();
      if (a.hasAttribute("aria-current")) return;
      applyShow(isPackages(a.getAttribute("href")), a.getAttribute("href"));
    });
    window.addEventListener("popstate", function () {
      applyShow(isPackages(window.location.search), null);
    });
  }
  form.addEventListener("input", function (e) {
    var t = e.target;
    if (t && t.type === "search") soon();
  });
  form.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || t.closest(".sortcw")) return; // the phone's combined sort sets the real boxes, which change
    if (t.type === "search") return;
    if (t.type === "checkbox" || t.type === "radio" || t.tagName === "SELECT") { clearTimeout(timer); refresh(); }
  });
})();
