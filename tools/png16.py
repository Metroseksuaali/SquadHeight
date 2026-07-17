"""
png16.py - minimal, dependency-free PNG writer (grayscale 8/16-bit, or
truecolor RGB 8-bit).

Why this exists:
    UE's bundled Python does NOT ship with Pillow, and we want the exporter to
    be fully self-contained.  PNG is simple enough to write by hand for the
    non-interlaced cases here: a fixed signature + IHDR + IDAT (zlib of
    filtered scanlines) + IEND.  Only stdlib (struct, zlib) is used, which is
    always available in UE's embedded Python (3.7+).

Usage:
    from png16 import write_gray_png, write_rgb_png
    write_gray_png("out.png", width, height, rows, bit_depth=16)
    write_rgb_png("out.png", width, height, rows)

    For write_gray_png, `rows` is any iterable yielding `height` iterables of
    `width` ints, each in [0, 255] for bit_depth=8 or [0, 65535] for
    bit_depth=16.
    For write_rgb_png, `rows` is any iterable yielding `height` iterables of
    `width * 3` ints in [0, 255] (interleaved R, G, B per pixel).
"""

import struct
import zlib

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag, data):
    """Build one PNG chunk: length + tag + data + CRC32(tag+data)."""
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _paeth_filter(raw, prior, bpp):
    """
    PNG filter type 4 (Paeth): predict each byte from the reconstructed
    neighbours left/above/above-left, store the residual. Heightmaps are
    smooth, so residuals cluster near 0 and zlib compresses them much better
    than the raw values - this is what actually shrinks the file, not the
    zlib level. Used unconditionally (no per-row None/Sub/Up/Average
    comparison) to keep this a single pass over the data; numpy isn't
    available inside the UE editor where this module runs.
    """
    out = bytearray(len(raw))
    for i in range(len(raw)):
        a = raw[i - bpp] if i >= bpp else 0
        b = prior[i]
        c = prior[i - bpp] if i >= bpp else 0
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
        out[i] = (raw[i] - pred) & 0xFF
    return bytes(out)


def write_gray_png(path, width, height, rows, bit_depth=16, compress_level=6):
    """
    Write a grayscale PNG.

    path           : output file path
    width, height  : image dimensions in pixels
    rows           : iterable of rows; each row is an iterable of ints
                     (consumed lazily, so a generator is fine and keeps
                     peak memory low for big maps)
    bit_depth      : 8 or 16
    """
    if bit_depth not in (8, 16):
        raise ValueError("bit_depth must be 8 or 16")

    # IHDR: width, height, bit depth, color type 0 (grayscale),
    # compression 0, filter 0, interlace 0.
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, 0, 0, 0, 0)

    # Filtered scanlines: each row is Paeth-filtered (type 4) against the
    # previous row, then prefixed with its filter type byte.
    # 16-bit samples are big-endian per the PNG spec.
    bpp = bit_depth // 8
    prior = bytes(width * bpp)
    compressor = zlib.compressobj(compress_level)
    idat_parts = []
    row_count = 0
    if bit_depth == 16:
        row_packer = struct.Struct(">%dH" % width)
        for row in rows:
            raw = row_packer.pack(*row)
            idat_parts.append(compressor.compress(b"\x04" + _paeth_filter(raw, prior, bpp)))
            prior = raw
            row_count += 1
    else:
        for row in rows:
            raw = bytes(row)
            idat_parts.append(compressor.compress(b"\x04" + _paeth_filter(raw, prior, bpp)))
            prior = raw
            row_count += 1
    idat_parts.append(compressor.flush())

    if row_count != height:
        raise ValueError(
            "row iterator produced %d rows, expected %d" % (row_count, height)
        )

    with open(path, "wb") as f:
        f.write(_PNG_SIGNATURE)
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", b"".join(idat_parts)))
        f.write(_chunk(b"IEND", b""))


def write_rgb_png(path, width, height, rows, compress_level=6):
    """
    Write an 8-bit truecolor (RGB) PNG.

    path           : output file path
    width, height  : image dimensions in pixels
    rows           : iterable of rows; each row is an iterable of
                      width * 3 ints in [0, 255] (interleaved R, G, B),
                      consumed lazily so a generator is fine.
    """
    # IHDR: width, height, bit depth 8, color type 2 (truecolor),
    # compression 0, filter 0, interlace 0.
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    bpp = 3
    prior = bytes(width * bpp)
    compressor = zlib.compressobj(compress_level)
    idat_parts = []
    row_count = 0
    for row in rows:
        raw = bytes(row)
        idat_parts.append(compressor.compress(b"\x04" + _paeth_filter(raw, prior, bpp)))
        prior = raw
        row_count += 1
    idat_parts.append(compressor.flush())

    if row_count != height:
        raise ValueError(
            "row iterator produced %d rows, expected %d" % (row_count, height)
        )

    with open(path, "wb") as f:
        f.write(_PNG_SIGNATURE)
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", b"".join(idat_parts)))
        f.write(_chunk(b"IEND", b""))
