"use strict";
/* The Binder page (binder_hub.py, One Binder 0.7.47): every card a member owns or wants as one
   list. This script adds what a page without scripts cannot do:
   - Select: ticks on the rows and the bar (Price alerts, Add to list, Watch, and in ⋯ Stop
     watching, Tag on Archidekt, Remove from binder, Select all in this view). A box's form is
     filled with the ticked rows (``keys``: the rows' keys for /binder; ``sel``: "rid name" lines
     for the Binder's own bulk change on /collection) before it opens.
   - A tap on a row opens the shared card viewer with the row's actions.
   - The order and tag menus show their choice at once.
   - Load more adds the next rows in place (the page's own address with part=rows).
   Without scripts the rows are a plain list, the menus have a Show button and Load more is a link. */
(function () {
  var root = document.querySelector(".bn");
  if (!root) return;
  var main = root.querySelector(".bn-main");
  var bar = root.querySelector(".ui-bar");
  var selectBtn = root.querySelector(".bn-select");
  var Box = window.MtgBox;

  function rows() { return Array.prototype.slice.call(root.querySelectorAll(".bn-rows .bn-row")); }
  function ticked() { return rows().filter(function (r) { var t = r.querySelector(".bn-tick"); return t && t.checked; }); }
  function plural(n, word) { return n.toLocaleString("en-US") + " " + word + (n === 1 ? "" : "s"); }
  function money(v) { return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function back() {
    var u = new URL(location.href);
    ["ok", "err", "m", "n", "undo", "t", "box", "from", "part"].forEach(function (k) { u.searchParams.delete(k); });
    return u.pathname + u.search;
  }

  // -- Select ---------------------------------------------------------------------------------------
  function selecting() { return root.classList.contains("selecting"); }
  function count() {
    var n = ticked().length;
    if (!bar) return;
    var c = bar.querySelector(".count .n");
    if (c) c.textContent = n.toLocaleString("en-US");
    else bar.querySelector(".count").textContent = n + " selected";
    bar.querySelectorAll("[data-bulk]").forEach(function (b) {
      if (b.getAttribute("data-bulk") !== "all") b.disabled = n === 0;
    });
  }
  function setSelecting(on) {
    root.classList.toggle("selecting", on);
    if (selectBtn) selectBtn.setAttribute("aria-pressed", on ? "true" : "false");
    if (bar) bar.hidden = !on;
    if (!on) rows().forEach(function (r) { var t = r.querySelector(".bn-tick"); if (t) t.checked = false; r.classList.remove("on"); });
    count();
  }
  if (selectBtn) selectBtn.addEventListener("click", function () { setSelecting(!selecting()); });
  root.addEventListener("change", function (e) {
    var t = e.target;
    if (t.classList && t.classList.contains("bn-tick")) {
      var row = t.closest(".bn-row");
      if (row) row.classList.toggle("on", t.checked);
      if (t.checked && !selecting()) setSelecting(true);
      count();
    }
    // the order and tag menus show their choice at once
    if (t.matches && t.matches("select[data-autosubmit]") && t.form) t.form.submit();
  });
  if (bar) {
    var x = bar.querySelector(".bn-cancel");
    if (x) x.addEventListener("click", function () { setSelecting(false); if (selectBtn) selectBtn.focus(); });
  }

  // -- the boxes --------------------------------------------------------------------------------------
  function fill(form, picked) {
    var keys = form.querySelector("input[name=keys]");
    if (keys) keys.value = picked.map(function (r) { return r.getAttribute("data-key"); }).join(" ");
    var sel = form.querySelector("input[name=sel]");
    if (sel) {
      var lines = [];
      picked.forEach(function (r) {
        try { lines = lines.concat(JSON.parse(r.getAttribute("data-rids") || "[]")); } catch (e) { /* none */ }
      });
      sel.value = lines.join("\n");
    }
  }
  function describe(kind, picked) {
    var watched = picked.filter(function (r) { return r.getAttribute("data-wid"); }).length;
    var owned = picked.filter(function (r) { return r.getAttribute("data-rids"); }).length;
    var n = picked.length;
    if (kind === "alerts") {
      if (watched === n) return plural(n, "card") + " selected.";
      return "Of the " + plural(n, "card") + " selected, " + watched.toLocaleString("en-US")
        + (watched === 1 ? " is watched and gets" : " are watched and get") + " these alerts. Watch the others first.";
    }
    if (kind === "tag" || kind === "remove") {
      if (owned === n) return plural(n, "card") + " selected.";
      return "Of the " + plural(n, "card") + " selected, you own " + owned.toLocaleString("en-US")
        + ". Cards you only want are left out.";
    }
    return plural(n, "card") + " selected.";
  }
  function openBox(kind, picked, from) {
    var box = document.getElementById("bn-" + kind);
    if (!box || !picked.length) return;
    var form = box.querySelector("form");
    if (form) fill(form, picked);
    box.querySelectorAll(".bn-count").forEach(function (p) { p.textContent = describe(kind, picked); });
    if (kind === "alerts") alertsShow(box);
    if (Box && Box.openElement) Box.openElement(box, { returnFocus: from || null });
  }
  function post(action, picked) {
    var f = document.createElement("form");
    f.method = "post";
    f.action = "/binder";
    var add = function (k, v) { var i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.appendChild(i); };
    add("csrf", root.getAttribute("data-csrf") || "");
    add("action", action);
    add("back", back());
    add("keys", picked.map(function (r) { return r.getAttribute("data-key"); }).join(" "));
    document.body.appendChild(f);
    f.submit();
  }
  function act(kind, picked, from) {
    if (kind === "watch" || kind === "unwatch") post(kind, picked);
    else openBox(kind, picked, from);
  }
  if (bar) bar.addEventListener("click", function (e) {
    var b = e.target.closest("[data-bulk]");
    if (!b) return;
    var kind = b.getAttribute("data-bulk");
    var dd = b.closest("details");
    if (dd) dd.open = false;
    if (kind === "all") {
      rows().forEach(function (r) { var t = r.querySelector(".bn-tick"); if (t) t.checked = true; r.classList.add("on"); });
      count();
      return;
    }
    act(kind, ticked(), b);
  });

  // Price alerts: "Use my defaults" or the card's own, with what they mean for a $10.00 card
  function alertsShow(box) {
    var own = box.querySelector("input[name=mode][value=own]");
    var fields = box.querySelector(".bn-own");
    if (fields) fields.hidden = !(own && own.checked);
    var ex = box.querySelector(".bn-example");
    if (!ex) return;
    if (!own || !own.checked) { ex.textContent = ""; return; }
    var num = function (n) { var v = parseFloat((box.querySelector("input[name=" + n + "]") || {}).value); return v > 0 ? v : null; };
    var floor = parseFloat(root.getAttribute("data-floor") || "0") || 0;
    var added = 10, drop = num("alert_drop"), rise = num("alert_rise"), said = [];
    if (drop !== null) { var u = Math.min(added * (1 - drop / 100), added - floor); if (u > 0) said.push("under " + money(u)); }
    if (rise !== null) said.push("over " + money(Math.max(added * (1 + rise / 100), added + floor)));
    ex.textContent = "A card added at " + money(added) + ": " + (said.length ? "alert " + said.join(" or ") + "." : "no alert.");
  }
  var alertsBox = document.getElementById("bn-alerts");
  if (alertsBox) {
    alertsBox.addEventListener("change", function () { alertsShow(alertsBox); });
    alertsBox.addEventListener("input", function () { alertsShow(alertsBox); });
    alertsShow(alertsBox);
  }

  // -- a row opens the card viewer --------------------------------------------------------------------
  function button(text, fn) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn";
    b.textContent = text;
    b.addEventListener("click", function () { if (window.MtgCardView) window.MtgCardView.close(); fn(b); });
    return b;
  }
  root.addEventListener("click", function (e) {
    var open = e.target.closest(".bn-open");
    if (!open) return;
    var row = open.closest(".bn-row");
    if (!row) return;
    if (selecting()) {  // while selecting, a tap ticks the row
      var t = row.querySelector(".bn-tick");
      if (t) { t.checked = !t.checked; row.classList.toggle("on", t.checked); count(); }
      return;
    }
    if (!window.MtgCardView) return;
    var acts = [];
    if (row.getAttribute("data-wid")) {
      acts.push(button("Price alerts…", function () { openBox("alerts", [row], open); }));
    }
    acts.push(button("Add to list…", function () { openBox("list", [row], open); }));
    if (row.getAttribute("data-wid")) acts.push(button("Stop watching", function () { post("unwatch", [row]); }));
    else if (row.getAttribute("data-sid")) acts.push(button("Watch", function () { post("watch", [row]); }));
    window.MtgCardView.open(window.MtgCardView.fromElement(row), acts);
  });

  // -- Load more ---------------------------------------------------------------------------------------
  if (main) main.addEventListener("click", function (e) {
    var a = e.target.closest(".bn-more a[data-from]");
    if (!a) return;
    e.preventDefault();
    if (a.getAttribute("aria-busy") === "true") return;
    a.setAttribute("aria-busy", "true");
    var u = new URL(a.href, location.href);
    u.searchParams.set("part", "rows");
    fetch(u.toString(), { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw r;
      var left = parseInt(r.headers.get("X-More") || "0", 10) || 0;
      return r.text().then(function (html) { return { html: html, left: left }; });
    }).then(function (x) {
      var list = main.querySelector(".bn-rows");
      var before = list.children.length;
      list.insertAdjacentHTML("beforeend", x.html);
      var first = list.children[before];
      var from = parseInt(a.getAttribute("data-from"), 10) + (list.children.length - before);
      if (x.left > 0) {
        a.setAttribute("data-from", String(from));
        var next = new URL(a.href, location.href);
        next.searchParams.set("from", String(from));
        a.href = next.pathname + next.search;
        a.textContent = "Load " + Math.min(x.left, 60).toLocaleString("en-US") + " more";
        a.removeAttribute("aria-busy");
      } else {
        a.parentNode.remove();
      }
      if (first) { var f = first.querySelector(".bn-open"); if (f) f.focus(); }
      count();
      if (window.MtgLoadMore) window.MtgLoadMore.loaded();
    }).catch(function (x) {
      a.removeAttribute("aria-busy");
      if (window.MtgLoadMore) window.MtgLoadMore.failed(x);
    });
  });

  count();
})();
