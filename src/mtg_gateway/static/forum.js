/* The Forum pages (forum_page.py): Load more on a section's thread table (a button that appends
   the next page's rows; without scripts the same link opens the next page), and the thread rows'
   menu by right-click, press and hold, or the ContextMenu key (R-228: static/cardmenu.js).
   The thread page's replies are the deck page's comment panel (static/social.js); this file adds
   the first post's ⋮ menu (the same ways in) and, on a contest thread, the Enter a deck… box
   (the member's decks are read as it opens, the summary's counter and checks) and Withdraw…. */
(function () {
  "use strict";
  var Menu = window.MtgMenu;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  var toast = Menu ? Menu.toast : function (t) { window.alert(t); };

  // -- Load more ---------------------------------------------------------------------------------
  function loadRows(a) {
    var table = $(".fm .fm-threads"), wrap = a.closest(".loadmore");
    if (!table || !Menu) return;
    a.setAttribute("aria-busy", "true");
    a.setAttribute("aria-disabled", "true");
    Menu.getJSON(a.getAttribute("href")).then(function (d) {
      if (!d.ok) throw new Error(d.message || "");
      var tmp = el("div"), first = null;
      tmp.innerHTML = d.html;
      while (tmp.firstChild) {
        if (!first && tmp.firstChild.nodeType === 1) first = tmp.firstChild;
        table.appendChild(tmp.firstChild);
      }
      wrap.textContent = "";
      if (d.has_more) {
        var next = el("a", "btn btn-plain", d.label);
        next.href = d.next; next.rel = "next"; next.setAttribute("data-load-more", "");
        wrap.appendChild(next);
      } else {
        wrap.appendChild(el("p", "muted", d.done));
      }
      if (first && first.focus) first.focus({ preventScroll: true });
    }).catch(function () {
      a.removeAttribute("aria-busy");
      a.removeAttribute("aria-disabled");
      toast("No connection; the threads could not be read. Try again.", { error: true });
    });
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".fm .loadmore [data-load-more]") : null;
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    if (a.getAttribute("aria-disabled") === "true") return;
    loadRows(a);
  });

  // -- the thread rows' menu -----------------------------------------------------------------------
  var table = $(".fm .fm-threads");
  if (table && Menu) {
    var rowOf = function (t) { return t && t.closest ? t.closest("a.tr[data-id]") : null; };
    Menu.bind(table, {
      itemOf: rowOf,
      skip: "button, input, select, textarea, summary, form, label",
      open: function (row, x, y) {
        var title = ($(".t", row) || row).textContent.trim();
        var href = row.getAttribute("href"), id = row.getAttribute("data-id");
        var items = [
          Menu.link("Open thread", "comment", href),
          Menu.link("Open in a new tab", "external", href, { ext: true }),
          Menu.item("Copy link", "link", function () {
            Menu.copyAndSay(location.origin + href, "Copied the thread's link.");
          }),
          Menu.link("Open on Archidekt", "external", "https://archidekt.com/forum/thread/" + id, { ext: true })
        ];
        Menu.open({ head: title, label: "Menu for " + title, items: items, focusBack: row }, x, y);
      }
    });
  }

  // -- the first post's menu ----------------------------------------------------------------------
  // Reply and Copy link as a reply's menu; Report comment… on someone else's post (the thread's
  // root is reported as a comment); Open on Archidekt. Archidekt's comment calls do not reach the
  // root post, so there is no Vote up, Edit or Delete here: those live on Archidekt.
  var post = $(".fm-post[data-id]");
  var Comments = window.MtgComments;
  if (post && Menu) {
    var postMenu = function (it, x, y) {
      var tid = post.getAttribute("data-id"), who = post.getAttribute("data-owner") || "someone";
      var own = post.hasAttribute("data-own");
      var items = [];
      if (Comments && $(".comments form.newcomment")) {
        items.push(Menu.item("Reply", "comment", function () { Comments.replyTo(null); }));
      }
      items.push(Menu.item("Copy link", "link", function () {
        Menu.copyAndSay(location.origin + location.pathname + "#post-" + tid, "Copied the post's link.");
      }));
      items.push(Menu.link("Open on Archidekt", "external", "https://archidekt.com/forum/thread/" + tid, { ext: true }));
      if (!own && Comments && document.getElementById("reportcomment")) {
        items.push(Menu.sep());
        items.push(Menu.item("Report comment…", "flag", function () { Comments.report(tid, who, 1, post); }));
      }
      Menu.open({ head: who + "'s post", label: "Menu for " + who + "'s post", items: items, focusBack: post }, x, y);
    };
    Menu.bind(post, {
      itemOf: function (t) { return t && t.closest && t.closest(".fm-post") === post ? post : null; },
      skip: "button, a, input, select, textarea, form, label",
      button: ".fm-post .ph .kebab",
      open: postMenu
    });
  }

  // -- a contest thread: Enter a deck… and Withdraw… --------------------------------------------
  var Box = window.MtgBox;
  function countChars(ta) {
    var id = ta.getAttribute("data-count"), c = id ? document.getElementById(id) : null;
    if (c) c.textContent = ta.value.length + " / " + (ta.getAttribute("maxlength") || "");
  }
  function noteIn(box, text) {
    var b = Box && Box.current ? Box.current() : null;
    if (b && b.panel === box) b.note(text, true);
  }
  function clearNote(box) {
    var n = $(".boxnote", box);
    if (n) { n.hidden = true; n.textContent = ""; }
  }
  function busy(box, on) {
    Array.prototype.forEach.call(box.querySelectorAll(".acts button[type=submit]"), function (b) {
      b.disabled = !!on;
      if (on) b.setAttribute("aria-busy", "true"); else b.removeAttribute("aria-busy");
    });
  }
  // the member's decks are read when the box opens, once (as My lists' Add to a deck…): the list
  // starts on "Pick a deck…", never on a deck (the entry replaces that deck's description on
  // Archidekt, so no deck may be picked by accident), and leaves card packages out (a package
  // is no contest entry; /api/decks/mine?packages=1 marks them); a pick made before the box was
  // closed is kept
  function fillDecks(box) {
    var sel = $("select[name=deck]", box);
    if (!sel || box.__decks || !Menu) return;
    box.__decks = true;
    var keep = sel.value;
    function show(text) {
      sel.textContent = "";
      if (text) sel.appendChild(el("option", null, text));
      if (window.MtgSelect) window.MtgSelect.refresh(sel);
    }
    show("Reading your decks…");
    sel.disabled = true;
    Menu.getJSON(sel.getAttribute("data-src")).then(function (r) {
      var rows = (r.decks || []).filter(function (dk) { return !dk.package && !dk.cardPackage; })
        .sort(function (a, b) { return (a.name || "").localeCompare(b.name || ""); });
      if (!rows.length) { box.__decks = false; show(r.message || "No decks to enter."); return; }
      show("");
      var pick = el("option", null, "Pick a deck…");
      pick.value = ""; pick.disabled = true; pick.selected = true; pick.hidden = true;
      sel.appendChild(pick);
      rows.forEach(function (dk) { var o = el("option", null, dk.name); o.value = dk.id; sel.appendChild(o); });
      if (keep && rows.some(function (dk) { return String(dk.id) === keep; })) sel.value = keep;
      sel.disabled = false;
      if (window.MtgSelect) window.MtgSelect.refresh(sel);
    }, function () { box.__decks = false; show("No connection; your decks could not be read."); });
  }
  function prepare(box) {
    clearNote(box);
    if (box.id === "enter") {
      fillDecks(box);
      Array.prototype.forEach.call(box.querySelectorAll("[data-count]"), countChars);
    }
  }
  document.addEventListener("click", function (e) {
    var opener = e.target && e.target.closest ? e.target.closest("#fm-contest [data-box-open]") : null;
    if (!opener || e.defaultPrevented) return;
    var box = document.getElementById(opener.getAttribute("data-box-open"));
    if (box) prepare(box);
  }, true);
  var drawnOpen = $("#enter[data-open], #withdraw[data-open]");
  if (drawnOpen) prepare(drawnOpen);
  document.addEventListener("input", function (e) {
    var t = e.target;
    if (t && t.hasAttribute && t.hasAttribute("data-count") && t.closest("#enter")) countChars(t);
  });
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f || f.id !== "fm-enter-f" && f.id !== "fm-withdraw-f") return;
    var box = f.closest(".ui-box");
    var bad = "";
    if (f.id === "fm-enter-f") {
      var sel = $("select[name=deck]", f), ta = $("textarea[name=summary]", f);
      if (!sel || sel.disabled || !/^\d+$/.test(sel.value || "")) bad = "Pick a deck first.";
      else if (!ta.value.trim()) bad = "A summary is required.";
    }
    if (bad) {
      e.preventDefault();
      e.stopImmediatePropagation();
      noteIn(box, bad);
      var first = bad === "A summary is required." ? $("textarea", f) : $("select", f);
      if (first) first.focus();
      return;
    }
    if (box) busy(box, true);
  }, true);
})();
