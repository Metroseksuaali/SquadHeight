"""
export_heightmap.py - "true surface" heightmap exporter for the Squad SDK
==========================================================================

Exports a heightmap of the CURRENTLY LOADED level by ray-tracing a top-down
grid against world collision.  Unlike the stock Landscape heightmap, this
includes buildings, bridges, rocks and any other static geometry that blocks
the configured trace channel - while explicitly EXCLUDING foliage (painted
foliage / InstancedFoliageActor and landscape grass), so tree canopies do not
pollute the elevation data.

Outputs (per map, into <output_dir>/<MapName>/):
    heightmap.json   - 2D array of heights in METERS, min normalized to 0.
                       Same format as SquadCalc's existing server-side
                       heightmaps (see heightmap_chora_example.json).
    heightmap_16bit.png - 16-bit grayscale PNG for visual inspection.
    heightmap_8bit.png  - 8-bit grayscale PNG (smaller, lossier preview only).
    heightmap_rb.png  - 8-bit RGB PNG, NOT greyscale: height is split across
                       the R and B channels (G always 0) for ~511 levels of
                       precision in a file the same byte depth as the 8-bit
                       preview. Decode: raw = 255 + R - B (0..510), height_m
                       = raw * meta.json's rb_meters_per_unit.
    meta.json        - bounds, resolution, min/max, z-offset, PNG scaling,
                       stats.  Everything SquadCalc needs to interpret the
                       data is in here AND logged to the output log.

Also maintained directly in <output_dir>/ (one level up, shared across maps):
    scaling.json     - {map_name: {png16_meters_per_unit, png8_meters_per_unit,
                       rb_meters_per_unit}} recap of every map's PNG scaling,
                       merged in after each map so you don't have to open
                       every meta.json to check one value.

How to run (interactively, inside the SDK editor):
    1. Window -> Developer Tools -> Output Log, switch the input to "Python"
       (requires the Python Editor Script Plugin - see README.md if missing).
    2. py "<path-to-repo>/tools/export_heightmap.py"
   or from the Python console / another script:
       import sys; sys.path.append("<path-to-repo>/tools")
       import export_heightmap
       export_heightmap.run_export(overrides={"resolution_m": 2.0})

TIP: do a first run at resolution_m = 8.0 (seconds instead of hours) to
verify bounds/orientation/foliage filtering before committing to 1.0 m.

Engine compatibility: written for UE5, with fallbacks for UE4.27 wherever the
API differs (editor world lookup, subsystems).  All Squad-specific unknowns
(collision channel names, foliage class names) are in CONFIG at the top.
"""

import json
import math
import os
import sys
import time
from array import array

import unreal

# Make sibling modules importable regardless of how the script is invoked
# (py command, pythonscript commandlet, or import).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.append(_SCRIPT_DIR)

import png16  # noqa: E402  (local minimal PNG writer, no external deps)


