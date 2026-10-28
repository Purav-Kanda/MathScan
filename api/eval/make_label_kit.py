"""
Build a labelling kit from the old calculus note pages: cut each page into
horizontal bands of ink (one band per row of work) and save them as crops.
Labels are typed in the labelling tool afterwards (see label_server.py).

WHY bands and not text lines: math is two-dimensional (stacked fractions,
limits, integral bounds), so "one line" is not a clean unit. A band is a run
of rows that contain ink, separated from the next run by blank space.

WHY ink is "dark OR colored": the pages use pale grey ruled lines and dots,
which must not count as ink, while the green/blue/purple pens are bright but
not dark. Pale grey has almost no color spread and a high brightness, so it
falls out of both tests.

Run from api/:  python eval/make_label_kit.py   (add --force to rebuild existing crops)
Then label:     python eval/label_server.py
Output (not for git): eval/label_set/crops/*.png
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
IMAGES_DIR = HERE / "test_set" / "images"
OUT_DIR = HERE / "label_set"
CROPS_DIR = OUT_DIR / "crops"

# Real math pages only. calculus-001 is a notebook cover and calculus-002 is a
# blank placeholder, so both are left out on purpose.
SOURCE_SUFFIX = ".jpg.bak"  # the disabled originals keep their old extension
PAGE_STEMS = [
    "calculus-003", "calculus-004", "calculus-005", "calculus-006",
    "calculus_p15", "calculus_p25", "calculus_p40", "calculus_p55",
    "calculus_p70", "calculus_p85", "calculus_p100",
]

DARK_BELOW = 120        # mean brightness under this counts as ink
COLOR_SPREAD_OVER = 55  # max channel minus min channel over this counts as ink
INK_ROW_FRAC = 0.002    # a row has ink if this share of its pixels is ink
SPLIT_GAP_FRAC = 0.012  # blank rows (share of page height) that split two bands
MIN_BAND_FRAC = 0.008   # bands shorter than this share of page height are specks
PAD_PX = 14


def ink_mask(rgb: np.ndarray) -> np.ndarray:
    spread = rgb.max(axis=2).astype(int) - rgb.min(axis=2).astype(int)
    return (rgb.mean(axis=2) < DARK_BELOW) | (spread > COLOR_SPREAD_OVER)


def find_bands(mask: np.ndarray) -> list:
    """Return (top, bottom) row pairs, one per band of ink."""
    height, width = mask.shape
    has_ink = mask.sum(axis=1) >= max(2, int(width * INK_ROW_FRAC))
    gap_limit = int(height * SPLIT_GAP_FRAC)
    min_height = int(height * MIN_BAND_FRAC)

    found, top, last = [], None, None
    for y, row_ink in enumerate(has_ink):
        if row_ink:
            if top is None:
                top = y
            last = y
        elif top is not None and y - last > gap_limit:
            found.append((top, last))
            top = None
    if top is not None:
        found.append((top, last))
    return [(a, b) for a, b in found if b - a >= min_height]


def cut_page(stem: str) -> list:
    src = IMAGES_DIR / f"{stem}{SOURCE_SUFFIX}"
    page = Image.open(src).convert("RGB")
    pixels = np.array(page)
    mask = ink_mask(pixels)
    height, width = mask.shape

    names = []
    for idx, (top, bottom) in enumerate(find_bands(mask), start=1):
        cols = np.where(mask[top : bottom + 1].any(axis=0))[0]
        left, right = int(cols[0]), int(cols[-1])
        box = (
            max(0, left - PAD_PX),
            max(0, top - PAD_PX),
            min(width, right + PAD_PX),
            min(height, bottom + PAD_PX),
        )
        name = f"{stem}_{idx:02d}.png"
        page.crop(box).save(CROPS_DIR / name)
        names.append(name)
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="rebuild even if crops exist (crop names are numbered by position, "
        "so rebuilding with different settings can shift them away from saved labels)",
    )
    args = parser.parse_args()

    if CROPS_DIR.exists() and any(CROPS_DIR.glob("*.png")) and not args.force:
        sys.exit(
            f"{CROPS_DIR} already has crops. Rebuilding can renumber them and break saved labels; "
            "use --force."
        )

    CROPS_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for stem in PAGE_STEMS:
        made = cut_page(stem)
        print(f"{stem}: {len(made)} crops")
        total += len(made)

    print(f"\ntotal crops: {total}")
    print("next: python eval/label_server.py")


if __name__ == "__main__":
    main()
