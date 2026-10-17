// Signing in to the Android app through the phone's browser (app_signin.py).
// On /login inside the app: ask the app to open the sign-in in the browser. The page's own link
// signs in inside the app instead.
// The browser's last page has no script: its button to the app waits for a tap.
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
})();
