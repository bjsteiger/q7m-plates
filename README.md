# q7m-plates

Builds a set of cover and background images every week and hosts them on GitHub Pages.

- Cover: 1280x720, one graded film still with a centered title.
- Background: 1920x1080, blended slanted panels over a faint wall of stills.

Links: `https://bjsteiger.github.io/q7m-plates/art/<shelf>/<tile>/cover.webp` and `background.webp`. `manifest.json` lists what rendered in the latest build.

Setup: add a TMDB key as the Actions secret `TMDB_API_KEY`, set Pages to deploy from GitHub Actions, then run the `build` workflow. It also runs every Sunday and whenever `config/` or `plates/` changes. Look and tile settings are in `config/settings.json`.

Local run: `pip install -r requirements.txt` then `TMDB_API_KEY=xxxx python -m plates.build --only Genres --limit 5`.

Font: Montserrat, SIL Open Font License. This product uses the TMDB API but is not endorsed or certified by TMDB.
