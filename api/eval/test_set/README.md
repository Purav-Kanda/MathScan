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
