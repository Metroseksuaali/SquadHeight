"""
batch_export.py - load each configured level in sequence and export it.
========================================================================

Runs INSIDE the Unreal editor's Python (same requirement as
export_heightmap.py). Two ways to launch it:

1. Headless (recommended for full re-exports after a map update):
       UnrealEditor-Cmd.exe <Project.uproject> -run=pythonscript
           -script="<path-to-repo>/tools/batch_export.py"
           -stdout -FullStdOutLogOutput -Unattended -NoSplash
   (see ../run_batch_export.bat - edit the paths at the top of that file).
   UE4.27 uses UE4Editor-Cmd.exe instead of UnrealEditor-Cmd.exe.

2. Interactively, from the editor Python console:
       py "<path-to-repo>/tools/batch_export.py"

Configuration is read from a JSON file, resolved in this order:
    1. the SQUADHEIGHT_CONFIG environment variable (set by the .bat)
    2. first extra argument in sys.argv
    3. maps_config.json next to this script
See maps_config.example.json for the schema.
"""

import json
import os
import sys
import time
import traceback

import unreal

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.append(_SCRIPT_DIR)

import export_heightmap  # noqa: E402


def _find_config_path():
    env = os.environ.get("SQUADHEIGHT_CONFIG")
    if env and os.path.isfile(env):
        return env
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".json") and os.path.isfile(arg):
            return arg
    default = os.path.join(_SCRIPT_DIR, "maps_config.json")
    if os.path.isfile(default):
        return default
    raise RuntimeError(
        "No batch config found. Copy maps_config.example.json to "
        "maps_config.json (next to batch_export.py), or point the "
        "SQUADHEIGHT_CONFIG env var at your config file."
    )


def _load_level(level_path):
    """
    Open a level by package path (e.g. '/Game/Maps/Chora/Chora').
    EditorLoadingAndSavingUtils works in UE4.27, UE5 AND in the pythonscript
    commandlet; LevelEditorSubsystem is tried first on UE5 for good measure.
    """
    loaded = False
    try:
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if les and les.load_level(level_path):
            loaded = True
    except (AttributeError, Exception):
        pass
    if not loaded:
        loaded = bool(unreal.EditorLoadingAndSavingUtils.load_map(level_path))
    if loaded:
        _force_load_sublevels(level_path)
    return loaded


def _get_world():
    try:
        sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        if sub:
            world = sub.get_editor_world()
            if world:
                return world
    except (AttributeError, Exception):
        pass
    return unreal.EditorLevelLibrary.get_editor_world()


def _landscape_count(world):
    cls = getattr(unreal, "LandscapeProxy", None) or getattr(unreal, "Landscape")
    return len(unreal.GameplayStatics.get_all_actors_of_class(world, cls))


def _find_sublevel_worlds(level_path):
    """
    World assets in the map's Sublevels/Levels folders, via the asset
    registry. Works even when UWorld.streaming_levels is not exposed to
    Python (e.g. Squad's UE 5.7 build).
    """
    if "/Sublevels/" in level_path:
        base = level_path.rsplit("/Sublevels/", 1)[0]
    else:
        base = level_path.rsplit("/", 1)[0]
    roots = [base + "/Sublevels", base + "/Levels"]

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    try:
        ar_filter = unreal.ARFilter(
            class_paths=[unreal.TopLevelAssetPath("/Script/Engine", "World")],
            package_paths=roots, recursive_paths=True)
    except Exception:
        ar_filter = unreal.ARFilter(class_names=["World"],
                                    package_paths=roots, recursive_paths=True)
    worlds = sorted({str(a.package_name) for a in registry.get_assets(ar_filter)})
    # Don't re-add master variants (the loaded map itself / its siblings).
    return [w for w in worlds
            if not w.rsplit("/", 1)[-1].lower().startswith("l_000_master")]


