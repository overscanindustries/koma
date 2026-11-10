"""The Koma mark: コマ (one frame of film) as geometric katakana, and マ alone for icons.

One source for every place the mark is drawn: the app's wordmark lockup, the favicon, the launcher's tray and
window icons, the deferred bundle icons and the README header. Stdlib only. Everything is flat polygons on a
64-unit square (no strokes, no clip paths), so an SVG can be inlined many times on one page and rasterised by
any renderer.

Construction, from Hiragino Sans' skeleton:
  コ  three bars; the vertical carries full weight w, the horizontals 0.88 w so they read the same.
  マ  a bar that turns into a diagonal at about 46 degrees; the diagonal stops two-thirds of the way down. The
      tick (the one amber stroke) starts above-left of the diagonal's end, crosses it, and lands on the baseline,
      cut flat there. The tick is drawn last, over the ink.
The icon cut (マ alone) is heavier and uncompensated, so it holds at 16 px.
"""
from __future__ import annotations

import math

INK = "#e7e5e0"        # --ink, dark theme
ACC = "#e39b3a"        # --acc, dark theme
INK_PAPER = "#161718"  # ink on the light theme
ACC_PAPER = "#c77a1a"  # amber on the light theme
TILE = "#232427"       # icon tile, dark

Poly = list[tuple[float, float]]


def _clip(poly: Poly, inside, cut) -> Poly:
    out: Poly = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        if inside(cur):
            if not inside(prev):
                out.append(cut(prev, cur))
            out.append(cur)
        elif inside(prev):
            out.append(cut(prev, cur))
    return out


def _clip_box(poly: Poly, x1=None, y0=None, y1=None) -> Poly:
    def at_x(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))

    def at_y(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]), y)

    if x1 is not None:
        poly = _clip(poly, lambda p: p[0] <= x1, at_x(x1))
    if y0 is not None:
        poly = _clip(poly, lambda p: p[1] >= y0, at_y(y0))
    if y1 is not None:
        poly = _clip(poly, lambda p: p[1] <= y1, at_y(y1))
    return poly


def _bar(p0, p1, w) -> Poly:
    """A straight stroke of width w from p0 to p1 with butt ends, as a quadrilateral."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = math.hypot(dx, dy)
    nx, ny = -dy / n * w / 2, dx / n * w / 2
    return [(p0[0] + nx, p0[1] + ny), (p1[0] + nx, p1[1] + ny), (p1[0] - nx, p1[1] - ny), (p0[0] - nx, p0[1] - ny)]


def _rect(x, y, w, h) -> Poly:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _ma(w, wh, wd, wt, top, bot, mx0, mx1, dend, t0, t1) -> tuple[list[Poly], Poly]:
    bar = _rect(mx0, top, mx1 - mx0, wh)
    sx, sy = mx1 - wd * 0.25, top + wh / 2
    dx, dy = dend[0] - sx, dend[1] - sy
    n = math.hypot(dx, dy)
    start = (sx - dx / n * w * 1.5, sy - dy / n * w * 1.5)   # run back past the corner; the clip squares it off
    diag = _clip_box(_bar(start, dend, wd), x1=mx1, y0=top)
    tick = _clip_box(_bar(t0, t1, wt), y1=bot)
    return [bar, diag], tick


def koma(w: float = 5.5, compensate: bool = True) -> tuple[list[Poly], Poly]:
    """コマ: (ink polygons, amber tick polygon). w=5.5 is the master; w=6 uncompensated is the 24-32 px cut."""
    hc = 0.88 if compensate else 1.0
    wh, wd, wt = hc * w, (0.95 if compensate else 1.0) * w, 0.92 * w
    top, bot = 20, 44
    kx0, kx1 = 2, 28
    ko = [_rect(kx0, top, kx1 - kx0, wh), _rect(kx1 - w, top, w, bot - top), _rect(kx0, bot - wh, kx1 - kx0, wh)]
    ma, tick = _ma(w, wh, wd, wt, top, bot, 34, 61.5, (47.3, 35.8), (44.2, 31.2), (54.2, 46.6))
    return ko + ma, tick


def ma() -> tuple[list[Poly], Poly]:
    """マ alone, the icon cut: heavy, uncompensated, sized to fill a tile."""
    w = 8
    return _ma(w, w, w, w * 0.95, 14, 52, 10, 54, (37, 38), (29, 30), (45, 58))


def _cw(p: Poly) -> Poly:
    """Same winding for every polygon, so overlaps add under the nonzero rule instead of cancelling."""
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(p, p[1:] + p[:1]))
    return p if area > 0 else p[::-1]


def _d(polys: list[Poly]) -> str:
    polys = [_cw(p) for p in polys]
    return "".join("M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in p) + "Z" for p in polys)


def paths(kind: str = "koma", **kw) -> tuple[str, str]:
    """(ink path data, amber path data) on the 64-unit square."""
    ink, tick = koma(**kw) if kind == "koma" else ma()
    return _d(ink), _d([tick])


def svg(kind: str = "koma", ink: str = INK, acc: str = ACC, tile: str | None = None, size: int | None = None,
        **kw) -> str:
    """A standalone SVG. kind 'koma' (コマ, for lockups) or 'ma' (マ, for icons); tile draws the rounded square."""
    i, a = paths(kind, **kw)
    wh = f' width="{size}" height="{size}"' if size else ""
    bg = f'<rect width="64" height="64" rx="14" fill="{tile}"/>' if tile else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"{wh}>{bg}'
            f'<path fill="{ink}" d="{i}"/><path fill="{acc}" d="{a}"/></svg>')


if __name__ == "__main__":
    import sys
    print(svg(*(sys.argv[1:2] or ["koma"])))
