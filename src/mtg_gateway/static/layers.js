/* One Back for every layer over a page: window.MtgLayers. A layer is anything that opens over the
   page and closes on its own: the boxes (static/box.js), the card and row menus (static/cardmenu.js,
   static/deck.js), the drop-down menus (static/feedback.js), the card viewer (static/cardview.js),
   the filter sheet and its pick-several lists (static/searchpanel.js), the printing picker
   (static/printpick.js), a themed drop-down's list (static/select.js), the card search's Add sheet
   (static/cardsearch.js) and the top bar's unfolded search (static/sitesearch.js). Each opens with
   MtgLayers.open({name, close}) and tells of its own closing with MtgLayers.release(layer, then);
   a Back that means "close this" reaches the top layer's close(true). Loaded on every page before
   static/box.js and the page scripts (theme.py).

   Audit F-1 and F-2 (eccfa3d9): a layer's own history entry was left behind as a dead Forward, or a
   dead Back after a reload; a layer without one let Back leave the page. Three ways, picked once a
   page:
   - the Android app ("key"): no entry at all. The app's Back key asks the page first
     (window.MtgBackKey(), MainActivity.kt) and goes back in the history only when no layer took
     it; an app that does so says it (MtgNative.backKey), an older one gets the history way.
   - Chromium on Android ("watcher"): no entry either. A CloseWatcher takes the Back gesture (the
     browser sends it as a close request, as it does Escape) while the layer is open, and is
     destroyed when the layer closes by itself.
   - every other browser ("history": Safari on the iPhone, Firefox, the desktop, an older app): on a
     phone, a touch screen in the rail range or the app, one history entry stands for "a layer is
     open" (a desktop with a mouse has no Back key for a layer, and pushes nothing). The first layer
     takes it, the last one closing gives it back with history.back(), and Back closes the top layer.
     The entry is one: a layer opened while that back() is still on its way takes it again once it
     has landed, so there are never two entries and never two backs in flight. Forward onto the
     entry a layer gave back steps back at once (there is nothing to show); a page loaded on the
     entry (a reload with a layer open) makes it a plain one.
   A page script that changes the page from a layer (a link, a form) waits for a back() of ours with
   MtgLayers.settled(go), and tells a popstate of ours from a Back press with MtgLayers.backPending(). */
