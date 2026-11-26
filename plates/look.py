"""The visual style: Cinema covers and blended panel backgrounds.

Every number that shapes the look lives in config/settings.json under
"cover" and "background", so you can tune it without touching this file.
"""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

FONT_PATH = str(Path(__file__).resolve().parent.parent / "fonts" / "Montserrat.ttf")
DARK = (8, 10, 14)


# ---------- shared helpers ----------

FONT_DIR = Path(FONT_PATH).parent


def font(size, weight, file=None):
    """Montserrat at a named weight, or a specific font file from fonts/ (e.g. a seasonal face)."""
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
    hues = [hh * 12 // 256 for (hh, ss, vv), al in zip(hsv.getdata(), sa.getdata()) if al > 128 and ss > 90 and vv > 60]
    if hues:
        counts = [hues.count(i) for i in range(12)]
        if sum(1 for c in counts if c > 0.08 * len(hues)) >= 5:
            return out
    # luminance over black where transparent, so the plate edge doesn't count as a mark
    # transparent areas take the plate's own brightness (the most common opaque value), so the
    # empty space around a small logo doesn't drag the local estimate toward black
    L = small.convert("L")
    hist = [0] * 256
    for v, al in zip(L.getdata(), sa.getdata()):
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
    marks = marks.resize((w0, h0), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.7))
    out.putalpha(ImageChops.subtract(a, marks))
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


