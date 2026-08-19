-- SquadHeightRuntime configuration.
-- Units exposed here are meters. Unreal Engine uses centimeters internally.

return {
    -- F8 toggles export. Change only if F8 conflicts with another mod/game bind.
    hotkey = "F8",

    -- 4 m is a practical first full-map pass. Set 1.0 for final high-resolution data.
    resolution_m = 1.0,

    -- Vertical trace limits. Squad terrain/structures should comfortably fit inside this.
    trace_top_m = 5000.0,
    trace_bottom_m = -5000.0,

    -- UE ETraceTypeQuery value. 0 == TraceTypeQuery1 (normally Visibility).
    trace_type_query = 0,
    trace_complex = true,
    fallback_simple_trace = true,

    -- First acceptable surface per cell by default.
    -- "terrain_under_overhang" is also supported.
    surface_mode = "topmost",
    overhang_min_clearance_m = 2.5,

    -- Retrace after an excluded hit from slightly below that hit.
    retrace_epsilon_m = 0.05,
    max_hits_per_column = 16,

    -- 0 disables normal filtering. Example: 0.5 keeps roughly walkable/up-facing surfaces.
    walkable_min_normal_z = 0.0,

    -- Work budget per game-thread update.
    -- The exporter stops when either limit is reached.
    max_cells_per_tick = 100000,
    frame_budget_ms = 35.0,

    -- UE4SS creates many temporary Lua values while unpacking FHitResult out-params.
    -- Do small GC work every chunk and a full collection periodically so long 1 m
    -- scans do not grow the process by tens of gigabytes.
    gc_step_kb = 4096,
    gc_full_every_cells = 200000,

    -- Output is relative to the game's current working directory.
    output_root = "SquadHeight_output",

    -- Keep the raw absolute float32 raster. Strongly recommended.
    keep_raw = true,

    -- Stream normalized JSON after the scan. Does not keep the raster in Lua memory.
    write_full_json = true,

    -- Also create a SquadCalc-friendly fixed-size nearest-neighbour copy.
    -- Set nil to disable.
    downsample_to = 500,

    -- Existing SquadHeight orientation controls.
    transpose = false,
    flip_rows = false,
    flip_cols = false,

    -- Optional global rotation of the sampling grid.
    -- Per-map overrides may be added in maps.lua.
    grid_rotation_deg = 0.0,

    -- Hit filters. These mirror the Python exporter conservatively.
    exclude_actor_classes = {
        InstancedFoliageActor = true,
    },

    -- Runtime-only moving actors should not become part of a static heightmap.
    -- Vehicles in Unreal games commonly derive from Pawn as well.
    exclude_actor_base_classes = {
        "/Script/Engine.Pawn",
        "/Script/Squad.SQVehicle",
        "/Script/Squad.SQDeployable",
    },

    exclude_actor_class_prefixes = {
        "BP_POI_Reference",
    },

    exclude_component_classes = {
        FoliageInstancedStaticMeshComponent = true,
        LandscapeGrassComponent = true,
    },

    -- Only skipped when the owning actor does not appear to contain a StaticMeshComponent.
    volume_shape_component_classes = {
        BoxComponent = true,
        SphereComponent = true,
        CapsuleComponent = true,
        BrushComponent = true,
    },

    exclude_asset_path_keywords = {
        "foliage",
        "surroundmesh",
    },

    -- Extra conservative actor-name fallbacks for invisible blockers.
    -- These are only consulted for shape/brush component hits.
    volume_actor_name_keywords = {
        "volume",
        "boundary",
        "waterblock",
    },

    -- Passing a large ActorsToIgnore array through reflection for every trace is expensive.
    -- Leave false unless profiling shows it helps in your build.
    use_foliage_ignore_list = false,

    -- Unknown map fallback. Set both manual_map_name and manual_bounds to use it.
    manual_map_name = nil,
    manual_bounds = nil,
    -- Example:
    -- manual_map_name = "MyMap",
    -- manual_bounds = { min_x=-2000, max_x=2000, min_y=-2000, max_y=2000 },

    -- Optional forced output name. nil = detected canonical map name.
    output_name = nil,
}
