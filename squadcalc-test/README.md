# Local SquadCalc test harness

Run the real SquadCalc front-end locally and feed it the heightmaps from
`../output/`, so you can see and sanity-check our exported elevation on the
actual maps before delivering anything.

How it works: SquadCalc has no heightmap data in its repo — at runtime it
fetches `${API_URL}/img/maps/<map>/heightmap.json` from its API server. This
harness builds SquadCalc with `API_URL=http://localhost:8080` and runs an
nginx that serves **our** heightmaps at that path while **proxying everything
else** (map tiles, basemap, `/get`, `/post`, `/health`) to the production API
`https://squadcalc.app/api`. The app is served from the same origin, so there
is no CORS issue.

## Run

```sh
python stage_heightmaps.py      # copy output/<Map>/heightmap_500.json into heightmaps/
docker compose up -d --build    # build + start on http://localhost:8080
```

Open <http://localhost:8080>, pick a map, place a weapon and target — the
height difference uses our data. All 26 exported maps are served locally; any
map we don't have falls back to production.

Refresh after a re-export: `python stage_heightmaps.py` then reload the browser
(the heightmaps are a mounted volume, no rebuild needed). Stop: `docker
compose down`.

## Checks

```sh
python crosscheck.py            # correlate ours vs production in all 8 orientations
python diag_outlier.py chora    # height distribution / outlier inspection
python render_compare.py chora  # side-by-side PNG (left=production, right=ours)
```

`crosscheck.py` confirms orientation: if alignment is correct the identity
orientation wins. Production is terrain-only, so `r < 1` is expected — we only
care which orientation wins and that ours carries the truer relief.

## Range-fan experiment (optional)

`rangefan.patch` is an optional, local-only SquadCalc patch that adds a
terrain-aware "range fan": for a placed weapon it shades every cell in range
green/red by whether the firing arc clears the terrain and buildings, using
the full-resolution surface. It is a proof of concept for what the true-surface
heightmaps enable, not an upstream proposal.

It consumes the high-res data that `stage_heightmaps.py` already stages
alongside the 500-grid: `heightmap_hd.png` (the 16-bit PNG export) and
`hd_meta.json`. Apply it against the cloned `SquadCalc/` before building:

```sh
git -C SquadCalc apply ../rangefan.patch   # adds squadHeightmapHD.js + squadRangeFan.js
python stage_heightmaps.py                 # stages heightmap_hd.png / hd_meta.json too
docker compose up -d --build
```

Without the patch the HD files are simply served and unused; without the HD
files the fan silently falls back to the coarse 500 grid.

Everything here except the cloned `SquadCalc/`, the staged `heightmaps/`, the
built `dist/` and the generated `*_compare.png` is tracked in git.
