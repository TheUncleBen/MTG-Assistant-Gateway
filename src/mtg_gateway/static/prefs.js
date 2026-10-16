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
  function post(body) {
    if (!window.fetch) return Promise.resolve(null);
    return fetch("/api/v1/prefs", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body)
    }).then(function (r) { return r.ok ? r.json() : null; }, function () { return null; });
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
  // printing picker. It applies at once on <html data-card-size> and follows the account.
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest("[data-card-size-set]") : null;
    if (!btn) return;
    var size = btn.getAttribute("data-card-size-set");
    if (size !== "s" && size !== "m" && size !== "l") return;
    document.documentElement.setAttribute("data-card-size", size);
    Array.prototype.forEach.call(document.querySelectorAll("[data-card-size-set]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-card-size-set") === size ? "true" : "false");
    });
    window.MtgPrefs.set("card.size", size);
  });

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
