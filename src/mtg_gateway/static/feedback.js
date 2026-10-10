// Loaded on every gateway page (theme.render).
// 1. A button that submits a form shows a busy state at once (aria-busy, styled in theme.py), and
//    a second press of the same form while it is sending is ignored, so a slow Apply or Create
//    never looks dead and never sends twice. Forms a page script handles itself (it calls
//    preventDefault) are left to that script.
// 2. Dropdown menus (details.dd: the account menu, a deck's More menu) close on a tap or click
//    anywhere outside them, on Escape, and on the Back button on phones, so one open menu never
//    has to be dismissed by finding its own button again.
// 3. Registers the gateway's service worker (/sw.js), which never caches a page: it only answers
//    with a plain "you're offline" page when a page can't be reached at all.
(function () {
  "use strict";
  var RESET_MS = 10000; // a download or a slow answer: allow another press after this long
  var LONG_RESET_MS = 120000; // a button with data-busy-label runs long (the deck simulation)

  function unbusy(button) {
    button.removeAttribute("aria-busy");
    if (button.dataset.busyRestore !== undefined) {
      button.disabled = false;
      var label = button.querySelector(".busy-label");
      if (label) label.textContent = button.dataset.busyRestore;
      delete button.dataset.busyRestore;
    }
  }
  function clear(form) {
    delete form.dataset.sending;
    var busy = form.querySelectorAll("[aria-busy=true]");
    for (var i = 0; i < busy.length; i++) unbusy(busy[i]);
    var outside = form.id ? document.querySelectorAll("[form='" + form.id + "'][aria-busy=true]") : [];
    for (var j = 0; j < outside.length; j++) unbusy(outside[j]);
  }
  // A button with data-busy-text says what it is doing ("Signing out…") and locks itself after the
  // form has been handed to the browser (next tick, so its own name=value still posts).
  // data-busy-label is the same for a long run (the deck simulation): the busy state lasts longer.
  function busyText(button) {
    var text = button.getAttribute("data-busy-text") || button.getAttribute("data-busy-label");
    if (!text) return;
    var label = null;
    for (var i = 0; i < button.childNodes.length; i++) {
      var node = button.childNodes[i];
      if (node.nodeType === 3 && node.textContent.trim()) {
        label = document.createElement("span");
        label.className = "busy-label";
        label.textContent = node.textContent;
        button.replaceChild(label, node);
        break;
      }
    }
    if (!label) return;
    button.dataset.busyRestore = label.textContent;
    label.textContent = text;
    setTimeout(function () { button.disabled = true; }, 0);
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || e.defaultPrevented) return;
    if (form.dataset.sending) {
      e.preventDefault();
      return;
    }
    form.dataset.sending = "1";
    var button = e.submitter;
    if (button && button.tagName === "BUTTON") { button.setAttribute("aria-busy", "true"); busyText(button); }
    setTimeout(function () { clear(form); }, button && button.dataset && button.dataset.busyLabel ? LONG_RESET_MS : RESET_MS);
  });

  // Coming back with the Back button restores the page from memory: nothing is sending any more.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var forms = document.querySelectorAll("form[data-sending]");
    for (var i = 0; i < forms.length; i++) clear(forms[i]);
  });

  function openMenus() { return document.querySelectorAll("details.dd[open]"); }
  function closeMenus(except) {
    var open = openMenus();
    for (var i = 0; i < open.length; i++) if (open[i] !== except) open[i].removeAttribute("open");
  }
  document.addEventListener("click", function (e) {
    var inside = e.target instanceof Element ? e.target.closest("details.dd") : null;
    closeMenus(inside);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenus(null); });
  // On phones (and in the app) one history entry stands for "a menu is open", so Back closes the
  // menu instead of leaving the page. This block is the entry's only owner: `pushed` says the
  // entry exists, `popping` that the page itself asked for it back. Opening the first menu
  // pushes it; the last menu closing gives it back; and when that back lands late, after the
  // person has already opened another menu, the entry is pushed again for that menu (never
  // before it lands, so two entries or two backs can never be in flight), so Back never leaves
  // the page while a menu is open and never goes back twice.
  var pushed = false, popping = false;
  function phoneLike() {
    return window.matchMedia("(max-width: 599.98px)").matches ||
      window.matchMedia("(max-width: 899.98px) and ((pointer: coarse) or (hover: none))").matches ||
      document.body.classList.contains("app");
  }
  function takeEntry() {
    // while the page's own back() is still landing, the popstate handler takes the entry instead
    if (pushed || popping || !phoneLike()) return;
    history.pushState({ menu: 1 }, "");
    pushed = true;
  }
  function giveEntryBack() {
    if (!pushed || popping) return;
    pushed = false;
    popping = true;
    history.back();
  }
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("dd")) return;
    if (d.open) {
      closeMenus(d);
      keepOnScreen(d);
      takeEntry();
    } else {
      var m = d.querySelector(":scope > .menu");
      if (m) { m.style.left = ""; m.style.right = ""; }
      if (!openMenus().length) giveEntryBack();
    }
  }, true);
  window.addEventListener("popstate", function () {
    if (popping) {
      // the page's own back() landed; a menu opened meanwhile needs the entry again
      popping = false;
      if (openMenus().length) takeEntry();
      return;
    }
    // the person pressed Back: the entry is gone, close what is open without going back again
    pushed = false;
    if (openMenus().length) closeMenus(null);
  });
  // A menu panel hangs from one edge of its button: the right edge usually, the left edge for
  // the deck page's More menu (.menu.left). Near the matching edge of the window (the first
  // card of a grid, a row's menu on a phone, More at 720 px) part of it would be off screen.
  // The panel is moved back inside, with an 8px margin, whichever edge it hangs from: its
  // used left offset (what the stylesheet's left or right resolved to) plus the shift becomes
  // an explicit left, so no knowledge of the anchor is needed. (Not a transform: Chrome's
  // mobile layout still counts a translated panel's old place as page width.) The move is
  // undone when the menu closes.
  function keepOnScreen(d) {
    var menu = d.querySelector(":scope > .menu");
    if (!menu || menu.classList.contains("sheet")) return;
    menu.style.left = ""; menu.style.right = "";
    var r = menu.getBoundingClientRect(), vw = document.documentElement.clientWidth, shift = 0;
    if (r.left < 8) shift = 8 - r.left;
    else if (r.right > vw - 8) shift = (vw - 8) - r.right;
    if (shift) {
      menu.style.left = (parseFloat(getComputedStyle(menu).left) || 0) + shift + "px";
      menu.style.right = "auto";
    }
  }

  // Inside the Android app: the account menu's App section calls the app through window.MtgNative
  // (the server renders those buttons from the app's user agent; the class is also set here in
  // case a proxy rewrote the user agent).
  if (window.MtgNative) {
    document.body.classList.add("app");
    // An app older than this page has no such action: leave its button out rather than show a dead one.
    var actions = document.querySelectorAll("[data-native]");
    for (var k = 0; k < actions.length; k++) {
      if (typeof window.MtgNative[actions[k].getAttribute("data-native")] !== "function") actions[k].setAttribute("hidden", "");
    }
    document.addEventListener("click", function (e) {
      var b = e.target instanceof Element ? e.target.closest("[data-native]") : null;
      if (!b) return;
      var fn = window.MtgNative[b.getAttribute("data-native")];
      if (typeof fn === "function") { e.preventDefault(); closeMenus(null); try { fn(); } catch (err) { /* older app */ } }
    });
  } else {
    var nativeOnly = document.querySelectorAll("[data-native]");
    for (var n = 0; n < nativeOnly.length; n++) nativeOnly[n].setAttribute("hidden", "");
  }

  // 4. Shared text helpers for every page script (deck.js, scan.js, collection.js): one date
  //    format in the viewer's own time zone and locale, and real plurals.
  var MtgText = {
    when: function (value, timeOnly) {
      var d = value instanceof Date ? value : new Date(value);
      if (isNaN(d)) return "";
      try {
        return timeOnly ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
          : d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
      } catch (e) { return d.toISOString().slice(0, 16).replace("T", " "); }
    },
    plural: function (n, word, pluralWord) {
      var num = Number(n);
      return (isNaN(num) ? n : num.toLocaleString()) + " " + (num === 1 ? word : (pluralWord || word + "s"));
    }
  };
  window.MtgText = MtgText;
  // Every <time datetime> the server rendered in UTC is shown in the viewer's time zone.
  var times = document.querySelectorAll("time[datetime]");
  for (var t = 0; t < times.length; t++) {
    var iso = times[t].getAttribute("datetime");
    var local = MtgText.when(iso, times[t].getAttribute("data-fmt") === "time");
    if (local) { times[t].setAttribute("title", times[t].textContent); times[t].textContent = local; }
  }
  // "1 card" / "7 cards" next to a number field: the word follows the value (the odds form).
  document.addEventListener("input", function (e) {
    var input = e.target;
    if (!(input instanceof HTMLInputElement) || !input.form || !input.name) return;
    var words = input.form.querySelectorAll("[data-plural='" + input.name + "']");
    for (var w = 0; w < words.length; w++) {
      var one = words[w].getAttribute("data-one") || words[w].textContent.replace(/s$/, "");
      words[w].setAttribute("data-one", one);
      words[w].textContent = Number(input.value) === 1 ? one : one + "s";
    }
  });

  // 5. A card image that cannot be loaded (Scryfall down, an old link) shows the card's name on a
  //    plain tile instead of the browser's broken-image glyph. Inline onerror handlers are not
  //    allowed by the pages' script policy, so the error is caught here, on its way up.
  document.addEventListener("error", function (e) {
    var img = e.target;
    if (!(img instanceof HTMLImageElement) || img.classList.contains("img-broken")) return;
    if (img.closest(".topbar") || img.classList.contains("av")) return;
    img.classList.add("img-broken");
    var ph = document.createElement("span");
    ph.className = "ph img-fallback";
    ph.textContent = img.getAttribute("alt") || img.getAttribute("title") || "Image unavailable";
    ph.setAttribute("role", "img");
    ph.setAttribute("aria-label", ph.textContent + " (image unavailable)");
    if (img.parentNode) img.parentNode.insertBefore(ph, img.nextSibling);
  }, true);

  // 6. A deep link to an element whose id carries a member's text (#cat-Ramp, #card-Sol%20Ring):
  //    the browser cannot always find it, so it is found here, opened and marked for a moment.
  function reveal() {
    var hash = location.hash;
    if (!hash || hash.length < 2) return;
    var id;
    try { id = decodeURIComponent(hash.slice(1)); } catch (e) { id = hash.slice(1); }
    // only the editor's own deep links; plain anchors (the guide's sections) are the browser's
    if (!/^(cat|card)-/.test(id)) return;
    var target = document.getElementById(id);
    if (!target) return;
    for (var d = target.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) d.open = true;
    target.classList.add("target");
    target.scrollIntoView({ block: "center" });
    if (target.tabIndex < 0) target.setAttribute("tabindex", "-1");
    target.focus({ preventScroll: true });
    setTimeout(function () { target.classList.remove("target"); }, 4000);
  }
  window.MtgReveal = reveal;
  window.addEventListener("hashchange", reveal);
  reveal();

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }
})();
