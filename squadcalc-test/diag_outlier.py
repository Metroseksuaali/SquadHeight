"""
Investigate maps whose max height or correlation looked off in crosscheck.
For each slug: height distribution, where the max sits and whether it is an
isolated spike or a broad region, and how correlation against production
improves if we ignore extreme outliers / crop to the in-play centre.

    python diag_outlier.py chora skorpo
"""

import json
import os
import sys
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.normpath(os.path.join(HERE, "..", "output"))
PROD = "https://squadcalc.app/api/img/maps/%s/heightmap.json"
SLUG_TO_DIR = {d.lower(): d for d in os.listdir(OUTPUT)
               if os.path.isdir(os.path.join(OUTPUT, d))}


def corr(a, b):
    a = a.ravel() - a.mean(); b = b.ravel() - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d else 0.0


def main():
    for slug in (s.lower() for s in (sys.argv[1:] or ["chora", "skorpo"])):
        d = SLUG_TO_DIR.get(slug)
        ours = np.array(json.load(open(os.path.join(OUTPUT, d, "heightmap_500.json"))), float)
        with urllib.request.urlopen(PROD % slug, timeout=30) as r:
            prod = np.array(json.load(r), float)

        print("\n=== %s ===" % slug)
        print("  ours: min=%.1f max=%.1f mean=%.1f  prod: min=%.1f max=%.1f mean=%.1f"
              % (ours.min(), ours.max(), ours.mean(),
                 prod.min(), prod.max(), prod.mean()))

        # percentiles of ours
        pcs = [50, 90, 99, 99.9, 99.99, 100]
        vals = np.percentile(ours, pcs)
        print("  ours percentiles: " +
              "  ".join("p%-5s=%.1f" % (p, v) for p, v in zip(pcs, vals)))

        # how many cells above thresholds
        for t in (prod.max(), prod.max() * 1.5, prod.max() * 2):
            n = int((ours > t).sum())
            print("  cells above %6.1fm : %7d  (%.3f%%)" % (t, n, 100.0 * n / ours.size))

        # locate the max & measure how isolated it is
        iy, ix = np.unravel_index(np.argmax(ours), ours.shape)
        win = ours[max(0, iy - 3):iy + 4, max(0, ix - 3):ix + 4]
        print("  max at (row=%d,col=%d); 7x7 around it: min=%.1f max=%.1f mean=%.1f"
              % (iy, ix, win.min(), win.max(), win.mean()))

        # correlation when extreme outliers in ours are clipped to prod range
        clipped = np.clip(ours, None, prod.max())
        print("  corr identity full      r=%.3f" % corr(ours, prod))
        print("  corr identity clipped   r=%.3f  (ours clipped to prod max)" % corr(clipped, prod))

        # centre crop (inner 60%) - excludes out-of-play borders
        m = int(ours.shape[0] * 0.2)
        c_ours = ours[m:-m, m:-m]; c_prod = prod[m:-m, m:-m]
        print("  corr centre 60%% crop    r=%.3f  (ours max here=%.1f, prod max=%.1f)"
              % (corr(c_ours, c_prod), c_ours.max(), c_prod.max()))


if __name__ == "__main__":
    main()
