"""
find_alignment.py - derive the SquadCalc minimap square (world bounds,
ROTATION and mirroring) by registering the legacy heightmap against our
axis-aligned ray-traced scan.

Why: SquadCalc stretches its 500x500 heightmap exactly over the minimap
image, so our export must cover the very same world-space square. Squad's
minimap captures are not necessarily world-axis-aligned (and capture
pipelines often mirror an axis), so this tool searches rotation angle,
mirroring, scale (map size) and offset simultaneously, using masked
normalized cross-correlation. The legacy file's constant "background" value
(mid-gray of the old 8-bit depthmap pipeline) is masked out automatically.

Runs OUTSIDE Unreal. Requires numpy (the only tool in this repo that does).

Usage:
    python find_alignment.py <legacy.json> <scan_dir> [out_dir]
    python find_alignment.py --selftest <scan_dir>

    legacy.json : old SquadCalc heightmap for the same map
    scan_dir    : exporter output folder with heightmap.json + meta.json from
                  an AXIS-ALIGNED scan (rotation 0, no flips). 8 m res is fine.
    --selftest  : cuts a synthetic "legacy" patch out of the scan with a known
                  pose and verifies the search recovers it. Run this once to
                  trust the tool.

Prints ready-to-paste overrides for the final export: bounds_m,
grid_rotation_deg and flip_cols (mirror).

Convention: legacy cell (i, j) maps to world
    P(i,j) = center + R(phi) . ( (j' - c)*s , (i - c)*s ),  j' = j mirrored?
matching export_heightmap.py's grid_rotation_deg (col axis =
(cos phi, sin phi), row axis = (-sin phi, cos phi), UE world XY, meters)
followed by its flip_cols output option.
"""

import json
import math
import os
import sys

import numpy as np

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import png16  # noqa: E402

MIN_OVERLAP_FRAC = 0.40


# ---------------------------------------------------------------------------
# sampling helpers
# ---------------------------------------------------------------------------

def bilinear(a, ii, jj):
    """Sample array `a` at float coords (ii=row, jj=col); (0, invalid) outside."""
    h, w = a.shape
    valid = (ii >= 0) & (ii <= h - 1) & (jj >= 0) & (jj <= w - 1)
    i0 = np.clip(np.floor(ii).astype(int), 0, h - 1)
    j0 = np.clip(np.floor(jj).astype(int), 0, w - 1)
    i1 = np.clip(i0 + 1, 0, h - 1)
    j1 = np.clip(j0 + 1, 0, w - 1)
    fi = np.clip(ii - i0, 0, 1)
    fj = np.clip(jj - j0, 0, 1)
    out = (a[i0, j0] * (1 - fi) * (1 - fj) + a[i0, j1] * (1 - fi) * fj
           + a[i1, j0] * fi * (1 - fj) + a[i1, j1] * fi * fj)
    return np.where(valid, out, 0.0), valid


def build_rotated_template(old, mask, scale_m, res_m, phi_deg):
    """
    Render the legacy map into the scan's pixel grid, rotated by phi and
    scaled to scale_m meters per legacy cell.
    Forward convention (used everywhere in this file): legacy cell (i, j)
    lands on canvas at
        dC = ( cos(phi)*(j-t) - sin(phi)*(i-t) ) * k
        dR = ( sin(phi)*(j-t) + cos(phi)*(i-t) ) * k
    relative to the canvas center; k = scale_m / res_m, t = legacy center.
    """
    n = old.shape[0]
    t = (n - 1) / 2.0
    k = scale_m / res_m
    phi = math.radians(phi_deg)
    side = int(math.ceil(n * k * (abs(math.cos(phi)) + abs(math.sin(phi))))) + 2
    cc = (side - 1) / 2.0

    R, C = np.mgrid[0:side, 0:side].astype(np.float64)
    dx, dy = C - cc, R - cc
    # inverse of the forward mapping above
    jj = (math.cos(phi) * dx + math.sin(phi) * dy) / k + t
    ii = (-math.sin(phi) * dx + math.cos(phi) * dy) / k + t
    tpl, inside = bilinear(old, ii, jj)
    msk, _ = bilinear(mask.astype(np.float64), ii, jj)
    msk = ((msk > 0.99) & inside).astype(np.float64)
    return tpl, msk, cc, t


