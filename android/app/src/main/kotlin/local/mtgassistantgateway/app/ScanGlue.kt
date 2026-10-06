package local.mtgassistantgateway.app

/**
 * The JavaScript the app runs on the gateway's /scan page.
 *
 * The page already knows how to read a photo of one card: find its edges and flatten it, read the
 * title and the info line, ask the gateway, and show the result with Add and the printing picker.
 * Its Photo button does exactly that through `scanFile`, and the same steps are reachable one by
 * one through the page's `window.__scan` hooks (`flattenCard`, `flatTitleRegion`, `infoRegionOf`,
 * `scanRegion`, `showTab`, `noCardFrame`, `undoLastAdd`). The glue below mirrors `scanFile` with
 * those hooks, so it works against any gateway that serves the current scan page.
 *
 * While the phone camera is open the page sits on its list tab (which stops the page's own camera)
 * and the app keeps the lens. Each photo is handed over with [onPhoto] as a same-origin URL the app
 * answers itself from `shouldInterceptRequest` (the page's CSP allows `img-src 'self'`), so no
 * base64 copy of the picture ever crosses into JavaScript. What the page did with it comes back
 * through the `MtgNative.scanEvent` bridge as small JSON objects, each carrying the [install]
 * nonce and the photo's sequence number so stale or spoofed events are dropped: the page's own
 * `scan:auto-added`, `scan:unsure`, `scan:unidentified` and `scan:undo` events, then `done` when
 * the photo has been fully processed, with the status line the page would have shown, or `no-card`
 * in its place when continuous mode found no card outline and so read nothing. In continuous mode the page's own rules apply
 * (`state.continuous`): a clean, unambiguous read is added without a tap, once per card shown, and
 * a shaky one waits for a tap on the page.
 *
 * The page's status line lives on its camera tab, which is not rendered while the app has the
 * camera, so the glue plants a hidden stand-in with the same id inside the page's root and reads
 * it back after each photo. It is removed by [END].
 *
 * Pure string building, so the unit tests can check the output without a WebView, and the
 * browser test in the gateway's own test suite runs the same script against the real page.
 */
object ScanGlue {
    private val NONCE = Regex("^[a-f0-9]{16,64}$")
    // The host may be an IPv6 literal (`https://[::1]`), which GatewayUrl.normalize accepts.
    private val PHOTO_URL = Regex("^(https://[A-Za-z0-9.:\\[\\]-]+|blob:[A-Za-z0-9.:/\\[\\]-]+)/[A-Za-z0-9/_.-]+$")
    /** Directory under the gateway origin the app answers itself; nothing under it reaches the network. */
    const val PHOTO_DIR = "/__mtgassistant/photo"
    /** [PHOTO_DIR] plus the slash the photo's sequence number follows. */
    const val PHOTO_PATH = "$PHOTO_DIR/"
    /** Long side of the working copy of a photo, matching the page's PHOTO_MAX. */
    const val PHOTO_MAX = 2400
    /** The hooks the glue needs on `window.__scan`; all in scan.js's export list. */
    val HOOKS = listOf("flattenCard", "flatTitleRegion", "infoRegionOf", "scanRegion", "showTab", "noCardFrame", "undoLastAdd")

    /** Installed once per page load. Idempotent. Evaluates to 'installed' or 'present'. */
    fun install(nonce: String): String {
        require(NONCE.matches(nonce)) { "bad nonce" }
        return INSTALL_TEMPLATE.replace("__NONCE__", nonce)
    }

