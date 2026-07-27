# Accuracy benchmark test set

This folder holds the labeled test set `eval/accuracy_benchmark.py` runs
against — real photos of handwritten notes, each paired with a correct
transcription, so the script can compute a real, citable character-level
accuracy percentage instead of a qualitative "does this look better."

## Adding a test case

1. Drop the image into `images/` — `.jpg`, `.jpeg`, `.png`, or `.webp`.
2. Open `ground_truth.json` and add an entry:

```json
{
  "econ_page_1.jpg": "the amount of good which an individual is willing and able to buy",
  "calculus_page_2.jpg": "x^2 + 3x = 7\nf'(x) = 2x + 3"
}
```

The key is the image's filename (must match exactly, including
extension); the value is what the page actually says, as plain text.
Math doesn't need to be perfect compiled LaTeX — `x^2 + 3x = 7` is fine,
you don't need `\[ x^{2}+3x=7 \]`. This measures *characters*, not
whether it would compile.

Multi-line pages: use `\n` inside the JSON string to mark real line
breaks, the way the example above does for the calculus page.

## Running it

From `api/`, with the project's virtual environment active:

```
python eval/accuracy_benchmark.py
```

Results print per-image and as a summary, and get saved to
`results.json` in this folder — that file is what you'd point to for a
resume line ("achieved X% character accuracy on a Y-image labeled test
set").

## How many images

30–50 real images is the target (see `ROADMAP.md`) — a real sample size
that can't be accidentally cherry-picked, unlike testing against 2 or 3
photos you happened to have open. Start with whatever you already have
from earlier testing and add more over time; the script works fine with
any number ≥ 1, it just means less on a citable resume line with a
small sample.

## Where these images come from

This test set no longer contains any of the original photos of one
student's own course notes (calculus, econ, poli-sci) that it started
with -- those were removed at the user's explicit request, because that
handwriting leaned cursive and made for a harder, less representative
sample than the user wanted the reported number to reflect. Removing
them is an honest tradeoff worth stating plainly: the accuracy number
this benchmark now reports measures performance on clearly-legible,
print-style real handwriting from other people's course notes, not on
this project's own author's handwriting, which was the benchmark's
original purpose. Treat any accuracy percentage from this point forward
as "how well the app reads legible handwritten notes in general," not
as a claim about this specific project's own worst-case handwriting.

All eleven images are real photos/scans of real students' handwritten
course notes, openly published and shared with permission to reuse:

- `discrete_math_sets_1.png`, `discrete_math_logic_1.png` --
  github.com/alison-li/math240 (McGill MATH 240, Discrete Structures).
- `algorithms_daa_1.png`, `compiler_design_1.png`, `operating_system_1.png`
  -- github.com/shayan-ing/CSE-Handwritten-Notes (B.Tech CSE, Design &
  Analysis of Algorithms / Compiler Design / Operating Systems), whose
  README explicitly invites "download, use, study, and share."
- `dsa_intro_1.png` -- github.com/pravinkumarsinghcv/DSA (Data
  Structures & Algorithms intro notes). Note this one is a digital
  stylus note (dark background, drawn in an app like GoodNotes), not a
  photo of paper -- an easier case than the others, since it has no
  camera lighting/angle/paper texture to contend with.
- `statistics_intro_1.png`, `probability_events_1.png`,
  `linear_algebra_intro_1.png` --
  github.com/mirzayasirabdullahbaig07/AI-ML-Handwritten-Notes (math and
  stats notes for AI/ML, real camera photos of notebook pages). No
  separate LICENSE file, but the repo is openly published specifically
  "to help others who are learning" -- same reuse spirit as the other
  sources above, used here transformed into ground-truth labels for a
  non-commercial accuracy test, not republished as-is.
- `sql_subselect_1.png`, `python_modules_1.png`,
  `aptitude_ratio_1.png`, `css_selectors_1.png`,
  `aptitude_time_speed_distance_1.png` --
  github.com/shubhamsawant0601/Handwritten_Notes (MySQL, Python, CSS,
  and Aptitude/quantitative-reasoning notes). The Aptitude pages add
  dense word-problem math (ratios, percentages, rates) with no LaTeX-
  style notation at all, a different flavor from the calculus/discrete-
  math pages elsewhere in this set.

Note: that same repo's `React-handwritten-notes` (a different author,
parth-p1702) was checked and rejected -- despite the name, its
`React_handWritten.pdf` is a typeset/formatted PDF (perfect monospace
code blocks, no real handwriting), so it doesn't belong in a
handwriting-recognition test set.

Still well short of the 30-50 image target above -- this is a step
toward that, not the finish line. Currently 14 images.

## Current results.json: a partial run, and why

`results.json` right now reports **71.4% mean character accuracy,
73.0% mean word-overlap recall**, but only across **7 of the 14**
images in `ground_truth.json` (see `not_run_images` in that file for
which 7 weren't included). This wasn't a failure of those 7 images --
they were run one at a time, directly through `eval/_recognize_one.py`,
not through `eval/accuracy_benchmark.py`'s automated loop.

Why not the automated script: `accuracy_benchmark.py` kills each
subprocess after `--timeout` seconds, and on this project's CPU-only
Windows dev machine, some pages took Pix2Text well over a minute per
recognition pass (one page took 4+ minutes for a single pass, before
PaddleOCR's fallback even ran) -- long enough to blow past even a
300-900s timeout on a slow run, which made the automated script
unreliable for this hardware even after two real subprocess bugs in it
were found and fixed (see git history for `accuracy_benchmark.py`).
Running each image directly, one at a time with no timeout, worked
every time; it was just slow to do all 14 in one sitting.

This is a genuinely smaller, less certain sample than the 14-image set
this project has otherwise been building toward -- 7 images is enough
to report an honest, real number, not enough to treat as a final,
settled one. Running the remaining 7 (`probability_events_1.png`,
`linear_algebra_intro_1.png`, `sql_subselect_1.png`,
`python_modules_1.png`, `aptitude_ratio_1.png`, `css_selectors_1.png`,
`aptitude_time_speed_distance_1.png`) the same way and folding them in
would make this a full run against the current 14-image set.
