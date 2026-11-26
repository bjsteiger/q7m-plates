"""The visual style: Cinema covers and blended panel backgrounds.

Every number that shapes the look lives in config/settings.json under
"cover" and "background", so you can tune it without touching this file.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONT_PATH = str(Path(__file__).resolve().parent.parent / "fonts" / "Montserrat.ttf")
DARK = (8, 10, 14)


# ---------- shared helpers ----------

def font(size, weight):
    f = ImageFont.truetype(FONT_PATH, size)
    f.set_variation_by_name(weight)
    return f


def fill(img, w, h, focus_x=0.5, focus_y=0.4):
    """Scale to fill w x h and crop, biased slightly upward so faces survive."""
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x = int((img.width - w) * focus_x)
    y = int((img.height - h) * focus_y)
    return img.crop((x, y, x + w, y + h))


def grade(img, sat=0.85):
    """Light teal and orange grade shared by covers and backgrounds."""
    img = ImageEnhance.Color(img.convert("RGB")).enhance(sat)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * 1.03 + 4)))
    b = b.point(lambda v: min(255, int(v * 0.95 + 10)))
    return Image.merge("RGB", (r, g, b))


def _mask_from_fn(w, h, fn, blur):
    """Build a smooth L mask by sampling fn(x01, y01) -> 0..1 on a small grid."""
    sw, sh = 192, 108
    small = Image.new("L", (sw, sh), 0)
    px = small.load()
    for y in range(sh):
        for x in range(sw):
            px[x, y] = int(255 * max(0.0, min(1.0, fn(x / sw, y / sh))))
    return small.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(blur))


# ---------- cover ----------

def _vignette(w, h, strength, inner):
    def fn(x, y):
        dx, dy = (x - 0.5) * 2, (y - 0.5) * 2
        d = (dx * dx * 0.8 + dy * dy) ** 0.5
        t = min(1, max(0, (d - inner) / (1.25 - inner)))
        return 1 - strength * t
    return _mask_from_fn(w, h, fn, 30)


def _tracked(draw, cx, cy, text, fnt, tracking, fill_rgba, shadow=None):
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    asc, desc = fnt.getmetrics()
    x, y = cx - total / 2, cy - (asc + desc) / 2
    for ch, w in zip(text, widths):
        if shadow:
            draw.text((x, y + 3), ch, font=fnt, fill=shadow)
        draw.text((x, y), ch, font=fnt, fill=fill_rgba)
        x += w + tracking


def _fit(draw, title, max_w, size, tracking_ratio, weight, max_lines=2):
    words = title.upper().split()
    options = [[" ".join(words)]]
    if len(words) > 1:
        best = min(range(1, len(words)),
                   key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
        options.append([" ".join(words[:best]), " ".join(words[best:])])
    if max_lines >= 3 and len(words) > 2:
        splits = [(i, j) for i in range(1, len(words) - 1) for j in range(i + 1, len(words))]
        i, j = min(splits, key=lambda ij: max(len(" ".join(words[:ij[0]])),
                                              len(" ".join(words[ij[0]:ij[1]])),
                                              len(" ".join(words[ij[1]:]))))
        options.append([" ".join(words[:i]), " ".join(words[i:j]), " ".join(words[j:])])
    for s in range(size, 40, -4):
        f = font(s, weight)
        tr = s * tracking_ratio
        for lines in options:
            widest = max(sum(draw.textlength(c, font=f) for c in ln) + tr * (len(ln) - 1) for ln in lines)
            if widest <= max_w:
                return lines, f, tr
    return options[-1], font(44, weight), 44 * tracking_ratio


def cover(still, title, cfg, size=None):
    """Cinema-style tile: one graded still, darker edges, centered title.

    size: (w, h). Wide, poster and square tiles all use the same look; the
    title scales with the short side and wraps to two lines when needed.
    """
    w, h = size or (1280, 720)
    img = grade(fill(still, w, h, focus_x=0.5, focus_y=0.35 if h > w else 0.4))
    dark = Image.new("RGB", (w, h), DARK)
    img = Image.composite(img, dark, _vignette(w, h, 0.8, 0.25))
    band = Image.new("L", (w, h), 0)
    ImageDraw.Draw(band).rectangle([0, h * 0.32, w, h * 0.68], fill=int(255 * cfg["title_band"]))
    img = Image.composite(dark, img, band.filter(ImageFilter.GaussianBlur(70)))

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    base = int(cfg["title_size"] * min(w, h) / 720)
    lines, f, tr = _fit(d, title, w * (0.84 if h >= w else 0.78), base, cfg["tracking"], cfg["weight"],
                        max_lines=3 if h > w else 2)
    a = int(255 * cfg["opacity"])
    lh = f.size * 1.08
    top = h / 2 - lh * len(lines) / 2
    if cfg.get("dash", True):
        d.rectangle([w / 2 - 22, top - 26, w / 2 + 22, top - 22], fill=(255, 255, 255, int(a * 0.8)))
    for i, ln in enumerate(lines):
        _tracked(d, w / 2, top + lh * i + lh / 2, ln, f, tr, (255, 255, 255, a), shadow=(0, 0, 0, 70))
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


# ---------- background ----------

def _left_fade(img, start, end, bottom, max_dark):
    w, h = img.size

    def fn(x, y):
        t = 1 - min(1, max(0, (x - start) / (end - start)))
        t = t * t * (3 - 2 * t)
        by = max(0, (y - bottom) / (1 - bottom))
        return min(max_dark, t * max_dark + by * 0.6)
    return Image.composite(Image.new("RGB", (w, h), DARK), img, _mask_from_fn(w, h, fn, 30))


def _slant_mask(w, h, xl, xr, lean, feather):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon([(xl + lean, 0), (xr + lean, 0), (xr, h), (xl, h)], fill=255)
    return m.filter(ImageFilter.GaussianBlur(feather))


def _blended_panels(images, w, h, n, lean_ratio, feather, max_dark):
    canvas = Image.new("RGB", (w, h), DARK)
    lean = int(h * lean_ratio)
    span = w * 0.78
    pw = span / n
    x0 = w - span
    for i in range(n):
        xl = x0 + i * pw - pw * 0.25
        xr = x0 + (i + 1) * pw + pw * 0.25
        tile = grade(fill(images[i % len(images)], int(xr - xl + lean + 2 * feather), h))
        full = Image.new("RGB", (w, h), DARK)
        full.paste(tile, (int(xl - lean / 2 - feather), 0))
        canvas = Image.composite(full, canvas, _slant_mask(w, h, xl - lean / 2, xr - lean / 2, lean, feather))
    return _left_fade(canvas, 0.2, 0.6, 0.55, max_dark)


def _blended_wall(images, w, h, angle, max_dark, feather=26):
    tw, th = int(w * 0.29), int(w * 0.29 * 9 / 16)
    step_x, step_y = int(tw * 0.86), int(th * 0.84)
    cols, rows = 8, 8
    big = Image.new("RGB", (cols * step_x + tw, rows * step_y + th), DARK)
    edge = Image.new("L", (tw, th), 0)
    ImageDraw.Draw(edge).rectangle([feather, feather, tw - feather, th - feather], fill=255)
    edge = edge.filter(ImageFilter.GaussianBlur(feather * 0.8))
    k = 0
    for r in range(rows):
        shift = step_x // 2 if r % 2 else 0
        for c in range(-1, cols):
            tile = grade(fill(images[k % len(images)], tw, th), sat=0.8)
            k += 5
            big.paste(tile, (c * step_x + shift, r * step_y), edge)
    big = big.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=DARK)
    canvas = Image.new("RGB", (w, h), DARK)
    canvas.paste(big, ((w - big.width) // 2 + int(w * 0.1), (h - big.height) // 2))
    return _left_fade(canvas, 0.15, 0.62, 0.55, max_dark)


def background(images, cfg):
    """Blended slanted panels in front, a faint blended wall behind them.

    images[:panels] feed the panels, the rest feed the wall.
    """
    w, h = cfg["width"], cfg["height"]
    n = cfg["panels"]
    front_imgs = images[:n]
    wall_imgs = images[n:] or images
    back = _blended_wall(wall_imgs, w, h, cfg["wall_angle"], cfg["max_dark"])
    back = ImageEnhance.Brightness(back).enhance(cfg["wall_brightness"])
    front = _blended_panels(front_imgs, w, h, n, cfg["panel_lean"], cfg["blend_softness"], cfg["max_dark"])
    mask = _mask_from_fn(w, h, lambda x, y: (x - 0.42) / 0.2, 40)
    return Image.composite(front, back, mask)
