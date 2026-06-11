"""
probe_points.py - load a map (with the batch runner's sublevel handling) and
trace a few world points, printing exactly which actor/component each ray
hits. For identifying mystery shapes in an exported heightmap.

    set SQUADHEIGHT_DIAG_MAP=/Game/Maps/Narva/Sublevels/L_000_Master_Narva
    set SQUADHEIGHT_PROBE=244,-655;748,-898;-662,255
    UnrealEditor-Cmd.exe <uproject> -run=pythonscript -script="tools/probe_points.py" ...
"""

import os
import sys

import unreal

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.append(_SCRIPT_DIR)

import batch_export  # reuse the exact same loading pipeline as the batch


def log(msg):
    unreal.log("[SquadProbe] %s" % msg)


def main():
    map_path = os.environ.get("SQUADHEIGHT_DIAG_MAP")
    points = []
    for part in os.environ.get("SQUADHEIGHT_PROBE", "").split(";"):
        if part.strip():
            x, y = part.split(",")
            points.append((float(x), float(y)))
    if not map_path or not points:
        log("set SQUADHEIGHT_DIAG_MAP and SQUADHEIGHT_PROBE first")
        return

    log("loading %s" % map_path)
    batch_export._load_level(map_path)
    world = batch_export._get_world()

    for (x_m, y_m) in points:
        x, y = x_m * 100.0, y_m * 100.0
        start_z, end_z = 100000.0, -100000.0
        log("--- probe (%.0f, %.0f) m ---" % (x_m, y_m))
        z = start_z
        for step in range(6):
            hit = unreal.SystemLibrary.line_trace_single(
                world, unreal.Vector(x, y, z), unreal.Vector(x, y, end_z),
                unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [],
                unreal.DrawDebugTrace.NONE, True)
            if not hit:
                break
            fields = unreal.GameplayStatics.break_hit_result(hit) \
                if hasattr(unreal.GameplayStatics, "break_hit_result") \
                else hit.to_tuple()
            loc = next((f for f in fields if isinstance(f, unreal.Vector)), None)
            comp = next((f for f in fields
                         if isinstance(f, unreal.ActorComponent)), None)
            actor = comp.get_owner() if comp else None
            log("   z=%8.2f m  comp=%-28s actor=%s (%s)" % (
                loc.z / 100.0 if loc else -1,
                comp.get_class().get_name() if comp else "?",
                actor.get_actor_label() if actor else "?",
                actor.get_class().get_name() if actor else "?"))
            if loc is None:
                break
            z = loc.z - 5.0
    log("probe complete")


if __name__ == "__main__":
    main()
