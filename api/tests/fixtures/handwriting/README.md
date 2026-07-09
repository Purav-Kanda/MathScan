# Handwriting accuracy test set

This folder is the data for M6 (accuracy testing + Mathpix fallback tuning).
Referenced in 10_SDD_MathScan.md section 7.

## What goes here

Real photos of handwritten math, named like:
```
001-algebra.jpg
002-fraction.jpg
003-integral.jpg
004-summation.jpg
005-matrix.jpg
...
```

Target: ~50 images total by the time M6 starts (around Jul 26), spanning:
- plain algebra (like the x^2+3x=7 test)
- fractions
- integrals
- summations
- matrices
- a mix of clean handwriting and messier/realistic handwriting
- ideally a few from other people's handwriting too (with their permission), not just yours -- a model that only works on your own handwriting isn't proving much

## Ground truth

For every image you add, add one matching entry to `ground_truth.json` in
this same folder: the exact correct LaTeX for that image, typed by hand.
That's the "answer key" -- M6's accuracy script will compare the model's
guess against this to compute real character-level accuracy (NFR-010:
target >=80%), instead of eyeballing results like we've been doing so far.

## Why this matters for the resume, not just the deadline

SDD section 11 point 6 calls this out directly: "a real measurement plan"
is one of the things that makes this project look serious to a recruiter,
versus just shipping whatever a pretrained model outputs and hoping it's
good. Collecting this now, gradually, means M6 is "run the script on data
we already have" instead of "scramble to collect 50 images in 3 days."