    private val INSTALL_TEMPLATE: String = """
(function () {
  if (window.__mtgNative) return 'present';
  var PHOTO_MAX = $PHOTO_MAX;
  var NONCE = '__NONCE__';
  var seq = 0;
  function hooks() {
    var s = window.__scan;
    if (!s || !s.state) return null;
    var ok = [${HOOKS.joinToString(", ") { "'$it'" }}].every(function (k) { return typeof s[k] === 'function'; });
    return ok ? s : null;
  }
  function send(ev) {
    ev.nonce = NONCE;
    try { if (window.MtgNative && typeof MtgNative.scanEvent === 'function') MtgNative.scanEvent(JSON.stringify(ev)); } catch (e) {}
  }
  ['auto-added', 'unsure', 'unidentified', 'undo'].forEach(function (n) {
    document.addEventListener('scan:' + n, function (e) {
      var d = e.detail || {};
      var s = hooks();
      var left = s && s.state.autoAdded ? s.state.autoAdded.length : 0; // automatic adds still there to undo
      send({ type: n, seq: seq, name: d.name || d.text || '', total: d.total, count: d.count, status: d.status, foil: !!d.foil, undoable: left });
    });
  });
  var total = function (s) { return s.state.items.reduce(function (n, it) { return n + (it.quantity || 0); }, 0); };
  function statusBox() {
    var root = document.getElementById('scan-app') || document.body;
    var el = root.querySelector('#cam-status');
    if (!el) {
      el = document.createElement('div');
      el.id = 'cam-status'; el.hidden = true; el.setAttribute('data-mtg-native', '1');
      root.appendChild(el);
    }
    return el;
  }
  function dropStatusBox() {
    var el = document.querySelector('#cam-status[data-mtg-native]');
    if (el) el.remove();
  }
  function load(url) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () {
        try {
          var k = Math.min(1, PHOTO_MAX / Math.max(img.naturalWidth, img.naturalHeight));
          var c = document.createElement('canvas');
          c.width = Math.max(1, Math.round(img.naturalWidth * k));
          c.height = Math.max(1, Math.round(img.naturalHeight * k));
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          resolve(c);
        } catch (e) { reject(e); }
      };
      img.onerror = function () { reject(new Error('photo not loaded')); };
      img.src = url;
    });
  }
  async function process(s, url, n, guide) {
    var c = await load(url);
    s.state.lastPicture = { w: c.width, h: c.height };
    // Where the dashed box was over the photo, as the page's own guide box is over its video.
    var g = guide ? { x: guide.x * c.width, y: guide.y * c.height, w: guide.w * c.width, h: guide.h * c.height }
      : { x: 0, y: 0, w: c.width, h: c.height };
    var flat = s.flattenCard(c, guide ? g : null);
    if (flat) {
      s.state.noCardFrames = 0;
      await s.scanRegion(flat.canvas, s.flatTitleRegion(flat.width, flat.height), s.infoRegionOf(0, 0, flat.width, flat.height), flat);
    } else if (s.state.continuous) {
      // Continuous mode reads nothing without an outline (the page does the same): count the
      // frame so a card that has left comes back as new, and say so.
      s.noCardFrame();
      send({ type: 'no-card', seq: n, frames: s.state.noCardFrames, total: total(s) });
      return 'no-card'; // stands in for 'done'
    } else {
      // One shot of a card filling the dashed box (or the picture): title in the top band, info line bottom left.
      await s.scanRegion(c, { x: g.x + g.w * 0.06, y: g.y + g.h * 0.035, w: g.w * 0.72, h: g.h * 0.085 },
        s.infoRegionOf(g.x, g.y, g.w, g.h), null);
    }
  }
  window.__mtgNative = {
    ready: function () { return !!hooks(); },
    begin: function () {
      var s = hooks();
      if (!s) return 'no-scan';
      if (s.state.continuous && typeof s.stopContinuous === 'function') s.stopContinuous(); // keeps the page's Auto switch in step
      s.showTab('list'); // stops the page's own camera; the list shows what has been added so far
      s.state.pending = null; s.state.continuous = false;
      statusBox();
      return 'ok';
    },
    setContinuous: function (on) {
      var s = hooks();
      if (!s) return 'no-scan';
      s.state.continuous = !!on;
      s.state.lastAutoKey = null; s.state.rejectedKey = null; s.state.noCardFrames = 0; s.state.lastRead = null;
      return 'ok';
    },
    onPhoto: function (url, guide) {
      var s = hooks();
      if (!s) return 'no-scan';
      var n = ++seq;
      var box = statusBox(); box.textContent = '';
      process(s, url, n, guide || null).then(function (r) {
        if (r === 'no-card') return;
        send({ type: 'done', seq: n, status: box.textContent, total: total(s), pending: !!s.state.pending });
      }, function (e) {
        send({ type: 'done', seq: n, status: '', error: String(e && e.message || e), total: total(s), pending: !!s.state.pending });
      });
      return 'ok:' + n;
    },
    undo: function () {
      var s = hooks();
      if (!s) return 'no-scan';
      return s.undoLastAdd() ? 'ok' : 'nothing';
    },
    end: function () {
      var s = hooks();
      dropStatusBox();
      if (!s) return 'no-scan';
      s.state.continuous = false;
      if (s.state.pending) { s.showTab('camera'); return 'pending'; } // the shaky read waits there for a tap
      return 'ok';
    }
  };
  return 'installed';
})();
"""

