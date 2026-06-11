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

Everything here except the cloned `SquadCalc/`, the staged `heightmaps/`, the
built `dist/` and the generated `*_compare.png` is tracked in git.
