"""Pack small previews of every built tile into one JSON file of data URIs.

    python -m plates.thumbs [--site site] [--out thumbs/thumbs.json]

The editing page can't load images from other sites, so it reads this file instead.
"""
import argparse
import base64
import io
import json
from pathlib import Path

from PIL import Image

SIZES = {"wide": (320, 180), "poster": (160, 240), "square": (180, 180), "background": (320, 180)}


def data_uri(path, size):
    im = Image.open(path).convert("RGB")
    im.thumbnail(size, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=58, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="site")
    ap.add_argument("--out", default="thumbs/thumbs.json")
    a = ap.parse_args()
    site = Path(a.site)
    manifest = json.loads((site / "manifest.json").read_text())
    tiles = manifest["tiles"] if isinstance(manifest, dict) else manifest
    out = {}
    for t in tiles:
        rec = {}
        for shape, rel in t.get("covers", {}).items():
            p = site / rel
            if p.exists():
                rec[shape] = data_uri(p, SIZES.get(shape, (320, 180)))
        bg = site / t["background"]
        if bg.exists():
            rec["background"] = data_uri(bg, SIZES["background"])
        out[f'{t["shelf"]}/{t["tile"]}'] = rec
    stamp = manifest.get("built") if isinstance(manifest, dict) else None
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"built": stamp, "tiles": out}, separators=(",", ":")))
    print(f"packed {len(out)} tiles, {Path(a.out).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
