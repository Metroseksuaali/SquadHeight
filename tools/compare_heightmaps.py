"""
compare_heightmaps.py - validate a new ray-traced heightmap against the old
landscape-only one (or any two SquadCalc-format heightmaps).

Runs OUTSIDE Unreal with any system Python 3 - stdlib only.

Usage:
    python compare_heightmaps.py <old.json> <new.json> [out_dir]

    e.g.
    python tools/compare_heightmaps.py legacy_chora.json output/Chora/heightmap_500.json output/Chora

If the grids have different dimensions (e.g. legacy 500x500 vs new 4065x4065),
the NEW grid is resampled (nearest neighbour) onto the OLD grid before
diffing - both files are assumed to cover the same world bounds.

Outputs into out_dir (default: alongside <new.json>):
    diff.png      - 8-bit visualization of (new - old):
                      128  = no difference
                      >128 = new is HIGHER (buildings, bridges, rocks - this
                             is exactly what the ray-traced export should add)
                      <128 = new is LOWER (suspicious! likely an orientation
                             mismatch or a foliage/bounds problem)
                    scaled so +/- diff_clamp_m maps to 0..255.
    diff_stats.json + printed summary.

Interpretation guide is in README.md ("Validating against the old heightmap").
"""

import json
import math
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import png16  # noqa: E402

DIFF_CLAMP_M = 15.0  # +/- range of diff.png; tweak if your structures are taller


def _load(path):
    with open(path, "r") as f:
        grid = json.load(f)
    if not grid or not isinstance(grid[0], list):
        raise ValueError("%s is not a 2D JSON array" % path)
    return grid


def _resample(grid, rows, cols):
    src_rows, src_cols = len(grid), len(grid[0])
    if (src_rows, src_cols) == (rows, cols):
        return grid
    out = []
    for r in range(rows):
        sr = min(int(r * src_rows / float(rows)), src_rows - 1)
        src = grid[sr]
        out.append([src[min(int(c * src_cols / float(cols)), src_cols - 1)]
                    for c in range(cols)])
    return out


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    old_path, new_path = argv[1], argv[2]
    out_dir = argv[3] if len(argv) > 3 else os.path.dirname(os.path.abspath(new_path))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    old = _load(old_path)
    new = _load(new_path)
    rows, cols = len(old), len(old[0])
    print("old: %dx%d   new: %dx%d  (new resampled onto old grid)"
          % (rows, cols, len(new), len(new[0])))
    new = _resample(new, rows, cols)

    # Both formats are min-normalized to 0 independently, so a constant offset
    # between the two is expected and uninteresting. Align medians before
    # diffing so the stats reflect SHAPE differences, not normalization.
    flat_old = sorted(v for row in old for v in row)
    flat_new = sorted(v for row in new for v in row)
    offset = flat_new[len(flat_new) // 2] - flat_old[len(flat_old) // 2]
    print("median offset (new - old): %.2f m (removed before diffing)" % offset)

    n = rows * cols
    sum_abs = 0.0
    max_up = (0.0, 0, 0)
    max_down = (0.0, 0, 0)
    raised_1m = 0
    lowered_1m = 0
    png_rows = []
    half = DIFF_CLAMP_M
    for r in range(rows):
        prow = []
        orow, nrow = old[r], new[r]
        for c in range(cols):
            d = (nrow[c] - offset) - orow[c]
            sum_abs += abs(d)
            if d > max_up[0]:
                max_up = (d, r, c)
            if d < -max_down[0]:
                max_down = (-d, r, c)
            if d >= 1.0:
                raised_1m += 1
            elif d <= -1.0:
                lowered_1m += 1
            g = int(round(128 + max(-half, min(half, d)) / half * 127))
            prow.append(max(0, min(255, g)))
        png_rows.append(prow)

    stats = {
        "old": os.path.abspath(old_path),
        "new": os.path.abspath(new_path),
        "grid": [rows, cols],
        "median_offset_m": round(offset, 3),
        "mean_abs_diff_m": round(sum_abs / n, 3),
        "max_raised_m": {"value": round(max_up[0], 2), "row": max_up[1], "col": max_up[2]},
        "max_lowered_m": {"value": round(max_down[0], 2), "row": max_down[1], "col": max_down[2]},
        "cells_raised_over_1m": raised_1m,
        "cells_raised_over_1m_pct": round(100.0 * raised_1m / n, 2),
        "cells_lowered_over_1m": lowered_1m,
        "cells_lowered_over_1m_pct": round(100.0 * lowered_1m / n, 2),
        "diff_png_clamp_m": DIFF_CLAMP_M,
    }

    diff_png = os.path.join(out_dir, "diff.png")
    png16.write_gray_png(diff_png, cols, rows, png_rows, bit_depth=8)
    with open(os.path.join(out_dir, "diff_stats.json"), "w") as f:
        json.dump(stats, f, indent=2)

    print(json.dumps(stats, indent=2))
    print("\nWrote %s  (128=no change, bright=new is higher, dark=new is lower)"
          % diff_png)

    if stats["cells_lowered_over_1m_pct"] > 2.0:
        print("\nWARNING: %.1f%% of cells are >1 m LOWER in the new export."
              % stats["cells_lowered_over_1m_pct"])
        print("That usually means an orientation mismatch (try transpose/"
              "flip_rows/flip_cols in CONFIG) or mismatched bounds.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
