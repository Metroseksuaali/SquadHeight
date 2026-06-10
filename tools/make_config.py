"""
make_config.py - generate maps_config.json by scanning the SDK's map assets.

Runs INSIDE the Unreal editor (any map can be open; nothing is loaded).
It lists all World assets under /Game/Maps via the asset registry, matches
them against the maps in squadcalc_bounds.json, and writes a ready-to-run
maps_config.json next to this script.

    py "<path-to-repo>/tools/make_config.py"

Review the output before running the batch: for each map it picks the world
asset with the shortest name inside the matched folder (the persistent /
art level), and prints the alternatives so you can swap in a different layer
if the guess is wrong. Unmatched maps are listed at the end - fill those in
by hand.
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

# Candidate paths containing any of these are gameplay/lighting/dev variants,
# not the base art level we want to trace. Used as a soft filter: if it
# eliminates every candidate for a map, the unfiltered list is used instead.
EXCLUDE_SUBSTRINGS = [
    "/development/", "/gameplay_layers/", "/lighting_layers/",
    "/weatherlayer", "/vfx", "entrymap", "_gpu", "/ll_", "/wl_",
    "blockout", "_test", "profile",
]


def _norm(s):
    return s.lower().replace("_", "").replace("-", "")


def rank_candidate(pkg):
    """Lower is better: folder-named level, then GEO/master/city variants."""
    parts = pkg.split("/")
    name, folder = parts[-1], parts[3] if len(parts) > 3 else ""
    n = _norm(name)
    if n == _norm(folder):
        score = 0
    elif any(k in n for k in ("geo", "master", "city")):
        score = 1
    else:
        score = 2
    return (score, len(name), pkg)


def find_world_assets():
    """All World asset package names under /Game/Maps."""
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    ar_filter = None
    try:  # UE5.1+: class paths
        ar_filter = unreal.ARFilter(
            class_paths=[unreal.TopLevelAssetPath("/Script/Engine", "World")],
            package_paths=["/Game/Maps"], recursive_paths=True)
    except Exception:
        pass
    if ar_filter is None:
        try:  # older engines: class names
            ar_filter = unreal.ARFilter(
                class_names=["World"],
                package_paths=["/Game/Maps"], recursive_paths=True)
        except Exception:
            raise RuntimeError("could not build an ARFilter on this engine")
    assets = unreal.AssetRegistryHelpers.get_asset_registry().get_assets(ar_filter)
    return sorted({str(a.package_name) for a in assets})


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
