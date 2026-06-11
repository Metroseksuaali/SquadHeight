"""
Package the exported heightmaps into release assets for GitHub Releases.

Produces three zips under output/_release/:

  squadcalc_heightmaps_500.zip   img/maps/<slug>/heightmap.json  (500x500
                                 drop-in tree, exactly SquadCalc's API layout)
  heightmaps_1m_fullres.zip      <Map>/heightmap.json + <Map>/meta.json
  heightmap_images_16bit_png.zip <Map>/heightmap.png

Each zip carries a NOTES.txt. Runs outside Unreal, stdlib only.

    python tools/build_release_zips.py
"""

import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUTPUT = os.path.join(ROOT, "output")
DEST = os.path.join(OUTPUT, "_release")

NOTES = """\
SquadHeight - true-surface heightmaps for Squad maps
====================================================

What this is
------------
Heightmaps exported from the Squad SDK by ray-tracing the actual world
collision, so buildings, bridges and rocks are included (foliage is excluded).
They are meant as a more accurate drop-in for SquadCalc (squadcalc.app), whose
stock heightmaps come from the terrain Landscape only.

Generated with the open-source tool at:
  https://github.com/Metroseksuaali/SquadHeight
Source: Squad Editor Public Testing (Mod SDK), UE5.

License / rights
----------------
The export TOOL is MIT licensed. THIS DATA is not: it is a derivative of Squad,
(c) Offworld Industries, produced with the official Mod SDK. Squad and its
assets are the property of Offworld Industries. This is an unofficial community
project, not affiliated with or endorsed by OWI, shared for use with tools such
as SquadCalc and subject to OWI's EULA and modding terms.

Format
------
* heightmap.json     : a 2D JSON array, rows then columns, heights in METERS,
                       minimum normalized to 0. Absolute meters (not 0-255).
* heightmap_500.json : the same data downsampled to 500x500. SquadCalc loads a
                       file named heightmap.json and currently hardcodes width
                       500, so the drop-in zip ships the 500 grid under that
                       name, laid out as img/maps/<map>/heightmap.json.
* heightmap.png      : 16-bit grayscale render for inspection (per-map
                       normalized; NOT the source of truth - use the JSON).
* meta.json          : world bounds, resolution, z-offset and PNG scaling.

Reading a value: row index runs over the minimap's vertical axis, column over
the horizontal axis. world_z = value + meta.z_offset_m. Mortar math only needs
height differences, so the offset rarely matters.

What the values mean at the edges
---------------------------------
* Water and out-of-play ground read as the map minimum (0). A flat sea surface
  is the correct value for mortar math.
* Where the SDK map's landscape does not fill the whole minimap square, the
  uncovered border is 0. The PLAYABLE AREA IS ACCURATE; only the out-of-play
  border lacks data. Most visible on Chora (non-square landscape). No terrain
  is invented there - extending edges would fabricate false plateaus.
* Heights are truer than the old Landscape data, including peaks: where the
  stock heightmaps clip tall terrain, these keep the real value (e.g. Skorpo
  peaks to ~1064 m, not ~557 m).
"""


def add_notes(zf):
    zf.writestr("NOTES.txt", NOTES)


def maps():
    for name in sorted(os.listdir(OUTPUT)):
        d = os.path.join(OUTPUT, name)
        if os.path.isdir(d) and not name.startswith("_"):
            yield name, d


def build():
    os.makedirs(DEST, exist_ok=True)
    z500 = os.path.join(DEST, "squadcalc_heightmaps_500.zip")
    zfull = os.path.join(DEST, "heightmaps_1m_fullres.zip")
    zpng = os.path.join(DEST, "heightmap_images_16bit_png.zip")

    with zipfile.ZipFile(z500, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps():
            src = os.path.join(d, "heightmap_500.json")
            if os.path.isfile(src):
                zf.write(src, "img/maps/%s/heightmap.json" % name.lower())
                n += 1
        print("squadcalc_heightmaps_500.zip : %d maps" % n)

    with zipfile.ZipFile(zfull, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps():
            hm = os.path.join(d, "heightmap.json")
            mt = os.path.join(d, "meta.json")
            if os.path.isfile(hm):
                zf.write(hm, "%s/heightmap.json" % name)
                if os.path.isfile(mt):
                    zf.write(mt, "%s/meta.json" % name)
                n += 1
        print("heightmaps_1m_fullres.zip : %d maps (this one is large)" % n)

    with zipfile.ZipFile(zpng, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps():
            png = os.path.join(d, "heightmap.png")
            if os.path.isfile(png):
                zf.write(png, "%s/heightmap.png" % name)
                n += 1
        print("heightmap_images_16bit_png.zip : %d maps" % n)

    for p in (z500, zfull, zpng):
        print("  %8.1f MB  %s" % (os.path.getsize(p) / 1048576, os.path.basename(p)))


if __name__ == "__main__":
    build()
