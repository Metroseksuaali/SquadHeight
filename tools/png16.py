"""
png16.py - minimal, dependency-free PNG writer (grayscale, 8-bit or 16-bit).

Why this exists:
    UE's bundled Python does NOT ship with Pillow, and we want the exporter to
    be fully self-contained.  PNG is simple enough to write by hand for the
    non-interlaced grayscale case: a fixed signature + IHDR + IDAT (zlib of
    filtered scanlines) + IEND.  Only stdlib (struct, zlib) is used, which is
    always available in UE's embedded Python (3.7+).

Usage:
    from png16 import write_gray_png
    write_gray_png("out.png", width, height, rows, bit_depth=16)

    `rows` is any iterable yielding `height` iterables of `width` ints,
    each in [0, 255] for bit_depth=8 or [0, 65535] for bit_depth=16.
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

    # Filtered scanlines: each row is prefixed with filter type 0 (None).
    # 16-bit samples are big-endian per the PNG spec.
    compressor = zlib.compressobj(compress_level)
    idat_parts = []
    row_count = 0
    if bit_depth == 16:
        row_packer = struct.Struct(">%dH" % width)
        for row in rows:
            idat_parts.append(compressor.compress(b"\x00" + row_packer.pack(*row)))
            row_count += 1
    else:
        for row in rows:
            idat_parts.append(compressor.compress(b"\x00" + bytes(row)))
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
