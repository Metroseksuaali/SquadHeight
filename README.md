# SquadHeight

True-surface heightmap exporter for the Squad SDK (Unreal editor), built for
[SquadCalc](https://github.com/sh4rkman/SquadCalc).

SquadCalc's current heightmaps come from the UE Landscape, which contains
terrain only. Buildings, bridges and rocks are missing, so elevation
calculations on or near structures are wrong. SquadHeight replaces that data
with a top-down ray-trace of the actual world collision: structures are
included, foliage (trees, bushes, landscape grass) is excluded. Re-exporting
after a map update is a single script run.

Verified working against the Squad SDK (UE5). A full 4 km map at 1 m
resolution takes about 6 minutes (~45k traces/s).

## Layout

```
run_batch_export.bat            headless batch export (edit paths at the top)
tools/
  export_heightmap.py           main exporter, runs inside the UE editor
  batch_export.py               loads each configured level and exports it
  maps_config.example.json      copy to maps_config.json and fill in levels
  squadcalc_bounds.json         exact SquadCalc minimap bounds for all maps
  compare_heightmaps.py         diff a new export against a legacy heightmap
  find_alignment.py             recover minimap bounds by image registration
  png16.py                      dependency-free 8/16-bit grayscale PNG writer
  _selftest.py                  offline tests (no Unreal needed)
plan_b_cpp/                     C++ commandlet skeleton (optional, see below)
```

## Output

Each export writes `output/<MapName>/`:

| file | description |
|---|---|
| `heightmap.json` | 2D JSON array of heights in meters, min normalized to 0, full resolution |
| `heightmap_500.json` | 500×500 nearest-neighbor downsample — drop-in replacement for SquadCalc's current files (written when `downsample_to: 500` is set) |
| `heightmap.png` | 16-bit grayscale render for inspection |
| `meta.json` | bounds, resolution, z-offset, PNG scaling, trace statistics |

Heights are absolute world Z minus `z_offset_m` (stored in `meta.json`), so
`world_z = value + z_offset_m`. SquadCalc only needs elevation differences,
so the offset never matters in practice.

Note: SquadCalc currently hardcodes the heightmap size to 500×500
(`squadHeightmaps.js`, `this.width = 500`). The full-resolution file becomes
useful once that line reads the size from the loaded array instead
(`this.width = this.json.length`).

## Setup

1. Enable the **Python Editor Script Plugin** in the SDK editor
   (Edit → Plugins → search "Python" → enable → restart). If the plugin
   window is locked down, add it to the `.uproject` by hand:

   ```json
   "Plugins": [ { "Name": "PythonScriptPlugin", "Enabled": true } ]
   ```

   or pass `-EnablePlugins=PythonScriptPlugin` on the command line.

2. There are no other dependencies. Everything runs on UE's embedded Python,
   except `find_alignment.py` which runs outside Unreal and needs numpy.

## Exporting a map

Open the map in the SDK editor and let it load fully (the first load of a
map compiles shaders and can take 10+ minutes; later loads are fast).
Switch the Output Log input from `Cmd` to `Python` and run:

```python
import sys; sys.path.append("C:/path/to/SquadHeight/tools")
import export_heightmap
export_heightmap.run_export(overrides={
    "resolution_m": 1.0,
    "bounds_m": {"min_x": -2464, "max_x": 1600, "min_y": -2664, "max_y": 1400},
    "trace_top_margin_m": 500.0,
    "downsample_to": 500,
})
```

**Always take `bounds_m` from [tools/squadcalc_bounds.json](tools/squadcalc_bounds.json).**
Those are the exact world-space squares SquadCalc stretches its minimaps
over (extracted from SquadCalc's own `src/data/maps.js`); using anything else
shifts every elevation lookup. The example above is Chora.

A progress dialog with a Cancel button appears; rate and ETA are logged.
Tip: run once with `"resolution_m": 8.0` first — it finishes in seconds and
lets you sanity-check the output before the real export.

## Batch / headless export

1. Copy `tools/maps_config.example.json` to `tools/maps_config.json` and fill
   in the level package paths (right-click the map asset in the Content
   Browser → Copy Reference → keep the `/Game/...` part). Bounds for every
   map are in `squadcalc_bounds.json`.
2. Edit the paths at the top of `run_batch_export.bat` (editor binary and
   `.uproject`).
3. Run it. Maps are exported one by one; failures don't stop the batch and
   everything is summarized in `output/batch_report.json`.

You can also run `py ".../tools/batch_export.py"` inside an open editor to
batch without going headless (useful if level streaming misbehaves in
commandlets).

## Configuration

Everything lives in `CONFIG` at the top of
[tools/export_heightmap.py](tools/export_heightmap.py), commented in place.
The ones that matter most:

* `resolution_m` — grid spacing. 1.0 is the sweet spot; 0.5 quadruples the time.
* `bounds_m` — see above. `null` auto-detects from Landscape bounds, which is
  fine for inspection but does NOT match the SquadCalc minimap.
* `trace_top_margin_m` — use 500 on maps with tall surround mountains,
  otherwise rays can start inside the meshes and clip their tops.
* `trace.channel` — `Visibility` works on the Squad SDK. Configurable in case
  a future SDK changes collision setups.
* `surface_mode` — `topmost` (default): bridge decks and rooftops win over
  the ground beneath them, which is what mortar fire cares about.
  `terrain_under_overhang`: drops through any mesh with at least
  `overhang_min_clearance_m` of open space under it; correct under bridges
  but also drops building roofs to interior floors, so it's not the default.
* `grid_rotation_deg` — rotates the sample grid for maps whose minimap
  capture isn't world-axis-aligned. Every map listed in
  `squadcalc_bounds.json` is axis-aligned, so this stays 0 unless
  `find_alignment.py` says otherwise for a new map.

## How foliage is excluded

1. Every `InstancedFoliageActor` (all painted foliage) goes on the trace
   ignore list, so rays pass through trees at zero cost.
2. Hits on foliage component classes and on landscape-grass instances are
   skipped and the ray continues downward.
3. Hits whose static-mesh asset path contains a configured keyword
   (default: `foliage`) are skipped — this catches hand-placed trees.

To verify on a new map: export at 8 m and check that forests look like
smooth ground in the PNG, not lumpy canopies. If trees leak through, click
one in the editor, read its mesh path and add a suitable substring to
`exclude_asset_path_keywords`.

## Validating an export

```
python tools/compare_heightmaps.py legacy_chora.json output/Chora/heightmap_500.json output/Chora/diff
```

Diffs a new export against the legacy SquadCalc file (grab one from the
SquadCalc repo/site). In `diff.png`, gray = unchanged, bright = new data is
higher. A correct export shows flat gray terrain with structures glowing —
buildings were flat ground in the old data. Note that legacy files pad the
area outside the playable zone with a constant value, which dominates naive
diff statistics; judge the playable area.

If you have a legacy heightmap but no bounds for a map (not listed in
`squadcalc_bounds.json`), `find_alignment.py` recovers the minimap square by
registering the legacy file against an axis-aligned scan — it searches
rotation, mirroring, scale and offset and prints ready-to-paste overrides:

```
python tools/find_alignment.py legacy.json output/MapName
python tools/find_alignment.py --selftest output/MapName   # verify the tool first
```

## Implementation notes

* **Engine compatibility:** written for UE5 with UE4.27 fallbacks. Reading
  trace hits differs between engine versions, so the exporter detects a
  working strategy at runtime (`HitResult read strategy` in the log).
* **Landscape seam cracks:** rays can slip through hairline gaps between
  landscape streaming-proxy collision bodies, leaving thin no-hit lines.
  These cells are filled from their nearest valid neighbors automatically.
* **Bridges:** a 2.5D heightmap stores one value per cell, so a bridge and
  the road under it can't both exist. See `surface_mode` above.
* **Performance:** ~40–50k traces/s in editor Python on a typical machine.
  4 km @ 1 m ≈ 16.5M traces ≈ 6 minutes. The editor stays responsive. If
  that's ever too slow (e.g. 0.5 m batches over all maps),
  `plan_b_cpp/` contains a commandlet skeleton of the same algorithm in C++
  with `ParallelFor`, which is orders of magnitude faster but requires
  building an editor module against the SDK.

## Troubleshooting

* `-run=pythonscript` says unknown commandlet → the Python plugin isn't
  enabled (see Setup).
* "No Landscape actors found" → the map isn't fully loaded, or pass
  `bounds_m` manually.
* Flat output, no structures → wrong trace channel; try
  `trace: {"by": "profile", "profile": "BlockAll"}`.
* Mountain tops flattened at one exact height → increase
  `trace_top_margin_m`.
* The 16-bit PNG looks black → the gray range covers the full height span;
  tall surround mountains compress the playable area into a few gray levels.
  The data is fine — inspect `heightmap.json` or stretch the contrast.
