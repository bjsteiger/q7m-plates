# Saved logos

One folder per shelf, one file per tile, named like the tile (lowercase, spaces and symbols become dashes, `&` becomes `and`, `+` becomes `plus`):

- `streaming-services/netflix.png`
- `networks/tv-land.png`
- `studios/walt-disney-studio.png`
- `awards/oscars.png`

PNG with transparency works best. SVG, WebP and JPG also work (a flat background colour is removed automatically).

A saved file is used instead of TMDB. To use TMDB for a tile even when a file is saved, set it in `config/settings.json` under `logo_choice`, e.g. `"Networks/TLC": "tmdb"`, or pick a specific TMDB logo with `"tmdb:/path.png"` (the picker page gives you these).

`config/logo-links.json` lists logos to download from other sites; run Actions > save logo links to pull them in here.
