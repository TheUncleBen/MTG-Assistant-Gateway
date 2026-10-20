// The admin Activity page (activity_view.py): a row's details open in place. Without scripts the
// chevron is a link that reloads the page with that row open (?open=<id>); with them it opens and
// closes the row where it is.
(function () {
  "use strict";
  document.addEventListener("click", function (ev) {
    var more = ev.target.closest && ev.target.closest(".act-table .c-more");
    if (!more) return;
    var row = more.closest(".tr");
    var body = row && row.querySelector(".more-body");
    if (!body) return;
    ev.preventDefault();
    var open = body.hidden;
    body.hidden = !open;
    row.classList.toggle("open", open);
    more.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.addEventListener("keydown", function (ev) {
    // a link drawn as a button also answers the space bar
    if (ev.key !== " ") return;
    var more = ev.target.closest && ev.target.closest(".act-table .c-more");
    if (!more) return;
    ev.preventDefault();
    more.click();
  });
})();
