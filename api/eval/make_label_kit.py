"""
Build a labelling kit from the old calculus note pages: cut each page into
horizontal bands of ink (one band per row of work), save them as crops, and
write label.html, a small offline page for typing the correct LaTeX under
each crop.

WHY bands and not text lines: math is two-dimensional (stacked fractions,
limits, integral bounds), so "one line" is not a clean unit. A band is a run
of rows that contain ink, separated from the next run by blank space.

WHY ink is "dark OR colored": the pages use pale grey ruled lines and dots,
which must not count as ink, while the green/blue/purple pens are bright but
not dark. Pale grey has almost no color spread and a high brightness, so it
falls out of both tests.

Run from api/:  python eval/make_label_kit.py
Output (not for git): eval/label_set/crops/*.png and eval/label_set/label.html
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
IMAGES_DIR = HERE / "test_set" / "images"
OUT_DIR = HERE / "label_set"
CROPS_DIR = OUT_DIR / "crops"

# Real math pages only. calculus-001 is a notebook cover and calculus-002 is a
# blank placeholder, so both are left out on purpose.
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
    src = IMAGES_DIR / f"{stem}.jpg.bak"
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


PAGE_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MathScan labelling</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 16px; }
  .item { border: 1px solid #ccc; border-radius: 8px; padding: 10px; margin: 14px 0; }
  .item img { max-width: 100%; border: 1px solid #eee; }
  textarea { width: 100%; box-sizing: border-box; min-height: 64px; font-family: monospace; font-size: 15px; }
  .name { color: #666; font-size: 13px; margin-bottom: 6px; }
  .bar { position: sticky; top: 0; background: #fff; padding: 8px 0; border-bottom: 1px solid #ddd; }
  .done { border-color: #4a4; }
</style>
<h1>Label the crops</h1>
<ul>
  <li>Type the correct LaTeX for everything in the crop, in reading order. One line per row.</li>
  <li>Math: write it as LaTeX, for example <code>\\int_{0}^{1} \\frac{1}{\\sqrt{x}} dx</code>. Words: plain text.</li>
  <li>Ignore arrows, underlines and circles. Include side notes only if you can read them.</li>
  <li>Blank or unreadable crop: tick "skip".</li>
  <li>Your typing saves in this browser automatically. Click Download when done and save the file as <code>labels.json</code> in this folder.</li>
</ul>
<div class="bar"><span id="progress"></span> <button id="dl">Download labels.json</button></div>
<div id="list"></div>
<script>
const CROPS = __CROPS__;
const STORE_KEY = "mathscan-label-kit-v1";
let saved = {};
try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch (e) { saved = {}; }

function persist() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(saved)); } catch (e) {}
}
function isDone(name) {
  const rec = saved[name];
  return !!rec && (rec.skip || rec.latex.trim().length > 0);
}
function refresh() {
  const n = CROPS.filter(isDone).length;
  document.getElementById("progress").textContent = n + " of " + CROPS.length + " done";
}

const host = document.getElementById("list");
CROPS.forEach(function (name) {
  const rec = saved[name] || { latex: "", skip: false };
  saved[name] = rec;

  const box = document.createElement("div");
  box.className = "item" + (isDone(name) ? " done" : "");

  const label = document.createElement("div");
  label.className = "name";
  label.textContent = name;

  const img = document.createElement("img");
  img.src = "crops/" + name;
  img.loading = "lazy";

  const area = document.createElement("textarea");
  area.value = rec.latex;
  area.addEventListener("input", function () {
    rec.latex = area.value; persist(); refresh();
    box.className = "item" + (isDone(name) ? " done" : "");
  });

  const skipLabel = document.createElement("label");
  const skipBox = document.createElement("input");
  skipBox.type = "checkbox";
  skipBox.checked = rec.skip;
  skipBox.addEventListener("change", function () {
    rec.skip = skipBox.checked; persist(); refresh();
    box.className = "item" + (isDone(name) ? " done" : "");
  });
  skipLabel.appendChild(skipBox);
  skipLabel.appendChild(document.createTextNode(" skip (blank or unreadable)"));

  box.append(label, img, area, skipLabel);
  host.appendChild(box);
});

document.getElementById("dl").addEventListener("click", function () {
  const out = {};
  CROPS.forEach(function (name) { out[name] = saved[name]; });
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "labels.json";
  link.click();
});
refresh();
</script>
"""


def main():
    CROPS_DIR.mkdir(parents=True, exist_ok=True)
    all_crops = []
    for stem in PAGE_STEMS:
        made = cut_page(stem)
        print(f"{stem}: {len(made)} crops")
        all_crops.extend(made)

    page_html = PAGE_TEMPLATE.replace("__CROPS__", json.dumps(all_crops))
    (OUT_DIR / "label.html").write_text(page_html, encoding="utf-8")
    print(f"\ntotal crops: {len(all_crops)}")
    print(f"open: {OUT_DIR / 'label.html'}")


if __name__ == "__main__":
    main()
