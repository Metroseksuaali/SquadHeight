local UEHelpers = require("UEHelpers")
local Config = require("config")
local Maps = require("maps")
local Json = require("json")

local M = {}

local CM_PER_M = 100.0
local TRACE_COLOR = { R=0, G=0, B=0, A=0 }
local DRAW_DEBUG_NONE = 0

local State = {
    phase = "idle",
    running = false,
    cancel_requested = false,
    loop_handle = nil,
    raw_file = nil,
    log_file = nil,
    row_parts = nil,
    component_excluded_cache = {},
    actor_mesh_cache = {},
    actor_excluded_cache = {},
    actor_landscape_cache = {},
    hit_class_cache = {},
    hit_result = {},
    trace_start = { X=0, Y=0, Z=0 },
    trace_end = { X=0, Y=0, Z=0 },
    cache_component_count = 0,
    cache_actor_count = 0,
    vegetation_component_count = 0,
    next_full_gc_cell = 0,
}

local function now_ms()
    return os.clock() * 1000.0
end

local function log(msg)
    local line = "[SquadHeightRuntime] " .. tostring(msg)
    print(line .. "\n")
    if State.log_file then
        State.log_file:write(os.date("%Y-%m-%d %H:%M:%S "), line, "\n")
        State.log_file:flush()
    end
end

local function warn(msg)
    log("WARNING: " .. tostring(msg))
end

local function path_join(a, b)
    if a:sub(-1) == "\\" or a:sub(-1) == "/" then
        return a .. b
    end
    return a .. "\\" .. b
end

local function ensure_dir(path)
    -- Windows retail target. mkdir returns an error when the directory already exists;
    -- redirect output because that condition is harmless.
    local cmd = string.format('mkdir "%s" >nul 2>nul', path:gsub('"', ''))
    os.execute(cmd)
end

local function is_valid(obj)
    if obj == nil then return false end
    local ok, result = pcall(function() return obj:IsValid() end)
    return ok and result == true
end

local function unwrap_object(value)
    if value == nil then return nil end
    if is_valid(value) then return value end

    local ok, obj = pcall(function() return value:Get() end)
    if ok and is_valid(obj) then return obj end

    return nil
end

local function object_name(obj)
    if not is_valid(obj) then return "<invalid>" end
    local ok, v = pcall(function() return obj:GetFName():ToString() end)
    return ok and v or "<name-error>"
end

local function class_name(obj)
    if not is_valid(obj) then return "<invalid>" end
    local ok, v = pcall(function()
        local cls = obj:GetClass()
        if not is_valid(cls) then return "<invalid-class>" end
        return cls:GetFName():ToString()
    end)
    return ok and v or "<class-error>"
end

local function full_name(obj)
    if not is_valid(obj) then return "<invalid>" end
    local ok, v = pcall(function() return obj:GetFullName() end)
    return ok and v or object_name(obj)
end

local function contains_ci(haystack, needle)
    return string.find(string.lower(haystack or ""), string.lower(needle or ""), 1, true) ~= nil
end

