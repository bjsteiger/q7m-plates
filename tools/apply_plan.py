"""Turn the editing page's shelf plan into the art pipeline's config.

    python tools/apply_plan.py [REPO]

Reads config/plan/meta.json and p0.json, p1.json ... plus config/catalog.json (ready-made
card queries the plan refers to as L12, B4 ...). Rewrites config/tiles.json, settings.json
and logo-links.json, then downloads any logo links into logos/.
"""
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
plan_dir = repo / "config" / "plan"
if not (plan_dir / "meta.json").exists():
    print("no plan, nothing to apply")
    sys.exit(0)
CATALOG = json.loads((repo / "config" / "catalog.json").read_text())
meta = json.loads((plan_dir / "meta.json").read_text())


tiles, shelf_meta = [], []
if meta.get("v") in (2, 3):
    for i in range(meta["parts"]):
        for sh in json.loads((plan_dir / f"p{i}.json").read_text())["shelves"]:
            shelf_meta.append({k: sh[k] for k in ("title", "style", "refresh")})
            for c in sh["cards"]:
                c = {"r": c} if isinstance(c, str) else c
                ref = c.get("r")
                orig = CATALOG.get(ref) if ref else None
                if ref and orig is None:
                    print("CHECK unknown card reference", ref, "on", sh["title"])
                    continue
                tiles.append({"id": c.get("id") or ref or "", "shelf": sh["title"], "title": c.get("t") or orig["t"],
                              "shape": c.get("s") or sh["shape"], "art": c.get("a", {}),
                              "sources": c["src"] if "src" in c else orig["s"],
                              "src": "base" if ref and ref[0] == "B" else ("lib" if ref else None)})
else:
    sys.exit("plan format not supported, send to build again from the page")
meta["shelves"] = shelf_meta

cfg = repo / "config"
settings = json.loads((cfg / "settings.json").read_text())
# TMDB stand-ins written earlier for tiles whose own rows come from Trakt
prior = {f'{t["shelf"]}/{t["title"]}': t["sources"] for t in json.loads((cfg / "tiles.json").read_text())}
style_of = {s["title"]: s["style"] for s in meta["shelves"]}
old_logos = settings.get("logos", {})

RULES = {
    "brand": {"type": "service", "style": "brand", "scale": 1.7},
    "station": {"type": "network", "style": "station"},
    "studio": {"type": "company", "style": "studio"},
    "portrait-director": {"type": "person", "style": "portrait", "role": "Director"},
    "portrait-actor": {"type": "person", "style": "portrait", "role": "Actor"},
}

out_tiles, notes, problems = [], [], []
fonts, tile_style, logo_choice, logo_color, logo_scale, brand, queries, links = {}, {}, {}, [], {}, {}, {}, {}
for t in tiles:
    key = f'{t["shelf"]}/{t["title"]}'
    srcs = t["sources"]
    if t.get("src") == "base" and key in prior:
        usable = prior[key]          # unchanged tile: keep the art queries already tuned for it
    else:
        usable = [s for s in srcs if "traktList" not in s] or prior.get(key, [])
    a = t.get("art", {})
    st = a.get("style") or style_of.get(t["shelf"], "")
    if not a.get("artQuery") and st == "portrait-director":
        a = {**a, "artQuery": {"director": t["title"]}}
    if not a.get("artQuery") and st == "portrait-actor" and not usable:
        a = {**a, "artQuery": {"actor": t["title"]}}
    out_tiles.append({"shelf": t["shelf"], "title": t["title"], "shape": t.get("shape", "wide"), "sources": usable})
    if a.get("font") or "upper" in a or a.get("fontScale") or a.get("tracking") is not None:
        f = {}
        if a.get("font"):
            f["font_file"] = a["font"]
        if a.get("upper") is False:
            f["upper"] = False
        if a.get("fontScale"):
            f["font_scale"] = a["fontScale"]
        if a.get("tracking") is not None:
            f["tracking"] = a["tracking"]
        if a.get("font") == "MountainsofChristmas-Bold.ttf":
            f["dash"] = False
        fonts[key] = f
    if a.get("style"):
        tile_style[key] = a["style"]
    if a.get("logoSource") in ("tmdb", "none"):
        logo_choice[key] = a["logoSource"]
    if a.get("logoUrl"):
        links[key] = a["logoUrl"]
    if a.get("logoColor"):
        logo_color.append(key)
    if a.get("logoScale"):
        logo_scale[key] = a["logoScale"]
    if a.get("brandColor"):
        brand[key] = a["brandColor"]
    q = a.get("artQuery")
    if q:
        queries[key] = {"keywords": [w.strip() for w in q.split(",") if w.strip()]} if isinstance(q, str) else q
    if a.get("note"):
        notes.append(f"{key}: {a['note']}")
    for r in a.get("rowRequests", []):
        notes.append(f"ROW WANTED {key} (tile id {t['id']}): {r}")
    if not usable and not q:
        problems.append(f"{key}: only Trakt sources and no art keyword, so it gets a title-only cover")

