/* Collection page (the member's Archidekt Collection). Works without this file: every control is a
   form or a link. With it: the stepper, the Details box and Remove update a row in place through the
   JSON API; − on the last copy and Remove take the row out at once, with Undo; the name box narrows the list
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
  var NOT_ANSWERING = "Archidekt can't be reached right now. Try again later.";
  var ICON_X = "<svg class='i' viewBox='0 0 24 24' aria-hidden='true'><path d='M18 6L6 18M6 6l12 12'/></svg>";

  function say(text) {
    var live = $("#live");
    if (live) live.textContent = text;
  }
  /* a strip at the top of the page, with a × to close; errors are alerts. One strip at a time
     (a second press replaces the first), and the strip is the one live region that reads it:
     the page's #live is used only when there is no place for a strip (QG-08). ``actions``: the
     buttons that can help (Sign in, Undo), after the text. */
  function strip(text, kind, actions) {
    if (!notes) { say(text); return null; }
    var old = notes.querySelector(".notice[data-strip]");
    if (old) old.remove();
    var d = document.createElement("div");
    d.className = "notice " + (kind === "error" ? "error" : "ok");
    d.setAttribute("role", kind === "error" ? "alert" : "status");
    d.setAttribute("data-strip", "");
    var x = document.createElement("button");
    x.type = "button"; x.className = "nx"; x.setAttribute("aria-label", "Close"); x.title = "Close";
    x.setAttribute("data-dismiss", ""); x.innerHTML = ICON_X;
    d.appendChild(x);
    /* the words and the buttons in one item beside the × (as the server's strips, collection.py
       strip()): a direct button would turn the strip's wrap on and drop the words under the × */
    var body = document.createElement("div");
    body.className = "nt";
    body.appendChild(document.createTextNode(text));
    (actions || []).forEach(function (a) { body.appendChild(document.createTextNode(" ")); body.appendChild(a); });
    d.appendChild(body);
    notes.insertBefore(d, notes.firstChild);
    return d;
  }
  /* what a failed change says: the one wording for signed out (with Sign in) and no connection
     (static/fetchjson.js), Archidekt's own reason otherwise */
  function failed(d, name) {
    var what = (name || "the card") + " was not changed";
    var why = d && d.error;
    if (why === "unavailable" || why === "rate_limited" || why === "busy") { strip(name + " was not changed. " + NOT_ANSWERING, "error"); return; }
    var pr = Fetch.problem(d, what);
    strip(pr.signedOut || pr.noConnection ? pr.text : name + " was not changed. " + pr.text, "error", pr.actions);
  }
  /* a strip marked data-remember stays closed on this browser once closed (the price-sort note,
     which otherwise came back on every visit until the daily price check: re-audit N10) */
  var REMEMBER = "mtg.binder.closed.";
  document.addEventListener("click", function (e) {
    var x = e.target.closest ? e.target.closest("#col-notes [data-dismiss]") : null;
    if (!x) return;
    var n = x.closest(".notice");
    if (!n) return;
    var key = n.getAttribute("data-remember");
    if (key) { try { localStorage.setItem(REMEMBER + key, "1"); } catch (err) { /* no storage */ } }
    n.remove();
  });
  $$("#col-notes .notice[data-remember]").forEach(function (n) {
    try { if (localStorage.getItem(REMEMBER + n.getAttribute("data-remember"))) n.remove(); } catch (err) { /* no storage */ }
  });
  var Fetch = window.MtgFetch;
  // never rejects: a lost connection or an ended session comes back as {ok: false} with the words
  function request(method, url, body) {
    return Fetch.answer(url, { method: method, body: body === undefined ? undefined : JSON.stringify(body), csrf: CSRF });
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
    // "All N entries shown." under the list follows the count too
    var line = $(".loadmore p.muted");
    // (ui.all_shown: "1 entry shown." for one, never "All 1")
    if (line && /^(All )?[\d,]+ (entry|entries) shown\.$/.test(line.textContent.trim())) {
      line.textContent = n === 1 ? "1 entry shown." : "All " + n.toLocaleString() + " entries shown.";
    }
  }
  /* -- Remove: at once, with Undo (QOL-01, 0.7.46 audit QG-09) ------------------------------
     − on the last copy, the hidden Remove form and the Details box's button all remove the row
     at once; the strip offers Undo for as long as it stays, which adds the row back with its
     copies, printing, finish, condition, language, price paid and Binder tags (the row's tags by
     name, set on the row that came back). */
  function gone(item, removed) {
    var name = nameOf(item);
    var next = item.nextElementSibling;
    item.remove();
    bumpCount(-1);
    var undo = document.createElement("button");
    undo.type = "button"; undo.className = "btn small"; undo.textContent = "Undo";
    undo.addEventListener("click", function () { putBack(item, removed, next, undo); });
    said("Removed from your binder.", removed ? [undo] : []);
    if (list && !$("li[data-id]", list)) list.hidden = true;  // the page's empty state comes with the next load
    return name;
  }
  /* what a removal (and its Undo) says: the shared toast at the foot of the screen, where the
     row that was pressed is in view on a phone, with Undo for 15 s; the strip at the top of the
     page without the shared menus */
  function said(text, actions) {
    var Menu = window.MtgMenu;
    if (Menu && Menu.toast) { Menu.toast(text, { ok: true, actions: actions || [], ms: 15000 }); say(text); return; }
    strip(text, "ok", actions);
  }
  function putBack(item, removed, next, undo) {
    var name = removed.name || nameOf(item);
    undo.disabled = true;
    var spec = { name: name, quantity: removed.quantity || 1, finish: removed.finish || "nonfoil", condition: removed.condition || "", language: removed.lang || "EN" };
    if (removed.set) spec.set_code = removed.set;
    if (removed.collector_number) spec.collector_number = removed.collector_number;
    request("POST", "/collection/api/add", { items: [spec], source: "manual" }).then(function (d) {
      var row = d.ok && d.added && d.added[0];
      if (!row) { undo.disabled = false; failed(d.ok ? { message: "Archidekt did not take the card back." } : d, name); return; }
      item.setAttribute("data-id", row.id);
      $$("button", item).forEach(function (b) { b.disabled = false; });
      $$("output", item).forEach(function (o) { o.textContent = row.quantity; });
      if (list) {
        list.hidden = false;
        if (next && next.parentNode === list) list.insertBefore(item, next); else list.appendChild(item);
      }
      bumpCount(1);
      // the price paid and the Binder tags go on the row that came back (the add has no field for them)
      var paid = removed.purchase_price, rest = { name: name };
      if (!(paid === null || paid === undefined || paid === "")) rest.purchase_price = String(paid);
      if (removed.tags && removed.tags.length) rest.tags = removed.tags.slice();
      if (!("purchase_price" in rest) && !("tags" in rest)) { said(name + " is back in your binder."); return; }
      request("POST", "/collection/api/rows/" + encodeURIComponent(row.id), rest).then(function (u) {
        if (u.ok) { said(name + " is back in your binder."); return; }
        var lost = "tags" in rest && "purchase_price" in rest ? "the price you paid and its tags" : "tags" in rest ? "its tags" : "the price you paid";
        strip(name + " is back in your binder, without " + lost + ". " + Fetch.problem(u, "they were not set").text, "error", Fetch.problem(u).actions);
      });
    });
  }
  function askRemove(item) { return doRemove(item); }
  function doRemove(item) {
    var id = item.getAttribute("data-id");
    var name = nameOf(item);
    $$("button", item).forEach(function (b) { b.disabled = true; });
    return request("DELETE", "/collection/api/rows/" + encodeURIComponent(id) + "?name=" + encodeURIComponent(name)).then(function (d) {
      if (!d.ok) { failed(d, name); $$("button", item).forEach(function (b) { b.disabled = false; }); return false; }
      gone(item, d.removed || null);
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
      if (action !== "inc" && action !== "dec") { form.submit(); return; }
      var name = nameOf(item);
      $$("form.qty button", item).forEach(function (b) { b.disabled = true; });
      /* one more or one fewer than Archidekt holds now: another tab's presses add up, not overwrite */
      request("POST", "/collection/api/rows/" + encodeURIComponent(id), { delta: action === "inc" ? 1 : -1, name: name }).then(function (d) {
        $$("form.qty button", item).forEach(function (b) { b.disabled = false; });
        if (!d.ok) { failed(d, name); return; }
        if (!d.row || d.row.quantity === 0) { gone(item); return; }
        $$("output", item).forEach(function (o) { o.textContent = d.row.quantity; });
        say(name + ": " + d.row.quantity + (d.row.quantity === 1 ? " copy" : " copies"));
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
    var ticks = $$("input[data-tag]", detailsBox);
    if (ticks.length) {
      /* the member's binder tags as boxes: tick the row's own */
      var low = tags.map(function (t) { return String(t).toLowerCase(); });
      ticks.forEach(function (c) { c.checked = low.indexOf(c.getAttribute("data-tag").toLowerCase()) !== -1; });
    } else if (tagbox && line) {
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
  /* a row's tag chips (collection.tag_chips): the colours come with the list (data-tag-colors) */
  var TAG_CHIPS = 4, tagColors = null;
  function colorOf(name) {
    if (tagColors === null) {
      tagColors = {};
      try { tagColors = JSON.parse((list && list.getAttribute("data-tag-colors")) || "{}"); } catch (err) { tagColors = {}; }
    }
    var low = String(name).toLowerCase();
    for (var k in tagColors) if (k.toLowerCase() === low) return tagColors[k];
    return "";
  }
  function drawChips(item, tags) {
    var row = $(".tagrow", item);
    if (!tags.length) { if (row) row.remove(); return; }
    if (!row) {
      row = document.createElement("span");
      row.className = "tagrow";
      var home = $(".meta", item) || $(".cardc > .tx", item);
      if (!home) return;
      home.appendChild(row);
    }
    row.textContent = "";
    tags.slice(0, TAG_CHIPS).forEach(function (t) {
      var chip = document.createElement("span"), sw = document.createElement("i");
      chip.className = "pill tag";
      var c = colorOf(t);
      if (c) chip.style.setProperty("--tagc", c);
      sw.className = "sw"; sw.setAttribute("aria-hidden", "true");
      chip.appendChild(sw); chip.appendChild(document.createTextNode(t));
      row.appendChild(chip);
    });
    if (tags.length > TAG_CHIPS) {
      var more = document.createElement("span");
      more.className = "pill tag more"; more.title = tags.slice(TAG_CHIPS).join(", ");
      more.textContent = "+" + (tags.length - TAG_CHIPS);
      row.appendChild(more);
    }
  }
  function applyRow(item, row) {
    item.setAttribute("data-fin", row.finish || "nonfoil");
    item.setAttribute("data-cond", row.condition || "");
    item.setAttribute("data-lang", row.lang || "EN");
    item.setAttribute("data-paid", row.purchase_price == null ? "" : row.purchase_price);
    if (row.tags) { item.setAttribute("data-tags", JSON.stringify(row.tags)); drawChips(item, row.tags); }
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
    // the facts as the page draws them (ui.facts): one span each, the dots between them drawn,
    // and the price kept where the line has one
    function facts(el, parts) {
      var pr = $(":scope > .pr", el) || $(".ui-dots > span > .pr", el), line = document.createElement("span");
      line.className = "tx ui-dots balanced";
      parts.filter(Boolean).forEach(function (p) {
        var s = document.createElement("span"); s.textContent = p; line.appendChild(s);
      });
      if (pr && !pr.classList.contains("lgh")) {
        var last = document.createElement("span"); last.appendChild(pr); line.appendChild(last); pr = null;
      }
      el.textContent = "";
      el.appendChild(line);
      if (pr) el.appendChild(pr);
    }
    var sub = $(".sub", item);
    if (sub) facts(sub, [item.getAttribute("data-setline"), item.getAttribute("data-setname"), fin, COND[cond] || cond || "Not set", row.lang_name || lang]);
    var sm = $(".smline", item);
    if (sm) facts(sm, [item.getAttribute("data-setline"), fin, cond || "Not set", row.lang_name || lang]);
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
        name: nameOf(item),
        finish: form.elements.finish.value,
        condition: form.elements.condition.value,
        language: form.elements.language.value,
        purchase_price: form.elements.purchase_price.value
      };
      var ticks = $$("input[data-tag]", form);
      if (ticks.length) body.tags = ticks.filter(function (c) { return c.checked; }).map(function (c) { return c.getAttribute("data-tag"); });
      var name = nameOf(item);
      var btns = $$("button", detailsBox);
      btns.forEach(function (b) { b.disabled = true; });
      request("POST", "/collection/api/rows/" + encodeURIComponent(form.getAttribute("data-id")), body).then(function (d) {
        btns.forEach(function (b) { b.disabled = false; });
        if (!d.ok) { failed(d, name); return; }
        applyRow(item, d.row || {});
        if (window.MtgBox) window.MtgBox.close();
        strip("Card details saved to your binder.", "ok");
      });
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

  /* -- the panel: the name box filters as you type; drop-downs apply on change ------------- */
  var listform = $("#listform");
  var nomatch = $(".col-nomatch");
  /* the name the rows on the page were read for ("" for the whole binder) */
  var listQuery = (($("#col-q") || {}).value || "").trim().toLowerCase();
  /* the rows already on the page, at once; "No cards match" only when they are every row that
     could match (all loaded, read for a name the new one contains) */
  function narrow(q) {
    if (!list) return;
    q = q.trim().toLowerCase();
    var any = false;
    $$("[data-name]", list).forEach(function (li) {
      var ok = !q || (li.getAttribute("data-name") || "").indexOf(q) !== -1;
      li.hidden = !ok;
      if (ok) any = true;
    });
    var all = !$(".loadmore [data-load-more]") && q.indexOf(listQuery) !== -1;
    if (nomatch) nomatch.hidden = any || !all || !$("[data-name]", list);
  }
  /* then, once typing stops, the whole binder: Archidekt's own name filter, with the count */
  var searchSeq = 0, searchTimer = null;
  function search() {
    var sent = new URLSearchParams(new FormData(listform));
    sent.delete("offset");
    if (!sent.get("q")) sent.delete("q");
    if (!sent.get("tag")) sent.delete("tag");
    /* the sort goes with every ask (a pick of the default is remembered as such, prefs.py); the
       defaults stay out of the address, as the page's own links leave them */
    var params = new URLSearchParams(sent);
    if (params.get("sort") !== "edition") { params.delete("sort"); params.delete("dir"); }
    else if (params.get("dir") === "desc") params.delete("dir");
    var mine = ++searchSeq;
    if (list) list.setAttribute("aria-busy", "true");
    Fetch.answer("/collection/api/rows?" + sent.toString()).then(function (d) {
        if (mine !== searchSeq) return;
        if (list) list.removeAttribute("aria-busy");
        if (!d.ok) throw d;
        if (list) { list.innerHTML = d.html; list.hidden = !d.total; }
        if (window.MtgLoadMore) window.MtgLoadMore.reset();  // a new list: no Load more pages yet
        var c = $("#col-count");
        if (c) c.textContent = d.count_text;
        var more = $(".loadmore");
        if (more && d.more) {
          var tmp = document.createElement("div");
          tmp.innerHTML = d.more;
          more.parentNode.replaceChild(tmp.firstChild, more);
        }
        if (nomatch) nomatch.hidden = d.total > 0;
        listQuery = (params.get("q") || "").trim().toLowerCase();
        if (!/[?&]view=/.test(location.search)) params.delete("view");
        var addr = "/collection" + (params.toString() ? "?" + params.toString() : "");
        if (window.MtgSearchPanel) window.MtgSearchPanel.address(addr);
        else try { history.replaceState(history.state, "", addr); } catch (err) { /* keep the address */ }
        say(d.count_text + (params.get("q") ? " match “" + params.get("q") + "”." : "."));
      }).catch(function (d) {
        if (mine !== searchSeq) return;
        if (list) list.removeAttribute("aria-busy");
        var pr = Fetch.problem(d, "the rows could not be read");
        strip(pr.signedOut || pr.noConnection ? pr.text : NOT_ANSWERING, "error", pr.actions);
      });
  }
  if (listform) {
    var qbox = $("#col-q", listform);
    if (qbox) qbox.addEventListener("input", function () {
      narrow(qbox.value);
      clearTimeout(searchTimer);
      searchTimer = setTimeout(search, 300);
    });
    listform.addEventListener("submit", function (e) {
      /* Enter in the name box: the same search at once, no page load */
      if (!qbox || document.activeElement !== qbox) return;
      if (window.MtgSearchPanel) window.MtgSearchPanel.closeSheet();
      e.preventDefault();
      clearTimeout(searchTimer);
      search();
    });
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
    var tagf = $("#col-tag", listform);
    if (tagf) tagf.addEventListener("change", submit);
  }

  /* -- an import in the background: its strip follows it, and the page shows the result -------- */
  var importing = $("[data-import]");
  if (importing) {
    var itext = $(".itext", importing);
    var failures = 0;
    var poll = function () {
      fetch("/collection/api/import/" + encodeURIComponent(importing.getAttribute("data-import")),
        { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); }).then(function (d) {
          failures = 0;
          if (!d.ok) { location.replace("/collection"); return; }
          if (itext && itext.textContent !== d.text) { itext.textContent = d.text; }
          if ((d.state === "done" || d.state === "failed") && d.next) { location.replace(d.next); return; }
          setTimeout(poll, 1500);
        }).catch(function () { failures += 1; setTimeout(poll, Math.min(15000, 1500 * (failures + 1))); });
    };
    setTimeout(poll, 1000);
  }

  /* -- the Binder's value being worked out: its widget follows the read and shows the result -- */
  var valueReading = $("[data-value-reading]");
  if (valueReading) {
    var valueTries = 0;
    var valuePoll = function () {
      fetch("/collection/api/value", { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.json(); }).then(function (d) {
          valueTries = 0;
          var wdg = valueReading.closest(".wdg");
          if (!d.ok || !wdg) return;
          if (d.reading) { setTimeout(valuePoll, 2000); return; }
          if (d.html) {
            var tmp = document.createElement("div");
            tmp.innerHTML = d.html;
            if (tmp.firstElementChild) wdg.replaceWith(tmp.firstElementChild);
          }
          var live = $("#live");
          if (live) live.textContent = "Your binder's value is worked out.";
        }).catch(function () { valueTries += 1; setTimeout(valuePoll, Math.min(15000, 2000 * (valueTries + 1))); });
    };
    setTimeout(valuePoll, 1500);
  }

  /* -- Load more: the next rows, appended ---------------------------------------------------- */
  document.addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest(".loadmore [data-load-more]") : null;
    if (!a || !list || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    var wrap = a.closest(".loadmore");
    var href = a.getAttribute("href").replace(/^\/collection(\?|$)/, "/collection/api/rows$1");
    a.setAttribute("aria-busy", "true"); a.setAttribute("aria-disabled", "true");
    Fetch.answer(href).then(function (d) {
      if (!d.ok) throw d;
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
      if (window.MtgLoadMore) window.MtgLoadMore.loaded();
    }).catch(function (d) {
      a.removeAttribute("aria-busy"); a.removeAttribute("aria-disabled");
      // the one wording every Load more uses (static/loadmore.js), in the page's strip
      var pr = window.MtgLoadMore ? window.MtgLoadMore.failed(d, { inline: true }) : Fetch.problem(d, "nothing more could be loaded");
      strip(pr.text, "error", pr.actions);
    });
  });

  // -- Select and the bulk bar (0.7.46 audit QG-17; re-audit N03) -------------------------------
  // Select opens the select mode: the tick boxes show (hidden otherwise, less clutter), nothing is
  // ticked, and the bar shows (its count, Select all, Condition…, Finish…, Tags…, Remove,
  // Cancel); the tick boxes and the boxes' fields belong to the one hidden form (#col-bulk), so a
  // press sends the ticked cards with the box's choice. Select all ticks every card on screen
  // (the ones the name box left showing), or clears them all once they all are. Cancel, or Select
  // again, closes the mode. Without this script there is no Select button, the tick boxes always
  // show, and the boxes open as pages of their own.
  var section = $(".collection");
  function selecting() { return !!section && section.classList.contains("selecting"); }
  function boxes() { return $$(".collection #cards .chk input[name=sel]"); }
  function ticked() { return boxes().filter(function (b) { return b.checked; }); }
  function shown() { return boxes().filter(function (b) { var li = b.closest("li"); return li && !li.hidden; }); }
  function redraw() {
    var n = ticked().length, bar = $(".collection .ui-bar[aria-label='Selected cards']");
    boxes().forEach(function (b) { var li = b.closest("li"); if (li) li.classList.toggle("sel", b.checked); });
    if (bar) {
      bar.hidden = !selecting();
      var c = $(".count", bar), num = c ? $(".n", c) : null;
      if (num) num.textContent = n; else if (c) c.textContent = n + " selected";
      var on = shown(), all = $(".col-all", bar);
      if (all) all.textContent = n > 0 && on.length > 0 && on.every(function (b) { return b.checked; }) ? "Select none" : "Select all";
    }
    var sel = $(".collection .col-select");
    if (sel) sel.setAttribute("aria-pressed", selecting() ? "true" : "false");
    $$("[data-bulk-count]").forEach(function (w) { w.textContent = n === 1 ? "The selected card" : "The " + n + " selected cards"; });
    $$("#col-bulk-cond p, #col-bulk-finish p, #col-bulk-tags p").forEach(function (w) {
      var t = w.childNodes[1];
      if (t && t.nodeType === 3) t.textContent = t.textContent.replace(/^ gets? /, n === 1 ? " gets " : " get ");
    });
  }
  function setAll(on) { shown().forEach(function (b) { b.checked = on; }); if (!on) boxes().forEach(function (b) { b.checked = false; }); redraw(); }
  function mode(on) {
    if (!section) return;
    section.classList.toggle("selecting", on);
    if (!on) setAll(false); else redraw();
    say(on ? "Select is on: tick the cards, then pick an action." : "Select is off.");
  }
  var selectBtn = $(".collection .col-select");
  if (selectBtn) selectBtn.hidden = false;
  if (section && selectBtn) section.classList.add("selectable");
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.name === "sel" && t.closest && t.closest(".collection #cards")) redraw();
  });
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var sel = e.target.closest ? e.target.closest(".collection .col-select") : null;
    if (sel) { mode(!selecting()); return; }
    var bar = e.target.closest ? e.target.closest(".collection .ui-bar[aria-label='Selected cards']") : null;
    if (!bar) return;
    if (e.target.closest(".col-clear")) { mode(false); if (selectBtn) selectBtn.focus(); return; }
    if (e.target.closest(".col-all")) { var on = shown(); setAll(!(on.length && on.every(function (b) { return b.checked; }))); return; }
    var opener = e.target.closest("a[data-box-open]");
    if (opener && ticked().length === 0) {
      e.preventDefault();
      var dd = opener.closest("details.dd[open]");
      if (dd) dd.removeAttribute("open");
      var Menu = window.MtgMenu;
      if (Menu && Menu.toast) Menu.toast("Tick a card first.", { error: true }); else say("Tick a card first.");
    }
  }, true);
  var bulkForm = $("#col-bulk");
  if (bulkForm) bulkForm.addEventListener("submit", function (e) {
    if (ticked().length === 0) {
      e.preventDefault();
      var Menu = window.MtgMenu;
      if (Menu && Menu.toast) Menu.toast("Tick a card first.", { error: true }); else say("Tick a card first.");
      return;
    }
    var bar = $(".collection .ui-bar[aria-label='Selected cards']");
    if (bar) { bar.setAttribute("aria-busy", "true"); $$("a, button", bar).forEach(function (b) { b.setAttribute("aria-disabled", "true"); }); }
  });
  redraw();
})();
