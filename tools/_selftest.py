"""
_selftest.py - offline test of everything testable WITHOUT the Unreal editor.

Stubs the `unreal` module, then exercises export_heightmap's pure-Python
helpers (JSON formatting, orientation, downsample) plus png16. Run with any
system Python 3:    python tools/_selftest.py
"""
import json
import os
import struct
import sys
import tempfile
import types
import zlib
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- Stub the unreal module so export_heightmap can be imported -----------
unreal_stub = types.ModuleType("unreal")
for name in (
    "Vector", "Actor", "ActorComponent", "InstancedStaticMeshComponent",
    "StaticMeshComponent", "TraceTypeQuery", "DrawDebugTrace",
):
    setattr(unreal_stub, name, type(name, (), {}))
unreal_stub.log = print
unreal_stub.log_warning = print
unreal_stub.log_error = print
sys.modules["unreal"] = unreal_stub

import export_heightmap as eh  # noqa: E402
import png16  # noqa: E402

failures = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (("  " + detail) if detail else ""))
    if not cond:
        failures.append(label)


# ---- _fmt_height matches legacy style --------------------------------------
check("fmt 16.32", eh._fmt_height(16.32, 2) == "16.32")
check("fmt 0.0 -> 0", eh._fmt_height(0.0, 2) == "0")
check("fmt 7.50 -> 7.5", eh._fmt_height(7.5, 2) == "7.5")
check("fmt 10.004 rounds", eh._fmt_height(10.004, 2) == "10")

# ---- _write_json produces valid JSON in the legacy shape --------------------
tmp = tempfile.mkdtemp()
rows = [array("f", [0.0, 1.234, 16.32]), array("f", [2.0, 3.5, 4.999])]
jpath = os.path.join(tmp, "hm.json")
eh._write_json(jpath, rows, 2)
parsed = json.load(open(jpath))
check("json shape", len(parsed) == 2 and len(parsed[0]) == 3)
check("json values", parsed[0] == [0, 1.23, 16.32] and parsed[1] == [2, 3.5, 5])

# ---- orientation ------------------------------------------------------------
grid = [array("f", [1, 2]), array("f", [3, 4]), array("f", [5, 6])]  # 3 rows x 2 cols
t = eh._apply_orientation([array("f", r) for r in grid],
                          {"transpose": True, "flip_rows": False, "flip_cols": False})
check("transpose dims", len(t) == 2 and len(t[0]) == 3)
check("transpose vals", list(t[0]) == [1, 3, 5] and list(t[1]) == [2, 4, 6])
fr = eh._apply_orientation([array("f", r) for r in grid],
                           {"transpose": False, "flip_rows": True, "flip_cols": True})
check("flip vals", list(fr[0]) == [6, 5] and list(fr[2]) == [2, 1])

# ---- downsample ---------------------------------------------------------------
big = [array("f", [float(r * 10 + c) for c in range(10)]) for r in range(10)]
small = eh._downsample(big, 5)
check("downsample dims", len(small) == 5 and len(small[0]) == 5)
check("downsample nearest", small[0][0] == 0.0 and small[4][4] == 88.0)

