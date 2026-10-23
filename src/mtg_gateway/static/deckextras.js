/* The deck page's release-2 boxes (deck_extras.py): Request deck help, Report deck and Report card,
   the Registration PDF, Add a package, Lock personal categories and the contest entry. The deck
   menu and the card menu (static/deck.js) open them through window.MtgDeckExtras.open(id, opts);
   this file fills a box from the card it was opened for, counts words and characters, reads the
   lists a box needs when it opens (your packages, your templates), checks the form before it
   goes, and sends the two forms that answer in place (the PDF, the lock) without leaving the page.
   Everything works without this file: the boxes are plain forms at the old addresses.
   Needs static/box.js (window.MtgBox) and static/cardmenu.js (window.MtgMenu). */
(function () {
  "use strict";
  var Menu = window.MtgMenu, Box = window.MtgBox;
  var banner = document.querySelector(".banner[data-deck-id]");
  if (!Menu || !Box || !banner) return;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var el = Menu.el, toast = Menu.toast;
  var NO_CONNECTION = "No connection; nothing was changed.";

  function words(text) { var m = (text || "").match(/\S+/g); return m ? m.length : 0; }
  function countWords(ta) {
    var id = ta.getAttribute("data-words"), c = id ? document.getElementById(id) : null;
    if (!c) return;
    var n = words(ta.value);
    c.textContent = n + " words (10 to 200)";
    c.className = "small " + (n && (n < 10 || n > 200) ? "dx-bad" : "muted");
  }
  function countChars(ta) {
    var id = ta.getAttribute("data-count"), c = id ? document.getElementById(id) : null;
    if (c) c.textContent = ta.value.length + " / " + (ta.getAttribute("maxlength") || "");
  }
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (!t || !t.closest || !t.closest(".dx-box")) return;
    if (t.hasAttribute("data-words")) countWords(t);
    if (t.hasAttribute("data-count")) countChars(t);
  });
  function note(box, text) {
    var b = Box.current();
    if (b && b.panel === box) b.note(text, true);
  }
  function clearNote(box) {
    var n = $(".boxnote", box);
    if (n) { n.hidden = true; n.textContent = ""; }
  }
  function fill(text, name) { return (text || "").replace("{name}", name); }
  function retitle(box, name) {
    var t = box.getAttribute("data-t"), h = $(".bh h2", box);
    if (t && h) h.textContent = fill(t, name);
  }
  function setValue(box, name, value) {
    var i = $("[name='" + name + "']", box);
    if (i) i.value = value == null ? "" : String(value);
  }
  function busy(box, on) {
    $$(".acts button[type=submit]", box).forEach(function (b) {
      b.disabled = !!on || b.hasAttribute("data-gate-off");
      if (on) b.setAttribute("aria-busy", "true"); else b.removeAttribute("aria-busy");
    });
  }
  function postForm(form) {
    var body = new URLSearchParams(new FormData(form));
    return fetch(form.getAttribute("action"), {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: body.toString()
    }).then(function (r) {
      return r.json().then(function (d) { d.status = r.status; return d; }, function () {
        return { ok: false, status: r.status, message: "The gateway sent an answer this page could not read. Reload the page to see whether the change was made." };
      });
    });
  }

  // -- what each box does when it opens ------------------------------------------------------------
  var openers = {
    help: function (box) { $$("[data-words]", box).forEach(countWords); },
    report: function (box) {},
    reportcard: function (box, o) {
      retitle(box, o.card || "");
      setValue(box, "card", o.card || "");
    },
    pdf: function (box) {
      var done = $(".dx-pdf-done", box);
      if (done) { done.hidden = true; done.textContent = ""; }
    },
    contest: function (box) { $$("[data-count]", box).forEach(countChars); },
    withdraw: function (box) {},
    package: function (box) {
      var wrap = $(".dx-mine", box), tree = wrap && $(".tree", wrap);
      if (!tree || wrap.__loaded) return;
      wrap.__loaded = true;
      tree.textContent = "";
      var p = el("p", "muted small loading", "Reading your packages from Archidekt…");
      p.setAttribute("role", "status");
      tree.appendChild(p);
      Menu.getJSON(wrap.getAttribute("data-src")).then(function (d) {
        tree.textContent = "";
        if (!d.ok) { wrap.__loaded = false; tree.appendChild(el("p", "muted small", d.message || "Your packages could not be read. Paste a package link or id instead.")); return; }
        if (!d.packages.length) { tree.appendChild(el("p", "muted small", "You have no packages yet. Paste a package link or id, or make one on My decks.")); return; }
        d.packages.forEach(function (pk) {
          var lab = el("label");
          var r = el("input");
          r.type = "radio"; r.name = "mine"; r.value = pk.id;
          r.setAttribute("aria-label", pk.name);
          r.addEventListener("change", function () { setValue(box, "package", pk.id); });
          lab.appendChild(r);
          lab.appendChild(Menu.icon("package"));
          var t = el("span");
          t.appendChild(el("b", null, pk.name));
          t.appendChild(el("span", "s", pk.line));
          lab.appendChild(t);
          tree.appendChild(lab);
        });
      }, function () { wrap.__loaded = false; tree.textContent = ""; tree.appendChild(el("p", "muted small", "No connection; your packages could not be read. Paste a package link or id instead.")); });
    },
    lock: function (box, o) {
      var name = o.card || "", cats = o.cats || [];
      retitle(box, name);
      setValue(box, "card", name);
      var sentence = $("[data-sentence]", box), left = $("[data-left]", box), ok = $$(".acts button[type=submit]", box)[0];
      sentence.textContent = "Checking which of its categories are templates…";
      sentence.className = "muted loading";
      left.hidden = true;
      if (ok) { ok.disabled = true; ok.setAttribute("data-gate-off", ""); }
      Menu.getJSON("/decks/api/templates").then(function (d) {
        sentence.className = "";
        if (!d.ok) { sentence.textContent = d.message || "Your category templates could not be read."; return; }
        var names = (d.templates || []).map(function (t) { return t.name; });
        var kept = cats.filter(function (c) { return names.indexOf(c) >= 0; });
        var out = cats.filter(function (c) { return names.indexOf(c) < 0; });
        sentence.textContent = "";
        if (!kept.length) {
          sentence.appendChild(document.createTextNode("None of this card's categories (" + (cats.join(", ") || "none") + ") is one of your category templates, so there is nothing to lock. Make a template first on "));
          var a = el("a", null, "My lists"); a.href = "/lists/templates";
          sentence.appendChild(a);
          sentence.appendChild(document.createTextNode("."));
          return;
        }
        sentence.appendChild(document.createTextNode("From now on, when you add “" + name + "” to any deck, it goes into "));
        sentence.appendChild(el("b", null, kept.join(", ")));
        sentence.appendChild(document.createTextNode(" (its categories in this deck). Saved to My lists › Default categories."));
        if (out.length) { left.hidden = false; left.textContent = "Left out, not a template: " + out.join(", ") + "."; }
        if (ok) { ok.removeAttribute("data-gate-off"); ok.disabled = false; }
      }, function () { sentence.className = ""; sentence.textContent = "No connection; your category templates could not be read."; });
    }
  };
  function open(id, o) {
    o = o || {};
    var box = document.getElementById(id);
    if (!box) return null;
    clearNote(box);
    if (openers[id]) openers[id](box, o);
    return Box.openElement(box, { returnFocus: o.returnFocus || null });
  }
  window.MtgDeckExtras = { open: open };
  // the strip's Edit or delist and the old addresses open the boxes through box.js (data-box-open);
  // those get their counters too
  document.addEventListener("click", function (e) {
    var opener = e.target && e.target.closest ? e.target.closest("[data-box-open]") : null;
    if (!opener || e.defaultPrevented) return;
    var box = document.getElementById(opener.getAttribute("data-box-open"));
    if (box && box.classList.contains("dx-box") && openers[box.id] && box.id !== "reportcard" && box.id !== "lock") {
      clearNote(box);
      openers[box.id](box, {});
    }
  }, true);
  var addressed = $(".dx-box[data-open]");
  if (addressed && openers[addressed.id] && addressed.id !== "lock" && addressed.id !== "reportcard") openers[addressed.id](addressed, {});

  // -- the checks before a form goes, and the two forms answered in place ------------------------------
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f || !f.closest || !f.closest(".dx-box")) return;
    var box = f.closest(".dx-box");
    var bad = "";
    if (f.id === "dx-help-f") {
      if ($$("[data-words]", f).some(function (t) { var n = words(t.value); return n < 10 || n > 200; })) bad = "Each box needs 10 to 200 words.";
    } else if (f.id === "report-f" || f.id === "reportcard-f") {
      if (!$("[name=message]", f).value.trim()) bad = "Say what is wrong first.";
    } else if (f.id === "dx-pkg-f") {
      if (!$("[name=package]", f).value.trim()) bad = "Pick one of your packages, or paste a package link or id.";
    } else if (f.id === "dx-contest-f") {
      if (!$("[name=contest]", f).value.trim()) bad = "Give the contest's id or its page address.";
      else if (!$("[name=summary]", f).value.trim()) bad = "A summary is required.";
    } else if (f.id === "dx-withdraw-f") {
      if (!$("[name=contest]", f).value.trim()) bad = "Give the contest's id or its page address.";
    }
    if (bad) {
      e.preventDefault();
      e.stopImmediatePropagation();
      note(box, bad);
      var first = $("textarea, input[type=text]", f);
      if (first) first.focus();
      return;
    }
    if (!f.hasAttribute("data-fetches")) { busy(box, true); return; }
    e.preventDefault();
    e.stopImmediatePropagation();
    clearNote(box);
    busy(box, true);
    postForm(f).then(function (d) {
      busy(box, false);
      if (!d.ok) { note(box, d.message || "Archidekt refused it."); return; }
      if (f.id === "dx-pdf-f") {
        var done = $(".dx-pdf-done", box);
        done.textContent = "Archidekt made the PDF. ";
        var a = el("a", null, "");
        a.href = d.pdf_url; a.target = "_blank"; a.rel = "noreferrer noopener";
        a.appendChild(Menu.icon("external"));
        a.appendChild(document.createTextNode(" Open the PDF"));
        done.appendChild(a);
        done.hidden = false;
        a.focus();
        return;
      }
      if (f.id === "dx-lock-f") {
        var b = Box.current();
        if (b) b.close();
        if (d.verified === false) toast(d.notice || "Sent to Archidekt, but the read-back didn't confirm every change.", { error: true });
        else toast(d.notice || "Default categories saved on Archidekt and read back.", { ok: true, ms: 5000 });
      }
    }, function () { busy(box, false); note(box, NO_CONNECTION); });
  }, true);
})();
