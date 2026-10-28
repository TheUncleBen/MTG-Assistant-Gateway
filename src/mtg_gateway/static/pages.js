// The small pages and the Account page (smallpage.py, pages.py).
// - A Back button ([data-history-back]) goes back one page when the browser has one; without
//   scripts, or on a page opened directly, its link goes to the page it names.
// - A box's final button ([data-enabled-by=<tick box id>]) stays disabled until that tick box is
//   ticked (the Delete my data box). Without scripts the tick box's `required` stops the form.
// - Theme and Layout on the Account page are sent with fetch and the page is drawn again in its
//   own place in the history, so Back goes to the page before it (the form's redirect added a
//   step: Back seemed to do nothing). Without scripts, or when the fetch fails, the form posts.
(function () {
  "use strict";
  // A link to a part of the account's cards from before they moved one row in (a summary email,
  // an older app's notification: /account#summary) opens that part of Account details.
  if (location.pathname === "/account" && location.hash.length > 1 && document.querySelector(".yhub"))
    location.replace("/account/details" + location.hash);
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("[data-history-back]") : null;
    if (!a || e.defaultPrevented || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button) return;
    if (window.history.length > 1) {
      e.preventDefault();
      window.history.back();
    }
  });
  function gate(btn) {
    var box = document.getElementById(btn.getAttribute("data-enabled-by"));
    if (!box) return;
    var sync = function () { btn.disabled = !box.checked; };
    box.addEventListener("change", sync);
    sync();
  }
  var gated = document.querySelectorAll("[data-enabled-by]");
  for (var i = 0; i < gated.length; i++) gate(gated[i]);
  document.addEventListener("submit", function (e) {
    var form = e.target, btn = e.submitter;
    if (!form || !window.fetch || e.defaultPrevented) return;
    var action = form.getAttribute("action");
    if (action !== "/theme" && action !== "/layout") return;
    if (form.getAttribute("data-sent") || !btn || !btn.name) return;
    e.preventDefault();
    var data = new URLSearchParams(new FormData(form)); // the form's own encoding, not multipart
    data.set(btn.name, btn.value);
    fetch(action, { method: "POST", body: data, credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok) throw new Error("not saved");
        location.replace(location.pathname + location.search); // no hash: a hash alone would not redraw
      })
      .catch(function () {
        // the form's own way: the server's answer then says what happened
        var keep = document.createElement("input");
        keep.type = "hidden"; keep.name = btn.name; keep.value = btn.value;
        form.appendChild(keep);
        form.setAttribute("data-sent", "1");
        form.submit();
      });
  });
})();
