# q7m-plates

Builds a set of cover and background images every week and hosts them on GitHub Pages. Only the images are published. Nothing else is stored here.

## Output

Each tile gets four images:

| File | Size | Use |
|---|---|---|
| `cover-wide.webp` | 1280x720 (16:9) | wide tiles |
| `cover-poster.webp` | 800x1200 (2:3) | poster tiles, uses a text-free poster when TMDB has one |
| `cover-square.webp` | 1000x1000 (1:1) | square tiles |
| `background.webp` | 1920x1080 | backdrop behind the tile |

Links follow this pattern:

```
https://bjsteiger.github.io/q7m-plates/art/<shelf>/<tile>/cover-wide.webp
```

Shelf and tile names are lowercased, `&` becomes `and`, and spaces and symbols become dashes. Example: `art/moods-and-vibes/feel-good/`.

`https://bjsteiger.github.io/q7m-plates/manifest.json` lists every tile that rendered in the latest build, with its image paths.

The images are not committed to the repo. Each run publishes them straight to Pages, so they only show up at the links above and under Deployments.

## Look

- Cover: one graded film still, darker edges, centered title in Montserrat Bold at 85% white.
- Background: three blended slanted panels on the right over a faint tilted wall of stills, fading to 90% black on the left.

## Schedule

Runs every Sunday at 3:17am Eastern, whenever `config/` or `plates/` changes, and on demand from Actions > build > Run workflow. A full build takes about 15 to 20 minutes.

## Config

- `config/tiles.json`: every tile and the TMDB queries behind it (genre and keyword ids, list and collection ids).
- `config/settings.json`: which shelves to build, the look, cover sizes, and `art_queries`, the TMDB-only searches used for tiles that have no TMDB rows of their own (by genre, keyword, director, date range or streaming service).

Useful settings:

| Setting | What it does |
|---|---|
| `shelves` | Which shelves get images |
| `skip_tiles` | Tiles to leave out, e.g. `"Moods & Vibes/Christmas"` |
| `rename` | Print a different label on a cover |
| `cover.weight` / `cover.opacity` | Title weight and transparency |
| `cover.shapes` | Cover sizes to render |
| `background.max_dark` | How solid the black is on the left (`0.9` lets about 10% through) |
| `background.panels` / `panel_lean` / `blend_softness` | Front panel count, slant and blend |
| `rotate_weekly` | `false` keeps the same cover still every week |

## Setup

1. Add a TMDB key as the Actions secret `TMDB_API_KEY`.
2. Set Settings > Pages > Source to GitHub Actions.
3. Run the `build` workflow.

## Local run

```
pip install -r requirements.txt
TMDB_API_KEY=xxxx python -m plates.build --only Genres --limit 5
```

`--offline <folder of images>` renders with local pictures instead of TMDB.

Font: Montserrat, SIL Open Font License. This product uses the TMDB API but is not endorsed or certified by TMDB.
