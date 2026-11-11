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
   window.MtgSearchPanel.closeSheet(then), .address(url), .backPending(), .badge(), .setForm(params),
   .showing() and .onAddress(fn). */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var panel = $(".spanel");
  if (!panel) return;
  var Layers = window.MtgLayers;  // the history and the Back key for every layer (static/layers.js)
  var form = panel.tagName === "FORM" ? panel : panel.closest("form") || $("form", panel);
  var sheetQuery = window.matchMedia ? window.matchMedia("(max-width:599.98px),(pointer:coarse)") : null;
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // -- pick-several drop-downs --------------------------------------------------------------------
  var openPick = null, scrim = null, pickLayer = null;
  function pickLabel(d) {
    var v = $(".v", d);
    if (!v) return;
    var picked = $$("input:checked", d).map(function (i) { return (i.parentNode.textContent || "").trim(); });
    v.textContent = picked.length ? picked.join(", ") : d.getAttribute("data-none") || "";
  }
  function closePick(focusBack, fromBack) {
    if (!openPick) return;
    var d = openPick, layer = pickLayer;
    openPick = null; pickLayer = null;
    d.open = false;
    d.classList.remove("sheet");
    if (scrim) { scrim.remove(); scrim = null; }
    if (layer && !fromBack && Layers) Layers.release(layer);
    if (focusBack) { var b = $("summary", d); if (b) b.focus(); }
  }
  $$("details.pickm", panel).forEach(function (d) {
    pickLabel(d);
    d.addEventListener("toggle", function () {
      if (!d.open) { if (openPick === d) closePick(false); return; }
      if (openPick && openPick !== d) closePick(false);
      openPick = d;
      // the open list is a layer (static/layers.js): on a phone Back closes it, not the sheet or the page
      if (Layers) pickLayer = Layers.open({ name: "pickm", close: function () { if (openPick === d) closePick(true, true); } });
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
  // the open sheet is a layer (static/layers.js), as the boxes and menus are: the phone's Back
  // key closes it instead of leaving the page (audit D4). Where the browser needs a history
  // entry for that, the layers hold one, and closing the sheet any other way gives it back, so
  // the next Back leaves the page as it would have (audit F-1: no entry is left behind, Forward
  // or Back). Whichever way it closes, the history is as it was before the sheet opened, with the
  // page's current address (audit N1): a filter set in the sheet changes the address in place
  // through address() below, which keeps the entry's state and remembers the address (sheetHref)
  // for the entry under the layers', which gets it when that entry goes
  var sheetLayer = null, sheetHref = null, carry = null;
  function here() { return location.pathname + location.search; }
  // what the page shows: the address its list, boxes and badge were drawn for, and the form as
  // drawn (every field, as the browser would send it). Both follow a filter applied in place
  // (address) and a redraw from an address (setForm, redraw)
  var shownAddr = here(), shownForm = formSerial(), onAddr = null;
  function formSerial() { return form ? new URLSearchParams(new FormData(form)).toString() : ""; }
  function shownHere() { return here() === shownAddr; }
  // the page's own address for a filter applied in place (static/cardsearch.js, decks.js,
  // precons.js, collection.js, search.js while the sheet is open): the entry keeps its state.
  // While the sheet's own back() is on its way (a Show pressed as a list was still loading),
  // the entry it lands on gets the address
  function address(url) {
    if (Layers && Layers.inFlight() && !landed) { carry = url; return; }
    try { history.replaceState(history.state, "", url); } catch (err) { /* a sandboxed frame */ }
    if (sheetOpen()) sheetHref = location.href;
    shownAddr = here();
    shownForm = formSerial();
  }
  var landed = false;  // the layers' back() has landed: an address set now goes on the entry it landed on
  var lateHref = null;  // an address for the entry a back() that was slow to land lands on
  // a page script's own entry (static/search.js, decks.js push one): the page shows that address
  function showing() { shownAddr = here(); shownForm = formSerial(); }
  // Back or Forward landed on an entry of this page with another address than the page shows:
  // the page redraws from it, in place where its script knows how (onAddress), else as a fresh
  // load of that address (card search's pickers and rows, the Binder's and precons' sort, view
  // and drop-downs come from the gateway). Audit R1: after a reload with the sheet open the next
  // Back is a same-document step onto the page's own entry in Chromium, and the address lost the
  // filter while the list, the boxes and the badge kept it
  function redraw() {
    shownAddr = here();
    if (onAddr) { onAddr(); shownForm = formSerial(); } else location.reload();
  }
  // the panel's form as an address has it (Back, Forward): a box the address does not name is
  // off, a drop-down it does not name is on its "none" choice (the empty value: Any format) or,
  // without one (the sort), as the page was drawn; the drop-downs' own labels and the phone's
  // sort follow, without sending the form (no bubbling)
  function setForm(p) {
    if (!form) return;
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name || el.type === "hidden" || el.type === "submit" || el.type === "button") return;
      if (el.closest(".sortcw")) return;  // the phone's combined sort follows the real boxes
      if (el.type === "checkbox") el.checked = p.getAll(el.name).indexOf(el.value) >= 0;
      else if (el.type === "radio") el.checked = p.has(el.name) ? p.get(el.name) === el.value : el.defaultChecked;
      else if (el.tagName === "SELECT") {
        var want = p.get(el.name), at = -1, none = -1;
        Array.prototype.forEach.call(el.options, function (o, i) {
          if (want !== null ? o.value === want : o.defaultSelected) at = i;
          if (o.value === "") none = i;
        });
        el.selectedIndex = want === null && none >= 0 ? none : at >= 0 ? at : 0;
        if (window.MtgSelect) window.MtgSelect.refresh(el);
      } else el.value = p.get(el.name) || "";
    });
    $$("details.pickm, .rbar .g.sort .sortf, .rbar .g.sort .dir", form).forEach(function (el) {
      el.dispatchEvent(new Event("change"));
    });
    badge();
    shownForm = formSerial();
  }
  // the layers' own back() is on its way, or its popstate is being handled: a page script's
  // popstate handler (static/search.js) is not seeing a Back press
  function backPending() { return !!(Layers && Layers.backPending()); }
  // give the sheet's layer back (its history entry where one is held): once that has landed,
  // the sheet's address goes on the entry it landed on, with any address a page script set
  // meanwhile (carry), and then ``then`` runs (a form sent, a search drawn, a link followed:
  // audit H1, L1, L5), so what follows is over the page's own entry
  function giveBack(layer, then) {
    var href = sheetHref;
    var go = function () {
      // the layers' wait for a back() that is slow to land ran out (a busy machine): the address
      // goes on the entry showing now and again on the one the back lands on (popstate below)
      var early = !!(Layers && Layers.inFlight());
      landed = true;
      if (href) address(href);
      if (carry) { address(carry); carry = null; }
      landed = false;
      if (early) lateHref = here();
      if (then) then();
    };
    if (layer && Layers) Layers.release(layer, go); else go();
  }
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
  function stopsIn(root) {
    return $$("a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])", root)
      .filter(function (f) { return f.getClientRects().length > 0 && !inClosedFold(f); });
  }
  function sheetStops() { return stopsIn(sheet); }
  function openSheet() {
    if (!sheet) return;
    sheet.classList.add("open");
    document.documentElement.classList.add("sp-sheet-open");
    // the sheet's room at its foot follows the foot's real height (theme --sp-foot): at 200% text
    // its two buttons wrap onto two rows and covered the card size above (0.7.46 audit AM1)
    var foot = $(".sp-sheet-foot", sheet);
    if (foot && !sheet._footWatch && window.ResizeObserver) {
      sheet._footWatch = new ResizeObserver(function () {
        sheet.style.setProperty("--sp-foot", Math.ceil(foot.getBoundingClientRect().height) + "px");
      });
      sheet._footWatch.observe(foot);
    }
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
    if (!sheetLayer && Layers) sheetLayer = Layers.open({ name: "sheet", close: function () { closeSheet(true); } });
    sheetHref = location.href;
    var c = $(".sp-sheet-head .close", sheet);
    if (c) c.focus();
  }
  // ``fromBack``: a Back press took the sheet's layer already (nothing to give back; the
  // sheet's address goes on the entry Back landed on); ``then`` runs once the sheet's entry
  // has been given back (at once where there is none to give)
  function closeSheet(fromBack, then) {
    if (!sheetOpen()) { if (then) then(); return; }
    sheet.classList.remove("open");
    document.documentElement.classList.remove("sp-sheet-open");
    sheet.removeAttribute("role");
    sheet.removeAttribute("aria-modal");
    pageInert(false);
    if (sheetScrim) { sheetScrim.remove(); sheetScrim = null; }
    if (openPick) closePick(false);
    var layer = sheetLayer;
    sheetLayer = null;
    if (fromBack) { if (sheetHref) address(sheetHref); if (then) then(); }
    else giveBack(layer, then);
    if (openBtn) openBtn.setAttribute("aria-expanded", "false");
    // the focus goes back to the button; where the button has gone with the sheet (the window
    // grew past phone width: the groups are the page's own now, audit D1) to the groups' first
    // control, or the panel's (a View sheet whose choices the bar shows at that width)
    var back = openBtn && openBtn.getClientRects().length ? openBtn : sheetStops()[0] || stopsIn(panel)[0];
    if (back) back.focus();
  }
  // Back or Forward landed on an entry of this page (the page's own entry under the one a
  // reload left, a search's) with another address than the page shows: the page redraws from
  // it. The layers' own back() (the sheet's, a menu's) is not a Back press. The sheet's own Back
  // is the layers' (static/layers.js), which closes it; Forward never brings a layer back
  window.addEventListener("popstate", function () {
    var late = lateHref;
    lateHref = null;  // for that landing only, never a later Back press
    if (late && Layers && Layers.landing()) {
      // the slow back() of the sheet's layer has landed (layers.js handled its popstate first)
      landed = true;
      address(late);
      landed = false;
      return;
    }
    if (backPending()) return;
    if (!shownHere()) redraw();
  });
  // a filter in the open sheet that sends the form (the Binder's tag, a precons set, Show on
  // most panels): the sheet's layer goes back first, then the form goes, so the new page is one
  // Back from the page the sheet opened on (audit N2: the entry stayed, one dead Back). A page
  // script that takes the submit itself (deck search, card search) has stopped it before this
  document.addEventListener("submit", function (e) {
    if (e.defaultPrevented || e.target !== form || !sheetOpen()) return;
    e.preventDefault();
    var submitter = e.submitter && e.submitter.form === form ? e.submitter : null;
    // Show with nothing changed since the page was drawn (the Binder's and precons' only filters
    // send the form by themselves): the sheet closes, the page is not asked for again (audit L4:
    // a page load to the same page, and a dead Back)
    if (submitter && submitter.classList.contains("sp-show") && formSerial() === shownForm) { closeSheet(); return; }
    closeSheet(false, function () {
      if (form.requestSubmit) { if (submitter) form.requestSubmit(submitter); else form.requestSubmit(); } else form.submit();
    });
  });
  // Clear filters in the open sheet, where it is a link to the page without filters (deck
  // search, precons, My decks, the Binder): the same, the layer goes back first, then the link
  // is followed, so the cleared page is one Back from the page the sheet opened on (audit L1:
  // Back brought the sheet back, and leaving took three presses)
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a.sp-clear[href]") : null;
    if (!a || e.defaultPrevented || !sheet || !sheet.contains(a) || !sheetOpen()) return;
    if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || (a.target && a.target !== "_self")) return;
    e.preventDefault();
    var href = a.href;
    closeSheet(false, function () { location.href = href; });
  });
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
  // a window that grows past phone width while the sheet is open (a Fold unfolding, a desktop
  // window widened): the sheet goes, with the page's inert and scroll lock, and the groups show
  // as the page's own (folded or open as the button says); on every panel, not only one with a
  // fold (audit D1: the page stayed inert behind a sheet the width had hidden)
  // the button says whether the groups show: on a phone that is the sheet, else the fold
  if (phoneQuery && sheet && openBtn) {
    var onWidth = function () {
      if (!phoneQuery.matches && sheetOpen()) closeSheet();
      openBtn.setAttribute("aria-expanded", (phoneQuery.matches || !folds ? sheetOpen() : !sheet.classList.contains("folded")) ? "true" : "false");
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

  window.MtgSearchPanel = {
    openSheet: openSheet,
    // closeSheet(then): ``then`` runs once the sheet's entry has been given back
    closeSheet: function (then) { closeSheet(false, typeof then === "function" ? then : null); },
    isSheetOpen: sheetOpen, badge: badge, address: address, backPending: backPending,
    // onAddress(fn): the page script redraws in place from the address Back or Forward landed
    // on; without one the page reloads. showing(): the page script pushed its own entry.
    // setForm(params): the panel's form as an address has it
    onAddress: function (fn) { onAddr = typeof fn === "function" ? fn : null; },
    showing: showing, setForm: setForm
  };
})();
