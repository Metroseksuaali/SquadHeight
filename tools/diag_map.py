"""
diag_map.py - load one map headless and report what is actually in the world.

Used to debug maps whose scan comes out empty: prints actor/level/landscape
inventory and fires a grid of test rays so you can see what (if anything)
blocks the Visibility channel.

    set SQUADHEIGHT_DIAG_MAP=/Game/Maps/Chora/Chora
    UnrealEditor-Cmd.exe <uproject> -run=pythonscript -script="tools/diag_map.py" ...
"""

import os
import sys

import unreal

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.append(_SCRIPT_DIR)

MAP = os.environ.get("SQUADHEIGHT_DIAG_MAP", "/Game/Maps/Chora/Chora")


def log(msg):
    unreal.log("[SquadDiag] %s" % msg)


def main():
    log("loading %s ..." % MAP)
    unreal.EditorLoadingAndSavingUtils.load_map(MAP)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    log("world: %s (class %s)" % (world.get_name(), world.get_class().get_name()))

    # World Partition?
    try:
        wp = world.get_editor_property("world_partition")
        log("world_partition property: %s" % ("SET (WP map!)" if wp else "None"))
    except Exception as exc:
        log("world_partition not readable: %s" % exc)

    # Loaded levels
    try:
        levels = unreal.EditorLevelUtils.get_levels(world)
        log("loaded ULevels: %d" % len(levels))
        for lvl in levels[:25]:
            log("   level: %s" % str(lvl.get_path_name()).split(".")[0])
    except Exception as exc:
        log("get_levels failed: %s" % exc)

    # Actor inventory
    actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
    log("total actors: %d" % len(actors))
    by_class = {}
    for a in actors:
        c = a.get_class().get_name()
        by_class[c] = by_class.get(c, 0) + 1
    for c, n in sorted(by_class.items(), key=lambda kv: -kv[1])[:20]:
        log("   %6d  %s" % (n, c))

    # Landscape details
    ls_cls = getattr(unreal, "LandscapeProxy", None) or unreal.Landscape
    proxies = unreal.GameplayStatics.get_all_actors_of_class(world, ls_cls)
    log("landscape actors: %d" % len(proxies))
    for p in proxies[:10]:
        o, e = p.get_actor_bounds(False)
        log("   %s (%s)  center=(%.0f, %.0f) ext=(%.0f, %.0f) m" % (
            p.get_actor_label(), p.get_class().get_name(),
            o.x / 100, o.y / 100, e.x / 100, e.y / 100))

    # Test rays: 8x8 grid over the landscape bounds
    if proxies:
        mn = [1e18, 1e18]
        mx = [-1e18, -1e18]
        zmn, zmx = 1e18, -1e18
        for p in proxies:
            o, e = p.get_actor_bounds(False)
            mn[0] = min(mn[0], o.x - e.x); mx[0] = max(mx[0], o.x + e.x)
            mn[1] = min(mn[1], o.y - e.y); mx[1] = max(mx[1], o.y + e.y)
            zmn = min(zmn, o.z - e.z); zmx = max(zmx, o.z + e.z)
        hits = 0
        miss = 0
        examples = []
        for i in range(8):
            for j in range(8):
                x = mn[0] + (i + 0.5) * (mx[0] - mn[0]) / 8
                y = mn[1] + (j + 0.5) * (mx[1] - mn[1]) / 8
                hit = unreal.SystemLibrary.line_trace_single(
                    world, unreal.Vector(x, y, zmx + 20000),
                    unreal.Vector(x, y, zmn - 10000),
                    unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [],
                    unreal.DrawDebugTrace.NONE, True)
                if hit:
                    hits += 1
                    if len(examples) < 5:
                        fields = unreal.GameplayStatics.break_hit_result(hit) \
                            if hasattr(unreal.GameplayStatics, "break_hit_result") \
                            else hit.to_tuple()
                        comp = next((f for f in fields
                                     if isinstance(f, unreal.ActorComponent)), None)
                        examples.append(comp.get_class().get_name() if comp else "?")
                else:
                    miss += 1
        log("test rays: %d hit / %d miss" % (hits, miss))
        log("hit component classes (sample): %s" % ", ".join(examples))

    log("diagnosis complete")


if __name__ == "__main__":
    main()