def masked_ncc(image, template, mask):
    """
    Masked normalized cross-correlation (Padfield) of `template` (validity
    `mask`) slid over `image`; partial border overlap allowed.
    r_map[idx] corresponds to template top-left at image cell
    (idx[0] - (th-1), idx[1] - (tw-1)).
    """
    ih, iw = image.shape
    th, tw = template.shape
    ph, pw = ih + th - 1, iw + tw - 1

    def corr(a, k):
        fa = np.fft.rfft2(a, (ph, pw))
        fk = np.fft.rfft2(k, (ph, pw))
        out = np.fft.irfft2(fa * np.conj(fk), (ph, pw))
        return np.roll(out, (th - 1, tw - 1), axis=(0, 1))

    ones = np.ones_like(image)
    tm = template * mask
    t2m = template * template * mask

    n = corr(ones, mask)
    si = corr(image, mask)
    si2 = corr(image * image, mask)
    st = corr(ones, tm)
    st2 = corr(ones, t2m)
    cross = corr(image, tm)

    n = np.maximum(n, 1e-9)
    var_i = si2 - si * si / n
    var_t = st2 - st * st / n
    denom = np.sqrt(np.maximum(var_i, 0) * np.maximum(var_t, 0))
    with np.errstate(invalid="ignore", divide="ignore"):
        r = (cross - si * st / n) / np.maximum(denom, 1e-9)
    r[(denom < 1e-6) | (n < MIN_OVERLAP_FRAC * mask.sum())] = -2.0
    return r, n, (-(th - 1), -(tw - 1))


def detect_background(old):
    vals, counts = np.unique(old, return_counts=True)
    bg = vals[counts.argmax()]
    return bg if counts.max() / old.size > 0.05 else None


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def evaluate(scan, old, mask, scale_m, res_m, phi_deg):
    tpl, msk, cc, t = build_rotated_template(old, mask, scale_m, res_m, phi_deg)
    if msk.sum() < 100:
        return None
    r, n, base = masked_ncc(scan, tpl, msk)
    idx = np.unravel_index(np.argmax(r), r.shape)
    return {
        "r": float(r[idx]),
        "row0": idx[0] + base[0], "col0": idx[1] + base[1],
        "canvas_center": cc, "legacy_center": t,
        "scale_m": float(scale_m), "phi_deg": float(phi_deg),
        "overlap": float(n[idx]),
    }


def search(scan, old, mask, res_m, log=print):
    """Coarse-to-fine over mirror x angle x scale. Returns best candidate."""
    variants = {False: (old, mask), True: (old[:, ::-1], mask[:, ::-1])}

    # ---- coarse: downsampled scan, full angle sweep -------------------------
    ds = 2
    scan_ds = scan[::ds, ::ds].copy()
    best = None
    log("coarse sweep (mirror x 360deg x scale, at %.0f m)..." % (res_m * ds))
    for mirror, (o, m) in variants.items():
        for phi in range(0, 360, 4):
            for s in (7.8, 8.2, 8.6):
                cand = evaluate(scan_ds, o, m, s, res_m * ds, float(phi))
                if cand and (best is None or cand["r"] > best["r"]):
                    cand["mirror"] = mirror
                    best = cand
                    log("  best: r=%.4f mirror=%s phi=%d scale=%.1f"
                        % (cand["r"], mirror, phi, s))

    # ---- refine at full resolution ------------------------------------------
    log("refining at %.0f m..." % res_m)
    o, m = variants[best["mirror"]]
    stages = [
        (np.arange(best["phi_deg"] - 5, best["phi_deg"] + 5.01, 1.0),
         np.arange(best["scale_m"] - 0.5, best["scale_m"] + 0.51, 0.1)),
        (None, None),  # placeholders filled from current best below
        (None, None),
    ]
    cand = evaluate(scan, o, m, best["scale_m"], res_m, best["phi_deg"])
    if cand:
        cand["mirror"] = best["mirror"]
        best = cand
    for stage in range(3):
        if stage == 0:
            phis, scales = stages[0]
        elif stage == 1:
            phis = np.arange(best["phi_deg"] - 1, best["phi_deg"] + 1.01, 0.2)
            scales = np.arange(best["scale_m"] - 0.1, best["scale_m"] + 0.101, 0.02)
        else:
            phis = np.arange(best["phi_deg"] - 0.25, best["phi_deg"] + 0.251, 0.05)
            scales = np.arange(best["scale_m"] - 0.02, best["scale_m"] + 0.021, 0.005)
        for phi in phis:
            for s in scales:
                cand = evaluate(scan, o, m, float(s), res_m, float(phi))
                if cand and cand["r"] > best["r"]:
                    cand["mirror"] = best["mirror"]
                    best = cand
        log("  stage %d: r=%.4f phi=%.2f scale=%.3f"
            % (stage, best["r"], best["phi_deg"], best["scale_m"]))
    return best


