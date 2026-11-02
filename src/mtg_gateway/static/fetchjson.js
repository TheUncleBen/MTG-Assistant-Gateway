/* The one way a page's script talks to the gateway, and the one wording when it cannot
   (0.7.46 audit QG-06, QG-07, QG-08). Loaded on every signed-in page before the page scripts
   (theme.py), so each of them sends through MtgFetch and shows its answers:

   - signed out: a request is answered with a 401 or a redirect to the sign-in page (on the
     sign-in provider in production, which a script cannot follow), so redirects are never
     followed. Either answer is "You're signed out, so nothing was changed." with a Sign in
     button that comes back to this very page (path and query). Try again cannot help here.
   - no connection: a request that got no answer at all (the browser's own TypeError, "Failed to
     fetch" in one browser and something else in the next) is "No connection; nothing was
     changed." Nothing raw from the browser reaches the screen.
   - an answer the page cannot read (an error page instead of JSON): the request reached the
     gateway and the outcome is unknown, so the member is told to reload.

   MtgFetch.json(href, opts) rejects with err.signedOut / err.noConnection and err.message in
   those words. MtgFetch.answer(href, opts) never rejects: it resolves to the gateway's JSON with
   .status, or to {ok: false, error: "signed_out" | "no_connection" | "server", message}, with
   .signedOut or .noConnection set, so a script that reads d.ok and d.message shows the right
   words with no code of its own. MtgFetch.problem(x, what) gives the text and the Sign in
   button for any of those answers or errors, for a toast, a strip or a note.

   A script's own fetch (not through MtgFetch) gets the same words as a page-wide note: signed
   out, with Sign in back to this page, for any request; no connection for a change. See
   "signed out or no connection" below.

   The gateway busy (dbwait.py: a request waited the limit for the database's write lock and
   was turned away with a 503 marked X-MTG-Busy, nothing changed): every request a page's script
   sends, through MtgFetch or a plain fetch (window.fetch is this script's from here on, loaded
   before every page script), is held rather than answered, and one note says the gateway is
   busy, with Try again, which sends the held requests once more, and ×, which hands each script
   the busy answer as it came (its own words then say the save did not go through). Nothing the
   member typed is touched, no page is left, and no raw answer reaches the screen. */
