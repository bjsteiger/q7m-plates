"""The visual style: Cinema covers and blended panel backgrounds.

Every number that shapes the look lives in config/settings.json under
"cover" and "background", so you can tune it without touching this file.
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

def _px(im):
    """Pixel values, flat. Uses the newer Pillow call when it exists."""
    f = getattr(im, "get_flattened_data", None)
    return f() if f else im.getdata()


FONT_PATH = str(Path(__file__).resolve().parent.parent / "fonts" / "Montserrat.ttf")
DARK = (8, 10, 14)

# Bump a style's number when its drawing changes, so only cards using it redraw.
# DRAW_VERSION covers shared code (grading, fills, backgrounds) and redraws everything.
DRAW_VERSION = 1
STYLE_VERSIONS = {"cinema": 1, "vibrant": 1, "outline": 1, "brand": 1, "station": 1, "studio": 1,
                  "portrait": 1, "award": 1, "title": 1}


# ---------- shared helpers ----------

FONT_DIR = Path(FONT_PATH).parent


_GF = {}


def hex_rgb(v):
    v = str(v or "#ffffff").lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) if len(v) == 6 else (255, 255, 255)


WGHT = {"Thin": 100, "ExtraLight": 200, "Light": 300, "Regular": 400, "Medium": 500, "SemiBold": 600, "Bold": 700, "ExtraBold": 800, "Black": 900}


def google_font(family, wght=400):
    """Path to a Google Font .ttf at a weight (100-900): fonts/gf/<Family>[-<wght>].ttf if it's in the repo,
    otherwise downloaded once per build (Google Fonts' CSS API hands old browsers .ttf links).
    A weight the font doesn't come in falls back to its regular weight."""
    key = (family, wght)
    if key in _GF:
        return _GF[key]
    local = FONT_DIR / "gf" / (f"{family}.ttf" if wght == 400 else f"{family}-{wght}.ttf")
    if local.exists():
        _GF[key] = str(local)
        return _GF[key]
    import re
    import tempfile
    import urllib.parse
    import requests
    q = urllib.parse.quote_plus(family) + ("" if wght == 400 else f":wght@{wght}")
    try:
        r = requests.get("https://fonts.googleapis.com/css2?family=" + q, headers={"User-Agent": "Mozilla/4.0"}, timeout=30)
        m = re.search(r"url\((https://[^)]+\.ttf)\)", r.text) if r.ok else None
    except requests.RequestException:
        m = None
    if not m:
        if wght != 400:
            _GF[key] = google_font(family, 400)
            return _GF[key]
        raise ValueError(f"Google font not found: {family}")
    path = Path(tempfile.gettempdir()) / f"gf-{re.sub(r'[^A-Za-z0-9]+', '-', family)}-{wght}.ttf"
    path.write_bytes(requests.get(m.group(1), timeout=60).content)
    _GF[key] = str(path)
    return _GF[key]


def font(size, weight, file=None):
    """Montserrat at a named weight, a font file from fonts/, or "gf:Family Name" for a Google Font."""
    if file and file.startswith("gf:"):
        return ImageFont.truetype(google_font(file[3:], WGHT.get(weight, 400)), size)
    if file:
        return ImageFont.truetype(str(FONT_DIR / file), size)
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


def _fit(draw, title, max_w, size, tracking_ratio, weight, max_lines=2, file=None, upper=True):
    words = (title.upper() if upper else title).split()
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
        f = font(s, weight, file)
        tr = s * tracking_ratio
        for lines in options:
            widest = max(sum(draw.textlength(c, font=f) for c in ln) + tr * (len(ln) - 1) for ln in lines)
            if widest <= max_w:
                return lines, f, tr
    return options[-1], font(44, weight, file), 44 * tracking_ratio


def _knockout_white(logo):
    """White logo that keeps its inner details.

    Marks that sit inside a solid plate (the letters in an ABC circle, the letters on TLC's
    squares, the text in a Sky Max box) are much lighter or darker than the plate around them.
    Those are cut out so they still read; everything else turns white. Text next to an icon,
    rather than inside it, stays white because it isn't surrounded by a solid area.
    """
    a = logo.getchannel("A")
    out = Image.new("RGBA", logo.size, (255, 255, 255, 0))
    out.putalpha(a)
    w0, h0 = logo.size
    sc = min(1.0, 240 / max(w0, h0))
    sw, sh = max(1, int(w0 * sc)), max(1, int(h0 * sc))
    small = logo.resize((sw, sh), Image.LANCZOS)
    sa = small.getchannel("A")
    # rainbow-style logos (many strong hues, like a peacock fan) read fine as a plain silhouette
    hsv = small.convert("RGB").convert("HSV")
    hues = [hh * 12 // 256 for (hh, ss, vv), al in zip(_px(hsv), _px(sa)) if al > 128 and ss > 90 and vv > 60]
    if hues:
        counts = [hues.count(i) for i in range(12)]
        strong = [i for i, c in enumerate(counts) if c > 0.08 * len(hues)]
        # a true rainbow runs through green and blue too, not just a purple-to-orange gradient
        if len(strong) >= 5 and any(3 <= i <= 5 for i in strong) and any(6 <= i <= 8 for i in strong):
            return out
    # luminance over black where transparent, so the plate edge doesn't count as a mark
    # transparent areas take the plate's own brightness (the most common opaque value), so the
    # empty space around a small logo doesn't drag the local estimate toward black
    L = small.convert("L")
    hist = [0] * 256
    for v, al in zip(_px(L), _px(sa)):
        if al > 200:
            hist[v] += 1
    plate_lum = max(range(256), key=lambda i: sum(hist[max(0, i - 6):i + 7]))
    lum = Image.composite(L, Image.new("L", (sw, sh), plate_lum), sa)
    k = max(5, min(41, int(min(sw, sh) / 2.2) | 1))
    plate = lum.filter(ImageFilter.MedianFilter(k))
    solid = sa.filter(ImageFilter.MedianFilter(k)).point(lambda v: 255 if v > 230 else 0)
    marks = ImageChops.difference(lum, plate).point(lambda v: 255 if v > 55 else 0)
    marks = ImageChops.multiply(ImageChops.multiply(marks, solid), sa.point(lambda v: 255 if v > 128 else 0))
    n_marks = marks.histogram()[255]
    n_opaque = sa.point(lambda v: 255 if v > 128 else 0).histogram()[255]
    if n_marks < 0.01 * n_opaque or n_marks > 0.6 * n_opaque:
        return out
    # refine at full size so the edges are as crisp as the original artwork
    near = marks.filter(ImageFilter.MaxFilter(5)).resize((w0, h0), Image.NEAREST)
    full = logo.convert("L").point(lambda v: 255 if abs(v - plate_lum) > 55 else 0)
    crisp = ImageChops.multiply(ImageChops.multiply(full, near), a.point(lambda v: 255 if v > 128 else 0))
    # marks become solid near-black inside the white logo (sharp, not see-through)
    out = Image.composite(Image.new("RGBA", logo.size, DARK + (255,)), out, crisp)
    return out


def _prep_logo(logo, mode):
    """Trim empty edges; 'white' turns the logo white but keeps inner details cut out."""
    logo = logo.convert("RGBA")
    alpha = logo.getchannel("A")
    if alpha.getextrema()[0] == 255:      # no transparency: knock out a flat backdrop
        logo = knock_background(logo)
        alpha = logo.getchannel("A")
    bbox = alpha.point(lambda v: 255 if v > 24 else 0).getbbox()   # ignore faint haze around the mark
    if bbox:
        logo = logo.crop(bbox)
    if mode == "white":
        logo = _knockout_white(logo)
    return logo


def knock_background(img, tol=38):
    """Make a flat backdrop colour (sampled from the corners) transparent, with a soft edge."""
    img = img.convert("RGBA")
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    bg = tuple(sorted(c[i] for c in corners)[1] for i in range(3))
    r, g, b = rgb.split()
    d = ImageChops.add(ImageChops.add(r.point(lambda v: abs(v - bg[0])), g.point(lambda v: abs(v - bg[1]))),
                       b.point(lambda v: abs(v - bg[2])))
    a = d.point(lambda v: 0 if v < tol else (255 if v > tol * 2 else int((v - tol) * 255 / tol)))
    img.putalpha(ImageChops.multiply(a, img.getchannel("A")))
    return img


def split_blocks(img, gap=0.03):
    """Split an image holding several logos side by side (or stacked) on transparency.
    Returns the blocks left to right (or top to bottom)."""
    a = img.getchannel("A")
    w, h = img.size
    def runs(sums, length):
        out, start, empty = [], None, 0
        for i, v in enumerate(sums):
            if v:
                if start is None:
                    start = i
                empty = 0
            elif start is not None:
                empty += 1
                if empty > length * gap:
                    out.append((start, i - empty + 1))
                    start, empty = None, 0
        if start is not None:
            out.append((start, len(sums)))
        return out
    cols = [1 if a.crop((x, 0, x + 1, h)).getbbox() else 0 for x in range(w)]
    rc = runs(cols, w)
    if len(rc) > 1:
        return [img.crop((x0, 0, x1, h)) for x0, x1 in rc]
    rows = [1 if a.crop((0, y, w, y + 1)).getbbox() else 0 for y in range(h)]
    rr = runs(rows, h)
    if len(rr) > 1:
        return [img.crop((0, y0, w, y1)) for y0, y1 in rr]
    return [img]


def corner_color(img):
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3))]
    return "#%02x%02x%02x" % tuple(sorted(c[i] for c in corners)[1] for i in range(3))