(function () {
  "use strict";
  if (window.MtgLayers) return;
  var app = document.body.classList.contains("app");
  var native = window.MtgNative || null;
  var mode;
  if (app && native && typeof native.backKey === "function") mode = "key";
  else if (!app && typeof window.CloseWatcher === "function" && /Android/i.test(navigator.userAgent || "")) mode = "watcher";
  else mode = "history";
  var stack = [];  // the open layers, bottom to top: {name, close(fromBack), entry, watcher}
  // history mode: ``owned``, the entry is on the history; ``pending``, our own back() over it has
  // not landed; ``landing``, its popstate is being handled (this task); ``waiting``, what runs then
  var owned = false, pending = false, landing = false, waiting = [];
  // a phone, a touch screen in the rail range (to 1279.98 px, as theme.py draws the rail) or the app
  function phoneLike() {
    return app || (window.matchMedia && (window.matchMedia("(max-width: 599.98px)").matches ||
      window.matchMedia("(max-width: 1279.98px) and ((pointer: coarse) or (hover: none))").matches));
  }
  function entries() { return mode === "history" && phoneLike(); }
  function wantsEntry() { return stack.some(function (l) { return l.entry; }); }
  function top() { return stack.length ? stack[stack.length - 1] : null; }
  // the app enables its Back key while a layer is open (MainActivity.syncBackCallback)
  function tell() {
    if (native && typeof native.layer === "function") { try { native.layer(stack.length > 0); } catch (e) { /* an older app */ } }
  }
  function push() {
    try { history.pushState({ mtglayer: 1 }, ""); owned = true; } catch (e) { owned = false; }
  }
  function flush() {
    var list = waiting;
    waiting = [];
    list.forEach(function (go) { go(); });
  }
  // runs go once a back() of ours has landed (at once when none is on its way); a Back that never
  // reports runs it anyway after a while (this one only, so an older call's wait never hurries it)
  function settled(go) {
    if (!pending) { go(); return; }
    waiting.push(go);
    setTimeout(function () {
      var at = waiting.indexOf(go);
      if (at >= 0) { waiting.splice(at, 1); go(); }
    }, 1500);
  }
  function drop(layer) {
    var at = stack.indexOf(layer);
    if (at < 0) return false;
    stack.splice(at, 1);
    if (layer.watcher) { try { layer.watcher.destroy(); } catch (e) { /* gone already */ } layer.watcher = null; }
    tell();
    return true;
  }
  /* spec: {name, close(fromBack), entry}. close(true) is called when Back took the layer (its entry
     or watcher is gone: nothing to give back); the layer closes as by its × then. entry: false says
     the layer is the page's own state (a box the address opened), so it takes no history entry;
     the Back key and the watcher still close it. Returns the layer, for release(). */
  function open(spec) {
    var layer = { name: (spec && spec.name) || "layer", close: spec.close, entry: !(spec && spec.entry === false), watcher: null };
    stack.push(layer);
    if (mode === "watcher") {
      try {
        var w = new window.CloseWatcher();
        w.onclose = function () { layer.watcher = null; fromBack(layer); };
        layer.watcher = w;
      } catch (e) { layer.watcher = null; }
    } else if (entries() && layer.entry && !owned) {
      // while a back() of ours is still landing, the popstate takes the entry instead
      if (!pending) push();
    }
    tell();
    return layer;
  }
  /* The layer closed by itself (×, Escape, a backdrop, an item picked). ``then`` runs once its
     history entry, if it held the last one, has been given back: a page change started before
     that would race the back(), and the back could win (a tap on Remove… left the member on the
     page, or took them back to it after the box had opened). */
  function release(layer, then) {
    if (!layer || !drop(layer)) { if (then) then(); return; }
    if (!owned || wantsEntry()) { if (then) then(); return; }
    // the last layer with an entry closed: its entry goes back; the popstate is ours
    owned = false;
    pending = true;
    if (then) settled(then);
    try { history.back(); } catch (e) { pending = false; flush(); }
  }
  // Back took the layer: it closes without giving anything back
  function fromBack(layer) {
    if (!drop(layer)) return;
    layer.close(true);
  }
  // the app's Back key (MainActivity.kt), or a script standing in for it: the top layer closes
  // and true comes back; false when no layer is open, so the app goes back in the history
  function backKey() {
    var t = top();
    if (!t) return false;
    if (mode === "history") t.close(false); else fromBack(t);
    return true;
  }
  window.addEventListener("popstate", function (e) {
    if (pending) {
      // our own back() landed. The page scripts' handlers run after this one in the same event:
      // still not a Back press (landing). A layer opened meanwhile takes the entry now
      pending = false;
      landing = true;
      setTimeout(function () { landing = false; }, 0);
      if (wantsEntry() && !owned && entries()) push();
      flush();
      return;
    }
    if (mode !== "history") return;
    if (owned) {
      // Back took the entry: the top layer closes; a layer still open under it (the sheet under
      // its pick-several list) takes the entry again
      owned = false;
      var t = top();
      if (t) fromBack(t);
      if (wantsEntry() && entries()) push();
      return;
    }
    if (e.state && e.state.mtglayer) {
      // Forward onto the entry a layer gave back: nothing to show, so straight back
      pending = true;
      try { history.back(); } catch (err) { pending = false; }
    }
  });
  // the page loaded on a layer's entry (a reload with a layer open, the app's pull to refresh):
  // the layer is not back, so the entry becomes a plain one of the page
  if (history.state && history.state.mtglayer) {
    try { history.replaceState(null, "", location.href); } catch (e) { /* the entry stays */ }
  }
  window.MtgLayers = {
    open: open, release: release, settled: settled, top: top, mode: mode,
    // true while a back() of ours has not landed, and through the task in which it lands
    backPending: function () { return pending || landing; },
    // the two apart: the back() is still on its way; its popstate is being handled (this task)
    inFlight: function () { return pending; },
    landing: function () { return landing; },
    // a layer is open (one not named ``except``: the header's own search box does not count
    // for its shortcuts)
    isOpen: function (except) { return stack.some(function (l) { return l.name !== except; }); },
    // the Back key's way: the top layer closes (false: none was open)
    backKey: backKey
  };
  window.MtgBackKey = backKey;
})();
