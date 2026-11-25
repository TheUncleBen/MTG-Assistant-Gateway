"""Checks the browser tests share: a hostile deck name (long, unbroken, right-to-left text and
emoji) and the WCAG contrast of an element's text against what is behind it."""

from __future__ import annotations

# long and unbroken, right-to-left Hebrew and Arabic, emoji (one of them a ZWJ sequence)
HOSTILE = (
    "Superlongunbrokendecknamewithoutanyspaceatall שלום עולם مرحبا بالعالم 🃏🔥🧙‍♂️ "
    "Aesi, Tyrant of Gyre Strait: Lands Matter Big Ramp Value Pile"
)


# WCAG contrast of an element's text against what is behind it: the translucent backgrounds of it
# and its ancestors laid over the first opaque one
CONTRAST = """
(el) => {
  const rgb = (s) => {
    const m = (s.match(/[\\d.]+/g) || []).map(Number);
    return m.length === 3 ? [...m, 1] : m;
  };
  const f = (c) => { c /= 255; return c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4; };
  const lum = ([r, g, b]) => .2126 * f(r) + .7152 * f(g) + .0722 * f(b);
  const layers = [];
  for (let a = el; a; a = a.parentElement) {
    const c = rgb(getComputedStyle(a).backgroundColor);
    if (c.length === 4 && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
  }
  let bg = [255, 255, 255];
  for (const [r, g, b, al] of layers.reverse())
    bg = [r * al + bg[0] * (1 - al), g * al + bg[1] * (1 - al), b * al + bg[2] * (1 - al)];
  const fg = rgb(getComputedStyle(el).color);
  const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (hi + .05) / (lo + .05);
}
"""
