/* Themed dropdown lists for every single-value <select> (R-131): the browser's own pop-up list
   is replaced by the gateway's menu panel on every page, in the Android app's WebView too. The
   native select stays in the document (the form posts it, scripts read and set its value and
   "change" still fires) but is hidden off-screen; a button in its place shows the chosen label
   and opens a listbox (the ARIA select-only combobox: focus stays on the button, and
   aria-activedescendant names the highlighted row). The list is built when it opens and thrown
   away when it closes, so a page with many selects (the deck editor's rows) pays only one button
   each up front; selects added later (the editor's new rows) are enhanced as they appear. Under
   600px, or with a coarse pointer, the list is a bottom sheet. Keyboard on the button: Enter,
   Space, Down or Up open; Down and Up move; Home and End jump; letters type ahead; Enter picks;
   Escape and Tab close. Selects with multiple, size > 1, hidden or data-native are left alone.
   A script that sets a select's value calls MtgSelect.refresh(select); a "change" event or a
   form reset refreshes the label too. */
(function () {
  "use strict";
  var counter = 0;
  var open = null;                     // the open list: {select, btn, list, scrim, items, active, sheet}
  var buttons = new WeakMap();          // select -> its button
  var selects = new WeakMap();          // button -> its select
  var typed = "", typedAt = 0;
  var sheetQuery = window.matchMedia ? window.matchMedia("(max-width:599.98px),(pointer:coarse)") : null;

  function skip(select) {
    return select.multiple || select.size > 1 || select.hidden || select.hasAttribute("data-native")
      || select.hasAttribute("data-msel");
  }
  function labelText(select) {
    var o = select.options[select.selectedIndex];
    return o ? o.text : "";
  }
  function refresh(select) {
    var btn = buttons.get(select);
    if (!btn) return;
    btn.firstChild.textContent = labelText(select) || " ";
    btn.disabled = !!select.disabled;
  }
  function accessibleName(select, btn) {
    var own = select.getAttribute("aria-label");
    if (own) { btn.setAttribute("aria-label", own); return; }
    var labs = select.labels, lab = labs && labs[0];
    if (!lab) return;
    if (lab.htmlFor) {
      if (!lab.id) lab.id = "msel-lbl" + (++counter);
      btn.setAttribute("aria-labelledby", lab.id);
      return;
    }
    // the select sits inside its label: the label's own words name the button, not the options
    var text = "";
    Array.prototype.forEach.call(lab.childNodes, function (n) {
      if (n === select || n === btn || (n.nodeType === 1 && n.tagName === "SELECT")) return;
      text += n.textContent;
    });
    text = text.replace(/\s+/g, " ").trim();
    if (text) btn.setAttribute("aria-label", text);
  }
  function nameOf(btn) {
    var by = btn.getAttribute("aria-labelledby"), el = by && document.getElementById(by);
    return (el ? el.textContent : btn.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
  }

  function enhance(select) {
    if (select.tagName !== "SELECT" || skip(select)) return;
    select.setAttribute("data-msel", "1");
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "msel-btn";
    if (select.id) btn.id = select.id + "-btn";
    btn.setAttribute("role", "combobox");
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    btn.appendChild(document.createElement("span")).className = "msel-txt";
    select.parentNode.insertBefore(btn, select.nextSibling);
    select.classList.add("msel-native");
    select.setAttribute("tabindex", "-1");
    select.setAttribute("aria-hidden", "true");
    buttons.set(select, btn);
    selects.set(btn, select);
    accessibleName(select, btn);
    refresh(select);
    btn.addEventListener("click", function () {
      if (open && open.select === select) close(); else show(select);
    });
    btn.addEventListener("keydown", onKey);
    btn.addEventListener("focusout", function () {
      // Tab or a click elsewhere: the list goes with the focus (a tap on a row keeps the focus here)
      setTimeout(function () { if (open && open.btn === btn && document.activeElement !== btn) close(); }, 0);
    });
  }
  function scan(root) {
    if (root.tagName === "SELECT") { enhance(root); return; }
    if (!root.querySelectorAll) return;
    Array.prototype.forEach.call(root.querySelectorAll("select"), enhance);
  }

  function build(select) {
    var list = document.createElement("div");
    list.className = "menu msel-list";
    list.id = "msel-list";
    list.setAttribute("role", "listbox");
    var items = [], group = null, chosen = select.selectedIndex;
    Array.prototype.forEach.call(select.options, function (o, i) {
      if (o.hidden) return;
      var g = o.parentNode.tagName === "OPTGROUP" ? o.parentNode : null;
      if (g && g !== group) {
        var head = document.createElement("div");
        head.className = "head";
        head.textContent = g.label;
        list.appendChild(head);
      }
      group = g;
      var row = document.createElement("button");
      row.type = "button";
      row.id = "msel-opt-" + i;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", "false");
      row.setAttribute("data-index", String(i));
      row.textContent = o.text;
      if (i === chosen) row.className = "on";
      if (o.disabled) row.disabled = true;
      list.appendChild(row);
      items.push(row);
    });
    return { list: list, items: items };
  }
  function place(btn, list) {
    var r = btn.getBoundingClientRect(), gap = 4, margin = 8;
    list.style.minWidth = Math.min(Math.max(r.width, 160), window.innerWidth - 2 * margin) + "px";
    list.style.left = Math.max(margin, Math.min(r.left, window.innerWidth - list.offsetWidth - margin)) + "px";
    var below = window.innerHeight - r.bottom - gap - margin, above = r.top - gap - margin;
    var h = list.offsetHeight;
    if (h <= below || below >= above) {
      list.style.top = (r.bottom + gap) + "px";
      list.style.maxHeight = Math.max(80, below) + "px";
    } else {
      list.style.maxHeight = Math.max(80, above) + "px";
      list.style.top = Math.max(margin, r.top - gap - list.offsetHeight) + "px";
    }
  }
  function show(select) {
    if (open) close();
    var btn = buttons.get(select);
    if (!btn || btn.disabled) return;
    var built = build(select), list = built.list;
    var sheet = !!(sheetQuery && sheetQuery.matches), scrim = null;
    if (sheet) {
      list.classList.add("sheet");
      var title = nameOf(btn);
      if (title) {
        var head = document.createElement("div");
        head.className = "head title";
        head.textContent = title;
        list.insertBefore(head, list.firstChild);
      }
      scrim = document.createElement("div");
      scrim.className = "msel-scrim";
      // The scrim stays until the tap's click lands on it: closing on pointerdown would remove
      // it first and the click would fall through to whatever is under the finger (a link, a
      // remove button). The pointerdown and the click are both swallowed here.
      scrim.addEventListener("pointerdown", function (e) { e.preventDefault(); });
      scrim.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); close(); });
      document.body.appendChild(scrim);
    }
    document.body.appendChild(list);
    if (!sheet) place(btn, list);
    open = { select: select, btn: btn, list: list, scrim: scrim, items: built.items, active: -1, sheet: sheet };
    btn.setAttribute("aria-expanded", "true");
    btn.setAttribute("aria-controls", list.id);
    var current = -1;
    built.items.forEach(function (row, n) { if (row.className === "on") current = n; });
    setActive(current >= 0 ? current : firstEnabled(0, 1), true);
    list.addEventListener("mousedown", function (e) { e.preventDefault(); });  // the focus stays on the button
    list.addEventListener("click", function (e) {
      var row = e.target.closest ? e.target.closest("[role=option]") : null;
      if (row && !row.disabled) pick(built.items.indexOf(row));
    });
    list.addEventListener("mousemove", function (e) {
      var row = e.target.closest ? e.target.closest("[role=option]") : null;
      if (row && !row.disabled) {
        var n = built.items.indexOf(row);
        if (n !== open.active) setActive(n, false);
      }
    });
  }
  function close() {
    if (!open) return;
    var o = open;
    open = null;
    o.list.parentNode.removeChild(o.list);
    if (o.scrim) o.scrim.parentNode.removeChild(o.scrim);
    o.btn.setAttribute("aria-expanded", "false");
    o.btn.removeAttribute("aria-controls");
    o.btn.removeAttribute("aria-activedescendant");
  }
  function firstEnabled(from, step) {
    var items = open.items;
    for (var n = from; n >= 0 && n < items.length; n += step) if (!items[n].disabled) return n;
    return -1;
  }
  function setActive(n, scroll) {
    if (!open || n < 0 || n >= open.items.length) return;
    if (open.active >= 0) open.items[open.active].setAttribute("aria-selected", "false");
    open.active = n;
    var row = open.items[n];
    row.setAttribute("aria-selected", "true");
    open.btn.setAttribute("aria-activedescendant", row.id);
    if (scroll && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
  }
  function move(step) {
    var n = firstEnabled(open.active + step, step);
    if (n >= 0) setActive(n, true);
  }
  function pick(n) {
    var o = open, row = o.items[n];
    if (!row) return;
    var idx = parseInt(row.getAttribute("data-index"), 10);
    close();
    if (o.select.selectedIndex !== idx) {
      o.select.selectedIndex = idx;
      o.select.dispatchEvent(new Event("input", { bubbles: true }));
      o.select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    refresh(o.select);  // a change handler may have set the value back
    if (document.body.contains(o.btn)) o.btn.focus();
  }
  function typeAhead(ch) {
    var now = Date.now();
    typed = now - typedAt < 600 ? typed + ch : ch;
    typedAt = now;
    var items = open.items, start = open.active, q = typed.toLowerCase();
    // one letter repeated cycles through the names starting with it; a longer text stays on its match
    if (q.length > 1 && /^(.)\1+$/.test(q)) q = q.charAt(0);
    var from = q.length === 1 ? start + 1 : Math.max(0, start);
    for (var k = 0; k < items.length; k++) {
      var n = (from + k) % items.length;
      if (!items[n].disabled && items[n].textContent.toLowerCase().indexOf(q) === 0) { setActive(n, true); return; }
    }
  }
  function onKey(e) {
    var btn = e.currentTarget, select = null;
    if (open && open.btn === btn) select = open.select;
    var key = e.key;
    if (!select) {
      if (key === "Enter" || key === " " || key === "ArrowDown" || key === "ArrowUp" || key === "Spacebar") {
        e.preventDefault();
        show(selects.get(btn));
      } else if (key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && key !== " ") {
        e.preventDefault();
        show(selects.get(btn));
        if (open) typeAhead(key);
      }
      return;
    }
    switch (key) {
      case "ArrowDown": e.preventDefault(); move(1); return;
      case "ArrowUp": e.preventDefault(); move(-1); return;
      case "Home": e.preventDefault(); setActive(firstEnabled(0, 1), true); return;
      case "End": e.preventDefault(); setActive(firstEnabled(open.items.length - 1, -1), true); return;
      case "PageDown": e.preventDefault(); setActive(Math.max(0, Math.min(open.items.length - 1, open.active + 10)), true); return;
      case "PageUp": e.preventDefault(); setActive(Math.max(0, open.active - 10), true); return;
      case "Enter": case " ": case "Spacebar": e.preventDefault(); if (open.active >= 0) pick(open.active); return;
      case "Escape": e.preventDefault(); close(); return;
      case "Tab": close(); return;
    }
    if (key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); typeAhead(key); }
  }
  document.addEventListener("pointerdown", function (e) {
    // a mouse list closes as soon as the pointer goes down outside it; the touch sheet waits for
    // the click on its scrim (above), so the tap never reaches the page behind it
    if (open && !open.sheet && !open.list.contains(e.target) && e.target !== open.btn && !open.btn.contains(e.target)) close();
  }, true);
  document.addEventListener("scroll", function (e) {
    if (open && !open.sheet && !open.list.contains(e.target)) close();
  }, true);
  window.addEventListener("resize", function () { if (open) close(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && open) { e.preventDefault(); close(); }
  });
  // a script set the value (a "change" is dispatched), the form was reset, or a label's click or
  // the browser's validation focused the hidden select: the button follows
  document.addEventListener("change", function (e) {
    if (e.target && e.target.tagName === "SELECT") refresh(e.target);
  }, true);
  document.addEventListener("reset", function (e) {
    var form = e.target;
    setTimeout(function () { if (form.querySelectorAll) Array.prototype.forEach.call(form.querySelectorAll("select"), refresh); }, 0);
  });
  document.addEventListener("focusin", function (e) {
    var btn = e.target && e.target.tagName === "SELECT" ? buttons.get(e.target) : null;
    if (btn && !btn.disabled) btn.focus();
  });

  function start() {
    scan(document.body);
    if (window.MutationObserver) {
      new MutationObserver(function (records) {
        records.forEach(function (rec) {
          Array.prototype.forEach.call(rec.addedNodes, function (n) { if (n.nodeType === 1) scan(n); });
        });
      }).observe(document.body, { childList: true, subtree: true });
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();

  window.MtgSelect = {
    refresh: refresh,
    enhance: scan,
    close: close,
    isOpen: function () { return !!open; }
  };
})();
