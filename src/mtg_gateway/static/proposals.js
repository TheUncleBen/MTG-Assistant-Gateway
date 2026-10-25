// The waiting-changes lists: the Proposals page (proposals_view.py) and the deck page's Proposals
// box (waiting_strip.py). The checked count goes on the bar's buttons ("Apply 3 changes" on the
// Proposals page, "Apply 3" in the box); the bar's buttons wait until a row is checked; a deck's
// "all" box checks its rows (the header's box every row); a checked row is tinted. A deck deletion
// on its review page asks once more, with the solid red button. Without this script every form
// still works.
(function () {
  "use strict";
  var forms = document.querySelectorAll("form.wform");
  for (var f = 0; f < forms.length; f++) wire(forms[f]);

  // A question's proposal rejected late (static/cardmenu.js settles one the deck page owed): its
  // row goes, and the deck's Proposals pill counts one fewer (no count once none wait).
  document.addEventListener("mtg:rejected", function (e) {
    var pid = e.detail && e.detail.proposal_id;
    if (!pid) return;
    var sel = "form.wform input[name=pick][value='" + (window.CSS && CSS.escape ? CSS.escape(pid) : pid) + "']";
    var pick = document.querySelector(sel);
    var row = pick ? pick.closest("li.wrow") : null;
    if (!row) return;
    var form = pick.form;
    row.parentNode.removeChild(row);
    var left = form ? form.querySelectorAll("li.wrow").length : 0;
    var nb = form && form.classList.contains("ybox") ? document.querySelector(".banner .soc.prop .nb") : null;
    if (nb) {
      if (left) nb.textContent = String(left);
      else { var said = nb.nextElementSibling; nb.remove(); if (said) said.remove(); }
    }
    if (form) form.dispatchEvent(new Event("change", { bubbles: true }));
  });

  // A deck deletion: the red outline button first, then "Delete this deck on Archidekt?" with
  // the solid red one, in place.
  document.addEventListener("click", function (e) {
    var ask = e.target instanceof Element ? e.target.closest("button.pr-ask") : null;
    if (!ask || e.defaultPrevented || ask.disabled || ask.getAttribute("aria-disabled") === "true") return;
    var form = ask.form;
    if (!form || form.querySelector(".pr-sure")) return;
    e.preventDefault();
    var box = document.createElement("div");
    box.className = "confirm rejectask pr-sure";
    box.setAttribute("role", "group");
    var q = document.createElement("span");
    q.textContent = "Delete this deck on Archidekt?";
    box.setAttribute("aria-label", q.textContent);
    var yes = document.createElement("button");
    yes.type = "submit";
    yes.name = "action";
    yes.value = "apply";
    yes.className = "danger";
    yes.textContent = ask.textContent;
    var no = document.createElement("button");
    no.type = "button";
    no.className = "btn-plain";
    no.textContent = "Cancel";
    box.appendChild(q);
    box.appendChild(yes);
    box.appendChild(no);
    ask.parentNode.insertBefore(box, ask.parentNode.firstChild);
    ask.hidden = true;
    no.addEventListener("click", function () { box.remove(); ask.hidden = false; ask.focus(); });
    box.addEventListener("keydown", function (ev) { if (ev.key === "Escape") { box.remove(); ask.hidden = false; ask.focus(); } });
    yes.focus();
  });

  function wire(form) {
    var bar = form.querySelector(".wbar, .ui-bar");
    if (!bar) return;
    var count = bar.querySelector(".count");
    var clear = bar.querySelector("button.clear");
    var acts = bar.querySelectorAll("button[name=action]");

    function picks() {
      return form.querySelectorAll("input[name=pick]");
    }

    function update() {
      var all = picks();
      var n = 0;
      for (var i = 0; i < all.length; i++) if (all[i].checked) n++;
      var decks = {};
      for (var d = 0; d < all.length; d++) {
        var row = all[d].closest(".tr");
        if (row) row.classList.toggle("sel", all[d].checked);
        var g = all[d].getAttribute("data-group");
        if (all[d].checked && g && g !== "untied") decks[g] = 1;
      }
      var nd = Object.keys(decks).length;
      for (var j = 0; j < acts.length; j++) {
        // the Proposals page says what the press does: "Apply 3 changes"; the strip "Apply 3"
        var long = acts[j].hasAttribute("data-long");
        var short = acts[j].getAttribute("data-label") + (n ? " " + n : "");
        acts[j].textContent = short;
        if (long && n) {
          // the long label only where it takes no more lines than the short one (large text on
          // a phone keeps "Apply 3")
          var h = acts[j].offsetHeight;
          acts[j].textContent = short + (n === 1 ? " change" : " changes");
          if (acts[j].offsetHeight > h + 2) acts[j].textContent = short;
        }
        acts[j].disabled = n === 0;
      }
      if (n) bar.removeAttribute("data-none");
      else bar.setAttribute("data-none", "");
      var on = n && bar.classList.contains("ui-bar") && nd ? " on " + nd + (nd === 1 ? " deck" : " decks") : "";
      if (count) count.textContent = n ? n + " checked" + on : "Check the changes to apply or reject";
      if (clear) clear.hidden = n === 0;
      var boxes = form.querySelectorAll("input.all[data-all]");
      for (var k = 0; k < boxes.length; k++) {
        var mine = rowsOf(boxes[k].getAttribute("data-all"));
        var on = 0;
        for (var m = 0; m < mine.length; m++) if (mine[m].checked) on++;
        boxes[k].checked = mine.length > 0 && on === mine.length;
        boxes[k].indeterminate = on > 0 && on < mine.length;
      }
    }

    function rowsOf(group) {
      var out = [];
      var all = picks();
      for (var i = 0; i < all.length; i++) if (group === "*" || all[i].getAttribute("data-group") === group) out.push(all[i]);
      return out;
    }

    form.addEventListener("change", function (e) {
      var t = e.target;
      if (t && t.classList && t.classList.contains("all")) {
        var mine = rowsOf(t.getAttribute("data-all"));
        for (var i = 0; i < mine.length; i++) mine[i].checked = t.checked;
      }
      update();
    });
    if (clear) {
      clear.addEventListener("click", function () {
        var all = form.querySelectorAll("input[type=checkbox]");
        for (var i = 0; i < all.length; i++) {
          all[i].checked = false;
          all[i].indeterminate = false;
        }
        update();
      });
    }
    // Apply asks in a box over this page (the server renders the confirm as a ui.box when the
    // form says box=1); anything but a box answer falls back to the confirm page.
    var apply = bar.querySelector("button[name=action][value=apply]");
    if (apply && window.MtgBox && window.fetch && window.FormData) {
      apply.addEventListener("click", function (e) {
        if (e.defaultPrevented || apply.disabled) return;
        e.preventDefault();
        var body = new FormData(form);
        body.set("action", "apply");
        body.set("box", "1");
        apply.disabled = true;
        fetch(form.getAttribute("action") || "/proposals/select", {
          method: "POST", body: body, credentials: "same-origin", headers: { Accept: "application/json" }
        }).then(function (r) { return r.json(); }).then(function (out) {
          apply.disabled = false;
          if (!out || !out.ok || !out.box) {
            if (out && out.message) { alertNote(out.message); return; }
            return fallback();
          }
          var old = document.getElementById("apply-box");
          if (old) old.remove();
          var holder = document.createElement("div");
          holder.innerHTML = out.box;
          var box = holder.firstElementChild;
          document.body.appendChild(box);
          window.MtgBox.openElement(box, { returnFocus: apply });
        }).catch(function () { apply.disabled = false; fallback(); });
      });
    }
    function fallback() {
      var act = document.createElement("input");
      act.type = "hidden"; act.name = "action"; act.value = "apply";
      form.appendChild(act);
      form.submit();
    }
    function alertNote(text) {
      var note = bar.querySelector(".boxnote") || document.createElement("p");
      note.className = "notice error boxnote";
      note.setAttribute("role", "alert");
      note.textContent = text;
      if (!note.parentNode) bar.appendChild(note);
    }
    // Reject asks first, in the bar ("Reject 3 changes?"); a rejected change can't
    // be brought back from the page. Without this script the server asks on a page of its own.
    var reject = bar.querySelector("button[name=action][value=reject]");
    var asking = null;
    function stopAsking(back) {
      if (!asking) return;
      asking.remove();
      asking = null;
      for (var i = 0; i < acts.length; i++) acts[i].hidden = false;
      update();
      if (back && reject) reject.focus();
    }
    if (reject) {
      reject.addEventListener("click", function (e) {
        if (form.querySelector("input[name=sure]")) return;
        e.preventDefault();
        var n = 0, all = picks();
        for (var i = 0; i < all.length; i++) if (all[i].checked) n++;
        if (!n) return;
        asking = document.createElement("div");
        asking.className = "confirm rejectask";
        asking.setAttribute("role", "group");
        var q = document.createElement("span");
        q.textContent = n === 1 ? "Reject this change?" : "Reject " + n + " changes?";
        asking.setAttribute("aria-label", q.textContent); // named as the deck page's confirms are
        var yes = document.createElement("button");
        yes.type = "button";
        yes.className = "danger";
        yes.textContent = n === 1 ? "Reject" : "Reject " + n;
        var no = document.createElement("button");
        no.type = "button";
        no.className = "btn btn-plain";
        no.textContent = "Cancel";
        asking.appendChild(q);
        asking.appendChild(yes);
        asking.appendChild(no);
        for (var j = 0; j < acts.length; j++) acts[j].hidden = true;
        bar.appendChild(asking);
        no.addEventListener("click", function () { stopAsking(true); });
        asking.addEventListener("keydown", function (ev) { if (ev.key === "Escape") stopAsking(true); });
        yes.addEventListener("click", function () {
          var sure = document.createElement("input");
          sure.type = "hidden";
          sure.name = "sure";
          sure.value = "1";
          form.appendChild(sure);
          var act = document.createElement("input");
          act.type = "hidden";
          act.name = "action";
          act.value = "reject";
          form.appendChild(act);
          yes.disabled = true;
          form.submit();
        });
        yes.focus();
      });
    }
    // back from the next page (the browser's page cache): ask again next time
    window.addEventListener("pageshow", function () {
      var left = form.querySelectorAll("input[name=sure], input[type=hidden][name=action]");
      for (var i = 0; i < left.length; i++) left[i].remove();
      stopAsking(false);
    });
    // a change of the checked rows while asking: ask again for the new set
    form.addEventListener("change", function () { stopAsking(false); });
    update();
  }
})();
