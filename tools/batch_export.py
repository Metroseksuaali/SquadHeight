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
import sh_log  # noqa: E402  (clean console + log-file reporter, stdlib-only)


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


def _load_level(level_path, keep_water_layers=False):
    """
    Open a level by package path (e.g. '/Game/Maps/Chora/Chora').
    EditorLoadingAndSavingUtils works in UE4.27, UE5 AND in the pythonscript
    commandlet; LevelEditorSubsystem is tried first on UE5 for good measure.
    keep_water_layers: also attach weather/lighting layers that carry the
    ocean (see _WATER_LAYER_NAME_KEYWORDS).
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
        _force_load_sublevels(level_path, keep_water_layers)
        _ensure_levels_visible(level_path, keep_water_layers)
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


# Sublevels that are NOT part of the static world geometry. Gameplay layers
# would add vehicles/deployables; whitebox/old levels are superseded
# blockouts; the rest are lighting/audio/dev variants.
_SUBLEVEL_SKIP = (
    "/gameplay_layer", "/lighting_layer", "/coop/", "/sound", "/audio",
    "/development/", "/automation/", "_gpu", "/weatherlayer", "/wl_",
    "/ll_", "flythrough", "whitebox", "blockout", "old_delete", "/old",
    "wip", "playtest", "deprecated", "/050_cameras",
    "refrence", "reference",  # designer POI marker levels (OWI typo included)
    "backupmeshes",  # Sanxian 060_BackupMeshes/*_BM: unused art copies
    "sanxian_islands_geo",  # stale whole-map snapshot, not streamed by the game
)

# Some maps keep their ocean (BP_Ocean from SQWater) in a weather layer
# (Black Coast: WeatherLayers/WL_BlackCoast_OpenOcean_Choppy), which the skip
# list above drops. The water-surface terrain export needs it, so a level
# skipped ONLY for being a weather/lighting layer is kept when its name says
# ocean/water. Other layer content is irrelevant there: terrain_only ignores
# every non-landscape, non-water hit.
_WEATHER_LAYER_SKIPS = ("/weatherlayer", "/wl_", "/lighting_layer", "/ll_")
_WATER_LAYER_NAME_KEYWORDS = ("ocean", "water")


def _is_skipped_level(pkg, keep_water_layers=False):
    low = pkg.lower()
    hits = [k for k in _SUBLEVEL_SKIP if k in low]
    if not hits:
        return False
    if keep_water_layers and all(k in _WEATHER_LAYER_SKIPS for k in hits):
        name = low.rsplit("/", 1)[-1]
        if any(k in name for k in _WATER_LAYER_NAME_KEYWORDS):
            return False
    return True


def _find_sublevel_worlds(level_path, keep_water_layers=False):
    """
    World assets that belong to the map, via the asset registry. Works even
    when UWorld.streaming_levels is not exposed to Python (e.g. Squad's
    UE 5.7 build). Scans the whole map folder because some maps keep their
    landscape at the folder root (Belaya's 'BelayaLandscape') rather than
    under Sublevels/.
    """
    if "/Sublevels/" in level_path:
        base = level_path.rsplit("/Sublevels/", 1)[0]
    else:
        base = level_path.rsplit("/", 1)[0]

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    try:
        ar_filter = unreal.ARFilter(
            class_paths=[unreal.TopLevelAssetPath("/Script/Engine", "World")],
            package_paths=[base], recursive_paths=True)
    except Exception:
        ar_filter = unreal.ARFilter(class_names=["World"],
                                    package_paths=[base], recursive_paths=True)
    worlds = sorted({str(a.package_name) for a in registry.get_assets(ar_filter)})

    def wanted(w):
        if w == level_path:
            return False  # the map we just loaded
        name = w.rsplit("/", 1)[-1].lower()
        if name.startswith("l_000_master"):
            return False  # sibling master variants
        return not _is_skipped_level(w, keep_water_layers)

    return [w for w in worlds if wanted(w)]


def _force_load_sublevels(level_path, keep_water_layers=False):
    """
    Map content can live in streaming sublevels that commandlets do not load:
    towns in <Map>/Levels/ (Black Coast), Art_Layers (Manicouagan), 070_
    Landscape chunks under a master, or a root-level landscape (Belaya).
    UWorld.streaming_levels is not script-exposed in Squad's UE 5.7 build,
    so the sublevels are discovered via the asset registry, compared against
    the levels actually loaded, and the missing ones are attached with
    EditorLevelUtils.add_level_to_world as always-loaded.
    """
    world = _get_world()
    subs = _find_sublevel_worlds(level_path, keep_water_layers)
    if keep_water_layers:
        water = [w for w in subs if _is_skipped_level(w)]
        if water:
            sh_log.get().detail("keeping water layers: %s" % ", ".join(water))
        else:
            sh_log.get().detail("no ocean/water weather layer found for %s"
                                % level_path)
    if not subs:
        if _landscape_count(world) == 0:
            sh_log.get().warn("no landscape AND no sublevel worlds found next "
                              "to %s" % level_path)
        return

    loaded_pkgs = set()
    loaded_known = False
    try:
        for lvl in unreal.EditorLevelUtils.get_levels(world):
            loaded_pkgs.add(str(lvl.get_path_name()).split(".")[0])
        loaded_known = True
    except Exception as exc:
        sh_log.get().warn("could not list loaded levels: %s" % exc)

    missing = [w for w in subs if w not in loaded_pkgs]
    if _landscape_count(world) == 0 or loaded_known:
        # Attach every missing non-variant level. Whitelisting "geometry-ish"
        # names was tried first and silently lost Mutaha's and Narva's
        # building levels - blacklist (in _SUBLEVEL_SKIP) is the safer side.
        to_add = missing
    else:
        to_add = []  # can't tell what's loaded; don't risk duplicates

    if not to_add:
        return
    sh_log.get().detail("attaching %d missing sublevels (of %d found)..."
                        % (len(to_add), len(subs)))
    added = 0
    for pkg in to_add:
        try:
            unreal.EditorLevelUtils.add_level_to_world(
                world, pkg, unreal.LevelStreamingAlwaysLoaded)
            added += 1
        except Exception as exc:
            sh_log.get().warn("add_level_to_world(%s): %s" % (pkg, exc))
    try:
        unreal.GameplayStatics.flush_level_streaming(world)
    except Exception:
        pass
    sh_log.get().detail("attached %d/%d; landscape proxies now: %d"
                        % (added, len(to_add), _landscape_count(world)))


def _ensure_levels_visible(level_path, keep_water_layers=False):
    """
    Hidden editor levels have NO collision: some masters (Mutaha, Narva)
    load their sublevels but keep them hidden, which leaves a scan with
    landscape-only hits. Force every loaded non-variant level visible.
    """
    world = _get_world()
    try:
        levels = list(unreal.EditorLevelUtils.get_levels(world))
    except Exception as exc:
        sh_log.get().warn("get_levels failed: %s" % exc)
        return
    targets = []
    for lvl in levels:
        pkg = str(lvl.get_path_name()).split(".")[0]
        name = pkg.rsplit("/", 1)[-1].lower()
        if pkg == level_path or name.startswith("l_000_master"):
            continue
        if _is_skipped_level(pkg, keep_water_layers):
            continue  # whitebox/old/lighting variants stay hidden
        targets.append(lvl)
    if not targets:
        return
    sh_log.get().detail("forcing %d loaded sublevels visible (hidden levels "
                        "have no collision)..." % len(targets))
    flags = [True] * len(targets)
    done = False
    for mode_name in ("DONT_MODIFY_DIRTY_FLAG", "MODIFY_ON_CHANGE"):
        mode = getattr(unreal.LevelVisibilityDirtyMode, mode_name, None)
        if mode is None:
            continue
        try:
            unreal.EditorLevelUtils.set_levels_visibility(
                targets, flags, False, mode)
            done = True
            break
        except Exception:
            continue
    if not done:
        try:  # older signature without the dirty-mode argument
            unreal.EditorLevelUtils.set_levels_visibility(targets, flags, False)
            done = True
        except Exception as exc:
            sh_log.get().warn("set_levels_visibility: %s" % exc)


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

    # One-map mode: export only the FIRST pending map, then exit without
    # writing the final report, so the .bat relaunch loop starts a fresh
    # editor process for every map. A long multi-map session can silently
    # lose collision on attached sublevels (Tallil exported with ~2% of its
    # structures as map ~22 of one session); a fresh process per map is the
    # reliable fix. The report - the .bat's stop signal - is only written by
    # the run that finds every map already exported.
    one_map = bool(os.environ.get("SQUADHEIGHT_ONE_MAP"))

    # Terrain-only mode: landscape heightfield without any meshes, written
    # to <output_root>/_terrain/<Map>/ (run_terrain_export.bat sets this).
    # Wins over any surface_mode in the config.
    terrain_only = bool(os.environ.get("SQUADHEIGHT_TERRAIN_ONLY"))
    # SQUADHEIGHT_TERRAIN_WATER=surface stops terrain columns at the water
    # surface instead of the seabed (output goes to _terrain_water/).
    terrain_water = os.environ.get("SQUADHEIGHT_TERRAIN_WATER", "").strip().lower()
    if terrain_water not in ("", "seabed", "surface"):
        raise RuntimeError("SQUADHEIGHT_TERRAIN_WATER must be 'seabed' or "
                           "'surface', got %r" % terrain_water)

    log = sh_log.start_session(
        output_root,
        "Batch export: %d map(s) -> %s%s%s"
        % (len(maps), output_root,
           ("  (terrain only, water: %s)" % (terrain_water or "seabed"))
           if terrain_only else "",
           "  (one map per editor run)" if one_map else ""))

    report = []
    finished_early = False
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
        if terrain_only:
            overrides["surface_mode"] = "terrain_only"
        if terrain_water:
            overrides["terrain_water"] = terrain_water

        # Resume support: skip maps that already have a finished export
        # (delete the map's output folder, or set SQUADHEIGHT_FORCE=1, to redo).
        cfg = export_heightmap.CONFIG
        surface_mode = overrides.get("surface_mode", cfg["surface_mode"])
        water_mode = overrides.get("terrain_water", cfg["terrain_water"])
        map_root = export_heightmap.map_output_root(
            output_root, surface_mode, water_mode)
        done_marker = os.path.join(map_root, name or level.rsplit("/", 1)[-1],
                                   "meta.json")
        label = "[%d/%d]" % (i + 1, len(maps))
        if os.path.isfile(done_marker) and not os.environ.get("SQUADHEIGHT_FORCE"):
            log.step("%s %s - already exported, skipping" % (label, level))
            report.append({"level": level, "status": "skipped", "seconds": 0})
            continue

        log.detail("%s loading level %s" % (label, level))
        t_map = time.time()
        try:
            keep_water = (surface_mode == "terrain_only"
                          and water_mode == "surface")
            if not _load_level(level, keep_water):
                raise RuntimeError("load_map returned false for %s" % level)
            out_dir = export_heightmap.run_export(
                output_dir=output_root, map_name=name, overrides=overrides,
                progress_label=label,
            )
            if out_dir is None:
                raise RuntimeError("export cancelled")
            report.append({
                "level": level, "status": "ok", "output": out_dir,
                "seconds": round(time.time() - t_map, 1),
            })
        except Exception as exc:
            log.error("FAILED %s: %s" % (level, exc))
            log.detail(traceback.format_exc())
            report.append({
                "level": level, "status": "failed", "error": str(exc),
                "seconds": round(time.time() - t_map, 1),
            })
            # Keep going - one broken map should not kill the whole batch.
        finally:
            # Memory accumulates across map loads and eventually OOMs the
            # editor (seen after ~9 maps on a 32 GB machine) - collect
            # aggressively between maps.
            try:
                unreal.SystemLibrary.collect_garbage()
            except Exception:
                pass

        if one_map and report and report[-1]["status"] in ("ok", "failed"):
            log.step("one-map mode: exiting after %s; the relaunch loop "
                     "continues with the next map." % level)
            finished_early = True
            break

    # Summary + machine-readable report for CI-style usage.
    ok = sum(1 for r in report if r["status"] in ("ok", "skipped"))
    log.phase("Batch finished: %d/%d ok in %s"
              % (ok, len(report), sh_log.fmt_duration(time.time() - t_batch)))
    for r in report:
        line = "%-8s %s (%s)" % (
            r["status"].upper(), r["level"], sh_log.fmt_duration(r["seconds"]))
        (log.error if r["status"] == "failed" else log.step)(line)

    if not finished_early:
        if not os.path.isdir(output_root):
            os.makedirs(output_root)
        report_path = os.path.join(output_root, "batch_report.json")
        with open(report_path, "w") as f:
            json.dump({"finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                     time.gmtime()),
                       "results": report}, f, indent=2)
        log.detail("wrote %s" % report_path)

    log.close()
    if ok != len(report):
        # Non-zero exit so the .bat / CI can detect partial failure.
        sys.exit(1)


if __name__ == "__main__":
    main()
