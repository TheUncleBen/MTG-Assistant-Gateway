/* The search panel card search, deck search and My decks share (searchpanel.py). The panel works
   without this file (every control is a form field; a pick-several drop-down is a <details>);
   this adds what a drop-down and a sheet need:
   - a pick-several drop-down closes on Esc (the focus goes back to its button), on a press
     outside it, and when another opens; its button shows what is picked ("Rare, Mythic rare");
     under 600 px or with a coarse pointer the list is a bottom sheet with a dimmed page behind it;
   - on phones the Filters button opens the filter groups as a full-screen sheet, which × , Esc
     and the sheet's own button close; the button counts the filters in use; while it is open it
     is a modal dialog: the page behind it is inert, Tab stays inside it, and the focus goes back
     to the button when it closes (audit C3);
   - on a Fold or desktop a panel whose button is marked "fold" (card search) folds the groups in
     place behind that button instead, and keeps the last state on the account (data-pref);
   - on phones Sort and its direction fold into one drop-down ("Name ↑", "Name ↓");
   - More filters (card search) opens the extra filters from the results bar;
   - a form marked data-apply=submit sends itself when a drop-down or the view changes (deck search:
     each search is an Archidekt request, so the text boxes and the colour buttons wait for Search).
   Loaded before the page's own script (cardsearch.js, search.js, decks.js), which may call
   window.MtgSearchPanel.closeSheet() and .badge(). */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var panel = $(".spanel");
  if (!panel) return;
  var form = panel.tagName === "FORM" ? panel : panel.closest("form") || $("form", panel);
  var sheetQuery = window.matchMedia ? window.matchMedia("(max-width:599.98px),(pointer:coarse)") : null;
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // -- pick-several drop-downs --------------------------------------------------------------------
  var openPick = null, scrim = null;
  function pickLabel(d) {
    var v = $(".v", d);
    if (!v) return;
    var picked = $$("input:checked", d).map(function (i) { return (i.parentNode.textContent || "").trim(); });
    v.textContent = picked.length ? picked.join(", ") : d.getAttribute("data-none") || "";
  }
  function closePick(focusBack) {
    if (!openPick) return;
    var d = openPick;
    openPick = null;
    d.open = false;
    d.classList.remove("sheet");
    if (scrim) { scrim.remove(); scrim = null; }
    if (focusBack) { var b = $("summary", d); if (b) b.focus(); }
  }
  $$("details.pickm", panel).forEach(function (d) {
    pickLabel(d);
    d.addEventListener("toggle", function () {
      if (!d.open) { if (openPick === d) closePick(false); return; }
      if (openPick && openPick !== d) closePick(false);
      openPick = d;
      if (sheetQuery && sheetQuery.matches) {
        d.classList.add("sheet");
        scrim = el("div", "pickm-scrim");
        scrim.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); closePick(true); });
        // inside the open Filters sheet (its own stacking order), else on the page
        (d.closest(".sp-filters.open") || document.body).appendChild(scrim);
      }
    });
    d.addEventListener("change", function () { pickLabel(d); });
  });
  document.addEventListener("pointerdown", function (e) {
    if (openPick && !openPick.classList.contains("sheet") && !openPick.contains(e.target)) closePick(false);
  });
  // capture: Escape closes only the list, not a sheet the list sits in
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && openPick) { e.preventDefault(); e.stopPropagation(); closePick(true); }
  }, true);

  // -- the phone sheet, and the in-place fold on a Fold or desktop -----------------------------
  var sheet = $("#sp-filters", panel), openBtn = $(".sp-open", panel), badgeEl = openBtn ? $(".n", openBtn) : null;
  var phoneQuery = window.matchMedia ? window.matchMedia("(max-width:599.98px)") : null;
  var folds = !!(sheet && openBtn && openBtn.classList.contains("fold"));
  function inPlace() { return folds && !(phoneQuery && phoneQuery.matches); }
  function folded() { return inPlace() && sheet.classList.contains("folded"); }
  // open or fold the groups in place; the choice follows the account (prefs.js), the focus stays
  // on the button, which says what it did (aria-expanded)
  function setFold(open) {
    sheet.classList.toggle("folded", !open);
    openBtn.setAttribute("aria-expanded", open ? "true" : "false");
    var pref = sheet.getAttribute("data-pref");
    if (pref && window.MtgPrefs) window.MtgPrefs.set(pref, open ? "open" : "closed");
  }
  function sheetOpen() { return !!(sheet && sheet.classList.contains("open")); }
  var sheetScrim = null, madeInert = [];
  // the page behind the open sheet is inert (a Tab, a screen reader and a tap reach only the
  // sheet and its backdrop): everything beside the sheet and its ancestors, put back on close
  function pageInert(on) {
    if (!on) { madeInert.forEach(function (k) { k.inert = false; }); madeInert = []; return; }
    for (var node = sheet; node && node.parentNode && node !== document.body; node = node.parentNode) {
      var kids = node.parentNode.children;
      for (var i = 0; i < kids.length; i++) {
        var k = kids[i];
        if (k === node || k === sheetScrim || k.tagName === "SCRIPT" || k.tagName === "STYLE" || k.inert) continue;
        k.inert = true;
        madeInert.push(k);
      }
    }
  }
  // what the keyboard can reach in the sheet, in order: not a hidden Clear filters (no box), and
  // not what a closed fold holds (More filters, a pick-several list: Chromium keeps a box for it
  // but refuses it the focus), only the fold's own summary
  function inClosedFold(f) {
    for (var d = f.parentElement && f.parentElement.closest("details:not([open])"); d;
         d = d.parentElement && d.parentElement.closest("details:not([open])")) {
      var sm = $(":scope > summary", d);
      if (!(sm && sm.contains(f))) return true;
    }
    return false;
  }
  function sheetStops() {
    return $$("a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])", sheet)
      .filter(function (f) { return f.getClientRects().length > 0 && !inClosedFold(f); });
  }
  function openSheet() {
    if (!sheet) return;
    sheet.classList.add("open");
    document.documentElement.classList.add("sp-sheet-open");
    if (openBtn) openBtn.setAttribute("aria-expanded", "true");
    // a real backdrop under the sheet (theme .sp-scrim): a tap on it closes the sheet and never
    // reaches the page under it (audit B2); Escape and the focus return work as before
    if (!sheetScrim) {
      sheetScrim = el("div", "sp-scrim");
      sheetScrim.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); closeSheet(); });
      sheet.parentNode.insertBefore(sheetScrim, sheet);
    }
    // a modal dialog named by its title (the markup's aria-labelledby) only while it is open: on
    // a Fold or desktop the same groups sit in the page and are no dialog
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    pageInert(true);
    var c = $(".sp-sheet-head .close", sheet);
    if (c) c.focus();
  }
  function closeSheet() {
    if (!sheetOpen()) return;
    sheet.classList.remove("open");
    document.documentElement.classList.remove("sp-sheet-open");
    sheet.removeAttribute("role");
    sheet.removeAttribute("aria-modal");
    pageInert(false);
    if (sheetScrim) { sheetScrim.remove(); sheetScrim = null; }
    if (openBtn) { openBtn.setAttribute("aria-expanded", "false"); openBtn.focus(); }
  }
  // how many filters are in use: a group of boxes counts once, a text box when it has text, a
  // drop-down when it is not on its first choice
  function badge() {
    if (!badgeEl || !sheet || form.hasAttribute("data-count-own")) return;
    var n = 0, seen = {};
    $$("input, select", sheet).forEach(function (f) {
      if (!f.name || f.closest(".sheet-size")) return;
      if (f.type === "checkbox" || f.type === "radio") { if (f.checked && !seen[f.name]) { seen[f.name] = 1; n += 1; } }
      else if (f.tagName === "SELECT") { if (f.selectedIndex > 0 && f.closest(".fg") && !f.closest(".colors")) n += 1; }
      else if (f.type !== "hidden" && f.value.trim()) n += 1;
    });
    badgeEl.textContent = n ? String(n) : "";
    // the name box sits outside the sheet (so it is not in the badge's count), but it is a filter
    // Clear filters clears
    var named = $$(".sp-name input", form).some(function (f) { return f.type !== "hidden" && f.value.trim(); });
    clearShown(n > 0 || named);
  }
  // Clear filters (the bar's and the sheet's) shows once a filter is set: a button that would do
  // nothing looked unintentional (polish C4)
  function clearShown(on) {
    $$(".sp-clear", panel).forEach(function (c) { c.hidden = !on; });
  }
  panel.addEventListener("click", function (e) {
    if (e.target.closest(".sp-open")) { if (inPlace()) setFold(sheet.classList.contains("folded")); else openSheet(); return; }
    if (e.target.closest(".sp-sheet-head .close")) { closeSheet(); return; }
    var show = e.target.closest(".sp-show");
    if (show && show.type === "button") { closeSheet(); return; }
  });
  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || !sheetOpen()) return;
    if (window.MtgSelect && window.MtgSelect.isOpen()) return;  // the themed list handles its own keys
    if (e.key === "Tab") {
      // Tab stays in the sheet: past its last control it goes to the first, and back; from the
      // page (a browser without inert) it comes into the sheet
      var stops = sheetStops();
      if (!stops.length) return;
      var a = document.activeElement, first = stops[0], last = stops[stops.length - 1];
      if (e.shiftKey && (a === first || !sheet.contains(a))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (a === last || !sheet.contains(a))) { e.preventDefault(); first.focus(); }
      return;
    }
    // an Escape a dropdown already used (to close its list or sheet) leaves the panel open: such
    // a handler stops the event (a pick-several list above, a card search picker's open list in
    // static/cardsearch.js), so an Escape that reaches here closes the sheet, wherever the focus
    // is in it (audit C3: a picker's list left open behind the focus kept the sheet open)
    if (e.key === "Escape") closeSheet();
  });
  if (form) {
    form.addEventListener("input", badge);
    form.addEventListener("change", badge);
  }
  badge();
  // a window that grows past phone width while the sheet is open: the sheet goes, the groups
  // show as the page's own (folded or open as the button says)
  // the button says whether the groups show: on a phone that is the sheet, else the fold
  if (phoneQuery && folds) {
    var onWidth = function () {
      if (!phoneQuery.matches && sheetOpen()) closeSheet();
      openBtn.setAttribute("aria-expanded", (phoneQuery.matches ? sheetOpen() : !sheet.classList.contains("folded")) ? "true" : "false");
    };
    onWidth();
    if (phoneQuery.addEventListener) phoneQuery.addEventListener("change", onWidth); else if (phoneQuery.addListener) phoneQuery.addListener(onWidth);
  }

  // -- Sort and its direction as one drop-down on phones ----------------------------------------
  $$(".rbar .g.sort", panel).forEach(function (g) {
    var sortf = $(".sortf", g), dir = $(".dir", g);
    if (!sortf || !dir) return;
    var wrap = el("span", "sortcw"), sel = el("select");
    sel.setAttribute("aria-label", "Sort by");
    function build() {
      sel.textContent = "";
      $$("option", sortf).forEach(function (o) {
        if (dir.value === "auto" && o.value === sortf.value) {
          var a = el("option", null, o.text); a.value = o.value + "|auto"; a.selected = true; sel.appendChild(a);
        }
        [["asc", "↑"], ["desc", "↓"]].forEach(function (d) {
          var x = el("option", null, o.text + " " + d[1]);
          x.value = o.value + "|" + d[0];
          if (o.value === sortf.value && dir.value === d[0]) x.selected = true;
          sel.appendChild(x);
        });
      });
      if (window.MtgSelect) window.MtgSelect.refresh(sel);
    }
    build();
    wrap.appendChild(sel);
    g.appendChild(wrap);
    sel.addEventListener("change", function () {
      var parts = sel.value.split("|");
      sortf.value = parts[0]; dir.value = parts[1];
      if (window.MtgSelect) { window.MtgSelect.refresh(sortf); window.MtgSelect.refresh(dir); }
      sortf.dispatchEvent(new Event("change", { bubbles: true }));
    });
    sortf.addEventListener("change", function (e) { if (e.target === sortf) build(); });
    dir.addEventListener("change", build);
  });

  // -- More filters from the results bar ----------------------------------------------------------
  $$("[data-more-filters]", panel).forEach(function (btn) {
    var more = document.getElementById(btn.getAttribute("aria-controls"));
    if (!more) return;
    more.classList.add("jsx");
    btn.hidden = false;
    btn.setAttribute("aria-expanded", more.open ? "true" : "false");
    btn.addEventListener("click", function () {
      // More filters sits inside the folded groups: they open first, else nothing would show
      if (folded()) { setFold(true); more.open = false; }
      more.open = !more.open;
      btn.setAttribute("aria-expanded", more.open ? "true" : "false");
      if (more.open) { var f = $("input, select, button", more); if (f) f.focus(); }
    });
    more.addEventListener("toggle", function () { btn.setAttribute("aria-expanded", more.open ? "true" : "false"); });
  });

  // -- a form that applies a drop-down or view choice at once ------------------------------------
  if (form && form.getAttribute("data-apply") === "submit") {
    form.addEventListener("change", function (e) {
      var t = e.target;
      if (!t || t.closest(".sortcw")) return;
      var apply = (t.tagName === "SELECT") || (t.type === "radio" && t.name === "view")
        || (t.type === "checkbox" && t.closest(".pickm"));
      if (!apply) return;
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    });
  }

  window.MtgSearchPanel = { openSheet: openSheet, closeSheet: closeSheet, isSheetOpen: sheetOpen, badge: badge };
})();
