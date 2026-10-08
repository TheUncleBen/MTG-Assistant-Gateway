"use strict";
// File pickers that fill a textarea: the file is read in the browser (FileReader) and its text
// put into the field named by data-fill, so the form posts plain text and the server needs no
// upload handling. data-kind names a <select> whose value follows the file's extension.
(function () {
  document.addEventListener("change", function (e) {
    var input = e.target;
    if (!input.matches || !input.matches("input[type=file][data-fill]")) return;
    var file = input.files && input.files[0];
    var area = document.getElementById(input.getAttribute("data-fill"));
    if (!file || !area) return;
    if (file.size > 4000000) { alert("That file is larger than 4 MB."); input.value = ""; return; }
    var reader = new FileReader();
    reader.onload = function () {
      area.value = String(reader.result || "");
      var kind = document.getElementById(input.getAttribute("data-kind") || "");
      if (kind) {
        var ext = (file.name.split(".").pop() || "").toLowerCase();
        var want = ext === "csv" ? "csv" : ext === "json" ? "json" : "list";
        if (kind.querySelector("option[value=" + want + "]")) kind.value = want;
      }
      area.dispatchEvent(new Event("input", { bubbles: true }));
    };
    reader.onerror = function () { alert("That file could not be read."); };
    reader.readAsText(file);
  });
})();
