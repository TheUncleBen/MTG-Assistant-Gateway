/* The bell and the Alerts page (alerts_page.py, theme.py). The count on the bell and on Alerts
   on the rail (the member's alerts since they last opened the list) is asked once the page has
   loaded, never during the page's own render, and asked again when the tab comes back into view
   and after every action here, so a reload, another tab or another device never leaves an old
   count. Pressing the bell opens the newest alerts in a box (Open all alerts, Settings); without
   this file the bell is a link to /notifications.

   Each kept alert row (on the page and in the box) has a menu from its ⋮, a right-click, a press
   and hold or Shift+F10: Open, Mark read or Mark unread, Dismiss. Mark all read and Clear all act
   in place; Undo in the toast takes them back. Every action is kept on the server
   (/notifications/act), which also takes the alert's message off the member's phones.

   A notice's × (form[data-dismiss]: the assistant notice on Home, the update strip) is sent in
   place and the notice goes. Needs static/box.js (window.MtgBox), static/cardmenu.js
   (window.MtgMenu) and static/fetchjson.js (window.MtgFetch). */
(function () {
  "use strict";
  if (!window.fetch) return;
  var bells = Array.prototype.slice.call(document.querySelectorAll("[data-bell]"));
  var Menu = window.MtgMenu;
  var count = null;
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  // -- the count ---------------------------------------------------------------------------------
  function setCount(n) {
    count = n;
    bells.forEach(function (bell) {
      var badge = bell.querySelector(".n");
      if (badge) {
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.hidden = !(n > 0);
      }
      var name = bell.getAttribute("data-bell-label") || "Alerts";
      bell.setAttribute("aria-label", n > 0 ? name + ", " + n + " new" : name);
      bell.title = bell.getAttribute("aria-label");
    });
    // Home's Needs you row (home.py): shown with the count once it is above zero
    var row = document.querySelector("[data-bell-row]"), num = document.querySelector("[data-bell-n]");
    var word = document.querySelector("[data-bell-word]");
    if (num) num.textContent = String(n);
    if (word) word.textContent = n === 1 ? "alert" : "alerts";
    if (row) row.hidden = !(n > 0);
    var section = document.querySelector("[data-bell-section]");
    if (section) section.hidden = !(n > 0);
  }
  function getJSON(href) {
    if (window.MtgFetch) return window.MtgFetch.json(href);
    return fetch(href, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); });
  }
  // Kept in this tab for a short while (sessionStorage), so moving between pages does not ask
  // again at once; any action here, and the tab coming back into view, asks again. Sign-out
  // clears it with the site's storage.
  var COUNT_KEY = "mtg-bell", KEEP_MS = 30000;
  function keep(n) {
    try { sessionStorage.setItem(COUNT_KEY, JSON.stringify({ at: Date.now(), n: n })); } catch (e) { /* storage off */ }
  }
  function forget() {
    try { sessionStorage.removeItem(COUNT_KEY); } catch (e) { /* storage off */ }
  }
  function ask() {
    getJSON("/notifications/count").then(function (d) {
      if (d && d.ok) { setCount(d.unseen || 0); keep(d.unseen || 0); }
    }, function () { /* signed out or no connection: the bell stays quiet */ });
  }
  function readCount() {
    try {
      var kept = JSON.parse(sessionStorage.getItem(COUNT_KEY) || "null");
      if (kept && Date.now() - kept.at < KEEP_MS && typeof kept.n === "number") { setCount(kept.n); return; }
    } catch (e) { /* storage off: ask */ }
    ask();
  }
  function later(fn) {
    if (window.requestIdleCallback) window.requestIdleCallback(fn, { timeout: 2000 });
    else setTimeout(fn, 50);
  }
  if (bells.length || document.querySelector("[data-bell-row]")) {
    if (document.readyState === "complete") later(readCount);
    else window.addEventListener("load", function () { later(readCount); });
    // back from another tab, app or device: what was read or cleared there shows here
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") { forget(); ask(); }
    });
    window.addEventListener("pageshow", function (e) { if (e.persisted) { forget(); ask(); } });
  }

  // -- acting on alerts --------------------------------------------------------------------------
  function post(body) {
    if (Menu && Menu.post) return Menu.post("/notifications/act", body);
    return Promise.resolve({ ok: false });
  }
  function failed(d) {
    if (Menu && Menu.fail) Menu.fail(d, "No connection; nothing was changed.");
  }
  function lists() { return $$(".al-list"); }
  function emptyIfNone() {
    lists().forEach(function (ul) {
      if (ul.querySelector(".al-row")) return;
      var div = el("div", "al-empty");
      div.appendChild(el("p", null, "")).appendChild(el("b", null, "You're all caught up."));
      ul.parentNode.replaceChild(div, ul);
    });
  }
  function markRow(row, read) {
    row.classList.toggle("unread", !read);
    row.setAttribute("data-read", read ? "1" : "0");
    var dot = row.querySelector(".al-dot");
    if (read && dot) dot.remove();
    if (!read && !dot) row.querySelector(".al-main").appendChild(el("span", "al-dot"));
    var sr = row.querySelector(".al-meta .sr-only");
    if (sr) sr.textContent = read ? " · Read" : " · Unread";
    // the no-script menu's first form says what it does now
    var f = row.querySelector(".al-menu form");
    if (f) {
      var a = f.querySelector("input[name=action]");
      if (a) a.value = read ? "unread" : "read";
    }
  }
  function rowsById(ids) {
    var want = {};
    ids.forEach(function (i) { want[String(i)] = true; });
    return $$(".al-row[data-alert]").filter(function (r) { return want[r.getAttribute("data-alert")]; });
  }
  function settle(d) {
    forget();
    if (d && typeof d.unseen === "number") { setCount(d.unseen); keep(d.unseen); }
    var all = $$("[data-al-all]");
    all.forEach(function (b) {
      var what = b.getAttribute("data-al-all");
      if (what === "read_all") b.disabled = !document.querySelector(".al-row.unread[data-alert]");
      if (what === "clear_all") b.disabled = !document.querySelector(".al-row[data-alert]");
    });
  }
  function undoButton(ids, action) {
    var b = el("button", "btn", "Undo");
    b.type = "button";
    b.addEventListener("click", function () {
      if (Menu.dismissToast) Menu.dismissToast();
      post({ action: action, ids: ids }).then(function (d) {
        if (!d || !d.ok) { failed(d); return; }
        location.reload();  // the rows come back where they were
      });
    });
    return b;
  }
  function act(action, ids) {
    return post({ action: action, ids: ids }).then(function (d) {
      if (!d || !d.ok) { failed(d); return; }
      var changed = d.changed || [];
      var touched = rowsById(changed);
      if (action === "read" || action === "read_all") touched.forEach(function (r) { markRow(r, true); });
      if (action === "unread") touched.forEach(function (r) { markRow(r, false); });
      if (action === "dismiss" || action === "clear_all") {
        touched.forEach(function (r) { r.remove(); });
        emptyIfNone();
      }
      settle(d);
      var undo = { dismiss: "undo", clear_all: "undo", read_all: "unread" }[action];
      if (Menu && Menu.toast) {
        var extra = undo && changed.length ? [undoButton(changed, undo)] : [];
        Menu.toast(d.message || "Done.", { ok: true, actions: extra });
      }
    });
  }
  function rowMenu(row, x, y) {
    if (!Menu) return;
    var id = row.getAttribute("data-alert");
    var read = row.getAttribute("data-read") === "1";
    var main = row.querySelector(".al-main");
    var title = (row.querySelector(".al-title") || {}).textContent || "";
    var items = [
      Menu.item("Open", "eye", function () { Menu.whenClosed(function () { location.href = main.getAttribute("href"); }); }),
      Menu.item(read ? "Mark unread" : "Mark read", read ? "bell" : "check", function () { act(read ? "unread" : "read", [id]); }),
      Menu.item("Dismiss", "x", function () { act("dismiss", [id]); })
    ];
    Menu.open({ items: items, head: title, label: "Menu: " + (title || "Alert") }, x, y);
  }
  function bindRows(root) {
    if (!Menu || !Menu.bind || !root || root.getAttribute("data-al-bound")) return;
    root.setAttribute("data-al-bound", "1");
    Menu.bind(root, {
      itemOf: function (t) { return t && t.closest ? t.closest(".al-row[data-alert]") : null; },
      button: ".al-menu > summary",
      skip: ".al-menu",
      open: function (row, x, y) { rowMenu(row, x, y); }
    });
  }
  $$("[data-al-page]").forEach(bindRows);
  document.addEventListener("click", function (e) {
    var b = e.target && e.target.closest ? e.target.closest("[data-al-all]") : null;
    if (!b || !Menu) return;
    e.preventDefault();
    var what = b.getAttribute("data-al-all");
    act(what, []);
  });

  // a notice's × (assistants.dismiss_form): sent in place, and the notice goes
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f || !f.hasAttribute || !f.hasAttribute("data-dismiss")) return;
    e.preventDefault();
    var box = f.closest("[data-dismissable]");
    if (box) box.remove();
    fetch(f.action, {
      method: "POST",
      credentials: "same-origin",
      body: new URLSearchParams(new FormData(f)),
      redirect: "manual"
    }).catch(function () { /* the notice comes back on the next page; nothing else is lost */ });
  });

  // -- the bell's box ----------------------------------------------------------------------------
  function open(back) {
    var Box = window.MtgBox;
    if (!Box) { location.href = "/notifications"; return; }
    var body = el("div");
    body.appendChild(el("p", "muted", "Reading your alerts…"));
    var all = el("a", "btn btn-primary");
    all.href = "/notifications";
    all.textContent = "Open all alerts";
    var settings = el("a", "btn btn-plain");
    settings.href = "/account/details/notifications";
    var gear = Menu && Menu.icon ? Menu.icon("settings") : null;
    if (gear) { settings.appendChild(gear); settings.appendChild(document.createTextNode(" ")); }
    settings.appendChild(document.createTextNode("Settings"));
    var box = Box.open({
      title: "Alerts",
      label: "Alerts",
      body: body,
      actions: [all, settings, Box.button("Close", "btn", null, { cancel: true })],
      returnFocus: back
    });
    getJSON("/notifications").then(function (d) {
      var wrap = el("div");
      if (!d || !d.ok) {
        var err = el("p", "notice error", (d && d.message) || "No connection; the list could not be read. Try again.");
        err.setAttribute("role", "alert");
        wrap.appendChild(err);
      } else {
        wrap.innerHTML = d.html;
        wrap.setAttribute("data-al-page", "");
        if (d.unread > 0) {
          var h = box.panel.querySelector(".bh h2");
          if (h) { h.appendChild(document.createTextNode(" ")); h.appendChild(el("span", "bell-badge", d.unread + " unread")); }
        }
        if (d.total > 0) {
          var more = el("p", "muted small bell-foot", d.total > 8 ? "The newest 8 of " + d.total + ". Open all alerts for the rest, to filter them, or to clear them." : "Open all alerts to filter them or clear them.");
          wrap.appendChild(more);
        }
        setCount(0);
        keep(0);
      }
      box.setBody(wrap);
      bindRows(wrap);
      box.focusFirst();
    }, function (err) {
      var e;
      if (err && err.signedOut && window.MtgFetch) e = window.MtgFetch.signedOut("the list");
      else {
        e = el("p", "notice error", "No connection; the list could not be read. Try again.");
        e.setAttribute("role", "alert");
      }
      box.setBody(e);
      box.focusFirst();
    });
  }
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("[data-bell], [data-bell-open]") : null;
    if (!a || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    // on the Alerts page itself the bell has nothing more to show
    if (location.pathname === "/notifications") return;
    e.preventDefault();
    open(a);
  });
  window.MtgBell = { count: function () { return count; }, open: open, ask: ask };
})();
