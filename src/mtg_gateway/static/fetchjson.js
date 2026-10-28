/* Reading the gateway's JSON from a page's script (static/bell.js), with a session that has ended
   told apart from a lost connection. Such a request is answered with a 401 or a redirect to the
   sign-in page (on the sign-in provider in production, which a script cannot follow), so it is
   not followed: either answer rejects with err.signedOut, and the script says "You're signed out"
   with a Sign in button (MtgFetch.signedOut). Only a request that got no answer at all is
   "No connection". Loaded on every signed-in page, before the scripts that use it. */
(function () {
  "use strict";
  function json(href, opts) {
    var o = opts || {};
    var headers = { Accept: "application/json" };
    if (o.headers) Object.keys(o.headers).forEach(function (k) { headers[k] = o.headers[k]; });
    return fetch(href, {
      method: o.method || "GET",
      body: o.body,
      headers: headers,
      credentials: "same-origin",
      // a JSON address answers a signed-out request with the sign-in redirect: never followed
      redirect: "manual"
    }).then(function (r) {
      if (r.type === "opaqueredirect" || r.status === 401) {
        var out = new Error("signed out");
        out.signedOut = true;
        throw out;
      }
      return r.json();
    });
  }
  // "You're signed out" and Sign in (the box's one orange button), which comes back to this page;
  // the app opens its own sign-in from the same address
  function signedOut(what) {
    var wrap = document.createElement("div");
    var p = document.createElement("p");
    p.className = "notice error";
    p.setAttribute("role", "alert");
    p.textContent = "You're signed out, so " + (what || "this") + " could not be read.";
    var a = document.createElement("a");
    a.className = "btn btn-primary";
    a.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
    a.textContent = "Sign in";
    wrap.appendChild(p);
    wrap.appendChild(a);
    return wrap;
  }
  window.MtgFetch = { json: json, signedOut: signedOut };
})();
