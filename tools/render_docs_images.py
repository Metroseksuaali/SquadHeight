"""
Render README/doc images from exported heightmaps: hillshaded relief that
makes structures pop, as a side-by-side "stock terrain-only (production) vs
true-surface (ours)" comparison, plus a single colorized hero.

Outputs small web-sized PNGs into docs/. stdlib + numpy + Pillow.

    python tools/render_docs_images.py
"""

import json
import os
import urllib.request

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUTPUT = os.path.join(ROOT, "output")
DOCS = os.path.join(ROOT, "docs")
PROD = "https://squadcalc.app/api/img/maps/%s/heightmap.json"


def load_ours(name):
    p = os.path.join(OUTPUT, name, "heightmap_500.json")
    return np.array(json.load(open(p)), float)


def load_prod(slug):
    with urllib.request.urlopen(PROD % slug, timeout=30) as r:
        return np.array(json.load(r), float)


def hillshade(z, az=315.0, alt=45.0, exag=2.0):
    z = z * exag
    dy, dx = np.gradient(z)
    slope = np.pi / 2.0 - np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    azr, altr = np.radians(360.0 - az + 90.0), np.radians(alt)
    s = (np.sin(altr) * np.sin(slope)
         + np.cos(altr) * np.cos(slope) * np.cos(azr - aspect))
    return np.clip(s, 0, 1)


# simple blue->green->brown->white elevation ramp
_STOPS = [(0.00, (40, 60, 90)), (0.02, (60, 110, 70)), (0.35, (90, 140, 70)),
          (0.6, (150, 130, 80)), (0.85, (140, 110, 90)), (1.0, (245, 245, 245))]


def colormap(norm):
    out = np.zeros(norm.shape + (3,), float)
    for (a, ca), (b, cb) in zip(_STOPS, _STOPS[1:]):
        m = (norm >= a) & (norm <= b)
        t = ((norm[m] - a) / (b - a))[:, None]
        out[m] = np.array(ca) * (1 - t) + np.array(cb) * t
    return out


def to_img(arr, width=620):
    h, w = arr.shape[:2]
    im = Image.fromarray(arr.astype(np.uint8))
    return im.resize((width, int(h * width / w)), Image.LANCZOS)


def shade_gray(z):
    return (hillshade(z)[..., None] * np.array([255, 255, 255])).astype(np.uint8)


def shade_color(z):
    norm = (z - z.min()) / max(z.max() - z.min(), 1e-6)
    rgb = colormap(norm)
    shade = hillshade(z)[..., None]
    return np.clip(rgb * (0.45 + 0.75 * shade), 0, 255).astype(np.uint8)


def label(im, text):
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, im.width, 22], fill=(0, 0, 0))
    d.text((8, 5), text, fill=(255, 255, 255))
    return im


def comparison(name, slug):
    prod, ours = load_prod(slug), load_ours(name)
    left = label(to_img(shade_gray(prod)), "STOCK  (terrain only)")
    right = label(to_img(shade_gray(ours)), "TRUE SURFACE  (this repo)")
    gap = 10
    canvas = Image.new("RGB", (left.width + gap + right.width,
                               max(left.height, right.height)), (255, 255, 255))
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width + gap, 0))
    out = os.path.join(DOCS, "compare_%s.jpg" % name.lower())
    canvas.save(out, "JPEG", quality=88, optimize=True)
    print("%s  (%.0f KB)" % (out, os.path.getsize(out) / 1024))


def hero(name):
    out = os.path.join(DOCS, "hero_%s.jpg" % name.lower())
    to_img(shade_color(load_ours(name)), 820).convert("RGB").save(
        out, "JPEG", quality=88, optimize=True)
    print("%s  (%.0f KB)" % (out, os.path.getsize(out) / 1024))


def main():
    os.makedirs(DOCS, exist_ok=True)
    comparison("AlBasrah", "albasrah")   # the map the in-app proof used
    comparison("Narva", "narva")         # dense city, structures obvious
    hero("Yehorivka")                    # clean large map, good colorized relief
    print("\nDone. Embed the PNGs in README.md.")


if __name__ == "__main__":
    main()