def _vgradient(logo, top=1.0, bottom=0.78):
    """Subtle top-to-bottom shading on a logo (white at top, soft grey at the bottom)."""
    w, h = logo.size
    grad = Image.linear_gradient("L").resize((w, h)).point(lambda v: int(255 * (top + (bottom - top) * v / 255)))
    r, g, b, a = logo.split()
    r, g, b = (ImageChops.multiply(ch, grad) for ch in (r, g, b))
    return Image.merge("RGBA", (r, g, b, a))


def _bright(img, cfg, default=1.0):
    """Backdrop brightness ("backdrop"), skipped when it's the look's default."""
    b = cfg.get("backdrop", default)
    return img if b == default else ImageEnhance.Brightness(img).enhance(b / default if default else b)


def _fade(img, cfg, strength, start, power=1.4, color=(0, 0, 0)):
    """Darkening gradient across the tile. Settings: fade (strength), fade_start (0..1, where it begins),
    fade_dir (bottom, top, left, right, both, none) and fade_color (hex)."""
    w, h = img.size
    k = cfg.get("fade", strength)
    d = cfg.get("fade_dir", "bottom")
    if not k or d == "none":
        return img
    st = cfg.get("fade_start", start)
    col = hex_rgb(cfg["fade_color"]) if cfg.get("fade_color") else color
    def ramp(t):
        return (max(0, (t - st) / max(0.01, 1 - st)) ** power) * k
    fn = {"bottom": lambda x, y: ramp(y), "top": lambda x, y: ramp(1 - y),
          "left": lambda x, y: ramp(1 - x), "right": lambda x, y: ramp(x),
          "both": lambda x, y: max(ramp(y), ramp(1 - y))}.get(d, lambda x, y: ramp(y))
    return Image.composite(Image.new("RGB", (w, h), col), img, _mask_from_fn(w, h, fn, 10))


