"""PNG charts of a batch for the agent and the designer to look at.

ASCII labels only, so the pictures look the same on every machine (no CJK font needed).
"""
import math

from PIL import Image, ImageDraw, ImageFont

BAND = {'Hard': (255, 236, 214), 'ExHard': (255, 214, 218)}
GEN, REF, MED, GRID, TEXT, FLAG = (214, 40, 57), (138, 148, 163), (31, 42, 55), (232, 236, 241), (90, 100, 115), (245, 158, 11)
EN = {'N': 'N objects', 'K': 'K kinds', 'G': 'G target count', 'P': 'P targets per minute',
      'S': 'S stacked share', 'M': 'M moving (est.)', 'D': 'D density (est.)', 'C': 'C crowd (est.)'}


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _dashed(draw, a, b, fill, width, dash=6, gap=4):
    (x0, y0), (x1, y1) = a, b
    length = math.hypot(x1 - x0, y1 - y0)
    if not length:
        return
    t = 0.0
    while t < length:
        e = min(t + dash, length)
        draw.line([(x0 + (x1 - x0) * t / length, y0 + (y1 - y0) * t / length),
                   (x0 + (x1 - x0) * e / length, y0 + (y1 - y0) * e / length)], fill=fill, width=width)
        t = e + gap


def panel(draw, box, xs, series, bands=None, flags=(), title='', small=False):
    """One line chart in box (x0, y0, x1, y1); y is a 0-100 score; None values break the line."""
    x0, y0, x1, y1 = box
    f = font(12 if small else 14)
    L, R, T, B = 40, 24, 24 if title else 8, 24
    ix0, iy0, ix1, iy1 = x0 + L, y0 + T, x1 - R, y1 - B
    n = len(xs)
    X = lambda i: ix0 + (ix1 - ix0) * (0.5 if n <= 1 else i / (n - 1))
    Y = lambda v: iy1 - (iy1 - iy0) * v / 100
    if title:
        draw.text((ix0, y0 + 4), title, fill=MED, font=font(13 if small else 16))
    step = (ix1 - ix0) / max(1, n - 1)
    for i, d in enumerate(bands or []):
        if d in BAND:
            draw.rectangle([X(i) - step / 2, iy0, X(i) + step / 2, iy1], fill=BAND[d])
    for v in range(0, 101, 20):
        draw.line([(ix0, Y(v)), (ix1, Y(v))], fill=GRID)
        draw.text((ix0 - 6, Y(v)), str(v), fill=TEXT, font=f, anchor='rm')
    every = max(1, math.ceil(n / (12 if small else 25)))
    for i, x in enumerate(xs):
        if i % every == 0 or i == n - 1:
            draw.text((X(i), iy1 + 6), str(x), fill=TEXT, font=f, anchor='mt')
    for i in flags:
        draw.line([(X(i), iy0), (X(i), iy1)], fill=FLAG, width=2)
    for s in series:
        pts = [(X(i), Y(v)) if v is not None else None for i, v in enumerate(s['values'])]
        for a, b in zip(pts, pts[1:]):
            if a and b:
                if s.get('dash'):
                    _dashed(draw, a, b, s['color'], s.get('width', 2))
                else:
                    draw.line([a, b], fill=s['color'], width=s.get('width', 2))
        if s.get('dots'):
            r = 2 if small else 3
            for p in pts:
                if p:
                    draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=s['color'])


def legend(draw, x, y, items):
    f = font(14)
    for label, color in items:
        draw.rectangle([x, y + 3, x + 14, y + 13], fill=color)
        draw.text((x + 20, y), label, fill=MED, font=f)
        x += 28 + draw.textlength(label, font=f)


def curve_png(path, xs, gen, ref, median, bands, flags, title):
    img = Image.new('RGB', (1600, 600), 'white')
    d = ImageDraw.Draw(img)
    d.text((40, 12), title, fill=MED, font=font(18))
    legend(d, 40, 40, [('generated', GEN), ('competitor, same level number', REF), ('rolling median (5)', MED),
                       ('flagged level', FLAG), ('Hard band', BAND['Hard']), ('ExHard band', BAND['ExHard'])])
    series = []
    if any(v is not None for v in ref):
        series.append({'values': ref, 'color': REF, 'width': 2, 'dots': True})
    series += [{'values': gen, 'color': GEN, 'width': 3, 'dots': True},
               {'values': median, 'color': MED, 'width': 2, 'dash': True}]
    panel(d, (0, 64, 1600, 600), xs, series, bands, flags)
    img.save(path)


def pressures_png(path, xs, gen_scores, ref_scores, bands, keys):
    cols, w, h = 2, 800, 260
    rows = math.ceil(len(keys) / cols)
    img = Image.new('RGB', (cols * w, rows * h + 40), 'white')
    d = ImageDraw.Draw(img)
    legend(d, 40, 10, [('generated score', GEN), ('competitor score', REF)])
    for n, k in enumerate(keys):
        r, c = divmod(n, cols)
        series = []
        ref = [s.get(k) for s in ref_scores]
        if any(v is not None for v in ref):
            series.append({'values': ref, 'color': REF, 'width': 2, 'dots': True})
        series.append({'values': [s.get(k) for s in gen_scores], 'color': GEN, 'width': 2, 'dots': True})
        panel(d, (c * w, 40 + r * h, (c + 1) * w, 40 + (r + 1) * h), xs, series, bands, title=EN[k], small=True)
    img.save(path)
