"""
Package the exported heightmaps into release assets for GitHub Releases.

Produces five zips under output/_release/:

  squadcalc_heightmaps_500.zip   img/maps/<slug>/heightmap.json  (500x500
                                 drop-in tree, exactly SquadCalc's API layout)
  heightmaps_1m_fullres.zip      <Map>/heightmap.json + <Map>/meta.json
  heightmap_images_16bit_png.zip <Map>/heightmap.png (16-bit greyscale)
  heightmap_images_8bit_png.zip  <Map>/heightmap.png (8-bit greyscale, lossier)
  heightmap_images_rb_png.zip    <Map>/heightmap.png (8-bit R+B encoded)

With --terrain, packages the ground-only export (output/_terrain/, written
by run_terrain_export.bat) instead, as the same five zips prefixed terrain_.
--terrain --water packages the variant that stops at the water surface
(output/_terrain_water/, run_terrain_export.bat water), prefixed terrain_water_.

Each zip carries a NOTES.txt. Runs outside Unreal, stdlib only.

    python tools/build_release_zips.py [--terrain [--water]]
"""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUTPUT = os.path.join(ROOT, "output")
DEST = os.path.join(OUTPUT, "_release")
# export_heightmap.TERRAIN_OUTPUT_SUBDIR / TERRAIN_WATER_OUTPUT_SUBDIR
TERRAIN_OUTPUT = os.path.join(OUTPUT, "_terrain")
TERRAIN_WATER_OUTPUT = os.path.join(OUTPUT, "_terrain_water")

SOURCE_LINE = """
Generated with the open-source tool at:
  https://github.com/Metroseksuaali/SquadHeight
Source: Squad Editor Public Testing (Mod SDK), UE5.

"""

INTRO_SURFACE = """\
SquadHeight - true-surface heightmaps for Squad maps
====================================================

What this is
------------
Heightmaps exported from the Squad SDK by ray-tracing the actual world
collision, so buildings, bridges and rocks are included (foliage is excluded).
They are meant as a more accurate drop-in for SquadCalc (squadcalc.app), whose
stock heightmaps come from the terrain Landscape only.
""" + SOURCE_LINE

INTRO_TERRAIN = """\
SquadHeight - ground-only (terrain) heightmaps for Squad maps
=============================================================

What this is
------------
The terrain of each map only: the Landscape heightfield plus the meshes the
map uses as terrain (surround mountains, baked landscape copies). Buildings,
bridges, walls, rocks and props are ignored, so the values are the bare
ground. {water}

Meant as a base for 3D modelling where structures are added on top - NOT for
mortar math, use the true-surface heightmaps for that. Same bounds, grid,
orientation and file formats as the true-surface release, so both line up
cell for cell.
Heights are normalized to THIS export's own minimum - compare the two via
world_z = value + meta.z_offset_m, not raw values.
""" + SOURCE_LINE

NOTES_BODY = """\
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
* heightmap.png      : a preview render - this zip's name says which kind:
                       - 16-bit zip : 16-bit grayscale, gray = height_m *
                         meta.json's png16_meters_per_unit.
                       - 8-bit zip  : 8-bit grayscale, smaller but lossier
                         (256 levels) - gray = height_m * meta.json's
                         png8_meters_per_unit.
                       - R+B zip    : 8-bit RGB, NOT grayscale - height is
                         split across the R and B channels (G is always 0)
                         for ~511 levels at the same byte depth as the 8-bit
                         zip - decode raw = 255 + R - B (0..510), then
                         height_m = raw * meta.json's rb_meters_per_unit.
                       No PNG is the source of truth - use the JSON.
* meta.json          : world bounds, resolution, z-offset and PNG scaling.

Reading a value: row index runs over the minimap's vertical axis, column over
the horizontal axis. world_z = value + meta.z_offset_m. Mortar math only needs
height differences, so the offset rarely matters.

"""

EDGES_SURFACE = """\
What the values mean at the edges
---------------------------------
* Water and out-of-play ground read as the map minimum (0). A flat sea surface
  is the correct value for mortar math.
* Out-of-play surround geometry that exists in the SDK (e.g. Chora/Kamdesh/
  Lashkar mountains, Tallil background terrain, Sanxian sea) is captured.
  Where the square truly contains nothing (parts of Black Coast, Harju,
  Kohat, Kokan, Skorpo, Mestia edges) the border reads 0. The PLAYABLE AREA
  IS ACCURATE; no terrain is invented - extending edges would fabricate
  false plateaus.
* Heights are truer than the old Landscape data, including peaks: where the
  stock heightmaps clip tall terrain, these keep the real value (e.g. Skorpo
  peaks to ~1064 m, not ~557 m).
"""