# ============================================================================
# CONFIG - everything you are likely to tweak lives here.
# All distances are METERS unless the key says otherwise (UE works in cm
# internally; conversion happens inside the script).
# ============================================================================
CONFIG = {
    # ---- Grid ---------------------------------------------------------
    # Grid spacing in meters. 1.0 default; 0.5 supported (4x the traces!).
    "resolution_m": 1.0,

    # Map bounds in meters (UE world space). None = auto-detect from the
    # union of all Landscape/LandscapeStreamingProxy actor bounds.
    # Manual example:
    #   "bounds_m": {"min_x": -2032, "max_x": 2032, "min_y": -2032, "max_y": 2032},
    "bounds_m": None,

    # Extra padding (m) added around auto-detected bounds. Landscape bounds
    # usually already cover everything; bump this if structures (e.g. piers)
    # extend past the landscape edge.
    "bounds_padding_m": 0.0,

    # Rotation of the sample grid (degrees, around the bounds center).
    # Squad's minimap captures are NOT always world-axis-aligned (Chora is
    # rotated ~20 deg); to line up with the SquadCalc minimap the export grid
    # must use the same rotation. tools/find_alignment.py computes this value
    # together with bounds_m by registering the legacy heightmap against an
    # axis-aligned scan. 0.0 = normal axis-aligned grid.
    "grid_rotation_deg": 0.0,

    # Fail fast if the loaded world has no LandscapeProxy. That practically
    # always means streaming sublevels did not load (common in commandlets),
    # and tracing an empty world wastes minutes producing garbage. Set False
    # only for maps that genuinely have no landscape.
    "require_landscape": True,

    # ---- Tracing ------------------------------------------------------
    # Squad's collision setup is custom, so this is deliberately flexible.
    #   "by": "channel"  -> SystemLibrary.line_trace_single (trace channel)
    #   "by": "profile"  -> SystemLibrary.line_trace_single_by_profile
    # Channel names: "Visibility" (=TraceTypeQuery1), "Camera" (=Query2),
    # or raw "TraceTypeQueryN" for custom game channels
    # (ECC_GameTraceChannel1 == TraceTypeQuery3, GameTraceChannel2 == 4, ...).
    "trace": {
        "by": "channel",
        "channel": "Visibility",
        "profile": "BlockAll",
        # Complex (per-triangle) collision gives accurate roof/bridge shapes
        # where meshes have it; falls back to simple collision otherwise.
        "trace_complex": True,
    },

    # Vertical trace range, relative to the detected geometry bounds:
    # rays start this many meters ABOVE the highest bound and end this many
    # meters BELOW the lowest bound. Generous margins are cheap.
    "trace_top_margin_m": 200.0,
    "trace_bottom_margin_m": 100.0,

    # ---- Foliage / hit filtering ---------------------------------------
    # Actors of these classes are put on the trace ignore list up-front
    # (fast path - the trace never even sees them). InstancedFoliageActor
    # covers everything painted with the foliage tool.
    "exclude_actor_classes": ["InstancedFoliageActor"],

    # Per-hit exclusion by actor class-name prefix. BP_POI_Reference covers
    # the floating designer marker blobs that hang ~40 m above each point of
    # interest on some maps (they have meshes AND collision).
    "exclude_actor_class_prefixes": ["BP_POI_Reference"],

    # Hits on these component classes are skipped and the ray continues
    # downward (slow path, for foliage that is NOT on an IFA).
    "exclude_component_classes": [
        "FoliageInstancedStaticMeshComponent",
        "LandscapeGrassComponent",
    ],

    # Shape/brush components are skipped ONLY when their owning actor has no
    # static meshes. Pure volume actors (map boundary blockers, water
    # blockers - one BoxComponent covered 12% of Al Basrah) are not real
    # surfaces, but old-style building blueprints (Chora's afg houses) use a
    # box as their collision shell - excluding those would erase the
    # buildings from the heightmap, so a box on a mesh-bearing actor counts
    # as a surface.
    "volume_shape_component_classes": [
        "BoxComponent",
        "SphereComponent",
        "CapsuleComponent",
        "BrushComponent",
    ],

    # Case-insensitive substrings matched against the hit static mesh's full
    # asset path (e.g. "/Game/Environment/Foliage/Trees/Oak_01.Oak_01").
    # Keep this conservative: "foliage" catches the usual folder layout.
    # Add e.g. "tree_", "bush_", "water" after inspecting Squad's content
    # naming - see README "Verifying foliage exclusion".
    "exclude_asset_path_keywords": ["foliage", "surroundmesh"],

    # ---- Surface selection ----------------------------------------------
    # A 2.5D heightmap can store only ONE value per cell. Two behaviours:
    #   "topmost"               - first (highest) non-foliage hit. Bridges and
    #                             roofs win over the ground beneath. DEFAULT.
    #   "terrain_under_overhang"- starting from the top hit, keep dropping to
    #                             the next surface below while the current one
    #                             is a non-landscape mesh with at least
    #                             overhang_min_clearance_m of open space under
    #                             it (i.e. you could walk/drive under it).
    #                             Bridges yield the road below; building roofs
    #                             with interior floors ALSO drop to the floors,
    #                             which is why "topmost" is the default.
    # Tradeoff details in README.md.
    "surface_mode": "topmost",
    "overhang_min_clearance_m": 2.5,

    # Reject hits whose upward normal Z is below this (1.0 = flat, 0.0 = wall).
    # 0.0 disables the filter. For mortar impact prediction you generally WANT
    # steep roofs included, so it is off by default; set ~0.5 to keep only
    # surfaces a soldier could stand on.
    "walkable_min_normal_z": 0.0,

    # When stepping the ray past an excluded hit, restart this far below the
    # hit point (m). Must be smaller than the thinnest real floor/deck you
    # care about, but big enough to escape the surface just hit.
    "retrace_epsilon_m": 0.05,

    # Safety cap on hits processed per grid column (foliage stacks, multi-
    # storey buildings). Columns denser than this keep the best hit found.
    "max_hits_per_column": 16,

    # ---- Output -----------------------------------------------------------
    # Subtract the minimum height so the lowest point is 0.00, matching the
    # existing SquadCalc heightmaps (heightmap_chora_example.json has min 0).
    # The subtracted offset is logged and stored in meta.json.
    "normalize_min_to_zero": True,

    # Heights are rounded to 2 decimals (cm precision) like the example file.
    "json_decimals": 2,

    # zlib compression level (0-9) for both heightmap.png (16-bit) and
    # heightmap_8bit.png (8-bit, written alongside it for smaller previews).
    # 9 = smallest file, slowest to write.
    "png_compress_level": 9,

    # Orientation fixes, applied to BOTH json and png so they always agree.
    # Defaults: row index = world Y (min->max), col index = world X (min->max).
    # If the PNG looks rotated/mirrored vs the SquadCalc minimap, flip these -
    # see README "Validating against the old heightmap".
    "transpose": False,
    "flip_rows": False,
    "flip_cols": False,

    # Optionally ALSO write a nearest-neighbour downsampled copy at exactly
    # N x N (e.g. 500 to mirror the legacy Chora file) as heightmap_<N>.json.
    # None disables.
    "downsample_to": None,

    # ---- Progress / responsiveness ------------------------------------
    # Rows per progress-dialog tick. The ScopedSlowTask dialog keeps the
    # editor pumping messages (and gives you a Cancel button); log lines with
    # rate + ETA are emitted every `log_every_rows`.
    "log_every_rows": 25,
}

# UE works in centimeters.
_M_TO_CM = 100.0
_NO_DATA = float("nan")


# ============================================================================
# Small engine-compat helpers (UE5 first, UE4.27 fallback)
# ============================================================================

def get_editor_world():
    """Return the editor world, trying the UE5 subsystem first."""
    # UE5: UnrealEditorSubsystem
    try:
        subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        if subsystem:
            world = subsystem.get_editor_world()
            if world:
                return world
    except (AttributeError, Exception):
        pass
    # UE4.27 (deprecated in UE5 but still present)
    try:
        return unreal.EditorLevelLibrary.get_editor_world()
    except AttributeError:
        pass
    raise RuntimeError(
        "Could not obtain the editor world. Are you running inside the "
        "Unreal editor with the Python Editor Script Plugin enabled?"
    )


def _get_all_actors_of_class(world, actor_class):
    """GameplayStatics works identically in UE4.27/UE5 and in commandlets."""
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, actor_class))


def _resolve_trace_channel(name):
    """Map a friendly channel name to unreal.TraceTypeQuery."""
    n = str(name).strip().lower()
    if n == "visibility":
        return unreal.TraceTypeQuery.TRACE_TYPE_QUERY1
    if n == "camera":
        return unreal.TraceTypeQuery.TRACE_TYPE_QUERY2
    if n.startswith("tracetypequery"):
        idx = int(n[len("tracetypequery"):])
        return getattr(unreal.TraceTypeQuery, "TRACE_TYPE_QUERY%d" % idx)
    raise ValueError(
        "Unknown trace channel %r. Use 'Visibility', 'Camera' or "
        "'TraceTypeQueryN' (custom ECC_GameTraceChannel1 == TraceTypeQuery3)."
        % name
    )


# How to read fields out of an FHitResult differs between engine versions:
#   "break" - unreal.GameplayStatics.break_hit_result (UE4.27 / early UE5)
#   "props" - direct struct property access (newer UE5 builds, where
#             break_hit_result was removed - e.g. the Squad SDK UE5 build)
#   "tuple" - HitResult.to_tuple() + scan fields by type (last resort)
# Detected once on the first hit, then cached (this runs millions of times).
_HIT_MODE = None


def _hit_prop(hit, *names):
    """Return the first readable struct property from `names`, else None."""
    for n in names:
        try:
            return hit.get_editor_property(n)
        except Exception:
            continue
    return None


