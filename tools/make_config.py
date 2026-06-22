"""
make_config.py - generate maps_config.json by scanning the SDK's map assets.

Reads the asset registry only - nothing is loaded, no map needs to be open.
It lists all World assets under /Game/Maps (and the game-feature plugin
roots) via the asset registry, matches them against the maps in
squadcalc_bounds.json, and writes a ready-to-run maps_config.json next to
this script.

HOW TO RUN:

  Headless (no editor window - run ../run_make_config.bat, which reuses the
  paths in settings.bat just like the batch exporter):
      UnrealEditor-Cmd.exe <Project.uproject> -run=pythonscript
          -script="<path-to-repo>/tools/make_config.py"
          -stdout -FullStdOutLogOutput -Unattended -NoSplash
  The chosen level for each map (and its alternatives) is printed to the log,
  so you can still review the picks afterwards in the console output.

  Interactively, in the editor's Output Log console at the bottom:

    Cmd mode (recommended - the dropdown shows "Cmd"):
        py "<path-to-repo>/tools/make_config.py"

    Python mode (the dropdown shows "Python"):
        exec(open(r"<path-to-repo>/tools/make_config.py").read())

    Replace <path-to-repo> with the folder you cloned into, e.g.
        py "C:/tools/SquadHeight/tools/make_config.py"

Review the output before running the batch: for each map it picks the base
art level (the one named like its folder, then GEO/master variants),
skipping Development / Gameplay / Lighting layers, and prints the
alternatives so you can swap one in if a guess is wrong. Maps with no match
are listed at the end together with the unclaimed /Game/Maps folders, so you
can add them by hand.
"""

import json
import os
import sys

import unreal

try:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # exec()'d into a console without __file__ - assume the standard layout
    # and complain clearly if the bounds file isn't found later.
    _SCRIPT_DIR = os.getcwd()

# SquadCalc map name -> substring(s) to look for in the SDK package path.
# SDK folder names don't always match SquadCalc names (e.g. AlBasrah lives
# in .../BASRAH_CITY/...), hence the aliases.
NAME_HINTS = {
    "AlBasrah": ["basrah"],
    "Anvil": ["anvil"],
    "Belaya": ["belaya"],
    "BlackCoast": ["blackcoast", "black_coast"],
    "Chora": ["chora"],
    "Fallujah": ["fallujah"],
    "FoolsRoad": ["fools"],
    "GooseBay": ["goose"],
    "Gorodok": ["gorodok"],
    "Jensen": ["jensen"],
    "Harju": ["harju"],
    "Kamdesh": ["kamdesh"],
    "Kohat": ["kohat"],
    "Kokan": ["kokan"],
    "Lashkar": ["lashkar"],
    "Logar": ["logar"],
    "Manicouagan": ["manic"],
    "Mestia": ["mestia"],
    "Mutaha": ["mutaha"],
    "Narva": ["narva"],
    "Pacific": ["pacific"],
    "Sanxian": ["sanxian"],
    "Skorpo": ["skorpo"],
    "Sumari": ["sumari"],
    "Tallil": ["tallil"],
    "Yehorivka": ["yeho"],
}
SKIP = {"Narva_f"}  # flooded Narva variant: same bounds, usually same level

# Newer/reworked maps ship as game feature PLUGINS with their own content
# roots (visible at editor startup as "Mounting Project plugin Al_Basrah"
# etc.), not under /Game/Maps. When a map has candidates under its plugin
# root, prefer those - anything left under /Game/Maps for it is a legacy
# leftover (e.g. the pre-rework /Game/Maps/BASRAH_CITY).
PREFERRED_ROOTS = {
    "AlBasrah": "/Al_Basrah/",
    "Harju": "/Harju/",
    "BlackCoast": "/BlackCoast/",
    "Sanxian": "/SanxianIslands/",
}

# Candidate paths containing any of these are gameplay/lighting/dev variants,
# not the base art level we want to trace. Used as a soft filter: if it
# eliminates every candidate for a map, the unfiltered list is used instead.
EXCLUDE_SUBSTRINGS = [
    "/development/", "/gameplay_layers/", "/lighting_layers/",
    "/weatherlayer", "/vfx", "entrymap", "_gpu", "/ll_", "/wl_",
    "blockout", "_test", "profile", "/coop/", "/freemissions/",
    "/automation/", "/sound_layers/", "/sounds/",
]


def _norm(s):
    return s.lower().replace("_", "").replace("-", "")


def rank_candidate(pkg):
    """Lower is better: folder-named level, then GEO/master/city variants."""
    parts = pkg.split("/")
    name = parts[-1]
    n = _norm(name)
    # A level named like any directory on its path (Chora/Chora,
    # /Al_Basrah/Maps/AlBasrah_Level-ish) is the base level.
    dir_norms = {_norm(p) for p in parts[1:-1] if p}
    if any(k in n for k in ("coop", "whitebox", "nolandscape", "playtest")):
        score = 3  # special-purpose master variants, not the base level
    elif n in dir_norms or n.replace("level", "") in dir_norms:
        score = 0
    elif any(k in n for k in ("geo", "master", "city", "level")):
        score = 1
    else:
        score = 2
    return (score, len(name), pkg)


