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

    def titles(self, tile, art_query=None):
        return gather(tile, self.tmdb, self.trakt, want=self.want, min_votes=self.min_votes, art_query=art_query)

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
                nid = self.tmdb.service_network(prov, tile["title"])
                path = nid and self.tmdb.best_logo("network", nid)
            elif kind == "wikimedia":
                wm = self.wiki = getattr(self, "wiki", None) or Wikimedia()
                f = rule.get("files", {}).get(tile["title"])
                img, src = wm.file(f) if f else wm.logo(rule.get("queries", {}).get(tile["title"], tile["title"] + " logo"))
                return img, f"commons: {src}" if img else "no commons logo"
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

    def titles(self, tile, art_query=None):
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
    week = dt.date.today().isocalendar()[1]
    offset = week if settings.get("rotate_weekly", True) else 0

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
            titles = fetch.titles(tile, aq)
            if len(titles) < 2:
                print(f"  - {key}: not enough titles with art, skipped")
                kept += 1
                continue
            c = pick(titles, used, offset)
            used.add((c["media"], c["id"]))
            rest = [t for t in titles if t is not c]
            n = settings["background"]["panels"]
            bg_imgs = [fetch.still(t, big=i < n) for i, t in enumerate(rest)]

            d = out / rel
            d.mkdir(parents=True, exist_ok=True)
            label = settings.get("rename", {}).get(key, name)
            rule = settings.get("logos", {}).get(shelf)
            logo, logo_note = (fetch.logo(tile, rule, aq) if rule else (None, ""))
            if rule and logo is None:
                print(f"    {key}: {logo_note}, using text")
            scale = (rule or {}).get("scale", 1.0)
            cover_cfg = {**settings["cover"], "logo_mode": (rule or {}).get("mode", settings["cover"].get("logo_mode", "white"))}
            wide_still = fetch.cover_still(c)
            for shape, (w, h) in settings["cover"]["shapes"].items():
                still = wide_still
                if h > w:   # poster: use a text-free poster when TMDB has one
                    still = fetch.poster_still(c) or wide_still
                if logo is not None and rule.get("style") == "brand":
                    bgc = rule.get("colors", {}).get(name, rule.get("default_color", "#0b0d10"))
                    im = look.brand_cover(logo, bgc, (w, h), scale)
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
