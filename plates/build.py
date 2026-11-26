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
import json
import os
import re
import sys
import traceback
from pathlib import Path

from PIL import Image

from . import look
from .sources import TMDB, Wikimedia, gather, trakt_from_env

ROOT = Path(__file__).resolve().parent.parent


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
    args = ap.parse_args()

    settings = json.loads((ROOT / "config" / "settings.json").read_text())
    tiles = json.loads((ROOT / "config" / "tiles.json").read_text())
    out = Path(args.out)
    (out / "art").mkdir(parents=True, exist_ok=True)

    fetch = OfflineFetcher(settings, args.offline) if args.offline else OnlineFetcher(settings)
    wanted = args.only or settings["shelves"]
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
    done = failed = kept = 0

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
            classic = t_ in ("monthly",) or shelf in refresh.get("classic_art", [])
            bg_titles = fetch.titles(tile, aq, mode="classic" if classic else "fresh")
            cov_titles = fetch.titles(tile, aq, mode="classic") if t_ == "fixed" and not classic else bg_titles
            d = out / rel
            d.mkdir(parents=True, exist_ok=True)
            label = settings.get("rename", {}).get(key, name)

            if len(cov_titles) < 2 or len(bg_titles) < 2:
                # nothing usable once horror is filtered out: title-only cover, plain background
                for shape, (w, h) in settings["cover"]["shapes"].items():
                    look.title_only_cover(label, settings["cover"], (w, h)).save(d / f"cover-{shape}.webp", "WEBP", quality=88)
                bw, bh = settings["background"]["width"], settings["background"]["height"]
                look.plain_field((bw, bh)).save(d / "background.webp", "WEBP", quality=84)
                manifest.append({"shelf": shelf, "tile": name, "path": rel, "title_only": True,
                                 "covers": {sh: f"{rel}/cover-{sh}.webp" for sh in settings["cover"]["shapes"]},
                                 "background": f"{rel}/background.webp"})
                done += 1
                print(f"  + {key}  (title only, no usable imagery)")
                continue

            c = pick(cov_titles, used, cov_off)
            used.add((c["media"], c["id"]))
            rest = [t for t in bg_titles if (t["media"], t["id"]) != (c["media"], c["id"])]
            k = bg_off % max(1, len(rest))
            rest = rest[k:] + rest[:k]
            n = settings["background"]["panels"]
            bg_imgs = [fetch.still(t, big=i < n) for i, t in enumerate(rest)]
            rule = settings.get("logos", {}).get(shelf)
            ov_bg, logo, logo_note = None, None, ""
            choice = settings.get("logo_choice", {}).get(key, "")
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
            scale = (rule or {}).get("scale", 1.0)
            cover_cfg = {**settings["cover"], "logo_mode": (rule or {}).get("mode", settings["cover"].get("logo_mode", "white")),
                         **settings.get("fonts", {}).get(key, {})}
            wide_still = fetch.cover_still(c)
            outline = shelf in settings.get("outline_shelves", [])
            for shape, (w, h) in settings["cover"]["shapes"].items():
                still = wide_still
                if h > w and not (rule and logo is not None):   # poster: text-free poster when TMDB has one
                    still = fetch.poster_still(c) or wide_still
                if outline:
                    im = look.outline_year_cover(wide_still, label, (w, h), cover_cfg)
                elif logo is not None and rule.get("style") == "brand":
                    # the app icon's own backdrop colour becomes the tile colour; its mark sits on top
                    if ov:
                        bgc = ov_bg or "#0b0d10"
                        im = look.brand_cover(logo, bgc, (w, h), scale)
                    else:
                        bgc = rule.get("colors", {}).get(name) or look.corner_color(logo)
                        im = look.brand_cover(look.knock_background(logo), bgc, (w, h), scale)
                elif logo is not None and rule.get("style") == "station":
                    im = look.station_cover(still, logo, (w, h), scale,
                                           color=f"{shelf}/{name}" in settings.get("logo_color", []))
                elif logo is not None and rule.get("style") == "studio":
                    im = look.studio_cover(wide_still, logo, (w, h), scale)
                elif logo is not None and rule.get("style") == "portrait":
                    im = look.person_cover(logo, label, rule.get("role", ""), (w, h), cover_cfg)
                else:
                    im = look.cover(still, label, cover_cfg, (w, h), logo=logo, logo_scale=scale)
                im.save(d / f"cover-{shape}.webp", "WEBP", quality=88)
            look.background(bg_imgs, settings["background"]).save(d / "background.webp", "WEBP", quality=84)
            manifest.append({"shelf": shelf, "tile": name, "path": rel,
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
        "week": week, "rendered": done, "skipped": kept, "failed": failed,
        "tiles": manifest,
    }, indent=1))
    print(f"\nDone. rendered={done} skipped={kept} failed={failed}")
    if done == 0:
        sys.exit("Nothing rendered, not publishing.")


if __name__ == "__main__":
    main()
