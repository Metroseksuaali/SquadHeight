"""
probe_hangars.py - diagnose why some structures (Tallil hangars) don't
register in the heightmap scan. Loads the map with the batch runner's
pipeline, finds actors whose label/class/mesh matches KEYWORDS, dumps their
collision setup, and traces straight down over each distinct mesh type with
several query methods (Visibility/Camera channels, BlockAll profile,
object-type queries) to see which one actually hits.

    set SQUADHEIGHT_DIAG_MAP=/Game/Maps/Tallil_Outskirts/Tallil_Outskirts
    UnrealEditor-Cmd.exe <uproject> -run=pythonscript -script="tools/probe_hangars.py" ...
"""

import os
import sys

import unreal

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.append(_SCRIPT_DIR)

import batch_export  # reuse the exact same loading pipeline as the batch

KEYWORDS = ("hangar", "shelter", "aircraft")
MAX_PROBES_PER_MESH = 1
MAX_DISTINCT_MESHES = 12


def log(msg):
    unreal.log("[SquadProbe] %s" % msg)


def _all_actors(world):
    try:
        sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        if sub:
            return list(sub.get_all_level_actors())
    except Exception:
        pass
    return list(unreal.EditorLevelLibrary.get_all_level_actors())


def _mesh_path(comp):
    try:
        mesh = comp.get_editor_property("static_mesh")
        if mesh:
            return mesh.get_path_name()
    except Exception:
        pass
    return None


def _collision_info(comp):
    bits = []
    try:
        bits.append("enabled=%s" % comp.get_collision_enabled())
    except Exception:
        bits.append("enabled=?")
    try:
        bits.append("profile=%s" % comp.get_collision_profile_name())
    except Exception:
        bits.append("profile=?")
    try:
        bits.append("objtype=%s" % comp.get_collision_object_type())
    except Exception:
        pass
    for label, ch_name in (("vis", "ECC_VISIBILITY"), ("cam", "ECC_CAMERA"),
                           ("wstat", "ECC_WORLD_STATIC")):
        ch = getattr(unreal.CollisionChannel, ch_name, None)
        if ch is None:
            continue
        try:
            bits.append("%s=%s" % (label, comp.get_collision_response_to_channel(ch)))
        except Exception:
            bits.append("%s=?" % label)
    return " ".join(str(b) for b in bits)


def _describe_hit(hit):
    if not hit:
        return "no hit"
    try:
        fields = hit.to_tuple()
    except Exception:
        try:
            fields = unreal.GameplayStatics.break_hit_result(hit)
        except Exception:
            return "hit (unparseable)"
    loc = next((f for f in fields if isinstance(f, unreal.Vector)), None)
    comp = next((f for f in fields if isinstance(f, unreal.ActorComponent)), None)
    actor = comp.get_owner() if comp else None
    mesh = _mesh_path(comp) if comp else None
    return "z=%.2f m  comp=%s actor=%s mesh=%s" % (
        loc.z / 100.0 if loc else -99999,
        comp.get_class().get_name() if comp else "?",
        actor.get_actor_label() if actor else "?",
        (mesh or "-").rsplit("/", 1)[-1])


