"""
Offline worker: run Pix2Text and PaddleOCR on one image and print both page
results (text + mean confidence) as one marker line. Groq correction is
disabled so the saved text is the raw engine output a selector would pick
between.

One image per process, same reason as _recognize_one.py.
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


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python eval/_collect_one.py <image_path>")
    img_path = Path(sys.argv[1])

    inference.load_model()
    img = Image.open(img_path).convert("RGB")

    started = time.time()
    p2t_page = inference.recognize_page(img, try_fallback=False)
    mid = time.time()
    paddle_page = inference._collapse_to_page_result(inference._recognize_page_paddleocr(img))
    done = time.time()

    row = {
        "image": img_path.name,
        "pix2text": _page_view(p2t_page, mid - started),
        "paddle": _page_view(paddle_page, done - mid),
    }
    print(RESULT_MARKER + json.dumps(row))


if __name__ == "__main__":
    main()
