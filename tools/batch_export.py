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
        _force_load_sublevels()
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


def _force_load_sublevels():
    """
    Master levels (e.g. /Game/Maps/X/Sublevels/L_000_Master_X) keep their
    content - landscape included - in streaming sublevels. Opening the map in
    a commandlet does not necessarily load them, which leaves the world empty
    for tracing. Flip every streaming level to loaded+visible and flush.
    """
    world = _get_world()
    try:
        streaming = list(world.get_editor_property("streaming_levels"))
    except Exception as exc:
        unreal.log_warning("[SquadHeight] could not read streaming levels: %s" % exc)
        return
    if not streaming:
        return

    def _unloaded():
        count = 0
        for sl in streaming:
            try:
                if sl.get_loaded_level() is None:
                    count += 1
            except Exception:
                pass
        return count

    before = _unloaded()
    if before == 0:
        unreal.log("[SquadHeight] all %d sublevels already loaded" % len(streaming))
        return
    unreal.log("[SquadHeight] %d/%d sublevels not loaded - forcing load..."
               % (before, len(streaming)))

    for sl in streaming:
        for prop, value in (("should_be_loaded", True),
                            ("should_be_visible", True),
                            ("should_be_visible_in_editor", True)):
            try:
                sl.set_editor_property(prop, value)
            except Exception:
                pass
    try:
        unreal.GameplayStatics.flush_level_streaming(world)
    except Exception as exc:
        unreal.log_warning("[SquadHeight] flush_level_streaming failed: %s" % exc)

    after = _unloaded()
    if after:
        # Plan B: re-add still-unloaded sublevels as always-loaded levels.
        for sl in streaming:
            try:
                if sl.get_loaded_level() is not None:
                    continue
                pkg = str(sl.get_editor_property("world_asset").get_path_name())
                pkg = pkg.split(".")[0]
                unreal.EditorLevelUtils.add_level_to_world(
                    world, pkg, unreal.LevelStreamingAlwaysLoaded)
            except Exception as exc:
                unreal.log_warning("[SquadHeight] add_level_to_world: %s" % exc)
        after = _unloaded()

    unreal.log("[SquadHeight] sublevels still unloaded after forcing: %d" % after)


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
    ok = sum(1 for r in report if r["status"] == "ok")
    unreal.log("[SquadHeight] Batch finished: %d/%d ok in %.0f s"
               % (ok, len(report), time.time() - t_batch))
    for r in report:
        line = "[SquadHeight]   %-8s %s (%.0fs)" % (
            r["status"].upper(), r["level"], r["seconds"])
        (unreal.log if r["status"] == "ok" else unreal.log_error)(line)

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
