#!/usr/bin/env python3
"""
convert_squadheight_png.py
==========================

Convert a SquadHeightRuntime UE4SS Lua export into the same PNG encodings used
by Metroseksuaali/SquadHeight's original editor exporter.

Input directory (example):
    SquadHeight_output/Gorodok/
        heightmap_world_f32.raw
        meta.json

Output:
    heightmap_16bit.png  - grayscale 16-bit, 0..65535
    heightmap_8bit.png   - grayscale 8-bit, 0..255
    heightmap_rb.png     - RGB 8-bit, height split over R/B, G=0

RB encoding is intentionally identical to the original exporter:
    raw = round(height_m * (510 / height_span_m))
    raw 0..255   -> R=0,       G=0, B=255-raw
    raw 256..510 -> R=raw-255, G=0, B=0

Decode:
    raw = 255 + R - B
    height_m = raw * rb_meters_per_unit

The raw file contains little-endian IEEE754 float32 absolute world Z values in
meters. Before encoding, NaN/no-hit cells are post-processed like the original
editor exporter: thin gaps are filled from 8-connected neighbours for up to
12 frontier passes, then any remaining large empty regions are filled with the
minimum valid map height. Values are normalized to that minimum before encoding.

No Pillow / NumPy dependency is required. PNG writing uses the same PNG layout
and Paeth scanline filtering strategy as the original repository's png16.py.
"""

from __future__ import annotations

import argparse
import json
import math
import mmap
import os
import struct
import sys
import tempfile
import zlib
from array import array
from pathlib import Path
from typing import Iterable, Iterator, Sequence

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RB_MAX_RAW = 510


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _paeth_filter(raw: bytes, prior: bytes, bpp: int) -> bytes:
    """PNG filter type 4 (Paeth), byte-for-byte equivalent in algorithm."""
    out = bytearray(len(raw))
    for i, x in enumerate(raw):
        a = raw[i - bpp] if i >= bpp else 0
        b = prior[i]
        c = prior[i - bpp] if i >= bpp else 0
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
        out[i] = (x - pred) & 0xFF
    return bytes(out)


def _write_single_idat_png(
    path: Path,
    width: int,
    height: int,
    bit_depth: int,
    color_type: int,
    raw_rows: Iterable[bytes],
    bpp: int,
    compress_level: int,
) -> None:
    """Write one non-interlaced PNG using one IDAT chunk and Paeth rows."""
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    compressor = zlib.compressobj(compress_level)
    prior = bytes(width * bpp)
    row_count = 0

    # Spool compressed IDAT to disk so giant heightmaps don't need another
    # tens/hundreds of MB of Python RAM, while still producing one IDAT chunk.
    with tempfile.TemporaryFile() as idat:
        for raw in raw_rows:
            expected = width * bpp
            if len(raw) != expected:
                raise ValueError(
                    f"row {row_count} has {len(raw)} bytes, expected {expected}"
                )
            filtered = b"\x04" + _paeth_filter(raw, prior, bpp)
            part = compressor.compress(filtered)
            if part:
                idat.write(part)
            prior = raw
            row_count += 1

        tail = compressor.flush()
        if tail:
            idat.write(tail)

        if row_count != height:
            raise ValueError(f"row iterator produced {row_count} rows, expected {height}")

        idat_size = idat.tell()
        idat.seek(0)

        with path.open("wb") as out:
            out.write(PNG_SIGNATURE)
            out.write(_chunk(b"IHDR", ihdr))

            out.write(struct.pack(">I", idat_size))
            out.write(b"IDAT")
            crc = zlib.crc32(b"IDAT")
            while True:
                block = idat.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
                crc = zlib.crc32(block, crc)
            out.write(struct.pack(">I", crc & 0xFFFFFFFF))

            out.write(_chunk(b"IEND", b""))


def write_gray_png(
    path: Path,
    width: int,
    height: int,
    rows: Iterable[Sequence[int]],
    bit_depth: int,
    compress_level: int,
) -> None:
    if bit_depth not in (8, 16):
        raise ValueError("bit_depth must be 8 or 16")

    if bit_depth == 16:
        def raw_rows() -> Iterator[bytes]:
            for row in rows:
                values = array("H", row)
                if sys.byteorder == "little":
                    values.byteswap()  # PNG 16-bit samples are big-endian.
                yield values.tobytes()
        bpp = 2
    else:
        def raw_rows() -> Iterator[bytes]:
            for row in rows:
                yield bytes(row)
        bpp = 1

    _write_single_idat_png(
        path, width, height,
        bit_depth=bit_depth,
        color_type=0,  # grayscale
        raw_rows=raw_rows(),
        bpp=bpp,
        compress_level=compress_level,
    )


