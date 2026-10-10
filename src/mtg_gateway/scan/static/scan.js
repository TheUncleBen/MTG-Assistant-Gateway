/* Card scanner page. Plain browser JavaScript, no build step, no third-party
   network calls: the OCR engine (Tesseract.js) is self-hosted under
   /scan/static/vendor and runs on the phone; card lookups go to this gateway,
   which asks Scryfall. */
(function () {
  'use strict';

  const cfg = JSON.parse(document.getElementById('scan-config').textContent);
  const root = document.getElementById('scan-app');
  // Keyed by an opaque per-user value from the server, so one account's draft never shows for another.
  const DRAFT_KEY = 'mtg-scan-draft-v1:' + (cfg.draft || 'anon');
  const OCR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ,'-/.";
  // The bottom-left info line of modern cards: "0472 R" over "CMR • EN" (★ instead of • on foils).
  const INFO_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/•★ ";
  const LANGS = ['EN', 'DE', 'FR', 'IT', 'ES', 'PT', 'JA', 'KO', 'RU', 'ZHS', 'ZHT', 'PH'];
  const T = Object.assign({ auto_add_confidence: 80, foil_star_ink_ratio: 0.09, glare_ratio: 0.08, min_ocr_confidence: 50 },
    cfg.thresholds || {});
  const G = window.ScanGeometry || null; // card edge detection and flattening (geometry.js)
  const PHOTO_MAX = 2400; // long side of the working copy of an uploaded photo, in pixels

  const state = {
    items: [],            // {quantity, name, status, note, card, foil}
    setLock: '',          // lowercase set code the user says every card comes from ('' = off)
    foilDefault: false,   // treat cards as foil unless the info line says otherwise
    sessionId: null,
    sessionName: '',
    dirty: false,
    tab: 'camera',
    stream: null,
    torch: false,         // camera torch (flash LED) on, where the track supports it
    continuous: false,    // keep scanning frames; certain matches are added without a tap
    scanning: false,      // a frame is being read right now
    autoAdded: [],        // undo stack for continuous mode: {key, name, quantity, at}
    unidentified: [],     // reads nobody could place: {text, info, strip, at}
    lastPicture: null,    // size of the last frame or photo copy that was read, in pixels
    lastAutoKey: null,    // the printing added from the last certain frame; the same card is not
                          // re-added until a different card is read with certainty or the card
                          // has left the frame (no outline for a few frames)
    rejectedKey: null,    // an auto-add the user undid: not re-added until the card has left
    noCardFrames: 0,      // consecutive continuous frames with no card outline
    lastRead: null,       // {key, res}: the previous frame's read, reused when a frame reads the same
    stats: { frames: 0, lookups: 0, reused: 0, skipped: 0 },
    ocr: null,            // Tesseract worker
    ocrState: cfg.ocr ? 'idle' : 'missing',
    pending: null,        // last resolution shown in the camera result box
    installPrompt: null,
  };

  // ---------------------------------------------------------------- helpers
  // Plain words for a failed request: fetch() rejects with a TypeError when there is no connection.
  const why = (err) => (err instanceof TypeError || !navigator.onLine)
    ? 'no connection to the gateway. Check your connection and try again'
    : (err && err.message) || String(err);
  const text = window.MtgText || {
    plural: (n, w) => n + ' ' + (Number(n) === 1 ? w : w + 's'),
    when: (v) => new Date(v).toLocaleString()
  };
  const cameraIcon = () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', 'i');
    svg.innerHTML = "<path d='M4 8h4l2-3h4l2 3h4v11H4z'/><circle cx='12' cy='13' r='3'/>";
    return svg;
  };
  const h = (tag, attrs, ...children) => {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    for (const c of children) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  };
  const $ = (sel) => root.querySelector(sel);
  const plural = (n, word) => n + ' ' + word + (n === 1 ? '' : 's');
  const buzz = (ms) => { try { if (navigator.vibrate) navigator.vibrate(ms); } catch (e) { /* ignore */ } };

  // The gateway runs one card lookup per account at a time (HTTP 429 "busy" otherwise). The
  // page never races itself: resolve calls are queued so only one is in flight, and a busy
  // answer caused by someone else's call for this account (an assistant, another tab) is
  // retried quietly with a short back-off before the error reaches the user.
  const RESOLVE_PATH = '/scan/api/resolve';
  const BUSY_RETRY_MS = [400, 900, 1600];
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let resolveQueue = Promise.resolve();

  function api(path, method, body) {
    if (path !== RESOLVE_PATH) return request(path, method, body, []);
    const run = resolveQueue.then(() => request(path, method, body, BUSY_RETRY_MS));
    resolveQueue = run.catch(() => {});
    return run;
  }

  async function request(path, method, body, busyRetries) {
    const opts = { method: method || 'GET', credentials: 'same-origin', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-CSRF-Token'] = cfg.csrf;
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(path, opts);
    let data = null;
    try { data = await resp.json(); } catch (e) { data = { ok: false, error: 'bad_response' }; }
    if (resp.status === 401) {
      clearDrafts();
      location.href = (data && data.login) || '/login?next=/scan';
      throw new Error('signed out');
    }
    if (resp.status === 429 && data && data.error === 'busy' && busyRetries.length) {
      await sleep(busyRetries[0]);
      return request(path, method, body, busyRetries.slice(1));
    }
    if (!resp.ok || !data.ok) throw new Error((data && data.message) || (data && data.error) || ('HTTP ' + resp.status));
    return data;
  }

  function clearDrafts() {
    try {
      Object.keys(localStorage).filter((k) => k.startsWith('mtg-scan-draft-')).forEach((k) => localStorage.removeItem(k));
    } catch (e) { /* ignore */ }
  }
  function saveDraft() {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({
        items: state.items, sessionId: state.sessionId, sessionName: state.sessionName, dirty: state.dirty,
        setLock: state.setLock, foilDefault: state.foilDefault,
      }));
    } catch (e) { /* storage may be unavailable */ }
  }
  function loadDraft() {
    try {
      // Drafts left by other accounts on this device are dropped, never shown.
      Object.keys(localStorage).filter((k) => k.startsWith('mtg-scan-draft-') && k !== DRAFT_KEY).forEach((k) => localStorage.removeItem(k));
      const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null');
      if (d && Array.isArray(d.items)) {
        state.items = d.items; state.sessionId = d.sessionId || null;
        state.sessionName = d.sessionName || ''; state.dirty = !!d.dirty;
      }
      if (d && typeof d.setLock === 'string') state.setLock = d.setLock;
      if (d && typeof d.foilDefault === 'boolean') state.foilDefault = d.foilDefault;
    } catch (e) { /* ignore */ }
  }

  function addItem(res, quantity) {
    const qty = Math.max(1, quantity || res.quantity || 1);
    const card = res.card || null;
    // One line per printing and finish: a foil and a non-foil copy of the same card stay apart.
    const foil = typeof res.foil === 'boolean' ? res.foil : undefined;
    const key = card ? card.scryfall_id : null;
    const existing = key && state.items.find((it) => it.card && it.card.scryfall_id === key && isFoil(it) === isFoil(res));
    if (existing) existing.quantity = Math.min(999, existing.quantity + qty);
    else state.items.push({ quantity: qty, name: card ? card.name : (res.input && res.input.name) || res.name || '',
      status: res.status || (card ? 'exact' : 'not_found'), note: res.note || '', card, foil });
    state.dirty = true;
    saveDraft();
    renderBadge();
  }

  const totalCards = () => state.items.reduce((n, it) => n + it.quantity, 0);
  // Foil as a plain yes/no: unknown counts as non-foil, in one place, so lines and keys agree.
  const isFoil = (x) => !!(x && x.foil === true);
  const lineKey = (x) => (x && x.card ? x.card.scryfall_id + '|' + String(isFoil(x)) : null);

  // ----------------------------------------------------------------- layout
  // A first-visit explanation of the three steps; "Got it" hides it for this browser, the "How it
  // works" link under the tabs brings it back.
  const INTRO_KEY = 'mtg-scan-intro-seen';
  const introSeen = () => { try { return localStorage.getItem(INTRO_KEY) === '1'; } catch (e) { return false; } };
  function introCard() {
    const step = (n, title, text) => h('li', null, h('span', { class: 'num', text: String(n) }), h('div', null, h('strong', { text: title }), ' ', text));
    const box = h('section', { class: 'card intro', id: 'intro' },
      h('h2', { text: 'How scanning works' }),
      h('ol', { class: 'steps' },
        step(1, 'Scan.', 'Hold a card in the frame with its title in the dashed box and tap the shutter, or switch on Auto and show cards one after another. No camera handy? Use the Type tab.'),
        step(2, 'Check the list.', 'Fix a wrong match, pick the exact printing or foil, change quantities.'),
        step(3, 'Choose what to do with the cards.', 'Save them to your collection, add them to one of your decks, start a new deck, or keep the scan for later (your assistant can pick it up too).')),
      h('div', { class: 'list-actions' }, h('button', { class: 'primary', onclick: () => { try { localStorage.setItem(INTRO_KEY, '1'); } catch (e) { /* private mode */ } box.remove(); } }, 'Got it')));
    return box;
  }

  function render() {
    root.textContent = '';
    if (!introSeen() && !state.items.length) root.append(introCard());
    const tabs = h('div', { class: 'tabs', role: 'tablist' },
      tabButton('camera', 'Camera'), tabButton('type', 'Type'), tabButton('list', 'List'), tabButton('sessions', 'Sessions'));
    root.append(tabs);
    root.append(h('div', { id: 'panel' }));
    root.append(h('p', { class: 'muted' }, 'Signed in as ', h('strong', { text: cfg.user }), '. ',
      'Scanned cards stay on this gateway until you delete them. ',
      h('a', { href: '#intro', onclick: (e) => { e.preventDefault(); if (!$('#intro')) root.prepend(introCard()); window.scrollTo(0, 0); } }, 'How it works')));
    const installBtn = h('button', { class: 'secondary install', id: 'install-btn', onclick: install }, 'Add to home screen');
    root.append(installBtn);
    if (state.installPrompt) installBtn.classList.add('show');
    showTab(state.tab);
  }
  function tabButton(id, label) {
    const b = h('button', { role: 'tab', 'data-tab': id, onclick: () => showTab(id) }, label);
    if (id === 'list') b.append(' ', h('span', { class: 'badge', id: 'list-badge', text: String(totalCards()) }));
    return b;
  }
  function renderBadge() { const b = $('#list-badge'); if (b) b.textContent = String(totalCards()); }

  function showTab(id) {
    if (state.tab === 'camera' && id !== 'camera') { state.continuous = false; stopCamera(); }
    state.tab = id;
    root.querySelectorAll('[role=tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === id)));
    const panel = $('#panel');
    panel.textContent = '';
    ({ camera: renderCamera, type: renderType, list: renderList, sessions: renderSessions })[id](panel);
  }

  // ----------------------------------------------------------------- camera
  /* Scan options: the set lock and foil default live on scan state and are set
     through the service's setSetLock / setFoilDefault below, which also save
     the draft. The field shows the code upper-case; the service stores it lower-case. */
  const scanOpts = {
    setLock: () => (getSetLock() || '').toUpperCase(),
    foil: () => !!getFoilDefault(),
    setSetLock: (code) => setSetLock(code),
    setFoil: (on) => setFoilDefault(on),
  };
  const SET_CODE = /^[A-Z0-9]{2,6}$/;

  function renderScanOptions() {
    const bar = h('div', { class: 'scan-opts', role: 'group', 'aria-label': 'Scan options' });
    const input = h('input', { type: 'text', id: 'set-lock', maxlength: '6', autocapitalize: 'characters', autocomplete: 'off',
      spellcheck: 'false', placeholder: 'Any', 'aria-describedby': 'set-lock-help', value: scanOpts.setLock() });
    const help = h('span', { id: 'set-lock-help', class: 'help', text: 'Set code, blank for any' });
    const field = h('label', { class: 'set-field', for: 'set-lock' }, h('span', { class: 'lbl', text: 'Set' }), input);
    const clear = h('button', { type: 'button', class: 'secondary clear', 'aria-label': 'Clear set lock', title: 'Clear set lock',
      onclick: () => { input.value = ''; applySet(); input.focus(); } }, '×');
    const applySet = () => {
      const code = input.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
      input.value = code;
      const ok = code === '' || SET_CODE.test(code);
      input.setAttribute('aria-invalid', ok ? 'false' : 'true');
      bar.classList.toggle('invalid', !ok);
      bar.classList.toggle('locked', ok && code !== '');
      help.textContent = !ok ? 'Use 2 to 6 letters or digits' : code ? 'Only ' + code + ' printings' : 'Set code, blank for any';
      if (ok) scanOpts.setSetLock(code);
    };
    input.addEventListener('input', () => { input.value = input.value.toUpperCase(); });
    input.addEventListener('change', applySet);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); applySet(); input.blur(); } });
    const sw = h('button', { type: 'button', id: 'foil-switch', class: 'switch', role: 'switch', 'aria-checked': scanOpts.foil() ? 'true' : 'false',
      onclick: () => { const on = sw.getAttribute('aria-checked') !== 'true'; sw.setAttribute('aria-checked', on ? 'true' : 'false'); scanOpts.setFoil(on); } },
      h('span', { class: 'knob' }), h('span', { class: 'lbl', text: 'Foil' }));
    const auto = h('button', { type: 'button', id: 'auto-switch', class: 'switch auto', role: 'switch', 'aria-checked': state.continuous ? 'true' : 'false',
      onclick: () => { if (state.continuous) stopContinuous(); else if (!startContinuous()) setStatus('Open the camera first to scan continuously.'); } },
      h('span', { class: 'knob' }), h('span', { class: 'lbl', text: 'Auto' }));
    document.addEventListener('scan:continuous', (e) => {
      auto.setAttribute('aria-checked', e.detail && e.detail.on ? 'true' : 'false');
      const sh = $('#shutter'); if (sh) sh.classList.toggle('live', !!(e.detail && e.detail.on));
    }, camSignal());
    bar.append(field, clear, help, sw, auto);
    applySet();
    return bar;
  }

  /* Listeners the camera tab's controls put on document are tied to one render: re-rendering the tab
     (every visit) aborts the previous set, so handlers for detached elements do not pile up. */
  let camListeners = null;
  const camSignal = () => ({ signal: camListeners ? camListeners.signal : undefined });

  function renderCamera(panel) {
    if (camListeners) camListeners.abort();
    camListeners = new AbortController();
    const video = h('video', { playsinline: true, autoplay: true, muted: true });
    const torch = h('button', { type: 'button', class: 'torch', id: 'torch', hidden: true, 'aria-pressed': 'false',
      'aria-label': 'Torch', title: 'Torch',
      onclick: () => setTorch(!getTorch()).then(() => torch.setAttribute('aria-pressed', getTorch() ? 'true' : 'false')) },
      h('span', { class: 'ico', 'aria-hidden': 'true', text: '⚡' }), h('span', { class: 'lbl', text: 'Torch' }));
    // The glare pill sits over the bottom of the viewfinder, where the eye already is, and shows
    // whenever the status text carries the glare warning.
    const glare = h('span', { class: 'glare-hint', id: 'glare-hint', role: 'status', hidden: true },
      h('span', { class: 'ico', 'aria-hidden': 'true', text: '☀' }), 'Glare: tilt the card away from the light');
    const cam = h('div', { class: 'camera', id: 'camera' }, video,
      h('div', { class: 'guide' }, h('div', { class: 'title' })),
      h('div', { class: 'hint', text: 'Fit the card in the frame, name in the dashed box' }),
      h('div', { class: 'flash', id: 'flash' }), torch, glare);
    const status = h('p', { class: 'muted', id: 'cam-status', text: '' });
    const file = h('input', { type: 'file', accept: 'image/*', capture: 'environment', id: 'photo-file', class: 'sr',
      onchange: (e) => e.target.files[0] && scanFile(e.target.files[0]) });
    const shutter = h('button', { class: 'shutter', id: 'shutter', title: 'Scan card', 'aria-label': 'Scan card',
      onclick: () => captureFrame(video) }, cameraIcon(), h('span', { class: 'sr', text: 'Scan' }));
    const controls = h('div', { class: 'controls' },
      h('label', { class: 'btn ctl', for: 'photo-file', title: 'Scan a photo from your pictures' }, 'Photo'), shutter,
      h('button', { class: 'ctl', title: 'Type the card name instead', onclick: () => showTab('type') }, 'Type'));
    // Inside the Android app, its own camera (torch brightness, zoom) is one tap away. The app
    // provides window.MtgNative; a browser has no such object and sees no button.
    const native = window.MtgNative;
    if (native && typeof native.openCamera === 'function') {
      controls.insertBefore(h('button', { class: 'ctl', id: 'phone-camera', onclick: () => native.openCamera() }, 'Phone camera'), shutter);
    }
    // Status and the match result sit between the preview and the control bar,
    // so on a phone the thing to confirm is right above the thumb.
    panel.append(h('div', { class: 'cam-panel' }, renderScanOptions(), cam, status, h('div', { id: 'result' }), controls, file));
    if (state.ocrState === 'missing') {
      status.textContent = 'On-device text recognition is not installed on this gateway; use Photo or Type, or ask the owner to run the asset fetch step.';
    }
    const camCtl = h('div', { class: 'cam-ctl', id: 'cam-ctl', hidden: true });
    cam.after(camCtl);
    cam.append(renderToast());
    status.after(renderUnidentified());
    startCamera(video).then(() => { if (cfg.ocr) ensureOcr(); });
    if (state.pending) showResult(state.pending);
  }

  /* Continuous mode: a toast over the viewfinder for each automatic add, with Undo. */
  function renderToast() {
    const text = h('span', { class: 'txt' });
    const undo = h('button', { type: 'button', class: 'undo', onclick: () => { if (!undoLastAdd()) hide(); } }, 'Undo');
    const toast = h('div', { class: 'toast', id: 'auto-toast', role: 'status', hidden: true }, text, undo);
    let timer = null;
    const hide = () => { toast.hidden = true; };
    const show = (msg, canUndo, ms) => {
      text.textContent = msg; undo.hidden = !canUndo; toast.hidden = false;
      clearTimeout(timer); timer = setTimeout(hide, ms);
    };
    document.addEventListener('scan:auto-added', (e) => {
      const d = e.detail || {}; const c = d.card || {};
      const where = c.set ? ' (' + c.set.toUpperCase() + ' ' + c.collector_number + ')' : '';
      show('Added ' + (d.name || '?') + where + (d.foil ? ', foil' : '') + ' · ' + (d.total != null ? plural(d.total, 'card') : ''), true, 6000);
    }, camSignal());
    document.addEventListener('scan:undo', (e) => {
      const d = e.detail || {};
      show('Removed ' + (d.name || '?') + ' · ' + (d.total != null ? plural(d.total, 'card') : ''), state.autoAdded.length > 0, 4000);
    }, camSignal());
    document.addEventListener('scan:continuous', (e) => { if (!(e.detail && e.detail.on)) hide(); }, camSignal());
    return toast;
  }

  /* Reads nobody could place: a counter under the status that opens a tray of the strips read,
     each with the suggestions to tap or a Type button that carries the text to the Type tab. */
  function renderUnidentified() {
    const count = h('span', { class: 'count', text: '0' });
    const list = h('ul', { class: 'plain unid-list', id: 'unid-list' });
    const tray = h('div', { class: 'unid-tray', hidden: true }, h('p', { class: 'muted', text: 'Cards the scanner could not place. Tap a suggestion, or type the name.' }), list);
    const toggle = h('button', { type: 'button', class: 'secondary unid-toggle', id: 'unid-toggle', 'aria-expanded': 'false', hidden: true,
      onclick: () => { tray.hidden = !tray.hidden; toggle.setAttribute('aria-expanded', tray.hidden ? 'false' : 'true'); } },
      count, ' unidentified');
    const draw = () => {
      const n = state.unidentified.length;
      count.textContent = String(n);
      toggle.hidden = n === 0;
      if (n === 0) { tray.hidden = true; toggle.setAttribute('aria-expanded', 'false'); }
      list.textContent = '';
      state.unidentified.slice().reverse().forEach((u) => {
        const chips = h('div', { class: 'chips' });
        (u.suggestions || []).slice(0, 4).forEach((name) => chips.append(h('button', { type: 'button', class: 'secondary',
          onclick: () => { dropUnidentified(u); pickName(name); } }, name)));
        const typeBtn = h('button', { type: 'button', class: 'primary', onclick: () => {
          dropUnidentified(u); showTab('type');
          const inp = $('#type-input'); if (inp) { inp.value = u.text || ''; inp.dispatchEvent(new Event('input')); inp.focus(); }
        } }, 'Type the name');
        const dismiss = h('button', { type: 'button', class: 'secondary', 'aria-label': 'Dismiss', onclick: () => dropUnidentified(u) }, '×');
        list.append(h('li', null,
          u.strip ? h('img', { class: 'strip', src: u.strip, alt: 'Title strip as read' }) : '',
          h('div', { class: 'read' }, h('span', { class: 'ocr', text: u.text ? 'read: ' + u.text : 'nothing readable' }), chips,
            h('div', { class: 'actions' }, typeBtn, dismiss))));
      });
    };
    const dropUnidentified = (u) => { const i = state.unidentified.indexOf(u); if (i >= 0) state.unidentified.splice(i, 1); draw(); };
    document.addEventListener('scan:unidentified', draw, camSignal());
    draw();
    return h('div', { class: 'unid', id: 'unid' }, toggle, tray);
  }

  /* Zoom and brightness sliders, each only when the camera reports the range. */
  function renderCameraControls(box) {
    const ranges = cameraRanges();
    box.textContent = '';
    const specs = [
      { key: 'zoom', label: 'Zoom', ico: '🔍', fmt: (v) => v.toFixed(1) + '×', reset: (r) => r.min },
      { key: 'exposureCompensation', label: 'Brightness', ico: '☀', fmt: (v) => (v > 0 ? '+' : '') + v.toFixed(1),
        reset: (r) => Math.min(r.max, Math.max(r.min, 0)) },
    ];
    specs.forEach((sp) => {
      const r = ranges[sp.key]; if (!r) return;
      const out = h('output', { text: sp.fmt(r.value) });
      const input = h('input', { type: 'range', min: String(r.min), max: String(r.max), step: String(r.step || (r.max - r.min) / 20),
        value: String(r.value), 'aria-label': sp.label,
        oninput: (e) => { out.textContent = sp.fmt(parseFloat(e.target.value)); },
        onchange: (e) => { setCamera({ [sp.key]: parseFloat(e.target.value) }); } });
      const reset = h('button', { type: 'button', class: 'secondary reset', 'aria-label': 'Reset ' + sp.label.toLowerCase(),
        onclick: () => { const v = sp.reset(r); input.value = String(v); out.textContent = sp.fmt(v); setCamera({ [sp.key]: v }); } }, '↺');
      box.append(h('label', { class: 'ctl' }, h('span', { class: 'ico', 'aria-hidden': 'true', text: sp.ico }),
        h('span', { class: 'lbl', text: sp.label }), input, out, reset));
    });
    box.hidden = !box.children.length;
  }

  async function startCamera(video) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus('This browser cannot open the camera here; use the Photo button.');
      $('#shutter').disabled = true;
      return;
    }
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1440 } }, audio: false,
      });
      video.srcObject = state.stream;
      await video.play().catch(() => {});
      syncCameraUi();
    } catch (err) {
      setStatus('Camera unavailable: ' + cameraProblem(err) + '. Use the Photo button or type the name.');
      const sh = $('#shutter'); if (sh) sh.disabled = true;
      syncCameraUi();
    }
  }
  function stopCamera() {
    if (state.stream) { state.stream.getTracks().forEach((t) => t.stop()); state.stream = null; }
    state.torch = false;
  }

  /* Torch (flash LED) through the camera track. Chrome on Android exposes it as a track
     capability; iOS Safari does not, so torchSupported() is false there and controls stay hidden. */
  function videoTrack() { return state.stream ? state.stream.getVideoTracks()[0] || null : null; }
  function torchSupported() {
    const t = videoTrack();
    try { return !!(t && t.getCapabilities && t.getCapabilities().torch); } catch (e) { return false; }
  }
  async function setTorch(on) {
    const t = videoTrack();
    if (!t || !torchSupported()) return false;
    try { await t.applyConstraints({ advanced: [{ torch: !!on }] }); state.torch = !!on; return true; } catch (e) { return false; }
  }
  const getTorch = () => state.torch;
  /* Other camera controls the track may report: zoom and exposure compensation (brightness).
     Chrome on Android exposes both when the camera does; Safari exposes zoom from 17.0. Each
     is a {min, max, step} range or null, and setCamera applies one or more at once. */
  function cameraRanges() {
    const t = videoTrack(); const out = { zoom: null, exposureCompensation: null };
    if (!t || !t.getCapabilities) return out;
    let caps = {}; let cur = {};
    try { caps = t.getCapabilities() || {}; cur = t.getSettings ? t.getSettings() || {} : {}; } catch (e) { return out; }
    ['zoom', 'exposureCompensation'].forEach((k) => {
      const c = caps[k];
      if (c && typeof c.min === 'number' && typeof c.max === 'number' && c.max > c.min) {
        out[k] = { min: c.min, max: c.max, step: c.step || 0, value: typeof cur[k] === 'number' ? cur[k] : c.min };
      }
    });
    return out;
  }
  async function setCamera(values) {
    const t = videoTrack(); if (!t) return false;
    const ranges = cameraRanges(); const clamped = {};
    Object.keys(values || {}).forEach((k) => {
      const r = ranges[k]; let v = values[k];
      if (r && typeof v === 'number') v = Math.min(r.max, Math.max(r.min, v));
      clamped[k] = v;
    });
    try { await t.applyConstraints({ advanced: [clamped] }); return true; } catch (e) { return false; }
  }
  /* The torch button and the sliders follow the track: called whenever the camera (re)starts. */
  function syncCameraUi() {
    const tb = $('#torch');
    if (tb) { tb.hidden = !torchSupported(); tb.setAttribute('aria-pressed', getTorch() ? 'true' : 'false'); }
    const box = $('#cam-ctl'); if (box) renderCameraControls(box);
  }
  // A themed yes/no bar next to the button that asked (never the browser's confirm box).
  function askConfirm(anchor, text, onYes) {
    const old = anchor.parentNode && anchor.parentNode.querySelector(':scope > .confirmbar');
    if (old) old.remove();
    const yes = h('button', { class: 'danger', type: 'button' }, 'Yes');
    const no = h('button', { class: 'secondary', type: 'button' }, 'Cancel');
    const bar = h('div', { class: 'confirmbar', role: 'alertdialog', 'aria-label': text }, h('span', { text: text }), yes, no);
    const done = () => { bar.remove(); anchor.focus(); };
    no.addEventListener('click', done);
    yes.addEventListener('click', () => { bar.remove(); onYes(); });
    bar.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); done(); } });
    anchor.insertAdjacentElement('afterend', bar);
    yes.focus();
  }
  // Plain words for the camera API's error names.
  const CAMERA_ERRORS = {
    NotFoundError: 'no camera was found on this device',
    DevicesNotFoundError: 'no camera was found on this device',
    NotAllowedError: 'the browser was not allowed to use the camera',
    PermissionDeniedError: 'the browser was not allowed to use the camera',
    NotReadableError: 'another app is using the camera',
    TrackStartError: 'another app is using the camera',
    OverconstrainedError: 'no camera matches the requested settings',
    SecurityError: 'the camera is blocked on this page',
    AbortError: 'the camera stopped unexpectedly'
  };
  function cameraProblem(err) {
    return (err && CAMERA_ERRORS[err.name]) || 'the camera could not be started';
  }
  function setStatus(text) {
    const s = $('#cam-status'); if (s) s.textContent = text;
    const glare = /glare on the card/i.test(text || '');
    const g = $('#glare-hint'); if (g) g.hidden = !glare;
    const cam = $('#camera'); if (cam) cam.classList.toggle('glare', glare);
  }

  let ocrLoading = null;
  async function ensureOcr() {
    if (state.ocr || state.ocrState === 'missing' || state.ocrState === 'failed') return state.ocr;
    if (window.__scanOcrOverride) { state.ocrState = 'ready'; return null; }
    // A scan started while the engine is still loading waits for it instead of failing.
    if (state.ocrState === 'loading' && ocrLoading) { await ocrLoading; return state.ocr; }
    ocrLoading = loadOcr();
    await ocrLoading;
    return state.ocr;
  }
  async function loadOcr() {
    state.ocrState = 'loading';
    setStatus('Loading text recognition (one-time download, about 8 MB)…');
    try {
      await loadScript(cfg.static + '/vendor/tesseract.min.js');
      const worker = await window.Tesseract.createWorker('eng', 1, {
        workerPath: cfg.static + '/vendor/worker.min.js',
        corePath: cfg.static + '/vendor/core',
        langPath: cfg.static + '/vendor/lang',
        gzip: false,
        workerBlobURL: false,
        logger: (m) => { if (m && m.status && typeof m.progress === 'number') setStatus(m.status + ' ' + Math.round(m.progress * 100) + '%'); },
      });
      await worker.setParameters({ tessedit_pageseg_mode: '7', tessedit_char_whitelist: OCR_WHITELIST, preserve_interword_spaces: '1' });
      state.ocr = worker; state.ocrState = 'ready';
      setStatus('Ready. Hold the card flat and tap Scan.');
    } catch (err) {
      state.ocrState = 'failed';
      setStatus('Text recognition failed to load (' + (err && err.message || err) + '). You can still type names.');
    }
  }
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (window.Tesseract) return resolve();
      const s = document.createElement('script');
      s.src = src; s.onload = resolve; s.onerror = () => reject(new Error('could not load ' + src));
      document.head.append(s);
    });
  }

  /* Map the dashed title box (CSS percentages of the guide, which is centred
     at 78% of the preview width with a 63:88 aspect) back to pixels of the
     video frame, which is shown with object-fit: cover. */
  function guideGeometry(video, el) {
    const vw = video.videoWidth, vh = video.videoHeight;
    const ew = el.clientWidth, eh = el.clientHeight;
    const scale = Math.max(ew / vw, eh / vh);
    const offX = (ew - vw * scale) / 2, offY = (eh - vh * scale) / 2;
    const gw = ew * 0.78, gh = gw * 88 / 63;
    return { scale, offX, offY, gw, gh, gx: (ew - gw) / 2, gy: (eh - gh) / 2 };
  }
  function titleRegion(video, el) {
    const g = guideGeometry(video, el);
    const tx = g.gx + g.gw * 0.04, ty = g.gy + g.gh * 0.04, tw = g.gw * 0.92 * 0.80, th = g.gh * 0.085; // skip the mana cost on the right
    return { x: (tx - g.offX) / g.scale, y: (ty - g.offY) / g.scale, w: tw / g.scale, h: th / g.scale };
  }

  function preprocess(source, region, targetH) {
    targetH = targetH || 96;
    const scale = targetH / region.h;
    const c = document.createElement('canvas');
    c.width = Math.round(region.w * scale); c.height = targetH;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(source, region.x, region.y, region.w, region.h, 0, 0, c.width, c.height);
    const img = ctx.getImageData(0, 0, c.width, c.height);
    const d = img.data; const n = d.length / 4;
    const gray = new Float32Array(n); let lo = 255, hi = 0;
    for (let i = 0; i < n; i++) {
      const g = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
      gray[i] = g; if (g < lo) lo = g; if (g > hi) hi = g;
    }
    // Stretch contrast between the 2nd and 98th percentile.
    const hist = new Uint32Array(256); for (let i = 0; i < n; i++) hist[gray[i] | 0]++;
    let acc = 0, p2 = 0, p98 = 255;
    for (let v = 0; v < 256; v++) { acc += hist[v]; if (acc >= n * 0.02) { p2 = v; break; } }
    acc = 0; for (let v = 255; v >= 0; v--) { acc += hist[v]; if (acc >= n * 0.02) { p98 = v; break; } }
    const span = Math.max(1, p98 - p2);
    // Card titles are dark text on a light bar for most frames; invert when the bar is dark.
    let sum = 0; for (let i = 0; i < n; i++) sum += gray[i];
    const invert = sum / n < 110;
    for (let i = 0; i < n; i++) {
      let v = Math.max(0, Math.min(255, ((gray[i] - p2) / span) * 255));
      if (invert) v = 255 - v;
      d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = v; d[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return c;
  }

  function cleanOcr(text) {
    let t = (text || '').replace(/[^A-Za-z0-9 ,'\-\/.]/g, ' ').replace(/\s+/g, ' ').trim();
    t = t.replace(/^[^A-Za-z]+/, '').replace(/[\s,\-\/.']+$/, '');
    // Mana symbols that slipped through read as lone digits or single letters at the end.
    t = t.replace(/(\s+[0-9A-Za-z]){1,3}$/, (m) => (/^(\s+[A-Z])+$/.test(m) || /\d/.test(m) ? '' : m));
    return t.trim();
  }

  /* kind: 'title' (one line of mixed case) or 'info' (two short upper-case lines). The info read
     also returns word boxes so the glyph between set code and language can be measured. */
  async function recognize(canvas, kind) {
    if (window.__scanOcrOverride) return window.__scanOcrOverride(canvas, kind || 'title');
    const worker = await ensureOcr();
    if (!worker) throw new Error('text recognition is not available');
    const info = kind === 'info';
    await worker.setParameters({
      tessedit_pageseg_mode: info ? '6' : '7',
      tessedit_char_whitelist: info ? INFO_WHITELIST : OCR_WHITELIST,
      preserve_interword_spaces: '1',
    });
    const { data } = await worker.recognize(canvas, {}, info ? { text: true, blocks: true } : { text: true });
    const words = [];
    (data.blocks || []).forEach((b) => (b.paragraphs || []).forEach((p) => (p.lines || []).forEach((l) =>
      (l.words || []).forEach((w) => words.push({ text: w.text, bbox: w.bbox, confidence: w.confidence })))));
    return { text: data.text, confidence: data.confidence, words };
  }

  /* Bottom-left info block of a modern-frame card, as a fraction of the card guide. */
  function infoRegionOf(gx, gy, gw, gh) {
    return { x: gx + gw * 0.03, y: gy + gh * 0.925, w: gw * 0.44, h: gh * 0.06 };
  }
  function infoRegion(video, el) {
    const g = guideGeometry(video, el);
    const r = infoRegionOf(g.gx, g.gy, g.gw, g.gh);
    return { x: (r.x - g.offX) / g.scale, y: (r.y - g.offY) / g.scale, w: r.w / g.scale, h: r.h / g.scale };
  }

  /* "0472 R\nCMR • EN" -> {number: '472', rarity: 'R', set: 'cmr', lang: 'en', foil: true|false|null}.
     Collector numbers lose their leading zeros, which is how Scryfall keys them. */
  function parseInfoLine(text, words, canvas) {
    const out = { number: '', rarity: '', set: '', lang: '', foil: null, raw: (text || '').trim() };
    const lines = (text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    for (const line of lines) {
      const num = line.match(/^(\d{1,4})(?:\s*\/\s*\d{1,4})?(?:\s+([CURMSLT]))?\b/);
      if (num && !out.number) { out.number = num[1].replace(/^0+(?=\d)/, ''); out.rarity = num[2] || ''; continue; }
      const langRe = new RegExp('\\b(' + LANGS.join('|') + ')\\b');
      const lm = line.match(langRe);
      if (lm) {
        const before = line.slice(0, lm.index);
        const setm = before.match(/\b([A-Z0-9]{2,6})\b/);
        if (setm && /[A-Z]/.test(setm[1])) out.set = setm[1].toLowerCase();
        out.lang = lm[1].toLowerCase();
        if (/★/.test(before)) out.foil = true;
        else if (/•/.test(before)) out.foil = false;
        else if (words && canvas && out.set) out.foil = foilFromGlyph(words, out.set, lm[1], canvas);
      }
    }
    return out;
  }

  /* Tesseract rarely reads • or ★ as such. Measure the ink of whatever sits between the set code
     and the language on the preprocessed (black on white) strip: a star is far heavier than a dot. */
  function foilFromGlyph(words, setCode, lang, canvas) {
    const setW = words.find((w) => (w.text || '').toUpperCase().replace(/[^A-Z0-9]/g, '') === setCode.toUpperCase());
    const langW = words.find((w) => (w.text || '').toUpperCase().replace(/[^A-Z]/g, '') === lang.toUpperCase());
    if (!setW || !langW || !setW.bbox || !langW.bbox) return null;
    const x0 = Math.max(0, Math.floor(setW.bbox.x1) + 1), x1 = Math.min(canvas.width, Math.ceil(langW.bbox.x0) - 1);
    const y0 = Math.max(0, Math.floor(Math.min(setW.bbox.y0, langW.bbox.y0)));
    const y1 = Math.min(canvas.height, Math.ceil(Math.max(setW.bbox.y1, langW.bbox.y1)));
    if (x1 - x0 < 3 || y1 - y0 < 3) return null;
    const d = canvas.getContext('2d', { willReadFrequently: true }).getImageData(x0, y0, x1 - x0, y1 - y0).data;
    let dark = 0; const n = d.length / 4;
    for (let i = 0; i < n; i++) if (d[i * 4] < 128) dark++;
    const ratio = dark / n;
    return ratio >= T.foil_star_ink_ratio;
  }

  const setSetLock = (code) => { state.setLock = String(code || '').trim().toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 6); saveDraft(); };
  const getSetLock = () => state.setLock;
  const setFoilDefault = (on) => { state.foilDefault = !!on; saveDraft(); };
  const getFoilDefault = () => state.foilDefault;

  /* The card guide box in video pixels (where the card should be). */
  function guideRegion(video, el) {
    const g = guideGeometry(video, el);
    return { x: (g.gx - g.offX) / g.scale, y: (g.gy - g.offY) / g.scale, w: g.gw / g.scale, h: g.gh / g.scale };
  }
  /* Title and info crops on a flattened (edge-detected, perspective-corrected) card. With the
     geometry known, the title crop runs wider than the guide's dashed box: long names reach the
     mana cost, and cleanOcr drops the symbols that slip in. */
  function flatTitleRegion(w, h) { return { x: w * 0.045, y: h * 0.035, w: w * 0.86, h: h * 0.085 }; }

  /* Find the card's edges and flatten it; null when no convincing outline is found, in which
     case the caller reads the guide-box regions of the raw frame as before. */
  function flattenCard(source, rect) {
    if (!G || window.__scanNoFlatten) return null;
    try { return G.flattenCard(source, rect); } catch (e) { return null; }
  }
  function flatRegions(flat) {
    return { title: flatTitleRegion(flat.width, flat.height), info: infoRegionOf(0, 0, flat.width, flat.height) };
  }

  async function captureFrame(video) {
    const cam = $('#camera');
    if (!video.videoWidth) { setStatus('Camera is not ready yet.'); return; }
    const flash = $('#flash'); flash.classList.add('on'); setTimeout(() => flash.classList.remove('on'), 60);
    const frame = document.createElement('canvas');
    frame.width = video.videoWidth; frame.height = video.videoHeight;
    frame.getContext('2d').drawImage(video, 0, 0);
    state.lastPicture = { w: frame.width, h: frame.height };
    const flat = flattenCard(frame, guideRegion(video, cam));
    if (flat) {
      state.noCardFrames = 0; // an outline was found, whatever the title read says
      const r = flatRegions(flat); await scanRegion(flat.canvas, r.title, r.info, flat); return;
    }
    if (state.continuous) { noCardFrame(); return; } // no outline: nothing to read, and no lookup
    await scanRegion(frame, titleRegion(video, cam), infoRegion(video, cam), null);
  }

  async function scanFile(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
      // Phone photos run to 50 MP; a working copy with the long side at most PHOTO_MAX px holds
      // every detail the OCR can use and keeps the tab's memory bounded.
      const k = Math.min(1, PHOTO_MAX / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      state.lastPicture = { w: c.width, h: c.height };
      // A photo of one card: find its edges anywhere in the picture and flatten it. Failing
      // that, assume the card fills the picture: title in the top band, info line bottom left.
      const flat = flattenCard(c, null);
      if (flat) { const r = flatRegions(flat); await scanRegion(flat.canvas, r.title, r.info, flat); return; }
      await scanRegion(c, { x: c.width * 0.06, y: c.height * 0.035, w: c.width * 0.72, h: c.height * 0.085 },
        infoRegionOf(0, 0, c.width, c.height), null);
    } catch (e) {
      setStatus('Could not read that photo.');
    } finally { URL.revokeObjectURL(url); }
  }

  /* Read the info line (set code, collector number, language, foil star) if the crop is given.
     Any failure here just means "unknown"; the title read decides whether a scan succeeded. */
  async function readInfoLine(source, region) {
    if (!region) return null;
    try {
      const strip = preprocess(source, region, 96);
      const ocr = await recognize(strip, 'info');
      const info = parseInfoLine(ocr.text, ocr.words, strip);
      info.confidence = ocr.confidence;
      return info;
    } catch (e) { return null; }
  }

  /* What the resolver is asked for: the title as read, plus set and number when the info line
     read cleanly (or the set lock supplies the set), and foil from the star or the default.
     Without a number the set alone still goes along, so a set lock narrows a name lookup. */
  function cardRequest(text, info) {
    const req = { name: text };
    const set = (info && info.set) || state.setLock;
    if (set && info && info.number) { req.set = set; req.collector_number = info.number; }
    else if (set) req.set = set;
    if (info && info.lang) req.lang = info.lang;
    if (info && typeof info.foil === 'boolean') req.foil = info.foil;
    else if (state.foilDefault) req.foil = true;
    return req;
  }

  /* Share of blown-out pixels in the title crop, before contrast stretching hides it. */
  function glareOf(source, region) {
    if (!G) return 0;
    try {
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(region.w / 4)); c.height = Math.max(1, Math.round(region.h / 4));
      c.getContext('2d').drawImage(source, region.x, region.y, region.w, region.h, 0, 0, c.width, c.height);
      return G.glareRatio(c);
    } catch (e) { return 0; }
  }

  /* source: the flattened card or the raw frame; region/info: title and info crops in its pixels;
     flat: the flattening result (corners, score) or null when the guide box was used as is. */
  async function scanRegion(source, region, info, flat) {
    const cam = $('#camera'); const shutter = $('#shutter');
    cam && cam.classList.add('busy'); if (shutter) shutter.disabled = true;
    setStatus('Reading the card name…');
    try {
      const glare = glareOf(source, region);
      const glareHint = glare >= T.glare_ratio ? ' Glare on the card: tilt it away from the light.' : '';
      const strip = preprocess(source, region);
      const ocr = await recognize(strip, 'title');
      const text = cleanOcr(ocr.text);
      if (text.length < 3) {
        setStatus('Could not read a name.' + (glareHint || ' Move closer, avoid glare, and keep the title inside the dashed box.'));
        return;
      }
      if (state.continuous) {
        state.stats.frames++;
        if (ocr.confidence != null && ocr.confidence < T.min_ocr_confidence) {
          // Too shaky to be worth a lookup: keep the read for later without asking the server.
          state.stats.skipped++;
          keepUnidentified({ status: 'not_found', card: null, suggestions: [],
            ocr: { text, confidence: ocr.confidence, strip: strip.toDataURL('image/png'), info: null } }, glareHint);
          return;
        }
      }
      setStatus('Reading the set and number…');
      const infoRead = await readInfoLine(source, info);
      const req = cardRequest(text, infoRead);
      // Reuse is keyed on the title and info line as read; the art hash differs frame to frame.
      const readKey = JSON.stringify(req);
      if (flat && window.ScanArt) { const ah = window.ScanArt.hashCard(source); if (ah) req.art_hash = ah; }
      let res;
      if (state.continuous && state.lastRead && state.lastRead.key === readKey) {
        // The same title and info line as the previous frame: the answer has not changed.
        state.stats.reused++;
        res = Object.assign({}, state.lastRead.res);
      } else {
        setStatus('Looking up “' + text + '”' + (req.set && req.collector_number ? ' (' + req.set.toUpperCase() + ' ' + req.collector_number + ')' : '') + '…');
        const out = await api('/scan/api/resolve', 'POST', { cards: [req] });
        state.stats.lookups++;
        res = out.cards[0];
        if (state.continuous) state.lastRead = { key: readKey, res: Object.assign({}, res) };
      }
      res.ocr = { text, confidence: ocr.confidence, strip: strip.toDataURL('image/png'), info: infoRead,
        glare, flattened: !!flat, edges: flat ? flat.score : null, sampled: flat ? flat.sampled : null,
        picture: state.lastPicture };
      if (state.continuous) { await handleContinuous(res, glareHint); return; }
      state.pending = res; showResult(res);
      setStatus((res.card ? 'Matched. Tap Add, or pick another card.' : 'No match. Pick from the suggestions or type the name.') + glareHint);
      buzz(res.status === 'exact' ? 30 : [20, 40, 20]);
    } catch (err) {
      setStatus('Scan failed: ' + why(err));
    } finally {
      cam && cam.classList.remove('busy'); if (shutter) shutter.disabled = false;
    }
  }

  // ------------------------------------------------------- printing picker
  /* Every printing of a card, for the picker (art thumbnails come from card.image_art). */
  async function loadPrints(oracleId) {
    const out = await api('/scan/api/prints?oracle_id=' + encodeURIComponent(oracleId || ''), 'GET');
    return out.cards || [];
  }
  /* Finishes a printing comes in; a one-finish printing decides foil on its own. No finish list
     (items saved before finishes were recorded) means unknown, so both choices stay open. */
  function foilChoices(card) {
    const f = (card && card.finishes) || [];
    if (!f.length) return { foil: true, nonfoil: true };
    return { foil: f.includes('foil') || f.includes('etched'), nonfoil: f.includes('nonfoil') };
  }
  /* Swap the printing on a result or a list item for another printing of the same card. Returns
     false (and changes nothing) for a different card. */
  function choosePrinting(target, print) {
    if (!target || !target.card || !print || !print.scryfall_id) return false;
    // Same card only: by oracle id when the target has one (the print must then carry it too),
    // else by name for items saved before oracle ids were recorded.
    if (target.card.oracle_id) { if (target.card.oracle_id !== print.oracle_id) return false; }
    else if (print.name !== target.card.name) return false;
    target.card = print;
    target.status = 'printing';
    target.note = 'printing chosen by hand';
    const can = foilChoices(print);
    if (!can.nonfoil) target.foil = true; else if (!can.foil) target.foil = false;
    const merged = lineChanged(target);
    emit('printing', { name: print.name, set: print.set, collector_number: print.collector_number, foil: target.foil, merged });
    return true;
  }
  /* After a list line's printing or finish changed: save, and fold it into another line that now
     names the same printing and finish (quantities summed, the moved line removed). Returns true
     when lines were merged. A pending result is not a line and nothing happens. */
  function lineChanged(target) {
    const i = state.items.indexOf(target);
    if (i < 0) return false;
    const twin = state.items.find((it) => it !== target && it.card && target.card
      && it.card.scryfall_id === target.card.scryfall_id && isFoil(it) === isFoil(target));
    if (twin) { twin.quantity = Math.min(999, twin.quantity + target.quantity); state.items.splice(i, 1); }
    state.dirty = true; saveDraft(); renderBadge();
    return !!twin;
  }
  /* Foil on or off for a result or a list item; refused when the printing has no such finish. */
  function setFoil(target, on) {
    if (!target) return false;
    const can = foilChoices(target.card);
    if (on && !can.foil) return false;
    if (!on && !can.nonfoil) return false;
    target.foil = !!on;
    lineChanged(target);
    return true;
  }

  // ------------------------------------------------------- continuous scan
  /* Confidence tiers. 'certain': two signals agree on the card (the title and the collector
     line, status 'printing'), or the title alone names a card that begins no other card's name
     (a title cut short by the strip could not have been that card), and the title was read cleanly
     (OCR confidence known and at or above auto_add_confidence, no glare). It is added without a
     tap. 'unsure': a card was found but something was shaky, so it waits for a tap. 'failed': no
     card; the read goes to the unidentified list for later. */
  async function tierOf(res) {
    if (!res || !res.card) return 'failed';
    const ocr = res.ocr || {};
    const clean = ocr.confidence != null && ocr.confidence >= T.auto_add_confidence && !(ocr.glare >= T.glare_ratio);
    if (!clean || res.conflict || (res.art && res.art.status === 'mismatch')) return 'unsure';
    if (res.status === 'printing') return 'certain';
    if (res.status === 'exact' && await nameIsComplete(res.card.name)) return 'certain';
    return 'unsure';
  }
  /* True when no other card's name begins with this one ("Sol Ring" yes; "Mountain" no, because
     of Mountain Goat), so a clean read of it cannot be a longer name cut short. One cached
     autocomplete lookup per distinct name. Fails closed: an exact-matched name always completes
     to itself, so a list without it (empty on an error, or a query the server declined) means the
     check did not happen, and that answer is not cached. */
  const completeNames = new Map();
  async function nameIsComplete(name) {
    const key = name.toLowerCase();
    if (completeNames.has(key)) return completeNames.get(key);
    const norm = (s) => s.split('//')[0].toLowerCase().replace(/[^a-z0-9]/g, '');
    let names;
    try { names = (await api('/scan/api/search?q=' + encodeURIComponent(name), 'GET')).names || []; }
    catch (e) { return false; }
    if (!names.some((n) => norm(n) === norm(name))) return false;
    const ok = names.every((n) => norm(n) === norm(name) || !norm(n).startsWith(norm(name)));
    completeNames.set(key, ok);
    return ok;
  }
  const emit = (name, detail) => document.dispatchEvent(new CustomEvent('scan:' + name, { detail }));

  /* A continuous frame with no card outline: after a few in a row the card has left the frame,
     so the same card may be added again and an undone card may come back. A long run of them
     means the edges cannot be found (busy or same-colour background, geometry.js missing), so
     say what to do, since continuous mode reads nothing without an outline. */
  const NO_CARD_FRAMES = 3;
  const NO_EDGES_HINT_FRAMES = 8;
  function noCardFrame() {
    state.noCardFrames++;
    if (state.noCardFrames >= NO_CARD_FRAMES) { state.lastAutoKey = null; state.rejectedKey = null; }
    if (state.noCardFrames === NO_EDGES_HINT_FRAMES) {
      setStatus('Can’t find the card’s edges: use a plain, contrasting background, or tap the shutter.');
    }
  }

  function keepUnidentified(res, glareHint) {
    const ocr = res.ocr || {};
    const last = state.unidentified[state.unidentified.length - 1];
    // One shaky card held for a few seconds reads the same rubbish frame after frame: keep it once.
    if (!(last && last.text === (ocr.text || ''))) {
      state.unidentified.push({ text: ocr.text || '', info: ocr.info || null, strip: ocr.strip || null, at: Date.now(),
        suggestions: res.suggestions || [] });
    }
    if (state.unidentified.length > 50) state.unidentified.shift();
    setStatus('Could not place “' + (ocr.text || '?') + '”; kept in the unidentified list.' + (glareHint || ''));
    emit('unidentified', { text: ocr.text || '', count: state.unidentified.length });
  }

  async function handleContinuous(res, glareHint) {
    state.noCardFrames = 0; // a frame with a title read had a card in it
    const tier = await tierOf(res);
    if (tier === 'certain') {
      const key = lineKey(res);
      if (key === state.lastAutoKey) { setStatus('Still ' + res.card.name + '. Show the next card.'); return; }
      if (key === state.rejectedKey) { setStatus('Removed ' + res.card.name + '. Move it away to add it again.'); return; }
      state.lastAutoKey = key; state.rejectedKey = null;
      addItem(res, 1);
      state.autoAdded.push({ key, name: res.card.name, quantity: 1, at: Date.now() });
      if (state.autoAdded.length > 50) state.autoAdded.shift();
      const where = res.card.set ? ' (' + res.card.set.toUpperCase() + ' ' + res.card.collector_number + ')' : '';
      setStatus('Added ' + res.card.name + where + '. Next card, or undo.');
      buzz(30);
      emit('auto-added', { name: res.card.name, card: res.card, foil: isFoil(res), total: totalCards() });
      return;
    }
    // An unsure or failed frame leaves the dedupe keys alone: a blurry frame of the card that was
    // just added must not let the next sharp frame add it again.
    if (tier === 'unsure') {
      state.pending = res; showResult(res);
      setStatus('Not sure: check this one. Tap Add, or pick another card.' + (glareHint || ''));
      buzz([20, 40, 20]);
      emit('unsure', { name: res.card.name, status: res.status });
      return;
    }
    keepUnidentified(res, glareHint);
  }

  /* Undo the last automatic add: one copy comes off that line (the line goes when it reaches 0),
     and that card is not added again until it has left the frame or the user taps Add. */
  function undoLastAdd() {
    const rec = state.autoAdded.pop();
    if (!rec) return null;
    const i = state.items.findIndex((it) => lineKey(it) === rec.key);
    if (i >= 0) {
      state.items[i].quantity -= rec.quantity;
      if (state.items[i].quantity <= 0) state.items.splice(i, 1);
      state.dirty = true; saveDraft(); renderBadge();
    }
    if (state.lastAutoKey === rec.key) state.lastAutoKey = null;
    state.rejectedKey = rec.key;
    setStatus('Removed ' + rec.name + '. Move it away to add it again.');
    emit('undo', { name: rec.name, total: totalCards() });
    return rec;
  }

  const FRAME_GAP_MS = 500; // pause between frames so the phone keeps up and the preview stays smooth
  async function continuousLoop(video) {
    while (state.continuous && state.tab === 'camera') {
      if (!state.pending && !state.scanning && state.stream && video.videoWidth) {
        state.scanning = true;
        try { await captureFrame(video); } catch (e) { /* captureFrame reports its own errors */ }
        state.scanning = false;
      }
      await sleep(FRAME_GAP_MS);
    }
    state.continuous = false;
  }
  function startContinuous() {
    const video = root.querySelector('video');
    if (state.continuous || !video) return false;
    state.continuous = true; state.lastAutoKey = null; state.rejectedKey = null; state.noCardFrames = 0; state.lastRead = null;
    emit('continuous', { on: true });
    setStatus('Scanning. Hold each card in the frame until it is added.');
    continuousLoop(video);
    return true;
  }
  function stopContinuous() {
    if (!state.continuous) return;
    state.continuous = false;
    emit('continuous', { on: false });
    setStatus('Stopped. Tap Scan for one card at a time.');
  }

  function showResult(res) {
    const box = $('#result'); if (!box) return;
    box.textContent = '';
    // While a match waits for a decision the preview shrinks (CSS) so the result sits above the controls.
    const panel = box.closest('.cam-panel');
    const clear = () => { box.textContent = ''; if (panel) panel.classList.remove('has-result'); };
    if (panel) panel.classList.add('has-result');
    box.append(resultCard(res, { onAdd: (q) => { addItem(res, q); state.pending = null; clear(); setStatus('Added. Next card.'); },
      onOther: () => { state.pending = null; clear(); showTab('type'); const inp = $('#type-input'); if (inp) { inp.value = (res.ocr && res.ocr.text) || (res.input && res.input.name) || ''; inp.dispatchEvent(new Event('input')); inp.focus(); } } }));
    if (box.scrollIntoView) box.scrollIntoView({ block: 'nearest' });
  }

  function resultCard(res, handlers) {
    const card = res.card;
    const qty = h('input', { type: 'number', min: '1', max: '999', value: String(res.quantity || 1), 'aria-label': 'Quantity' });
    const stepper = stepperFor(qty);
    const meta = h('div', { class: 'meta' });
    if (card) {
      meta.append(h('h3', { text: card.name }),
        h('div', { class: 'muted', text: [card.type_line, card.set_name].filter(Boolean).join(' · ') }),
        printingLine(card, res.foil != null ? res.foil : scanOpts.foil(),
          () => openPicker(res)));
    } else {
      meta.append(h('h3', { text: 'No match' }));
    }
    meta.append(h('div', { class: 'status status-' + res.status, text: statusLabel(res) }));
    // A note the status label does not already carry, such as "unknown set code, matched by name".
    if (res.note && res.status !== 'fuzzy' && res.status !== 'not_found') meta.append(h('div', { class: 'note', text: res.note }));
    if (res.ocr) meta.append(h('div', { class: 'ocr', text: 'read: ' + res.ocr.text + (res.ocr.confidence != null ? ' (' + Math.round(res.ocr.confidence) + '%)' : '') }));
    if (res.art && res.art.note && res.status !== 'fuzzy') meta.append(h('div', { class: 'muted art-note', text: 'Artwork: ' + res.art.note }));
    if (res.suggestions && res.suggestions.length) {
      const chips = h('div', { class: 'chips' });
      res.suggestions.forEach((name) => chips.append(h('button', { class: 'secondary', onclick: () => pickName(name) }, name)));
      meta.append(h('div', { class: 'suggest-label', text: 'Did you mean:' }), chips);
    }
    const actions = h('div', { class: 'actions' });
    if (card) actions.append(stepper, h('button', { class: 'primary', onclick: () => handlers.onAdd(parseInt(qty.value, 10) || 1) }, 'Add'));
    actions.append(h('button', { class: 'secondary', onclick: handlers.onOther }, card ? 'Not this card' : 'Type the name'));
    meta.append(actions);
    const wrap = h('div', { class: 'card result is-' + res.status });
    if (card && card.image_small) wrap.append(h('img', { src: card.image_small, alt: '' }));
    wrap.append(meta);
    return wrap;
  }
  /* "CMR 472" and a foil mark: the exact printing that Add will record. */
  function printingLine(card, foil, onChange) {
    const line = h('div', { class: 'printing' });
    if (card && card.set) line.append(h('span', { class: 'setcode', text: card.set.toUpperCase() + ' ' + (card.collector_number || '') }));
    if (foil) line.append(h('span', { class: 'foil', text: 'Foil' }));
    if (card && onChange) line.append(h('button', { type: 'button', class: 'secondary printings', onclick: onChange }, 'Other printings'));
    return line;
  }

  // ------------------------------------------------------- printing picker
  /* The picker is DOM only. The data and the edits come from the scan hooks on window.__scan (PR D):
     loadPrints(oracleId) lists every printing newest first; choosePrinting(target, print) re-points the
     pending result or a list item; setFoil(target, on) and foilChoices(card) handle the finish. */
  const hooks = () => window.__scan || {};
  const FINISH_LABEL = { nonfoil: 'Non-foil', foil: 'Foil', etched: 'Etched' };
  let pickerKeys = null;   // the open sheet's Escape handler, removed however the sheet closes
  let pickerOpener = null; // the element that opened the sheet, given focus back when it closes
  function closePicker() {
    const el = $('#picker'); if (el) el.remove();
    document.body.classList.remove('picker-open');
    if (pickerKeys) { document.removeEventListener('keydown', pickerKeys); pickerKeys = null; }
    if (pickerOpener && pickerOpener.isConnected) { try { pickerOpener.focus(); } catch (e) { /* ignore */ } }
    pickerOpener = null;
  }
  /* A sheet listing every printing as a tile with its picture, set, number, year and finishes. The current
     printing is marked; tapping a tile records it with the foil switch's setting and closes the sheet.
     target is the pending result (state.pending) or the list item (state.items[i]). */
  async function openPicker(target) {
    closePicker();
    pickerOpener = document.activeElement;
    const card = (target && target.card) || {};
    let foil = !!(target && target.foil);
    const grid = h('div', { class: 'tiles', role: 'listbox', 'aria-label': 'Printings of ' + (card.name || 'this card') });
    const status = h('p', { class: 'muted pick-status', text: 'Loading printings…' });
    const filter = h('input', { type: 'search', id: 'pick-filter', placeholder: 'Filter by set code or name', autocomplete: 'off', 'aria-label': 'Filter printings' });
    const foilSw = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': foil ? 'true' : 'false',
      onclick: () => { foil = !foil; foilSw.setAttribute('aria-checked', foil ? 'true' : 'false'); } },
      h('span', { class: 'knob' }), h('span', { class: 'lbl', text: 'Foil' }));
    const close = h('button', { type: 'button', class: 'secondary icon close', 'aria-label': 'Close', onclick: closePicker }, '✕');
    const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Choose a printing' },
      h('div', { class: 'sheet-head' }, h('div', { class: 'ttl' }, h('h3', { text: 'Choose a printing' }), h('div', { class: 'muted', text: card.name || '' })), close),
      h('div', { class: 'sheet-tools' }, filter, foilSw), status, h('div', { class: 'sheet-body' }, grid));
    const wrap = h('div', { id: 'picker', class: 'picker', onclick: (e) => { if (e.target === wrap) closePicker(); } }, sheet);
    root.append(wrap); document.body.classList.add('picker-open');
    pickerKeys = (e) => { if (e.key === 'Escape') closePicker(); };
    document.addEventListener('keydown', pickerKeys);
    filter.focus();
    let all = [];
    const choose = (p) => {
      const hk = hooks();
      if (!hk.choosePrinting) { status.textContent = 'Could not switch to that printing.'; return; }
      // The finish is settled before the printing is swapped, so choosePrinting merges lines (once) with the
      // finish the user chose: the switch where the printing offers both, otherwise its only finish.
      const can = (hk.foilChoices && hk.foilChoices(p)) || { foil: (p.finishes || []).some((f) => f !== 'nonfoil'), nonfoil: (p.finishes || ['nonfoil']).includes('nonfoil') };
      const was = target.foil;
      target.foil = can.foil && can.nonfoil ? foil : !!can.foil && !can.nonfoil;
      if (!hk.choosePrinting(target, p)) { target.foil = was; status.textContent = 'Could not switch to that printing.'; return; }
      closePicker();
      if (target === state.pending) { showResult(target); setStatus('Printing set to ' + (p.set || '').toUpperCase() + ' ' + (p.collector_number || '') + '.'); }
      else showTab('list');
    };
    const draw = () => {
      grid.textContent = '';
      const q = filter.value.trim().toLowerCase();
      const shown = all.filter((p) => !q || (p.set || '').toLowerCase().includes(q) || (p.set_name || '').toLowerCase().includes(q)
        || String(p.collector_number || '').toLowerCase() === q);
      shown.forEach((p) => {
        const current = p.scryfall_id === card.scryfall_id;
        const year = p.released_at ? String(p.released_at).slice(0, 4) : '';
        // A div, not a button: Chromium does not grow a flex-column button around its picture.
        const tile = h('div', { class: 'tile' + (current ? ' current' : ''), role: 'option', tabindex: '0',
          'aria-selected': current ? 'true' : 'false', onclick: () => choose(p),
          onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(p); } } },
          h('span', { class: 'pic' }, p.image_small ? h('img', { src: p.image_small, alt: '', loading: 'lazy' }) : h('span', { class: 'thumb' }),
            current ? h('span', { class: 'tag now', text: 'Current' }) : null),
          h('span', { class: 'setcode', text: (p.set || '').toUpperCase() + ' ' + (p.collector_number || '') }),
          h('span', { class: 'setname', text: p.set_name || '' }),
          h('span', { class: 'fin' }, year ? h('span', { class: 'year', text: year }) : null,
            ...(p.finishes || []).map((f) => h('span', { class: 'chip ' + f, text: FINISH_LABEL[f] || f }))));
        grid.append(tile);
      });
      status.textContent = all.length ? (shown.length + ' of ' + all.length + ' printings' + (q ? ' match' : '')) : 'No other printings found.';
    };
    filter.addEventListener('input', draw);
    try {
      const hk = hooks();
      if (!hk.loadPrints) throw new Error('printings are not available in this version');
      all = await hk.loadPrints(card.oracle_id); draw();
    } catch (err) { status.textContent = 'Could not load printings: ' + why(err); }
  }
  function statusLabel(res) {
    return { exact: 'Exact match', printing: 'Matched by set and number', fuzzy: 'Closest match' + (res.note ? ': ' + res.note : ''),
      ambiguous: 'Several cards match', not_found: 'Not found' + (res.note ? ': ' + res.note : ''),
      deferred: 'Not looked up yet' + (res.note ? ': ' + res.note : ''), error: 'Error' }[res.status] || res.status;
  }

  /* A number field with a big minus and plus button either side (44px targets). */
  function stepperFor(input, onChange) {
    const clamp = (v) => Math.max(1, Math.min(999, v));
    const set = (v) => { input.value = String(clamp(v)); if (onChange) onChange(clamp(v)); };
    return h('div', { class: 'stepper', role: 'group', 'aria-label': 'Quantity' },
      h('button', { type: 'button', class: 'minus', 'aria-label': 'fewer', onclick: () => set((parseInt(input.value, 10) || 1) - 1) }, '−'),
      input,
      h('button', { type: 'button', class: 'plus', 'aria-label': 'more', onclick: () => set((parseInt(input.value, 10) || 1) + 1) }, '+'));
  }

  async function pickName(name) {
    const out = await api('/scan/api/resolve', 'POST', { cards: [{ name }] });
    const res = out.cards[0];
    if (state.tab === 'camera') { state.pending = res; showResult(res); }
    else showTypeResult(res);
  }

  // ------------------------------------------------------------------- type
  function renderType(panel) {
    const input = h('input', { type: 'search', id: 'type-input', placeholder: 'Card name…', autocomplete: 'off', autocapitalize: 'words' });
    const list = h('ul', { class: 'hidden', role: 'listbox' });
    const suggest = h('div', { class: 'suggest' }, input, list);
    let timer = null, selected = -1, names = [];
    const renderList = () => {
      list.textContent = ''; list.classList.toggle('hidden', names.length === 0);
      names.forEach((n, i) => list.append(h('li', { role: 'option', 'aria-selected': String(i === selected), onmousedown: (e) => { e.preventDefault(); choose(n); } }, n)));
    };
    const choose = (name) => { input.value = name; names = []; renderList(); lookup(name); };
    const lookup = async (name) => {
      if (!name.trim()) return;
      const box = $('#type-result'); box.textContent = 'Looking up…';
      try { const out = await api('/scan/api/resolve', 'POST', { cards: [{ name: name.trim() }] }); showTypeResult(out.cards[0]); }
      catch (err) { box.textContent = 'Lookup failed: ' + why(err); }
    };
    input.addEventListener('input', () => {
      clearTimeout(timer); const q = input.value.trim(); selected = -1;
      if (q.length < 2) { names = []; renderList(); return; }
      timer = setTimeout(async () => {
        try { const out = await api('/scan/api/search?q=' + encodeURIComponent(q)); if (input.value.trim() === q) { names = out.names.slice(0, 8); renderList(); } }
        catch (e) { /* ignore */ }
      }, 180);
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { selected = Math.min(names.length - 1, selected + 1); renderList(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { selected = Math.max(-1, selected - 1); renderList(); e.preventDefault(); }
      else if (e.key === 'Enter') { e.preventDefault(); if (selected >= 0) choose(names[selected]); else { names = []; renderList(); lookup(input.value); } }
      else if (e.key === 'Escape') { names = []; renderList(); }
    });
    input.addEventListener('blur', () => setTimeout(() => { names = []; renderList(); }, 150));
    const ta = h('textarea', { id: 'paste-input', placeholder: 'Or paste a list, one card per line:\n2 Sol Ring (CMR) 472\n1 Aesi, Tyrant of Gyre Strait' });
    const pasteBtn = h('button', { class: 'primary', onclick: async () => {
      const text = ta.value.trim(); if (!text) return;
      const lines = text.split(/\r?\n/).filter((l) => l.trim());
      const box = $('#type-result'); box.textContent = 'Resolving ' + lines.length + ' lines…';
      try {
        const out = await api('/scan/api/resolve', 'POST', { cards: lines });
        let added = 0; const review = [];
        out.cards.forEach((r) => { if (r.card && (r.status === 'exact' || r.status === 'printing')) { addItem(r); added++; } else review.push(r); });
        box.textContent = '';
        const deferred = out.deferred || 0;
        box.append(h('div', { class: 'notice ' + (deferred ? 'warn' : 'ok'), text: 'Added ' + added + ' line' + (added === 1 ? '' : 's') + ' to the list.' + (review.length ? ' ' + review.length + ' need a decision:' : '') + (deferred ? ' ' + out.message : '') }));
        review.forEach((r) => box.append(resultCard(r, { onAdd: (q) => { addItem(r, q); box.textContent = 'Added.'; }, onOther: () => { input.value = (r.input && r.input.name) || ''; input.focus(); } })));
        if (added && !review.length) ta.value = '';
      } catch (err) { box.textContent = 'Resolve failed: ' + why(err); }
    } }, 'Resolve list');
    pasteBtn.classList.add('primary');
    panel.append(h('div', { class: 'card' }, h('label', { for: 'type-input', text: 'Type a card name' }), suggest,
      h('div', { class: 'muted', text: 'Suggestions appear as you type; Enter looks the name up.' })),
      h('div', { id: 'type-result' }),
      h('div', { class: 'card' }, h('label', { for: 'paste-input', text: 'Paste a decklist' }), ta, pasteBtn));
  }
  function showTypeResult(res) {
    const box = $('#type-result'); if (!box) return;
    box.textContent = '';
    box.append(resultCard(res, { onAdd: (q) => { addItem(res, q); box.textContent = ''; box.append(h('div', { class: 'notice ok', text: 'Added ' + q + ' × ' + res.card.name + '.' })); const i = $('#type-input'); if (i) { i.value = ''; i.focus(); } },
      onOther: () => { const i = $('#type-input'); if (i) i.focus(); } }));
  }

  // ------------------------------------------------------------------- list
  function renderList(panel) {
    const name = h('input', { type: 'text', id: 'session-name', placeholder: 'Name this scan (e.g. Trade binder, Aesi upgrades)', value: state.sessionName,
      oninput: (e) => { state.sessionName = e.target.value; state.dirty = true; saveDraft(); } });
    const ul = h('ul', { class: 'items', id: 'items' });
    const summary = h('p', { class: 'muted summary', id: 'summary' });
    const msg = h('div', { id: 'list-msg' });
    const draw = () => {
      ul.textContent = '';
      state.items.forEach((it, idx) => {
        const card = it.card || {};
        const li = h('li', null,
          card.image_small ? h('img', { src: card.image_small, alt: '' }) : h('span', { class: 'thumb' }),
          h('div', { class: 'name' }, h('span', { text: it.name || '?' }),
            h('small', null, h('span', { class: 'status-' + it.status, text: statusLabel(it) }),
              card.set ? h('button', { type: 'button', class: 'setbtn', 'aria-label': 'Change printing of ' + (it.name || 'card') + ', now ' + card.set.toUpperCase() + ' ' + card.collector_number,
                onclick: () => openPicker(it) },
                h('span', { class: 'setcode', text: card.set.toUpperCase() + ' ' + card.collector_number }), ' ▾') : '',
              it.foil ? h('span', { class: 'foil', text: 'Foil' }) : '')),
          h('div', { class: 'stepper', role: 'group', 'aria-label': 'Quantity' },
            h('button', { type: 'button', class: 'minus', 'aria-label': 'fewer', onclick: () => { it.quantity -= 1; if (it.quantity <= 0) state.items.splice(idx, 1); state.dirty = true; saveDraft(); draw(); renderBadge(); } }, '−'),
            h('input', { type: 'number', min: '1', max: '999', value: String(it.quantity), 'aria-label': 'Quantity of ' + (it.name || 'card'),
              onchange: (e) => { const v = parseInt(e.target.value, 10); it.quantity = Math.max(1, Math.min(999, v || 1)); state.dirty = true; saveDraft(); draw(); renderBadge(); } }),
            h('button', { type: 'button', class: 'plus', 'aria-label': 'more', onclick: () => { it.quantity = Math.min(999, it.quantity + 1); state.dirty = true; saveDraft(); draw(); renderBadge(); } }, '+')));
        ul.append(li);
      });
      const unresolved = state.items.filter((it) => !it.card).length;
      summary.textContent = state.items.length ? totalCards() + ' cards, ' + state.items.length + ' distinct' + (unresolved ? ', ' + unresolved + ' unresolved' : '') + (state.sessionId ? ' · saved as ' + state.sessionId + (state.dirty ? ' (unsaved changes)' : '') : ' · not saved yet') : 'Nothing scanned yet.';
    };
    draw();
    const save = h('button', { id: 'save-btn', class: 'primary', onclick: async () => {
      msg.textContent = '';
      if (!state.items.length) { msg.append(h('div', { class: 'notice error', text: 'Scan or type at least one card first.' })); return; }
      try {
        const payload = { name: state.sessionName, items: state.items };
        const out = state.sessionId ? await api('/scan/api/sessions/' + encodeURIComponent(state.sessionId), 'PUT', payload)
          : await api('/scan/api/sessions', 'POST', payload);
        state.sessionId = out.id; state.sessionName = out.name; state.dirty = false; saveDraft(); draw();
        name.value = out.name;
        msg.append(h('div', { class: 'notice ok' }, 'Saved as ', h('strong', { text: out.name }), ' (', h('code', { text: out.id }), '). ',
          'In Claude or ChatGPT, say: “get my scan session ', h('em', { text: out.name }), '” to use these cards.'));
        buzz(30);
      } catch (err) { msg.append(h('div', { class: 'notice error', text: 'Save failed: ' + why(err) })); }
    } }, 'Save scan');
    save.classList.add('primary');
    save.title = 'Keep this list on the gateway; your assistant can pick it up with “get my scan session”.';
    const copy = h('button', { class: 'secondary', onclick: async () => {
      const text = state.items.map((it) => it.quantity + ' ' + (it.name || '?') + (it.card && it.card.set ? ' (' + it.card.set.toUpperCase() + ') ' + it.card.collector_number : '')).join('\n');
      try { await navigator.clipboard.writeText(text); msg.textContent = ''; msg.append(h('div', { class: 'notice ok', text: 'Decklist copied.' })); }
      catch (e) { msg.textContent = ''; msg.append(h('pre', { text: text })); }
    } }, 'Copy decklist');
    const clear = h('button', { class: 'danger', onclick: () => {
      if (!state.items.length && !state.sessionId) return;
      askConfirm(actions, 'Start a new, empty scan? The saved session (if any) stays on the gateway.', () => {
        state.items = []; state.sessionId = null; state.sessionName = ''; state.dirty = false; saveDraft(); name.value = ''; draw(); renderBadge(); msg.textContent = '';
      });
    } }, 'New scan');
    const actions = h('div', { class: 'list-actions' }, save, copy, clear);
    panel.append(h('div', { class: 'card' }, h('label', { for: 'session-name', text: 'Scan name' }), name, summary, ul, actions), msg);
    if (state.items.length) panel.append(whatNext(msg));
  }

  // "What next": the three places scanned cards can go. Each needs the scan saved first (the deck
  // pages read it by id), so an unsaved list is saved on the way.
  async function ensureSaved() {
    if (state.sessionId && !state.dirty) return state.sessionId;
    const payload = { name: state.sessionName, items: state.items };
    const out = state.sessionId ? await api('/scan/api/sessions/' + encodeURIComponent(state.sessionId), 'PUT', payload)
      : await api('/scan/api/sessions', 'POST', payload);
    state.sessionId = out.id; state.sessionName = out.name; state.dirty = false; saveDraft();
    return out.id;
  }
  function whatNext(msg) {
    const resolved = state.items.filter((it) => it.card);
    const note = (kind, text) => { msg.textContent = ''; msg.append(h('div', { class: 'notice ' + kind, text: text })); };
    const toCollection = h('button', { class: 'secondary', onclick: async () => {
      if (!resolved.length) { note('error', 'No card is matched yet; fix the unresolved ones first.'); return; }
      toCollection.disabled = true;
      try {
        note('', 'Saving ' + plural(resolved.length, 'card') + ' to your Archidekt collection… about a second a card.');
        const out = await api('/collection/api/add', 'POST', { items: resolved.map((it) => ({ card: it.card, quantity: it.quantity, foil: isFoil(it) })), source: 'scan', scan_session: state.sessionId || undefined });
        const n = (out.added || []).reduce((a, r) => a + (r.quantity || 0), 0);
        msg.textContent = '';
        msg.append(h('div', { class: 'notice ok' }, 'Saved ' + plural((out.added || []).length, 'card') + ' (' + n + ' copies) to your Archidekt collection. ', h('a', { href: '/collection' }, 'Open the collection'),
          (out.skipped || []).length || resolved.length < state.items.length ? ' Cards Archidekt could not match were left out.' : ''));
        if (state.sessionId) { state.sessionId = null; state.sessionName = ''; state.dirty = false; saveDraft(); renderBadge(); }
        buzz(30);
      } catch (err) { note('error', 'Could not add to the collection: ' + why(err)); }
      toCollection.disabled = false;
    } }, 'Save to collection');
    const deckSel = h('select', { id: 'deck-pick', 'aria-label': 'Deck to add the cards to' }, h('option', { value: '', text: 'Loading your decks…' }));
    const toDeck = h('button', { class: 'secondary', disabled: true, onclick: async () => {
      if (!deckSel.value) { note('error', 'Pick a deck first.'); return; }
      try { const id = await ensureSaved(); location.href = '/decks/' + encodeURIComponent(deckSel.value) + '/edit?scan_session=' + encodeURIComponent(id); }
      catch (err) { note('error', 'Could not save the scan: ' + why(err)); }
    } }, 'Add to deck');
    api('/api/v1/decks').then((out) => {
      deckSel.textContent = '';
      const decks = (out.decks || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
      if (!decks.length) { deckSel.append(h('option', { value: '', text: 'No decks on your Archidekt account yet' })); return; }
      deckSel.append(h('option', { value: '', text: 'Choose a deck…' }));
      decks.forEach((d) => deckSel.append(h('option', { value: String(d.id), text: d.name })));
      toDeck.disabled = false;
    }).catch((err) => { deckSel.textContent = ''; deckSel.append(h('option', { value: '', text: /link/i.test(String(err.message)) ? 'Link Archidekt on the Account page first' : 'Decks unavailable: ' + why(err) })); });
    const newDeck = h('button', { class: 'secondary', onclick: async () => {
      try { const id = await ensureSaved(); location.href = '/decks/new?scan_session=' + encodeURIComponent(id); }
      catch (err) { note('error', 'Could not save the scan: ' + why(err)); }
    } }, 'New deck from these cards');
    return h('section', { class: 'card next', 'aria-label': 'What next' },
      h('h2', { text: 'What next?' }),
      h('div', { class: 'nextrow' }, h('div', { class: 'what' }, h('strong', { text: 'Keep them as owned cards' }), h('small', { class: 'muted', text: 'Adds the matched cards to your Collection on Archidekt; the scan is then done with.' })), toCollection),
      h('div', { class: 'nextrow' }, h('div', { class: 'what' }, h('strong', { text: 'Add them to one of your decks' }), h('small', { class: 'muted', text: 'Opens the deck editor with these cards filled in; you review the change before it is applied.' }), deckSel), toDeck),
      h('div', { class: 'nextrow' }, h('div', { class: 'what' }, h('strong', { text: 'Start a new deck' }), h('small', { class: 'muted', text: 'Opens the new-deck form with this list as the decklist.' })), newDeck));
  }

  // --------------------------------------------------------------- sessions
  async function renderSessions(panel) {
    panel.append(h('p', { class: 'muted', text: 'Loading…' }));
    try {
      const out = await api('/scan/api/sessions');
      panel.textContent = '';
      if (!out.sessions.length) { panel.append(h('div', { class: 'card', text: 'No saved scans yet.' })); return; }
      const ul = h('ul', { class: 'sessions items' });
      out.sessions.forEach((s) => ul.append(h('li', null,
        h('a', { href: '#', onclick: async (e) => { e.preventDefault(); const full = await api('/scan/api/sessions/' + encodeURIComponent(s.id)); state.items = full.items; state.sessionId = full.id; state.sessionName = full.name; state.dirty = false; saveDraft(); renderBadge(); showTab('list'); } },
          h('strong', { text: s.name, title: s.name }), h('small', { style: 'display:block', class: 'muted', text: text.plural(s.card_count, 'card') + ' · ' + s.status + (s.unresolved ? ' · ' + s.unresolved + ' unresolved' : '') + ' · ' + text.when(s.updated_at * 1000) })),
        h('button', { class: 'danger icon', 'aria-label': 'Delete ' + s.name, title: 'Delete', onclick: (e) => {
          askConfirm(e.currentTarget, 'Delete “' + s.name + '” from the gateway?', async () => {
            await api('/scan/api/sessions/' + encodeURIComponent(s.id), 'DELETE', {});
            if (state.sessionId === s.id) { state.sessionId = null; state.dirty = true; saveDraft(); }
            showTab('sessions');
          });
        } }, '✕'))));
      panel.append(h('div', { class: 'card' }, ul));
    } catch (err) { panel.textContent = ''; panel.append(h('div', { class: 'notice error', text: 'Could not load sessions: ' + why(err) })); }
  }

  // ---------------------------------------------------------------- install
  function install() {
    if (state.installPrompt) { state.installPrompt.prompt(); state.installPrompt = null; const b = $('#install-btn'); if (b) b.classList.remove('show'); }
  }
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); state.installPrompt = e; const b = $('#install-btn'); if (b) b.classList.add('show'); });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/scan/sw.js?v=' + encodeURIComponent(cfg.version), { scope: '/scan' }).catch(() => {});
  window.addEventListener('pagehide', stopCamera);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopCamera(); else if (state.tab === 'camera') { const v = root.querySelector('video'); if (v && !state.stream) startCamera(v); } });

  // Test and debugging hooks (read-only view of state plus the pure helpers).
  window.__scan = { state, cleanOcr, preprocess, addItem, scanRegion, showTab, api,
    parseInfoLine, cardRequest, infoRegionOf, setSetLock, getSetLock, setFoilDefault, getFoilDefault,
    flattenCard, flatTitleRegion, glareOf, torchSupported, setTorch, getTorch, cameraRanges, setCamera,
    tierOf, handleContinuous, undoLastAdd, startContinuous, stopContinuous, noCardFrame, nameIsComplete,
    loadPrints, choosePrinting, setFoil, foilChoices, openPicker, closePicker };

  loadDraft();
  render();
})();