def station_cover(still, logo, size, scale=1.0):
    """Network tile: a dim, blurred still behind a soft glow, white logo with a slight gradient."""
    w, h = size
    img = fill(still, w, h).filter(ImageFilter.GaussianBlur(max(6, w // 110)))
    img = ImageEnhance.Color(img).enhance(0.8)
    img = ImageEnhance.Brightness(img).enhance(0.24)
    dark = Image.new("RGB", (w, h), (6, 7, 10))
    img = Image.composite(img, dark, _vignette(w, h, 0.95, 0.15))
    glow = _mask_from_fn(w, h, lambda x, y: 0.22 * max(0, 1 - ((x - 0.5) * 2.2) ** 2 - ((y - 0.46) * 2.4 * (h / w if h > w else 1)) ** 2), 60)
    img = Image.composite(Image.new("RGB", (w, h), (120, 140, 170)), img, glow)

    lg = _vgradient(_prep_logo(logo, "white"))
    box_w = min(w * 0.8, w * (0.6 if h >= w else 0.42) * scale)
    box_h = min(h * 0.5, h * (0.26 if h > w else 0.32) * scale)
    k = min(box_w / lg.width, box_h / lg.height)
    lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), Image.LANCZOS)
    shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    shadow.putalpha(lg.getchannel("A").point(lambda v: int(v * 0.5)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    out = img.convert("RGBA")
    x, y = (w - lg.width) // 2, int(h * 0.46 - lg.height / 2)
    out.alpha_composite(shadow, (x, y + 4))
    out.alpha_composite(lg, (x, y))
    return out.convert("RGB")


def cover(still, title, cfg, size=None, logo=None, logo_scale=1.0):
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
    if logo is not None:
        lg = _prep_logo(logo, cfg.get("logo_mode", "white"))
        box_w = min(w * 0.86, w * (0.62 if h >= w else 0.46) * logo_scale)
        box_h = min(h * 0.6, h * (0.24 if h > w else 0.30) * logo_scale)
        s_ = min(box_w / lg.width, box_h / lg.height)
        lg = lg.resize((max(1, int(lg.width * s_)), max(1, int(lg.height * s_))), Image.LANCZOS)
        a = cfg["opacity"]
        lg.putalpha(lg.getchannel("A").point(lambda v: int(v * a)))
        shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
        shadow.putalpha(lg.getchannel("A").point(lambda v: int(v * 0.45)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(6))
        x, y = (w - lg.width) // 2, (h - lg.height) // 2
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


def brand_cover(logo, bg_hex, size, scale=1.0):
    """App-tile style: brand-colour field, full-colour logo, soft dark gradient rising from the bottom."""
    w, h = size
    bg = tuple(int(bg_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    img = Image.new("RGB", (w, h), bg)
    # gentle lift in the middle so the field isn't flat
    glow = _mask_from_fn(w, h, lambda x, y: 0.10 * max(0, 1 - (((x - 0.5) * 1.6) ** 2 + ((y - 0.42) * 2) ** 2)), 40)
    img = Image.composite(Image.new("RGB", (w, h), tuple(min(255, c + 40) for c in bg)), img, glow)
    # dark gradient coming up from the bottom
    fade = _mask_from_fn(w, h, lambda x, y: (max(0, (y - 0.45) / 0.55) ** 1.4) * 0.6, 10)
    img = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, fade)

    lg = _prep_logo(logo, "color")
    col, _ = _dominant(lg)
    if sum((col[i] - bg[i]) ** 2 for i in range(3)) ** 0.5 < 110:   # logo would vanish into the field
        lg = _prep_logo(logo, "white")
    box_w = min(w * 0.8, w * (0.58 if h >= w else 0.42) * scale)
    box_h = min(h * 0.5, h * (0.22 if h > w else 0.28) * scale)
    k = min(box_w / lg.width, box_h / lg.height)
    lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), Image.LANCZOS)
    out = img.convert("RGBA")
    out.alpha_composite(lg, ((w - lg.width) // 2, int(h * 0.44 - lg.height / 2)))
    return out.convert("RGB")


def _dominant(logo):
    """Average colour of the logo's visible pixels, and how bright it is (0..255)."""
    rgba = logo.convert("RGBA").resize((64, 64))
    px = [p for p in rgba.getdata() if p[3] > 128]
    if not px:
        return (40, 40, 48), 128
    r, g, b = (sum(p[i] for p in px) / len(px) for i in range(3))
    return (int(r), int(g), int(b)), 0.299 * r + 0.587 * g + 0.114 * b


def studio_cover(still, logo, size, scale=1.0):
    """Studio tile: the logo large and in its own colours over a deep field tinted from the logo,
    with the film still faintly underneath, like a studio's opening card."""
    w, h = size
    col, _ = _dominant(_prep_logo(logo, "color"))
    lg = _vgradient(_prep_logo(logo, "white"), 1.0, 0.82)   # every studio logo in white, same treatment
    if max(col) - min(col) < 25:      # grey or black logo: use a neutral blue-grey field
        col = (60, 70, 90)
    deep = tuple(max(6, int(c * 0.28)) for c in col)
    mid = tuple(max(10, int(c * 0.55)) for c in col)
    base = Image.new("RGB", (w, h), deep)
    glow = _mask_from_fn(w, h, lambda x, y: max(0, 1 - ((x - 0.5) * 1.5) ** 2 - ((y - 0.45) * 1.8) ** 2) * 0.75, 50)
    base = Image.composite(Image.new("RGB", (w, h), mid), base, glow)
    under = ImageEnhance.Brightness(ImageOps.grayscale(fill(still, w, h)).convert("RGB")).enhance(0.5)
    base = Image.blend(base, ImageChops.multiply(base, under.point(lambda v: min(255, v + 120))), 0.6)
    base = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), base,
                           _mask_from_fn(w, h, lambda x, y: (max(0, (y - 0.55) / 0.45) ** 1.5) * 0.55, 10))
    box_w = min(w * 0.88, w * (0.78 if h >= w else 0.62) * scale)
    box_h = min(h * 0.7, h * (0.42 if h > w else 0.56) * scale)
    k = min(box_w / lg.width, box_h / lg.height)
    lg = lg.resize((max(1, int(lg.width * k)), max(1, int(lg.height * k))), Image.LANCZOS)
    shadow = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    shadow.putalpha(lg.getchannel("A").point(lambda v: int(v * 0.55)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    out = base.convert("RGBA")
    x, y = (w - lg.width) // 2, int(h * 0.47 - lg.height / 2)
    out.alpha_composite(shadow, (x, y + 6))
    out.alpha_composite(lg, (x, y))
    return out.convert("RGB")


def person_cover(headshot, name, role, size, cfg):
    """Portrait tile for a person: moody headshot, dark vignette, name anchored bottom left."""
    w, h = size
    img = fill(headshot, w, h, focus_x=0.5, focus_y=0.18)
    img = ImageEnhance.Color(img).enhance(0.75)
    img = ImageEnhance.Contrast(img).enhance(1.1)
    dark = Image.new("RGB", (w, h), (6, 7, 10))
    img = Image.composite(img, dark, _vignette(w, h, 0.85, 0.3))
    img = Image.composite(dark, img, _mask_from_fn(w, h, lambda x, y: (max(0, (y - 0.5) / 0.5) ** 1.3) * 0.92, 12))

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * cfg["opacity"])
    words = name.upper().split()
    lines = [" ".join(words[:1]), " ".join(words[1:])] if len(words) > 1 else [name.upper()]
    size_ = int(min(w, h) * 0.085)
    tr = size_ * 0.12
    while size_ > 18:
        f = font(size_, cfg.get("weight", "Bold"))
        widest = max(sum(d.textlength(c, font=f) for c in ln) + tr * (len(ln) - 1) for ln in lines)
        if widest <= w * 0.84:
            break
        size_ -= 2
        tr = size_ * 0.12
    f = font(size_, cfg.get("weight", "Bold"))
    small = font(max(12, int(size_ * 0.32)), "SemiBold")
    x0 = int(w * 0.08)
    lh = size_ * 1.02
    bottom = h - int(h * 0.06) - (small.size + 10 if role else 0)
    y = bottom - lh * len(lines)
    for ln in lines:
        cx = x0
        for ch in ln:
            d.text((cx, y + 2), ch, font=f, fill=(0, 0, 0, 90))
            d.text((cx, y), ch, font=f, fill=(255, 255, 255, a))
            cx += d.textlength(ch, font=f) + tr
        y += lh
    if role:
        cx = x0
        for ch in role.upper():
            d.text((cx, y + 6), ch, font=small, fill=(255, 255, 255, int(a * 0.55)))
            cx += d.textlength(ch, font=small) + small.size * 0.35
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


def plain_field(size):
    """Dark textured field used when a tile has no usable imagery."""
    w, h = size
    img = Image.new("RGB", (w, h), (12, 13, 17))
    glow = _mask_from_fn(w, h, lambda x, y: 0.35 * max(0, 1 - ((x - 0.5) * 1.4) ** 2 - ((y - 0.45) * 1.8) ** 2), 50)
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
    img = grade(fill(still, w, h))
    img = ImageEnhance.Brightness(img).enhance(0.55)
    dark = Image.new("RGB", (w, h), DARK)
    img = Image.composite(img, dark, _vignette(w, h, 0.85, 0.2))
    f_file = cfg.get("outline_font", "BebasNeue-Regular.ttf")
    if cfg.get("outline_drop_s", True) and text[-1:] in "sS" and text[:-1].isdigit():
        text = text[:-1]                 # "1970s" -> "1970"
    if h > w and len(text) > 3:          # tall tile: stack it, "19" over "70s"
        text = text[:2] + "\n" + text[2:]
    # size the text so it is ~1.12x the tile width: it bleeds past both sides, every digit still visible
    probe = font(400, "Bold", f_file)
    d0 = ImageDraw.Draw(Image.new("L", (1, 1)))
    tw = max(d0.textlength(ln, font=probe) for ln in text.split("\n"))
    fs = int(400 * (w * cfg.get("outline_bleed", 1.12)) / max(1, tw))
    fs = min(fs, int(h * (0.62 if "\n" in text else 1.25)))
    f = font(fs, "Bold", f_file)
    bbox = d0.multiline_textbbox((0, 0), text, font=f, spacing=-int(fs * 0.12), align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w - tw) / 2 - bbox[0]
    y = (h - th) / 2 - bbox[1] + h * 0.03
    stroke = max(2, fs // cfg.get("outline_weight", 85))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # faint solid copy, slightly larger and offset, gives the 'coming at you' depth
    ghost = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sp = -int(fs * 0.12)
    ImageDraw.Draw(ghost).multiline_text((x - fs * 0.02, y - fs * 0.02), text, font=f, fill=(255, 255, 255, 22),
                                         spacing=sp, align="center")
    ghost = ghost.filter(ImageFilter.GaussianBlur(fs // 40))
    a = int(255 * cfg.get("outline_opacity", 0.72))
    ld.multiline_text((x, y), text, font=f, fill=(0, 0, 0, 0), stroke_width=stroke,
                      stroke_fill=(255, 255, 255, a), spacing=sp, align="center")
    out = img.convert("RGBA")
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