def write_rgb_png(
    path: Path,
    width: int,
    height: int,
    rows: Iterable[Sequence[int]],
    compress_level: int,
) -> None:
    def raw_rows() -> Iterator[bytes]:
        for row in rows:
            yield bytes(row)

    _write_single_idat_png(
        path, width, height,
        bit_depth=8,
        color_type=2,  # truecolor RGB
        raw_rows=raw_rows(),
        bpp=3,
        compress_level=compress_level,
    )


class RawHeightmap:
    def __init__(self, raw_path: Path, rows: int, cols: int):
        self.raw_path = raw_path
        self.rows = rows
        self.cols = cols
        expected = rows * cols * 4
        actual = raw_path.stat().st_size
        if actual != expected:
            raise ValueError(
                f"raw size mismatch: {actual:,} bytes, expected {expected:,} "
                f"for {cols}x{rows} float32 cells"
            )
        self._file = raw_path.open("rb")
        self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)

    def close(self) -> None:
        self._mm.close()
        self._file.close()

    def __enter__(self) -> "RawHeightmap":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def value(self, row: int, col: int) -> float:
        offset = (row * self.cols + col) * 4
        return struct.unpack_from("<f", self._mm, offset)[0]

    def source_row(self, row: int) -> array:
        start = row * self.cols * 4
        data = self._mm[start:start + self.cols * 4]
        values = array("f")
        values.frombytes(data)
        if sys.byteorder != "little":
            values.byteswap()
        return values


class ArrayHeightmap:
    """In-memory float32 raster used by the original-style hole post-process."""

    def __init__(self, values: array, rows: int, cols: int):
        self.values = values
        self.rows = rows
        self.cols = cols

    def value(self, row: int, col: int) -> float:
        return self.values[row * self.cols + col]

    def source_row(self, row: int) -> array:
        start = row * self.cols
        return self.values[start:start + self.cols]


def _load_float32_raster(raw_path: Path, rows: int, cols: int) -> array:
    """
    Load the runtime raw raster as a compact array('f').

    A 4032x4031 1 m map is ~65 MB here, rather than hundreds of MB / multiple
    GB as Python list-of-lists. This lets us reproduce the editor exporter's
    in-place frontier hole fill without excessive RAM use.
    """
    expected = rows * cols * 4
    actual = raw_path.stat().st_size
    if actual != expected:
        raise ValueError(
            f"raw size mismatch: {actual:,} bytes, expected {expected:,} "
            f"for {cols}x{rows} float32 cells"
        )

    values = array("f")
    with raw_path.open("rb") as f:
        values.fromfile(f, rows * cols)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def _valid_extrema(values: Sequence[float]) -> tuple[float, float, int]:
    """Return (min, max, NaN count), ignoring no-hit NaNs."""
    h_min = float("inf")
    h_max = float("-inf")
    no_hit = 0
    for v in values:
        if math.isnan(v):
            no_hit += 1
        else:
            if v < h_min:
                h_min = v
            if v > h_max:
                h_max = v

    if h_min > h_max:
        raise RuntimeError(
            "No geometry was hit at all - check trace channel / map bounds."
        )
    return h_min, h_max, no_hit


