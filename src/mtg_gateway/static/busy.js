/* The busy page's one script (dbwait.py: the gateway turned a request away while its database
   was locked), for a posted form the page could not keep (a file upload, a very long body): Go
   back returns to the form the member filled in. A page the browser restores from its cache
   still holds what was typed; one it loads afresh gets the typed draft back from static/draft.js,
   which would otherwise drop a draft whose form was submitted: the note left here says the
   submit did not go through. Served from /static, which never waits for the database. */
(function () {
  "use strict";
  var back = document.querySelector("[data-busy-back]");
  if (!back) return;
  back.addEventListener("click", function () {
    try { sessionStorage.setItem("mtg_draft_back", String(Date.now())); } catch (e) { /* storage off */ }
    if (window.history.length > 1) window.history.back();
  });
})();