def map_to_world(best, b, res_m, i, j, n):
    """Legacy cell (i, j) -> world meters, using the winning registration."""
    if best["mirror"]:
        j = (n - 1) - j
    phi = math.radians(best["phi_deg"])
    k = best["scale_m"] / res_m
    t = best["legacy_center"]
    dC = (math.cos(phi) * (j - t) - math.sin(phi) * (i - t)) * k
    dR = (math.sin(phi) * (j - t) + math.cos(phi) * (i - t)) * k
    c = best["col0"] + best["canvas_center"] + dC
    r = best["row0"] + best["canvas_center"] + dR
    return b["min_x"] + c * res_m, b["min_y"] + r * res_m


def sample_scan_on_legacy_grid(best, scan, b, res_m, n):
    """Resample the scan onto the legacy grid using the registration."""
    I, J = np.mgrid[0:n, 0:n].astype(np.float64)
    Jm = (n - 1) - J if best["mirror"] else J
    phi = math.radians(best["phi_deg"])
    k = best["scale_m"] / res_m
    t = best["legacy_center"]
    dC = (np.cos(phi) * (Jm - t) - np.sin(phi) * (I - t)) * k
    dR = (np.sin(phi) * (Jm - t) + np.cos(phi) * (I - t)) * k
    cols = best["col0"] + best["canvas_center"] + dC
    rows = best["row0"] + best["canvas_center"] + dR
    samp, valid = bilinear(scan, rows, cols)
    samp[~valid] = np.nan
    return samp


# ---------------------------------------------------------------------------
# self-test: recover a known synthetic pose
# ---------------------------------------------------------------------------