    /** Asks the page whether the scan hooks are there. Evaluates to `true` or `false`. */
    const val READY: String = "(function(){return !!(window.__mtgNative && window.__mtgNative.ready());})();"

    /** Run when the phone camera opens: parks the page on its list tab. Evaluates to 'ok' or 'no-scan'. */
    const val BEGIN: String = "(function(){return window.__mtgNative ? window.__mtgNative.begin() : 'no-glue';})();"

    /** Run when the phone camera closes. Evaluates to 'ok', or 'pending' when a result now waits on the page. */
    const val END: String = "(function(){return window.__mtgNative ? window.__mtgNative.end() : 'no-glue';})();"

    /** Takes one copy of the last automatic add back off the list. Evaluates to 'ok' or 'nothing'. */
    const val UNDO: String = "(function(){return window.__mtgNative ? window.__mtgNative.undo() : 'no-glue';})();"

    /** Turns the page's continuous-mode rules on or off for the photos that follow. */
    fun setContinuous(on: Boolean): String =
        "(function(){return window.__mtgNative ? window.__mtgNative.setContinuous($on) : 'no-glue';})();"

    /**
     * Hands a photo to the page by URL (a gateway-origin [PHOTO_PATH] URL the app answers itself, or a
     * blob: URL in tests). Evaluates to 'ok:<seq>' at once; the outcome arrives as scan events carrying
     * that seq. [guide] is where the dashed box lay over the photo, as fractions `[x, y, w, h]` of its
     * size (see [TorchMath.guideFractions]), or null to search the whole picture.
     */
    fun onPhoto(url: String, guide: FloatArray? = null): String {
        require(PHOTO_URL.matches(url)) { "bad photo url" }
        val g = if (guide == null) "null" else {
            require(guide.size == 4 && guide.all { it.isFinite() && it >= 0f && it <= 1f }) { "bad guide" }
            "{x:${guide[0]},y:${guide[1]},w:${guide[2]},h:${guide[3]}}"
        }
        return "(function(){return window.__mtgNative ? window.__mtgNative.onPhoto('$url', $g) : 'no-glue';})();"
    }

    /** The gateway URL the photo with sequence number [n] is served at (see [photoSeqOf]). */
    fun photoUrl(origin: String, n: Int): String = origin + PHOTO_PATH + n

    /**
     * For a request the WebView is about to make: null when it is not under [PHOTO_DIR] on the gateway
     * (it goes to the network as usual), otherwise the photo sequence number it asks for, or -1 when
     * it names none (answered with a 404, still without touching the network).
     */
    fun photoSeqOf(origin: String, url: String): Int? {
        if (!GatewayUrl.isGateway(origin, url) || !GatewayUrl.pathStartsWith(url, PHOTO_DIR)) return null
        val path = try {
            java.net.URI(url).path ?: return -1
        } catch (e: java.net.URISyntaxException) {
            return -1
        }
        val n = path.removePrefix(PHOTO_PATH)
        return if (path.startsWith(PHOTO_PATH) && n.length in 1..9 && n.all { it in '0'..'9' }) n.toInt() else -1
    }

    /** The seq a successful [onPhoto] evaluation reported, or null for anything else (quoted JSON string in). */
    fun acceptedSeq(evaluateResult: String?): Int? {
        val m = Regex("^\"ok:(\\d+)\"$").find(evaluateResult ?: return null) ?: return null
        return m.groupValues[1].toIntOrNull()
    }
}