def _parse_hit(hit):
    """
    Extract (location, impact_normal, actor, component) from a HitResult,
    coping with the API differences between engine versions.
    """
    global _HIT_MODE
    if _HIT_MODE is None:
        if hasattr(unreal.GameplayStatics, "break_hit_result"):
            _HIT_MODE = "break"
        elif _hit_prop(hit, "location", "impact_point") is not None:
            _HIT_MODE = "props"
        else:
            _HIT_MODE = "tuple"
        unreal.log("[SquadHeight] HitResult read strategy: %s" % _HIT_MODE)

    if _HIT_MODE == "props":
        location = _hit_prop(hit, "location", "impact_point")
        normal = _hit_prop(hit, "impact_normal", "normal")
        component = _hit_prop(hit, "component", "hit_component")
        actor = None
        if component is not None:
            try:
                actor = component.get_owner()
            except Exception:
                actor = None
        if actor is None:
            # Newer UE5 wraps the actor in an FActorInstanceHandle.
            handle = _hit_prop(hit, "hit_object_handle")
            if handle is not None:
                try:
                    actor = handle.get_editor_property("actor")
                except Exception:
                    actor = None
        return location, normal, actor, component

    if _HIT_MODE == "break":
        fields = unreal.GameplayStatics.break_hit_result(hit)
    else:  # "tuple"
        fields = hit.to_tuple()

    # Both layouts keep the vectors in a stable order: location, impact_point,
    # normal, impact_normal [, trace_start, trace_end]. The first Actor (older
    # builds) is the hit actor; otherwise derive it from the component owner.
    vectors = []
    actor = None
    component = None
    for f in fields:
        if isinstance(f, unreal.Vector):
            vectors.append(f)
        elif actor is None and isinstance(f, unreal.Actor):
            actor = f
        elif component is None and isinstance(f, unreal.ActorComponent):
            component = f
    location = vectors[0] if vectors else None
    impact_normal = vectors[3] if len(vectors) > 3 else None
    if actor is None and component is not None:
        try:
            actor = component.get_owner()
        except Exception:
            pass
    return location, impact_normal, actor, component


# ============================================================================
# Bounds
# ============================================================================

def _landscape_class():
    """LandscapeProxy covers Landscape AND LandscapeStreamingProxy."""
    cls = getattr(unreal, "LandscapeProxy", None)
    if cls is None:
        cls = getattr(unreal, "Landscape", None)
    if cls is None:
        raise RuntimeError("Landscape classes not exposed to Python in this build.")
    return cls


def compute_bounds(world, cfg):
    """
    Return (min_x, max_x, min_y, max_y, min_z, max_z) in CM.

    XY comes from cfg["bounds_m"] if set, otherwise from the union of all
    LandscapeProxy actor bounds. Z is ALWAYS auto-detected from the landscape
    (plus margins later) since the user-facing bounds are 2D.
    """
    proxies = _get_all_actors_of_class(world, _landscape_class())
    if not proxies and cfg.get("require_landscape", True):
        raise RuntimeError(
            "No LandscapeProxy in the loaded world. If this map keeps its "
            "content in streaming sublevels, they did not load (the batch "
            "runner forces them; check its log). For maps that truly have "
            "no landscape, set CONFIG['require_landscape'] = False."
        )
    if not proxies and cfg["bounds_m"] is None:
        raise RuntimeError(
            "No Landscape actors found in this level and no manual "
            "CONFIG['bounds_m'] provided. Set bounds_m (meters) and re-run."
        )

    lmin = [float("inf")] * 3
    lmax = [float("-inf")] * 3
    for actor in proxies:
        origin, extent = actor.get_actor_bounds(False)
        for i, axis in enumerate(("x", "y", "z")):
            o, e = getattr(origin, axis), getattr(extent, axis)
            lmin[i] = min(lmin[i], o - e)
            lmax[i] = max(lmax[i], o + e)

    if cfg["bounds_m"] is not None:
        b = cfg["bounds_m"]
        min_x, max_x = b["min_x"] * _M_TO_CM, b["max_x"] * _M_TO_CM
        min_y, max_y = b["min_y"] * _M_TO_CM, b["max_y"] * _M_TO_CM
        # If there is no landscape at all, fall back to a generous Z range.
        min_z = lmin[2] if proxies else -100000.0
        max_z = lmax[2] if proxies else 100000.0
    else:
        pad = cfg["bounds_padding_m"] * _M_TO_CM
        min_x, max_x = lmin[0] - pad, lmax[0] + pad
        min_y, max_y = lmin[1] - pad, lmax[1] + pad
        min_z, max_z = lmin[2], lmax[2]

    return min_x, max_x, min_y, max_y, min_z, max_z


# ============================================================================
# Hit classification
# ============================================================================

# Async asset compilation leaves static meshes WITHOUT COLLISION until the
# finalization runs on the game thread - and in a commandlet nothing pumps
# it, so whole building sets silently vanish from the scan (Chora's houses,
# Tallil's hangars; which meshes make it varies run to run). These console
# commands force the pending compilations to finish; which name exists varies
# by engine build, unknown ones are ignored, and running the whole battery
# costs ~half a second. Verified on Squad SDK 5.7: canary traces over Chora's
# mosque/police station went from landscape-only to correct roof heights.
_FINISH_COMPILATION_CMDS = (
    "StaticMesh.FinishCompilation",
    "FinishAllAssetCompilation",
    "Editor.AsyncAssetCompilationFinishAll",
)


