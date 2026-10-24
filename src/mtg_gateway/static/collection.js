/* Collection page (the member's Archidekt Collection). Works without this file: every control is a
   form or a link. With it: the stepper, the Details box and Remove update a row in place through the
   JSON API; − on the last copy and Remove ask first (the shared box); the name box narrows the list
   as you type; Load more appends the next rows; the drop-downs apply on change; notices are strips
   with a × to close. The shared menus.js reaches the row's hidden Remove form and Details hook. */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var csrfInput = $("input[name=csrf]");
  var CSRF = csrfInput ? csrfInput.value : "";
  var list = $("#cards");
  var notes = $("#col-notes");
  var FIN = { nonfoil: "Normal", foil: "Foil", etched: "Etched" };
  var COND = { "": "Not set", NM: "Near Mint (NM)", LP: "Lightly Played (LP)", MP: "Moderately Played (MP)", HP: "Heavily Played (HP)", DMG: "Damaged (DMG)" };
  var LANG = { EN: "English", ES: "Spanish", FR: "French", DE: "German", IT: "Italian", PT: "Portuguese", JA: "Japanese", KO: "Korean", RU: "Russian", ZHS: "Chinese (Simplified)", ZHT: "Chinese (Traditional)" };
  var NOT_ANSWERING = "Archidekt is not answering right now. Try again later.";
  var ICON_X = "<svg class='i' viewBox='0 0 24 24' aria-hidden='true'><path d='M18 6L6 18M6 6l12 12'/></svg>";

  function say(text) {
    var live = $("#live");
    if (live) live.textContent = text;
  }
  /* a strip at the top of the page, with a × to close; errors are alerts */
  function strip(text, kind) {
    say(text);
    if (!notes) return;
    var d = document.createElement("div");
    d.className = "notice " + (kind === "error" ? "error" : "ok");
    d.setAttribute("role", kind === "error" ? "alert" : "status");
    var x = document.createElement("button");
    x.type = "button"; x.className = "nx"; x.setAttribute("aria-label", "Close"); x.title = "Close";
    x.setAttribute("data-dismiss", ""); x.innerHTML = ICON_X;
    d.appendChild(x);
    d.appendChild(document.createTextNode(text));
    notes.insertBefore(d, notes.firstChild);
  }
  document.addEventListener("click", function (e) {
    var x = e.target.closest ? e.target.closest("#col-notes [data-dismiss]") : null;
    if (x) { var n = x.closest(".notice"); if (n) n.remove(); }
  });
  function failText(d, name) {
    var why = d && d.error;
    var msg = (why === "unavailable" || why === "rate_limited" || why === "busy") ? NOT_ANSWERING : ((d && d.message) || "That did not work.");
    return (name ? name + " was not changed. " : "") + msg;
  }
  function request(method, url, body) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (d) { d.status = r.status; return d; }); });
  }
  function nameOf(item) {
    var n = $(".name", item);
    return n ? (n.childNodes[0] ? n.childNodes[0].textContent : n.textContent).trim() : "Card";
  }
  function bumpCount(delta) {
    var c = $("#col-count");
    if (!c) return;
    var n = Math.max(0, (parseInt(c.textContent.replace(/,/g, ""), 10) || 0) + delta);
    c.textContent = n.toLocaleString() + (n === 1 ? " entry" : " entries");
  }
  function gone(item) {
    var name = nameOf(item);
    item.remove();
    bumpCount(-1);
    strip("Removed from your Collection on Archidekt.", "ok");
    if (list && !$("li[data-id]", list)) location.reload();
    return name;
  }

  /* -- the Remove box: − on the last copy, the hidden Remove form, the Details box's button --- */
  function askRemove(item, returnFocus) {
    var name = nameOf(item);
    var out = $("form.qty output", item);
    var qty = out ? parseInt(out.textContent, 10) || 1 : 1;
    var fin = FIN[item.getAttribute("data-fin")] || "Normal";
    var where = (item.getAttribute("data-setline") || "").trim();
    var text = "This removes " + (qty === 1 ? "it" : "all " + qty + " copies") + " (" + (where ? where + ", " : "") + fin + ") from your Collection on Archidekt.";
    if (!window.MtgBox) {
      if (window.confirm("Remove “" + name + "”? " + text)) doRemove(item);
      return;
    }
    window.MtgBox.confirm({
      title: "Remove “" + name + "”?", text: text, ok: "Remove", danger: true, icon: "trash",
      returnFocus: returnFocus,
      onOk: function () { return doRemove(item); }
    });
  }
  function doRemove(item) {
    var id = item.getAttribute("data-id");
    var name = nameOf(item);
    $$("button", item).forEach(function (b) { b.disabled = true; });
    return request("DELETE", "/collection/api/rows/" + encodeURIComponent(id)).then(function (d) {
      if (!d.ok) { strip(failText(d, name), "error"); $$("button", item).forEach(function (b) { b.disabled = false; }); return false; }
      gone(item);
      return false;
    }).catch(function () {
      $$("button", item).forEach(function (b) { b.disabled = false; });
      strip(failText(null, name), "error");
      return false;
    });
  }

  /* -- the stepper and the hidden Remove form ------------------------------------------------ */
  if (list) {
    list.addEventListener("click", function (e) {
      var show = e.target.closest(".thumbbtn");
      if (show && window.MtgCardView) {
        var card = show.closest("[data-card]");
        if (card) { e.preventDefault(); window.MtgCardView.open(window.MtgCardView.fromElement(card), []); return; }
      }
      var btn = e.target.closest("button[name=action]");
      if (!btn) return;
      var form = btn.form;
      var item = btn.closest("li[data-id]");
      if (!form || !item) return;
      var id = item.getAttribute("data-id");
      var out = form.querySelector("output");
      var current = out ? parseInt(out.textContent, 10) || 0 : 0;
      var action = btn.value;
      e.preventDefault();
      if (action === "remove" || (action === "dec" && current <= 1)) { askRemove(item, $("form.qty button[value=dec]", item) || $(".kebab", item)); return; }
      var next = action === "inc" ? current + 1 : Math.max(0, current - 1);
      if (action !== "inc" && action !== "dec") { form.submit(); return; }
      var name = nameOf(item);
      $$("form.qty button", item).forEach(function (b) { b.disabled = true; });
      request("POST", "/collection/api/rows/" + encodeURIComponent(id), { quantity: next }).then(function (d) {
        $$("form.qty button", item).forEach(function (b) { b.disabled = false; });
        if (!d.ok) { strip(failText(d, name), "error"); return; }
        if (!d.row || d.row.quantity === 0) { gone(item); return; }
        $$("output", item).forEach(function (o) { o.textContent = d.row.quantity; });
        say(name + ": " + d.row.quantity + (d.row.quantity === 1 ? " copy" : " copies"));
      }).catch(function () {
        $$("form.qty button", item).forEach(function (b) { b.disabled = false; });
        strip(failText(null, name), "error");
      });
    });
    /* the shared menu's Details… opens the row's hidden hook; that opens the Details box */
    list.addEventListener("toggle", function (e) {
      var d = e.target;
      if (!d.matches || !d.matches("details.rowmenu") || !d.open) return;
      d.open = false;
      var item = d.closest("li[data-id]");
      if (item) openDetails(item, $(".kebab", item));
    }, true);
  }

  /* -- the Details box ----------------------------------------------------------------------- */
  var detailsBox = $("#col-details");
  function setSelect(sel, value) {
    if (!sel) return;
    if (!Array.prototype.some.call(sel.options, function (o) { return o.value === value; })) {
      var o = document.createElement("option"); o.value = value; o.textContent = value; sel.insertBefore(o, sel.firstChild);
    }
    sel.value = value;
    if (window.MtgSelect && window.MtgSelect.refresh) window.MtgSelect.refresh(sel);
  }
  function openDetails(item, returnFocus) {
    if (!detailsBox) {
      var href = "/collection?details=" + encodeURIComponent(item.getAttribute("data-id"));
      if (window.MtgMenu) window.MtgMenu.whenClosed(function () { location.href = href; }); else location.href = href;
      return;
    }
    var form = $("form", detailsBox);
    var name = nameOf(item);
    var h = $("#col-details-title");
    if (h) h.textContent = "Details: " + name;
    form.setAttribute("data-id", item.getAttribute("data-id"));
    form.elements.id.value = item.getAttribute("data-id");
    var sub = $(".dsub", detailsBox);
    if (sub) sub.textContent = [item.getAttribute("data-setline"), item.getAttribute("data-setname")].filter(Boolean).join(" · ");
    setSelect(form.elements.finish, item.getAttribute("data-fin") || "nonfoil");
    setSelect(form.elements.condition, item.getAttribute("data-cond") || "");
    setSelect(form.elements.language, item.getAttribute("data-lang") || "EN");
    form.elements.purchase_price.value = item.getAttribute("data-paid") || "";
    var tags = [];
    try { tags = JSON.parse(item.getAttribute("data-tags") || "[]"); } catch (err) { tags = []; }
    var tagbox = $(".tags", detailsBox), line = $(".tagline", detailsBox);
    if (tagbox && line) {
      line.textContent = "";
      tags.forEach(function (t) { var s = document.createElement("span"); s.className = "pill"; s.textContent = t; line.appendChild(s); });
      tagbox.hidden = tags.length === 0;
    }
    if (window.MtgBox) window.MtgBox.openElement(detailsBox, { returnFocus: returnFocus });
    else detailsBox.hidden = false;
  }
  function itemOf(id) {
    return list ? $("li[data-id='" + id + "']", list) : null;
  }
  function applyRow(item, row) {
    item.setAttribute("data-fin", row.finish || "nonfoil");
    item.setAttribute("data-cond", row.condition || "");
    item.setAttribute("data-lang", row.lang || "EN");
    item.setAttribute("data-paid", row.purchase_price == null ? "" : row.purchase_price);
    item.setAttribute("data-finish", row.finish === "foil" ? "Foil" : row.finish === "etched" ? "Etched" : "");
    var fin = FIN[row.finish] || "Normal", cond = row.condition || "", lang = row.lang || "EN";
    row.lang_name = LANG[lang] || lang;
    var ty = $(".meta .ty", item);
    if (ty) ty.textContent = (item.getAttribute("data-setline") || "") + (lang !== "EN" ? " \u00B7 " + row.lang_name : "");
    var pic = $(".pic", item);
    if (pic) {
      var f = $(".fin", pic), c = $(".cond", pic);
      if (fin !== "Normal") { if (!f) { f = document.createElement("span"); f.className = "fin"; pic.appendChild(f); } f.textContent = fin; }
      else if (f) f.remove();
      if (cond) { if (!c) { c = document.createElement("span"); c.className = "cond"; pic.appendChild(c); } c.textContent = cond; c.title = COND[cond] || cond; }
      else if (c) c.remove();
    }
    var cells = $$(".md-hide", item);
    if (cells.length === 4) {
      cells[1].textContent = fin; cells[2].textContent = COND[cond] || cond || "Not set";
      cells[3].textContent = row.lang_name || lang;
    }
    var sub = $(".sub", item);
    if (sub) {
      var parts = [item.getAttribute("data-setline"), item.getAttribute("data-setname"), fin, COND[cond] || cond || "Not set", row.lang_name || lang];
      sub.textContent = parts.filter(Boolean).join(" · ");
    }
    var sm = $(".smline", item);
    if (sm) sm.textContent = [item.getAttribute("data-setline"), fin, cond || "Not set", row.lang_name || lang].filter(Boolean).join(" · ");
  }
  if (detailsBox) {
    detailsBox.addEventListener("click", function (e) {
      var rm = e.target.closest("button[name=action][value=remove]");
      if (!rm) return;
      var form = $("form", detailsBox);
      var item = itemOf(form.getAttribute("data-id"));
      if (!item) return;  // without a row on the page the form posts as it would without scripts
      e.preventDefault();
      if (window.MtgBox) window.MtgBox.close();
      askRemove(item, $(".kebab", item));
    });
    detailsBox.addEventListener("submit", function (e) {
      var form = e.target.closest("form.detailsform");
      if (!form) return;
      var item = itemOf(form.getAttribute("data-id"));
      if (!item) return;
      var sub = e.submitter;
      if (sub && sub.value === "remove") { e.preventDefault(); return; }
      e.preventDefault();
      var body = {
        finish: form.elements.finish.value,
        condition: form.elements.condition.value,
        language: form.elements.language.value,
        purchase_price: form.elements.purchase_price.value
      };
      var name = nameOf(item);
      var btns = $$("button", detailsBox);
      btns.forEach(function (b) { b.disabled = true; });
      request("POST", "/collection/api/rows/" + encodeURIComponent(form.getAttribute("data-id")), body).then(function (d) {
        btns.forEach(function (b) { b.disabled = false; });
        if (!d.ok) { strip(failText(d, name), "error"); return; }
        applyRow(item, d.row || {});
        if (window.MtgBox) window.MtgBox.close();
        strip("Card details saved to your Collection on Archidekt.", "ok");
      }).catch(function () { btns.forEach(function (b) { b.disabled = false; }); strip(failText(null, name), "error"); });
    });
  }

  /* -- the Add cards box: two tabs, the action row's button follows ------------------------- */
  var addBox = $("#col-add");
  function showTab(which) {
    if (!addBox) return;
    $$("[data-coltab]", addBox).forEach(function (t) { t.setAttribute("aria-selected", t.getAttribute("data-coltab") === which ? "true" : "false"); });
    $$("[data-pane]", addBox).forEach(function (p) { p.hidden = p.getAttribute("data-pane") !== which; });
    $$("[data-pane-btn]", addBox).forEach(function (b) { b.hidden = b.getAttribute("data-pane-btn") !== which; });
    var first = $("[data-pane='" + which + "'] input:not([type=hidden]):not([type=file]), [data-pane='" + which + "'] textarea", addBox);
    if (first && addBox.contains(document.activeElement)) first.focus();
  }
  if (addBox) {
    addBox.addEventListener("click", function (e) {
      var t = e.target.closest("[data-coltab]");
      if (t) showTab(t.getAttribute("data-coltab"));
    });
    addBox.addEventListener("keydown", function (e) {
      var t = e.target.closest("[data-coltab]");
      if (!t || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      var tabs = $$("[data-coltab]", addBox), i = tabs.indexOf(t);
      var n = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      n.focus(); showTab(n.getAttribute("data-coltab"));
    });
    // an opener whose address says which tab (…?add=import) opens that tab
    document.addEventListener("click", function (e) {
      var o = e.target.closest ? e.target.closest("[data-box-open='col-add']") : null;
      if (!o) return;
      showTab(/add=import/.test(o.getAttribute("href") || "") ? "import" : "add");
      setTimeout(function () {
        var which = $("[data-coltab][aria-selected=true]", addBox);
        var first = which && $("[data-pane='" + which.getAttribute("data-coltab") + "'] input:not([type=hidden]):not([type=file]), [data-pane='" + which.getAttribute("data-coltab") + "'] textarea", addBox);
        if (first) first.focus();
      }, 50);
    }, true);
  }

  /* -- the panel: the name box narrows the list as you type; drop-downs apply on change ----- */
  var listform = $("#listform");
  var nomatch = $(".col-nomatch");
  function narrow(q) {
    if (!list) return;
    q = q.trim().toLowerCase();
    var any = false;
    $$("[data-name]", list).forEach(function (li) {
      var ok = !q || (li.getAttribute("data-name") || "").indexOf(q) !== -1;
      li.hidden = !ok;
      if (ok) any = true;
    });
    if (nomatch) nomatch.hidden = any || !$("[data-name]", list);
  }
  if (listform) {
    var qbox = $("#col-q", listform);
    if (qbox) qbox.addEventListener("input", function () { narrow(qbox.value); });
    var sortf = $(".sortf", listform), dirIn = $("input[name=dir]", listform), dirBtn = $(".dirbtn", listform), sortc = $(".sortc", listform);
    function go(sort, dir) {
      if (sortf) sortf.value = sort;
      if (dirBtn) dirBtn.removeAttribute("name");
      if (sort === "edition") {
        if (!dirIn) { dirIn = document.createElement("input"); dirIn.type = "hidden"; dirIn.name = "dir"; listform.appendChild(dirIn); }
        dirIn.value = dir || "desc";
      } else if (dirIn) dirIn.remove();
      submit();
    }
    function submit() { if (listform.requestSubmit) listform.requestSubmit(); else listform.submit(); }
    if (sortc) sortc.addEventListener("change", function () { var p = sortc.value.split(":"); go(p[0], p[1]); });
    if (sortf) sortf.addEventListener("change", function () { go(sortf.value, dirIn ? dirIn.value : "desc"); });
    $$("input[name=view]", listform).forEach(function (r) { r.addEventListener("change", submit); });
  }

  /* -- Load more: the next rows, appended ---------------------------------------------------- */
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".loadmore [data-load-more]") : null;
    if (!a || !list || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    var wrap = a.closest(".loadmore");
    var href = a.getAttribute("href").replace(/^\/collection(\?|$)/, "/collection/api/rows$1");
    a.setAttribute("aria-busy", "true"); a.setAttribute("aria-disabled", "true");
    fetch(href, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) throw new Error(d.message || "");
      var tmp = document.createElement("ul");
      tmp.innerHTML = d.html;
      var first = null;
      while (tmp.firstChild) { if (!first && tmp.firstChild.nodeType === 1) first = tmp.firstChild; list.appendChild(tmp.firstChild); }
      var q = $("#col-q");
      if (q && q.value) narrow(q.value);
      var line = $("p", wrap);
      if (d.has_more) {
        a.setAttribute("href", d.next); a.removeAttribute("aria-busy"); a.removeAttribute("aria-disabled");
        if (line) line.textContent = d.shown.toLocaleString() + " of " + d.total.toLocaleString();
      } else {
        a.remove();
        if (!line) { line = document.createElement("p"); line.className = "muted"; wrap.appendChild(line); }
        line.textContent = d.done;
      }
      say(d.shown + " of " + d.total + " shown.");
      var focus = first ? $(".thumbbtn, .kebab", first) : null;
      if (focus) focus.focus({ preventScroll: true });
    }).catch(function () {
      a.removeAttribute("aria-busy"); a.removeAttribute("aria-disabled");
      strip(NOT_ANSWERING, "error");
    });
  });
})();
