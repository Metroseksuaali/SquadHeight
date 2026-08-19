# SquadHeightRuntime (UE4SS Lua)

Runtime heightmap exporter for the currently loaded Squad map.

This is a pure Lua UE4SS implementation. It does not require the Squad SDK,
Unreal Editor Python, Blueprint mods, or a custom C++ DLL.

## Installation

Place this folder so the final layout is:

    UE4SS Mods/
      SquadHeightRuntime/
        scripts/
          main.lua
          exporter.lua
          config.lua
          maps.lua
          json.lua

Enable it in the UE4SS mod list:

    SquadHeightRuntime : 1

Use a UE4SS build that supports the Unreal Engine version used by your game.
Run only in an offline/local environment where runtime modding is permitted.

A known-good settings example is included under `Settings/UE4SS-settings.ini`.
Some UE4SS hooks can be unstable for this use case; the supplied settings keep
the hooks needed by the exporter enabled while avoiding unnecessary ones.

### UE4SS version

The exporter uses the newer game-thread delayed-action API
(`LoopInGameThreadAfterFrames` / `LoopInGameThreadWithDelay`). That API is in the
UE4SS **experimental** line and is not available in the old stable v3.0.1 Lua
API. The reference build for this package is UE4SS - v3.0.1 Beta - Git SHA #1c1a1497.

## Usage

1. Load fully into the map/layer you want to scan.
2. Press F8 once.
3. Watch `UE4SS.log` or the generated `runtime_export.log`.
4. Press F8 again while scanning to cancel.

The shipped configuration uses **1 m** cells:

    resolution_m = 1.0

For a faster first pass, set it to `4.0` (or even `8.0`/`16.0` while diagnosing
bounds or trace-channel issues), then switch back to `1.0` for final data.

## Output

By default:

    SquadHeight_output/<Map>/
      heightmap_world_f32.raw
      heightmap.json
      heightmap_500.json
      meta.json
      runtime_export.log

`heightmap_world_f32.raw` is little-endian IEEE754 float32, row-major, absolute
Unreal world Z in meters. No-hit cells are NaN.

The JSON files are normalized exactly in the SquadHeight style: the minimum
valid world Z is subtracted so the minimum becomes 0.

## PNG converter

`convert_squadheight_png.py` converts a completed runtime export into the same
PNG encodings used by the original SquadHeight editor exporter. It uses only the
Python 3 standard library; Pillow and NumPy are not required.

Run it from the folder containing the script and pass the exported map directory:

    python convert_squadheight_png.py "SquadHeight_output/Gorodok"

The input directory must contain at least:

    meta.json
    heightmap_world_f32.raw

The converter writes these files into the same map directory:

    heightmap_16bit.png   grayscale 16-bit, 0..65535
    heightmap_8bit.png    grayscale 8-bit, 0..255
    heightmap_rb.png      RGB 8-bit, height split over R/B, G=0

By default it also:

- fills thin NaN/no-hit gaps using the original exporter's 8-neighbour frontier
  pass, then fills any remaining empty regions with the map minimum;
- updates `meta.json` with the actual Z range, hole-fill statistics, and PNG
  scaling fields;
- creates or updates `SquadHeight_output/scaling.json` with per-map meters-per-
  unit values for all three PNG encodings.

Useful options:

    --compression 0..9        PNG zlib compression level (default: 9)
    --no-hole-fill            leave NaNs to encode as normalized 0
    --hole-fill-passes N      maximum frontier passes (default: 12)
    --no-meta-update          do not add/update PNG fields in meta.json
    --no-scaling-recap        do not create/update ../scaling.json

Use `python convert_squadheight_png.py --help` for the current CLI help.

The RB encoding is compatible with the original exporter:

    raw = 255 + R - B
    height_m = raw * rb_meters_per_unit

The required `rb_meters_per_unit` value is written to `meta.json` and
`scaling.json`.

## Performance

The scan runs on the Unreal game thread because the trace UFunction must run
there, but work is split across ticks. `max_cells_per_tick` and
`frame_budget_ms` control how much work is allowed per update.

At 1 m, large maps can require tens of millions of traces. The exporter remains
memory-bounded because it streams rows to the raw file rather than retaining a
Lua `rows[][]` table.

Post-processing is pure file I/O and Lua and is dispatched through UE4SS
`ExecuteAsync`; it does not access Unreal objects from the async callback.

## Map detection

`maps.lua` contains the canonical SquadCalc minimap bounds used by SquadHeight,
plus bounds for Chornivsk and HrodnaBorder. Runtime world names are matched by
canonical map-name substring, so layer worlds such as `Gorodok_*` resolve to
`Gorodok`.

For an unknown or mod map, set both values in `config.lua`:

    manual_map_name = "MyMap",
    manual_bounds = {
        min_x = -2000, max_x = 2000,
        min_y = -2000, max_y = 2000
    },

### Finding `manual_bounds` for a mod map

The bounds are the XY world coordinates of the two map-texture corner actors
referenced by the layer's `SQWorldSettings`:

    Properties.MapTextureCornerZero
    Properties.MapTextureCornerOne

In an asset dump, first find the `SQWorldSettings` for the gameplay
layer. Follow those two object references to the corresponding
`MapTextureCorner0_*` and `MapTextureCorner1_*` actors, then read their world
locations.

Unreal world coordinates are normally stored in centimeters, while
`manual_bounds` is in **meters**. Divide X and Y by 100 when the dump reports
centimeters. Z is not used. Do not rely on CornerZero being the numerical
minimum; take the minimum and maximum independently for each axis:

    min_x = min(corner0_x, corner1_x)
    max_x = max(corner0_x, corner1_x)
    min_y = min(corner0_y, corner1_y)
    max_y = max(corner0_y, corner1_y)

For example, the HrodnaBorder corners resolve to:

    manual_map_name = "HrodnaBorder",
    manual_bounds = {
        min_x = -2015, max_x = 2015,
        min_y = -2015, max_y = 2016
    },

If the automatic map-name match is wrong for a mod layer, `manual_map_name` +
`manual_bounds` takes precedence over `maps.lua`.

## Trace channel

Default:

    trace_type_query = 0

In Unreal's `ETraceTypeQuery`, this is `TraceTypeQuery1`, normally Visibility.

If retail Squad's collision profile differs, the fastest diagnostic is to
change that number and run at an 8-16 m resolution until terrain/buildings are
hit correctly.

## Foliage filtering

The Lua exporter mirrors the original Python exporter:

- excludes `InstancedFoliageActor`;
- excludes `FoliageInstancedStaticMeshComponent`;
- excludes `LandscapeGrassComponent`;
- excludes `BP_POI_Reference*`;
- rejects mesh asset paths containing `foliage` or `surroundmesh`;
- retraces downward after an excluded hit;
- conservatively rejects pure volume shape components;
- supports `topmost` and `terrain_under_overhang` surface modes.

Because retail class/asset names can differ from the SDK, inspect
`runtime_export.log` and adjust the filter lists in `config.lua` if a particular
retail build exposes different names.

## Orientation

The same knobs as the Python exporter are available:

    transpose
    flip_rows
    flip_cols
    grid_rotation_deg

The raw file is always stored in source scan order. Orientation is applied when
writing JSON and by the PNG converter.
