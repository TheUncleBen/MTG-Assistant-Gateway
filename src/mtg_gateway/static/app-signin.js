// Signing in to the Android app through the phone's browser (app_signin.py).
// On /login inside the app: ask the app to open the sign-in in the browser, or, in an app too old
// to know window.MtgNative.signInWithBrowser, follow the in-app sign-in link as before.
// On the browser's last page: try to open the app at once; the button stays for when the browser
// wants a tap first.
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
    if (!go()) {
      location.replace(box.getAttribute("data-inapp"));
      return;
    }
    document.getElementById("app-signin-go").addEventListener("click", go);
  }
  var back = document.getElementById("app-return");
  if (back) location.href = back.href;
})();
