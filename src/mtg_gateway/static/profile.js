/* Account › Archidekt profile, email and notification switches, new device default settings
   (profile_page.py): each switch and drop-down saves to Archidekt as it is changed, with a toast;
   the picture box checks the chosen file's type and size before it is sent and shows it in the
   circle. Every form works without this file (its Save button, a reload with the notice).
   Needs static/cardmenu.js (window.MtgMenu, for the toast) and static/box.js. */
(function () {
  "use strict";
  var acc = document.querySelector(".acc");
  if (!acc) return;
  var Menu = window.MtgMenu;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var NO_CONNECTION = "No connection; nothing was changed.";
  function toast(text, opts) {
    if (Menu && Menu.toast) Menu.toast(text, opts);
    else if (opts && opts.error) alert(text);
  }

  // -- the switches and drop-downs: saved as they change ----------------------------------------
  function postForm(form) {
    var body = new URLSearchParams();
    $$("input, select", form).forEach(function (i) {
      if (i.type === "checkbox") body.append(i.name, i.checked ? "1" : "0");
      else if (i.name) body.append(i.name, i.value);
    });
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
  function revert(form) {
    var was = $("[name=was]", form), sw = $("input[type=checkbox]", form), sel = $("select", form);
    if (!was) return;
    if (sw) sw.checked = was.value === "1";
    if (sel) {
      sel.value = was.value;
      if (window.MtgSelect) window.MtgSelect.refresh(sel);  // the themed button shows the old label again
    }
  }
  function remember(form) {
    var was = $("[name=was]", form), sw = $("input[type=checkbox]", form), sel = $("select", form);
    if (!was) return;
    was.value = sw ? (sw.checked ? "1" : "0") : sel ? sel.value : was.value;
  }
  $$("form[data-saves]", acc).forEach(function (form) {
    form.addEventListener("submit", function (e) { e.preventDefault(); save(form); });
    form.addEventListener("change", function () { save(form); });
  });
  function save(form) {
    if (form.getAttribute("aria-busy") === "true") return;
    form.setAttribute("aria-busy", "true");
    $$("input, select", form).forEach(function (i) { if (i.type !== "hidden") i.disabled = true; });
    postForm(form).then(function (d) {
      form.removeAttribute("aria-busy");
      $$("input, select", form).forEach(function (i) { i.disabled = false; });
      if (!d.ok) { revert(form); toast(d.message || "Archidekt refused the change.", { error: true }); return; }
      remember(form);
      if (d.verified === false) toast(d.notice || "Sent to Archidekt, but the read-back didn't confirm the change.", { error: true });
      else toast(d.notice || "Saved on Archidekt.", { ok: true, ms: 5000 });
    }, function () {
      form.removeAttribute("aria-busy");
      $$("input, select", form).forEach(function (i) { i.disabled = false; });
      revert(form);
      toast(NO_CONNECTION, { error: true });
    });
  }

  // -- the picture box: the file checked here before it is sent ---------------------------------
  var MAX_BYTES = 1000000;
  var box = $("#picture");
  if (box) {
    var file = $("#pr-file", box), pick = $("#pr-pick", box), use = $("#pr-use"), name = $("#pr-filename", box);
    var preview = $("#pr-preview", box), initial = preview ? preview.textContent : "";
    var err = $("#pr-err", box);
    var url = null;
    // a refused file is said inside the box (a toast would sit behind it, or over its buttons)
    function complain(msg) {
      if (err) { err.textContent = msg; err.hidden = false; }
      else toast(msg, { error: true });
    }
    function reset(msg) {
      if (url) { try { URL.revokeObjectURL(url); } catch (e) { /* gone */ } url = null; }
      if (preview) { preview.textContent = initial; }
      if (name) { name.hidden = !msg; name.textContent = msg || ""; }
      if (err) { err.hidden = true; err.textContent = ""; }
      if (use) use.disabled = true;
      if (file) file.value = "";
    }
    if (use) use.disabled = true;
    if (pick && file) pick.addEventListener("click", function () { file.click(); });
    if (file) file.addEventListener("change", function () {
      var f = file.files && file.files[0];
      if (!f) { reset(""); return; }
      if (["image/jpeg", "image/png"].indexOf(f.type) < 0) { reset(""); complain("Use a JPEG or PNG picture."); return; }
      if (f.size > MAX_BYTES) { reset(""); complain("The picture is larger than 1 MB, Archidekt's limit. Choose a smaller one."); return; }
      if (url) { try { URL.revokeObjectURL(url); } catch (e) { /* gone */ } }
      url = URL.createObjectURL(f);
      if (err) { err.hidden = true; err.textContent = ""; }
      if (preview) {
        preview.textContent = "";
        var img = document.createElement("img");
        img.alt = "";
        img.src = url;
        preview.appendChild(img);
      }
      if (name) { name.hidden = false; name.textContent = f.name + " · " + Math.max(1, Math.round(f.size / 1024)) + " KB"; }
      if (use) use.disabled = false;
    });
    var form = $("#pr-pic-f", box);
    if (form) form.addEventListener("submit", function (e) {
      var f = file && file.files && file.files[0];
      if (!f || f.size > MAX_BYTES || ["image/jpeg", "image/png"].indexOf(f.type) < 0) {
        e.preventDefault();
        complain("Choose a JPEG or PNG up to 1 MB first.");
        return;
      }
      if (use) { use.disabled = true; use.setAttribute("aria-busy", "true"); }
    });
    box.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest("[data-cancel], .bx")) reset("");
    });
  }
})();
