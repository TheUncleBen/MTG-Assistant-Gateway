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
   button for any of those answers or errors, for a toast, a strip or a note. */
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
  window.MtgFetch = {
    json: json, answer: answer, problem: problem, signedOut: signedOut, signInLink: signInLink, signInHref: signInHref,
    isSignedOut: isSignedOut, signedOutError: signedOutError, noConnectionError: noConnectionError,
    NO_CONNECTION: NO_CONNECTION, SIGNED_OUT: SIGNED_OUT, UNREADABLE: UNREADABLE
  };
})();
