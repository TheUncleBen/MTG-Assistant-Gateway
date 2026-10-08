/* The host side of an in-chat card: the MCP Apps postMessage protocol (ui/initialize, the
   tool-result notification, host context, size reports), plus the few helpers every card
   shares. Inlined into each card by cards.card_html(); not served on its own.
   Every value a card draws goes through textContent: names, notes and rules text are data. */
var Bridge = (function () {
  "use strict";
  var PROTOCOL = "2026-01-26";
  var host = window.parent;
  var pending = {}, nextId = 1, hostCaps = {}, hostCtx = {}, handlers = {};

  function post(msg) { host.postMessage(msg, "*"); }
  function request(method, params) {
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { resolve: resolve, reject: reject };
      post({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
    });
  }
  function notify(method, params) { post({ jsonrpc: "2.0", method: method, params: params || {} }); }
  function reply(id, result) { post({ jsonrpc: "2.0", id: id, result: result }); }
  function replyError(id, code, message) { post({ jsonrpc: "2.0", id: id, error: { code: code, message: message } }); }

  window.addEventListener("message", function (ev) {
    if (ev.source !== host) return;
    var m = ev.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && m.method === undefined) {
      var p = pending[m.id];
      if (!p) return;
      delete pending[m.id];
      if (m.error) p.reject(m.error); else p.resolve(m.result);
      return;
    }
    switch (m.method) {
      case "ui/notifications/tool-result": if (handlers.result) handlers.result(m.params || {}); break;
      case "ui/notifications/host-context-changed": applyContext(m.params || {}); break;
      case "ui/notifications/tool-input": case "ui/notifications/tool-input-partial":
      case "ui/notifications/tool-cancelled": case "ui/notifications/request-teardown": break;
      case "ui/resource-teardown": if (m.id !== undefined) reply(m.id, {}); break;
      case "ping": if (m.id !== undefined) reply(m.id, {}); break;
      default: if (m.id !== undefined) replyError(m.id, -32601, "Method not found");
    }
  });

  function applyContext(ctx) {
    if (!ctx) return;
    Object.keys(ctx).forEach(function (k) { hostCtx[k] = ctx[k]; });
    var root = document.documentElement;
    if (ctx.theme) root.setAttribute("data-theme", ctx.theme);
    var vars = ctx.styles && ctx.styles.variables;
    if (vars) Object.keys(vars).forEach(function (k) {
      root.style.setProperty(k.indexOf("--") === 0 ? k : "--" + k, String(vars[k]));
    });
    var fonts = ctx.styles && ctx.styles.css && ctx.styles.css.fonts;
    if (fonts && !document.getElementById("hostfonts")) {
      var st = document.createElement("style"); st.id = "hostfonts"; st.textContent = String(fonts);
      document.head.appendChild(st);
    }
    var ins = ctx.safeAreaInsets;
    if (ins) ["top", "right", "bottom", "left"].forEach(function (side) {
      if (typeof ins[side] === "number") root.style.setProperty("--inset-" + side, ins[side] + "px");
    });
    if (ctx.displayMode) root.setAttribute("data-display", String(ctx.displayMode));
    var dims = ctx.containerDimensions;
    if (dims && typeof dims.maxHeight === "number") root.style.setProperty("--max-h", dims.maxHeight + "px");
    if (handlers.context) handlers.context(hostCtx);
  }

  var sizeTimer = null;
  function reportSize() {
    if (sizeTimer) return;
    sizeTimer = setTimeout(function () {
      sizeTimer = null;
      var el = document.documentElement;
      notify("ui/notifications/size-changed", { width: el.scrollWidth, height: el.scrollHeight });
    }, 30);
  }
  if (window.ResizeObserver) {
    var ro = new ResizeObserver(reportSize);
    ro.observe(document.documentElement); ro.observe(document.body);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }
  function $(id) { return document.getElementById(id); }

  // A small Scryfall picture: by Scryfall id when the row has one, else by exact name.
  function scryfallImage(uid, name, version) {
    var v = version || "small";
    if (uid) return "https://api.scryfall.com/cards/" + encodeURIComponent(uid) + "?format=image&version=" + v;
    return "https://api.scryfall.com/cards/named?exact=" + encodeURIComponent(name || "") + "&format=image&version=" + v;
  }
  function picture(src, cls) {
    var img = el("img", cls || "pic");
    img.alt = ""; img.loading = "lazy"; img.decoding = "async"; img.referrerPolicy = "no-referrer";
    img.src = src;
    img.onerror = function () { img.classList.add("none"); img.onerror = null; };
    return img;
  }

  function openLink(url, fallback) {
    if (!url) return;
    request("ui/open-link", { url: String(url) }).catch(function () {
      if (fallback) fallback("Open this link in your browser: " + url);
    });
  }
  // What the person did on the card, for the assistant's next turn. Hosts that cannot carry
  // it ignore the request; the card already shows the result to the person.
  function tellModel(text) {
    return request("ui/update-model-context", { content: [{ type: "text", text: String(text) }] }).catch(function () {});
  }
  // A data value (a card or set name, a user's own words) quoted for the text sent to the model: one
  // line, at most 200 characters, in quotes, so it reads as data and not as part of the sentence.
  function quote(v) {
    var s = String(v == null ? "" : v).replace(/[\r\n\t]+/g, " ").replace(/"/g, "'").trim();
    if (s.length > 200) s = s.slice(0, 200) + "…";
    return '"' + s + '"';
  }
  function displayModes() {
    var modes = hostCaps.availableDisplayModes || (hostCtx.availableDisplayModes) || [];
    return Array.isArray(modes) ? modes : [];
  }
  function requestDisplayMode(mode) {
    return request("ui/request-display-mode", { mode: mode }).catch(function () {});
  }
  function setStatus(text, kind) {
    var s = $("status");
    if (!s) return;
    s.textContent = text || "";
    s.className = "status" + (kind ? " " + kind : "") + (text ? "" : " hidden");
    reportSize();
  }

  function start(appName, onResult, opts) {
    handlers.result = onResult;
    handlers.context = opts && opts.onContext;
    request("ui/initialize", {
      appInfo: { name: appName, version: "1" },
      appCapabilities: { availableDisplayModes: (opts && opts.displayModes) || ["inline"] },
      protocolVersion: PROTOCOL
    }).then(function (res) {
      hostCaps = (res && res.hostCapabilities) || {};
      applyContext(res && res.hostContext);
      notify("ui/notifications/initialized", {});
      reportSize();
    }).catch(function () {
      setStatus("This card could not connect to the chat. The same information is in the assistant's message.", "err");
    });
  }

  return {
    start: start, request: request, notify: notify, el: el, $: $, picture: picture, scryfallImage: scryfallImage,
    openLink: openLink, tellModel: tellModel, quote: quote, reportSize: reportSize, setStatus: setStatus,
    displayModes: displayModes, requestDisplayMode: requestDisplayMode, context: function () { return hostCtx; }
  };
})();
