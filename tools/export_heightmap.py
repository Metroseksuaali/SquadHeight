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
    heightmap.png    - 16-bit grayscale PNG for visual inspection.
    meta.json        - bounds, resolution, min/max, z-offset, PNG scaling,
                       stats.  Everything SquadCalc needs to interpret the
                       data is in here AND logged to the output log.

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

    # Hits on these component classes are skipped and the ray continues
    # downward (slow path, for foliage that is NOT on an IFA).
    "exclude_component_classes": [
        "FoliageInstancedStaticMeshComponent",
        "LandscapeGrassComponent",
    ],

    # Case-insensitive substrings matched against the hit static mesh's full
    # asset path (e.g. "/Game/Environment/Foliage/Trees/Oak_01.Oak_01").
    # Keep this conservative: "foliage" catches the usual folder layout.
    # Add e.g. "tree_", "bush_", "water" after inspecting Squad's content
    # naming - see README "Verifying foliage exclusion".
    "exclude_asset_path_keywords": ["foliage"],

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


def _is_excluded_hit(actor, component, cfg):
    """Per-hit foliage filtering (slow path) - see CONFIG keys for rationale."""
    if actor is not None:
        ifa_class = getattr(unreal, "InstancedFoliageActor", None)
        if ifa_class is not None and isinstance(actor, ifa_class):
            return True
        if actor.get_class().get_name() in cfg["exclude_actor_classes"]:
            return True

    if component is not None:
        comp_class = component.get_class().get_name()
        if comp_class in cfg["exclude_component_classes"]:
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


def _write_png(path, rows, h_min, h_max):
    """16-bit grayscale: 0 = h_min, 65535 = h_max."""
    span = h_max - h_min
    scale = 65535.0 / span if span > 1e-9 else 0.0

    def row_iter():
        for row in rows:
            yield [int((v - h_min) * scale + 0.5) for v in row]

    png16.write_gray_png(path, len(rows[0]), len(rows), row_iter(), bit_depth=16)
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
    rows = []
    no_hit = 0
    structure_cells = 0
    landscape_fallbacks = 0
    t0 = time.time()

    with unreal.ScopedSlowTask(n_rows, "SquadHeight: exporting %s" % map_name) as task:
        task.make_dialog(True)  # progress bar + Cancel button in the editor
        for r in range(n_rows):
            if task.should_cancel():
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
            if (r + 1) % cfg["log_every_rows"] == 0 or r == n_rows - 1:
                elapsed = time.time() - t0
                rate = (r + 1) * n_cols / max(elapsed, 1e-6)
                eta = (n_rows - r - 1) * n_cols / max(rate, 1e-6)
                unreal.log(
                    "[SquadHeight] row %d/%d  (%.0f traces/s, ETA %dm%02ds)"
                    % (r + 1, n_rows, rate, int(eta // 60), int(eta % 60))
                )

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
        # No-hit cells are usually hairline collision gaps along landscape
        # streaming-proxy seams (thin staircase lines on rotated landscapes)
        # plus areas outside any geometry. Filling them with the map minimum
        # creates ugly black cracks, so instead flood-fill each hole with the
        # average of its valid neighbors, growing inward pass by pass.
        unreal.log_warning(
            "[SquadHeight] %d cells had no hit (landscape seam gaps / out-of-"
            "level areas); filling from nearest valid neighbors." % no_hit
        )
        holes = set()
        for r in range(len(rows)):
            row = rows[r]
            for c in range(len(row)):
                if math.isnan(row[c]):
                    holes.add((r, c))
        neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1),
                     (1, 1), (1, -1), (-1, 1), (-1, -1))
        max_r, max_c = len(rows), len(rows[0])
        while holes:
            filled_this_pass = []
            for (r, c) in holes:
                total, count = 0.0, 0
                for dr, dc in neighbors:
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < max_r and 0 <= cc < max_c:
                        v = rows[rr][cc]
                        if not math.isnan(v):
                            total += v
                            count += 1
                if count:
                    filled_this_pass.append((r, c, total / count))
            if not filled_this_pass:
                # Should not happen (only if the whole grid is NaN, which is
                # caught above) - bail out with min instead of looping forever.
                for (r, c) in holes:
                    rows[r][c] = h_min
                break
            for r, c, v in filled_this_pass:
                rows[r][c] = v
                holes.discard((r, c))

    z_offset = h_min if cfg["normalize_min_to_zero"] else 0.0
    if z_offset:
        for row in rows:
            for i in range(len(row)):
                row[i] -= z_offset
    out_min, out_max = h_min - z_offset, h_max - z_offset

    rows = _apply_orientation(rows, cfg)

    # ---- Write outputs ------------------------------------------------------
    json_path = os.path.join(map_dir, "heightmap.json")
    png_path = os.path.join(map_dir, "heightmap.png")
    _write_json(json_path, rows, cfg["json_decimals"])
    png_scale = _write_png(png_path, rows, out_min, out_max)

    down_path = None
    if cfg["downsample_to"]:
        n = int(cfg["downsample_to"])
        down_path = os.path.join(map_dir, "heightmap_%d.json" % n)
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
        # PNG: gray16 = (height_m - 0) * png_units_per_meter
        "z_offset_m": round(z_offset, 4),
        "height_min_m": round(out_min, 4),
        "height_max_m": round(out_max, 4),
        "world_z_min_m": round(h_min, 4),
        "world_z_max_m": round(h_max, 4),
        "png_units_per_meter": round(png_scale, 6),
        "png_meters_per_unit": round((out_max - out_min) / 65535.0, 9),
        "stats": {
            "scan_seconds": round(scan_seconds, 1),
            "structure_cells": structure_cells,
            "landscape_fallback_cells": landscape_fallbacks,
            "no_hit_cells_filled": no_hit,
            "foliage_hits_skipped": tracer.foliage_skips,
            "overhang_drops": tracer.overhang_drops,
            "ignored_foliage_actors": len(ignore_actors),
        },
    }
    with open(os.path.join(map_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    unreal.log("[SquadHeight] ----- DONE: %s -----" % map_name)
    unreal.log("[SquadHeight] height min/max: %.2f / %.2f m (z_offset %.2f m, "
               "world Z %.2f..%.2f m)"
               % (out_min, out_max, z_offset, h_min, h_max))
    unreal.log("[SquadHeight] PNG scale: %.4f gray-units per meter "
               "(%.6f m per gray-unit)"
               % (png_scale, (out_max - out_min) / 65535.0))
    unreal.log("[SquadHeight] cells on structures: %d, foliage hits skipped: %d, "
               "scan time: %.0f s"
               % (structure_cells, tracer.foliage_skips, scan_seconds))
    unreal.log("[SquadHeight] wrote: %s" % json_path)
    unreal.log("[SquadHeight]        %s" % png_path)
    if down_path:
        unreal.log("[SquadHeight]        %s" % down_path)
    return map_dir


if __name__ == "__main__":
    # Running the file directly (py ".../export_heightmap.py") exports the
    # currently open level with the CONFIG defaults.
    run_export()
