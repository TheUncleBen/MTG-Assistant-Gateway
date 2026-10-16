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
    update();
  }
})();
