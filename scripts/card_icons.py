#!/usr/bin/env python3
"""The weather icons of the Home Assistant card (ADR 0045): the icon set of the Vigie family, in four styles, drawn here and
written into ``custom_components/vigie/frontend/vigie-card.js`` between its two ``icons`` marker lines.

    python scripts/card_icons.py           # rewrites the block of the card
    python scripts/card_icons.py --check   # exit 1 when the block is not what this script draws (a test runs it)

The four styles
---------------
* ``meridien``: the drawings of the Meridien screen (``tools/icones/generer_icones.py`` of its repository), the same discs,
  capsules and polygons on its grid of 128, as SVG. The default of the card.
* ``line``, ``duo``, ``badge``: one geometry on a grid of 24, drawn three ways: a stroke of 1.75; a stroke of 1.6 filled with
  its own colour at 32 %; a solid glyph on a rounded square tinted with the colour of the weather (its box is 32 wide, so the
  glyph is three quarters of the icon).

How an icon is drawn
--------------------
* One to three layers, each with the class of its role: ``vs`` sun, ``vl`` moon, ``vn`` cloud, ``vm`` dark cloud, ``ve``
  water, ``vg`` snow and ice, ``vx`` lightning, ``vb`` mist. The shapes paint in ``currentColor``; the card gives each role its
  colour from the palette of the theme, so no colour is written here.
* A layer behind another (the sun behind a cloud) is cut by a mask: an empty gap around the front layer, as the screen erases
  it. Each run of painted shapes is masked by all the erasing shapes that follow it in the layer. The area of a mask is in
  absolute units (the box, widened by half of it on each side).
* The ids of the masks are ``@0``, ``@1``... The card replaces ``@`` by a prefix of its own for each icon it draws, so two icons
  of a page never share an id.

The 20 icons are the weather icons of the screen, day and night: the names are the ones of the icon set, shared by the screen,
the card and the other apps of the family.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "custom_components" / "vigie" / "frontend" / "vigie-card.js"
BEGIN = "// <icons: drawn by scripts/card_icons.py, edit the script and run it, never this block by hand>"
END = "// </icons>"

NAMES = [
    "soleil", "lune", "eclaircies", "nuit_nuageuse", "couvert", "brouillard", "bruine", "pluie", "pluie_forte",
    "pluie_verglacante", "averses", "averses_nuit", "averses_violentes", "neige", "neige_forte", "grains_de_neige",
    "averses_neige", "averses_neige_nuit", "orage", "orage_grele",
]  # fmt: skip

# ------------------------------------------------------------------------------------------------------------- numbers


def num(v: float, digits: int = 2) -> str:
    """A number as short as SVG takes it: no trailing zero, no leading zero (".5"), never "-0"."""
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        return "0"
    if s.startswith("0."):
        return s[1:]
    if s.startswith("-0."):
        return "-" + s[2:]
    return s


# --------------------------------------------------------------------------------------------------------- elements


def circle(cx, cy, r, d=2):
    return f'<circle cx="{num(cx, d)}" cy="{num(cy, d)}" r="{num(r, d)}"/>'


def path(dd):
    return f'<path d="{dd}"/>'


def segment(x0, y0, x1, y1, d=2):
    if abs(x0 - x1) < 1e-9 and abs(y0 - y1) < 1e-9:
        return path(f"M{num(x0, d)} {num(y0, d)}h0")
    return path(f"M{num(x0, d)} {num(y0, d)}L{num(x1, d)} {num(y1, d)}")


def polygon(points, d=2):
    return '<polygon points="' + " ".join(f"{num(x, d)} {num(y, d)}" for x, y in points) + '"/>'


def rect(x, y, w, h, rx, d=2):
    s = f'<rect x="{num(x, d)}" y="{num(y, d)}" width="{num(w, d)}" height="{num(h, d)}"'
    return s + (f' rx="{num(rx, d)}"/>' if rx > 0 else "/>")


# ------------------------------------------------------------------------------------------------------------ drawing
# An operation: (sign, kind, element, width, fill opacity). Sign "+" paints, "-" erases (a mask). Kind "f" filled, "s" a
# stroke only, "fs" filled and stroked.


def _group(kind, width, opacity, elements, mask_colour=None):
    body = "".join(elements)
    if mask_colour:
        c = mask_colour
        if kind == "f":
            return f'<g fill="{c}">{body}</g>'
        if kind == "s":
            return f'<g fill="none" stroke="{c}" stroke-width="{num(width)}">{body}</g>'
        return f'<g fill="{c}" stroke="{c}" stroke-width="{num(width)}">{body}</g>'
    if kind == "f":
        return body if opacity >= 1 else f'<g fill-opacity="{num(opacity)}">{body}</g>'
    if kind == "s":
        return f'<g fill="none" stroke="currentColor" stroke-width="{num(width)}">{body}</g>'
    op = "" if opacity >= 1 else f' fill-opacity="{num(opacity)}"'
    return f'<g stroke="currentColor" stroke-width="{num(width)}"{op}>{body}</g>'


def _emit(ops, mask_colour=None):
    out, key, run = [], None, []
    for _, kind, element, width, opacity in ops:
        k = (kind, width, opacity)
        if k != key and run:
            out.append(_group(*key, run, mask_colour))
            run = []
        key = k
        run.append(element)
    if run:
        out.append(_group(*key, run, mask_colour))
    return "".join(out)


def draw_icon(layers, id_prefix, box):
    """layers: [(role class, operations)] -> the content of the SVG, masks included."""
    out, k = [], 0
    x, y, w, h = (float(v) for v in box.split())
    area = f'x="{num(x - w / 2)}" y="{num(y - h / 2)}" width="{num(2 * w)}" height="{num(2 * h)}"'
    for role, ops in layers:
        parts, i = [], 0
        while i < len(ops):
            j = i
            while j < len(ops) and ops[j][0] == "+":
                j += 1
            painted = ops[i:j]
            erased = [o for o in ops[j:] if o[0] == "-"]
            if painted:
                if erased:
                    mask = f"{id_prefix}{k}"
                    k += 1
                    out.append(f'<mask id="{mask}" maskUnits="userSpaceOnUse" {area}><rect {area} fill="#fff"/>'
                               f'{_emit(erased, "#000")}</mask>')
                    parts.append(f'<g mask="url(#{mask})">{_emit(painted)}</g>')
                else:
                    parts.append(_emit(painted))
            i = j
            while i < len(ops) and ops[i][0] == "-":
                i += 1
        out.append(f'<g class="{role}">{"".join(parts)}</g>')
    return "".join(out)


# ======================================================================================================================
# meridien: the drawings of the screen, on its grid of 128 (the functions of its generator; drawing in black, which erases
# there, is an erasing operation here).


class Shape:
    def __init__(self):
        self.ops = []

    def _add(self, paint, kind, element, width=0.0, opacity=1.0):
        self.ops.append(("+" if paint else "-", kind, element, width, opacity))

    def disc(self, cx, cy, r, paint=True):
        if r > 0:
            self._add(paint, "f", circle(cx, cy, r, 1))

    def capsule(self, x0, y0, x1, y1, width, paint=True):
        self._add(paint, "s", segment(x0, y0, x1, y1, 1), round(width, 1))

    def round_rect(self, x0, y0, x1, y1, radius, paint=True):
        self._add(paint, "f", rect(x0, y0, x1 - x0, y1 - y0, radius, 1))

    def polygon(self, points, paint=True):
        self._add(paint, "f", polygon(points, 1))

    def round_polygon(self, points, radius, paint=True):
        self._add(paint, "fs", polygon(points, 1), round(2 * radius, 1))

    def star4(self, cx, cy, r, paint=True):
        a = r * 0.22
        self.polygon([(cx, cy - r), (cx + a, cy - a), (cx + r, cy), (cx + a, cy + a), (cx, cy + r), (cx - a, cy + a),
                      (cx - r, cy), (cx - a, cy - a)], paint)


def cloud(t, cx, cy, s=1.0, grow=0.0, paint=True):
    g = grow
    t.round_rect(cx - 46 * s - g, cy + 2 * s - g, cx + 48 * s + g, cy + 28 * s + g, 13 * s + g, paint)
    t.disc(cx - 21 * s, cy + 3 * s, 19 * s + g, paint)
    t.disc(cx + 6 * s, cy - 6 * s, 25 * s + g, paint)
    t.disc(cx + 31 * s, cy + 11 * s, 17 * s + g, paint)


def sun(t, cx, cy, r, r0, r1, width, rays=tuple(range(8))):
    t.disc(cx, cy, r)
    for i in rays:
        a = math.radians(i * 45)
        t.capsule(cx + math.cos(a) * r0, cy + math.sin(a) * r0, cx + math.cos(a) * r1, cy + math.sin(a) * r1, width)


def crescent(t, cx, cy, r, opening=(0.45, -0.38), cut=0.86):
    t.disc(cx, cy, r)
    t.disc(cx + opening[0] * r * 1.35, cy + opening[1] * r * 1.35, r * cut, False)


def flake(t, cx, cy, r, width):
    for i in range(3):
        a = math.radians(i * 60 + 90)
        t.capsule(cx - math.cos(a) * r, cy - math.sin(a) * r, cx + math.cos(a) * r, cy + math.sin(a) * r, width)
    for i in range(6):
        a = math.radians(i * 60 + 90)
        px, py = cx + math.cos(a) * r * 0.62, cy + math.sin(a) * r * 0.62
        for d in (-1, 1):
            b = a + d * math.radians(50)
            t.capsule(px, py, px + math.cos(b) * r * 0.30, py + math.sin(b) * r * 0.30, width * 0.72)


def streak(t, x, y, length=20, slant=0.30, width=7.5):
    t.capsule(x + length * slant / 2, y - length / 2, x - length * slant / 2, y + length / 2, width)


def bolt(t, cx, cy, s=1.0):
    points = [(14, -30), (-14, 6), (-1, 6), (-10, 30), (18, -8), (4, -8)]
    t.round_polygon([(cx + x * s, cy + y * s) for x, y in points], 1.6 * s)


def ops(*draw):
    t = Shape()
    for f in draw:
        f(t)
    return t.ops


# The pieces of the screen's weather icons; "part" is behind a cloud, cut around it.
def m_sun(t):
    sun(t, 64, 64, 24, 36, 53, 8)


def m_sun_part(t):
    sun(t, 46, 45, 19, 29, 41, 6.5, rays=(3, 4, 5, 6, 7))
    cloud(t, 70, 80, 0.98, grow=5, paint=False)


def m_sun_high_part(t):
    sun(t, 47, 34, 17, 25, 36, 6, rays=(3, 4, 5, 6))
    cloud(t, 72, 52, 0.9, grow=5, paint=False)


def m_moon(t):
    crescent(t, 60, 68, 36)
    t.star4(97, 38, 9)
    t.star4(104, 66, 5.5)


def m_moon_part(t):
    crescent(t, 46, 46, 27)
    t.star4(76, 24, 7)
    cloud(t, 70, 80, 0.98, grow=5, paint=False)


def m_moon_high_part(t):
    crescent(t, 46, 34, 23)
    cloud(t, 72, 52, 0.9, grow=5, paint=False)


def m_cloud_low(t):
    cloud(t, 70, 80, 0.98)


def m_cloud_high(t):
    cloud(t, 64, 46, 1.0)


def m_cloud_shower(t):
    cloud(t, 72, 52, 0.9)


def m_cloud_back(t):
    cloud(t, 86, 42, 0.80)
    cloud(t, 56, 70, 1.0, grow=5, paint=False)


def m_cloud_front(t):
    cloud(t, 56, 70, 1.0)


def m_bolt(t):
    bolt(t, 66, 94, 1.0)


def m_drizzle(t):
    for x, y in ((40, 94), (64, 94), (88, 94), (52, 108), (76, 108)):
        t.disc(x, y, 4.2)


def m_rain(t):
    for x, y in ((46, 98), (66, 106), (86, 98)):
        streak(t, x, y, 19)


def m_heavy_rain(t):
    for x, y in ((40, 92), (62, 92), (84, 92), (52, 112), (74, 112), (96, 112)):
        streak(t, x, y, 18, width=8)


def m_shower(t):
    for x, y in ((52, 99), (72, 107)):
        streak(t, x, y, 19)


def m_heavy_shower(t):
    for x, y in ((44, 96), (64, 96), (84, 96), (54, 114), (74, 114)):
        streak(t, x, y, 18, width=8)


def m_freezing_rain(t):
    for x, y in ((46, 98), (82, 98)):
        streak(t, x, y, 19)


def m_freezing_ice(t):
    for x, y in ((64, 94), (56, 112), (74, 112)):
        t.disc(x, y, 5.2)


def m_snow(t):
    for x, y in ((42, 100), (64, 108), (86, 100)):
        flake(t, x, y, 10.5, 4.8)


def m_heavy_snow(t):
    for x, y in ((38, 94), (64, 94), (90, 94), (51, 114), (77, 114)):
        flake(t, x, y, 10.5, 4.8)


def m_grains(t):
    for x, y in ((40, 96), (62, 96), (84, 96), (51, 112), (73, 112)):
        t.disc(x, y, 5.4)


def m_snow_shower(t):
    for x, y in ((52, 100), (76, 108)):
        flake(t, x, y, 10.5, 4.8)


def m_hail(t):
    for x, y in ((36, 100), (47, 117), (94, 100), (83, 117)):
        t.disc(x, y, 5.4)


def m_mist(t):
    for y, x0, x1 in ((86, 26, 96), (100, 40, 108), (114, 28, 84)):
        t.capsule(x0, y, x1, y, 7.5)


MERIDIEN = {
    "soleil": [("vs", ops(m_sun))],
    "lune": [("vl", ops(m_moon))],
    "eclaircies": [("vs", ops(m_sun_part)), ("vn", ops(m_cloud_low))],
    "nuit_nuageuse": [("vl", ops(m_moon_part)), ("vn", ops(m_cloud_low))],
    "couvert": [("vm", ops(m_cloud_back)), ("vn", ops(m_cloud_front))],
    "brouillard": [("vn", ops(m_cloud_high)), ("vb", ops(m_mist))],
    "bruine": [("vn", ops(m_cloud_high)), ("ve", ops(m_drizzle))],
    "pluie": [("vm", ops(m_cloud_high)), ("ve", ops(m_rain))],
    "pluie_forte": [("vm", ops(m_cloud_high)), ("ve", ops(m_heavy_rain))],
    "pluie_verglacante": [("vm", ops(m_cloud_high)), ("ve", ops(m_freezing_rain)), ("vg", ops(m_freezing_ice))],
    "averses": [("vs", ops(m_sun_high_part)), ("vm", ops(m_cloud_shower)), ("ve", ops(m_shower))],
    "averses_nuit": [("vl", ops(m_moon_high_part)), ("vm", ops(m_cloud_shower)), ("ve", ops(m_shower))],
    "averses_violentes": [("vm", ops(m_cloud_high)), ("ve", ops(m_heavy_shower))],
    "neige": [("vn", ops(m_cloud_high)), ("vg", ops(m_snow))],
    "neige_forte": [("vn", ops(m_cloud_high)), ("vg", ops(m_heavy_snow))],
    "grains_de_neige": [("vn", ops(m_cloud_high)), ("vg", ops(m_grains))],
    "averses_neige": [("vs", ops(m_sun_high_part)), ("vn", ops(m_cloud_shower)), ("vg", ops(m_snow_shower))],
    "averses_neige_nuit": [("vl", ops(m_moon_high_part)), ("vn", ops(m_cloud_shower)), ("vg", ops(m_snow_shower))],
    "orage": [("vm", ops(m_cloud_high)), ("vx", ops(m_bolt))],
    "orage_grele": [("vm", ops(m_cloud_high)), ("vx", ops(m_bolt)), ("vg", ops(m_hail))],
}

# ======================================================================================================================
# line, duo, badge: one geometry on the grid of 24 (closed shapes, solid shapes, strokes), drawn three ways.


def intersections(c1, c2):
    (x1, y1, r1), (x2, y2, r2) = c1, c2
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy)
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0))
    xm, ym = x1 + a * dx / d, y1 + a * dy / d
    return (xm + h * dy / d, ym - h * dx / d), (xm - h * dy / d, ym + h * dx / d)


def arc(c, p0, p1, keep=lambda m: True):
    """The arc of circle c from p0 to p1 whose middle passes `keep` (the other way round when it does not)."""
    cx, cy, r = c
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    for sweep in (0, 1):
        span = (a1 - a0) % (2 * math.pi) if sweep else (a0 - a1) % (2 * math.pi)
        mid = a0 + span / 2 if sweep else a0 - span / 2
        m = (cx + r * math.cos(mid), cy + r * math.sin(mid))
        if keep(m):
            return f"A{num(r)} {num(r)} 0 {1 if span > math.pi else 0} {sweep} {num(p1[0])} {num(p1[1])}"
    raise ValueError("no arc")


def cloud24(cx, cy, s=1.0):
    """A cloud of three bumps with a flat bottom, about 18.5 x 11.5 at s = 1, centred on (cx, cy): one path."""
    bumps = [(-5.75, 2.25, 3.5), (0.25, -0.25, 5.5), (5.25, 1.75, 4.0)]
    left, top, right = [(cx + x * s, cy + y * s, r * s) for x, y, r in bumps]
    yb = cy + 5.75 * s
    p_rt = min(intersections(right, top), key=lambda p: p[1])
    p_lt = min(intersections(left, top), key=lambda p: p[1])
    return (f"M{num(left[0])} {num(yb)}H{num(right[0])}" + arc(right, (right[0], yb), p_rt) + arc(top, p_rt, p_lt)
            + arc(left, p_lt, (left[0], yb)) + "Z")


def crescent24(cx, cy, r, opening=(0.45, -0.38), cut=0.86):
    k = (cx + opening[0] * r * 1.35, cy + opening[1] * r * 1.35, r * cut)
    o = (cx, cy, r)
    p1, p2 = intersections(o, k)

    def outside_k(m):
        return math.hypot(m[0] - k[0], m[1] - k[1]) > k[2]

    def inside_o(m):
        return math.hypot(m[0] - cx, m[1] - cy) < r

    return f"M{num(p1[0])} {num(p1[1])}" + arc(o, p1, p2, outside_k) + arc(k, p2, p1, inside_o) + "Z"


def star24(cx, cy, r):
    a = r * 0.24
    return polygon([(cx, cy - r), (cx + a, cy - a), (cx + r, cy), (cx + a, cy + a), (cx, cy + r), (cx - a, cy + a),
                    (cx - r, cy), (cx - a, cy - a)])


def rays24(cx, cy, r0, r1):
    out = []
    for i in range(8):
        a = math.radians(i * 45)
        out.append(segment(cx + math.cos(a) * r0, cy + math.sin(a) * r0, cx + math.cos(a) * r1, cy + math.sin(a) * r1))
    return out


def rain24(points, length=3.4, slant=0.32):
    return [segment(x + length * slant / 2, y - length / 2, x - length * slant / 2, y + length / 2) for x, y in points]


def asterisk(cx, cy, r):
    out = []
    for i in range(3):
        a = math.radians(i * 60 + 90)
        out.append(segment(cx - math.cos(a) * r, cy - math.sin(a) * r, cx + math.cos(a) * r, cy + math.sin(a) * r))
    return out


def layer(role, closed=(), solid=(), strokes=(), cut_by=()):
    """A layer: closed shapes (outlined, filled or solid as the style says), solid shapes (solid in every style), strokes
    (an element, or (element, factor of the width)), and the shapes of the layers in front that cut it."""
    return dict(role=role, closed=list(closed), solid=list(solid),
                strokes=[(s, 1.0) if isinstance(s, str) else s for s in strokes], cut_by=list(cut_by))


CLOUD_HIGH = path(cloud24(12, 9.3, 0.95))
CLOUD_PART = path(cloud24(14.1, 15.6, 0.9))
CLOUD_SHOWER = path(cloud24(14, 11.2, 0.82))
CLOUD_FRONT = path(cloud24(10.5, 13.8, 0.95))
CLOUD_BACK = path(cloud24(15.6, 8.8, 0.72))
BOLT = polygon([(12.5 + x * 0.19, 16.6 + y * 0.19) for x, y in [(14, -30), (-14, 6), (-1, 6), (-10, 30), (18, -8), (4, -8)]])
SNOW_SHOWER = [(s, 0.8) for p in ((11.3, 19.2), (16, 20.8)) for s in asterisk(*p, 1.6)]
SHOWER = rain24([(11.6, 19.3), (15.6, 20.6)])


def sun_behind(cx, cy, r, r0, r1, front):
    return layer("vs", closed=[circle(cx, cy, r)], strokes=rays24(cx, cy, r0, r1), cut_by=[front])


def moon_behind(cx, cy, r, front, star=None):
    return layer("vl", closed=[path(crescent24(cx, cy, r))], solid=[star24(*star)] if star else [], cut_by=[front])


GRID24 = {
    "soleil": [layer("vs", closed=[circle(12, 12, 4.4)], strokes=rays24(12, 12, 7.4, 9.9))],
    "lune": [layer("vl", closed=[path(crescent24(11, 13, 7.6))], solid=[star24(18.6, 6.4, 2.3), star24(20.3, 12.4, 1.4)])],
    "eclaircies": [sun_behind(9.1, 9.6, 3.7, 5.9, 7.9, CLOUD_PART), layer("vn", closed=[CLOUD_PART])],
    "nuit_nuageuse": [moon_behind(9.2, 9.8, 5.6, CLOUD_PART, (16.4, 4.3, 1.8)), layer("vn", closed=[CLOUD_PART])],
    "couvert": [layer("vm", closed=[CLOUD_BACK], cut_by=[CLOUD_FRONT]), layer("vn", closed=[CLOUD_FRONT])],
    "brouillard": [layer("vn", closed=[path(cloud24(12, 8.6, 0.95))]),
                   layer("vb", strokes=[segment(4.6, 17.4, 16.8, 17.4), segment(7.6, 20.8, 19.6, 20.8)])],
    "bruine": [layer("vn", closed=[CLOUD_HIGH]),
               layer("ve", solid=[circle(x, y, 1.0) for x, y in ((7.8, 18.4), (12, 18.4), (16.2, 18.4), (9.9, 21.5),
                                                                 (14.1, 21.5))])],
    "pluie": [layer("vm", closed=[CLOUD_HIGH]), layer("ve", strokes=rain24([(8.4, 19.2), (12.3, 20.6), (16.2, 19.2)]))],
    "pluie_forte": [layer("vm", closed=[CLOUD_HIGH]),
                    layer("ve", strokes=[(s, 1.1) for s in rain24([(6.9, 19.5), (10.6, 20.5), (14.3, 19.5), (18, 20.5)],
                                                                   length=4.4)])],
    "pluie_verglacante": [layer("vm", closed=[CLOUD_HIGH]), layer("ve", strokes=rain24([(8.2, 19.4), (16.4, 19.4)])),
                          layer("vg", solid=[circle(12.3, 18.2, 1.15), circle(10.5, 21.6, 1.15), circle(14.1, 21.6, 1.15)])],
    "averses": [sun_behind(8, 8, 3.2, 5.1, 6.9, CLOUD_SHOWER), layer("vm", closed=[CLOUD_SHOWER]), layer("ve", strokes=SHOWER)],
    "averses_nuit": [moon_behind(8.2, 7.6, 4.7, CLOUD_SHOWER), layer("vm", closed=[CLOUD_SHOWER]), layer("ve", strokes=SHOWER)],
    "averses_violentes": [layer("vm", closed=[CLOUD_HIGH]),
                          layer("ve", strokes=[(s, 1.1) for s in rain24([(7.6, 18.1), (11.6, 18.1), (15.6, 18.1), (9.6, 21.3),
                                                                         (13.6, 21.3)], length=2.4)])],
    "neige": [layer("vn", closed=[CLOUD_HIGH]),
              layer("vg", strokes=[(s, 0.8) for p in ((7.2, 18.9), (12, 21.1), (16.8, 18.9)) for s in asterisk(*p, 1.65)])],
    "neige_forte": [layer("vn", closed=[CLOUD_HIGH]),
                    layer("vg", strokes=[(s, 0.7) for p in ((7.1, 18.1), (12, 18.1), (16.9, 18.1), (9.55, 21.4), (14.45, 21.4))
                                         for s in asterisk(*p, 1.3)])],
    "grains_de_neige": [layer("vn", closed=[CLOUD_HIGH]),
                        layer("vg", solid=[circle(x, y, 1.1) for x, y in ((7.6, 18.4), (12, 18.4), (16.4, 18.4), (9.8, 21.5),
                                                                          (14.2, 21.5))])],
    "averses_neige": [sun_behind(8, 8, 3.2, 5.1, 6.9, CLOUD_SHOWER), layer("vn", closed=[CLOUD_SHOWER]),
                      layer("vg", strokes=SNOW_SHOWER)],
    "averses_neige_nuit": [moon_behind(8.2, 7.6, 4.7, CLOUD_SHOWER), layer("vn", closed=[CLOUD_SHOWER]),
                           layer("vg", strokes=SNOW_SHOWER)],
    "orage": [layer("vm", closed=[CLOUD_HIGH], cut_by=[BOLT]), layer("vx", closed=[BOLT])],
    "orage_grele": [layer("vm", closed=[CLOUD_HIGH], cut_by=[BOLT]), layer("vx", closed=[BOLT]),
                    layer("vg", solid=[circle(6.4, 19, 1.1), circle(7.8, 22, 1.1), circle(17.9, 19.3, 1.1), circle(16.5, 22.2, 1.1)])],
}

# badge: the role whose colour tints the square behind the glyph.
BADGE_ROLE = {
    "soleil": "vs", "lune": "vl", "eclaircies": "vs", "nuit_nuageuse": "vl", "couvert": "vm", "brouillard": "vb", "bruine": "ve",
    "pluie": "ve", "pluie_forte": "ve", "pluie_verglacante": "vg", "averses": "ve", "averses_nuit": "ve", "averses_violentes": "ve",
    "neige": "vg", "neige_forte": "vg", "grains_de_neige": "vg", "averses_neige": "vg", "averses_neige_nuit": "vg", "orage": "vx",
    "orage_grele": "vx",
}


def grid24_set(style):
    """style: "line", "duo" or "badge" -> {name: layers}."""
    out = {}
    for name, layers in GRID24.items():
        drawn = []
        if style == "badge":
            drawn.append((BADGE_ROLE[name], [("+", "f", rect(-4, -4, 32, 32, 9), 0, 0.2)]))
        for c in layers:
            o = []
            if style in ("line", "duo"):
                w = 1.75 if style == "line" else 1.6
                if style == "line":
                    o += [("+", "s", e, w, 1) for e in c["closed"]]
                else:
                    o += [("+", "fs", e, w, 0.32) for e in c["closed"]]
                o += [("+", "f", e, 0, 1) for e in c["solid"]]
                o += [("+", "s", e, round(w * f, 2), 1) for e, f in c["strokes"]]
                o += [("-", "fs", e, w + 2.8, 1) for e in c["cut_by"]]
            else:  # badge: a solid glyph
                w = 2.1
                o += [("+", "fs", e, 1.1, 1) for e in c["closed"]]
                o += [("+", "f", e, 0, 1) for e in c["solid"]]
                o += [("+", "s", e, round(w * f, 2), 1) for e, f in c["strokes"]]
                o += [("-", "fs", e, 1.1 + 2.8, 1) for e in c["cut_by"]]
            drawn.append((c["role"], o))
        out[name] = drawn
    return out


STYLES = {  # name in the card's options -> (box, layers of each icon)
    "meridien": ("0 0 128 128", MERIDIEN),
    "line": ("0 0 24 24", grid24_set("line")),
    "duo": ("0 0 24 24", grid24_set("duo")),
    "badge": ("-4 -4 32 32", grid24_set("badge")),
}

# ======================================================================================================================
# The block of the card


def block() -> str:
    lines = [BEGIN, f"const ICON_STYLES = [{', '.join(repr(s) for s in STYLES)}];", "const ICON_SETS = {"]
    for style, (box, icons) in STYLES.items():
        assert set(icons) == set(NAMES), f"{style} does not draw every icon"
        lines.append(f"  {style}: {{ box: '{box}', icons: {{")
        for name in NAMES:
            body = draw_icon(icons[name], "@", box)
            assert "'" not in body and "\\" not in body, name
            lines.append(f"    {name}: '{body}',")
        lines.append("  } },")
    lines += ["};", END]
    return "\n".join(lines)


def with_block(source: str) -> str:
    head, sep, rest = source.partition(BEGIN)
    if not sep or END not in rest:
        raise SystemExit(f"{CARD.name}: the marker lines of the icons are missing")
    return head + block() + rest.split(END, 1)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the block of the card is not up to date")
    args = parser.parse_args(argv)
    source = CARD.read_text(encoding="utf-8")
    wanted = with_block(source)
    if args.check:
        if wanted != source:
            print(f"{CARD.relative_to(ROOT)}: the icons are not the ones scripts/card_icons.py draws; run it", file=sys.stderr)
            return 1
        return 0
    if wanted != source:
        CARD.write_bytes(wanted.encode("utf-8"))
        print(f"{CARD.relative_to(ROOT)}: icons written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