def _trace_methods(world, x, y, z_top, z_bottom):
    start = unreal.Vector(x, y, z_top)
    end = unreal.Vector(x, y, z_bottom)
    results = []

    for label, query in (("Visibility", unreal.TraceTypeQuery.TRACE_TYPE_QUERY1),
                         ("Camera    ", unreal.TraceTypeQuery.TRACE_TYPE_QUERY2)):
        try:
            hit = unreal.SystemLibrary.line_trace_single(
                world, start, end, query, True, [],
                unreal.DrawDebugTrace.NONE, True)
            results.append((label, _describe_hit(hit)))
        except Exception as exc:
            results.append((label, "ERROR %s" % exc))

    try:
        hit = unreal.SystemLibrary.line_trace_single_by_profile(
            world, start, end, "BlockAll", True, [],
            unreal.DrawDebugTrace.NONE, True)
        results.append(("BlockAll  ", _describe_hit(hit)))
    except Exception as exc:
        results.append(("BlockAll  ", "ERROR %s" % exc))

    # Object-type query: WorldStatic + WorldDynamic (queries 1 and 2).
    try:
        objs = [unreal.ObjectTypeQuery.OBJECT_TYPE_QUERY1,
                unreal.ObjectTypeQuery.OBJECT_TYPE_QUERY2]
        hit = unreal.SystemLibrary.line_trace_single_for_objects(
            world, start, end, objs, True, [],
            unreal.DrawDebugTrace.NONE, True)
        results.append(("ObjType1+2", _describe_hit(hit)))
    except Exception as exc:
        results.append(("ObjType1+2", "ERROR %s" % exc))

    # All object types 1..6, in case the mesh sits on a custom object channel.
    try:
        objs = [getattr(unreal.ObjectTypeQuery, "OBJECT_TYPE_QUERY%d" % i)
                for i in range(1, 7)]
        hit = unreal.SystemLibrary.line_trace_single_for_objects(
            world, start, end, objs, True, [],
            unreal.DrawDebugTrace.NONE, True)
        results.append(("ObjType1-6", _describe_hit(hit)))
    except Exception as exc:
        results.append(("ObjType1-6", "ERROR %s" % exc))

    return results


def main():
    map_path = os.environ.get("SQUADHEIGHT_DIAG_MAP",
                              "/Game/Maps/Tallil_Outskirts/Tallil_Outskirts")
    log("loading %s" % map_path)
    batch_export._load_level(map_path)
    world = batch_export._get_world()

    actors = _all_actors(world)
    log("%d actors loaded" % len(actors))

    # mesh path -> list of (actor, comp)
    by_mesh = {}
    matched_actors = 0
    for actor in actors:
        try:
            label = actor.get_actor_label().lower()
            cls = actor.get_class().get_name().lower()
        except Exception:
            continue
        comps = []
        try:
            comps = list(actor.get_components_by_class(unreal.StaticMeshComponent))
        except Exception:
            pass
        actor_matched = False
        for comp in comps:
            path = _mesh_path(comp) or ""
            hay = "%s %s %s" % (label, cls, path.lower())
            if any(k in hay for k in KEYWORDS):
                by_mesh.setdefault(path or "<no mesh>", []).append((actor, comp))
                actor_matched = True
        if actor_matched:
            matched_actors += 1

    log("matched %d actors, %d distinct meshes:" % (matched_actors, len(by_mesh)))
    for path, lst in sorted(by_mesh.items(), key=lambda kv: -len(kv[1])):
        log("  %4d x %s" % (len(lst), path))

    for path, lst in sorted(by_mesh.items(),
                            key=lambda kv: -len(kv[1]))[:MAX_DISTINCT_MESHES]:
        for actor, comp in lst[:MAX_PROBES_PER_MESH]:
            try:
                origin, extent = actor.get_actor_bounds(False)
            except Exception:
                origin = actor.get_actor_location()
                extent = unreal.Vector(0, 0, 0)
            x, y = origin.x, origin.y
            z_top = origin.z + extent.z + 5000.0   # 50 m above the roof
            z_bottom = origin.z - extent.z - 20000.0
            log("--- %s" % path.rsplit("/", 1)[-1])
            log("    actor=%s at (%.1f, %.1f) m, bounds z [%.1f .. %.1f] m"
                % (actor.get_actor_label(), x / 100.0, y / 100.0,
                   (origin.z - extent.z) / 100.0, (origin.z + extent.z) / 100.0))
            log("    collision: %s" % _collision_info(comp))
            for name, desc in _trace_methods(world, x, y, z_top, z_bottom):
                log("    %s -> %s" % (name, desc))

    log("probe complete")


if __name__ == "__main__":
    main()
