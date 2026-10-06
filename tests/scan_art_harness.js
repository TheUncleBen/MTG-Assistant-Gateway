// Test harness: reads RGBA bytes on stdin, prints the base64 art hash computed by scan-art.js.
// Usage: node tests/scan_art_harness.js <path to scan-art.js> <width> <height>
const path = require('path');
const A = require(path.resolve(process.argv[2]));
const w = +process.argv[3], h = +process.argv[4];
const buf = require('fs').readFileSync(0);
const out = A.hashPixels(new Uint8ClampedArray(buf.buffer, buf.byteOffset, buf.length), w, h);
process.stdout.write(out ? Buffer.from(out).toString('base64') : '');
