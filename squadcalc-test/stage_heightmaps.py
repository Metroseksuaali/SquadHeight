"""
Stage our exported heightmaps for the local SquadCalc container.

Copies  output/<Map>/heightmap_500.json
   to   squadcalc-test/heightmaps/img/maps/<slug>/heightmap.json

<slug> is the lowercase map-folder name, which matches SquadCalc's mapURL
slugs (albasrah, blackcoast, foolsroad, ...). nginx serves this tree and
falls back to production for any map we don't stage.

SquadCalc hard-codes the heightmap width to 500, so we ship the 500x500
drop-in (heightmap_500.json) under the name it fetches (heightmap.json).

    python stage_heightmaps.py
"""

import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.normpath(os.path.join(HERE, "..", "output"))
DEST_ROOT = os.path.join(HERE, "heightmaps", "img", "maps")

# Our folder name -> SquadCalc slug. Lowercase works for every current map;
# add explicit entries here if a future name ever diverges.
SPECIAL = {}


def slug_for(name):
    return SPECIAL.get(name, name.lower())


def main():
    if not os.path.isdir(OUTPUT):
        raise SystemExit("output/ folder not found at %s" % OUTPUT)

    staged = 0
    for name in sorted(os.listdir(OUTPUT)):
        src_dir = os.path.join(OUTPUT, name)
        if not os.path.isdir(src_dir):
            continue
        src = os.path.join(src_dir, "heightmap_500.json")
        if not os.path.isfile(src):
            print("  skip %-14s (no heightmap_500.json)" % name)
            continue
        slug = slug_for(name)
        dst_dir = os.path.join(DEST_ROOT, slug)
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, "heightmap.json")
        shutil.copyfile(src, dst)
        size_kb = os.path.getsize(dst) / 1024.0
        print("  staged %-14s -> img/maps/%s/heightmap.json (%.0f KB)"
              % (name, slug, size_kb))

        # Full-res surface as 16-bit PNG + its meta, for the range-fan HD
        # sampler (heights = pixel * png16_meters_per_unit). Optional: the
        # front-end falls back to the 500 grid when these are missing.
        png = os.path.join(src_dir, "heightmap_16bit.png")
        meta = os.path.join(src_dir, "meta.json")
        if os.path.isfile(png) and os.path.isfile(meta):
            shutil.copyfile(png, os.path.join(dst_dir, "heightmap_hd.png"))
            shutil.copyfile(meta, os.path.join(dst_dir, "hd_meta.json"))
            print("         %-14s -> img/maps/%s/heightmap_hd.png (%.1f MB)"
                  % ("", slug, os.path.getsize(png) / 1024.0 / 1024.0))
        staged += 1

    print("\nStaged %d heightmaps into %s" % (staged, DEST_ROOT))


if __name__ == "__main__":
    main()