EDGES_TERRAIN = """\
What the values mean at the edges
---------------------------------
* Only terrain counts. Where the minimap square has no terrain underneath
  (e.g. open sea without a seabed), thin gaps are filled from their
  neighbours and larger empty regions read as the map minimum (0). No
  terrain is invented.
"""

WATER_SEABED = "Water is ignored too: under water the value is the seabed."
WATER_SURFACE = ("Water stops the scan: over the sea, rivers and lakes the\n"
                 "value is the water surface, never the bed below it.")

NOTES = INTRO_SURFACE + NOTES_BODY + EDGES_SURFACE
NOTES_TERRAIN = (INTRO_TERRAIN.replace("{water}", WATER_SEABED)
                 + NOTES_BODY + EDGES_TERRAIN)
NOTES_TERRAIN_WATER = (INTRO_TERRAIN.replace("{water}", WATER_SURFACE)
                       + NOTES_BODY + EDGES_TERRAIN)


def maps(output):
    for name in sorted(os.listdir(output)):
        d = os.path.join(output, name)
        if os.path.isdir(d) and not name.startswith("_"):
            yield name, d


def build(terrain=False, water=False):
    if terrain and water:
        output, notes, prefix = TERRAIN_WATER_OUTPUT, NOTES_TERRAIN_WATER, "terrain_water_"
    elif terrain:
        output, notes, prefix = TERRAIN_OUTPUT, NOTES_TERRAIN, "terrain_"
    else:
        output, notes, prefix = OUTPUT, NOTES, ""
    if not os.path.isdir(output):
        raise SystemExit("no export found at %s" % output)

    def add_notes(zf):
        zf.writestr("NOTES.txt", notes)

    os.makedirs(DEST, exist_ok=True)
    z500 = os.path.join(DEST, prefix + "squadcalc_heightmaps_500.zip")
    zfull = os.path.join(DEST, prefix + "heightmaps_1m_fullres.zip")
    zpng = os.path.join(DEST, prefix + "heightmap_images_16bit_png.zip")
    zpng8 = os.path.join(DEST, prefix + "heightmap_images_8bit_png.zip")
    zpngrb = os.path.join(DEST, prefix + "heightmap_images_rb_png.zip")

    with zipfile.ZipFile(z500, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps(output):
            src = os.path.join(d, "heightmap_500.json")
            if os.path.isfile(src):
                zf.write(src, "img/maps/%s/heightmap.json" % name.lower())
                n += 1
        print(prefix + "squadcalc_heightmaps_500.zip : %d maps" % n)

    with zipfile.ZipFile(zfull, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps(output):
            hm = os.path.join(d, "heightmap.json")
            mt = os.path.join(d, "meta.json")
            if os.path.isfile(hm):
                zf.write(hm, "%s/heightmap.json" % name)
                if os.path.isfile(mt):
                    zf.write(mt, "%s/meta.json" % name)
                n += 1
        print(prefix + "heightmaps_1m_fullres.zip : %d maps (this one is large)" % n)

    with zipfile.ZipFile(zpng, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps(output):
            png = os.path.join(d, "heightmap_16bit.png")
            if not os.path.isfile(png):
                png = os.path.join(d, "heightmap.png")  # older exports used this name
            if os.path.isfile(png):
                zf.write(png, "%s/heightmap.png" % name)
                n += 1
        print(prefix + "heightmap_images_16bit_png.zip : %d maps" % n)

    with zipfile.ZipFile(zpng8, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps(output):
            png = os.path.join(d, "heightmap_8bit.png")
            if os.path.isfile(png):
                zf.write(png, "%s/heightmap.png" % name)
                n += 1
        print(prefix + "heightmap_images_8bit_png.zip : %d maps" % n)

    with zipfile.ZipFile(zpngrb, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        add_notes(zf)
        n = 0
        for name, d in maps(output):
            png = os.path.join(d, "heightmap_rb.png")
            if os.path.isfile(png):
                zf.write(png, "%s/heightmap.png" % name)
                n += 1
        print(prefix + "heightmap_images_rb_png.zip : %d maps" % n)

    for p in (z500, zfull, zpng, zpng8, zpngrb):
        print("  %8.1f MB  %s" % (os.path.getsize(p) / 1048576, os.path.basename(p)))


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--water" in args and "--terrain" not in args:
        raise SystemExit("--water only applies together with --terrain")
    build(terrain="--terrain" in args, water="--water" in args)
