"""
Worker script spawned as a fresh subprocess per image by accuracy_benchmark.py.

WHY THIS EXISTS: running recognize_page() for multiple images back-to-back in
one long-lived Python process crashed silently (no Python traceback, no
summary printed, shell prompt just returned) after 2 successful images out
of 3. Running the exact same third image alone, in its own process, worked
fine -- ruling out a bad image and pointing at native-code (Paddle/ONNX)
resource buildup across repeated model loads inside a single process, which
can segfault without ever surfacing as a catchable Python exception.

Isolating each image into its own subprocess means each one gets a clean
process with fresh native state, so a crash on image N can't be caused by
whatever image 1..N-1 left behind -- and if a specific image genuinely does
crash on its own, that failure is now attributable to that image alone, not
disguised as "the whole benchmark run died partway through."

Communicates back to the parent via a single marker-prefixed stdout line
(RESULT_JSON:<json>) so it can be picked out from the large amount of
non-JSON logging Paddle/Pix2Text print to stdout/stderr during model loading.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import inference

RESULT_MARKER = "RESULT_JSON:"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path")
    parser.add_argument("--apply-contrast", action="store_true")
    parser.add_argument("--no-fallback", dest="try_fallback", action="store_false", default=True)
    parser.add_argument(
        "--resized-shape",
        type=int,
        default=768,
        help="Passed straight to Pix2Text's recognize_text_formula. Default (768) matches the live "
        "app's default -- override to test whether a higher value (e.g. 1024, 1536) recovers detail "
        "on dense/busy handwritten pages before OCR ever sees them, a free (no API cost) hypothesis "
        "flagged in recognize_page's docstring but never actually measured until now.",
    )
    args = parser.parse_args()

    inference.load_model()
    image = Image.open(args.image_path).convert("RGB")
    result = inference.recognize_page(
        image,
        apply_contrast=args.apply_contrast,
        try_fallback=args.try_fallback,
        resized_shape=args.resized_shape,
    )
    print(RESULT_MARKER + json.dumps(result))


if __name__ == "__main__":
    main()
