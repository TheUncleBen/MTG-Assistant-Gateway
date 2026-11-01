/* Nothing typed is lost (0.7.46 audit QG-01 to QG-04; QOL-03): a form or a box marked
   data-draft="key" keeps what is typed in it as a draft in this browser's storage, per member,
   and puts it back when the form is next shown empty: after a reload, Back, a page left by
   mistake, a sign-in that ended while the form was filled in (the POST is dropped on the way
   to the sign-in page; the page comes back with ?draft=1 and the draft fills the form again),
   or a submit the busy gateway turned away (its page's Go back, static/busy.js).

   What is kept: every named text field, textarea, select, checkbox and radio in the form (never
   a password or a file). Saved as it is typed and put back over what the page drew: a draft
   exists only when the form was left without its submit, so what was typed wins. A submit that leaves the
   page marks the draft sent: it is dropped the next time the form is shown, unless the address
   says ?draft=1 (the sign-in came back). A script that posts without leaving the page clears the
   draft itself when the post went through (MtgDraft.clear(key)). Drafts older than 14 days are
   dropped. Loaded on every signed-in page before the page scripts (theme.py); a form added to
   the page later is kept with MtgDraft.keep(form, key).

   The key is the member's: data-who in the top bar (a hash of the member's id, theme.py), so
   the next member on a shared browser never sees another's draft. */
(function () {
  "use strict";
  var DAYS = 14 * 24 * 3600 * 1000;
  function who() {
    var w = document.querySelector("header.topbar [data-who]");
    return (w && w.getAttribute("data-who")) || "";
  }
  function keyOf(key) { return "mtg_draft:" + who() + ":" + key; }
  function read(key) {
    try {
      var raw = localStorage.getItem(keyOf(key));
      if (!raw) return null;
      var d = JSON.parse(raw);
      if (!d || typeof d !== "object" || !d.at || Date.now() - d.at > DAYS) { drop(key); return null; }
      return d;
    } catch (e) { return null; }
  }
  function write(key, d) {
    try { localStorage.setItem(keyOf(key), JSON.stringify(d)); } catch (e) { /* storage off or full */ }
  }
  function drop(key) {
    try { localStorage.removeItem(keyOf(key)); } catch (e) { /* storage off */ }
  }
  function fields(root) {
    var out = [];
    var all = root.querySelectorAll("input[name], textarea[name], select[name]");
    for (var i = 0; i < all.length; i++) {
      var f = all[i], t = (f.type || "").toLowerCase();
      if (t === "password" || t === "file" || t === "hidden" || t === "submit" || t === "button") continue;
      if (f.name === "csrf") continue;
      out.push(f);
    }
    return out;
  }
  function values(root) {
    var v = {};
    fields(root).forEach(function (f) {
      var t = (f.type || "").toLowerCase();
      if (t === "checkbox") v[f.name] = f.checked ? "1" : "";
      else if (t === "radio") { if (f.checked) v[f.name] = f.value; }
      else v[f.name] = f.value;
    });
    return v;
  }
  function hasText(v) {
    return Object.keys(v).some(function (k) { return typeof v[k] === "string" && v[k].trim() && !/^(0|1)$/.test(v[k]) && v[k].length > 1; });
  }
  function restore(root, v) {
    var touched = false;
    fields(root).forEach(function (f) {
      if (!(f.name in v)) return;
      var t = (f.type || "").toLowerCase();
      if (t === "checkbox") { if (f.checked !== (v[f.name] === "1")) { f.checked = v[f.name] === "1"; touched = true; } }
      else if (t === "radio") { if (f.value === v[f.name] && !f.checked) { f.checked = true; touched = true; } }
      else if (f.tagName === "SELECT") {
        if (f.value !== v[f.name] && Array.prototype.some.call(f.options, function (o) { return o.value === v[f.name]; })) {
          f.value = v[f.name]; touched = true;
          if (window.MtgSelect && window.MtgSelect.refresh) window.MtgSelect.refresh(f);
        }
      } else if (f.value !== v[f.name]) { f.value = v[f.name]; touched = true; }
    });
    if (touched) root.dispatchEvent(new Event("input", { bubbles: true }));
    return touched;
  }
  // The gateway's busy page sent the member back to this form (static/busy.js): their submit
  // did not go through, so a draft marked sent is put back as after a sign-in. The note is
  // this tab's and is used once; a page the browser restored from its cache keeps its fields
  // itself and the note is dropped unused (pageshow).
  var BACK = "mtg_draft_back";
  function sentBack() {
    var at = 0;
    try { at = Number(sessionStorage.getItem(BACK)) || 0; sessionStorage.removeItem(BACK); } catch (e) { at = 0; }
    return at > 0 && Date.now() - at < 2 * 60 * 1000;
  }
  var cameBack = /[?&]draft=1(&|$)/.test(location.search) || sentBack();
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) { try { sessionStorage.removeItem(BACK); } catch (err) { /* storage off */ } }
  });
  /* keep: the form (or any element holding fields) under ``key``; returns {key, restored, clear}.
     Called again with another key (a box reused for another card), the form is kept under the
     new key from then on, with that key's draft put back. */
  function keep(root, key) {
    if (!root || !key) return null;
    var h = root.__draft;
    if (h && h.key === key) return h;
    var d = read(key), restored = false;
    if (d) {
      if (d.sent && !cameBack) drop(key);
      else restored = restore(root, d.v);
    }
    if (h) { h.key = key; h.restored = restored; return h; }
    h = { key: key, restored: restored, clear: function () { drop(h.key); } };
    var save = function () {
      var v = values(root);
      if (hasText(v)) write(h.key, { at: Date.now(), v: v });
      else drop(h.key);
    };
    root.addEventListener("input", save);
    root.addEventListener("change", save);
    // a submit that leaves the page: the draft is marked sent, and goes the next time the form
    // shows (the POST went through, or the server drew the form again with its values), unless
    // the page comes back from a sign-in with ?draft=1. One the page's script handled itself
    // (preventDefault) stays as it is: that script clears it when the post went through.
    if (root.tagName === "FORM") root.addEventListener("submit", function (e) {
      setTimeout(function () {
        if (e.defaultPrevented) return;
        var cur = read(h.key);
        if (cur) { cur.sent = Date.now(); write(h.key, cur); }
      }, 0);
    });
    root.__draft = h;
    return h;
  }
  function all() {
    var marked = document.querySelectorAll("[data-draft]");
    for (var i = 0; i < marked.length; i++) keep(marked[i], marked[i].getAttribute("data-draft"));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", all); else all();
  window.MtgDraft = { keep: keep, clear: drop, read: read, cameBack: cameBack };
})();
