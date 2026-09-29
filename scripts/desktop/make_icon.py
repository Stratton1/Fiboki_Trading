#!/usr/bin/env python3
"""Fiboki app icon: the SVG source, generated so the geometry is exact.

Design (matches ``apps/web/app/globals.css``: quiet graphite chrome, one accent):

* a macOS icon tile (1024 canvas, 824 content squircle, Apple's template margins)
  in the workstation's neutral graphite, hue 255, ``--bg-raised`` to ``--bg-sunken``;
* a golden rectangle subdivided into Fibonacci squares (13, 8, 5, 3, 2, 1, 1),
  drawn as hairlines in ``--border-strong`` so the construction is visible up
  close and disappears at 32 px;
* the golden spiral through those squares in the Fiboki brand emerald
  (the V1 identity: ``--color-primary #059669`` to ``--color-primary-light
  #10B981``, lifted to ``#34D399`` at the tip for the glow), round caps, ending
  in a dot: the mark reads as a Fibonacci curve rather than lettering, which
  does not survive 16 px anyway. The workstation UI keeps its blue accent:
  its stylesheet reserves green for P&L-up, and the icon is outside that rule.

Neutrals are the sRGB values of the OKLCH tokens (computed, not eyeballed).
Usage: ``python3 scripts/desktop/make_icon.py`` writes ``fiboki-icon.svg`` next
to this file; ``node scripts/desktop-icon.mjs`` (from ``apps/web``) renders the iconset.
"""
from __future__ import annotations

import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def oklch_to_hex(L: float, C: float, h: float) -> str:
    """OKLCH -> sRGB hex (Björn Ottosson's OKLab matrices), clamped."""
    a = C * math.cos(math.radians(h))
    b = C * math.sin(math.radians(h))
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_**3, m_**3, s_**3
    r = +4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc
    g = -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc
    bb = -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc

    def gam(x: float) -> float:
        x = max(0.0, min(1.0, x))
        return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055

    return "#" + "".join(f"{round(gam(v) * 255):02x}" for v in (r, g, bb))


TOKENS = {
    "bg_raised": oklch_to_hex(0.205, 0.010, 255),
    "bg_canvas": oklch_to_hex(0.145, 0.008, 255),
    "bg_sunken": oklch_to_hex(0.125, 0.008, 255),
    "border": oklch_to_hex(0.320, 0.012, 255),
    "border_strong": oklch_to_hex(0.420, 0.014, 255),
    "fg": oklch_to_hex(0.955, 0.004, 255),
    # Brand emerald (legacy/v1/frontend/src/app/globals.css), not an OKLCH token.
    "brand": "#059669",
    "brand_light": "#10b981",
    "brand_tip": "#34d399",
}

# Fibonacci squares tiling a 13 x 8 golden rectangle: (x, y, side, arc centre,
# arc start, arc end) in rectangle units, y down. Each arc is the quarter
# circle centred on the square's corner opposite the curve.
SQUARES = [
    (0, 0, 8, (8, 8), (0, 8), (8, 0)),
    (8, 0, 5, (8, 5), (8, 0), (13, 5)),
    (10, 5, 3, (10, 5), (13, 5), (10, 8)),
    (8, 6, 2, (10, 6), (10, 8), (8, 6)),
    (8, 5, 1, (9, 6), (8, 6), (9, 5)),
    (9, 5, 1, (9, 6), (9, 5), (10, 6)),
]

CANVAS = 1024
CONTENT = 824  # Apple's template: the tile occupies 824 of 1024
UNIT = 50.0  # px per rectangle unit: 13 units = 650 px wide inside the tile
RECT_W, RECT_H = 13 * UNIT, 8 * UNIT
OX = (CANVAS - RECT_W) / 2
OY = (CANVAS - RECT_H) / 2 + 8  # a touch below centre: optical centre of a wide mark


def px(u: float, axis: str) -> float:
    return (OX if axis == "x" else OY) + u * UNIT


def build_svg() -> str:
    t = TOKENS
    tile = (CANVAS - CONTENT) / 2
    radius = CONTENT * 0.2237  # macOS squircle approximation for a rounded rect
    grid = []
    for x, y, side, *_ in SQUARES:
        grid.append(
            f'<rect x="{px(x, "x"):.1f}" y="{px(y, "y"):.1f}" width="{side * UNIT:.1f}" '
            f'height="{side * UNIT:.1f}"/>'
        )
    d = [f'M {px(SQUARES[0][4][0], "x"):.1f} {px(SQUARES[0][4][1], "y"):.1f}']
    for _x, _y, side, _c, _start, end in SQUARES:
        r = side * UNIT
        d.append(f'A {r:.1f} {r:.1f} 0 0 1 {px(end[0], "x"):.1f} {px(end[1], "y"):.1f}')
    spiral = " ".join(d)
    tip_x, tip_y = px(SQUARES[-1][5][0], "x"), px(SQUARES[-1][5][1], "y")
    stroke = UNIT * 0.66
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}" viewBox="0 0 {CANVAS} {CANVAS}">
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{t['bg_raised']}"/>
      <stop offset="0.55" stop-color="{t['bg_canvas']}"/>
      <stop offset="1" stop-color="{t['bg_sunken']}"/>
    </linearGradient>
    <radialGradient id="sheen" cx="0.5" cy="0" r="0.9">
      <stop offset="0" stop-color="{t['fg']}" stop-opacity="0.07"/>
      <stop offset="0.6" stop-color="{t['fg']}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="curve" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="{t['brand']}"/>
      <stop offset="0.6" stop-color="{t['brand_light']}"/>
      <stop offset="1" stop-color="{t['brand_tip']}"/>
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="14" result="b"/>
      <feColorMatrix in="b" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 0.45 0"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="clip"><rect x="{tile}" y="{tile}" width="{CONTENT}" height="{CONTENT}" rx="{radius:.1f}"/></clipPath>
  </defs>
  <g clip-path="url(#clip)">
    <rect x="{tile}" y="{tile}" width="{CONTENT}" height="{CONTENT}" fill="url(#tile)"/>
    <rect x="{tile}" y="{tile}" width="{CONTENT}" height="{CONTENT}" fill="url(#sheen)"/>
    <g fill="none" stroke="{t['border_strong']}" stroke-width="2.5" stroke-opacity="0.7">
      {''.join(grid)}
    </g>
    <path d="{spiral}" fill="none" stroke="url(#curve)" stroke-width="{stroke:.1f}" stroke-linecap="round" filter="url(#glow)"/>
    <circle cx="{tip_x:.1f}" cy="{tip_y:.1f}" r="{stroke * 0.55:.1f}" fill="{t['fg']}"/>
  </g>
  <rect x="{tile + 1}" y="{tile + 1}" width="{CONTENT - 2}" height="{CONTENT - 2}" rx="{radius - 1:.1f}" fill="none" stroke="{t['border']}" stroke-width="2" stroke-opacity="0.9"/>
</svg>
"""


if __name__ == "__main__":
    out = HERE / "fiboki-icon.svg"
    out.write_text(build_svg())
    print(f"wrote {out}")
    for k, v in TOKENS.items():
        print(f"  {k:14s} {v}")
