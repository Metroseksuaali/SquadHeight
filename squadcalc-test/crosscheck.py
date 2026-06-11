"""
Cross-check our exported heightmaps against SquadCalc's CURRENT production
data, to confirm orientation/alignment is correct before delivery.

For each map:
  * download production heightmap.json (terrain-only, the data SquadCalc
    ships today) from https://squadcalc.app/api/img/maps/<slug>/heightmap.json
  * load ours from ../output/<Map>/heightmap_500.json
  * compute Pearson correlation between ours and production under all 8
    dihedral orientations (identity, 3 rotations, 4 mirrorings).

If our alignment matches SquadCalc, the IDENTITY orientation must give the
highest correlation. A different winner means a rotation/flip mismatch.
Production is terrain-only and ours adds buildings, so r<1 is expected; we
only care which orientation wins and by how much.

    python crosscheck.py                 # default focus maps
    python crosscheck.py skorpo tallil   # specific slugs
"""

import json
import os
import sys
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.normpath(os.path.join(HERE, "..", "output"))
PROD = "https://squadcalc.app/api/img/maps/%s/heightmap.json"

# slug -> our output folder name
SLUG_TO_DIR = {d.lower(): d for d in os.listdir(OUTPUT)
               if os.path.isdir(os.path.join(OUTPUT, d))}

DEFAULT = ["skorpo", "tallil", "yehorivka", "chora"]

ORIENTS = []
for flip in (False, True):
    for k in range(4):
        ORIENTS.append((k, flip))


def label(k, flip):
    base = {0: "identity", 1: "rot90", 2: "rot180", 3: "rot270"}[k]
    return base + ("+mirror" if flip else "")


def orient(a, k, flip):
    t = np.rot90(a, k)
    if flip:
        t = np.fliplr(t)
    return t


def fetch_prod(slug):
    url = PROD % slug
    with urllib.request.urlopen(url, timeout=30) as r:
        return np.array(json.load(r), dtype=np.float64)


def load_ours(slug):
    d = SLUG_TO_DIR.get(slug)
    if not d:
        return None
    p = os.path.join(OUTPUT, d, "heightmap_500.json")
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        return np.array(json.load(f), dtype=np.float64)


def corr(a, b):
    a = a.ravel(); b = b.ravel()
    a = a - a.mean(); b = b - b.mean()
    denom = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / denom) if denom else 0.0


def main():
    slugs = [s.lower() for s in sys.argv[1:]] or DEFAULT
    for slug in slugs:
        print("\n=== %s ===" % slug)
        ours = load_ours(slug)
        if ours is None:
            print("  no local export found")
            continue
        try:
            prod = fetch_prod(slug)
        except Exception as e:
            print("  production fetch failed: %s" % e)
            continue
        if prod.shape != ours.shape:
            print("  shape mismatch ours=%s prod=%s" % (ours.shape, prod.shape))
            continue

        scores = sorted(
            ((corr(orient(ours, k, f), prod), k, f) for (k, f) in ORIENTS),
            reverse=True)
        best_r, bk, bf = scores[0]
        ident_r = next(r for (r, k, f) in scores if k == 0 and not f)
        winner = label(bk, bf)
        runner = label(scores[1][1], scores[1][2])
        flag = "OK" if winner == "identity" else "!! MISMATCH"
        print("  ours max=%.1fm prod max=%.1fm" % (ours.max(), prod.max()))
        print("  best orientation : %-14s r=%.3f   [%s]" % (winner, best_r, flag))
        print("  identity         : %-14s r=%.3f" % ("identity", ident_r))
        print("  runner-up        : %-14s r=%.3f" % (runner, scores[1][0]))


if __name__ == "__main__":
    main()
