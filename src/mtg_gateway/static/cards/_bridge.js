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

  // A label (a status, a risk): the word, its colour class and an icon, as the site's own labels
  // (ui.label). The icons are the site's icon set, drawn inline so the card loads nothing.
  // the icons as elements, built node by node (every card is one self-contained document)
  var ICONS = {
    clock: [["circle", {cx: "12", cy: "12", r: "9"}], ["path", {d: "M12 7v5l3 2"}]],
    check: [["path", {d: "M5 12l4 4L19 6"}]],
    alert: [["circle", {cx: "12", cy: "12", r: "9"}], ["path", {d: "M12 7v6M12 16.5v.5"}]],
    pause: [["path", {d: "M9 6v12M15 6v12"}]],
    refresh: [["path", {d: "M20 12a8 8 0 1 1-2.3-5.7"}], ["path", {d: "M20 4v5h-5"}]],
    spinner: [["path", {d: "M12 3a9 9 0 1 0 9 9"}]],
    x: [["path", {d: "M6 6l12 12M18 6L6 18"}]],
    back: [["path", {d: "M9 14L4 9l5-5"}], ["path", {d: "M4 9h10a6 6 0 0 1 0 12h-3"}]],
    arrows: [["path", {d: "M4 8h13l-3-3M20 16H7l3 3"}]],
    hourglass: [["path", {d: "M7 3h10M7 21h10M8 3c0 5 8 6 8 9s-8 4-8 9M16 3c0 5-8 6-8 9"}]],
    shield: [["path", {d: "M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"}]],
    triangle: [["path", {d: "M12 4l9 16H3z"}], ["path", {d: "M12 10v4M12 17v.5"}]],
    hand: [["path", {d: "M8 13V6a1.5 1.5 0 0 1 3 0v5M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v7c0 4-2.5 7-6 7-2.5 0-4-1.5-5.5-3.5L4 13.5a1.5 1.5 0 0 1 2.3-1.9L8 13"}]],
    trash: [["path", {d: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"}], ["path", {d: "M10 11v6M14 11v6"}]],
    link: [["path", {d: "M10 14a4 4 0 0 1 0-5.7l3-3a4 4 0 0 1 5.7 5.7l-1.5 1.5"}], ["path", {d: "M14 10a4 4 0 0 1 0 5.7l-3 3a4 4 0 0 1-5.7-5.7l1.5-1.5"}]],
    camera: [["path", {d: "M4 8h4l2-3h4l2 3h4v11H4z"}], ["circle", {cx: "12", cy: "13", r: "3"}]],
    stats: [["path", {d: "M4 20V10M10 20V4M16 20v-7M22 20H2"}]]
  };
  function icon(name) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "i"); svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
    (ICONS[name] || ICONS.check).forEach(function (part) {
      var node = document.createElementNS(ns, part[0]);
      Object.keys(part[1]).forEach(function (k) { node.setAttribute(k, part[1][k]); });
      svg.appendChild(node);
    });
    return svg;
  }
  // label(node, "Applied", "applied", "check"): the node reads as the word with its icon before it
  function label(node, text, cls, iconName) {
    node.replaceChildren();
    if (iconName) node.appendChild(icon(iconName));
    node.appendChild(document.createTextNode(text));
    node.className = "badge " + (cls || "");
    return node;
  }

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
      setStatus((opts && opts.connectError) ||
        "This card could not connect to the chat. The same information is in the assistant's message.", "err");
    });
  }

  return {
    start: start, request: request, notify: notify, el: el, $: $, icon: icon, label: label, picture: picture, scryfallImage: scryfallImage,
    openLink: openLink, tellModel: tellModel, quote: quote, reportSize: reportSize, setStatus: setStatus,
    displayModes: displayModes, requestDisplayMode: requestDisplayMode, context: function () { return hostCtx; }
  };
})();
