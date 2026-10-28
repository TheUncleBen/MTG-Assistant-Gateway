// Signing in to the Android app through the phone's browser (app_signin.py).
// On /login inside the app: ask the app to open the sign-in in the browser. The page's own link
// signs in inside the app instead.
// On the browser's last page (/login/app-return): when the gateway marked it (data-auto, a sign-in
// made during this leg), open the app once by itself, where the browser allows that without a tap;
// the button stays for when it doesn't. Once per code, so coming back to the page (Back, a reload)
// never loops. Unmarked, the page waits for the member's tap.
(function () {
  "use strict";
  var box = document.getElementById("app-signin");
  if (box) {
    var go = function () {
      var n = window.MtgNative;
      if (!n || typeof n.signInWithBrowser !== "function") return false;
      n.signInWithBrowser(box.getAttribute("data-next") || "/", box.getAttribute("data-fresh") === "1");
      return true;
    };
    go();
    document.getElementById("app-signin-go").addEventListener("click", go);
  }
  var back = document.getElementById("app-return");
  if (back && back.getAttribute("data-auto") === "1") {
    var key = "mtg-app-return:" + back.getAttribute("href");
    var tried = false;
    try { tried = window.sessionStorage.getItem(key) === "1"; window.sessionStorage.setItem(key, "1"); } catch (e) { /* no storage: try once on this load */ }
    if (!tried) window.location.href = back.getAttribute("href");
  }
})();
