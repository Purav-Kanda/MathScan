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

All six images are real photos/scans of real students' handwritten
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
