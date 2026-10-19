// The admin Settings page (settings_page.py). Without scripts the page works as it is: every
// confirmation tick shows, and "Set in your stack" is a link to ?locked=1. With scripts:
// - a confirmation tick shows only once its value is typed lower or switched off, with a first
//   sentence saying what changed, and goes away when the value goes back up (the server still
//   refuses a lowering save without the tick);
// - each group says how many of its settings are changed but not saved yet;
// - the "Set in your stack" chip shows only the rows the stack sets, and everything again.
(function () {
  "use strict";
  var page = document.querySelector(".set-page");
  if (!page) return;

  function controlOf(row) {
    return row.querySelector(".set-ctl input[type=number]");
  }

  function offChecked(row) {
    var off = row.querySelector(".set-ctl input[type=radio][value=off]");
    return !!(off && off.checked);
  }

  function updateConfirm(box) {
    var row = box.closest(".set-row");
    var lead = box.querySelector(".lead");
    var said = box.querySelector(".said");
    var from = box.getAttribute("data-from");
    var show = false;
    var text = "";
    if (box.getAttribute("data-confirm") === "off") {
      show = from === "on" && offChecked(row);
      text = "You turned this off.";
    } else {
      var input = controlOf(row);
      var raw = input ? input.value.trim() : "";
      if (raw !== "" && isFinite(Number(raw)) && Number(raw) < Number(from)) {
        show = true;
        text = "You lowered this from " + from + " to " + raw + ".";
      }
    }
    box.hidden = !show;
    if (lead) lead.hidden = !show;
    if (said) said.textContent = text;
    if (!show) {
      var tick = box.querySelector("input[type=checkbox]");
      if (tick) tick.checked = false;
    }
  }

  function changed(row) {
    var fields = row.querySelectorAll(".set-ctl input, .set-ctl select");
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      if (f.disabled) continue;
      if (f.type === "radio" || f.type === "checkbox") {
        if (f.checked !== f.defaultChecked) return true;
      } else if (f.tagName === "SELECT") {
        for (var j = 0; j < f.options.length; j++) {
          if (f.options[j].selected !== f.options[j].defaultSelected) return true;
        }
      } else if (f.value !== f.defaultValue) {
        return true;
      }
    }
    return false;
  }

  function updateForm(form) {
    var n = 0;
    var rows = form.querySelectorAll(".set-row");
    for (var i = 0; i < rows.length; i++) {
      var isChanged = changed(rows[i]);
      if (isChanged) n++;
      var num = rows[i].querySelector(".set-num");
      if (num) num.classList.toggle("typed", isChanged);
    }
    var hint = form.querySelector(".set-save .unsaved");
    if (hint) hint.textContent = n ? n + (n === 1 ? " change" : " changes") + " not saved yet" : "";
  }

  var boxes = page.querySelectorAll(".set-confirm[data-confirm]");
  for (var b = 0; b < boxes.length; b++) {
    // a box the server showed after a refused save keeps its sentence until the value changes
    if (!boxes[b].hasAttribute("data-shown")) updateConfirm(boxes[b]);
  }

  page.addEventListener("input", onEdit);
  page.addEventListener("change", onEdit);

  function onEdit(ev) {
    var row = ev.target.closest && ev.target.closest(".set-row");
    if (!row) return;
    if (ev.target.closest(".set-confirm")) return;
    var box = row.querySelector(".set-confirm[data-confirm]");
    if (box) updateConfirm(box);
    if (row.closest("form")) updateForm(row.closest("form"));
  }

  var chip = page.querySelector(".lockchip");
  if (chip) {
    chip.addEventListener("click", function (ev) {
      ev.preventDefault();
      var on = !page.classList.contains("only-locked");
      page.classList.toggle("only-locked", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      if (on) {
        // a folded group with a setting the stack sets opens, so its rows show
        var folded = page.querySelectorAll(".set-group.has-locked details");
        for (var i = 0; i < folded.length; i++) folded[i].open = true;
      }
      try {
        history.replaceState(null, "", on ? "?locked=1" : location.pathname);
      } catch (e) {
        /* nothing to keep */
      }
    });
  }
})();