def _ensure_assets_scanned(registry):
    """
    Make sure the asset registry is fully populated before we query it.

    In the interactive editor the initial scan finished long ago, so this is
    a no-op. In the -run=pythonscript commandlet the startup scan can still be
    in flight when this script runs (unlike batch_export.py, which only hits
    the registry AFTER load_map gives it time): a naive get_assets() then
    returns a partial list and silently drops maps - typically the plugin
    roots (/Al_Basrah/ etc.) that get scanned late, which would land them in
    the "unmatched" list for no real reason.

    Force the map roots scanned synchronously, then let any background scan
    finish. Every call is best-effort - the registry API surface varies
    across engine versions, same as the ARFilter fallback below.
    """
    roots = ["/Game/Maps"] + [r.rstrip("/") for r in PREFERRED_ROOTS.values()]
    try:
        registry.scan_paths_synchronous(roots, True)
    except Exception as exc:
        unreal.log_warning("[SquadHeight] scan_paths_synchronous(%s) failed: %s"
                           % (roots, exc))
    # Let any still-running full scan complete (covers future plugin roots not
    # in PREFERRED_ROOTS). wait_for_completion is cheap when already done;
    # search_all_assets(True) is the heavier fallback if it's unavailable.
    for meth, args in (("wait_for_completion", ()), ("search_all_assets", (True,))):
        fn = getattr(registry, meth, None)
        if fn is None:
            continue
        try:
            fn(*args)
            return
        except Exception:
            continue


def find_world_assets():
    """
    All World asset package names that live in a Maps folder - both the
    classic /Game/Maps tree and game-feature-plugin roots like
    /Al_Basrah/Maps, /Harju/Maps, /BlackCoast/Maps, /SanxianIslands/Maps.
    """
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    _ensure_assets_scanned(registry)
    ar_filter = None
    try:  # UE5.1+: class paths
        ar_filter = unreal.ARFilter(
            class_paths=[unreal.TopLevelAssetPath("/Script/Engine", "World")],
            recursive_paths=True)
    except Exception:
        pass
    if ar_filter is None:
        try:  # older engines: class names
            ar_filter = unreal.ARFilter(class_names=["World"],
                                        recursive_paths=True)
        except Exception:
            raise RuntimeError("could not build an ARFilter on this engine")
    assets = registry.get_assets(ar_filter)
    worlds = {str(a.package_name) for a in assets}
    return sorted(w for w in worlds
                  if "/maps/" in w.lower() and not w.startswith("/Engine"))


def main():
    with open(os.path.join(_SCRIPT_DIR, "squadcalc_bounds.json")) as f:
        bounds = {k: v for k, v in json.load(f).items()
                  if not k.startswith("_")}

    worlds = find_world_assets()
    unreal.log("[SquadHeight] %d world assets under /Game/Maps" % len(worlds))

    maps = []
    unmatched = []
    for name, b in bounds.items():
        if name in SKIP:
            continue
        hints = NAME_HINTS.get(name, [name.lower()])
        cands = [w for w in worlds
                 if any(h in w.lower() for h in hints)]
        if not cands:
            unmatched.append(name)
            continue
        # Drop dev/gameplay/lighting variants, then prefer the level named
        # like its folder (the base art level), then GEO/master variants.
        filtered = [w for w in cands
                    if not any(x in w.lower() for x in EXCLUDE_SUBSTRINGS)]
        if filtered:
            cands = filtered
        # Reworked maps: prefer the plugin content root over legacy leftovers.
        preferred_root = PREFERRED_ROOTS.get(name)
        if preferred_root:
            in_plugin = [w for w in cands if w.startswith(preferred_root)]
            if in_plugin:
                cands = in_plugin
        cands.sort(key=rank_candidate)
        level = cands[0]
        maps.append({
            "level": level,
            "name": name,
            "overrides": {"bounds_m": b},
        })
        unreal.log("[SquadHeight] %-12s -> %s" % (name, level))
        for alt in cands[1:6]:
            unreal.log("[SquadHeight]                (alt: %s)" % alt)
        if len(cands) > 6:
            unreal.log("[SquadHeight]                (+%d more)" % (len(cands) - 6))

    config = {
        "_comment": ["generated by make_config.py - review the level paths,",
                     "especially where alternatives were printed"],
        "output_root": "",
        "defaults": {
            "resolution_m": 1.0,
            "surface_mode": "topmost",
            "downsample_to": 500,
            "trace_top_margin_m": 500.0,
        },
        "maps": maps,
    }
    out_path = os.path.join(_SCRIPT_DIR, "maps_config.json")
    with open(out_path, "w") as f:
        json.dump(config, f, indent=2)

    unreal.log("[SquadHeight] wrote %s with %d maps" % (out_path, len(maps)))
    if unmatched:
        unreal.log_warning("[SquadHeight] NOT matched (add by hand): %s"
                           % ", ".join(unmatched))
        # Help identify them: list map folders no matched level lives in.
        claimed = {m["level"].split("/")[3] for m in maps}
        folders = sorted({w.split("/")[3] for w in worlds if len(w.split("/")) > 3}
                         - claimed)
        unreal.log("[SquadHeight] unclaimed folders under /Game/Maps: %s"
                   % ", ".join(folders))


if __name__ == "__main__":
    main()