# ---- png16: write 16-bit and 8-bit, then decode by hand and compare ----------
def decode_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "bad signature"
    pos, chunks = 8, {}
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])[0]
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, "bad CRC in " + repr(tag)
        chunks.setdefault(tag, b"")
        chunks[tag] += payload
        pos += 12 + length
    w, h, depth, ctype = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    raw = zlib.decompress(chunks[b"IDAT"])
    channels = 3 if ctype == 2 else 1  # only grayscale(0)/truecolor(2) used here
    bpp = (depth // 8) * channels
    stride = 1 + w * bpp
    out = []
    for r in range(h):
        line = raw[r * stride:(r + 1) * stride]
        assert line[0] == 0, "unexpected filter type"
        if depth == 16:
            out.append(list(struct.unpack(">%dH" % (w * channels), line[1:])))
        else:
            out.append(list(line[1:]))
    return w, h, depth, ctype, out

px16 = [[0, 1000, 65535], [123, 40000, 7]]
p16 = os.path.join(tmp, "t16.png")
png16.write_gray_png(p16, 3, 2, px16, bit_depth=16)
w, h, d, ct, decoded = decode_png(p16)
check("png16 header", (w, h, d, ct) == (3, 2, 16, 0))
check("png16 pixels", decoded == px16)

px8 = [[0, 128, 255], [1, 2, 3]]
p8 = os.path.join(tmp, "t8.png")
png16.write_gray_png(p8, 3, 2, px8, bit_depth=8)
w, h, d, ct, decoded = decode_png(p8)
check("png8 header", (w, h, d, ct) == (3, 2, 8, 0))
check("png8 pixels", decoded == px8)

# ---- png16.write_rgb_png: 2x2 truecolor round-trip ---------------------------
px_rgb = [[10, 0, 245, 255, 0, 0], [0, 0, 255, 5, 0, 0]]  # 2 px/row, RGB
prgb = os.path.join(tmp, "trgb.png")
png16.write_rgb_png(prgb, 2, 2, px_rgb)
w, h, d, ct, decoded_rgb = decode_png(prgb)
check("rgb header", (w, h, d, ct) == (2, 2, 8, 2))
check("rgb pixels", decoded_rgb == px_rgb, str(decoded_rgb))

# ---- _write_png scaling round-trip -------------------------------------------
hp = os.path.join(tmp, "hm.png")
hrows = [array("f", [0.0, 10.0]), array("f", [5.0, 20.0])]
scale = eh._write_png(hp, hrows, 0.0, 20.0)
_, _, _, _, decoded = decode_png(hp)
check("heightmap png scale", abs(scale - 65535.0 / 20.0) < 1e-6)
check("heightmap png values",
      decoded[0][0] == 0 and decoded[1][1] == 65535
      and abs(decoded[0][1] - 32768) <= 1 and abs(decoded[1][0] - 16384) <= 1,
      str(decoded))

hp8 = os.path.join(tmp, "hm8.png")
scale8 = eh._write_png(hp8, hrows, 0.0, 20.0, bit_depth=8, compress_level=9)
_, _, d8, _, decoded8 = decode_png(hp8)
check("heightmap png8 header", d8 == 8)
check("heightmap png8 scale", abs(scale8 - 255.0 / 20.0) < 1e-6)
check("heightmap png8 values",
      decoded8[0][0] == 0 and decoded8[1][1] == 255
      and abs(decoded8[0][1] - 128) <= 1 and abs(decoded8[1][0] - 64) <= 1,
      str(decoded8))

# ---- _write_rb_png scaling round-trip (R+B encoding, raw = 255 + R - B) ------
hp_rb = os.path.join(tmp, "hm_rb.png")
scale_rb = eh._write_rb_png(hp_rb, hrows, 0.0, 20.0, compress_level=9)
_, _, d_rb, ct_rb, decoded_rb = decode_png(hp_rb)
check("heightmap rb header", (d_rb, ct_rb) == (8, 2))
check("heightmap rb scale", abs(scale_rb - 510.0 / 20.0) < 1e-6)


def _rb_raw(pixel_bytes, px_idx):
    r, g, b = pixel_bytes[px_idx * 3:px_idx * 3 + 3]
    return 255 + r - b


check("heightmap rb values",
      _rb_raw(decoded_rb[0], 0) == 0          # v=0.0  -> raw 0
      and _rb_raw(decoded_rb[0], 1) == 255    # v=10.0 -> raw 255
      and abs(_rb_raw(decoded_rb[1], 0) - 128) <= 1  # v=5.0  -> raw ~127.5
      and _rb_raw(decoded_rb[1], 1) == 510,   # v=20.0 -> raw 510
      str(decoded_rb))

# ---- scaling.json recap merge ------------------------------------------------
eh._update_scaling_recap(tmp, "MapA", 0.001, 0.2, 0.05)
eh._update_scaling_recap(tmp, "MapB", 0.002, 0.3, 0.06)
with open(os.path.join(tmp, "scaling.json")) as f:
    recap = json.load(f)
check("scaling recap has both maps", set(recap) == {"MapA", "MapB"}, str(recap))
check("scaling recap values", recap["MapA"] == {
    "png16_meters_per_unit": 0.001, "png8_meters_per_unit": 0.2,
    "rb_meters_per_unit": 0.05})

eh._update_scaling_recap(tmp, "MapA", 0.005, 0.4, 0.07)
with open(os.path.join(tmp, "scaling.json")) as f:
    recap = json.load(f)
check("scaling recap overwrite + preserve sibling",
      recap["MapA"]["png16_meters_per_unit"] == 0.005 and "MapB" in recap,
      str(recap))

# ---- sh_log: clean console vs. verbose log file -----------------------------
import sh_log  # noqa: E402

sh_log.unreal = None  # don't mirror to the stubbed unreal.log during tests

check("fmt_duration",
      sh_log.fmt_duration(294) == "4m54s" and sh_log.fmt_duration(9) == "9s")
check("fmt_count",
      sh_log.fmt_count(16_700_000) == "16.7M"
      and sh_log.fmt_count(4100) == "4.1k" and sh_log.fmt_count(320) == "320")

# A Reporter splits lines across two channels: a clean console (phase/step/
# warn/progress) and a verbose file (everything). detail() is file-only.
log_path = os.path.join(tmp, "logs", "sess.log")
rep = sh_log.Reporter(log_path=log_path, verbose=False)
console = []
rep._to_console = lambda text, newline=True: console.append(text)
rep.phase("Scanning grid")
rep.step("Grid 10 x 10")
rep.detail("HitResult strategy: tuple")   # file only, must NOT reach console
rep.warn("did not stabilize")
rep.progress(10, 10, "row 10/10")          # final tick -> file breadcrumb
rep.close()

console_text = "\n".join(console)
check("console shows phase", "==> Scanning grid" in console_text)
check("console shows step", "Grid 10 x 10" in console_text)
check("console hides detail", "HitResult strategy" not in console_text)
check("console shows warn", "did not stabilize" in console_text)

file_text = open(log_path, encoding="utf-8").read()
check("file keeps phase", "==> Scanning grid" in file_text)
check("file keeps detail", "HitResult strategy: tuple" in file_text)
check("file keeps warn", "WARNING: did not stabilize" in file_text)
check("file keeps progress", "progress 100%" in file_text)

# verbose=True echoes detail to the console too (live troubleshooting).
rep2 = sh_log.Reporter(log_path=os.path.join(tmp, "logs", "sess2.log"),
                       verbose=True)
console2 = []
rep2._to_console = lambda text, newline=True: console2.append(text)
rep2.detail("verbose detail line")
rep2.close()
check("verbose echoes detail", any("verbose detail line" in t for t in console2))

# start_session puts one dated file under <output_root>/logs and a same-day
# re-entry appends to it (the .bat relaunch loop reuses one file per day).
s1 = sh_log.start_session(tmp, "Session A")
sess_path = s1.log_path
sh_log.start_session(tmp, "Session B")
sess_text = open(sess_path, encoding="utf-8").read()
check("session log under logs/", os.path.basename(os.path.dirname(sess_path)) == "logs")
check("session log appends", "Session A" in sess_text and "Session B" in sess_text)
sh_log.get().close()

# ---- make_config.rank_candidate: pick the geometry level, not navmesh -------
# Candidate lists below are the real ones captured from a make_config run.
import make_config as mc  # noqa: E402


def best_level(cands):
    return sorted(cands, key=mc.rank_candidate)[0]


# Maps whose master ships only as _Navmesh/_Profiling helpers: the real
# geometry is a root landscape / baselayer, which must win.
check("Belaya -> landscape, not navmesh", best_level([
    "/Game/Maps/Belaya_Pass/Sublevels/L_000_Master_Belaya_Navmesh",
    "/Game/Maps/Belaya_Pass/Sublevels/L_000_Master_Belaya_Profiling",
    "/Game/Maps/Belaya_Pass/BelayaLandscape",
    "/Game/Maps/Belaya_Pass/Sublevels/050_Cameras/L_050_Cameras_Belaya_Static",
]) == "/Game/Maps/Belaya_Pass/BelayaLandscape")

check("Skorpo -> baselayer, not navmesh", best_level([
    "/Game/Maps/Skorpo/Sublevels/L_000_Master_Skorpo_Navmesh",
    "/Game/Maps/Skorpo/Sublevels/L_000_Master_Skorpo_Profiling",
    "/Game/Maps/Skorpo/Skorpo_Baselayer",
    "/Game/Maps/Skorpo/Sublevels/050_Cameras/L_050_Cameras_Skorpo_Static",
]) == "/Game/Maps/Skorpo/Skorpo_Baselayer")

check("Fallujah -> landscape, not navmesh/vfx", best_level([
    "/Game/Maps/Fallujah_City/Sublevels/L_000_Master_Fallujah_Navmesh",
    "/Game/Maps/Fallujah_City/Sublevels/L_000_Master_Fallujah_Profiling",
    "/Game/Maps/Fallujah_City/Sublevels/090_VisualFX/L_090_VisualFX_Fallujah_01",
    "/Game/Maps/Fallujah_City/Sublevels/070_Landscape/L_070_Landscape_Fallujah_00",
    "/Game/Maps/Fallujah_City/Sublevels/070_Landscape/L_070_Landscape_Fallujah_01",
]).endswith("070_Landscape/L_070_Landscape_Fallujah_00"))

check("Harju -> landscape, not navmesh", best_level([
    "/Harju/Maps/Sublevels/L_000_Master_Harju_Navmesh",
    "/Harju/Maps/Sublevels/L_000_Master_Harju_Profiling",
    "/Harju/Maps/Harju_Landscape",
    "/Harju/Maps/Sublevels/L_000_Master_Harju_Coop",
]) == "/Harju/Maps/Harju_Landscape")

# Maps that already picked correctly must NOT regress.
check("AlBasrah keeps bare master", best_level([
    "/Al_Basrah/Maps/Sublevels/L_000_Master_AlBasrah",
    "/Al_Basrah/Maps/Sublevels/L_000_Master_AlBasrah_Navmesh",
    "/Al_Basrah/Maps/Sublevels/L_000_Master_AlBasrah_Profiling",
]) == "/Al_Basrah/Maps/Sublevels/L_000_Master_AlBasrah")

check("Narva keeps bare master over fx/navmesh", best_level([
    "/Game/Maps/Narva/Sublevels/L_000_Master_Narva",
    "/Game/Maps/Narva/Sublevels/100_SoundFX/L_100_SoundFX_Narva_Master",
    "/Game/Maps/Narva/Sublevels/L_000_Master_Narva_Navmesh",
    "/Game/Maps/Narva/Sublevels/090_VisualFX/L_090_VisualFX_Narva_Master",
    "/Game/Maps/Narva/Sublevels/L_000_Master_Narva_Profiling",
]) == "/Game/Maps/Narva/Sublevels/L_000_Master_Narva")

check("Anvil keeps GEO", best_level([
    "/Game/Maps/Anvil/Anvil_GEO",
    "/Game/Maps/Anvil/Sublevels/L_000_Master_Anvil_Navmesh",
    "/Game/Maps/Anvil/Sublevels/050_Cameras/L_050_Cameras_Anvil_Static",
]) == "/Game/Maps/Anvil/Anvil_GEO")

check("Chora keeps folder-named", best_level([
    "/Game/Maps/Chora/Chora",
    "/Game/Maps/Chora/Sublevels/L_000_Master_Chora_Navmesh",
]) == "/Game/Maps/Chora/Chora")

print()
if failures:
    print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("All self-tests passed.")