local function starts_with(s, prefix)
    return s:sub(1, #prefix) == prefix
end

-- Pre-normalize PCG filter strings once at mod load. These lists are then used
-- only on component-classification cache misses, never in the per-cell cache-hit path.
local PCG_VEGETATION_ACTOR_PREFIXES = {}
for _, prefix in ipairs(Config.pcg_vegetation_actor_name_prefixes or {}) do
    PCG_VEGETATION_ACTOR_PREFIXES[#PCG_VEGETATION_ACTOR_PREFIXES + 1] =
        string.lower(prefix)
end

local PCG_VEGETATION_COMPONENT_BASES = {}
for _, base in ipairs(Config.pcg_vegetation_component_name_bases or {}) do
    PCG_VEGETATION_COMPONENT_BASES[#PCG_VEGETATION_COMPONENT_BASES + 1] =
        string.lower(base)
end

local function matches_instance_base_name(low_name, low_base)
    if low_name == low_base then return true end
    if not starts_with(low_name, low_base) then return false end
    -- PCG/ISM generated components normally append "_0", "_1", etc.
    -- Requiring the separator avoids broad substring matches.
    return low_name:sub(#low_base + 1, #low_base + 1) == "_"
end

local function is_known_pcg_vegetation(actor, component)
    if #PCG_VEGETATION_COMPONENT_BASES == 0 or not is_valid(component) then
        return false
    end

    -- Exact known vegetation component-base match. This is intentionally not a
    -- broad keyword test: e.g. rocks/props inside PCGStamp are unaffected.
    -- Generated ISM components may append _0, _1, ... to the configured base.
    local low_component_name = string.lower(object_name(component))
    local component_matches = false
    for _, base in ipairs(PCG_VEGETATION_COMPONENT_BASES) do
        if matches_instance_base_name(low_component_name, base) then
            component_matches = true
            break
        end
    end
    if not component_matches then
        return false
    end

    -- Runtime PCG can re-parent generated components under an actor whose FName
    -- is not the editor-side PCGStamp_N name. Exact vegetation component names
    -- are already selective; by default we therefore do not require actor name.
    if not Config.pcg_vegetation_require_actor_prefix then
        return true
    end

    if #PCG_VEGETATION_ACTOR_PREFIXES == 0 or not is_valid(actor) then
        return false
    end

    local low_actor_name = string.lower(object_name(actor))
    for _, prefix in ipairs(PCG_VEGETATION_ACTOR_PREFIXES) do
        if starts_with(low_actor_name, prefix) then
            return true
        end
    end

    return false
end

local function get_hit_field(hit, field)
    local ok, value = pcall(function() return hit[field] end)
    if ok then return value end
    return nil
end

local function get_hit_point(hit)
    local p = get_hit_field(hit, "ImpactPoint")
    if p and p.Z ~= nil then return p end
    p = get_hit_field(hit, "Location")
    if p and p.Z ~= nil then return p end
    return nil
end

local function get_hit_normal(hit)
    local p = get_hit_field(hit, "ImpactNormal")
    if p and p.Z ~= nil then return p end
    p = get_hit_field(hit, "Normal")
    if p and p.Z ~= nil then return p end
    return nil
end

local function get_hit_actor(hit)
    local ok, actor = pcall(function()
        return UEHelpers.GetActorFromHitResult(hit)
    end)
    if ok and is_valid(actor) then return actor end
    return nil
end

local function get_hit_component(hit)
    local value = get_hit_field(hit, "Component")
    return unwrap_object(value)
end

local function object_address(obj)
    if not is_valid(obj) then return 0 end
    local ok, addr = pcall(function() return obj:GetAddress() end)
    if ok and type(addr) == "number" then return addr end
    return 0
end

local function get_static_mesh_class()
    if State.static_mesh_class and is_valid(State.static_mesh_class) then
        return State.static_mesh_class
    end
    local cls = StaticFindObject("/Script/Engine.StaticMeshComponent")
    if is_valid(cls) then
        State.static_mesh_class = cls
        return cls
    end
    return nil
end

local function actor_has_static_mesh(actor)
    if not is_valid(actor) then return false end

    local addr = object_address(actor)
    if addr ~= 0 and State.actor_mesh_cache[addr] ~= nil then
        return State.actor_mesh_cache[addr]
    end

    local result = nil
    local sm_class = get_static_mesh_class()
    if sm_class then
        local ok, comps = pcall(function()
            return actor:GetComponentsByClass(sm_class)
        end)
        if ok and comps ~= nil then
            if type(comps) == "table" then
                result = #comps > 0
            else
                local ok_num, n = pcall(function() return comps:GetArrayNum() end)
                if ok_num then result = n > 0 end
            end
        end
    end

    -- Conservative fallback: obvious volume classes have no real surface mesh.
    if result == nil then
        local cn = string.lower(class_name(actor))
        result = not contains_ci(cn, "volume")
    end

    if addr ~= 0 then State.actor_mesh_cache[addr] = result end
    return result
end

local function component_mesh_path(component)
    if not is_valid(component) then return nil end

    local mesh = nil
    local ok, value = pcall(function() return component.StaticMesh end)
    if ok then mesh = unwrap_object(value) end

    if not mesh then
        ok, value = pcall(function() return component:GetStaticMesh() end)
        if ok then mesh = unwrap_object(value) end
    end

    if mesh then
        return full_name(mesh)
    end
    return nil
end

local function is_landscape(actor)
    if not is_valid(actor) then return false end

    local addr = object_address(actor)
    if addr ~= 0 and State.actor_landscape_cache[addr] ~= nil then
        return State.actor_landscape_cache[addr]
    end

    local result = false
    local ok, yes = pcall(function()
        return actor:IsA("/Script/Landscape.LandscapeProxy")
    end)
    if ok and yes then
        result = true
    else
        local cn = class_name(actor)
        result = (cn == "Landscape" or cn == "LandscapeProxy" or cn == "LandscapeStreamingProxy")
    end

    if addr ~= 0 then State.actor_landscape_cache[addr] = result end
    return result
end

local function actor_is_excluded(actor)
    if not is_valid(actor) then return false end

    local addr = object_address(actor)
    if addr ~= 0 and State.actor_excluded_cache[addr] ~= nil then
        return State.actor_excluded_cache[addr]
    end

    local excluded = false
    local cn = class_name(actor)

    if Config.exclude_actor_classes[cn] then
        excluded = true
    end

    if not excluded then
        for _, base_class in ipairs(Config.exclude_actor_base_classes or {}) do
            local ok, yes = pcall(function() return actor:IsA(base_class) end)
            if ok and yes then
                excluded = true
                break
            end
        end
    end

    if not excluded then
        for _, prefix in ipairs(Config.exclude_actor_class_prefixes or {}) do
            if starts_with(cn, prefix) then
                excluded = true
                break
            end
        end
    end

    if addr ~= 0 then
        if State.actor_excluded_cache[addr] == nil then
            State.cache_actor_count = State.cache_actor_count + 1
        end
        State.actor_excluded_cache[addr] = excluded
    end

    return excluded
end

local function component_is_excluded(actor, component)
    if not is_valid(component) then return false end

    local addr = object_address(component)
    if addr ~= 0 and State.component_excluded_cache[addr] ~= nil then
        return State.component_excluded_cache[addr]
    end

    local excluded = false
    local cn = class_name(component)

    if Config.exclude_component_classes[cn] then
        excluded = true
    end

    -- UE5 PCG vegetation can be emitted as ordinary ISM components owned by
    -- generic Actor instances named PCGStamp_N. Avoid a broad PCGStamp ignore:
    -- require a known vegetation component name as well.
    if not excluded and is_known_pcg_vegetation(actor, component) then
        excluded = true
        State.vegetation_component_count = State.vegetation_component_count + 1
    end

    if not excluded and Config.volume_shape_component_classes[cn] then
        if not actor_has_static_mesh(actor) then
            excluded = true
        elseif is_valid(actor) then
            local an = string.lower(class_name(actor) .. " " .. object_name(actor))
            for _, kw in ipairs(Config.volume_actor_name_keywords or {}) do
                if contains_ci(an, kw) and not actor_has_static_mesh(actor) then
                    excluded = true
                    break
                end
            end
        end
    end

    if not excluded and #Config.exclude_asset_path_keywords > 0 then
        local path = component_mesh_path(component)
        if path then
            local low = string.lower(path)
            for _, kw in ipairs(Config.exclude_asset_path_keywords) do
                if string.find(low, string.lower(kw), 1, true) then
                    excluded = true
                    break
                end
            end
        end
    end

    if addr ~= 0 then State.component_excluded_cache[addr] = excluded end
    return excluded
end

local function hit_is_excluded(actor, component, normal)
    if actor_is_excluded(actor) then
        return true
    end

    if component_is_excluded(actor, component) then
        return true
    end

    if (Config.walkable_min_normal_z or 0) > 0 and normal and normal.Z ~= nil then
        if normal.Z < Config.walkable_min_normal_z then
            return true
        end
    end

    return false
end

-- Cache the classification of a component. With walkable_min_normal_z == 0,
-- exclusion + landscape-ness do not vary from hit to hit, so millions of
-- repeated UObject/class/property lookups can be avoided.
local function classify_hit_cached(hit)
    local component = get_hit_component(hit)
    local caddr = object_address(component)

    if caddr ~= 0 and (Config.walkable_min_normal_z or 0) <= 0 then
        local cached = State.hit_class_cache[caddr]
        if cached ~= nil then
            return cached.excluded, cached.landscape
        end
    end

    -- Actor and Normal are intentionally fetched only on a cache miss.
    local actor = get_hit_actor(hit)
    local normal = nil
    if (Config.walkable_min_normal_z or 0) > 0 then
        normal = get_hit_normal(hit)
    end

    local excluded = hit_is_excluded(actor, component, normal)
    local landscape = (not excluded) and is_landscape(actor) or false

    if caddr ~= 0 and (Config.walkable_min_normal_z or 0) <= 0 then
        if State.hit_class_cache[caddr] == nil then
            State.cache_component_count = State.cache_component_count + 1
        end
        State.hit_class_cache[caddr] = {
            excluded = excluded,
            landscape = landscape,
        }
    end

    return excluded, landscape
end

local function build_ignore_list()
    if not Config.use_foliage_ignore_list then return {} end

    local result = {}
    local ok, actors = pcall(function() return FindAllOf("InstancedFoliageActor") end)
    if ok and actors then
        for _, actor in ipairs(actors) do
            if is_valid(actor) then result[#result+1] = actor end
        end
    end
    log(string.format("Up-front foliage ignore actors: %d", #result))
    return result
end

local function do_line_trace(x_cm, y_cm, z_start_cm, z_end_cm, trace_complex)
    -- Reuse the top-level Lua tables. At 1 m resolution a map can execute
    -- millions of traces; allocating three fresh tables per trace makes Lua
    -- and UE4SS's struct bridge retain/collect an enormous amount of garbage.
    local start_pos = State.trace_start
    start_pos.X, start_pos.Y, start_pos.Z = x_cm, y_cm, z_start_cm

    local end_pos = State.trace_end
    end_pos.X, end_pos.Y, end_pos.Z = x_cm, y_cm, z_end_cm

    local hit = State.hit_result

    -- IMPORTANT: UE4SS currently cannot Set FWeakObjectProperty values.
    -- FHitResult contains weak-object fields (e.g. Actor/Component/PhysMaterial).
    -- Because this table is reused, leaving the previous result in it makes the
    -- next UFunction call try to copy those old weak fields back into native
    -- FHitResult, producing:
    --   [push_weakobjectproperty] Operation::Set is not supported
    --
    -- Keep the table allocation, but clear all previous fields before using it
    -- as an OutHit parameter. LineTraceSingle repopulates it on return.
    for k in pairs(hit) do
        hit[k] = nil
    end

    local ok, was_hit = pcall(function()
        return State.ksl:LineTraceSingle(
            State.world_context,
            start_pos,
            end_pos,
            Config.trace_type_query,
            trace_complex,
            State.ignore_actors,
            DRAW_DEBUG_NONE,
            hit,
            true,
            TRACE_COLOR,
            TRACE_COLOR,
            0.0
        )
    end)

    State.trace_calls = State.trace_calls + 1

    if not ok then
        error("LineTraceSingle failed: " .. tostring(was_hit))
    end

    if not was_hit then
        return nil
    end

    return hit
end

local function trace_first_acceptable(x_cm, y_cm, z_start_cm)
    local current_start = z_start_cm
    local max_hits = Config.max_hits_per_column or 16
    local epsilon_cm = (Config.retrace_epsilon_m or 0.05) * CM_PER_M

    for _ = 1, max_hits do
        if current_start <= State.z_bottom_cm then return nil end

        local hit = do_line_trace(
            x_cm, y_cm, current_start, State.z_bottom_cm, Config.trace_complex
        )

        if not hit and Config.trace_complex and Config.fallback_simple_trace then
            hit = do_line_trace(
                x_cm, y_cm, current_start, State.z_bottom_cm, false
            )
        end

        if not hit then return nil end

        local point = get_hit_point(hit)
        if not point or point.Z == nil then
            return nil
        end

        local excluded, landscape = classify_hit_cached(hit)

        if not excluded then
            -- Keep this result pure-Lua/numeric. Do not retain transient
            -- RemoteObject wrappers from FHitResult beyond the current call.
            return {
                z_cm = point.Z,
                landscape = landscape,
            }
        end

        State.filtered_hits = State.filtered_hits + 1
        current_start = point.Z - epsilon_cm
    end

    State.max_hit_columns = State.max_hit_columns + 1
    return nil
end

local function sample_column(x_cm, y_cm)
    local first = trace_first_acceptable(x_cm, y_cm, State.z_top_cm)
    if not first then return nil, false end

    if Config.surface_mode ~= "terrain_under_overhang" then
        return first.z_cm, not first.landscape
    end

    local current = first
    local epsilon_cm = (Config.retrace_epsilon_m or 0.05) * CM_PER_M
    local min_clearance_cm = (Config.overhang_min_clearance_m or 2.5) * CM_PER_M

    for _ = 1, Config.max_hits_per_column or 16 do
        if current.landscape then break end

        local below = trace_first_acceptable(x_cm, y_cm, current.z_cm - epsilon_cm)
        if not below then break end

        local clearance = current.z_cm - below.z_cm
        if clearance >= min_clearance_cm then
            current = below
            State.overhang_drops = State.overhang_drops + 1
        else
            break
        end
    end

    return current.z_cm, not current.landscape
end

local function normalize_world_name(name)
    name = name or ""
    -- Defensive PIE stripping; retail normally has no UEDPIE_ prefix.
    name = name:gsub("^UEDPIE_%d+_", "")
    return name
end

local sorted_map_names = nil
local function get_sorted_map_names()
    if sorted_map_names then return sorted_map_names end
    sorted_map_names = {}
    for name, _ in pairs(Maps) do sorted_map_names[#sorted_map_names+1] = name end
    table.sort(sorted_map_names, function(a, b) return #a > #b end)
    return sorted_map_names
end

local function detect_map(world_name)
    if Config.manual_map_name and Config.manual_bounds then
        return Config.manual_map_name, Config.manual_bounds
    end

    local low = string.lower(normalize_world_name(world_name))
    for _, name in ipairs(get_sorted_map_names()) do
        if string.find(low, string.lower(name), 1, true) then
            return name, Maps[name]
        end
    end
    return nil, nil
end

local function map_rotation_deg(map_name)
    local map = Maps[map_name]
    if map and map.grid_rotation_deg ~= nil then return map.grid_rotation_deg end
    return Config.grid_rotation_deg or 0.0
end

local function setup_paths(map_name)
    ensure_dir(Config.output_root)
    local output_name = Config.output_name or map_name
    local map_dir = path_join(Config.output_root, output_name)
    ensure_dir(map_dir)

    State.output_dir = map_dir
    State.raw_path = path_join(map_dir, "heightmap_world_f32.raw")
    State.full_json_path = path_join(map_dir, "heightmap.json")
    State.downsample_path = Config.downsample_to
        and path_join(map_dir, string.format("heightmap_%d.json", Config.downsample_to))
        or nil
    State.meta_path = path_join(map_dir, "meta.json")
    State.log_path = path_join(map_dir, "runtime_export.log")

    State.log_file = io.open(State.log_path, "a")
end

local function cancel_loop()
    if State.loop_handle ~= nil then
        pcall(function() CancelDelayedAction(State.loop_handle) end)
        State.loop_handle = nil
    end
end

local function cleanup_files(delete_partial)
    if State.raw_file then
        pcall(function() State.raw_file:flush() end)
        pcall(function() State.raw_file:close() end)
        State.raw_file = nil
    end

    if State.log_file then
        pcall(function() State.log_file:flush() end)
        pcall(function() State.log_file:close() end)
        State.log_file = nil
    end

    if delete_partial and State.raw_path then
        pcall(function() os.remove(State.raw_path) end)
    end
end

local function abort_export(reason)
    cancel_loop()
    warn(reason)
    State.running = false
    State.phase = "idle"
    cleanup_files(true)
end

local function format_num(v, decimals)
    if v == nil or v ~= v then return "0" end
    local s = string.format("%." .. tostring(decimals or 2) .. "f", v)
    return s:gsub(",", ".")
end

local function source_offset_bytes(row0, col0, cols)
    return ((row0 * cols) + col0) * 4
end

local function read_float_at(file, row0, col0, cols)
    local ok = file:seek("set", source_offset_bytes(row0, col0, cols))
    if not ok then return nil end
    local bytes = file:read(4)
    if not bytes or #bytes ~= 4 then return nil end
    local value = string.unpack("<f", bytes)
    return value
end

local function oriented_dimensions(rows, cols, cfg)
    if cfg.transpose then return cols, rows end
    return rows, cols
end

local function oriented_to_source(orow0, ocol0, rows, cols, cfg)
    local o_rows, o_cols = oriented_dimensions(rows, cols, cfg)

    local r = orow0
    local c = ocol0

    if cfg.flip_rows then r = o_rows - 1 - r end
    if cfg.flip_cols then c = o_cols - 1 - c end

    if cfg.transpose then
        return c, r
    end
    return r, c
end

local function normalized_value(v, z_offset)
    if v == nil or v ~= v then return 0.0 end
    return v - z_offset
end

local function write_full_json(snapshot)
    if not snapshot.write_full_json then return true end

    log("Post-process: writing heightmap.json ...")
    local src, err = io.open(snapshot.raw_path, "rb")
    if not src then
        warn("Could not open raw file for JSON: " .. tostring(err))
        return false
    end

    local out, out_err = io.open(snapshot.full_json_path, "wb")
    if not out then
        src:close()
        warn("Could not open heightmap.json: " .. tostring(out_err))
        return false
    end

    local rows, cols = snapshot.rows, snapshot.cols
    local o_rows, o_cols = oriented_dimensions(rows, cols, snapshot)
    out:write("[\n")

    -- Fast path: normal row-major orientation and optional row flip.
    local fast_path = not snapshot.transpose and not snapshot.flip_cols

    for r = 0, o_rows - 1 do
        if r > 0 then out:write(",\n") end
        out:write("[")

        if fast_path then
            local sr = snapshot.flip_rows and (rows - 1 - r) or r
            src:seek("set", source_offset_bytes(sr, 0, cols))
            local row_bytes = src:read(cols * 4)
            if not row_bytes or #row_bytes ~= cols * 4 then
                src:close(); out:close()
                warn("Unexpected EOF while reading raw row " .. tostring(sr))
                return false
            end

            local pos = 1
            for c = 0, cols - 1 do
                local v
                v, pos = string.unpack("<f", row_bytes, pos)
                if c > 0 then out:write(",") end
                out:write(format_num(normalized_value(v, snapshot.z_offset_m), 2))
            end
        else
            for c = 0, o_cols - 1 do
                local sr, sc = oriented_to_source(r, c, rows, cols, snapshot)
                local v = read_float_at(src, sr, sc, cols)
                if c > 0 then out:write(",") end
                out:write(format_num(normalized_value(v, snapshot.z_offset_m), 2))
            end
        end

        out:write("]")
        if r % 100 == 0 then out:flush() end
    end

    out:write("\n]\n")
    src:close()
    out:close()
    return true
end

local function write_downsample_json(snapshot)
    local n = snapshot.downsample_to
    if not n then return true end

    log(string.format("Post-process: writing heightmap_%d.json ...", n))
    local src, err = io.open(snapshot.raw_path, "rb")
    if not src then
        warn("Could not open raw file for downsample: " .. tostring(err))
        return false
    end
    local out, out_err = io.open(snapshot.downsample_path, "wb")
    if not out then
        src:close()
        warn("Could not open downsample JSON: " .. tostring(out_err))
        return false
    end

    local rows, cols = snapshot.rows, snapshot.cols
    local o_rows, o_cols = oriented_dimensions(rows, cols, snapshot)

    out:write("[\n")
    for r = 0, n - 1 do
        if r > 0 then out:write(",\n") end
        out:write("[")
        local orow = math.min(math.floor(r * o_rows / n), o_rows - 1)

        for c = 0, n - 1 do
            local ocol = math.min(math.floor(c * o_cols / n), o_cols - 1)
            local sr, sc = oriented_to_source(orow, ocol, rows, cols, snapshot)
            local v = read_float_at(src, sr, sc, cols)
            if c > 0 then out:write(",") end
            out:write(format_num(normalized_value(v, snapshot.z_offset_m), 2))
        end
        out:write("]")
    end
    out:write("\n]\n")

    src:close()
    out:close()
    return true
end

local function write_meta(snapshot)
    local meta = {
        map = snapshot.map_name,
        world_name = snapshot.world_name,
        exported_utc = os.date("!%Y-%m-%dT%H:%M:%SZ"),
        resolution_m = snapshot.resolution_m,
        grid_cols = snapshot.cols,
        grid_rows = snapshot.rows,
        bounds_m = snapshot.bounds,
        grid_rotation_deg = snapshot.rotation_deg,
        surface_mode = snapshot.surface_mode,
        trace = {
            trace_type_query = snapshot.trace_type_query,
            trace_complex = snapshot.trace_complex,
            fallback_simple_trace = snapshot.fallback_simple_trace,
            top_m = snapshot.trace_top_m,
            bottom_m = snapshot.trace_bottom_m,
        },
        orientation = {
            transpose = snapshot.transpose,
            flip_rows = snapshot.flip_rows,
            flip_cols = snapshot.flip_cols,
        },
        z_offset_m = snapshot.z_offset_m,
        height_min_m = 0.0,
        height_max_m = snapshot.world_z_max_m - snapshot.z_offset_m,
        world_z_min_m = snapshot.world_z_min_m,
        world_z_max_m = snapshot.world_z_max_m,
        raw = {
            file = "heightmap_world_f32.raw",
            format = "little-endian IEEE754 float32",
            units = "meters absolute UE world Z",
            layout = "row-major; source row=minY->maxY, col=minX->maxX before orientation",
            no_hit = "NaN; JSON writers replace with normalized 0 (map minimum)",
        },
        stats = {
            scan_seconds = snapshot.scan_seconds,
            cells = snapshot.total_cells,
            trace_calls = snapshot.trace_calls,
            filtered_hits = snapshot.filtered_hits,
            vegetation_components_filtered = snapshot.vegetation_component_count,
            structure_cells = snapshot.structure_cells,
            no_hit_cells = snapshot.no_hit_cells,
            max_hit_columns = snapshot.max_hit_columns,
            overhang_drops = snapshot.overhang_drops,
        },
    }

    local f, err = io.open(snapshot.meta_path, "wb")
    if not f then
        warn("Could not write meta.json: " .. tostring(err))
        return false
    end
    f:write(Json.encode(meta, true), "\n")
    f:close()
    return true
end

local function finalize_async(snapshot)
    local ok, err = pcall(function()
        write_meta(snapshot)
        if snapshot.downsample_to then write_downsample_json(snapshot) end
        if snapshot.write_full_json then write_full_json(snapshot) end

        if not snapshot.keep_raw then
            os.remove(snapshot.raw_path)
        end
    end)

    if ok then
        log(string.format(
            "DONE: %s | %.2f..%.2f m world Z | %d cells | %d trace calls",
            snapshot.map_name,
            snapshot.world_z_min_m,
            snapshot.world_z_max_m,
            snapshot.total_cells,
            snapshot.trace_calls
        ))
        log("Output: " .. snapshot.output_dir)
    else
        warn("Post-process failed: " .. tostring(err))
    end

    State.running = false
    State.phase = "idle"
    if State.log_file then
        State.log_file:flush()
        State.log_file:close()
        State.log_file = nil
    end
end

local function begin_postprocess()
    cancel_loop()

    if State.raw_file then
        State.raw_file:flush()
        State.raw_file:close()
        State.raw_file = nil
    end

    State.phase = "finalizing"

    local elapsed = os.clock() - State.scan_start_clock
    local zmin = State.world_z_min_m
    local zmax = State.world_z_max_m

    if zmin == math.huge or zmax == -math.huge then
        abort_export("No geometry was hit at all. Check trace channel / map bounds.")
        return
    end

    local snapshot = {
        map_name = State.map_name,
        world_name = State.world_name,
        output_dir = State.output_dir,
        raw_path = State.raw_path,
        full_json_path = State.full_json_path,
        downsample_path = State.downsample_path,
        meta_path = State.meta_path,

        bounds = State.bounds,
        resolution_m = Config.resolution_m,
        rows = State.rows,
        cols = State.cols,
        rotation_deg = State.rotation_deg,

        z_offset_m = zmin,
        world_z_min_m = zmin,
        world_z_max_m = zmax,

        transpose = Config.transpose,
        flip_rows = Config.flip_rows,
        flip_cols = Config.flip_cols,

        surface_mode = Config.surface_mode,
        trace_type_query = Config.trace_type_query,
        trace_complex = Config.trace_complex,
        fallback_simple_trace = Config.fallback_simple_trace,
        trace_top_m = Config.trace_top_m,
        trace_bottom_m = Config.trace_bottom_m,

        total_cells = State.total_cells,
        trace_calls = State.trace_calls,
        filtered_hits = State.filtered_hits,
        vegetation_component_count = State.vegetation_component_count,
        structure_cells = State.structure_cells,
        no_hit_cells = State.no_hit_cells,
        max_hit_columns = State.max_hit_columns,
        overhang_drops = State.overhang_drops,
        scan_seconds = math.floor(elapsed * 10 + 0.5) / 10,

        keep_raw = Config.keep_raw,
        write_full_json = Config.write_full_json,
        downsample_to = Config.downsample_to,
    }

    log(string.format(
        "Scan complete in %.1fs. Starting file post-process asynchronously.",
        snapshot.scan_seconds
    ))

    -- This callback performs file I/O and pure Lua only: no Unreal object access.
    if type(ExecuteAsync) == "function" then
        ExecuteAsync(function() finalize_async(snapshot) end)
    else
        warn("ExecuteAsync is unavailable; post-process will run synchronously.")
        finalize_async(snapshot)
    end
end

local function finish_current_row()
    if State.row_parts and #State.row_parts > 0 then
        State.raw_file:write(table.concat(State.row_parts))
        State.row_parts = {}
    end
end

local function cancel_scan()
    cancel_loop()
    finish_current_row()
    log("Export cancelled by F8.")
    State.running = false
    State.phase = "idle"
    cleanup_files(true)
end

local function scan_one_cell()
    local r = State.row
    local c = State.col

    local v = r * State.step_cm - State.half_v
    local u = c * State.step_cm - State.half_u

    local x = State.center_x + u * State.cos_r - v * State.sin_r
    local y = State.center_y + u * State.sin_r + v * State.cos_r

    local z_cm, is_structure = sample_column(x, y)
    local z_m

    if z_cm == nil then
        z_m = 0 / 0 -- NaN marker in raw file
        State.no_hit_cells = State.no_hit_cells + 1
    else
        z_m = z_cm / CM_PER_M
        if z_m < State.world_z_min_m then State.world_z_min_m = z_m end
        if z_m > State.world_z_max_m then State.world_z_max_m = z_m end
        if is_structure then State.structure_cells = State.structure_cells + 1 end
    end

    State.row_parts[#State.row_parts+1] = string.pack("<f", z_m)

    State.col = State.col + 1
    State.cells_done = State.cells_done + 1

    if State.col >= State.cols then
        finish_current_row()
        State.col = 0
        State.row = State.row + 1
    end
end

local function run_gc_maintenance()
    -- Incremental work each chunk keeps short-lived FHitResult tables/userdata
    -- from outrunning Lua's automatic collector during multi-million trace runs.
    if Config.gc_step_kb and Config.gc_step_kb > 0 then
        pcall(function() collectgarbage("step", Config.gc_step_kb) end)
    end

    if Config.gc_full_every_cells and Config.gc_full_every_cells > 0
       and State.cells_done >= State.next_full_gc_cell then
        pcall(function() collectgarbage("collect") end)
        State.next_full_gc_cell = State.cells_done + Config.gc_full_every_cells
    end
end

local function process_chunk()
    if not State.running or State.phase ~= "scanning" then
        cancel_loop()
        return
    end

    if State.cancel_requested then
        cancel_scan()
        return
    end

    local t0 = now_ms()
    local cells = 0

    local ok, err = pcall(function()
        while State.row < State.rows do
            scan_one_cell()
            cells = cells + 1

            if cells >= Config.max_cells_per_tick then break end
            if (now_ms() - t0) >= Config.frame_budget_ms then break end
        end
    end)

    if not ok then
        abort_export("Scan error: " .. tostring(err))
        return
    end

    run_gc_maintenance()

    if State.row >= State.rows then
        begin_postprocess()
        return
    end

    local now = os.clock()
    if now - State.last_progress_clock >= 2.0 then
        State.last_progress_clock = now
        local pct = State.cells_done * 100.0 / State.total_cells
        local elapsed = math.max(now - State.scan_start_clock, 0.001)
        local rate = State.cells_done / elapsed
        local remaining = (State.total_cells - State.cells_done) / math.max(rate, 0.001)

        local lua_mb = collectgarbage("count") / 1024.0
        log(string.format(
            "%.1f%% | row %d/%d | %.0f cells/s | %.0fs remaining | traces=%d filtered=%d vegComp=%d | lua=%.1fMB compCache=%d actorCache=%d",
            pct, State.row + 1, State.rows, rate, remaining,
            State.trace_calls, State.filtered_hits, State.vegetation_component_count,
            lua_mb, State.cache_component_count, State.cache_actor_count
        ))
    end
end

local function schedule_scan_loop()
    if type(LoopInGameThreadAfterFrames) == "function" and EngineTickAvailable then
        State.loop_handle = LoopInGameThreadAfterFrames(1, process_chunk)
        log("Scheduler: EngineTick / once per frame")
        return true
    end

    if type(LoopInGameThreadWithDelay) == "function" then
        State.loop_handle = LoopInGameThreadWithDelay(16, process_chunk)
        log("Scheduler: delayed game-thread loop / ~16 ms")
        return true
    end

    return false
end

function M.start()
    if State.running then
        if State.phase == "scanning" then
            State.cancel_requested = true
            log("Cancellation requested; stopping on next chunk.")
        else
            log("Exporter is currently finalizing files; wait for DONE.")
        end
        return
    end

    State.phase = "initializing"
    State.running = true
    State.cancel_requested = false
    State.component_excluded_cache = {}
    State.actor_mesh_cache = {}
    State.actor_excluded_cache = {}
    State.actor_landscape_cache = {}
    State.hit_class_cache = {}
    State.hit_result = {}
    State.trace_start = { X=0, Y=0, Z=0 }
    State.trace_end = { X=0, Y=0, Z=0 }
    State.cache_component_count = 0
    State.cache_actor_count = 0
    State.vegetation_component_count = 0

    local ok, err = pcall(function()
        State.world = UEHelpers.GetWorld()
        if not is_valid(State.world) then
            error("UWorld is not available. Load into a map first.")
        end

        State.world_name = normalize_world_name(State.world:GetFName():ToString())
        State.map_name, State.bounds = detect_map(State.world_name)
        if not State.map_name or not State.bounds then
            error(
                "Unknown map/world '" .. tostring(State.world_name) ..
                "'. Add it to maps.lua or set manual_map_name/manual_bounds in config.lua."
            )
        end

        State.ksl = UEHelpers.GetKismetSystemLibrary()
        if not is_valid(State.ksl) then
            error("KismetSystemLibrary is not available.")
        end

        local pc = UEHelpers.GetPlayerController()
        if is_valid(pc) and pc.Pawn and is_valid(unwrap_object(pc.Pawn)) then
            State.world_context = unwrap_object(pc.Pawn)
        elseif is_valid(pc) then
            State.world_context = pc
        else
            State.world_context = State.world
        end

        State.ignore_actors = build_ignore_list()

        setup_paths(State.map_name)
        State.raw_file = assert(io.open(State.raw_path, "wb"))

        State.step_cm = Config.resolution_m * CM_PER_M
        local b = State.bounds
        local min_x = b.min_x * CM_PER_M
        local max_x = b.max_x * CM_PER_M
        local min_y = b.min_y * CM_PER_M
        local max_y = b.max_y * CM_PER_M

        State.cols = math.floor((max_x - min_x) / State.step_cm) + 1
        State.rows = math.floor((max_y - min_y) / State.step_cm) + 1
        State.total_cells = State.cols * State.rows

        State.center_x = (min_x + max_x) / 2.0
        State.center_y = (min_y + max_y) / 2.0
        State.half_u = (State.cols - 1) * State.step_cm / 2.0
        State.half_v = (State.rows - 1) * State.step_cm / 2.0

        State.rotation_deg = map_rotation_deg(State.map_name)
        local rot = math.rad(State.rotation_deg)
        State.cos_r = math.cos(rot)
        State.sin_r = math.sin(rot)

        State.z_top_cm = Config.trace_top_m * CM_PER_M
        State.z_bottom_cm = Config.trace_bottom_m * CM_PER_M

        State.row = 0
        State.col = 0
        State.row_parts = {}
        State.cells_done = 0
        State.next_full_gc_cell = Config.gc_full_every_cells or 200000
        State.trace_calls = 0
        State.filtered_hits = 0
        State.structure_cells = 0
        State.no_hit_cells = 0
        State.max_hit_columns = 0
        State.overhang_drops = 0
        State.world_z_min_m = math.huge
        State.world_z_max_m = -math.huge
        State.scan_start_clock = os.clock()
        State.last_progress_clock = 0

        log(string.format(
            "START map=%s world=%s grid=%dx%d @ %.2fm (%d cells)",
            State.map_name, State.world_name,
            State.cols, State.rows, Config.resolution_m, State.total_cells
        ))
        log(string.format(
            "Bounds X %.1f..%.1f m | Y %.1f..%.1f m | rotation %.2f deg",
            b.min_x, b.max_x, b.min_y, b.max_y, State.rotation_deg
        ))
        log(string.format(
            "Trace query=%d complex=%s Z %.0f..%.0f m mode=%s",
            Config.trace_type_query, tostring(Config.trace_complex),
            Config.trace_bottom_m, Config.trace_top_m, Config.surface_mode
        ))
        log(string.format(
            "Vegetation filter: %d exact component bases | require PCGStamp owner=%s",
            #PCG_VEGETATION_COMPONENT_BASES,
            tostring(Config.pcg_vegetation_require_actor_prefix == true)
        ))

        State.phase = "scanning"
        if not schedule_scan_loop() then
            error(
                "No supported game-thread loop API. Use a current UE4SS build " ..
                "with LoopInGameThreadAfterFrames/LoopInGameThreadWithDelay."
            )
        end
    end)

    if not ok then
        abort_export(tostring(err))
    end
end

function M.toggle()
    M.start()
end

function M.status()
    return {
        phase = State.phase,
        running = State.running,
        map = State.map_name,
        world = State.world_name,
        row = State.row,
        col = State.col,
        rows = State.rows,
        cols = State.cols,
        cells_done = State.cells_done,
        total_cells = State.total_cells,
    }
end

return M
