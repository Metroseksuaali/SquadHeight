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
    bpp = depth // 8
    stride = 1 + w * bpp
    out = []
    for r in range(h):
        line = raw[r * stride:(r + 1) * stride]
        assert line[0] == 0, "unexpected filter type"
        if depth == 16:
            out.append(list(struct.unpack(">%dH" % w, line[1:])))
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

# ---- scaling.json recap merge ------------------------------------------------
eh._update_scaling_recap(tmp, "MapA", 0.001, 0.2)
eh._update_scaling_recap(tmp, "MapB", 0.002, 0.3)
with open(os.path.join(tmp, "scaling.json")) as f:
    recap = json.load(f)
check("scaling recap has both maps", set(recap) == {"MapA", "MapB"}, str(recap))
check("scaling recap values", recap["MapA"] == {
    "png16_meters_per_unit": 0.001, "png8_meters_per_unit": 0.2})

eh._update_scaling_recap(tmp, "MapA", 0.005, 0.4)
with open(os.path.join(tmp, "scaling.json")) as f:
    recap = json.load(f)
check("scaling recap overwrite + preserve sibling",
      recap["MapA"]["png16_meters_per_unit"] == 0.005 and "MapB" in recap,
      str(recap))

print()
if failures:
    print("FAILED: %d check(s): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("All self-tests passed.")
