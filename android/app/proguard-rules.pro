# The scan page calls these through the WebView bridge (window.MtgNative); the default Android rules
# already keep @JavascriptInterface methods, this makes the intent explicit.
-keepclassmembers class local.mtgassistantgateway.app.MainActivity$NativeBridge {
    @android.webkit.JavascriptInterface <methods>;
}