def _settle_async_collision(world, tracer, n_cols, n_rows, step,
                            half_u, half_v, center_x, center_y,
                            cos_r, sin_r):
    """
    Force async asset compilation to finish, then verify with a sparse
    pre-scan (~64x64 columns) repeated until the structure-hit count stops
    changing between rounds. Returns (rounds_used, final_sparse_count).
    """
    stride_r = max(1, n_rows // 64)
    stride_c = max(1, n_cols // 64)

    def sparse_structure_count():
        n = 0
        for r in range(0, n_rows, stride_r):
            v = r * step - half_v
            for c in range(0, n_cols, stride_c):
                u = c * step - half_u
                x = center_x + u * cos_r - v * sin_r
                y = center_y + u * sin_r + v * cos_r
                z, _landscape_z, is_structure = tracer.sample_column(x, y)
                if z is not None and is_structure:
                    n += 1
        return n

    prev = -1
    n = 0
    rounds = 0
    for rounds in range(1, 9):
        for cmd in _FINISH_COMPILATION_CMDS:
            try:
                unreal.SystemLibrary.execute_console_command(world, cmd)
            except Exception:
                pass
        n = sparse_structure_count()
        unreal.log("[SquadHeight] collision settle round %d: %d structure "
                   "hits in sparse pre-scan" % (rounds, n))
        if n == prev:
            break
        prev = n
        time.sleep(0.5)
    else:
        unreal.log_warning("[SquadHeight] structure hits did NOT stabilize "
                           "in 8 rounds - export may be incomplete!")
    # The pre-scan ran through the tracer - reset its diagnostics so
    # meta.json reflects the real scan only.
    tracer.mesh_counts = {}
    tracer.foliage_skips = 0
    return rounds, n


def _build_ignore_list(world, cfg):
    """
    Collect actors to exclude from tracing entirely. This is the FAST path
    for foliage: traces never report ignored actors, so no re-trace needed.
    """
    ignore = []
    # Painted foliage lives on InstancedFoliageActor (one per streaming level).
    ifa_class = getattr(unreal, "InstancedFoliageActor", None)
    if ifa_class is not None:
        ignore.extend(_get_all_actors_of_class(world, ifa_class))
    # Plus any class names from config (matched against the exact class name,
    # so subclassed Squad foliage actors can be added here once identified).
    wanted = set(cfg["exclude_actor_classes"]) - {"InstancedFoliageActor"}
    if wanted:
        for actor in _get_all_actors_of_class(world, unreal.Actor):
            if actor.get_class().get_name() in wanted:
                ignore.append(actor)
    return ignore


def _is_landscape_ground(actor, component):
    """
    True if the hit is the landscape HEIGHTFIELD itself.
    Landscape grass renders/collides via InstancedStaticMesh components owned
    by the LandscapeProxy - those are vegetation, not ground, so they don't
    count (and are excluded separately below).
    """
    if actor is None or not isinstance(actor, _landscape_class()):
        return False
    if component is not None and isinstance(
        component, unreal.InstancedStaticMeshComponent
    ):
        return False
    return True


# Per-run cache: does this actor own any StaticMeshComponent? Used to tell
# real geometry (building blueprint with a box collision shell) apart from
# pure invisible volumes (boundary/water blockers). Reset by run_export.
_ACTOR_HAS_MESH = {}


def _actor_has_mesh(actor):
    key = id(actor)
    cached = _ACTOR_HAS_MESH.get(key)
    if cached is None:
        try:
            cached = len(actor.get_components_by_class(
                unreal.StaticMeshComponent)) > 0
        except Exception:
            cached = True  # when in doubt, keep the hit
        _ACTOR_HAS_MESH[key] = cached
    return cached


def _is_excluded_hit(actor, component, cfg):
    """Per-hit foliage/volume filtering - see CONFIG keys for rationale."""
    if actor is not None:
        ifa_class = getattr(unreal, "InstancedFoliageActor", None)
        if ifa_class is not None and isinstance(actor, ifa_class):
            return True
        actor_class = actor.get_class().get_name()
        if actor_class in cfg["exclude_actor_classes"]:
            return True
        if any(actor_class.startswith(p)
               for p in cfg.get("exclude_actor_class_prefixes", ())):
            return True

    if component is not None:
        comp_class = component.get_class().get_name()
        if comp_class in cfg["exclude_component_classes"]:
            return True
        # Invisible collision volumes: skip unless the shape belongs to an
        # actor that also carries real meshes (a building's collision shell).
        if comp_class in cfg.get("volume_shape_component_classes", ()):
            if actor is None or not _actor_has_mesh(actor):
                return True
        # Landscape grass: ISM/HISM component owned by a LandscapeProxy.
        if (
            actor is not None
            and isinstance(actor, _landscape_class())
            and isinstance(component, unreal.InstancedStaticMeshComponent)
        ):
            return True
        # Asset-path keyword match (covers hand-placed StaticMeshActor trees).
        if cfg["exclude_asset_path_keywords"] and isinstance(
            component, unreal.StaticMeshComponent
        ):
            mesh = component.static_mesh
            if mesh is not None:
                path = mesh.get_path_name().lower()
                for kw in cfg["exclude_asset_path_keywords"]:
                    if kw.lower() in path:
                        return True
    return False


# ============================================================================
# Tracing
# ============================================================================

class _Tracer(object):
    """Wraps the per-column multi-step trace so the hot loop stays tight."""

    def __init__(self, world, cfg, ignore_actors, z_top_cm, z_bottom_cm):
        self.world = world
        self.cfg = cfg
        self.ignore = ignore_actors
        self.z_top = z_top_cm
        self.z_bottom = z_bottom_cm
        self.trace_complex = cfg["trace"]["trace_complex"]
        self.by_profile = cfg["trace"]["by"] == "profile"
        if self.by_profile:
            self.profile = cfg["trace"]["profile"]
        else:
            self.channel = _resolve_trace_channel(cfg["trace"]["channel"])
        self.epsilon = cfg["retrace_epsilon_m"] * _M_TO_CM
        self.max_hits = cfg["max_hits_per_column"]
        self.clearance = cfg["overhang_min_clearance_m"] * _M_TO_CM
        self.min_normal_z = cfg["walkable_min_normal_z"]
        self.topmost_mode = cfg["surface_mode"] == "topmost"
        self.debug_none = unreal.DrawDebugTrace.NONE
        # stats
        self.foliage_skips = 0
        self.overhang_drops = 0
        # Histogram of which mesh assets the chosen structure hits landed on.
        # This is how you find foliage that slips through the filters: leaked
        # tree canopies show up at the top of this list in meta.json, and
        # their asset paths tell you what to add to
        # exclude_asset_path_keywords. Costs almost nothing (cached lookups).
        self.mesh_counts = {}

    def _count_hit_asset(self, component):
        # No caching by id(component): the Python wrapper objects are
        # transient and their ids get recycled, which silently attributed
        # most hits to whatever component first claimed the id.
        path = "<unknown>"
        try:
            if isinstance(component, unreal.StaticMeshComponent):
                mesh = component.static_mesh
                if mesh is not None:
                    path = mesh.get_path_name()
            elif component is not None:
                path = "<%s>" % component.get_class().get_name()
        except Exception:
            pass
        self.mesh_counts[path] = self.mesh_counts.get(path, 0) + 1

    def _trace_once(self, x, y, z_start):
        start = unreal.Vector(x, y, z_start)
        end = unreal.Vector(x, y, self.z_bottom)
        if self.by_profile:
            return unreal.SystemLibrary.line_trace_single_by_profile(
                self.world, start, end, self.profile, self.trace_complex,
                self.ignore, self.debug_none, True,
            )
        return unreal.SystemLibrary.line_trace_single(
            self.world, start, end, self.channel, self.trace_complex,
            self.ignore, self.debug_none, True,
        )

    def sample_column(self, x, y):
        """
        Walk the ray down through the column, skipping excluded (foliage)
        hits, and return (chosen_z_cm or None, landscape_z_cm or None,
        chose_structure: bool).

        Surfaces are gathered top->down. Downward line traces only hit
        upward-facing geometry, so each accepted hit is a stackable "floor"
        (bridge deck, roof, terrain) - undersides are never reported.
        """
        surfaces = []        # [(z_cm, is_landscape)]
        landscape_z = None
        z_start = self.z_top

        for _ in range(self.max_hits):
            hit = self._trace_once(x, y, z_start)
            if not hit:
                break
            location, normal, actor, component = _parse_hit(hit)
            if location is None:
                break
            z = location.z

            if _is_excluded_hit(actor, component, self.cfg):
                self.foliage_skips += 1
            else:
                is_ls = _is_landscape_ground(actor, component)
                walkable = (
                    self.min_normal_z <= 0.0
                    or normal is None
                    or normal.z >= self.min_normal_z
                )
                if walkable:
                    surfaces.append((z, is_ls))
                    if is_ls:
                        landscape_z = z
                        break  # nothing of interest below the heightfield
                    if len(surfaces) == 1:
                        self._count_hit_asset(component)
                    if self.topmost_mode:
                        break  # first valid hit wins; skip the rest
                elif is_ls:
                    # Steep landscape rejected by the walkable filter still
                    # serves as the fallback height.
                    landscape_z = z
                    break

            # Step past this hit and keep going down.
            z_start = z - self.epsilon
            if z_start <= self.z_bottom:
                break

        if not surfaces:
            return None, landscape_z, False

        if self.topmost_mode:
            z, is_ls = surfaces[0]
            return z, landscape_z, not is_ls

        # terrain_under_overhang: drop below meshes that have enough open
        # space beneath them to be an overhang (bridge, archway, ...).
        idx = 0
        while (
            not surfaces[idx][1]                     # current is a mesh
            and idx + 1 < len(surfaces)              # something below exists
            and surfaces[idx][0] - surfaces[idx + 1][0] >= self.clearance
        ):
            idx += 1
            self.overhang_drops += 1
        z, is_ls = surfaces[idx]
        return z, landscape_z, not is_ls


# ============================================================================
# Output helpers
# ============================================================================

_PROGRESS_BAR_WIDTH = 24
_console_file = None
_console_failed = False


def _console_write(text):
    """
    Write straight to the Windows console (CONOUT$), bypassing sys.stdout.
    UnrealEditor-Cmd.exe mirrors its entire log (thousands of asset-load
    lines) to whatever stdout is, with no flag to filter that down to just
    errors - so run_batch_export.bat redirects the editor's stdout/stderr to
    a dated file under logs/ and relies on this function for the cmd
    window's live status instead. CONOUT$ is the literal console device,
    independent of stdout redirection. Falls back to sys.stdout if CONOUT$
    can't be opened (e.g. not on Windows, or no console attached - the
    interactive in-editor run uses the GUI ScopedSlowTask dialog instead and
    never calls this).
    """
    global _console_file, _console_failed
    if not _console_failed:
        if _console_file is None:
            try:
                _console_file = open("CONOUT$", "w")
            except OSError:
                _console_failed = True
        if _console_file is not None:
            try:
                _console_file.write(text)
                _console_file.flush()
                return
            except OSError:
                _console_failed = True
    sys.stdout.write(text)
    sys.stdout.flush()


_log_failed = False


def _log_write(text):
    """
    Append straight to the run's dated log file, NOT shown live in the cmd
    window. Used for detail worth keeping in the log but that would clutter
    or fight with the live progress bar if it also went to the console (e.g.
    periodic row-scan checkpoints, which the bar already covers visually).

    Writes via os.write(1, ...) - the raw stdout file descriptor - rather
    than through sys.stdout or a fresh open() of the log path. Both of those
    were tried first and both silently produced an empty log: UE's embedded
    Python's sys.stdout is not reliably wired to the OS-level stream the
    .bat's redirect (>>"%RUNLOG%" 2>&1) captures, and separately open()-ing
    that same path collides with the handle the .bat's redirect already
    holds open on it (Windows sharing violation -> OSError -> silently
    disabled). fd 1 IS that already-open handle, inherited from the parent
    process, so reusing it sidesteps both problems. No-ops if fd 1 isn't
    writable (e.g. an interactive editor session with no redirect at all).
    """
    global _log_failed
    if _log_failed:
        return
    try:
        os.write(1, text.encode("utf-8", "replace"))
    except OSError:
        _log_failed = True


def _status(text):
    """One-line milestone: shown live in the console AND persisted to the
    run's log file - use for things worth seeing both live and later."""
    _console_write(text)
    _log_write(text)


def _print_progress_bar(row, n_rows, rate, eta_s):
    """
    Redraw one line in place via a bare carriage return - deliberately
    bypasses unreal.log(), which always prepends a timestamp and a newline
    (no way to suppress that), so it can never update in place. Fixed-width
    fields so every redraw is the same length and never leaves stray
    characters from a longer previous line.
    """
    frac = row / n_rows if n_rows else 1.0
    filled = int(frac * _PROGRESS_BAR_WIDTH)
    bar = "#" * filled + "-" * (_PROGRESS_BAR_WIDTH - filled)
    _console_write(
        "\r[%s] row %6d/%-6d %5.1f%%  %7.0f tr/s  ETA %3dm%02ds"
        % (bar, row, n_rows, frac * 100, rate, int(eta_s // 60), int(eta_s % 60))
    )


def _fmt_height(v, decimals):
    """Format like the legacy files: '16.32', '7.5', '0' (no trailing zeros)."""
    s = "%.*f" % (decimals, v)
    s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def _apply_orientation(rows, cfg):
    """Transpose/flip the grid in-place-ish; applied to ALL outputs equally."""
    if cfg["transpose"]:
        n_rows, n_cols = len(rows), len(rows[0])
        rows = [
            array("f", (rows[r][c] for r in range(n_rows)))
            for c in range(n_cols)
        ]
    if cfg["flip_rows"]:
        rows.reverse()
    if cfg["flip_cols"]:
        for row in rows:
            row.reverse()
    return rows


def _write_json(path, rows, decimals):
    """Stream the 2D array to disk (a 4k x 4k grid is ~100+ MB of JSON)."""
    with open(path, "w") as f:
        f.write("[")
        last = len(rows) - 1
        for i, row in enumerate(rows):
            f.write("[")
            f.write(",".join(_fmt_height(v, decimals) for v in row))
            f.write("]")
            if i != last:
                f.write(",\n")
        f.write("]")


def _update_scaling_recap(output_dir, map_name, png16_meters_per_unit,
                           png8_meters_per_unit, rb_meters_per_unit):
    """
    Merge this map's PNG scaling into <output_dir>/scaling.json, a one-file
    recap of every map's scaling so it doesn't take opening 25 meta.json
    files to check one. Read-merge-write because batch_export.py's relaunch
    loop (gotcha 5 in CLAUDE.md) runs one map per editor process - this file
    must accumulate correctly across separate processes, unlike
    batch_report.json which only the final completing run writes.
    """
    path = os.path.join(output_dir, "scaling.json")
    recap = {}
    if os.path.isfile(path):
        with open(path, "r") as f:
            recap = json.load(f)
    recap[map_name] = {
        "png16_meters_per_unit": png16_meters_per_unit,
        "png8_meters_per_unit": png8_meters_per_unit,
        "rb_meters_per_unit": rb_meters_per_unit,
    }
    with open(path, "w") as f:
        json.dump(recap, f, indent=2, sort_keys=True)


def _write_png(path, rows, h_min, h_max, bit_depth=16, compress_level=9):
    """Grayscale PNG: 0 = h_min, max_val = h_max. Returns the scale used."""
    max_val = (1 << bit_depth) - 1
    span = h_max - h_min
    scale = max_val / span if span > 1e-9 else 0.0

    def row_iter():
        for row in rows:
            yield [int((v - h_min) * scale + 0.5) for v in row]

    png16.write_gray_png(path, len(rows[0]), len(rows), row_iter(),
                          bit_depth=bit_depth, compress_level=compress_level)
    return scale


# Max raw value encodable across R+B (255 + R - B, R/B each in [0, 255]).
_RB_MAX_RAW = 510


def _write_rb_png(path, rows, h_min, h_max, compress_level=9):
    """
    Non-greyscale 8-bit RGB PNG: height is split across R and B for ~511
    levels (vs 256 for a single 8-bit grey channel) at the same byte depth.
    G is always 0. Decode (JS side): raw = 255 + R - B; height = raw * scale.

    Encoding is the exact inverse, split so each channel ramps monotonically
    (good for zlib - one channel is constant while the other moves):
        raw in [0, 255]   -> R=0,        B=255-raw
        raw in [256, 510]  -> R=raw-255,  B=0
    Returns the scale used (raw units per meter).
    """
    span = h_max - h_min
    scale = _RB_MAX_RAW / span if span > 1e-9 else 0.0

    def row_iter():
        for row in rows:
            pixels = []
            for v in row:
                raw = int((v - h_min) * scale + 0.5)
                raw = 0 if raw < 0 else (_RB_MAX_RAW if raw > _RB_MAX_RAW else raw)
                if raw <= 255:
                    pixels.extend((0, 0, 255 - raw))
                else:
                    pixels.extend((raw - 255, 0, 0))
            yield pixels

    png16.write_rgb_png(path, len(rows[0]), len(rows), row_iter(),
                         compress_level=compress_level)
    return scale


def _downsample(rows, n):
    """Nearest-neighbour resample to n x n (legacy SquadCalc sizes, e.g. 500)."""
    src_rows, src_cols = len(rows), len(rows[0])
    out = []
    for r in range(n):
        sr = min(int(r * src_rows / float(n)), src_rows - 1)
        src = rows[sr]
        out.append(
            array(
                "f",
                (src[min(int(c * src_cols / float(n)), src_cols - 1)] for c in range(n)),
            )
        )
    return out


# ============================================================================
# Main export
# ============================================================================

def run_export(output_dir=None, map_name=None, overrides=None):
    """
    Export the currently loaded level.

    output_dir : folder that will receive a <MapName>/ subfolder.
                 Default: <this repo>/output/
    map_name   : override the subfolder name (default: world name).
    overrides  : dict merged shallowly over CONFIG (nested "trace" dict is
                 merged too), e.g. {"resolution_m": 0.5,
                                    "trace": {"channel": "TraceTypeQuery3"}}.
    Returns the path of the per-map output folder.
    """
    cfg = dict(CONFIG)
    cfg["trace"] = dict(CONFIG["trace"])
    if overrides:
        for k, v in overrides.items():
            if k == "trace" and isinstance(v, dict):
                cfg["trace"].update(v)
            else:
                cfg[k] = v

    world = get_editor_world()
    if map_name is None:
        map_name = world.get_name()
    _ACTOR_HAS_MESH.clear()  # actor ids are only valid within one map

    if output_dir is None:
        output_dir = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "output"))
    map_dir = os.path.join(output_dir, map_name)
    if not os.path.isdir(map_dir):
        os.makedirs(map_dir)

    unreal.log("[SquadHeight] ===== Exporting '%s' =====" % map_name)

    # ---- Bounds and grid ---------------------------------------------------
    min_x, max_x, min_y, max_y, min_z, max_z = compute_bounds(world, cfg)
    step = cfg["resolution_m"] * _M_TO_CM
    n_cols = int(math.floor((max_x - min_x) / step)) + 1
    n_rows = int(math.floor((max_y - min_y) / step)) + 1
    z_top = max_z + cfg["trace_top_margin_m"] * _M_TO_CM
    z_bottom = min_z - cfg["trace_bottom_margin_m"] * _M_TO_CM

    unreal.log(
        "[SquadHeight] Bounds (m): X [%.1f .. %.1f]  Y [%.1f .. %.1f]  "
        "Z [%.1f .. %.1f]" % (
            min_x / 100, max_x / 100, min_y / 100, max_y / 100,
            min_z / 100, max_z / 100,
        )
    )
    unreal.log(
        "[SquadHeight] Grid: %d cols x %d rows @ %.2f m  (%d traces minimum)"
        % (n_cols, n_rows, cfg["resolution_m"], n_cols * n_rows)
    )

    # Optional grid rotation: sample positions are spun around the bounds
    # center so the exported rows/cols match a rotated minimap capture.
    # With rotation 0 this reduces exactly to x = min_x + c*step etc.
    rot = math.radians(cfg.get("grid_rotation_deg", 0.0) or 0.0)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    half_u = (n_cols - 1) * step / 2.0
    half_v = (n_rows - 1) * step / 2.0
    if rot:
        unreal.log("[SquadHeight] Grid rotated %.2f deg around (%.1f, %.1f) m"
                   % (cfg["grid_rotation_deg"], center_x / 100, center_y / 100))

    # ---- Trace ----------------------------------------------------------
    ignore_actors = _build_ignore_list(world, cfg)
    unreal.log(
        "[SquadHeight] Ignoring %d foliage actors up-front; mode=%s, "
        "channel/profile=%s" % (
            len(ignore_actors), cfg["surface_mode"],
            cfg["trace"]["profile"] if cfg["trace"]["by"] == "profile"
            else cfg["trace"]["channel"],
        )
    )

    tracer = _Tracer(world, cfg, ignore_actors, z_top, z_bottom)

    settle_rounds, settle_hits = _settle_async_collision(
        world, tracer, n_cols, n_rows, step, half_u, half_v,
        center_x, center_y, cos_r, sin_r)

    rows = []
    no_hit = 0
    structure_cells = 0
    landscape_fallbacks = 0
    t0 = time.time()

    with unreal.ScopedSlowTask(n_rows, "SquadHeight: exporting %s" % map_name) as task:
        task.make_dialog(True)  # progress bar + Cancel button in the editor
        for r in range(n_rows):
            if task.should_cancel():
                _console_write("\n")
                unreal.log_warning("[SquadHeight] Cancelled by user at row %d." % r)
                return None
            v = r * step - half_v
            row = array("f")
            for c in range(n_cols):
                u = c * step - half_u
                x = center_x + u * cos_r - v * sin_r
                y = center_y + u * sin_r + v * cos_r
                z, landscape_z, is_structure = tracer.sample_column(x, y)
                if z is None:
                    # Requirement: fall back to landscape height if no
                    # (acceptable) hit; NaN if the column is a true hole.
                    if landscape_z is not None:
                        z = landscape_z
                        landscape_fallbacks += 1
                    else:
                        z = _NO_DATA
                        no_hit += 1
                elif is_structure:
                    structure_cells += 1
                row.append(z / _M_TO_CM)  # store METERS
            rows.append(row)

            task.enter_progress_frame(1)
            elapsed = time.time() - t0
            rate = (r + 1) * n_cols / max(elapsed, 1e-6)
            eta = (n_rows - r - 1) * n_cols / max(rate, 1e-6)
            # Live single-line bar on the console only - too frequent and not
            # newline-terminated to belong in a log file.
            _print_progress_bar(r + 1, n_rows, rate, eta)
            if (r + 1) % cfg["log_every_rows"] == 0 or r == n_rows - 1:
                line = ("[SquadHeight] row %d/%d  (%.0f traces/s, ETA %dm%02ds)"
                        % (r + 1, n_rows, rate, int(eta // 60), int(eta % 60)))
                unreal.log(line)  # -> Saved/Logs
                _log_write(line + "\n")  # -> logs/export_<timestamp>.log
    _console_write("\n")

    scan_seconds = time.time() - t0

    # ---- Post-process: fill holes, normalize -------------------------------
    h_min = float("inf")
    h_max = float("-inf")
    for row in rows:
        for v in row:
            if not math.isnan(v):
                if v < h_min:
                    h_min = v
                if v > h_max:
                    h_max = v
    if h_min > h_max:
        raise RuntimeError("No geometry was hit at all - check trace channel/bounds.")

    if no_hit:
        # Two kinds of holes: hairline collision gaps along landscape proxy
        # seams (must be neighbor-filled or they show as cracks) and big
        # genuinely empty regions (ocean, out-of-play corners of the minimap
        # square - the legacy heightmaps used a constant there). A frontier
        # walk fills thin gaps from their neighbors for a bounded number of
        # passes without ever materializing the full hole set (11M holes in
        # a Python set has crashed the editor); the rest gets the map min.
        unreal.log_warning(
            "[SquadHeight] %d cells had no hit; neighbor-filling thin gaps, "
            "min-filling large empty regions." % no_hit
        )
        neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1),
                     (1, 1), (1, -1), (-1, 1), (-1, -1))
        max_r, max_c = len(rows), len(rows[0])
        frontier = []
        for r in range(max_r):
            row = rows[r]
            for c in range(max_c):
                if math.isnan(row[c]):
                    for dr, dc in neighbors:
                        rr, cc = r + dr, c + dc
                        if (0 <= rr < max_r and 0 <= cc < max_c
                                and not math.isnan(rows[rr][cc])):
                            frontier.append((r, c))
                            break
        for _ in range(12):  # fills gaps up to ~24 cells wide
            if not frontier:
                break
            next_frontier = set()
            for (r, c) in frontier:
                total, count = 0.0, 0
                for dr, dc in neighbors:
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < max_r and 0 <= cc < max_c:
                        v = rows[rr][cc]
                        if not math.isnan(v):
                            total += v
                            count += 1
                if count:
                    rows[r][c] = total / count
                    for dr, dc in neighbors:
                        rr, cc = r + dr, c + dc
                        if (0 <= rr < max_r and 0 <= cc < max_c
                                and math.isnan(rows[rr][cc])):
                            next_frontier.add((rr, cc))
            frontier = list(next_frontier)
        remaining = 0
        for row in rows:
            for i in range(len(row)):
                if math.isnan(row[i]):
                    row[i] = h_min
                    remaining += 1
        if remaining:
            unreal.log("[SquadHeight] %d cells in large empty regions filled "
                       "with min height %.2f m." % (remaining, h_min))

    z_offset = h_min if cfg["normalize_min_to_zero"] else 0.0
    if z_offset:
        for row in rows:
            for i in range(len(row)):
                row[i] -= z_offset
    out_min, out_max = h_min - z_offset, h_max - z_offset

    rows = _apply_orientation(rows, cfg)

    # ---- Write outputs ------------------------------------------------------
    json_path = os.path.join(map_dir, "heightmap.json")
    png_path = os.path.join(map_dir, "heightmap_16bit.png")
    png8_path = os.path.join(map_dir, "heightmap_8bit.png")
    png_rb_path = os.path.join(map_dir, "heightmap_rb.png")
    _status("Saving heightmap.json...\n")
    _write_json(json_path, rows, cfg["json_decimals"])
    _status("Saving heightmap_16bit.png...\n")
    png_scale = _write_png(png_path, rows, out_min, out_max,
                            bit_depth=16, compress_level=cfg["png_compress_level"])
    _status("Saving heightmap_8bit.png...\n")
    png8_scale = _write_png(png8_path, rows, out_min, out_max,
                             bit_depth=8, compress_level=cfg["png_compress_level"])
    _status("Saving heightmap_rb.png...\n")
    rb_scale = _write_rb_png(png_rb_path, rows, out_min, out_max,
                              compress_level=cfg["png_compress_level"])

    down_path = None
    if cfg["downsample_to"]:
        n = int(cfg["downsample_to"])
        down_path = os.path.join(map_dir, "heightmap_%d.json" % n)
        _status("Saving heightmap_%d.json...\n" % n)
        _write_json(down_path, _downsample(rows, n), cfg["json_decimals"])

    meta = {
        "map": map_name,
        "exported_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "resolution_m": cfg["resolution_m"],
        "grid_cols": len(rows[0]),
        "grid_rows": len(rows),
        "bounds_m": {
            "min_x": min_x / 100, "max_x": max_x / 100,
            "min_y": min_y / 100, "max_y": max_y / 100,
        },
        "grid_rotation_deg": cfg.get("grid_rotation_deg", 0.0),
        "surface_mode": cfg["surface_mode"],
        "trace": cfg["trace"],
        "orientation": {
            "transpose": cfg["transpose"],
            "flip_rows": cfg["flip_rows"],
            "flip_cols": cfg["flip_cols"],
            "row_axis": "world Y (min to max)" if not cfg["transpose"] else "world X",
            "col_axis": "world X (min to max)" if not cfg["transpose"] else "world Y",
        },
        # --- Z scaling info for SquadCalc -----------------------------------
        # heightmap.json values are METERS, already offset so min == 0.
        # To recover absolute UE world Z (m): world_z = value + z_offset_m.
        # heightmap_16bit.png: gray16 = (height_m - 0) * png16_units_per_meter
        # heightmap_8bit.png:  gray8  = (height_m - 0) * png8_units_per_meter
        # (8-bit is lossier - 256 levels instead of 65536 - small preview
        # file only, not the source of truth).
        # heightmap_rb.png: NOT greyscale - raw = 255 + R - B (0..510) =
        # (height_m - 0) * rb_units_per_meter; height_m = raw * rb_meters_per_unit.
        # ~511 levels (vs 256 for 8-bit grey) at the same 8-bit byte depth.
        "z_offset_m": round(z_offset, 4),
        "height_min_m": round(out_min, 4),
        "height_max_m": round(out_max, 4),
        "world_z_min_m": round(h_min, 4),
        "world_z_max_m": round(h_max, 4),
        "png16_units_per_meter": round(png_scale, 6),
        "png16_meters_per_unit": round((out_max - out_min) / 65535.0, 9),
        "png8_units_per_meter": round(png8_scale, 6),
        "png8_meters_per_unit": round((out_max - out_min) / 255.0, 9),
        "rb_units_per_meter": round(rb_scale, 6),
        "rb_meters_per_unit": round((out_max - out_min) / _RB_MAX_RAW, 9),
        "stats": {
            "scan_seconds": round(scan_seconds, 1),
            "structure_cells": structure_cells,
            "landscape_fallback_cells": landscape_fallbacks,
            "no_hit_cells_filled": no_hit,
            "collision_settle_rounds": settle_rounds,
            "collision_settle_sparse_hits": settle_hits,
            "foliage_hits_skipped": tracer.foliage_skips,
            "overhang_drops": tracer.overhang_drops,
            "ignored_foliage_actors": len(ignore_actors),
        },
        # Top mesh assets by cells - check this when verifying foliage
        # exclusion: leaked tree canopies dominate the list and their paths
        # show what to add to exclude_asset_path_keywords.
        "hit_assets_top": [
            {"cells": n, "asset": p}
            for p, n in sorted(tracer.mesh_counts.items(),
                               key=lambda kv: -kv[1])[:40]
        ],
    }
    _status("Saving meta.json...\n")
    with open(os.path.join(map_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    _update_scaling_recap(output_dir, map_name,
                           meta["png16_meters_per_unit"],
                           meta["png8_meters_per_unit"],
                           meta["rb_meters_per_unit"])

    unreal.log("[SquadHeight] ----- DONE: %s -----" % map_name)
    unreal.log("[SquadHeight] height min/max: %.2f / %.2f m (z_offset %.2f m, "
               "world Z %.2f..%.2f m)"
               % (out_min, out_max, z_offset, h_min, h_max))
    unreal.log("[SquadHeight] 16-bit PNG scale: %.4f gray-units per meter "
               "(%.6f m per gray-unit)"
               % (png_scale, (out_max - out_min) / 65535.0))
    unreal.log("[SquadHeight] cells on structures: %d, foliage hits skipped: %d, "
               "scan time: %.0f s"
               % (structure_cells, tracer.foliage_skips, scan_seconds))
    unreal.log("[SquadHeight] wrote: %s" % json_path)
    unreal.log("[SquadHeight]        %s" % png_path)
    unreal.log("[SquadHeight]        %s" % png8_path)
    unreal.log("[SquadHeight]        %s" % png_rb_path)
    if down_path:
        unreal.log("[SquadHeight]        %s" % down_path)
    unreal.log("[SquadHeight]        %s" % os.path.join(output_dir, "scaling.json"))
    return map_dir


if __name__ == "__main__":
    # Running the file directly (py ".../export_heightmap.py") exports the
    # currently open level with the CONFIG defaults.
    run_export()