def station_cover(still, logo, size, scale=1.0, color=False, cfg=None):
    """Network tile: a dim, blurred still behind a soft glow, white logo with a slight gradient.
    color=True keeps the logo in its own colours (for logos listed in logo_color)."""
    cfg = cfg or {}
    w, h = size
    img = fill(still, w, h).filter(ImageFilter.GaussianBlur(max(1, int(max(6, w // 110) * cfg.get("blur", 1.0)))))
    img = ImageEnhance.Color(img).enhance(0.8)
    img = ImageEnhance.Brightness(img).enhance(cfg.get("backdrop", 0.24))
    dark = Image.new("RGB", (w, h), (6, 7, 10))
    img = Image.composite(img, dark, _vignette(w, h, cfg.get("edges", 0.95), 0.15))
    gl = cfg.get("glow", 0.22)
    glow = _mask_from_fn(w, h, lambda x, y: gl * max(0, 1 - ((x - 0.5) * 2.2) ** 2 - ((y - 0.5) * 2.4 * (h / w if h > w else 1)) ** 2), 60)
    img = Image.composite(Image.new("RGB", (w, h), hex_rgb(cfg.get("glow_color", "#788caa"))), img, glow)
    img = _fade(img, cfg, 0, 0.45)

    lg = _vgradient(_prep_logo(logo, None) if color else _prep_logo(logo, "white"), cfg.get("gradient_top", 1.0), cfg.get("gradient", 0.78))   # shading applies to color logos too
    box_w = min(w * 0.8, w * (0.6 if h >= w else 0.42) * scale)
    box_h = min(h * 0.5, h * (0.26 if h > w else 0.32) * scale)
    k = min(box_w / lg.width, box_h / lg.height)
    lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), Image.LANCZOS)
    shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    sa = cfg.get("shadow", 0.5)
    shadow.putalpha(lg.getchannel("A").point(lambda v: int(v * sa)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(cfg.get("shadow_blur", 8)))
    out = img.convert("RGBA")
    x, y = (w - lg.width) // 2, int(h * cfg.get("logo_y", 0.5) - lg.height / 2)
    out.alpha_composite(shadow, (x, y + 4))
    out.alpha_composite(lg, (x, y))
    return out.convert("RGB")


def cover(still, title, cfg, size=None, logo=None, logo_scale=1.0):
    """Cinema-style tile: one graded still, darker edges, centered title.

    size: (w, h). Wide, poster and square tiles all use the same look; the
    title scales with the short side and wraps to two lines when needed.
    """
    w, h = size or (1280, 720)
    ty = cfg.get("title_y", 0.5)
    img = grade(fill(still, w, h, focus_x=0.5, focus_y=0.35 if h > w else 0.4), cfg.get("saturation", 0.85))
    img = _bright(img, cfg)
    dark = Image.new("RGB", (w, h), DARK)
    img = Image.composite(img, dark, _vignette(w, h, cfg.get("vignette", 0.8), 0.25))
    img = _fade(img, cfg, 0, 0.45)
    band = Image.new("L", (w, h), 0)
    ImageDraw.Draw(band).rectangle([0, h * (ty - 0.18), w, h * (ty + 0.18)], fill=int(255 * cfg["title_band"]))
    img = Image.composite(dark, img, band.filter(ImageFilter.GaussianBlur(70)))

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if logo is not None:
        lg = _prep_logo(logo, cfg.get("logo_mode", "white"))
        box_w = min(w * 0.86, w * (0.62 if h >= w else 0.46) * logo_scale)
        box_h = min(h * 0.6, h * (0.24 if h > w else 0.30) * logo_scale)
        s_ = min(box_w / lg.width, box_h / lg.height)
        lg = lg.resize((max(1, int(lg.width * s_)), max(1, int(lg.height * s_))), Image.LANCZOS)
        a = cfg["opacity"]
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * a)))
        shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
        lsa = cfg.get("logo_shadow", 0.45)
        shadow.putalpha(lg.getchannel("A").point(lambda v: int(v * lsa)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(cfg.get("logo_shadow_blur", 6)))
        x, y = (w - lg.width) // 2, (h - lg.height) // 2 if "logo_y" not in cfg else int(h * cfg["logo_y"] - lg.height / 2)
        layer.alpha_composite(shadow, (x, y + 3))
        layer.alpha_composite(lg, (x, y))
        out = img.convert("RGBA")
        out.alpha_composite(layer)
        return out.convert("RGB")
    d = ImageDraw.Draw(layer)
    base = int(cfg["title_size"] * min(w, h) / 720)
    base = int(base * cfg.get("font_scale", 1.0))
    lines, f, tr = _fit(d, title, w * (0.84 if h >= w else 0.78), base, cfg["tracking"], cfg["weight"],
                        file=cfg.get("font_file"), upper=cfg.get("upper", True),
                        max_lines=cfg["lines"] if isinstance(cfg.get("lines"), int) else (3 if h > w else 2))
    a = int(255 * cfg["opacity"])
    col = hex_rgb(cfg.get("text_color", "#ffffff"))
    lh = f.size * 1.08
    top = h * ty - lh * len(lines) / 2
    sa, sb = int(round(255 * cfg.get("shadow", 70 / 255))), cfg.get("shadow_blur", 0)
    if cfg.get("dash", True):
        dy = top - 26
        if cfg.get("font_file"):     # other fonts sit differently in their line: put the dash just above the letters themselves
            gt = d.textbbox((w / 2, top + lh / 2), lines[0], font=f, anchor="mm")[1]
            dy = min(dy, gt - 18)
        d.rectangle([w / 2 - 22, dy, w / 2 + 22, dy + 4], fill=col + (int(a * 0.8),))
    if sb:      # soft shadow on its own layer
        sl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for i, ln in enumerate(lines):
            _tracked(sd, w / 2, top + lh * i + lh / 2 + 3, ln, f, tr, (0, 0, 0, sa))
        layer = Image.alpha_composite(sl.filter(ImageFilter.GaussianBlur(sb * min(w, h) / 720)), layer)
        d = ImageDraw.Draw(layer)
    for i, ln in enumerate(lines):
        _tracked(d, w / 2, top + lh * i + lh / 2, ln, f, tr, col + (a,), shadow=None if sb else (0, 0, 0, sa))
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


def brand_cover(logo, bg_hex, size, scale=1.0, cfg=None):
    """App-tile style: brand-colour field, full-colour logo, soft dark gradient rising from the bottom."""
    cfg = cfg or {}
    w, h = size
    bg = tuple(int(bg_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    img = Image.new("RGB", (w, h), bg)
    # gentle lift in the middle so the field isn't flat
    gl = cfg.get("glow", 0.10)
    glow = _mask_from_fn(w, h, lambda x, y: gl * max(0, 1 - (((x - 0.5) * 1.6) ** 2 + ((y - 0.5) * 2) ** 2)), 40)
    img = Image.composite(Image.new("RGB", (w, h), tuple(min(255, c + 40) for c in bg)), img, glow)
    # dark gradient coming up from the bottom
    if cfg.get("edges"):
        img = Image.composite(img, Image.new("RGB", (w, h), (6, 7, 10)), _vignette(w, h, cfg["edges"], 0.15))
    img = _fade(img, cfg, 0.6, 0.45)

    lg = _prep_logo(logo, "color")
    col, _ = _dominant(lg)
    if sum((col[i] - bg[i]) ** 2 for i in range(3)) ** 0.5 < 110:   # logo would vanish into the field
        lg = _prep_logo(logo, "white")
    box_w = min(w * 0.8, w * (0.58 if h >= w else 0.42) * scale)
    box_h = min(h * 0.5, h * (0.22 if h > w else 0.28) * scale)
    k = min(box_w / lg.width, box_h / lg.height)
    lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), Image.LANCZOS)
    if cfg.get("gradient_top", 1.0) != 1.0 or cfg.get("gradient", 1.0) != 1.0:
        lg = _vgradient(lg, cfg.get("gradient_top", 1.0), cfg.get("gradient", 1.0))
    out = img.convert("RGBA")
    pos = ((w - lg.width) // 2, int(h * cfg.get("logo_y", 0.5) - lg.height / 2))
    if cfg.get("shadow", 0):
        sh = Image.new("RGBA", lg.size, (0, 0, 0, 0))
        sa = cfg["shadow"]
        sh.putalpha(lg.getchannel("A").point(lambda v: int(v * sa)))
        out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(cfg.get("shadow_blur", 8))), (pos[0], pos[1] + 4))
    out.alpha_composite(lg, pos)
    return out.convert("RGB")


def _dominant(logo):
    """Average colour of the logo's visible pixels, and how bright it is (0..255)."""
    rgba = logo.convert("RGBA").resize((64, 64))
    px = [p for p in _px(rgba) if p[3] > 128]
    if not px:
        return (40, 40, 48), 128
    r, g, b = (sum(p[i] for p in px) / len(px) for i in range(3))
    return (int(r), int(g), int(b)), 0.299 * r + 0.587 * g + 0.114 * b


def studio_cover(still, logo, size, scale=1.0, cfg=None):
    """Studio tile: the logo large and in its own colours over a deep field tinted from the logo,
    with the film still faintly underneath, like a studio's opening card."""
    cfg = cfg or {}
    w, h = size
    col, _ = _dominant(_prep_logo(logo, "color"))
    lg = _vgradient(_prep_logo(logo, "color") if cfg.get("logo_color") else _prep_logo(logo, "white"), cfg.get("gradient_top", 1.0), cfg.get("gradient", 0.82))   # studio logos in white unless kept in color
    if cfg.get("tint"):               # the studio's own color, picked per studio
        col = hex_rgb(cfg["tint"])
    elif max(col) - min(col) < 25:    # grey or black logo with no color picked: a neutral field
        col = hex_rgb(cfg.get("neutral", "#3c465a"))
    deep = tuple(max(6, int(c * 0.28)) for c in col)
    mid = tuple(max(10, int(c * 0.55)) for c in col)
    base = Image.new("RGB", (w, h), deep)
    gl = cfg.get("glow", 0.75)
    glow = _mask_from_fn(w, h, lambda x, y: max(0, 1 - ((x - 0.5) * 1.5) ** 2 - ((y - 0.5) * 1.8) ** 2) * gl, 50)
    base = Image.composite(Image.new("RGB", (w, h), hex_rgb(cfg["glow_color"]) if cfg.get("glow_color") else mid), base, glow)
    under = ImageEnhance.Brightness(ImageOps.grayscale(fill(still, w, h)).convert("RGB")).enhance(0.5)
    if cfg.get("blur"):
        under = under.filter(ImageFilter.GaussianBlur(max(1, int(max(6, w // 110) * cfg["blur"]))))
    base = Image.blend(base, ImageChops.multiply(base, under.point(lambda v: min(255, v + 120))), cfg.get("still", 0.6))
    if cfg.get("edges"):
        base = Image.composite(base, Image.new("RGB", (w, h), (6, 7, 10)), _vignette(w, h, cfg["edges"], 0.15))
    base = _fade(base, cfg, 0.55, 0.55, 1.5)
    box_w = min(w * 0.88, w * (0.78 if h >= w else 0.62) * scale)
    box_h = min(h * 0.7, h * (0.42 if h > w else 0.56) * scale)
    k = min(box_w / lg.width, box_h / lg.height)
    lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), Image.LANCZOS)
    shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    sa = cfg.get("shadow", 0.55)
    shadow.putalpha(lg.getchannel("A").point(lambda v: int(v * sa)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(cfg.get("shadow_blur", 10)))
    out = base.convert("RGBA")
    x, y = (w - lg.width) // 2, int(h * cfg.get("logo_y", 0.5) - lg.height / 2)
    out.alpha_composite(shadow, (x, y + 6))
    out.alpha_composite(lg, (x, y))
    return out.convert("RGB")


def person_cover(headshot, name, role, size, cfg):
    """Portrait tile for a person: moody headshot, dark vignette, name anchored bottom left."""
    w, h = size
    z = cfg.get("photo_zoom", 1.0)
    fx, fy = cfg.get("photo_x", 0.5), cfg.get("photo_y", 0.18)
    if ("photo_x" in cfg or "photo_y" in cfg) and z < 1.25:
        z = 1.25            # a photo that exactly fills the card has no room to move, so moving it zooms in a little
    if z == 1.0:
        img = fill(headshot, w, h, focus_x=fx, focus_y=fy)
    else:                                  # zoom in, then crop around the chosen spot
        big = fill(headshot, int(w * z), int(h * z), focus_x=fx, focus_y=fy)
        x, y = int((big.width - w) * fx), int((big.height - h) * fy)
        img = big.crop((x, y, x + w, y + h))
    img = ImageEnhance.Color(img).enhance(cfg.get("saturation", 0.75))
    img = ImageEnhance.Contrast(img).enhance(1.1)
    img = _bright(img, cfg)
    dark = Image.new("RGB", (w, h), (6, 7, 10))
    img = Image.composite(img, dark, _vignette(w, h, cfg.get("vignette", 0.85), 0.3))
    img = Image.composite(dark, img, _mask_from_fn(w, h, lambda x, y: (max(0, (y - 0.5) / 0.5) ** 1.3) * cfg.get("shade", 0.92), 12))
    img = _fade(img, cfg, 0, 0.45)
    ntr, nfile, ncol = cfg.get("name_tracking", 0.12), cfg.get("font_file"), hex_rgb(cfg.get("text_color", "#ffffff"))

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * cfg["opacity"])
    words = name.upper().split()
    lines = [" ".join(words[:1]), " ".join(words[1:])] if len(words) > 1 else [name.upper()]
    size_ = int(min(w, h) * cfg.get("name_size", 0.085))
    tr = size_ * ntr
    while size_ > 18:
        f = font(size_, cfg.get("weight", "Bold"), nfile)
        widest = max(sum(d.textlength(c, font=f) for c in ln) + tr * (len(ln) - 1) for ln in lines)
        if widest <= w * 0.84:
            break
        size_ -= 2
        tr = size_ * ntr
    f = font(size_, cfg.get("weight", "Bold"), nfile)
    small = font(max(12, int(size_ * cfg.get("role_size", 0.32))), cfg.get("role_weight", "SemiBold"), cfg.get("role_font"))
    x0 = int(w * cfg.get("name_x", 0.08))
    align = cfg.get("name_align", "left")
    lh = size_ * 1.02
    bottom = h - int(h * cfg.get("name_bottom", 0.06)) - (small.size + 10 if role else 0)
    y = bottom - lh * len(lines)

    def start(text, fnt, gap):            # left edge of a line for the chosen alignment
        if align == "left":
            return x0
        tw = sum(d.textlength(c, font=fnt) for c in text) + gap * (len(text) - 1)
        return (w - tw) / 2 if align == "center" else w - x0 - tw
    for ln in lines:
        cx = start(ln, f, tr)
        for ch in ln:
            d.text((cx, y + 2), ch, font=f, fill=(0, 0, 0, int(round(255 * cfg.get("shadow", 90 / 255)))))
            d.text((cx, y), ch, font=f, fill=ncol + (a,))
            cx += d.textlength(ch, font=f) + tr
        y += lh
    if role and cfg.get("show_role", True):
        cx = start(role.upper(), small, small.size * cfg.get("role_tracking", 0.35))
        for ch in role.upper():
            d.text((cx, y + 6), ch, font=small, fill=hex_rgb(cfg.get("role_color", "#ffffff")) + (int(a * cfg.get("role_opacity", 0.55)),))
            cx += d.textlength(ch, font=small) + small.size * cfg.get("role_tracking", 0.35)
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


def plain_field(size):
    """Dark textured field used when a tile has no usable imagery."""
    w, h = size
    img = Image.new("RGB", (w, h), (12, 13, 17))
    glow = _mask_from_fn(w, h, lambda x, y: 0.35 * max(0, 1 - ((x - 0.5) * 1.4) ** 2 - ((y - 0.5) * 1.8) ** 2), 50)
    img = Image.composite(Image.new("RGB", (w, h), (34, 37, 46)), img, glow)
    noise = Image.effect_noise((w, h), 18).convert("RGB")
    return Image.blend(img, noise, 0.04)


def title_only_cover(title, cfg, size):
    """Same title treatment as the Cinema cover, on a plain dark field, no film image."""
    return cover(plain_field(size), title, {**cfg, "title_band": 0.0}, size)


def outline_year_cover(still, text, size, cfg):
    """Decades tile: the year huge, in outline, so big it runs off the edges but still reads.
    A faint filled copy sits behind for depth, like it's coming off the screen."""
    w, h = size
    img = grade(fill(still, w, h), cfg.get("saturation", 0.85))
    img = ImageEnhance.Brightness(img).enhance(cfg.get("backdrop", 0.55))
    dark = Image.new("RGB", (w, h), DARK)
    img = Image.composite(img, dark, _vignette(w, h, cfg.get("vignette", 0.85), 0.2))
    img = _fade(img, cfg, 0, 0.45)
    ow = cfg.get("outline_weight_name", "Bold")
    f_file = cfg.get("outline_font", "BebasNeue-Regular.ttf")
    if cfg.get("outline_drop_s", True) and text[-1:] in "sS" and text[:-1].isdigit():
        text = text[:-1]                 # "1970s" -> "1970"
    if h > w and len(text) > 3 and cfg.get("outline_stack", True):          # tall tile: stack it, "19" over "70s"
        text = text[:2] + "\n" + text[2:]
    # size the text so it is ~1.12x the tile width: it bleeds past both sides, every digit still visible
    probe = font(400, ow, f_file)
    d0 = ImageDraw.Draw(Image.new("L", (1, 1)))
    tw = max(d0.textlength(ln, font=probe) for ln in text.split("\n"))
    fs = int(400 * (w * cfg.get("outline_bleed", 1.12)) / max(1, tw))
    fs = min(fs, int(h * (0.62 if "\n" in text else 1.25) * cfg.get("outline_bleed", 1.12) / 1.12))
    f = font(fs, ow, f_file)
    bbox = d0.multiline_textbbox((0, 0), text, font=f, spacing=-int(fs * 0.12), align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w - tw) * cfg.get("year_x", 0.5) - bbox[0]     # 0 shows the start of the year, 1 the end
    y = (h - th) / 2 - bbox[1] + h * 0.03 + h * (cfg.get("title_y", 0.5) - 0.5)
    stroke = max(2, fs // cfg.get("outline_weight", 85))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # faint solid copy, slightly larger and offset, gives the 'coming at you' depth
    ghost = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sp = -int(fs * 0.12)
    ImageDraw.Draw(ghost).multiline_text((x - fs * 0.02, y - fs * 0.02), text, font=f, fill=hex_rgb(cfg.get("outline_color", "#ffffff")) + (int(255 * cfg.get("ghost", 22 / 255)),),
                                         spacing=sp, align="center")
    ghost = ghost.filter(ImageFilter.GaussianBlur(fs // 40))
    a = int(255 * cfg.get("outline_opacity", 0.72))
    oc = hex_rgb(cfg.get("outline_color", "#ffffff"))
    ld.multiline_text((x, y), text, font=f, fill=(0, 0, 0, 0), stroke_width=stroke,
                      stroke_fill=oc + (a,), spacing=sp, align="center")
    out = img.convert("RGBA")
    sa = cfg.get("shadow", 0)
    if sa:                            # soft drop shadow under the outline
        k = min(w, h) / 720
        sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sh.putalpha(layer.getchannel("A").point(lambda v: int(v * sa)))
        sh = sh.filter(ImageFilter.GaussianBlur(max(1, int(cfg.get("shadow_blur", 10) * k))))
        out.alpha_composite(sh, (0, int(4 * k)))
    out.alpha_composite(ghost)
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


def background_name(img, title, cfg, fcfg):
    """The card's name in the top left of its background, a little way down, in the card's own font."""
    if not title or not cfg.get("name_show", False):
        return img
    w, h = img.size
    if cfg.get("name_card_font", True) is False:      # one font for every background instead of each card's own
        fcfg = {"font_file": cfg.get("name_font") or None, "weight": cfg.get("name_weight", "SemiBold"),
                "tracking": cfg.get("name_tracking", 0.1) / 0.7, "upper": cfg.get("name_upper", True)}
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    size = int(cfg.get("name_size", 64) * h / 1080 * fcfg.get("font_scale", 1.0))
    lines, f, tr = _fit(d, title, w * cfg.get("name_width", 0.42), size, fcfg.get("tracking", 0.14) * 0.7, fcfg.get("weight", "SemiBold"),
                        file=fcfg.get("font_file"), upper=fcfg.get("upper", True), max_lines=2)
    x0, y0 = w * cfg.get("name_x", 0.05), h * cfg.get("name_y", 0.14)
    lh = f.size * 1.1
    a = int(255 * cfg.get("name_opacity", 0.92))
    col = hex_rgb(cfg.get("name_color", "#ffffff"))
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    for i, ln in enumerate(lines):
        lw = sum(d.textlength(c, font=f) for c in ln) + tr * (len(ln) - 1)
        cx, cy = x0 + lw / 2, y0 + lh * i + lh / 2
        _tracked(sd, cx, cy + 3, ln, f, tr, (0, 0, 0, int(a * 0.75)))
        _tracked(d, cx, cy, ln, f, tr, col + (a,))
    # shading: the letters go from top brightness to bottom brightness down each line, like the logo shading
    top, bot = cfg.get("name_shade_top", 1.0), cfg.get("name_shade_bottom", 0.8)
    if top != 1 or bot != 1:
        ramp = Image.new("L", (w, h), 255)
        rd = ImageDraw.Draw(ramp)
        for i in range(len(lines)):
            cy = y0 + lh * i + lh / 2
            y1, y2 = int(cy - f.size * 0.42), int(cy + f.size * 0.42)
            for y in range(max(0, int(cy - lh / 2)), min(h, int(cy + lh / 2) + 1)):
                t = min(1, max(0, (y - y1) / max(1, y2 - y1)))
                rd.line([(0, y), (w, y)], fill=int(255 * (top + (bot - top) * t)))
        r, g, b, al = layer.split()
        r, g, b = (ImageChops.multiply(c, ramp) for c in (r, g, b))
        layer = Image.merge("RGBA", (r, g, b, al))
    out = img.convert("RGBA")
    glow = cfg.get("name_glow", 0.35)
    if glow > 0:      # soft halo in the glow color behind the letters
        m = layer.getchannel("A").filter(ImageFilter.GaussianBlur(max(2, f.size * cfg.get("name_glow_size", 1.2) * 0.45)))
        m = m.point(lambda v: min(255, int(v * 2.4 * glow)))
        halo = Image.new("RGBA", (w, h), hex_rgb(cfg.get("name_glow_color", "#788caa")) + (0,))
        halo.putalpha(m)
        out.alpha_composite(halo)
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(max(2, f.size // 14))))
    out.alpha_composite(layer)
    return out.convert("RGB")


# ---------- vibrant ----------

def accent_color(img):
    """A bright, saturated colour taken from the picture, for the subtitle."""
    small = img.convert("RGB").resize((64, 36))
    hsv = small.convert("HSV")
    bins = {}
    for (h, s, v) in _px(hsv):
        if s > 70 and v > 60:
            b = h // 16
            bins[b] = bins.get(b, 0) + s * v
    if not bins:
        return (235, 235, 240)
    h = (max(bins, key=bins.get) * 16 + 8) % 256
    px = Image.new("HSV", (1, 1), (h, 200, 250)).convert("RGB").getpixel((0, 0))
    return px


def vibrant_cover(still, title, subtitle, size, cfg=None):
    """Bold title filled with a brightened copy of the picture, a light outline, and a short
    subtitle in a colour pulled from the picture."""
    cfg = cfg or {}
    w, h = size
    k = min(w, h) / 720
    base = ImageEnhance.Contrast(ImageEnhance.Color(fill(still, w, h)).enhance(cfg.get("saturation", 1.25))).enhance(1.08)
    img = ImageEnhance.Brightness(base).enhance(cfg.get("backdrop", 0.5))
    img = Image.composite(img, Image.new("RGB", (w, h), DARK), _vignette(w, h, cfg.get("vignette", 0.75), 0.2))
    img = _fade(img, cfg, 0, 0.45)
    vivid = ImageEnhance.Brightness(ImageEnhance.Color(base).enhance(1.7)).enhance(cfg.get("vivid", 1.45))
    vivid = Image.blend(vivid, Image.new("RGB", (w, h), (255, 255, 255)), 0.18)

    d0 = ImageDraw.Draw(Image.new("L", (1, 1)))
    text = title.upper()
    size_ = int(cfg.get("title_size", 88) * 1.45 * k * cfg.get("font_scale", 1.0))
    max_w = w * (0.86 if h >= w else 0.82)
    words = text.split()
    wrap = cfg.get("lines", "auto")
    lines = [text]
    if wrap == 2 and len(words) > 1:
        mid = max(1, len(words) // 2)
        lines = [" ".join(words[:mid]), " ".join(words[mid:])]
    while True:
        f = font(size_, cfg.get("weight_v", "Black"), cfg.get("font_file"))
        tr = size_ * cfg.get("tracking_v", 0.01)
        widest = max(sum(d0.textlength(c, font=f) for c in ln) + tr * (len(ln) - 1) for ln in lines)
        if widest <= max_w or size_ < 30 * k:
            break
        if len(lines) == 1 and len(words) > 1 and (h > w if wrap == "auto" else wrap != 1):
            mid = max(1, len(words) // 2)
            lines = [" ".join(words[:mid]), " ".join(words[mid:])]
            continue
        size_ = int(size_ * 0.94)
    sub = (subtitle or "").upper() if cfg.get("show_subtitle", True) else ""
    sf = font(max(12, int(size_ * cfg.get("subtitle_size", 0.21))), "Bold")
    str_ = sf.size * cfg.get("subtitle_tracking", 0.14)
    lh = f.size * 1.0
    block = lh * len(lines) + (sf.size * 1.8 if sub else 0)
    top = h * cfg.get("title_y", 0.5) - block / 2

    mask = Image.new("L", (w, h), 0)
    outline = Image.new("L", (w, h), 0)
    md, od = ImageDraw.Draw(mask), ImageDraw.Draw(outline)
    sw = max(2 if "outline_width" not in cfg else 1, int(size_ * cfg.get("outline_width", 0.03)))
    for i, ln in enumerate(lines):
        widths = [d0.textlength(c, font=f) for c in ln]
        x = w / 2 - (sum(widths) + tr * (len(ln) - 1)) / 2
        y = top + lh * i
        for ch, cw in zip(ln, widths):
            od.text((x, y), ch, font=f, fill=255, stroke_width=sw, stroke_fill=255)
            md.text((x, y), ch, font=f, fill=255)
            x += cw + tr
    ring = ImageChops.subtract(outline, mask)
    sa = cfg.get("shadow", 0.55)
    shadow = outline.filter(ImageFilter.GaussianBlur(max(4 if "shadow_blur" not in cfg else 1, int(cfg.get("shadow_blur", 10) * k)))).point(lambda v: int(v * sa))
    img = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, shadow)
    img = Image.composite(vivid, img, mask)
    oa = cfg.get("outline", 0.8)
    img = Image.composite(Image.new("RGB", (w, h), hex_rgb(cfg.get("outline_color", "#f5f5fa"))), img, ring.point(lambda v: int(v * oa)))

    if sub:
        col = accent_color(still) if cfg.get("subtitle_color", "auto") == "auto" else hex_rgb(cfg["subtitle_color"])
        dd = ImageDraw.Draw(img)
        widths = [dd.textlength(c, font=sf) for c in sub]
        total = sum(widths) + str_ * (len(sub) - 1)
        if total > w * 0.9:
            sf = font(max(10, int(sf.size * w * 0.9 / total)), "Bold")
            str_ = sf.size * cfg.get("subtitle_tracking", 0.14) * 0.1 / 0.14
            widths = [dd.textlength(c, font=sf) for c in sub]
            total = sum(widths) + str_ * (len(sub) - 1)
        x = w / 2 - total / 2
        y = top + lh * len(lines) + sf.size * 0.8
        for ch, cw in zip(sub, widths):
            dd.text((x + 1, y + 2), ch, font=sf, fill=(0, 0, 0))
            dd.text((x, y), ch, font=sf, fill=col)
            x += cw + str_
    return img