shelf_cfg = meta["shelves"]
settings["shelves"] = [s["title"] for s in shelf_cfg]
logos, outline = {}, []
for s in shelf_cfg:
    st, title = s["style"], s["title"]
    if st in RULES:
        prev = old_logos.get(title, {})
        logos[title] = {**RULES[st], **({"scale": prev["scale"]} if prev.get("style") == RULES[st]["style"] and "scale" in prev else {})}
    elif st == "award":
        logos[title] = old_logos.get(title) or {"type": "wikimedia", "scale": 1.6, "files": {}}
    elif st == "outline":
        outline.append(title)
    elif st == "title":
        for t in tiles:
            if t["shelf"] == title:
                tile_style.setdefault(f'{title}/{t["title"]}', "title")
settings["logos"] = logos
settings["outline_shelves"] = outline

ref = settings.setdefault("refresh", {})
for tier in ("weekly", "monthly", "fixed"):
    ref[tier] = [s["title"] for s in shelf_cfg if s["refresh"] == tier]
portrait = [s["title"] for s in shelf_cfg if s["style"].startswith("portrait") or s["style"] == "award"]
names = set(settings["shelves"])
ref["fixed_monthly_bg"] = sorted(set(x for x in ref.get("fixed_monthly_bg", []) if x in names) | set(portrait))
ref["classic_art"] = sorted(set(x for x in ref.get("classic_art", []) if x in names) | set(portrait))

settings["fonts"] = fonts
settings["tile_style"] = tile_style
settings["logo_choice"] = {**{k: v for k, v in settings.get("logo_choice", {}).items() if v.startswith("tmdb:")}, **logo_choice}
settings["logo_color"] = logo_color
settings["logo_scale"] = logo_scale
settings["brand_colors"] = brand
settings["art_queries"] = queries
prefs = meta.get("prefs", {})
settings["cover"].update({k: v for k, v in prefs.get("cover", {}).items() if k in ("weight", "opacity", "title_size", "tracking", "dash")})
settings["background"].update({k: v for k, v in prefs.get("background", {}).items()
                               if k in ("panels", "panel_lean", "blend_softness", "wall_angle", "wall_brightness", "max_dark")})
rules = prefs.get("rules", {})
settings["rules"] = {"noHorror": rules.get("noHorror", True), "freshDays": rules.get("freshDays", 540)}
if rules.get("note"):
    notes.append(f"Look: {rules['note']}")

(cfg / "tiles.json").write_text(json.dumps(out_tiles, indent=1) + "\n")
(cfg / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
(cfg / "logo-links.json").write_text(json.dumps(links, indent=2) + "\n")
print(f"{len(out_tiles)} tiles on {len(settings['shelves'])} shelves; {len(links)} logo links")
for n in notes:
    print("NOTE", n)
for p in problems:
    print("CHECK", p)

# download logo links into logos/<shelf>/<tile>.<ext>
if links:
    import os
    import re

    import requests

    def slug(x):
        x = x.lower().replace("&", "and").replace("+", "plus")
        return re.sub(r"[^a-z0-9]+", "-", x).strip("-")
    for key, url in links.items():
        shelf, tile = key.split("/", 1)
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "q7m-plates/1.0 (https://github.com/bjsteiger/q7m-plates)"})
            r.raise_for_status()
        except Exception as e:
            print("CHECK logo link failed", key, e)
            continue
        ext = ".svg" if url.lower().split("?")[0].endswith(".svg") or r.content[:200].lstrip().startswith((b"<svg", b"<?xml")) else os.path.splitext(url.split("?")[0])[1].lower() or ".png"
        if ext not in (".png", ".svg", ".webp", ".jpg", ".jpeg"):
            ext = ".png"
        d = repo / "logos" / slug(shelf)
        d.mkdir(parents=True, exist_ok=True)
        for old in d.iterdir():
            if old.stem == slug(tile):
                old.unlink()
        (d / f"{slug(tile)}{ext}").write_bytes(r.content)
        print("saved logo", key)
