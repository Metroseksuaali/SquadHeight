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
This package contains no anti-cheat bypass.

## Usage

1. Load fully into the map/layer you want to scan.
2. Press F8 once.
3. Watch UE4SS.log or the generated `runtime_export.log`.
4. Press F8 again while scanning to cancel.

Default resolution is 4 m. Change `resolution_m = 1.0` in `scripts/config.lua`
for a final 1 m scan.

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

The JSON files are normalized exactly in the SquadHeight style:
the minimum valid world Z is subtracted so the minimum becomes 0.

## Performance

The scan runs on the Unreal game thread because the trace UFunction must run there,
but work is split across ticks. `max_cells_per_tick` and `frame_budget_ms` control
how much work is allowed per update.

At 1 m, large maps can require tens of millions of traces. The exporter remains
memory-bounded because it streams rows to the raw file rather than retaining a
Lua `rows[][]` table.

Post-processing is pure file I/O and Lua and is dispatched through UE4SS
`ExecuteAsync`; it does not access Unreal objects from the async callback.

## Map detection

`maps.lua` contains the canonical SquadCalc bounds from the original SquadHeight
repository. Runtime world names are matched by canonical map-name substring, so
layer worlds such as `Gorodok_*` resolve to `Gorodok`.

For an unknown map, set in `config.lua`:

    manual_map_name = "MyMap",
    manual_bounds = {
        min_x = -2000, max_x = 2000,
        min_y = -2000, max_y = 2000
    },

## Trace channel

Default:

    trace_type_query = 0

In Unreal's `ETraceTypeQuery`, this is `TraceTypeQuery1`, normally Visibility.

If retail Squad's collision profile differs, the fastest diagnostic is to change
that number and run at an 8-16 m resolution until terrain/buildings are hit
correctly.

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
writing JSON.
