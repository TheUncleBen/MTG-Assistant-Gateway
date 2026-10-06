/* Art hash of a flattened card, computed on the phone and sent with the title.
   Spec: algo_version 1 of neotoxicfr/mtg-scanner-art-index (MIT, see docs/THIRD-PARTY-NOTICES.md):
   interior art box y 16%..50%, x 14%..86% of the portrait card; four 256-bit dHash planes
   (grey, B, G, R) of a 17x16 box-averaged thumbnail, bit = pixel[x+1] > pixel[x], row-major;
   128 bytes. The gateway hashes Scryfall's images the same way (scan/art.py) and compares by
   Hamming distance, so the two sides must stay in step: the Python test suite runs this file
   under Node against the Python hash of the same pixels.
   The downsample is written out (not canvas drawImage) so it stays close to Pillow's BOX
   filter: the test measures 14 bits of 1024 apart on the same pixels, against a 40-bit margin.
   Exposed as window.ScanArt = { hashCard, hashPixels, encode }. */
(function (root) {
  'use strict';
  const ART = { top: 0.16, bottom: 0.50, left: 0.14, right: 0.86 };
  const SIDE = 16;

  /* Pillow-style BOX downsample of one plane to (SIDE+1) x SIDE: output cell (x, y) is the
     mean of the input pixels whose centres fall in [x*sx, (x+1)*sx) x [y*sy, (y+1)*sy),
     rounded to 8 bits after each pass like Pillow does. */
  function boxDown(plane, w, h) {
    const ow = SIDE + 1, oh = SIDE;
    const sx = w / ow, sy = h / oh;
    const mid = new Float64Array(ow * h);
    for (let x = 0; x < ow; x++) {
      const j0 = Math.max(0, Math.ceil(x * sx - 0.5)), j1 = Math.min(w, Math.ceil((x + 1) * sx - 0.5));
      const n = Math.max(1, j1 - j0);
      for (let y = 0; y < h; y++) {
        let s = 0;
        for (let j = j0; j < j1; j++) s += plane[y * w + j];
        mid[y * ow + x] = Math.round(s / n);
      }
    }
    const out = new Uint8Array(ow * oh);
    for (let y = 0; y < oh; y++) {
      const i0 = Math.max(0, Math.ceil(y * sy - 0.5)), i1 = Math.min(h, Math.ceil((y + 1) * sy - 0.5));
      const n = Math.max(1, i1 - i0);
      for (let x = 0; x < ow; x++) {
        let s = 0;
        for (let i = i0; i < i1; i++) s += mid[i * ow + x];
        out[y * ow + x] = Math.round(s / n);
      }
    }
    return out;
  }

  function dhashPlane(small, out, offset) {
    let bit = 0;
    for (let y = 0; y < SIDE; y++) {
      for (let x = 0; x < SIDE; x++, bit++) {
        if (small[y * (SIDE + 1) + x + 1] > small[y * (SIDE + 1) + x]) out[offset + (bit >> 3)] |= 0x80 >> (bit & 7);
      }
    }
  }

  /* data: RGBA bytes of a portrait card image (w x h). Returns the 128-byte hash of its art box. */
  function hashPixels(data, w, h) {
    const x0 = Math.max(0, Math.round(w * ART.left)), y0 = Math.max(0, Math.round(h * ART.top));
    const x1 = Math.min(w, Math.round(w * ART.right)), y1 = Math.min(h, Math.round(h * ART.bottom));
    const cw = x1 - x0, ch = y1 - y0;
    if (cw < SIDE + 1 || ch < SIDE) return null;
    const grey = new Uint8Array(cw * ch), r = new Uint8Array(cw * ch), g = new Uint8Array(cw * ch), b = new Uint8Array(cw * ch);
    for (let y = 0; y < ch; y++) {
      for (let x = 0; x < cw; x++) {
        const i = ((y0 + y) * w + (x0 + x)) * 4, o = y * cw + x;
        r[o] = data[i]; g[o] = data[i + 1]; b[o] = data[i + 2];
        grey[o] = (data[i] * 19595 + data[i + 1] * 38470 + data[i + 2] * 7471 + 0x8000) >> 16; // Pillow's L
      }
    }
    const out = new Uint8Array(128);
    dhashPlane(boxDown(grey, cw, ch), out, 0);
    dhashPlane(boxDown(b, cw, ch), out, 32);
    dhashPlane(boxDown(g, cw, ch), out, 64);
    dhashPlane(boxDown(r, cw, ch), out, 96);
    return out;
  }

  function encode(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return (typeof btoa === 'function' ? btoa(s) : Buffer.from(s, 'binary').toString('base64'));
  }

  /* canvas: the flattened card (geometry.js output). Returns base64 of 128 bytes, or null. */
  function hashCard(canvas) {
    try {
      const w = canvas.width, h = canvas.height;
      if (!w || !h) return null;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      const bytes = hashPixels(ctx.getImageData(0, 0, w, h).data, w, h);
      return bytes ? encode(bytes) : null;
    } catch (e) { return null; }
  }

  const api = { hashCard, hashPixels, encode, ART, SIDE };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.ScanArt = api;
})(typeof window !== 'undefined' ? window : null);
