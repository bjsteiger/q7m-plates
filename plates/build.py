"""Build cover and background images for each tile.

Run:   python -m plates.build
Needs: TMDB_API_KEY (required), TRAKT_CLIENT_ID (optional, for Trakt-list tiles)

Writes images only to site/, which the GitHub workflow publishes to Pages:
  site/art/<shelf>/<tile>/cover-wide.webp     1280x720  (16:9)
  site/art/<shelf>/<tile>/cover-poster.webp   800x1200  (2:3)
  site/art/<shelf>/<tile>/cover-square.webp   1000x1000 (1:1)
  site/art/<shelf>/<tile>/background.webp     1920x1080
  site/manifest.json   <- which tiles rendered this week (tile names and paths only)

Every tile gets all three cover shapes, so a tile's shape can change without a rebuild.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import traceback
from pathlib import Path

from PIL import Image

from . import look, sources
from .sources import TMDB, Wikimedia, gather, trakt_from_env

ROOT = Path(__file__).resolve().parent.parent


# a subtle field color for studios whose logos have no color of their own (a card's own brand color wins)
STUDIO_TINTS = {"20th-century-studios": "#b08a3e", "a24": "#4f7a6b", "blumhouse-productions": "#8a1c1c", "dreamworks-pictures": "#3f6fb5",
                "focus-features": "#5a6e8c", "illumination": "#e0b21f", "lionsgate": "#b9802f", "lucasfilm-ltd": "#6f7a8a",
                "new-line-cinema": "#6a5a8c", "pixar": "#3a7bbf", "searchlight-pictures": "#c48a2c", "sony-pictures": "#4b6fa8",
                "studio-ghibli": "#5d8fb8", "walt-disney-studio": "#1f4fb5"}


def slug(s):
    s = s.lower().replace("&", "and").replace("+", "plus")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


class OnlineFetcher:
    def __init__(self, settings):
        key = os.environ.get("TMDB_API_KEY")
        if not key:
            sys.exit("TMDB_API_KEY is not set. Add it under Settings > Secrets and variables > Actions.")
        self.tmdb = TMDB(key)
        self.trakt = trakt_from_env()
        self.want = settings["titles_per_tile"]
        self.min_votes = settings["min_votes"]

    def titles(self, tile, art_query=None, mode=None):
        return gather(tile, self.tmdb, self.trakt, want=self.want, min_votes=self.min_votes,
                      art_query=art_query, mode=mode)

    def cover_still(self, t):
        path = self.tmdb.clean_backdrop(t["media"], t["id"], t["backdrop"])
        return self.tmdb.image(path, "original")

    def logo(self, tile, rule, art_query):
        """Return (logo image, where it came from) or (None, reason)."""
        kind = rule["type"]
        def first(field):
            for s in tile["sources"]:
                ids = s.get("tmdbDiscover", {}).get("_0", {}).get(field) or []
                if ids:
                    return ids[0]
            return None
        try:
            if kind == "network":
                nid = first("networks")
                path = nid and self.tmdb.best_logo("network", nid)
            elif kind == "company":
                cid = first("companies") or self.tmdb.search_company(tile["title"])
                path = cid and self.tmdb.best_logo("company", cid)
            elif kind == "service":
                prov = (art_query or {}).get("provider", tile["title"])
                img, pname = self.tmdb.provider_icon(prov)
                return img, (f"tmdb app icon: {pname}" if img is not None else f"no app icon for {prov}")
            elif kind == "person":
                img = self.tmdb.person_headshot(tile["title"])
                return img, ("tmdb headshot" if img is not None else "no headshot")
            elif kind == "wikimedia":
                wm = self.wiki = getattr(self, "wiki", None) or Wikimedia()
                f = rule.get("files", {}).get(tile["title"])
                if not f:
                    return None, "no pinned logo file"
                img, src = wm.file(f)
                return img, (f"commons: {src}" if img is not None else "commons file not found")
            else:
                return None, f"unknown logo type {kind}"
            if not path:
                return None, "no TMDB logo"
            return self.tmdb.logo_image(path), f"tmdb {kind} logo"
        except Exception as e:
            return None, f"logo lookup failed: {e}"

    def still(self, t, big=False):
        return self.tmdb.image(t["backdrop"], "original" if big else "w1280")

    def poster_still(self, t):
        path = self.tmdb.clean_poster(t["media"], t["id"])
        return self.tmdb.image(path, "original") if path else None


class OfflineFetcher:
    """Uses local images instead of TMDB, for testing the look without a key."""

    def __init__(self, settings, folder):
        self.files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
        self.want = settings["titles_per_tile"]

    def titles(self, tile, art_query=None, mode=None):
        n = len(tile["title"])
        return [{"media": "movie", "id": (n * 7 + i) % len(self.files), "title": f.stem, "backdrop": str(f), "popularity": 0}
                for i, f in enumerate(self.files[n % len(self.files):] + self.files[:n % len(self.files)])][: self.want]

    def cover_still(self, t):
        return Image.open(t["backdrop"]).convert("RGB")

    def still(self, t, big=False):
        return Image.open(t["backdrop"]).convert("RGB")

    def poster_still(self, t):
        return None

    def logo(self, tile, rule, art_query):
        return None, "offline"


# per-tile art style overrides (settings "tile_style") map to these logo rules
STYLE_RULES = {
    "brand": {"type": "service", "style": "brand", "scale": 1.7},
    "station": {"type": "network", "style": "station"},
    "studio": {"type": "company", "style": "studio"},
    "portrait-director": {"type": "person", "style": "portrait", "role": "Director"},
    "portrait-actor": {"type": "person", "style": "portrait", "role": "Actor"},
    "portrait-actress": {"type": "person", "style": "portrait", "role": "Actress"},
}

LOGO_EXTS = (".png", ".webp", ".jpg", ".jpeg", ".svg")


def local_logo(settings, shelf, tile):
    """A logo you saved in logos/<shelf>/<tile>.<ext>, unless settings say to use TMDB for it.
    Returns (image, background colour or None, note) or (None, None, None)."""
    key = f"{shelf}/{tile}"
    if settings.get("logo_choice", {}).get(key, "").startswith("tmdb"):
        return None, None, None
    folder = ROOT / "logos" / slug(shelf)
    path = next((folder / f"{slug(tile)}{e}" for e in LOGO_EXTS if (folder / f"{slug(tile)}{e}").exists()), None)
    if not path:
        return None, None, None
    data = path.read_bytes()
    if path.suffix == ".svg":
        import cairosvg
        data = cairosvg.svg2png(bytestring=data, output_width=1800)
    import io
    raw = Image.open(io.BytesIO(data)).convert("RGBA")
    img = raw
    opaque = raw.getchannel("A").getextrema()[0] == 255
    if opaque:
        img = look.knock_background(raw)
    if key in settings.get("logo_parts", {}):        # only split files that hold several logos
        blocks = look.split_blocks(img)
        img = blocks[min(settings["logo_parts"][key], len(blocks) - 1)]
    if key in settings.get("logo_white", []):
        img = look._prep_logo(img, "white")
    bg = settings.get("brand_colors", {}).get(key) or (look.corner_color(raw) if opaque else None)
    return img, bg, f"saved logo {path.relative_to(ROOT)}"


def picker_candidates(fetch, settings, tile, out):
    """Collect logo options for one tile: your saved file plus TMDB's alternatives.
    Thumbnails go to site/picker/<shelf>/<tile>/ for the picker page."""
    shelf, name = tile["shelf"], tile["title"]
    key = f"{shelf}/{name}"
    rule = settings.get("logos", {}).get(shelf) or {}
    if rule.get("type") not in ("network", "company", "service"):
        return None
    d = out / "picker" / slug(shelf) / slug(name)
    d.mkdir(parents=True, exist_ok=True)
    opts = []
    folder = ROOT / "logos" / slug(shelf)
    saved = next((folder / f"{slug(name)}{e}" for e in LOGO_EXTS if (folder / f"{slug(name)}{e}").exists()), None)
    if saved:
        import shutil
        shutil.copy(saved, d / f"saved{saved.suffix}")
        opts.append({"id": "saved", "label": "Saved in repo", "src": f"picker/{slug(shelf)}/{slug(name)}/saved{saved.suffix}"})
    tm = getattr(fetch, "tmdb", None)
    if tm:
        paths = []
        def first(field):
            for s_ in tile["sources"]:
                ids = s_.get("tmdbDiscover", {}).get("_0", {}).get(field) or []
                if ids:
                    return ids[0]
        try:
            if rule["type"] == "network" and first("networks"):
                paths = tm.all_logos("network", first("networks"))
            elif rule["type"] == "company":
                cid = first("companies") or tm.search_company(name)
                paths = tm.all_logos("company", cid) if cid else []
            elif rule["type"] == "service":
                from .sources import find_provider
                aq = settings.get("art_queries", {}).get(key, {})
                pr = find_provider(tm, aq.get("provider", name))
                if pr and pr.get("logo_path"):
                    paths = [pr["logo_path"]]
        except Exception as e:
            print(f"    picker {key}: {e}")
        for i, pth in enumerate(paths):
            try:
                tm.logo_image(pth).save(d / f"tmdb-{i}.png")
                opts.append({"id": f"tmdb:{pth}", "label": f"TMDB {i + 1}", "src": f"picker/{slug(shelf)}/{slug(name)}/tmdb-{i}.png"})
            except Exception:
                continue
    choice = settings.get("logo_choice", {}).get(key)
    current = choice or ("saved" if saved else "tmdb")
    return {"shelf": shelf, "tile": name, "key": key, "current": current, "options": opts}


def pick(titles, used, offset):
    """Rotate through the top titles week to week, avoiding covers used by other tiles."""
    if not titles:
        return None
    order = titles[offset % len(titles):] + titles[:offset % len(titles)]
    for t in order:
        if (t["media"], t["id"]) not in used:
            return t
    return order[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="limit to these shelf titles (repeatable)")
    ap.add_argument("--limit", type=int, help="max tiles per shelf, for quick tests")
    ap.add_argument("--offline", help="folder of local stills to use instead of TMDB")
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--full", action="store_true", help="redraw every tile, even unchanged ones")
    args = ap.parse_args()

    settings = json.loads((ROOT / "config" / "settings.json").read_text())
    rules = settings.get("rules", {})
    sources.NO_HORROR = rules.get("noHorror", True)
    sources.FRESH_DAYS = int(rules.get("freshDays", 540))
    tiles = json.loads((ROOT / "config" / "tiles.json").read_text())
    out = Path(args.out)
    (out / "art").mkdir(parents=True, exist_ok=True)

    fetch = OfflineFetcher(settings, args.offline) if args.offline else OnlineFetcher(settings)
    wanted = args.only or settings["shelves"] + settings.get("parked_shelves", [])
    skip = set(settings.get("skip_tiles", []))
    today = dt.date.today()
    week = today.isocalendar()[1]
    month = today.year * 12 + today.month
    refresh = settings.get("refresh", {})

    def tier(shelf):
        for name, shelves_ in refresh.items():
            if shelf in shelves_:
                return name
        return "weekly"

    def offsets(shelf):
        """(cover offset, background offset) for this shelf's refresh tier."""
        t = tier(shelf)
        if not settings.get("rotate_weekly", True):
            return 0, 0
        if t == "fixed":
            return 0, (month if shelf in refresh.get("fixed_monthly_bg", []) else week)
        if t == "monthly":
            return month, month
        return week, week

    used, manifest, per_shelf = set(), [], {}
    done = failed = kept = reused = 0

    # Change detection: a tile whose inputs match the live site's last build is copied, not redrawn.
    # Inputs include the code, the house style, the tile's rows and overrides, its saved logo and the
    # refresh period, so Look changes, code changes and the weekly/monthly refresh still redraw.
    site_url = os.environ.get("PAGES_URL", "https://bjsteiger.github.io/q7m-plates").rstrip("/")
    prev = {}
    if not args.offline and not args.full and not os.environ.get("FULL_BUILD"):
        try:
            import requests
            live = requests.get(site_url + "/manifest.json", timeout=30).json()
            prev = {f'{t["shelf"]}/{t["tile"]}': t for t in live.get("tiles", []) if t.get("sig")}
            print(f"{len(prev)} tiles from the live site can be reused if unchanged")
        except Exception as e:
            print(f"live manifest unavailable ({e}), drawing everything")

    looks = settings.get("looks", {})

    def look_of(key, shelf):
        return settings.get("tile_look", {}).get(key) or settings.get("tile_style", {}).get(key) or "cinema"

    build_only = None
    bo = ROOT / "config" / "build-only.json"
    if bo.exists() and os.environ.get("BUILD_EVENT", "push") == "push" and not args.full and not os.environ.get("FULL_BUILD"):
        sel = json.loads(bo.read_text())
        if sel.get("full"):
            prev = {}
            print("full redraw requested")
        else:
            build_only = set(sel.get("keys", []))
            print(f"building only {len(build_only)} selected cards, the rest are copied from the live site")

    def studio_tint(shelf, name, key):
        rule = settings.get("logos", {}).get(shelf) or {}
        lk = look_of(key, shelf)
        lb = settings.get("look_base", {}).get(lk, lk)
        if rule.get("style") != "studio" and lb != "studio":
            return None
        return settings.get("brand_colors", {}).get(key) or STUDIO_TINTS.get(slug(name))

    def tile_sig(tile, key, shelf, name, aq, t_, cov_off, bg_off):
        logo_hash = None
        folder = ROOT / "logos" / slug(shelf)
        lp = next((folder / f"{slug(name)}{e}" for e in LOGO_EXTS if (folder / f"{slug(name)}{e}").exists()), None)
        if lp:
            logo_hash = hashlib.sha1(lp.read_bytes()).hexdigest()
        lk = look_of(key, shelf)
        lb = settings.get("look_base", {}).get(lk, lk)
        lk_base = "portrait" if lb.startswith("portrait") else lb
        parts = [look.DRAW_VERSION, look.STYLE_VERSIONS.get(lk_base), lk, looks.get(lk if lk != lb else lk_base), tile.get("sources"), aq, t_, cov_off, bg_off,
                 settings["cover"].get("shapes"), settings.get("background"),
                 settings.get("rules"), settings.get("logos", {}).get(shelf), shelf in settings.get("outline_shelves", []),
                 shelf in refresh.get("classic_art", []), shelf in refresh.get("fixed_monthly_bg", []), logo_hash]
        parts += [settings.get(k, {}).get(key) for k in ("fonts", "tile_style", "logo_choice", "logo_scale", "brand_colors", "logo_parts", "rename", "subtitles")]
        parts += [key in settings.get(k, []) for k in ("logo_color", "logo_white")]
        if key in settings.get("logo_y", {}):
            parts.append(["logo_y", settings["logo_y"][key]])
        tint = studio_tint(shelf, name, key)
        if tint:
            parts.append(tint)
        return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]

    # fetch the live art of every card in parallel up front, so reusing unchanged cards is quick
    import requests
    from concurrent.futures import ThreadPoolExecutor
    fetched = {}
    sess = requests.Session()
    sess.mount("https://", requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32))

    def grab(f):
        for _ in range(3):
            try:
                r = sess.get(f"{site_url}/{f}", timeout=60)
                if r.ok:
                    return f, r.content
            except requests.RequestException:
                pass
        return f, None
    if prev:
        want = sorted({f for e in prev.values() for f in list(e.get("covers", {}).values()) + [e["background"]]})
        with ThreadPoolExecutor(24) as pool:
            for f, data in pool.map(grab, want):
                if data is not None:
                    fetched[f] = data
        print(f"fetched {len(fetched)} of {len(want)} live files for reuse")

    def reuse(entry, d_out):
        files = list(entry.get("covers", {}).values()) + [entry["background"]]
        for f in files:
            data = fetched.get(f)
            if data is None:
                data = grab(f)[1]
                if data is None:
                    return False
            dst = d_out / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        return True

    for tile in tiles:
        shelf, name = tile["shelf"], tile["title"]
        if shelf not in wanted:
            continue
        per_shelf[shelf] = per_shelf.get(shelf, 0) + 1
        if args.limit and per_shelf[shelf] > args.limit:
            continue
        key = f"{shelf}/{name}"
        if key in skip:
            kept += 1
            continue
        rel = f"art/{slug(shelf)}/{slug(name)}"
        try:
            aq = settings.get("art_queries", {}).get(key)
            if aq is None and shelf == "Directors":
                aq = {"director": name}
            t_ = tier(shelf)
            cov_off, bg_off = offsets(shelf)
            sig = tile_sig(tile, key, shelf, name, aq, t_, cov_off, bg_off)
            old = prev.get(key)
            if old and build_only is not None and key not in build_only:
                if reuse(old, out):            # not selected this time: keep the live art as it is
                    manifest.append(old)
                    reused += 1
                continue
            if build_only is None and old and old.get("sig") == sig and reuse(old, out):
                manifest.append(old)
                reused += 1
                continue
            classic = t_ in ("monthly",) or shelf in refresh.get("classic_art", [])
            bg_titles = fetch.titles(tile, aq, mode="classic" if classic else "fresh")
            cov_titles = fetch.titles(tile, aq, mode="classic") if t_ == "fixed" and not classic else bg_titles
            d = out / rel
            d.mkdir(parents=True, exist_ok=True)
            label = settings.get("rename", {}).get(key, name)

            tstyle = settings.get("tile_style", {}).get(key)
            tstyle = settings.get("look_base", {}).get(tstyle, tstyle)     # your own looks draw like the look they're based on
            if len(cov_titles) < 2 or len(bg_titles) < 2 or tstyle == "title":
                # nothing usable once horror is filtered out: title-only cover, plain background
                for shape, (w, h) in settings["cover"]["shapes"].items():
                    tl = looks.get("title", {})
                    look.title_only_cover(label, {**settings["cover"], **{k: v for k, v in tl.items() if k != "sizes"}, **tl.get("sizes", {}).get(shape, {})}, (w, h)).save(d / f"cover-{shape}.webp", "WEBP", quality=88)
                bw, bh = settings["background"]["width"], settings["background"]["height"]
                look.plain_field((bw, bh)).save(d / "background.webp", "WEBP", quality=84)
                manifest.append({"shelf": shelf, "tile": name, "path": rel, "title_only": True, "sig": sig,
                                 "covers": {sh: f"{rel}/cover-{sh}.webp" for sh in settings["cover"]["shapes"]},
                                 "background": f"{rel}/background.webp"})
                done += 1
                print(f"  + {key}  (title only, no usable imagery)")
                continue

            c = pick(cov_titles, used, 0 if isinstance(aq, dict) and aq.get("titles") else cov_off)   # named titles: the first one is the cover
            used.add((c["media"], c["id"]))
            rest = [t for t in bg_titles if (t["media"], t["id"]) != (c["media"], c["id"])]
            k = bg_off % max(1, len(rest))
            rest = rest[k:] + rest[:k]
            n = settings["background"]["panels"]
            bg_imgs = [fetch.still(t, big=i < n) for i, t in enumerate(rest)]
            rule = settings.get("logos", {}).get(shelf)
            if tstyle in ("cinema", "outline", "vibrant"):
                rule = None
            elif tstyle in STYLE_RULES:
                rule = {**STYLE_RULES[tstyle], **({k: v for k, v in (rule or {}).items() if k == "scale"} if rule and rule.get("style") == STYLE_RULES[tstyle]["style"] else {})}
            ov_bg, logo, logo_note = None, None, ""
            choice = settings.get("logo_choice", {}).get(key, "")
            if choice == "none":
                rule = None
            if rule and choice.startswith("tmdb:") and hasattr(fetch, "tmdb"):
                try:
                    logo, logo_note = fetch.tmdb.logo_image(choice[5:]), f"picked TMDB logo {choice[5:]}"
                except Exception as e:
                    print(f"    {key}: picked TMDB logo failed ({e})")
            if rule and logo is None and not choice.startswith("tmdb"):
                try:
                    logo, ov_bg, logo_note = local_logo(settings, shelf, name)
                except Exception as e:
                    print(f"    {key}: saved logo unreadable ({e})")
                    logo = None
            ov = logo is not None and logo_note.startswith("saved")
            if logo is None and rule:
                logo, logo_note = fetch.logo(tile, rule, aq)
            if rule and logo is None:
                print(f"    {key}: {logo_note}, using text")
            lk = look_of(key, shelf)
            lks = looks.get(lk) if lk in settings.get("look_base", {}) else looks.get("portrait" if lk.startswith("portrait") else lk, {})
            lks = lks or {}
            scale = lks.get("scale", (rule or {}).get("scale", 1.0)) * settings.get("logo_scale", {}).get(key, 1.0)
            def cfg_for(shape):
                """The look's settings, then that size's own adjustments, then this card's own choices."""
                sz = lks.get("sizes", {}).get(shape, {})
                return {**settings["cover"], "logo_mode": (rule or {}).get("mode", settings["cover"].get("logo_mode", "white")),
                        **{k: v for k, v in lks.items() if k not in ("scale", "sizes")}, **{k: v for k, v in sz.items() if k != "scale"},
                        **settings.get("fonts", {}).get(key, {}),
                        **({"logo_y": settings["logo_y"][key]} if key in settings.get("logo_y", {}) else {})}
            base_scale = scale
            wide_still = fetch.cover_still(c)
            outline = tstyle == "outline" or (shelf in settings.get("outline_shelves", []) and tstyle in (None, "outline"))
            for shape, (w, h) in settings["cover"]["shapes"].items():
                cover_cfg = cfg_for(shape)
                sz_scale = lks.get("sizes", {}).get(shape, {}).get("scale")
                scale = base_scale if sz_scale is None else sz_scale * settings.get("logo_scale", {}).get(key, 1.0)
                still = wide_still
                if h > w and not (rule and logo is not None):   # poster: text-free poster when TMDB has one
                    still = fetch.poster_still(c) or wide_still
                if tstyle == "vibrant":
                    im = look.vibrant_cover(still if h > w else wide_still, label, settings.get("subtitles", {}).get(key, ""), (w, h), cover_cfg)
                elif outline:
                    im = look.outline_year_cover(wide_still, label, (w, h), cover_cfg)
                elif logo is not None and rule.get("style") == "brand":
                    # the app icon's own backdrop colour becomes the tile colour; its mark sits on top
                    if ov:
                        bgc = ov_bg or "#0b0d10"
                        im = look.brand_cover(logo, bgc, (w, h), scale, cover_cfg)
                    else:
                        bgc = rule.get("colors", {}).get(name) or look.corner_color(logo)
                        im = look.brand_cover(look.knock_background(logo), bgc, (w, h), scale, cover_cfg)
                elif logo is not None and rule.get("style") == "station":
                    im = look.station_cover(still, logo, (w, h), scale,
                                           color=f"{shelf}/{name}" in settings.get("logo_color", []), cfg=cover_cfg)
                elif logo is not None and rule.get("style") == "studio":
                    im = look.studio_cover(wide_still, logo, (w, h), scale, {**cover_cfg, "tint": studio_tint(shelf, name, key), "logo_color": f"{shelf}/{name}" in settings.get("logo_color", [])})
                elif logo is not None and rule.get("style") == "portrait":
                    im = look.person_cover(logo, label, rule.get("role", ""), (w, h), cover_cfg)
                else:
                    im = look.cover(still, label, cover_cfg, (w, h), logo=logo, logo_scale=scale)
                im.save(d / f"cover-{shape}.webp", "WEBP", quality=88)
            look.background(bg_imgs, settings["background"]).save(d / "background.webp", "WEBP", quality=84)
            manifest.append({"shelf": shelf, "tile": name, "path": rel, "sig": sig,
                             "covers": {sh: f"{rel}/cover-{sh}.webp" for sh in settings["cover"]["shapes"]},
                             "background": f"{rel}/background.webp"})
            done += 1
            print(f"  + {key}  (cover: {c['title']}{'; ' + logo_note if logo is not None else ''})")
        except Exception as e:  # one bad tile shouldn't sink the run
            failed += 1
            print(f"  ! {key}: {e}")
            traceback.print_exc(limit=1)

    # logo picker page and its data
    cands = []
    for tile in tiles:
        if tile["shelf"] in wanted:
            cnd = picker_candidates(fetch, settings, tile, out)
            if cnd:
                cands.append(cnd)
    (out / "picker").mkdir(exist_ok=True)
    (out / "picker" / "candidates.json").write_text(json.dumps(cands, indent=1))
    (out / "picker" / "index.html").write_text((ROOT / "plates" / "picker.html").read_text(), encoding="utf-8")
    (out / ".nojekyll").write_text("")
    (out / "manifest.json").write_text(json.dumps({
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "week": week, "rendered": done, "reused": reused, "skipped": kept, "failed": failed,
        "tiles": manifest,
    }, indent=1))
    print(f"\nDone. rendered={done} reused={reused} skipped={kept} failed={failed}")
    if done == 0 and reused == 0:
        sys.exit("Nothing rendered, not publishing.")


if __name__ == "__main__":
    main()