(function () {
  "use strict";
  var NO_CONNECTION = "No connection; nothing was changed.";
  var SIGNED_OUT = "You're signed out, so nothing was changed.";
  var UNREADABLE = "The gateway sent an answer this page could not read. Reload the page to see whether the change was made.";

  function signInHref() {
    return "/login?next=" + encodeURIComponent(location.pathname + location.search);
  }
  // the Sign in button (the one orange button), which comes back to this page; the app opens
  // its own sign-in from the same address
  function signInLink(cls) {
    var a = document.createElement("a");
    a.className = cls || "btn btn-primary";
    a.href = signInHref();
    a.textContent = "Sign in";
    return a;
  }
  function signedOutError(what) {
    var out = new Error(what ? "You're signed out, so " + what + "." : SIGNED_OUT);
    out.signedOut = true;
    return out;
  }
  function noConnectionError(what) {
    var out = new Error(what ? "No connection; " + what + "." : NO_CONNECTION);
    out.noConnection = true;
    return out;
  }
  function isSignedOut(r) {
    return !!r && (r.type === "opaqueredirect" || r.status === 401);
  }
  function send(href, o) {
    var headers = {};
    if (o.json !== false) headers.Accept = "application/json";
    if (typeof o.body === "string" && !o.form) headers["Content-Type"] = "application/json";
    if (o.form) headers["Content-Type"] = "application/x-www-form-urlencoded";
    if (o.csrf) headers["X-CSRF-Token"] = o.csrf;
    if (o.headers) Object.keys(o.headers).forEach(function (k) { headers[k] = o.headers[k]; });
    var init = {
      method: o.method || (o.body !== undefined ? "POST" : "GET"),
      body: o.body,
      headers: headers,
      credentials: "same-origin",
      // a JSON address answers a signed-out request with the sign-in redirect: never followed
      redirect: "manual"
    };
    if (o.signal) init.signal = o.signal;
    if (o.cache) init.cache = o.cache;
    if (o.keepalive) init.keepalive = true;
    // this script's own request: its caller shows the words, so the page-wide note stays away
    init.mtgShared = true;
    return fetch(href, init);
  }
  /* The gateway's JSON, or a rejection: err.signedOut, err.noConnection, or the answer's own
     message (err.status, err.server when the body was no JSON). opts: method, body (a string is
     sent as JSON; with form: true as a form), csrf, headers, signal, cache, keepalive, what (what
     was not done, for the message: "the card was not added"). */
  function json(href, opts) {
    var o = opts || {};
    return send(href, o).then(function (r) {
      if (isSignedOut(r)) throw signedOutError(o.what);
      return r.json().then(function (d) {
        if (d && typeof d === "object") d.status = r.status;
        return d;
      }, function () {
        var out = new Error(UNREADABLE);
        out.status = r.status;
        out.server = true;
        throw out;
      });
    }, function (err) {
      if (err && err.name === "AbortError") throw err;
      throw noConnectionError(o.what);
    });
  }
  /* The same request, never rejected: the JSON answer with .status, or {ok: false, ...} with
     .signedOut / .noConnection / .server and the one message, for scripts that read d.ok. An
     abort (opts.signal) still rejects, as it is the script's own doing. */
  function answer(href, opts) {
    return json(href, opts).then(function (d) {
      return d && typeof d === "object" ? d : { ok: false, status: 200, error: "server", message: UNREADABLE, server: true };
    }, function (err) {
      if (err && err.name === "AbortError") throw err;
      var d = { ok: false, status: err.status || 0, message: err.message };
      if (err.signedOut) { d.error = "signed_out"; d.signedOut = true; }
      else if (err.noConnection) { d.error = "no_connection"; d.noConnection = true; }
      else { d.error = "server"; d.server = true; }
      return d;
    });
  }
  /* x: a rejected error or an {ok: false} answer. The text to show and the actions that can
     help: Sign in when signed out (Try again cannot help then), nothing otherwise, so the
     caller adds its own Try again. ``what`` is what was not done ("the card was not added"),
     used when x carries no message of its own. */
  function problem(x, what) {
    var signedOut = !!x && (x.signedOut || x.status === 401 || x.error === "unauthenticated" || x.error === "signed_out");
    var text;
    if (signedOut) text = what ? "You're signed out, so " + what + "." : SIGNED_OUT;
    else if (x && x.noConnection) text = what ? "No connection; " + what + "." : NO_CONNECTION;
    else text = (x && x.message) || (what ? "That did not work; " + what + ". Try again." : "That did not work. Try again.");
    return { text: text, signedOut: signedOut, noConnection: !!(x && x.noConnection), actions: signedOut ? [signInLink()] : [] };
  }
  // "You're signed out" and Sign in as a block for a panel: what could not be read
  function signedOut(what) {
    var wrap = document.createElement("div");
    var p = document.createElement("p");
    p.className = "notice error";
    p.setAttribute("role", "alert");
    p.textContent = "You're signed out, so " + (what || "this") + " could not be read.";
    wrap.appendChild(p);
    wrap.appendChild(signInLink());
    return wrap;
  }
  // One press at a time (0.7.46 audit QG-13): the button is busy and disabled until ``work``
  // (a promise, or a function returning one) settles; a press while busy does nothing. Returns
  // the promise, or null when the button was already busy.
  function press(button, work) {
    if (!button) return typeof work === "function" ? work() : work;
    if (button.getAttribute("aria-busy") === "true") return null;
    button.setAttribute("aria-busy", "true");
    var wasDisabled = button.disabled;
    button.disabled = true;
    var done = function () { button.removeAttribute("aria-busy"); button.disabled = wasDisabled; };
    var p;
    try { p = typeof work === "function" ? work() : work; } catch (e) { done(); throw e; }
    return Promise.resolve(p).then(function (v) { done(); return v; }, function (e) { done(); throw e; });
  }
  // -- the gateway busy ------------------------------------------------------------------------
  var BUSY = "The gateway is busy right now. Nothing was changed; try again.";
  var nativeFetch = window.fetch;
  var held = [];  // the requests waiting for Try again: {again, resolve, reject, first, signal, onAbort}
  var busyNote = null;
  function isBusy(r) {
    return !!r && r.status === 503 && !!r.headers && r.headers.get("X-MTG-Busy") === "1";
  }
  function abortError() {
    try { return new DOMException("The request was aborted.", "AbortError"); } catch (e) {
      var err = new Error("The request was aborted."); err.name = "AbortError"; return err;
    }
  }
  // the gateway's own words for the note (the JSON answer's message), or the one wording here
  function busyText(r) {
    return r.clone().json().then(function (d) {
      return (d && typeof d.message === "string" && d.message) || BUSY;
    }, function () { return BUSY; });
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }
  function showBusy(text) {
    if (!busyNote) {
      busyNote = el("div", "toast cards-toast error busy-toast");
      busyNote.setAttribute("role", "alert");
      busyNote.appendChild(el("span", "msg", text));
      var again = el("button", "btn", "Try again");
      again.type = "button";
      again.addEventListener("click", retryHeld);
      busyNote.appendChild(again);
      var x = el("button", "close icon-only", "×");
      x.type = "button";
      x.setAttribute("aria-label", "Dismiss");
      x.addEventListener("click", dismissHeld);
      busyNote.appendChild(x);
      (document.body || document.documentElement).appendChild(busyNote);
    } else {
      busyNote.querySelector(".msg").textContent = text;
    }
  }
  function hideBusy() {
    if (busyNote) busyNote.remove();
    busyNote = null;
  }
  function release(entry) {
    var i = held.indexOf(entry);
    if (i >= 0) held.splice(i, 1);
    if (entry.signal && entry.onAbort) entry.signal.removeEventListener("abort", entry.onAbort);
    if (!held.length) hideBusy();
  }
  function park(entry, r) {
    entry.first = r;
    held.push(entry);
    busyText(r).then(function (text) { if (held.indexOf(entry) >= 0) showBusy(text); });
  }
  function resend(entry) {
    var p;
    try { p = entry.again(); } catch (e) { release(entry); entry.reject(e); return; }
    Promise.resolve(p).then(function (r) {
      if (isBusy(r)) { park(entry, r); return; }
      release(entry);
      entry.resolve(r);
    }, function (err) { release(entry); entry.reject(err); });
  }
  function retryHeld() {
    hideBusy();
    var waiting = held.slice();
    held = [];
    waiting.forEach(resend);
  }
  function dismissHeld() {
    var waiting = held.slice();
    held = [];
    hideBusy();
    waiting.forEach(function (entry) { release(entry); entry.resolve(entry.first); });
  }
  function hold(again, signal, r) {
    return new Promise(function (resolve, reject) {
      var entry = { again: again, resolve: resolve, reject: reject, first: r, signal: signal || null, onAbort: null };
      if (signal) {
        if (signal.aborted) { reject(abortError()); return; }
        entry.onAbort = function () { release(entry); reject(abortError()); };
        signal.addEventListener("abort", entry.onAbort);
      }
      park(entry, r);
    });
  }
  // -- signed out or no connection, for a script's own fetch ------------------------------------
  /* A script that sends a plain fetch (not through MtgFetch) and reads only "did it work" used to
     say its own "Network error" with no way back in. Every same-origin request a page's script
     sends is watched here instead: a signed-out answer (a 401, a redirect not followed, or one
     followed to the sign-in page) puts up one note, "You're signed out, so nothing was changed."
     with Sign in back to this page; a change that got no answer at all puts up "No connection;
     nothing was changed." A request that failed for want of an answer is told apart from a
     signed-out one that the browser could not follow to the sign-in provider (another site) by
     one small look at the gateway's /api/v1/me. The script's own promise is answered as before.
     MtgFetch's own requests (marked mtgShared) are left to their caller, which says the same. */
  var sessionNote = null;
  var offlineNote = null;
  var probing = null;
  function sameOrigin(input) {
    try {
      var u = new URL(typeof input === "string" ? input : (input && input.url) || "", location.href);
      return u.origin === location.origin;
    } catch (e) { return false; }
  }
  function methodOf(input, init) {
    var m = (init && init.method) || (input && typeof input === "object" && input.method) || "GET";
    return String(m).toUpperCase();
  }
  function landedOnSignIn(r) {
    if (!r || !r.redirected || !r.url) return false;
    try {
      var path = new URL(r.url).pathname;
      return path === "/login" || path === "/signed-out" || path === "/logout";
    } catch (e) { return false; }
  }
  function note(cls, text, actions, onClose) {
    var n = el("div", "toast cards-toast error " + cls);
    n.setAttribute("role", "alert");
    n.appendChild(el("span", "msg", text));
    actions.forEach(function (a) { n.appendChild(a); });
    var x = el("button", "close icon-only", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss");
    x.addEventListener("click", onClose);
    n.appendChild(x);
    (document.body || document.documentElement).appendChild(n);
    return n;
  }
  function hideOffline() {
    if (offlineNote) offlineNote.remove();
    offlineNote = null;
  }
  function showSignedOut() {
    hideOffline();
    if (sessionNote) return;
    sessionNote = note("session-toast", SIGNED_OUT, [signInLink("btn btn-primary")], function () {
      if (sessionNote) sessionNote.remove();
      sessionNote = null;
    });
  }
  function showOffline() {
    if (sessionNote || offlineNote) return;
    offlineNote = note("offline-toast", NO_CONNECTION, [], hideOffline);
  }
  window.addEventListener("online", hideOffline);
  // a change that got no answer: signed out (the sign-in provider's page could not be read) or
  // no connection, by one look at the gateway; a read that got none says nothing (a poll, a
  // lookup: its own script stays quiet or says so)
  function noAnswer(write) {
    if (!probing) {
      probing = nativeFetch.call(window, "/api/v1/me", {
        credentials: "same-origin", redirect: "manual", cache: "no-store", headers: { Accept: "application/json" }
      }).then(function (r) { return isSignedOut(r) ? "signed_out" : "answered"; }, function () { return "offline"; });
      probing.then(function () { probing = null; }, function () { probing = null; });
    }
    probing.then(function (seen) {
      if (seen === "signed_out") showSignedOut();
      else if (seen === "offline" && write) showOffline();
    });
  }
  function watch(input, init, p) {
    if ((init && init.mtgShared) || !sameOrigin(input)) return p;
    var write = ["GET", "HEAD"].indexOf(methodOf(input, init)) < 0;
    return p.then(function (r) {
      if (isSignedOut(r) || landedOnSignIn(r)) showSignedOut();
      else if (r && r.status < 500) hideOffline();
      return r;
    }, function (err) {
      if (!(err && err.name === "AbortError")) noAnswer(write);
      throw err;
    });
  }
  function busyFetch(input, init) {
    var again = function () { return nativeFetch.call(window, input, init); };
    var signal = init && init.signal;
    return watch(input, init, again().then(function (r) { return isBusy(r) ? hold(again, signal, r) : r; }));
  }
  if (typeof nativeFetch === "function") window.fetch = busyFetch;

  window.MtgFetch = {
    json: json, answer: answer, problem: problem, signedOut: signedOut, signInLink: signInLink, signInHref: signInHref,
    isSignedOut: isSignedOut, signedOutError: signedOutError, noConnectionError: noConnectionError,
    NO_CONNECTION: NO_CONNECTION, SIGNED_OUT: SIGNED_OUT, UNREADABLE: UNREADABLE, BUSY: BUSY,
    press: press, isBusy: isBusy, retryBusy: retryHeld, dismissBusy: dismissHeld
  };
})();