def _fill_holes_like_original(
    values: array,
    rows: int,
    cols: int,
    h_min: float,
    max_passes: int = 12,
) -> dict:
    """
    Reproduce SquadHeight editor exporter's no-hit post-processing.

    This intentionally keeps the original neighbour order and in-place update
    behaviour:
        (down, up, right, left, down-right, down-left, up-right, up-left)

    1. Find NaN cells touching any valid cell in the 8-neighbourhood.
    2. For up to `max_passes` frontier passes, replace each frontier NaN with
       the average of currently valid 8-neighbours.
    3. Any NaNs still remaining are large genuinely empty regions and become
       the map minimum.

    The height raster itself stays in compact float32 storage (~65 MB for a
    4032x4031 map) instead of a Python list-of-lists.
    """
    if rows <= 0 or cols <= 0:
        return {
            "initial_no_hit": 0,
            "neighbor_filled": 0,
            "min_filled": 0,
            "passes_used": 0,
        }

    neighbors = (
        (1, 0), (-1, 0), (0, 1), (0, -1),
        (1, 1), (1, -1), (-1, 1), (-1, -1),
    )

    def get(r: int, c: int) -> float:
        return values[r * cols + c]

    frontier: list[tuple[int, int]] = []
    initial_no_hit = 0

    # Same row-major initial frontier construction as export_heightmap.py.
    for r in range(rows):
        base = r * cols
        for c in range(cols):
            if not math.isnan(values[base + c]):
                continue
            initial_no_hit += 1
            for dr, dc in neighbors:
                rr, cc = r + dr, c + dc
                if (
                    0 <= rr < rows
                    and 0 <= cc < cols
                    and not math.isnan(get(rr, cc))
                ):
                    frontier.append((r, c))
                    break

    neighbor_filled = 0
    passes_used = 0

    # Mirrors the original loop closely. Values are updated in place, so cells
    # later in a pass can see values filled earlier in that same pass.
    for _ in range(max_passes):
        if not frontier:
            break
        passes_used += 1
        next_frontier: set[tuple[int, int]] = set()

        for r, c in frontier:
            idx = r * cols + c
            was_nan = math.isnan(values[idx])

            total = 0.0
            count = 0
            for dr, dc in neighbors:
                rr, cc = r + dr, c + dc
                if 0 <= rr < rows and 0 <= cc < cols:
                    v = get(rr, cc)
                    if not math.isnan(v):
                        total += v
                        count += 1

            if count:
                values[idx] = total / count
                if was_nan:
                    neighbor_filled += 1

                for dr, dc in neighbors:
                    rr, cc = r + dr, c + dc
                    if (
                        0 <= rr < rows
                        and 0 <= cc < cols
                        and math.isnan(get(rr, cc))
                    ):
                        next_frontier.add((rr, cc))

        frontier = list(next_frontier)

    min_filled = 0
    for idx, v in enumerate(values):
        if math.isnan(v):
            values[idx] = h_min
            min_filled += 1

    return {
        "initial_no_hit": initial_no_hit,
        "neighbor_filled": neighbor_filled,
        "min_filled": min_filled,
        "passes_used": passes_used,
    }


class OrientedNormalizedRows:
    """Re-iterable view of oriented, normalized source heights."""

    def __init__(
        self,
        raw: RawHeightmap,
        z_offset_m: float,
        transpose: bool,
        flip_rows: bool,
        flip_cols: bool,
    ):
        self.raw = raw
        self.z_offset = z_offset_m
        self.transpose = transpose
        self.flip_rows = flip_rows
        self.flip_cols = flip_cols
        self.out_rows = raw.cols if transpose else raw.rows
        self.out_cols = raw.rows if transpose else raw.cols

    @staticmethod
    def _norm(v: float, z_offset: float) -> float:
        if math.isnan(v):
            return 0.0
        return v - z_offset

    def _oriented_to_source(self, out_r: int, out_c: int) -> tuple[int, int]:
        r, c = out_r, out_c
        if self.flip_rows:
            r = self.out_rows - 1 - r
        if self.flip_cols:
            c = self.out_cols - 1 - c
        if self.transpose:
            return c, r
        return r, c

    def __iter__(self) -> Iterator[list[float]]:
        # Common fast path: orientation does not transpose. We can read an
        # entire float32 row at once instead of unpacking one cell at a time.
        if not self.transpose:
            for out_r in range(self.out_rows):
                src_r = self.raw.rows - 1 - out_r if self.flip_rows else out_r
                values = self.raw.source_row(src_r)
                row = [self._norm(v, self.z_offset) for v in values]
                if self.flip_cols:
                    row.reverse()
                yield row
            return

        # Transpose inherently turns source columns into output rows.
        for out_r in range(self.out_rows):
            row = []
            append = row.append
            for out_c in range(self.out_cols):
                src_r, src_c = self._oriented_to_source(out_r, out_c)
                append(self._norm(self.raw.value(src_r, src_c), self.z_offset))
            yield row


def _quantized_gray_rows(
    heights: Iterable[Sequence[float]],
    max_raw: int,
    span_m: float,
) -> Iterator[list[int]]:
    scale = max_raw / span_m if span_m > 1e-9 else 0.0
    for row in heights:
        out = []
        append = out.append
        for h in row:
            raw = int(h * scale + 0.5) if scale else 0
            # Raw + meta originate from the same scan, so clipping should only
            # matter for tiny float round-off or manually edited metadata.
            if raw < 0:
                raw = 0
            elif raw > max_raw:
                raw = max_raw
            append(raw)
        yield out


