// Loaded on every gateway page (theme.render).
// 1. A button that submits a form shows a busy state at once (aria-busy, styled in theme.py), and
//    a second press of the same form while it is sending is ignored, so a slow Apply or Create
//    never looks dead and never sends twice. Forms a page script handles itself (it calls
//    preventDefault) are left to that script.
// 2. Registers the gateway's service worker (/sw.js), which never caches a page: it only answers
//    with a plain "you're offline" page when a page can't be reached at all.
(function () {
  "use strict";
  var RESET_MS = 10000; // a download or a slow answer: allow another press after this long

  function clear(form) {
    delete form.dataset.sending;
    var busy = form.querySelectorAll("[aria-busy=true]");
    for (var i = 0; i < busy.length; i++) busy[i].removeAttribute("aria-busy");
    var outside = form.id ? document.querySelectorAll("[form='" + form.id + "'][aria-busy=true]") : [];
    for (var j = 0; j < outside.length; j++) outside[j].removeAttribute("aria-busy");
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || e.defaultPrevented) return;
    if (form.dataset.sending) {
      e.preventDefault();
      return;
    }
    form.dataset.sending = "1";
    var button = e.submitter;
    if (button && button.tagName === "BUTTON") button.setAttribute("aria-busy", "true");
    setTimeout(function () { clear(form); }, RESET_MS);
  });

  // Coming back with the Back button restores the page from memory: nothing is sending any more.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var forms = document.querySelectorAll("form[data-sending]");
    for (var i = 0; i < forms.length; i++) clear(forms[i]);
  });

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }
})();
