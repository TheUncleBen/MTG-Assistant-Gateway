"use strict";
// File pickers that fill a textarea: the file is read in the browser (FileReader) and its text
// put into the field named by data-fill, so the form posts plain text and the server needs no
// upload handling. data-kind names a <select> whose value follows the file's extension. The
// picker is themed (theme.py .filepick): the chosen file's name goes into the field's .fname.
(function () {
  document.addEventListener("change", function (e) {
    var input = e.target;
    if (!input.matches || !input.matches("input[type=file]")) return;
    var file = input.files && input.files[0];
    var field = input.closest ? input.closest(".filepick") : null;
    var fname = field && field.querySelector(".fname");
    if (fname) fname.textContent = file ? file.name : "No file chosen";
    if (!input.hasAttribute("data-fill")) return;
    var area = document.getElementById(input.getAttribute("data-fill"));
    if (!file || !area) return;
    if (file.size > 4000000) {
      alert("That file is larger than 4 MB.");
      input.value = "";
      if (fname) fname.textContent = "No file chosen";
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      area.value = String(reader.result || "");
      var kind = document.getElementById(input.getAttribute("data-kind") || "");
      if (kind) {
        var ext = (file.name.split(".").pop() || "").toLowerCase();
        var want = ext === "csv" ? "csv" : ext === "json" ? "json" : "list";
        if (kind.querySelector("option[value=" + want + "]")) {
          kind.value = want;
          if (window.MtgSelect) window.MtgSelect.refresh(kind);
        }
      }
      area.dispatchEvent(new Event("input", { bubbles: true }));
    };
    reader.onerror = function () { alert("That file could not be read."); };
    reader.readAsText(file);
  });
})();
