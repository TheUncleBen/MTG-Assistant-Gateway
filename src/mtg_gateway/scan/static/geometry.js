/* Card geometry for the scan page: find the card's four edges, flatten it with a
   perspective transform, and measure glare. Plain JavaScript, no dependencies; the
   heavy loops run on a downscaled grey copy, the warp samples the full frame.

   Exposed as window.ScanGeometry = { flattenCard, glareRatio, homography, warp }.
   Memory: edge finding works on a grey copy at most 480 px wide, and the warp samples only the
   card's bounding box, downscaled to a long side of 2048 px; both buffers are reused between
   frames, so a frame or a large photo is never copied whole.
   flattenCard(source, rect, opts) -> { canvas, corners, score } or null when no
   convincing card outline is found (callers then fall back to the guide box). */
(function () {
  'use strict';

  const CARD_W = 1008, CARD_H = 1408; // 63 x 88 mm at 16 px/mm: enough for the small info line

  const greyCanvas = document.createElement('canvas');
  function grey(source, sw, sh, maxW) {
    const scale = Math.min(1, maxW / sw);
    const w = Math.max(8, Math.round(sw * scale)), h = Math.max(8, Math.round(sh * scale));
    const c = greyCanvas; if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(source, 0, 0, sw, sh, 0, 0, w, h);
    const d = ctx.getImageData(0, 0, w, h).data;
    const g = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) g[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
    // 3x3 box blur to calm JPEG noise and card text before taking gradients.
    const b = new Float32Array(w * h);
    for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
      let s = 0; for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) s += g[(y + dy) * w + x + dx];
      b[y * w + x] = s / 9;
    }
    // The border has no blurred value; copy its neighbour so the picture's edge is not a step.
    for (let x = 0; x < w; x++) { b[x] = b[w + x]; b[(h - 1) * w + x] = b[(h - 2) * w + x]; }
    for (let y = 0; y < h; y++) { b[y * w] = b[y * w + 1]; b[y * w + w - 1] = b[y * w + w - 2]; }
    return { g: b, w, h, scale };
  }

  /* Gradient across the expected edge direction. 'h' edges (top/bottom) use the vertical
     derivative, 'v' edges (left/right) the horizontal one. */
  function deriv(img, x, y, dir) {
    const { g, w, h } = img;
    if (x < 1 || y < 1 || x >= w - 1 || y >= h - 1) return 0;
    return dir === 'h' ? g[(y + 1) * w + x] - g[(y - 1) * w + x] : g[y * w + x + 1] - g[y * w + x - 1];
  }

  /* Fit v = a*u + b to points, dropping outliers twice. Returns null when too few points agree. */
  function fitLine(pts, minKeep) {
    let keep = pts;
    let a = 0, b = 0;
    for (let pass = 0; pass < 3; pass++) {
      const n = keep.length; if (n < 6) return null;
      let su = 0, sv = 0, suu = 0, suv = 0;
      for (const p of keep) { su += p.u; sv += p.v; suu += p.u * p.u; suv += p.u * p.v; }
      const den = n * suu - su * su; if (Math.abs(den) < 1e-9) return null;
      a = (n * suv - su * sv) / den; b = (sv - a * su) / n;
      const res = keep.map((p) => Math.abs(p.v - (a * p.u + b))).sort((x, y) => x - y);
      const mad = res[Math.floor(res.length / 2)] || 0;
      const tol = Math.max(1.5, 2.5 * mad);
      const next = keep.filter((p) => Math.abs(p.v - (a * p.u + b)) <= tol);
      if (next.length === keep.length) break;
      keep = next;
    }
    if (keep.length < minKeep) return null;
    return { a, b, n: keep.length };
  }

  /* One edge: for each column (top/bottom) or row (left/right), walk the band from the outside
     in and take the first brightness step strong enough to be an edge, at its local peak. The
     outermost strong step is the card's edge whatever the colours of card and table, where the
     strongest step would often be the title bar or the frame inside the card. */
  function findEdge(img, side, rect, band, minStep) {
    const pts = [];
    const horizontal = side === 'top' || side === 'bottom';
    const dir = horizontal ? 'h' : 'v';
    const along0 = horizontal ? rect.x + rect.w * 0.12 : rect.y + rect.h * 0.12;
    const along1 = horizontal ? rect.x + rect.w * 0.88 : rect.y + rect.h * 0.88;
    const expect = side === 'top' ? rect.y : side === 'bottom' ? rect.y + rect.h : side === 'left' ? rect.x : rect.x + rect.w;
    const limit = horizontal ? img.h - 2 : img.w - 2;
    const lo = Math.max(1, Math.round(expect - band)), hi = Math.min(limit, Math.round(expect + band));
    const outward = side === 'top' || side === 'left'; // outside is the low end of the band
    const step = Math.max(1, Math.round((along1 - along0) / 64));
    const at = (u, v) => Math.abs(horizontal ? deriv(img, u, v, dir) : deriv(img, v, u, dir));
    for (let u = Math.round(along0); u <= along1; u += step) {
      let found = -1;
      for (let k = 0; lo + k <= hi; k++) {
        const v = outward ? lo + k : hi - k;
        if (at(u, v) < minStep) continue;
        // Over the threshold: follow the step inward to its peak.
        let best = v, bestD = at(u, v);
        for (let j = 1; j <= 3; j++) {
          const w = outward ? v + j : v - j;
          if (w < lo || w > hi) break;
          const d = at(u, w);
          if (d > bestD) { best = w; bestD = d; } else break;
        }
        found = best; break;
      }
      if (found >= 0) pts.push({ u, v: found });
    }
    const total = Math.floor((along1 - along0) / step) + 1;
    const line = fitLine(pts, Math.max(8, Math.ceil(total * 0.45)));
    return line ? { ...line, side, horizontal, coverage: line.n / total } : null;
  }

  // Lines: horizontal ones are y = a*x + b; vertical ones are x = a*y + b.
  function cross(hLine, vLine) {
    // y = a1 x + b1 ; x = a2 y + b2  ->  x = a2 (a1 x + b1) + b2
    const den = 1 - hLine.a * vLine.a;
    if (Math.abs(den) < 1e-9) return null;
    const x = (vLine.a * hLine.b + vLine.b) / den;
    return { x, y: hLine.a * x + hLine.b };
  }

  /* 3x3 homography mapping the unit destination (0..w, 0..h) onto the source quad
     [tl, tr, br, bl], solved from the 8 point correspondences by Gaussian elimination. */
  function homography(quad, w, h) {
    const src = [[0, 0], [w, 0], [w, h], [0, h]];
    const A = [], B = [];
    for (let i = 0; i < 4; i++) {
      const [x, y] = src[i]; const X = quad[i].x, Y = quad[i].y;
      A.push([x, y, 1, 0, 0, 0, -X * x, -X * y]); B.push(X);
      A.push([0, 0, 0, x, y, 1, -Y * x, -Y * y]); B.push(Y);
    }
    const n = 8;
    for (let c = 0; c < n; c++) {
      let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
      [A[c], A[p]] = [A[p], A[c]]; [B[c], B[p]] = [B[p], B[c]];
      if (Math.abs(A[c][c]) < 1e-12) return null;
      for (let r = 0; r < n; r++) {
        if (r === c) continue;
        const f = A[r][c] / A[c][c];
        for (let k = c; k < n; k++) A[r][k] -= f * A[c][k];
        B[r] -= f * B[c];
      }
    }
    const m = B.map((v, i) => v / A[i][i]);
    return [m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], 1];
  }

  // Working buffers reused from frame to frame, so continuous scanning does not churn memory.
  const sampleCanvas = document.createElement('canvas');
  const SAMPLE_MAX = 2048; // long side of the region sampled for the warp; more adds nothing at 16 px/mm

  /* Resample the source through H into a w x h canvas (bilinear). Only the part of the source
     the card occupies (``box``, padded) is drawn into the sample buffer, downscaled when larger
     than SAMPLE_MAX: a 50 MP photo is never copied whole. H maps output pixels to source pixels;
     the sample is offset and scaled from those. */
  function warp(source, sw, sh, H, w, h, box) {
    const bx = Math.max(0, Math.floor(box.x)), by = Math.max(0, Math.floor(box.y));
    const bw = Math.min(sw, Math.ceil(box.x + box.w)) - bx, bh = Math.min(sh, Math.ceil(box.y + box.h)) - by;
    const k = Math.min(1, SAMPLE_MAX / Math.max(bw, bh));
    const cw = Math.max(2, Math.round(bw * k)), ch = Math.max(2, Math.round(bh * k));
    if (sampleCanvas.width !== cw || sampleCanvas.height !== ch) { sampleCanvas.width = cw; sampleCanvas.height = ch; }
    const sctx = sampleCanvas.getContext('2d', { willReadFrequently: true });
    sctx.drawImage(source, bx, by, bw, bh, 0, 0, cw, ch);
    const sd = sctx.getImageData(0, 0, cw, ch).data;
    const out = document.createElement('canvas'); out.width = w; out.height = h;
    const octx = out.getContext('2d');
    const od = octx.createImageData(w, h); const o = od.data;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const d = H[6] * x + H[7] * y + H[8];
        const sx = ((H[0] * x + H[1] * y + H[2]) / d - bx) * k, sy = ((H[3] * x + H[4] * y + H[5]) / d - by) * k;
        const i = (y * w + x) * 4;
        if (sx < 0 || sy < 0 || sx >= cw - 1 || sy >= ch - 1) { o[i] = o[i + 1] = o[i + 2] = 0; o[i + 3] = 255; continue; }
        const x0 = sx | 0, y0 = sy | 0, fx = sx - x0, fy = sy - y0;
        const i00 = (y0 * cw + x0) * 4, i10 = i00 + 4, i01 = i00 + cw * 4, i11 = i01 + 4;
        for (let c = 0; c < 3; c++) {
          o[i + c] = (sd[i00 + c] * (1 - fx) + sd[i10 + c] * fx) * (1 - fy) + (sd[i01 + c] * (1 - fx) + sd[i11 + c] * fx) * fy;
        }
        o[i + 3] = 255;
      }
    }
    octx.putImageData(od, 0, 0);
    return { canvas: out, sampled: { w: cw, h: ch } };
  }

  function sizeOf(source) {
    return { w: source.videoWidth || source.naturalWidth || source.width, h: source.videoHeight || source.naturalHeight || source.height };
  }

  /* rect: where the card is expected in source pixels (the guide box), or null to search the
     whole picture. Returns the flattened card (CARD_W x CARD_H) with its corners in source pixels. */
  function flattenCard(source, rect, opts) {
    opts = opts || {};
    const { w: sw, h: sh } = sizeOf(source);
    if (!sw || !sh) return null;
    const img = grey(source, sw, sh, opts.maxWidth || 480);
    const s = img.scale;
    const r = rect
      ? { x: rect.x * s, y: rect.y * s, w: rect.w * s, h: rect.h * s }
      : { x: img.w * 0.05, y: img.h * 0.05, w: img.w * 0.9, h: img.h * 0.9 };
    const bandFrac = rect ? (opts.band || 0.2) : 0.3;
    const minStep = opts.minStep || 18;
    const top = findEdge(img, 'top', r, r.h * bandFrac, minStep);
    const bottom = findEdge(img, 'bottom', r, r.h * bandFrac, minStep);
    const left = findEdge(img, 'left', r, r.w * bandFrac, minStep);
    const right = findEdge(img, 'right', r, r.w * bandFrac, minStep);
    if (!top || !bottom || !left || !right) return null;
    const tl = cross(top, left), tr = cross(top, right), br = cross(bottom, right), bl = cross(bottom, left);
    if (!tl || !tr || !br || !bl) return null;
    const corners = [tl, tr, br, bl].map((p) => ({ x: p.x / s, y: p.y / s }));
    // Sanity: a convex quad about the shape of a card, not a sliver or the whole frame.
    const wTop = dist(corners[0], corners[1]), wBot = dist(corners[3], corners[2]);
    const hL = dist(corners[0], corners[3]), hR = dist(corners[1], corners[2]);
    const aspect = ((wTop + wBot) / 2) / ((hL + hR) / 2);
    if (!(aspect > 0.55 && aspect < 0.95)) return null;
    if (Math.min(wTop, wBot) / Math.max(wTop, wBot) < 0.7 || Math.min(hL, hR) / Math.max(hL, hR) < 0.7) return null;
    if (!convex(corners)) return null;
    const expectedArea = rect ? rect.w * rect.h : sw * sh * 0.81;
    const area = polyArea(corners);
    if (area < expectedArea * 0.3 || area > expectedArea * 1.6) return null;
    const H = homography(corners, CARD_W, CARD_H);
    if (!H) return null;
    const xs = corners.map((c) => c.x), ys = corners.map((c) => c.y);
    const bx0 = Math.min(...xs), bx1 = Math.max(...xs), by0 = Math.min(...ys), by1 = Math.max(...ys);
    const pad = 0.02 * Math.max(bx1 - bx0, by1 - by0);
    const box = { x: bx0 - pad, y: by0 - pad, w: bx1 - bx0 + 2 * pad, h: by1 - by0 + 2 * pad };
    const warped = warp(source, sw, sh, H, CARD_W, CARD_H, box);
    const score = Math.min(top.coverage, bottom.coverage, left.coverage, right.coverage);
    return { canvas: warped.canvas, corners, score, width: CARD_W, height: CARD_H, sampled: warped.sampled };
  }

  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
  function polyArea(p) {
    let a = 0; for (let i = 0; i < p.length; i++) { const q = p[(i + 1) % p.length]; a += p[i].x * q.y - q.x * p[i].y; }
    return Math.abs(a) / 2;
  }
  function convex(p) {
    let sign = 0;
    for (let i = 0; i < 4; i++) {
      const a = p[i], b = p[(i + 1) % 4], c = p[(i + 2) % 4];
      const z = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
      if (z === 0) continue;
      if (sign === 0) sign = Math.sign(z); else if (Math.sign(z) !== sign) return false;
    }
    return true;
  }

  /* Share of blown-out (near white) pixels in a canvas: glare on a sleeve or a foil. */
  function glareRatio(canvas, threshold) {
    threshold = threshold || 245;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const w = canvas.width, h = canvas.height;
    const d = ctx.getImageData(0, 0, w, h).data;
    let hot = 0; const n = w * h;
    for (let i = 0; i < n; i++) if (d[i * 4] > threshold && d[i * 4 + 1] > threshold && d[i * 4 + 2] > threshold) hot++;
    return hot / n;
  }

  window.ScanGeometry = { flattenCard, glareRatio, homography, warp, CARD_W, CARD_H };
})();