def selftest(scan, res_m, b):
    true_phi, true_scale, mirror = -25.0, 8.30, False
    n = 500
    bg = 16.32
    cx = b["min_x"] + (scan.shape[1] - 1) * res_m / 2.0
    cy = b["min_y"] + (scan.shape[0] - 1) * res_m / 2.0

    I, J = np.mgrid[0:n, 0:n].astype(np.float64)
    t = (n - 1) / 2.0
    phi = math.radians(true_phi)
    u = (J - t) * true_scale
    v = (I - t) * true_scale
    x = cx + u * np.cos(phi) - v * np.sin(phi)
    y = cy + u * np.sin(phi) + v * np.cos(phi)
    rows = (y - b["min_y"]) / res_m
    cols = (x - b["min_x"]) / res_m
    fake, valid = bilinear(scan, rows, cols)
    fake = np.round(fake / 0.128) * 0.128          # mimic 8-bit quantization
    fake[~valid] = bg

    mask = np.abs(fake - bg) > 0.02
    print("selftest: true phi=%.2f scale=%.2f center=(%.1f, %.1f), "
          "%d%% valid" % (true_phi, true_scale, cx, cy, 100 * mask.mean()))
    best = search(scan, fake, mask, res_m)
    fcx, fcy = map_to_world(best, b, res_m, t, t, n)
    print("recovered: phi=%.2f scale=%.3f mirror=%s center=(%.1f, %.1f) r=%.4f"
          % (best["phi_deg"], best["scale_m"], best["mirror"], fcx, fcy,
             best["r"]))
    dphi = (best["phi_deg"] - true_phi + 180) % 360 - 180
    ok = (abs(dphi) < 0.3 and abs(best["scale_m"] - true_scale) < 0.05
          and abs(fcx - cx) < 12 and abs(fcy - cy) < 12
          and not best["mirror"] and best["r"] > 0.97)
    print("SELFTEST %s" % ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------

def main(argv):
    if len(argv) >= 3 and argv[1] == "--selftest":
        scan_dir = argv[2]
        meta = json.load(open(os.path.join(scan_dir, "meta.json")))
        scan = np.array(json.load(open(os.path.join(scan_dir, "heightmap.json"))),
                        dtype=np.float64)
        return selftest(scan, float(meta["resolution_m"]), meta["bounds_m"])

    if len(argv) < 3:
        print(__doc__)
        return 2
    old_path, scan_dir = argv[1], argv[2]
    out_dir = argv[3] if len(argv) > 3 else os.path.join(scan_dir, "alignment")
    os.makedirs(out_dir, exist_ok=True)

    old = np.array(json.load(open(old_path)), dtype=np.float64)
    meta = json.load(open(os.path.join(scan_dir, "meta.json")))
    scan = np.array(json.load(open(os.path.join(scan_dir, "heightmap.json"))),
                    dtype=np.float64)
    res_m = float(meta["resolution_m"])
    b = meta["bounds_m"]
    o = meta["orientation"]
    assert not (o["transpose"] or o["flip_rows"] or o["flip_cols"]) and \
        not meta.get("grid_rotation_deg"), \
        "scan must be exported axis-aligned (no rotation/transpose/flips)"

    bg = detect_background(old)
    if bg is None:
        mask = np.ones(old.shape, dtype=bool)
        print("no constant background detected - using full mask")
    else:
        mask = np.abs(old - bg) > 0.02
        print("legacy background: %.2f m (%.0f%% masked out)"
              % (bg, 100 * (1 - mask.mean())))

    best = search(scan, old, mask, res_m)
    n = old.shape[0]
    size_m = best["scale_m"] * n
    print("\nBEST: r=%.4f  rotation=%.2f deg  mirror=%s  scale=%.3f m/cell  "
          "size=%.1f m  overlap=%.0f cells"
          % (best["r"], best["phi_deg"], best["mirror"], best["scale_m"],
             size_m, best["overlap"]))

    cx, cy = map_to_world(best, b, res_m, (n - 1) / 2.0, (n - 1) / 2.0, n)
    half = size_m / 2.0
    overrides = {
        "bounds_m": {
            "min_x": round(cx - half, 1), "max_x": round(cx + half, 1),
            "min_y": round(cy - half, 1), "max_y": round(cy + half, 1),
        },
        "grid_rotation_deg": round(best["phi_deg"], 2),
        "flip_cols": bool(best["mirror"]),
        "downsample_to": 500,
    }
    print("\n=== overrides for the final export ===")
    print(json.dumps(overrides, indent=2))

    samp = sample_scan_on_legacy_grid(best, scan, b, res_m, n)

    def to8(a):
        a = np.nan_to_num(a, nan=np.nanmin(a))
        a = a - a.min()
        m = a.max()
        return (a / m * 255).astype(np.uint8) if m > 0 else a.astype(np.uint8)

    png16.write_gray_png(os.path.join(out_dir, "legacy_500.png"), n, n,
                         to8(old).tolist(), bit_depth=8)
    png16.write_gray_png(os.path.join(out_dir, "scan_aligned_500.png"), n, n,
                         to8(samp).tolist(), bit_depth=8)
    print("\npreviews in %s - legacy_500.png and scan_aligned_500.png "
          "should show the same terrain in the same pose." % out_dir)
    if best["r"] < 0.85:
        print("WARNING: correlation %.2f is weak - inspect the previews "
              "before trusting these values!" % best["r"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