def _force_load_sublevels(level_path):
    """
    Master levels (e.g. /Game/Maps/X/Sublevels/L_000_Master_X) keep their
    content - landscape included - in streaming sublevels, and commandlets do
    not necessarily load them. The clean route (UWorld.streaming_levels) is
    not script-exposed in every build, so when the world looks empty (no
    LandscapeProxy) the sublevels are discovered via the asset registry and
    attached with EditorLevelUtils.add_level_to_world as always-loaded.
    """
    world = _get_world()
    if _landscape_count(world) > 0:
        return  # content is loaded; nothing to do

    subs = _find_sublevel_worlds(level_path)
    if not subs:
        unreal.log_warning("[SquadHeight] no landscape AND no sublevel worlds "
                           "found next to %s" % level_path)
        return

    unreal.log("[SquadHeight] no landscape after load - attaching %d sublevels "
               "from the asset registry..." % len(subs))
    added = 0
    for pkg in subs:
        try:
            unreal.EditorLevelUtils.add_level_to_world(
                world, pkg, unreal.LevelStreamingAlwaysLoaded)
            added += 1
        except Exception as exc:
            unreal.log_warning("[SquadHeight] add_level_to_world(%s): %s"
                               % (pkg, exc))
    try:
        unreal.GameplayStatics.flush_level_streaming(world)
    except Exception:
        pass
    unreal.log("[SquadHeight] attached %d/%d sublevels; landscape proxies now: %d"
               % (added, len(subs), _landscape_count(world)))


def main():
    config_path = _find_config_path()
    with open(config_path, "r") as f:
        config = json.load(f)

    output_root = config.get("output_root") or os.path.normpath(
        os.path.join(_SCRIPT_DIR, "..", "output")
    )
    defaults = config.get("defaults", {})
    maps = config.get("maps", [])
    if not maps:
        raise RuntimeError("Config %s has an empty 'maps' list." % config_path)

    unreal.log("[SquadHeight] Batch export: %d map(s), output root: %s"
               % (len(maps), output_root))

    report = []
    t_batch = time.time()
    for i, entry in enumerate(maps):
        level = entry["level"]
        name = entry.get("name")  # None -> world name
        overrides = dict(defaults)
        # Per-map overrides win over defaults; nested "trace" dicts merge.
        for k, v in entry.get("overrides", {}).items():
            if k == "trace" and isinstance(v, dict) and isinstance(
                overrides.get("trace"), dict
            ):
                merged = dict(overrides["trace"])
                merged.update(v)
                overrides["trace"] = merged
            else:
                overrides[k] = v

        # Resume support: skip maps that already have a finished export
        # (delete the map's output folder, or set SQUADHEIGHT_FORCE=1, to redo).
        done_marker = os.path.join(output_root, name or level.rsplit("/", 1)[-1],
                                   "meta.json")
        if os.path.isfile(done_marker) and not os.environ.get("SQUADHEIGHT_FORCE"):
            unreal.log("[SquadHeight] ===== [%d/%d] %s — already exported, "
                       "skipping =====" % (i + 1, len(maps), level))
            report.append({"level": level, "status": "skipped", "seconds": 0})
            continue

        unreal.log("[SquadHeight] ===== [%d/%d] %s =====" % (i + 1, len(maps), level))
        t_map = time.time()
        try:
            if not _load_level(level):
                raise RuntimeError("load_map returned false for %s" % level)
            out_dir = export_heightmap.run_export(
                output_dir=output_root, map_name=name, overrides=overrides
            )
            if out_dir is None:
                raise RuntimeError("export cancelled")
            report.append({
                "level": level, "status": "ok", "output": out_dir,
                "seconds": round(time.time() - t_map, 1),
            })
        except Exception as exc:
            unreal.log_error("[SquadHeight] FAILED %s: %s" % (level, exc))
            unreal.log_error(traceback.format_exc())
            report.append({
                "level": level, "status": "failed", "error": str(exc),
                "seconds": round(time.time() - t_map, 1),
            })
            # Keep going - one broken map should not kill the whole batch.

    # Summary + machine-readable report for CI-style usage.
    ok = sum(1 for r in report if r["status"] in ("ok", "skipped"))
    unreal.log("[SquadHeight] Batch finished: %d/%d ok in %.0f s"
               % (ok, len(report), time.time() - t_batch))
    for r in report:
        line = "[SquadHeight]   %-8s %s (%.0fs)" % (
            r["status"].upper(), r["level"], r["seconds"])
        (unreal.log if r["status"] != "failed" else unreal.log_error)(line)

    if not os.path.isdir(output_root):
        os.makedirs(output_root)
    with open(os.path.join(output_root, "batch_report.json"), "w") as f:
        json.dump({"finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "results": report}, f, indent=2)

    if ok != len(report):
        # Non-zero exit so the .bat / CI can detect partial failure.
        sys.exit(1)


if __name__ == "__main__":
    main()
