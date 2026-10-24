/* The bell in the top bar (theme.py, profile_page.py): Archidekt's unseen count, asked once the
   page has loaded (never during the page's own render, so the bar never slows a page), and the
   list as a box when the bell or Home's "On Archidekt" widget is pressed. Without this file the
   bell is a link to the /notifications page. Needs static/box.js (window.MtgBox). */
(function () {
  "use strict";
  var bell = document.querySelector("[data-bell]");
  if (!bell || !window.fetch) return;
  var badge = bell.querySelector(".n");
  var count = null;

  function setCount(n) {
    count = n;
    if (badge) {
      badge.textContent = n > 99 ? "99+" : String(n);
      badge.hidden = !(n > 0);
    }
    bell.setAttribute("aria-label", n > 0 ? "Notifications, " + n + " new" : "Notifications");
    bell.title = bell.getAttribute("aria-label");
    var big = document.querySelector("[data-bell-big]"), det = document.querySelector("[data-bell-det]");
    if (big) big.textContent = String(n);
    if (det) det.textContent = n > 0 ? (n === 1 ? "new notification" : "new notifications") : "nothing new";
  }
  function getJSON(href) {
    return fetch(href, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); });
  }
  function readCount() {
    getJSON("/notifications/count").then(function (d) {
      if (d && d.ok) setCount(d.unseen || 0);
    }, function () { /* no connection: the bell stays quiet */ });
  }
  function later(fn) {
    if (window.requestIdleCallback) window.requestIdleCallback(fn, { timeout: 2000 });
    else setTimeout(fn, 50);
  }
  if (document.readyState === "complete") later(readCount);
  else window.addEventListener("load", function () { later(readCount); });

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function open(back) {
    var Box = window.MtgBox;
    if (!Box) { location.href = "/notifications"; return; }
    var body = el("div");
    body.appendChild(el("p", "muted", "Reading Archidekt's notifications…"));
    var settings = el("a", "btn btn-plain");
    settings.href = "/account#email";
    var gear = window.MtgCardMenu && window.MtgCardMenu.icon ? window.MtgCardMenu.icon("settings") : null;
    if (gear) { settings.appendChild(gear); settings.appendChild(document.createTextNode(" ")); }
    settings.appendChild(document.createTextNode("Settings"));
    var box = Box.open({
      title: "Notifications",
      label: "Notifications",
      body: body,
      actions: [settings, Box.button("Close", "btn", null, { cancel: true })],
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
        if (d.unseen > 0) {
          var h = box.panel.querySelector(".bh h2");
          if (h) { h.appendChild(document.createTextNode(" ")); h.appendChild(el("span", "bell-badge", d.unseen + " new")); }
        }
        // no mark-as-read request was seen in Archidekt's scripts: the count clears once the list was opened
        setCount(0);
      }
      var note = el("p", "muted small bell-foot", "The count clears when you open this list. The items stay here for as long as Archidekt keeps them. Turn them off under Account › Email and notifications.");
      wrap.appendChild(note);
      box.setBody(wrap);
      box.focusFirst();
    }, function () {
      var e = el("p", "notice error", "No connection; the list could not be read. Try again.");
      e.setAttribute("role", "alert");
      box.setBody(e);
    });
  }
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("[data-bell], [data-bell-open]") : null;
    if (!a || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    open(a);
  });
  window.MtgBell = { count: function () { return count; }, open: open };
})();
