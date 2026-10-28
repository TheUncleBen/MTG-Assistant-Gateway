/* Choices that follow the account (0.7.29, prefs.py): views, card size, stack orders, the deck
   stats toggles and the dismissed tips are kept on the member's account, so every device shows
   the same. The page hands over the ones its scripts need in <script id="mtg-prefs">, so nothing
   waits for a request; a change is saved with POST /api/v1/prefs.

   Before 0.7.29 some of these lived in this browser's storage. They are copied to the account
   once (the account's own value wins), then removed here. Three things stay on this device on
   purpose: the last deck picked in "Add to deck", the last set per deck in the printing picker,
   and a scan in progress. Loaded on every signed-in page, before the page's own scripts. */
(function () {
  "use strict";
  var values = {};
  var blob = document.getElementById("mtg-prefs");
  try { values = (blob && JSON.parse(blob.textContent)) || {}; } catch (e) { values = {}; }

  function csrf() {
    var input = document.querySelector("input[name=csrf]");
    return input ? input.value : "";
  }
  // Saves go one after another, in the order they were made: two quick changes (L, then S) must
  // never reach the gateway the other way round and leave the account with the first one. Each
  // save ends one way or another (an answer cut off or unreadable, no network, or no answer within
  // SAVE_WAIT_MS), so one bad save never holds up the ones after it.
  var SAVE_WAIT_MS = 15000;
  var queue = Promise.resolve();
  function send(body) {
    var stop = window.AbortController ? new AbortController() : null;
    var timer = stop ? setTimeout(function () { stop.abort(); }, SAVE_WAIT_MS) : null;
    return fetch("/api/v1/prefs", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body),
      signal: stop ? stop.signal : undefined
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (answer) { if (timer) clearTimeout(timer); return answer; });
  }
  function post(body) {
    if (!window.fetch) return Promise.resolve(null);
    queue = queue.then(function () { return send(body); });
    return queue;
  }
  function has(name) { return Object.prototype.hasOwnProperty.call(values, name); }

  window.MtgPrefs = {
    get: function (name) { return has(name) ? values[name] : undefined; },
    // null forgets the choice. The page acts at once; the save follows (a failed save leaves
    // the choice on this page until the next one).
    set: function (name, value) {
      if (value === null || value === undefined) delete values[name];
      else values[name] = value;
      var body = { set: {} };
      body.set[name] = value === undefined ? null : value;
      return post(body);
    }
  };

  // -- card size: the S / M / L switch next to "View as" (theme.card_size_control) and in the
  // printing picker. Each page keeps its own (card.size.<page>, Ben 2026-10-28): the page's
  // applies at once on <html data-card-size>, the printing picker's on the picker and
  // <html data-card-size-printings>. It follows the account, with a copy on this device that a
  // page the account has no size for starts with.
  var root = document.documentElement;
  var SIZES = { s: 1, m: 1, l: 1 };
  function local(page, size) {
    try {
      if (size === undefined) return window.localStorage.getItem("mtg.card.size." + page);
      window.localStorage.setItem("mtg.card.size." + page, size);
    } catch (e) { /* private window: the account still has it */ }
    return null;
  }
  function showSize(page, size, picker) {
    var mine = page === "printings";
    if (mine) {
      root.setAttribute("data-card-size-printings", size);
      if (picker) picker.setAttribute("data-card-size", size);
    } else {
      root.setAttribute("data-card-size", size);
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-card-size-set]"), function (b) {
      var own = b.closest("[data-card-size-page]");
      if (((own && own.getAttribute("data-card-size-page")) === "printings") !== mine) return;
      b.setAttribute("aria-pressed", b.getAttribute("data-card-size-set") === size ? "true" : "false");
    });
  }
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest("[data-card-size-set]") : null;
    if (!btn) return;
    var size = btn.getAttribute("data-card-size-set");
    if (!SIZES[size]) return;
    var own = btn.closest("[data-card-size-page]");
    var page = (own && own.getAttribute("data-card-size-page")) || root.getAttribute("data-card-size-page") || "deck";
    showSize(page, size, btn.closest(".printpick"));
    local(page, size);
    window.MtgPrefs.set("card.size." + page, size);
  });
  (function () {  // a page the account has no size for: this device's, copied to the account
    var page = root.getAttribute("data-card-size-page");
    if (!page || !root.hasAttribute("data-card-size-unset")) return;
    var size = local(page);
    if (!SIZES[size]) return;
    var copy = {};
    copy["card.size." + page] = size;
    var apply = function () { showSize(page, size, null); };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
    else apply();
    post({ copy: copy });
  })();

  // -- the one-time copy of this browser's pre-0.7.29 choices ---------------------------------
  var store;
  try { store = window.localStorage; } catch (e) { return; }  // private window: nothing kept
  if (!store) return;
  var copy = {}, keys = [];
  function take(key, name, value) {
    if (value === undefined || value === null) return;
    keys.push(key);
    copy[name] = value;
    // this page already shows it; the account's wins. After "Forget my view choices" the old
    // choice is never shown: the gateway drops it instead of copying it.
    if (!has(name) && values["prefs.forgotten"] !== true) values[name] = value;
  }
  try {
    for (var i = 0; i < store.length; i++) {
      var key = store.key(i);
      if (key && key.indexOf("mtg-stack-order:") === 0) {
        var order = JSON.parse(store.getItem(key) || "null");
        if (Array.isArray(order) && order.length) take(key, "stack:" + key.slice(16), order);
        else keys.push(key);
      }
    }
    if (store.getItem("mtg.edhplay.steps") === "1") take("mtg.edhplay.steps", "edhplay.steps", true);
    if (store.getItem("mtg-scan-intro-seen") === "1") take("mtg-scan-intro-seen", "scan.intro", true);
    var stats = store.getItem("mtg.deckstats");
    if (stats) {
      var parsed = JSON.parse(stats);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) take("mtg.deckstats", "deckstats", parsed);
      else keys.push("mtg.deckstats");
    }
  } catch (e) { /* unreadable: left as it is */ }
  if (!keys.length) return;
  post({ copy: copy }).then(function (answer) {
    if (!answer || !answer.ok) return;  // tried again on the next page
    keys.forEach(function (k) { try { store.removeItem(k); } catch (e) { /* kept */ } });
  });
})();
