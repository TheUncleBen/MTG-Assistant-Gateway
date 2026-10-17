// The waiting-changes lists (waiting_strip.py): the Proposals page and the deck page's strip.
// The checked count goes on the bar's buttons ("Apply 3", "Reject 3"); the bar's buttons wait
// until a row is checked; a deck's "all" box checks its rows; the deck strip opens by itself on a
// wide screen (Q134 A2: one line on a phone). Without this script every form still works.
(function () {
  "use strict";
  var forms = document.querySelectorAll("form.wform");
  for (var f = 0; f < forms.length; f++) wire(forms[f]);

  var strip = document.querySelector("details.wstrip[data-open-wide]");
  if (strip && window.matchMedia && window.matchMedia("(min-width: 701px)").matches) strip.open = true;

  function wire(form) {
    var bar = form.querySelector(".wbar");
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
      for (var j = 0; j < acts.length; j++) {
        acts[j].textContent = acts[j].getAttribute("data-label") + (n ? " " + n : "");
        acts[j].disabled = n === 0;
      }
      if (n) bar.removeAttribute("data-none");
      else bar.setAttribute("data-none", "");
      if (count) count.textContent = n ? n + " checked" : "Check the changes to apply or reject";
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
      for (var i = 0; i < all.length; i++) if (all[i].getAttribute("data-group") === group) out.push(all[i]);
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
    // Q141 item 8C: Reject asks first, in the bar ("Reject 3 changes?"); a rejected change can't
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
        no.className = "btn";
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
