/* The deck, folder, card and row menus and their boxes, on every signed-in page (after
   static/box.js, the box every pop-up shares, window.MtgBox, and static/cardmenu.js, the menu
   layer, window.MtgMenu).

   Deck tiles (My decks, Home, deck search, precons, profiles: li.deck with data-deck-id) and
   folder tiles (li.folder with data-folder-id) get their menu from a right-click, a press and
   hold, their ⋮ or Shift+F10; the tile itself still opens on a click. The menu's boxes call the
   JSON routes of companion_folders.py (rename, move, delete and export a deck; a new folder,
   rename, move and delete a folder) and reload My decks with its ?ok= notice when they are done.
   "+ New folder" in My decks' heading opens the new folder box. On the deck Settings page the
   collaborator forms ask in a box before they are sent (the confirm page's address still works).
   Card rows on the Collection (its own ⋮ stays the Details form; the menu has Details… too), the
   Watchlist and the compare page, and the rows of the Proposals, History, Reports and Watchlist
   tables, get a menu of the actions the row has today. */
(function () {
  "use strict";
  var Menu = window.MtgMenu, Box = window.MtgBox, Card = window.MtgCardMenu;
  if (!Menu || !Box) return;
  var el = Menu.el, item = Menu.item, link = Menu.link, sep = Menu.sep, toast = Menu.toast, post = Menu.post, getJSON = Menu.getJSON;
  var enc = encodeURIComponent;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var TOP = "Top level (no folder)";
  var NO_NET = "No connection; nothing was changed.";

  // -- small shared pieces -----------------------------------------------------------------------
  function hiddenForm(action, fields) {
    var f = el("form");
    f.method = "post";
    f.action = action;
    f.hidden = true;
    var all = { csrf: Menu.csrf() };
    Object.keys(fields || {}).forEach(function (k) { all[k] = fields[k]; });
    Object.keys(all).forEach(function (k) {
      var i = el("input");
      i.type = "hidden"; i.name = k; i.value = all[k];
      f.appendChild(i);
    });
    document.body.appendChild(f);
    return f;
  }
  function submitForm(action, fields) { hiddenForm(action, fields).submit(); }
  function field(labelText, input, id) {
    var wrap = el("div", "field");
    var lab = el("label", null, labelText);
    input.id = id;
    lab.htmlFor = id;
    wrap.appendChild(lab);
    wrap.appendChild(input);
    return wrap;
  }
  function textInput(value, o) {
    o = o || {};
    var i = el("input");
    i.type = "text";
    i.value = value || "";
    i.autocomplete = "off";
    if (o.maxlength) i.maxLength = o.maxlength;
    if (o.placeholder) i.placeholder = o.placeholder;
    return i;
  }
  function cancelButton(text) { return Box.button(text || "Cancel", "btn", null, { cancel: true }); }
  function loading(text) {
    var p = el("p", "muted loading", text || "Reading from Archidekt…");
    p.setAttribute("role", "status");
    return p;
  }
  function failed(box, d) {
    box.busy(false);
    box.note((d && d.message) || NO_NET, true);
  }
  // where My decks goes after a box: the open folder, and the notice the gateway named
  function currentFolder() {
    var i = $("#listform input[name=folder]");
    var v = i ? i.value : new URLSearchParams(location.search).get("folder");
    return v || "";
  }
  function reloadDecks(notice, folder) {
    var f = folder === undefined ? currentFolder() : folder;
    var q = [];
    if (f) q.push("folder=" + enc(f));
    if (notice) q.push("ok=" + enc(notice));
    location.href = "/decks" + (q.length ? "?" + q.join("&") : "");
  }
  var SAID = { saved: "Deck renamed on Archidekt.", moved: "Deck moved on Archidekt." };
  function onDecks() { return location.pathname === "/decks"; }
  // the typed-name gate of the delete boxes: the name, in any capitals, enables the red button
  function typedGate(input, name, button) {
    var check = function () {
      var ok = Menu.norm(input.value) === Menu.norm(name);
      button.disabled = !ok;
      if (ok) button.removeAttribute("data-gate-off"); else button.setAttribute("data-gate-off", "");
    };
    input.addEventListener("input", check);
    check();
  }
  // the folder tree as radios, the top level first; skip: folder ids left out (a folder cannot
  // go inside itself); current: the checked one (null: the top level)
  function treeRadios(info, current, skip, name) {
    var wrap = el("div", "tree");
    wrap.setAttribute("role", "radiogroup");
    wrap.setAttribute("aria-label", "Folders");
    var rows = [{ id: null, name: TOP, depth: 0 }].concat((info.folders || []).filter(function (f) { return f.depth > 0; }));
    var known = rows.some(function (f) { return f.id === current; });
    rows.forEach(function (f) {
      if (f.id !== null && skip[f.id]) return;
      var lab = el("label");
      lab.style.paddingLeft = (0.4 + 1.2 * Math.max(0, f.depth - 1)) + "rem";
      var r = el("input");
      r.type = "radio";
      r.name = name || "mv";
      r.value = f.id === null ? "" : String(f.id);
      r.checked = known ? f.id === current : f.id === null;
      lab.appendChild(r);
      lab.appendChild(Menu.icon(f.id === null ? "decks" : "folder"));
      lab.appendChild(document.createTextNode(" " + f.name));
      wrap.appendChild(lab);
    });
    return wrap;
  }
  function picked(wrap, name) {
    var r = $("input[name=" + (name || "mv") + "]:checked", wrap);
    return r ? (r.value ? Number(r.value) : null) : null;
  }
  // a folder and every folder inside it
  function familyOf(info, fid) {
    var out = {};
    out[fid] = true;
    var more = true;
    while (more) {
      more = false;
      (info.folders || []).forEach(function (f) {
        if (f.parent !== null && f.parent !== undefined && out[f.parent] && !out[f.id]) { out[f.id] = true; more = true; }
      });
    }
    return out;
  }
  function folderSelect(info, current) {
    var s = el("select");
    var rows = (info.folders || []).filter(function (f) { return f.depth > 0; });
    var o = el("option", null, TOP);
    o.value = "";
    s.appendChild(o);
    rows.forEach(function (f) {
      var op = el("option", null, "   ".repeat(Math.max(0, f.depth - 1)) + f.name);
      op.value = String(f.id);
      if (current !== null && current !== undefined && String(current) === String(f.id)) op.selected = true;
      s.appendChild(op);
    });
    return s;
  }

  // -- deck boxes --------------------------------------------------------------------------------
  function renameDeck(d) {
    var input = textInput(d.name, { maxlength: 200 });
    var ok = Box.button("Rename", "primary", function () {
      var name = input.value.trim();
      if (!name) { box.note("Type a name.", true); input.focus(); return; }
      box.busy(true);
      post("/decks/api/decks/" + enc(d.id) + "/rename", { name: name }).then(function (r) {
        if (!r.ok) return failed(box, r);
        if (onDecks()) return reloadDecks(r.notice || "saved");
        $$("li.deck[data-deck-id='" + d.id + "'] .name").forEach(function (n) { n.textContent = r.name || name; n.title = r.name || name; });
        d.el.setAttribute("data-deck-name", r.name || name);
        box.close();
        toast(SAID[r.notice] || SAID.saved, { ok: true });
      }, function () { failed(box); });
    });
    var box = Box.open({
      title: "Rename deck",
      body: field("New name", input, "mb-name"),
      actions: [cancelButton(), ok],
      returnFocus: d.focus
    });
    input.select();
  }
  function moveDeck(d) {
    var box = Box.open({ title: "Move “" + d.name + "”", body: loading(), actions: [cancelButton()], returnFocus: d.focus });
    getJSON("/decks/api/folders").then(function (info) {
      if (!info.ok) return failed(box, info);
      var current = d.folder ? Number(d.folder) : null;
      if (current === info.root_id) current = null;
      var tree = treeRadios(info, current, {});
      var ok = Box.button("Move", "primary", function () {
        box.busy(true);
        post("/decks/api/decks/" + enc(d.id) + "/move", { folder_id: picked(tree) }).then(function (r) {
          if (!r.ok) return failed(box, r);
          if (onDecks()) return reloadDecks(r.notice || "moved");
          box.close();
          toast(SAID[r.notice] || SAID.moved, { ok: true });
        }, function () { failed(box); });
      });
      box.setBody(tree);
      box.setActions([cancelButton(), ok]);
      box.focusFirst();
    }, function () { failed(box); });
  }
  function deleteDeck(d) {
    var box = Box.open({ title: "Delete “" + d.name + "”?", body: loading(), actions: [cancelButton("Keep it")], returnFocus: d.focus });
    getJSON("/decks/api/decks/" + enc(d.id) + "/delete-info").then(function (info) {
      if (!info.ok) return failed(box, info);
      var name = info.name || d.name;
      box.setTitle("Delete “" + name + "”?");
      var body = el("div", "stack");
      var n = info.cards || 0;
      body.appendChild(el("p", null, "This deletes the deck (" + n + " card" + (n === 1 ? "" : "s") + ") from your Archidekt account. "
        + "A snapshot of the deck is kept under History" + (info.backups ? ", and a private copy is made in your backup folder on Archidekt first" : "")
        + ". Archidekt itself has no undo for a deleted deck."));
      var input = textInput("", { maxlength: 200, placeholder: name });
      body.appendChild(field("Type the deck's name to confirm", input, "mb-typed"));
      var ok = Box.button("Delete this deck", "danger", function () {
        box.busy(true);
        post("/decks/api/decks/" + enc(d.id) + "/delete", { typed_name: input.value }).then(function (r) {
          if (!r.ok) return failed(box, r);
          location.href = "/decks?ok=" + enc(r.notice || "deleted");
        }, function () { failed(box); });
      }, { icon: "trash" });
      typedGate(input, name, ok);
      box.setBody(body);
      box.setActions([cancelButton("Keep it"), ok]);
      box.focusFirst();
    }, function () { failed(box); });
  }
  function exportDeck(d) {
    var box = Box.open({ title: "Export “" + d.name + "”", body: loading(), actions: [cancelButton("Close")], wide: true, returnFocus: d.focus });
    getJSON("/decks/api/decks/" + enc(d.id) + "/export").then(function (x) {
      if (!x.ok) return failed(box, x);
      box.setTitle("Export “" + (x.name || d.name) + "”");
      var tabs = [["Archidekt import text", x.archidekt || ""], ["Plain decklist", x.plain || ""]];
      if (x.side) tabs.push(["Sideboard and maybeboard", x.side]);
      tabs.push(["Files", null]);
      var body = el("div", "stack");
      var bar = el("div", "segc");
      bar.setAttribute("role", "tablist");
      var area = el("textarea");
      area.readOnly = true;
      var files = el("div", "actions files");
      (x.files || []).forEach(function (f) {
        var a = el("a", "btn", f.label);
        a.href = f.href;
        a.setAttribute("download", "");
        files.appendChild(a);
      });
      var copy = Box.button("Copy", "btn", function () { Menu.copyAndSay(area.value, "Copied."); }, { icon: "copy" });
      var buttons = [];
      function show(i) {
        buttons.forEach(function (b, k) { b.setAttribute("aria-selected", k === i ? "true" : "false"); b.tabIndex = k === i ? 0 : -1; });
        var text = tabs[i][1];
        // shown or not by style: the page's own display rules would win over the hidden attribute
        area.style.display = text === null ? "none" : "";
        files.style.display = text !== null ? "none" : "";
        copy.style.display = text === null ? "none" : "";
        if (text !== null) { area.value = text; area.setAttribute("aria-label", tabs[i][0]); }
      }
      tabs.forEach(function (t, i) {
        var b = el("button", null, t[0]);
        b.type = "button";
        b.setAttribute("role", "tab");
        b.addEventListener("click", function () { show(i); });
        b.addEventListener("keydown", function (e) {
          if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
            e.preventDefault();
            var k = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
            show(k);
            buttons[k].focus();
          }
        });
        buttons.push(b);
        bar.appendChild(b);
      });
      body.appendChild(bar);
      body.appendChild(area);
      body.appendChild(files);
      show(0);
      box.setBody(body);
      box.setActions([cancelButton("Close"), copy]);
      buttons[0].focus();
    }, function () { failed(box); });
  }
  function compareDeck(d) {
    var box = Box.open({ title: "Compare “" + d.name + "” with", body: loading(), actions: [cancelButton()], returnFocus: d.focus });
    getJSON("/api/decks/mine").then(function (mine) {
      var body = el("div", "stack");
      var s = el("select");
      ((mine && mine.decks) || []).forEach(function (x) {
        if (String(x.id) === String(d.id)) return;
        var o = el("option", null, x.name || "Unnamed deck");
        o.value = String(x.id);
        s.appendChild(o);
      });
      var any = el("option", null, "Any Archidekt deck (paste a link)…");
      any.value = "any";
      s.appendChild(any);
      body.appendChild(field("Deck", s, "mb-cmp"));
      var ref = textInput("", { placeholder: "Archidekt link or deck ID" });
      var refField = field("Archidekt deck", ref, "mb-ref");
      refField.hidden = s.value !== "any";
      body.appendChild(refField);
      s.addEventListener("change", function () { refField.hidden = s.value !== "any"; if (s.value === "any") ref.focus(); });
      var go = Box.button("Compare", "primary", function () {
        var other = s.value === "any" ? ref.value.trim() : s.value;
        if (!other) { box.note("Pick a deck, or paste an Archidekt link or deck ID.", true); return; }
        location.href = "/decks/" + enc(d.id) + "/compare?with=" + enc(other);
      });
      box.setBody(body);
      box.setActions([cancelButton(), go]);
      box.focusFirst();
    }, function () { failed(box); });
  }
  function bookmarkDeck(d) {
    var box = Box.open({
      title: "Bookmark this deck on Archidekt?",
      body: el("p", null, "This adds “" + d.name + "” to your bookmarks on Archidekt."),
      actions: [cancelButton(), Box.button("Bookmark", "primary", function () {
        box.busy(true);
        post("/social/api/decks/" + enc(d.id) + "/bookmark", { on: true }).then(function (r) {
          if (!r.ok) return failed(box, r);
          box.close();
          toast(d.name + " is bookmarked on Archidekt.", { ok: true });
        }, function () { failed(box); });
      }, { icon: "bookmark" })],
      returnFocus: d.focus
    });
  }
  function watchDeck(d) {
    // the Watchlist page's own "Watch deck" form, as a press there
    submitForm("/watchlist", { action: "add_deck", deck: typeof d === "object" ? d.id : d });
  }
  window.MtgDeckWatch = watchDeck; // the deck page's menu (deck.js) reuses it

  // -- the deck menu -----------------------------------------------------------------------------
  function deckOf(node) {
    var li = node && node.closest ? node.closest("li.deck[data-deck-id], .rd[data-deck-id]") : null;
    if (!li) return null;
    var a = $("a[href]", li);
    return {
      el: li, id: li.getAttribute("data-deck-id"), name: li.getAttribute("data-deck-name") || "Unnamed deck",
      own: li.getAttribute("data-own") === "1", folder: li.getAttribute("data-folder") || "",
      href: a ? a.getAttribute("href") : "/decks/" + li.getAttribute("data-deck-id"), focus: a || li
    };
  }
  function deckItems(d) {
    var id = enc(d.id), items = [];
    var archidekt = "https://archidekt.com/decks/" + id;
    items.push(link("Open", "eye", d.href));
    items.push(link("Open in a new tab", "external", d.href, { ext: true }));
    if (d.own) items.push(link("Edit deck", "edit", "/decks/" + id + "/edit"));
    items.push(sep());
    if (!d.own) items.push(item("Clone into my decks", "clone", function () { submitForm("/decks/" + id + "/clone", {}); }));
    items.push(link("Goldfish", "play", "/decks/" + id + "/goldfish"));
    items.push(item("Run full test", "stats", function () { submitForm("/decks/" + id + "/report", {}); }));
    if (d.own) {
      items.push(item("Clone", "clone", function () { submitForm("/decks/" + id + "/clone", {}); }));
      items.push(item("Rename…", "edit", function () { renameDeck(d); }));
      items.push(item("Move to folder…", "folder", function () { moveDeck(d); }));
    }
    items.push(item("Export…", "download", function () { exportDeck(d); }));
    items.push(item("Compare with…", "layers", function () { compareDeck(d); }));
    if (!d.own) items.push(item("Bookmark", "bookmark", function () { bookmarkDeck(d); }));
    if (Menu.enabled()) items.push(item("Watch deck price", "eye", function () { watchDeck(d); }));
    if (d.own) items.push(link("Deck settings", "settings", "/decks/" + id + "/settings"));
    items.push(sep());
    items.push(item("Copy link", "link", function () { Menu.copyAndSay(location.origin + "/decks/" + id, "Link copied."); }));
    items.push(link("Open on Archidekt", "external", archidekt, { ext: true }));
    if (d.own) {
      items.push(sep());
      items.push(item("Delete deck…", "trash", function () { deleteDeck(d); }, { danger: true }));
    }
    return items;
  }
  function openDeckMenu(li, x, y) {
    var d = deckOf(li);
    if (!d) return;
    Menu.open({ head: d.name, label: "Deck: " + d.name, items: deckItems(d), focusBack: d.focus }, x, y);
  }

  // -- folder boxes ------------------------------------------------------------------------------
  function newFolder(inside, focus) {
    var box = Box.open({ title: "New folder", body: loading(), actions: [cancelButton()], returnFocus: focus });
    getJSON("/decks/api/folders").then(function (info) {
      if (!info.ok) return failed(box, info);
      var body = el("div", "stack");
      var name = textInput("", { maxlength: 100, placeholder: "Folder name" });
      name.required = true;
      var where = folderSelect(info, inside);
      body.appendChild(field("Name", name, "mb-fname"));
      body.appendChild(field("Inside", where, "mb-finside"));
      var next = Box.button("Next", "primary", function () {
        var n = name.value.trim();
        if (!n) { box.note("Type a name.", true); name.focus(); return; }
        var parent = where.value ? Number(where.value) : null;
        box.busy(true);
        post("/decks/api/folders/check", { name: n, parent_id: parent }).then(function (c) {
          if (!c.ok) return failed(box, c);
          box.note("");
          box.busy(false);
          var placed = c.top_level ? "your top level" : "“" + c.parent_name + "”";
          box.setTitle("Create this folder?");
          box.setBody(el("p", null, "Create the folder “" + c.name + "” in " + placed + " on Archidekt?"));
          var back = Box.button("Back", "btn", function () {
            box.setTitle("New folder");
            box.setBody(body);
            box.setActions([cancelButton(), next]);
            name.focus();
          });
          var create = Box.button("Create folder", "primary", function () {
            box.busy(true);
            post("/decks/api/folders", { name: c.name, parent_id: parent }).then(function (r) {
              if (!r.ok) return failed(box, r);
              reloadDecks(r.notice || "folder_created");
            }, function () { failed(box); });
          }, { icon: "check" });
          box.setActions([back, create]);
          create.focus();
        }, function () { failed(box); });
      });
      box.setBody(body);
      box.setActions([cancelButton(), next]);
      name.focus();
    }, function () { failed(box); });
  }
  function renameFolder(f) {
    var input = textInput(f.name, { maxlength: 100 });
    var ok = Box.button("Rename", "primary", function () {
      var name = input.value.trim();
      if (!name) { box.note("Type a name.", true); input.focus(); return; }
      box.busy(true);
      post("/decks/api/folders/" + enc(f.id) + "/rename", { name: name }).then(function (r) {
        if (!r.ok) return failed(box, r);
        reloadDecks(r.notice || "folder_renamed");
      }, function () { failed(box); });
    });
    var box = Box.open({ title: "Rename folder", body: field("New name", input, "mb-fname"), actions: [cancelButton(), ok], returnFocus: f.focus });
    input.select();
  }
  function moveFolder(f) {
    var box = Box.open({ title: "Move the folder “" + f.name + "”", body: loading(), actions: [cancelButton()], returnFocus: f.focus });
    getJSON("/decks/api/folders").then(function (info) {
      if (!info.ok) return failed(box, info);
      var me = (info.folders || []).filter(function (x) { return String(x.id) === String(f.id); })[0];
      var current = me && me.parent !== null && me.parent !== info.root_id ? me.parent : null;
      var body = el("div", "stack");
      var tree = treeRadios(info, current, familyOf(info, Number(f.id)));
      body.appendChild(tree);
      body.appendChild(el("p", "muted small", "Everything inside moves with it. A folder can't go inside itself, so it and its own folders are not listed."));
      var ok = Box.button("Move", "primary", function () {
        box.busy(true);
        post("/decks/api/folders/" + enc(f.id) + "/move", { parent_id: picked(tree) }).then(function (r) {
          if (!r.ok) return failed(box, r);
          reloadDecks(r.notice || "folder_moved");
        }, function () { failed(box); });
      });
      box.setBody(body);
      box.setActions([cancelButton(), ok]);
      box.focusFirst();
    }, function () { failed(box); });
  }
  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
  function deleteFolder(f, said) {
    var box = Box.open({ title: "Delete the folder “" + f.name + "”?", body: loading(), actions: [cancelButton("Keep it")], returnFocus: f.focus });
    getJSON("/decks/api/folders/" + enc(f.id) + "/contents").then(function (c) {
      if (!c.ok && c.error === "backup_inside") {
        box.setTitle("“" + f.name + "” can't be deleted here");
        box.setBody(el("p", null, c.message || ""));
        box.setActions([Box.button("OK", "primary", null, { cancel: true })]);
        box.focusFirst();
        return;
      }
      if (!c.ok) return failed(box, c);
      var name = c.name || f.name;
      var body = el("div", "stack");
      if (said) body.appendChild(el("p", "notice warn", said));
      body.appendChild(el("p", null, "This deletes, on Archidekt:"));
      var ul = el("ul");
      ul.appendChild(el("li", null, plural(c.decks, "deck") + ": each gets a snapshot under History first"
        + (c.backups ? ", and a private copy in your backup folder when backups are on" : "")));
      ul.appendChild(el("li", null, plural(c.packages, "card package") + ": no snapshot or copy, " + (c.packages === 1 ? "it is" : "they are") + " lost"));
      ul.appendChild(el("li", null, plural(c.folders, "folder") + " inside it, and the folder itself"));
      body.appendChild(ul);
      body.appendChild(el("p", null, "The gateway makes the snapshots and copies first and stops if any of them fails. Then Archidekt deletes everything in one step, which can't be undone."));
      var input = textInput("", { maxlength: 100, placeholder: name });
      body.appendChild(field("Type the folder's name to confirm", input, "mb-typed"));
      var ok = Box.button("Delete folder", "danger", function () {
        box.busy(true);
        post("/decks/api/folders/" + enc(f.id) + "/delete", { typed_name: input.value, decks: c.decks, packages: c.packages, folders: c.folders }).then(function (r) {
          if (!r.ok) return failed(box, r);
          var line = el("p", "progress");
          line.setAttribute("role", "status");
          line.setAttribute("aria-live", "polite");
          line.textContent = "Saving every deck first…";
          box.setBody(line);
          box.setActions([]);
          // the box stays until the job ends (Esc and the scrim are refused), so its outcome
          // is always seen here: My decks with the notice, or what went wrong
          box.lock(true);
          watchJob(r.job, line, box, f);
        }, function () { failed(box); });
      }, { icon: "trash" });
      typedGate(input, name, ok);
      box.setBody(body);
      box.setActions([cancelButton("Keep it"), ok]);
      box.focusFirst();
    }, function () { failed(box); });
  }
  // the delete runs as a job: its step line until it is done, then My decks with the notice
  function watchJob(job, line, box, f) {
    function ended(text) {
      // the job is over: the box may be closed again, with the outcome on it
      box.lock(false);
      line.textContent = text;
      line.className = "notice error";
      box.setActions([cancelButton("Close")]);
      box.focusFirst();
    }
    function tick() {
      getJSON("/decks/api/folders/jobs/" + enc(job.id)).then(function (r) {
        if (!r.ok) { ended(r.message || NO_NET); return; }
        var j = r.job || {};
        if (j.state === "done") { reloadDecks(j.notice || "folder_deleted"); return; }
        if (j.state === "failed") {
          if (j.error === "changed") {
            box.close();
            deleteFolder(f, j.message || "What is inside this folder changed, so nothing was deleted. Here is what it holds now.");
            return;
          }
          ended(j.message || "The folder could not be deleted.");
          return;
        }
        if (j.step) line.textContent = j.step;
        setTimeout(tick, 1500);
      }, function () { setTimeout(tick, 3000); });
    }
    setTimeout(tick, 1500);
  }

  // -- the folder menu ---------------------------------------------------------------------------
  function folderOf(node) {
    var li = node && node.closest ? node.closest("li.folder[data-folder-id]") : null;
    if (!li) return null;
    var a = $("a[href]", li);
    return {
      el: li, id: li.getAttribute("data-folder-id"), name: li.getAttribute("data-folder-name") || "",
      backup: li.hasAttribute("data-backup"),  // the gateway's backup folder: never moved or deleted
      href: a ? a.getAttribute("href") : "/decks?folder=" + li.getAttribute("data-folder-id"), focus: a || li
    };
  }
  function folderItems(f) {
    var id = enc(f.id);
    var items = [
      link("Open", "folder", f.href),
      link("Open in a new tab", "external", f.href, { ext: true }),
      sep(),
      item("New folder inside…", "plus", function () { newFolder(f.id, f.focus); }),
      item("Rename…", "edit", function () { renameFolder(f); })
    ];
    if (!f.backup) items.push(item("Move to folder…", "folder", function () { moveFolder(f); }));
    items.push(item("Copy link", "link", function () { Menu.copyAndSay(location.origin + "/decks?folder=" + id, "Link copied."); }));
    items.push(link("Open on Archidekt", "external", "https://archidekt.com/folders/" + id, { ext: true }));
    if (!f.backup) {
      items.push(sep());
      items.push(item("Delete folder…", "trash", function () { deleteFolder(f); }, { danger: true }));
    }
    return items;
  }
  function openFolderMenu(li, x, y) {
    var f = folderOf(li);
    if (!f) return;
    Menu.open({ head: f.name, label: "Folder: " + f.name, items: folderItems(f), focusBack: f.focus }, x, y);
  }

  // -- card rows elsewhere: the Collection, the Watchlist, the compare page ----------------------
  function cardMenuBase(name, o) {
    o = o || {};
    var spec = {
      name: name,
      onOpen: o.onOpen || function () { location.href = "/cards?q=" + enc(name); },
      addHref: o.noAdd ? null : "/cards?q=" + enc(name),
      collect: !o.noCollect,
      watch: Menu.enabled() && !o.noWatch ? { watched: !!o.watched, onWatch: function () { Card.watch({ name: name }, { was: !!o.watched }).catch(function () {}); } } : null,
      before: o.before || [], after: o.after || [], focusBack: o.focus
    };
    return spec;
  }
  function collectionRowOf(node) {
    var li = node && node.closest ? node.closest("ul.colllist li.row[data-id], ul.collgrid li.c[data-id]") : null;
    return li;
  }
  function openCollectionMenu(li, x, y) {
    var nameEl = $(".name", li), name = nameEl ? nameEl.childNodes[0].textContent.trim() : "";
    var thumb = $(".thumbbtn", li), details = $("details.rowmenu", li), rm = $("form.rm button", li);
    var out = $("form.qty output", li), minus = $("form.qty button[value=dec]", li), plus = $("form.qty button[value=inc]", li);
    var qty = null;
    if (out && minus && plus) {
      qty = el("div", "item mqty");
      qty.setAttribute("role", "none");
      qty.appendChild(el("span", "lbl", "Quantity"));
      var n = el("b", "n", out.textContent);
      var step = function (btn, text, by) {
        var b = item(text, null, function () { btn.click(); setTimeout(function () { n.textContent = out.textContent; }, 400); }, { keepOpen: true, cls: "step" });
        b.setAttribute("aria-label", (by < 0 ? "One fewer " : "One more ") + name);
        return b;
      };
      qty.appendChild(step(minus, "−", -1));
      qty.appendChild(n);
      qty.appendChild(step(plus, "+", 1));
    }
    var before = [];
    if (qty) before.push(qty);
    if (details) before.push(item("Details…", "edit", function () {
      details.open = true;
      var f = $("select, input", details);
      if (f) f.focus();
    }));
    var after = [];
    if (rm) { after.push(sep()); after.push(item("Remove from collection", "x", function () { rm.click(); }, { danger: true })); }
    Card.open(cardMenuBase(name, {
      onOpen: thumb ? function () { thumb.click(); } : null, noCollect: true, before: before, after: after, focus: thumb || li
    }), x, y);
  }
  function alertBox(tr, inRow, focusBack) {
    var card = $(".wcard[data-card]", tr), name = card ? card.getAttribute("data-card") : "this card";
    var form = el("form");
    form.method = "post";
    form.action = inRow.getAttribute("action") || location.pathname;
    form.id = "wx-alert-box-f";
    ["csrf", "action", "watch_id"].forEach(function (n) {
      var src = inRow.elements[n];
      if (!src) return;
      var h = el("input"); h.type = "hidden"; h.name = n; h.value = src.value; form.appendChild(h);
    });
    var stack = el("div", "stack");
    [["alert_pct", "Moves more than (%)"], ["alert_above", "Rises above"], ["alert_below", "Falls below"]].forEach(function (f) {
      var src = inRow.elements[f[0]];
      var wrap = el("div", "field"), lab = el("label", null, f[1]), input = el("input");
      input.type = "text"; input.inputMode = "decimal"; input.name = f[0]; input.id = "wxb-" + f[0];
      input.value = src ? src.value : "";
      lab.htmlFor = input.id;
      wrap.appendChild(lab); wrap.appendChild(input); stack.appendChild(wrap);
    });
    var hint = $(".ui-hint", inRow);
    if (hint) stack.appendChild(el("p", "ui-hint", hint.textContent));
    form.appendChild(stack);
    var save = Box.button("Save alert", "primary", function () {
      if (form.requestSubmit) form.requestSubmit(); else form.submit();
    }, { icon: "bell" });
    Box.open({ title: "Price alert: " + name, body: form, actions: [Box.button("Cancel", "btn", null, { cancel: true }), save], returnFocus: focusBack });
  }
  function watchRowOf(node) {
    var tr = node && node.closest ? node.closest(".tr[id^='wr-']") : null;
    return tr;
  }
  function openWatchMenu(tr, x, y) {
    var key = tr.id.slice(3);
    var wx = document.getElementById("wx-" + key);
    var more = $(".act .more", tr);
    var alert = item("Price alert…", "bell", function () {
      // a card row: its alert form as a box (M16), posting as the in-row form does; a row that is
      // already open, or a deck row (no alert fields), opens the row's details instead
      var inRow = wx ? $("form.wx-alert", wx) : null;
      if (inRow && Box && !(more && more.getAttribute("aria-expanded") === "true")) { alertBox(tr, inRow, more || tr); return; }
      if (more && more.getAttribute("aria-expanded") !== "true") more.click();
      var f = wx ? $("input[name=alert_pct], .wx-alert input, .wx-alert a", wx) : null;
      if (f) f.focus();
    });
    var rmBtn = wx ? $("form.rm button, button[form^='rm-']", wx) : null;
    var remove = rmBtn ? item("Remove", "x", function () { rmBtn.click(); }, { danger: true }) : null;
    var card = $(".wcard[data-card]", tr);
    if (card) {
      var name = card.getAttribute("data-card");
      Card.open(cardMenuBase(name, {
        onOpen: function () { card.click(); }, noWatch: true, before: [alert], after: remove ? [sep(), remove] : [], focus: card
      }), x, y);
      return;
    }
    var title = $(".title .t", tr), open = wx ? $("a[href^='/decks/']", wx) : null;
    var name2 = title ? title.textContent.trim() : "Deck";
    var items = [];
    if (open) items.push(link("Open", "eye", open.getAttribute("href")));
    items.push(alert);
    if (remove) { items.push(sep()); items.push(remove); }
    Menu.open({ head: name2, label: "Deck: " + name2, items: items, focusBack: more || tr }, x, y);
  }
  function cardLinkOf(node) {
    if (!node || !node.closest) return null;
    var b = node.closest("button.cardlink[data-card]");
    if (b) return b;
    // the row's ⋮ beside the card button (the Compare page's rows)
    var row = node.closest(".kebab") ? node.closest(".tr, li") : null;
    return row ? $("button.cardlink[data-card]", row) : null;
  }
  function openCardLinkMenu(b, x, y) {
    var name = b.getAttribute("data-card");
    Card.open(cardMenuBase(name, { onOpen: function () { b.click(); }, focus: b }), x, y);
  }

  // -- the list pages' rows: Proposals, History, Reports ------------------------------------------
  function proposalRowOf(node) {
    var tr = node && node.closest ? node.closest(".pr-table .tr") : null;
    return tr && !tr.classList.contains("group") && !tr.classList.contains("th") && $("a[href^='/proposals/']", tr) ? tr : null;
  }
  function openProposalMenu(tr, x, y) {
    var review = $("a[href^='/proposals/']", tr);
    var pick = $("input[name=pick]", tr);
    var name = $(".title .t", tr) ? $(".title .t", tr).textContent.replace(/\s+/g, " ").trim() : "Change";
    var deck = tr.getAttribute("data-deck") || (pick && pick.getAttribute("data-group") !== "untied" ? pick.getAttribute("data-group") : "");
    var items = [link("Review", "eye", review.getAttribute("href"))];
    if (pick) {
      var risk = pick.getAttribute("data-risk") || "";
      var form = pick.form;
      var send = function (action) {
        $$("input[name=pick]", form).forEach(function (p) { p.checked = p === pick; });
        var a = el("input");
        a.type = "hidden"; a.name = "action"; a.value = action;
        form.appendChild(a);
        form.submit();
      };
      // only a change a press here may apply: a deck delete, or a change that needs its review
      // page, is approved there
      if (form && (risk === "low" || risk === "high")) items.push(item("Apply", "check", function () { send("apply"); }));
      if (form) items.push(item("Reject", "x", function () { send("reject"); }, { danger: true }));
    }
    if (deck) { items.push(sep()); items.push(link("Open the deck", "decks", "/decks/" + enc(deck))); }
    Menu.open({ head: name, label: "Change: " + name, items: items, focusBack: review }, x, y);
  }
  function historyRowOf(node) {
    var tr = node && node.closest ? node.closest(".hy-table .tr") : null;
    return tr && !tr.classList.contains("group") && !tr.classList.contains("th") && !tr.classList.contains("daygroup") && $(".title", tr) ? tr : null;
  }
  function openHistoryMenu(tr, x, y) {
    var name = $(".title .t", tr) ? $(".title .t", tr).textContent.trim() : "";
    var details = $(".act a[href*='open=']", tr), restore = $("form.hy-restore button", tr);
    var report = $("a[href^='/history/reports/']", tr), deck = $(".title a.t[href^='/decks/']", tr);
    var items = [];
    if (details) items.push(link("Details", "eye", details.getAttribute("href")));
    if (restore) items.push(item("Restore (review first)", "undo", function () { restore.click(); }));
    if (report) items.push(link("Open report", "report", report.getAttribute("href")));
    if (deck) { items.push(sep()); items.push(link("Open the deck", "decks", deck.getAttribute("href"))); }
    Menu.open({ head: name, label: "History: " + name, items: items, focusBack: details || report || tr }, x, y);
  }
  function reportRowOf(node) {
    var tr = node && node.closest ? node.closest(".rp-t .tr") : null;
    return tr && $("a[href^='/history/reports/']", tr) ? tr : null;
  }
  function openReportMenu(tr, x, y) {
    var open = $(".title a[href^='/history/reports/']", tr) || $("a[href^='/history/reports/']", tr);
    var href = open.getAttribute("href");
    var name = $(".title .t a", tr) ? $(".title .t a", tr).textContent.trim() : "Report";
    var deck = tr.getAttribute("data-deck") || ($(".title", tr) || tr).getAttribute("data-deck");
    var items = [
      link("Open", "eye", href),
      link("Play more games", "play", href + "#forge-more"),
      item("Copy as Markdown", "copy", function () {
        fetch(href + "/export.md", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : Promise.reject(new Error(String(r.status))); })
          .then(function (t) { Menu.copyAndSay(t, "Report copied as Markdown."); }, function () { toast("The report could not be read.", { error: true }); });
      })
    ];
    if (deck) { items.push(sep()); items.push(link("Open the deck", "decks", "/decks/" + enc(deck))); }
    Menu.open({ head: name, label: "Report: " + name, items: items, focusBack: open }, x, y);
  }

  // -- the ways in, for every kind at once ------------------------------------------------------
  var KINDS = [
    { of: deckOf, open: openDeckMenu, el: function (d) { return d.el; } },
    { of: folderOf, open: openFolderMenu, el: function (f) { return f.el; } },
    { of: collectionRowOf, open: openCollectionMenu },
    { of: watchRowOf, open: openWatchMenu },
    { of: cardLinkOf, open: openCardLinkMenu },
    { of: proposalRowOf, open: openProposalMenu },
    { of: historyRowOf, open: openHistoryMenu },
    { of: reportRowOf, open: openReportMenu }
  ];
  function itemOf(target) {
    if (!target || !target.closest) return null;
    if (target.closest(".ctxmenu, .mtgbox, .cardview, .printpick, details.rowmenu .menu")) return null;
    for (var i = 0; i < KINDS.length; i++) {
      var got = KINDS[i].of(target);
      if (got) { var node = KINDS[i].el ? KINDS[i].el(got) : got; node.__mtgKind = KINDS[i]; return node; }
    }
    return null;
  }
  Menu.bind(document, {
    itemOf: itemOf,
    skip: "button, input, select, textarea, summary, form.qty, form.rm, .wbtn",
    button: ".kebab",
    open: function (node, x, y) { node.__mtgKind.open(node, x, y); }
  });

  // the rows' ⋮: added here so the tables keep their markup
  function decorate() {
    // inside the row's action cell when that is a container; beside the title (never inside its
    // link) when the open action is the link itself, as the rows are a grid of fixed columns
    $$(".hy-table .tr, .rp-t .tr, .tr[id^='wr-'], .pr-table .tr").forEach(function (tr) {
      if ($(".kebab", tr)) return;
      var kind = historyRowOf(tr) || reportRowOf(tr) || watchRowOf(tr) || proposalRowOf(tr);
      if (!kind) return;
      var t = $(".title .t", tr), act = $(".act", tr);
      var k = Menu.kebab("Menu for " + (t ? t.textContent.replace(/\s+/g, " ").trim() : "this row"));
      if (act && act.tagName !== "A") { act.appendChild(k); return; }
      if (!t) return;
      k.classList.add("inline");
      t.parentNode.insertBefore(k, t.nextSibling);
    });
    $$(".home .recent a[data-deck-id]").forEach(function (a) {
      if (a.parentNode.classList.contains("rd")) return;
      var wrap = el("span", "rd");
      wrap.setAttribute("data-deck-id", a.getAttribute("data-deck-id"));
      wrap.setAttribute("data-deck-name", a.getAttribute("data-deck-name") || "");
      wrap.setAttribute("data-own", "1");
      a.parentNode.insertBefore(wrap, a);
      wrap.appendChild(a);
      wrap.appendChild(Menu.kebab("Menu for " + (a.getAttribute("data-deck-name") || "this deck")));
    });
  }
  decorate();
  document.addEventListener("decks:loaded", decorate);

  // -- My decks' heading: + New folder -----------------------------------------------------------
  document.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-new-folder]") : null;
    if (!b) return;
    e.preventDefault();
    newFolder(currentFolder() || null, b);
  });

  // -- the deck Settings page: Save categories asks first when an assistant's version would wait --
  // (modes.hand_edit_confirm: the commander, from a Premier switch changed anywhere or the name or
  // a switch of the Commander category or one holding the commander; or which cards count, from a
  // Not in deck switch or a rename to or from Sideboard or Maybeboard. The words are the backend's,
  // on the section; without scripts the gateway asks on a page of its own)
  var cats = $("#categories[data-confirm]");
  var countsByName = ["sideboard", "maybeboard"];
  if (cats) cats.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form") || $("input[name=confirmed]", form)) return;
    var commander = false, counts = false;
    $$("li[data-cat]", form).forEach(function (li) {
      var name = $("input[type=text]", li), premier = $("input[name^=premier_]", li);
      var notInDeck = $("input[name^=not_in_deck_]", li);
      var renamed = name && name.value.trim() !== name.defaultValue;
      var changed = renamed ||
        $$("input[type=checkbox]", li).some(function (c) { return c.checked !== c.defaultChecked; });
      if ((premier && premier.checked !== premier.defaultChecked) || (changed && li.hasAttribute("data-commander"))) commander = true;
      if ((notInDeck && notInDeck.checked !== notInDeck.defaultChecked) ||
          (renamed && (countsByName.indexOf(name.defaultValue.toLowerCase()) >= 0 ||
                       countsByName.indexOf(name.value.trim().toLowerCase()) >= 0))) counts = true;
    });
    if (!commander && !counts) return;
    e.preventDefault();
    var pre = commander ? "data-confirm" : "data-counts-confirm";
    var box = Box.open({
      title: cats.getAttribute(pre + "-title") || "",
      body: el("p", null, cats.getAttribute(pre) || ""),
      actions: [cancelButton(), Box.button("Save categories", "primary", function () {
        var c = el("input");
        c.type = "hidden"; c.name = "confirmed"; c.value = "1";
        form.appendChild(c);
        box.busy(true);
        form.submit();
      }, { icon: "check" })],
      returnFocus: $("button[name=action]", form)
    });
  });

  // -- the deck Settings page: the collaborator forms ask in a box first ------------------------
  var collab = $("#collaborators[data-deck-name]");
  if (collab) collab.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form") || $("input[name=confirmed]", form)) return;
    var action = ($("input[name=action]", form) || {}).value;
    var who = ($("input[name=username]", form) || {}).value;
    who = (who || "").trim();
    if (!who || (action !== "add" && action !== "remove")) return;
    var listed = action === "add" && $("#collabname", form) ? $("#collabname", form).getAttribute("data-options") : null;
    if (listed) {
      // when the page knows who is followed, someone else goes straight to the gateway, which says why not
      var people = [];
      try { people = JSON.parse(listed); } catch (err) { people = []; }
      var low = who.toLowerCase();
      if (!people.some(function (n) { return String(n && n.name !== undefined ? n.name : n).toLowerCase() === low; })) return;
    }
    e.preventDefault();
    var deck = collab.getAttribute("data-deck-name") || "";
    var adding = action === "add";
    var box = Box.open({
      title: adding ? "Let “" + who + "” edit “" + deck + "” on Archidekt?" : "Stop “" + who + "” editing “" + deck + "” on Archidekt?",
      body: adding ? el("p", "muted small", collab.getAttribute("data-note") || "") : el("p", null, "“" + who + "” can no longer change this deck. You can add them again later."),
      // adding is the box's one orange button; removing ends on the solid red one (red outline before it)
      actions: [cancelButton(), Box.button(adding ? "Add “" + who + "”" : "Remove “" + who + "”", adding ? "primary" : "danger", function () {
        var c = el("input");
        c.type = "hidden"; c.name = "confirmed"; c.value = "1";
        form.appendChild(c);
        box.busy(true);
        form.submit();
      }, { icon: "check" })],
      returnFocus: $("button", form)
    });
  });
})();
