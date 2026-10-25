"""
Offline worker: run Pix2Text and PaddleOCR on one or more images and print
each image's two page results (text + mean confidence) as one marker line.
Groq correction is disabled so the saved text is the raw engine output a
selector would pick between.

Models load once per process, so pass several images per call when you can.
Keep batches small (see collect_batch.py): an earlier note in
_recognize_one.py records a silent native crash in long-lived processes.
"""

import json
import os
import sys
import time
from pathlib import Path

os.environ.pop("GROQ_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import inference

RESULT_MARKER = "RESULT_JSON:"


def _page_view(page: dict, seconds: float) -> dict:
    regions = page["regions"]
    return {
        "text": regions[0]["latex"] if regions else "",
        "confidence": page["confidence_mean"],
        "region_count": regions[0].get("region_count", 0) if regions else 0,
        "seconds": round(seconds, 2),
    }


def collect_one(img_path: Path) -> dict:
    img = Image.open(img_path).convert("RGB")

    started = time.time()
    p2t_page = inference.recognize_page(img, try_fallback=False)
    mid = time.time()
    paddle_page = inference._collapse_to_page_result(inference._recognize_page_paddleocr(img))
    done = time.time()

    return {
        "image": img_path.name,
        "pix2text": _page_view(p2t_page, mid - started),
        "paddle": _page_view(paddle_page, done - mid),
    }


def main():
    paths = [Path(arg) for arg in sys.argv[1:]]
    if not paths:
        sys.exit("usage: python eval/_collect_one.py <image_path> [<image_path> ...]")

    inference.load_model()
    for path in paths:
        try:
            row = collect_one(path)
        except Exception as exc:
            # One bad image should not cost the rest of the batch. The batch
            # runner notices the missing row and a rerun retries it.
            print(f"FAILED {path.name}: {exc!r}", file=sys.stderr, flush=True)
            continue
        print(RESULT_MARKER + json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