def _quantized_rb_rows(
    heights: Iterable[Sequence[float]],
    span_m: float,
) -> Iterator[list[int]]:
    scale = RB_MAX_RAW / span_m if span_m > 1e-9 else 0.0
    for row in heights:
        pixels = []
        extend = pixels.extend
        for h in row:
            raw = int(h * scale + 0.5) if scale else 0
            if raw < 0:
                raw = 0
            elif raw > RB_MAX_RAW:
                raw = RB_MAX_RAW

            if raw <= 255:
                extend((0, 0, 255 - raw))
            else:
                extend((raw - 255, 0, 0))
        yield pixels


def _update_meta(
    meta_path: Path,
    meta: dict,
    span_m: float,
    scale16: float,
    scale8: float,
    scale_rb: float,
) -> None:
    meta["png16_units_per_meter"] = round(scale16, 6)
    meta["png16_meters_per_unit"] = round(span_m / 65535.0, 9)
    meta["png8_units_per_meter"] = round(scale8, 6)
    meta["png8_meters_per_unit"] = round(span_m / 255.0, 9)
    meta["rb_units_per_meter"] = round(scale_rb, 6)
    meta["rb_meters_per_unit"] = round(span_m / RB_MAX_RAW, 9)

    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _update_scaling_recap(map_dir: Path, map_name: str, span_m: float) -> Path:
    path = map_dir.parent / "scaling.json"
    recap = {}
    if path.is_file():
        with path.open("r", encoding="utf-8") as f:
            recap = json.load(f)

    recap[map_name] = {
        "png16_meters_per_unit": round(span_m / 65535.0, 9),
        "png8_meters_per_unit": round(span_m / 255.0, 9),
        "rb_meters_per_unit": round(span_m / RB_MAX_RAW, 9),
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(recap, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    return path


def convert_map_dir(
    map_dir: Path,
    compress_level: int = 9,
    update_meta: bool = True,
    update_scaling: bool = True,
    hole_fill: bool = True,
    hole_fill_passes: int = 12,
) -> None:
    meta_path = map_dir / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"meta.json not found: {meta_path}")

    with meta_path.open("r", encoding="utf-8") as f:
        meta = json.load(f)

    rows = int(meta["grid_rows"])
    cols = int(meta["grid_cols"])

    orientation = meta.get("orientation") or {}
    transpose = bool(orientation.get("transpose", False))
    flip_rows = bool(orientation.get("flip_rows", False))
    flip_cols = bool(orientation.get("flip_cols", False))

    raw_name = ((meta.get("raw") or {}).get("file") or "heightmap_world_f32.raw")
    raw_path = map_dir / raw_name
    if not raw_path.is_file():
        raise FileNotFoundError(f"raw heightmap not found: {raw_path}")

    out_rows = cols if transpose else rows
    out_cols = rows if transpose else cols

    print(f"Map: {meta.get('map', map_dir.name)}")
    print(f"Source: {cols}x{rows}, output: {out_cols}x{out_rows}")

    # The editor exporter derives min/max from actual valid samples before
    # filling holes. Do the same instead of trusting possibly stale metadata.
    print("Loading float32 raster and scanning valid Z range ...")
    values = _load_float32_raster(raw_path, rows, cols)
    h_min, h_max, no_hit = _valid_extrema(values)

    hole_stats = {
        "initial_no_hit": no_hit,
        "neighbor_filled": 0,
        "min_filled": 0,
        "passes_used": 0,
    }
    if no_hit:
        if hole_fill:
            print(
                f"Filling {no_hit:,} no-hit cells like original exporter "
                f"(8-neighbour frontier, max {hole_fill_passes} passes) ..."
            )
            hole_stats = _fill_holes_like_original(
                values, rows, cols, h_min, max_passes=hole_fill_passes
            )
            print(
                "Hole fill: "
                f"{hole_stats['neighbor_filled']:,} neighbour-filled, "
                f"{hole_stats['min_filled']:,} min-filled, "
                f"{hole_stats['passes_used']} pass(es)"
            )
        else:
            print(
                f"Hole fill disabled: {no_hit:,} NaNs will encode as normalized 0."
            )

    # Original default behaviour normalizes the valid map minimum to zero.
    z_offset = h_min
    span_m = h_max - h_min
    if span_m < 0:
        raise ValueError(f"invalid height span: {span_m}")

    print(f"World Z range: {h_min:.6f} .. {h_max:.6f} m")
    print(f"World Z offset: {z_offset:.6f} m; normalized span: {span_m:.6f} m")
    print(
        "Orientation: "
        f"transpose={transpose}, flip_rows={flip_rows}, flip_cols={flip_cols}"
    )

    scale16 = 65535.0 / span_m if span_m > 1e-9 else 0.0
    scale8 = 255.0 / span_m if span_m > 1e-9 else 0.0
    scale_rb = RB_MAX_RAW / span_m if span_m > 1e-9 else 0.0

    png16_path = map_dir / "heightmap_16bit.png"
    png8_path = map_dir / "heightmap_8bit.png"
    rb_path = map_dir / "heightmap_rb.png"

    raw = ArrayHeightmap(values, rows, cols)
    heights = OrientedNormalizedRows(
        raw, z_offset,
        transpose=transpose,
        flip_rows=flip_rows,
        flip_cols=flip_cols,
    )

    print(f"Writing {png16_path.name} ...")
    write_gray_png(
        png16_path,
        out_cols,
        out_rows,
        _quantized_gray_rows(heights, 65535, span_m),
        bit_depth=16,
        compress_level=compress_level,
    )

    print(f"Writing {png8_path.name} ...")
    write_gray_png(
        png8_path,
        out_cols,
        out_rows,
        _quantized_gray_rows(heights, 255, span_m),
        bit_depth=8,
        compress_level=compress_level,
    )

    print(f"Writing {rb_path.name} ...")
    write_rgb_png(
        rb_path,
        out_cols,
        out_rows,
        _quantized_rb_rows(heights, span_m),
        compress_level=compress_level,
    )

    if update_meta:
        # Keep metadata consistent with the actual raster, as the editor
        # exporter does after its hole-fill/normalization stage.
        meta["z_offset_m"] = round(z_offset, 4)
        meta["height_min_m"] = 0.0
        meta["height_max_m"] = round(span_m, 4)
        meta["world_z_min_m"] = round(h_min, 4)
        meta["world_z_max_m"] = round(h_max, 4)
        stats = meta.setdefault("stats", {})
        stats["no_hit_cells_filled"] = int(hole_stats["initial_no_hit"])
        stats["neighbor_filled_cells"] = int(hole_stats["neighbor_filled"])
        stats["min_filled_cells"] = int(hole_stats["min_filled"])
        stats["hole_fill_passes"] = int(hole_stats["passes_used"])
        _update_meta(meta_path, meta, span_m, scale16, scale8, scale_rb)
        print(f"Updated {meta_path.name} with PNG scaling + hole-fill stats")

    if update_scaling:
        recap_path = _update_scaling_recap(
            map_dir,
            str(meta.get("map") or map_dir.name),
            span_m,
        )
        print(f"Updated {recap_path}")

    print("Done.")
    print(f"  {png16_path}")
    print(f"  {png8_path}")
    print(f"  {rb_path}")
    print(f"  png16_meters_per_unit = {span_m / 65535.0:.9f}")
    print(f"  png8_meters_per_unit  = {span_m / 255.0:.9f}")
    print(f"  rb_meters_per_unit    = {span_m / RB_MAX_RAW:.9f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert SquadHeightRuntime raw float32 heightmap to the same "
            "16-bit / 8-bit / RB PNG encodings as Metroseksuaali/SquadHeight."
        )
    )
    parser.add_argument(
        "map_dir",
        type=Path,
        help="Directory containing meta.json and heightmap_world_f32.raw",
    )
    parser.add_argument(
        "--compression",
        type=int,
        default=9,
        choices=range(0, 10),
        metavar="0..9",
        help="zlib PNG compression level (default: 9, same as exporter CONFIG)",
    )
    parser.add_argument(
        "--no-hole-fill",
        action="store_true",
        help=(
            "Disable original-style NaN frontier filling; remaining NaNs "
            "encode as normalized 0"
        ),
    )
    parser.add_argument(
        "--hole-fill-passes",
        type=int,
        default=12,
        metavar="N",
        help=(
            "Maximum 8-neighbour frontier passes for thin no-hit gaps "
            "(default: 12, same as original exporter)"
        ),
    )
    parser.add_argument(
        "--no-meta-update",
        action="store_true",
        help="Do not add PNG scaling fields to meta.json",
    )
    parser.add_argument(
        "--no-scaling-recap",
        action="store_true",
        help="Do not update ../scaling.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.hole_fill_passes < 0:
            raise ValueError("--hole-fill-passes must be >= 0")
        convert_map_dir(
            args.map_dir.resolve(),
            compress_level=args.compression,
            update_meta=not args.no_meta_update,
            update_scaling=not args.no_scaling_recap,
            hole_fill=not args.no_hole_fill,
            hole_fill_passes=args.hole_fill_passes,
        )
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
