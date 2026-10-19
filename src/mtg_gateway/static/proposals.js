// The waiting-changes lists: the Proposals page (proposals_view.py) and the deck page's strip
// (waiting_strip.py). The checked count goes on the bar's buttons ("Apply 3 changes" on the
// Proposals page, "Apply 3" on the strip); the bar's buttons wait until a row is checked; a deck's
// "all" box checks its rows (the header's box every row); a checked row is tinted; the deck strip
// opens by itself on a wide screen (one line on a phone). A deck deletion on its review page asks
// once more, with the solid red button. Without this script every form still works.
(function () {
  "use strict";
  var forms = document.querySelectorAll("form.wform");
  for (var f = 0; f < forms.length; f++) wire(forms[f]);

  var strip = document.querySelector("details.wstrip[data-open-wide]");
  if (strip && window.matchMedia && window.matchMedia("(min-width: 701px)").matches) strip.open = true;

  // A deck deletion: the red outline button first, then "Delete this deck on Archidekt?" with
  // the solid red one (UI-STANDARD §5), in place.
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
        acts[j].textContent = acts[j].getAttribute("data-label") + (n ? " " + n : "") +
          (long && n ? (n === 1 ? " change" : " changes") : "");
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
