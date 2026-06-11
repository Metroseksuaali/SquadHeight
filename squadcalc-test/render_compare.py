"""
Render ours vs production heightmaps side by side for visual comparison.
Both panels use the SAME colour scale (0..prod.max) so they are directly
comparable; values above prod.max in ours are shown in magenta (so spikes
and out-of-range structures stand out). Writes <slug>_compare.png.

    python render_compare.py chora skorpo
"""

import json
import os
import sys
import urllib.request

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.normpath(os.path.join(HERE, "..", "output"))
PROD = "https://squadcalc.app/api/img/maps/%s/heightmap.json"
SLUG_TO_DIR = {d.lower(): d for d in os.listdir(OUTPUT)
               if os.path.isdir(os.path.join(OUTPUT, d))}


def colorize(a, vmax):
    """Grayscale 0..vmax; cells above vmax -> magenta."""
    norm = np.clip(a / vmax, 0, 1)
    g = (norm * 255).astype(np.uint8)
    rgb = np.stack([g, g, g], axis=-1)
    over = a > vmax
    rgb[over] = (255, 0, 255)
    # exact-zero cells -> dark blue, to see dead/no-data regions
    zero = a == 0
    rgb[zero] = (20, 20, 60)
    return rgb


def main():
    for slug in (s.lower() for s in (sys.argv[1:] or ["chora"])):
        d = SLUG_TO_DIR.get(slug)
        ours = np.array(json.load(open(os.path.join(OUTPUT, d, "heightmap_500.json"))), float)
        with urllib.request.urlopen(PROD % slug, timeout=30) as r:
            prod = np.array(json.load(r), float)
        vmax = prod.max()

        gap = np.full((ours.shape[0], 8, 3), 255, np.uint8)
        combo = np.concatenate([colorize(prod, vmax), gap, colorize(ours, vmax)], axis=1)
        out = os.path.join(HERE, "%s_compare.png" % slug)
        Image.fromarray(combo, "RGB").resize(
            (combo.shape[1] * 2, combo.shape[0] * 2), Image.NEAREST).save(out)
        print("%s: left=PRODUCTION  right=OURS  scale 0..%.1fm  "
              "(magenta=above prod max, dark-blue=exactly 0)\n  -> %s"
              % (slug, vmax, out))


if __name__ == "__main__":
    main()
