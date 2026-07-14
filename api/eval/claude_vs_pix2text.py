"""
Compares Pix2Text's OCR output against Claude's vision model on real pages
from your own PDFs -- a manual evaluation tool you run locally with your
own Anthropic API key, NOT part of the automated pytest suite (api/tests/)
and NOT wired into the live app.

WHY this exists, and why it's a separate offline script rather than
something built straight into the live, publicly-deployed API: real
testing (6 subjects' worth of actual class notes) found that Pix2Text
badly hallucinates -- random Chinese characters, decoder repetition loops
-- on dense handwritten prose, not just isolated equations. The app's own
confidence score correctly flagged every one of those pages as unreliable
(<=40%), so the signal is trustworthy; the open question is whether routing
low-confidence pages to Claude actually fixes it, and how much that would
cost per page. Answering that on the live app would mean any random
visitor's low-confidence page silently triggers a real, billed Claude API
call -- with no usage cap or accounts system built yet (see ROADMAP.md M5),
that's unbounded cost exposure for something not yet proven to help. This
script answers the same question on a small, controlled, YOU-triggered
sample instead, with the cost shown up front.

Usage, paid comparison against Claude (from api/, with the project venv
active):
    pip install anthropic
    $env:ANTHROPIC_API_KEY = "sk-..."          (PowerShell)
    python eval/claude_vs_pix2text.py "path/to/some.pdf" --pages 3

WHY --pages defaults to a small number, not every page in the PDF: each
page sent to Claude costs real money (~$0.004-$0.04/page depending on
model -- see 11_Cost_Analysis_MathScan.md section 4.2). A 40-page lecture
PDF at the higher end of that range is a real bill, not a rounding error --
defaulting to a small sample keeps a first run cheap and predictable
instead of an accidental surprise charge.

Usage, free-only (no API key, no cost, tests Pix2Text alone -- e.g. to try
whether a higher --resized-shape helps a page that's currently garbled):
    python eval/claude_vs_pix2text.py "path/to/some.pdf" --pages 3 --no-claude --resized-shape 1536
"""

import argparse
import base64
import os
import sys
from io import BytesIO
from pathlib import Path

# WHY this path manipulation: this script lives in api/eval/, one level
# below api/ -- it needs api/inference.py importable the same way
# api/tests/pytest.ini's `pythonpath = .` makes it importable for pytest,
# but a standalone script invoked with `python eval/script.py` doesn't get
# that automatically, regardless of which directory it's run from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf2image import convert_from_path

import inference

PROMPT = (
    "Transcribe everything handwritten on this page exactly as written. "
    "Any math should be transcribed as LaTeX source (no $ or \\[ \\] "
    "wrappers needed, just the math itself). Preserve line breaks between "
    "distinct lines/thoughts. Output only the transcription -- no "
    "commentary, no preamble."
)

# Rough per-page cost range from 11_Cost_Analysis_MathScan.md section 4.2 --
# kept in one place here so the pre-run estimate below doesn't drift from
# the real numbers already computed there.
COST_LOW_PER_PAGE = 0.004  # Haiku
COST_HIGH_PER_PAGE = 0.038  # Opus


def adaptive_binarize(image):
    """
    WHY this is different from inference.py's enhance_contrast(): that
    function (ImageOps.autocontrast) looks at the WHOLE image's histogram
    and stretches it once, globally -- a single darkest-to-black,
    lightest-to-white mapping applied everywhere. That doesn't help much if
    lighting is uneven ACROSS the page (a shadow on one corner of a phone
    photo, for example) -- the shadowed region and the well-lit region
    would need different corrections, which a single global stretch can't
    give them. Adaptive thresholding instead looks at small local
    neighborhoods independently and decides black-vs-white for EACH region
    based on its own local surroundings -- the standard classical-CV answer
    to uneven document lighting, not something Pix2Text/PaddleOCR do
    internally.

    WHY untested against real accuracy numbers as of writing this: no
    working PaddleOCR/Pix2Text environment was available to verify this the
    way --resized-shape and --dpi were verified earlier -- this is a
    reasonable, standard technique, not a guaranteed win. That's what
    running --adaptive-threshold on a real page is actually for.
    """
    import cv2
    import numpy as np
    from PIL import Image

    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    binarized = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=25,  # size of the local neighborhood examined per pixel --
        # large enough to span real handwriting strokes without being so
        # large it degenerates back into a global threshold.
        C=10,  # constant subtracted from the local mean -- higher values
        # bias toward more black pixels, lower toward more white; 10 is a
        # reasonable starting point for handwriting on lined paper, not a
        # value tuned against real results yet.
    )
    return Image.fromarray(binarized).convert("RGB")


def call_claude(image, api_key: str, model: str) -> str:
    import anthropic  # imported here, not at module top: keeps this an

    # opt-in dependency (pip install anthropic) rather than something
    # api/requirements.txt needs for the live app, which doesn't use it.

    buf = BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": b64},
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    return response.content[0].text


# WHY EasyOCR specifically, as the free second-model candidate: it does
# its own region detection + recognition + per-region confidence out of the
# box (via readtext()), the same shape of output Pix2Text already gives us
# -- unlike, say, TrOCR, which expects a single pre-cropped line/region as
# input and has no built-in page-level detection step of its own. Matching
# interfaces matters here specifically because it's what makes a later
# "compare confidence, pick the winner" ensemble feasible without first
# building a separate region-detection pipeline.
_easyocr_reader = None


def run_easyocr(image, decoder: str = "greedy") -> tuple[str, float | None]:
    global _easyocr_reader
    import numpy as np
    import easyocr

    if _easyocr_reader is None:
        # WHY built once and cached in a global, not per call: EasyOCR loads
        # its own model weights on Reader() construction -- same "load once,
        # reuse" reasoning as inference.py's _p2t global, just for this
        # standalone script instead of the live app's process lifetime.
        print("Loading EasyOCR (first call only)...")
        _easyocr_reader = easyocr.Reader(["en"])

    # WHY decoder is exposed as a parameter: EasyOCR's default "greedy"
    # decode picks the single most likely character at each step, fast but
    # more prone to compounding small mistakes. "beamsearch" keeps several
    # candidate sequences in play and picks the best-scoring one at the
    # end -- slower, but worth testing here since there's no real-time
    # constraint on this evaluation script the way there would be on a live
    # request.
    results = _easyocr_reader.readtext(np.array(image), decoder=decoder)
    if not results:
        return "(nothing detected)", None
    lines = [text for (_bbox, text, _conf) in results]
    confidences = [conf for (_bbox, _text, conf) in results]
    mean_confidence = sum(confidences) / len(confidences)
    return "\n".join(lines), mean_confidence


# WHY PaddleOCR is worth testing alongside EasyOCR: independent benchmarks
# put general handwriting accuracy around ~73% for PaddleOCR vs ~62% for
# EasyOCR (and ~45% for Tesseract) -- a meaningful enough gap to be worth a
# real test, not just theoretical. Same "load once, cache in a global"
# reasoning as EasyOCR above.
_paddle_reader = None


def run_paddleocr(image) -> tuple[str, float | None]:
    global _paddle_reader
    import numpy as np
    from paddleocr import PaddleOCR

    if _paddle_reader is None:
        print("Loading PaddleOCR (first call only)...")
        _paddle_reader = PaddleOCR(use_textline_orientation=True, lang="en")

    # WHY [:, :, ::-1]: PIL images are RGB; PaddleOCR (built on OpenCV
    # conventions) expects BGR channel order. Skipping this wouldn't crash,
    # but would silently feed it a color-channel-swapped image -- unlikely
    # to matter much for mostly-grayscale handwriting, but cheap to do
    # correctly rather than rely on that assumption.
    image_bgr = np.array(image)[:, :, ::-1]

    # WHY .predict(), not .ocr(cls=True): PaddleOCR 3.x's .ocr() is a
    # deprecated shim over .predict() that no longer accepts the old `cls`
    # kwarg at all -- confirmed by a real TypeError hit running this against
    # paddleocr 3.7.0. .predict() also returns a different result shape
    # than the old 2.x .ocr() API (dict-like objects with 'rec_texts' /
    # 'rec_scores' keys, not [bbox, (text, score)] tuples), which is what
    # the parsing below expects.
    result = _paddle_reader.predict(image_bgr)

    if not result:
        return "(nothing detected)", None
    page = result[0]
    texts = page.get("rec_texts") or []
    scores = page.get("rec_scores") or []
    if not texts:
        return "(nothing detected)", None
    mean_confidence = sum(scores) / len(scores) if scores else None
    return "\n".join(texts), mean_confidence


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", help="Path to a PDF of handwritten notes")
    parser.add_argument("--pages", type=int, default=3, help="Max pages to sample (cost control, default 3)")
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5-20251001",
        help="Claude model to compare against (default: Haiku, the cheapest option)",
    )
    parser.add_argument(
        "--resized-shape",
        type=int,
        default=768,
        help="Pix2Text's internal resize size before OCR (default 768, the live app's current value). "
        "Try a higher value (e.g. 1536) to test, for free, whether losing detail at 768px is part of "
        "why dense prose pages come out garbled -- no API key or cost involved in this part.",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Skip Claude entirely -- run only the free Pix2Text side (e.g. to test --resized-shape "
        "without needing an API key or spending anything).",
    )
    parser.add_argument(
        "--try-easyocr",
        action="store_true",
        help="Also run EasyOCR (pip install easyocr) on each page, free, as a candidate second model "
        "for a future confidence-based ensemble -- see this file's run_easyocr() docstring for why "
        "EasyOCR specifically.",
    )
    parser.add_argument(
        "--easyocr-decoder",
        default="greedy",
        choices=["greedy", "beamsearch"],
        help="EasyOCR's decode strategy -- 'beamsearch' is slower but often more accurate than the "
        "default 'greedy'; worth testing since this script has no real-time constraint.",
    )
    parser.add_argument(
        "--try-paddleocr",
        action="store_true",
        help="Also run PaddleOCR (pip install paddleocr) on each page, free -- independent benchmarks "
        "put it meaningfully ahead of EasyOCR on general handwriting, worth testing directly.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Resolution to render PDF pages at before either model sees them (default 200, the live "
        "app's current value). A different, earlier lever than --resized-shape: this controls how much "
        "raw detail exists in the source image at all, before any model-internal resizing happens.",
    )
    parser.add_argument(
        "--adaptive-threshold",
        action="store_true",
        help="Apply adaptive thresholding (local binarization, not the global contrast stretch "
        "inference.py's enhance_contrast() already does) before OCR -- see adaptive_binarize()'s "
        "docstring for why this targets a different problem (uneven lighting/shadows) than what's "
        "already built. Untested against real OCR results as of writing this flag -- that's what "
        "running it is for.",
    )
    args = parser.parse_args()

    api_key = None
    if not args.no_claude:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Set ANTHROPIC_API_KEY first (see this file's docstring for how), or pass --no-claude.")
            sys.exit(1)

        print(
            f"About to send {args.pages} page(s) to {args.model}. "
            f"Estimated cost: ${COST_LOW_PER_PAGE * args.pages:.3f}-${COST_HIGH_PER_PAGE * args.pages:.2f}."
        )
        if input("Continue? [y/N] ").strip().lower() != "y":
            print("Cancelled -- nothing was sent.")
            sys.exit(0)

    print("Loading Pix2Text (~30s)...")
    inference.load_model()

    # WHY first_page/last_page, not convert_from_path(...)[:args.pages]:
    # without these, convert_from_path renders EVERY page of the PDF to an
    # image before Python ever gets to slice the list down -- for a large
    # scanned lecture PDF (dozens of pages), that's minutes of rendering
    # work for pages that just get thrown away. Matches the same fix
    # pdf_preprocessor.py's split_pages() already uses in the real app, for
    # the same reason.
    print(f"Rendering page(s) 1-{args.pages} from the PDF at {args.dpi} DPI...")
    images = convert_from_path(args.pdf, dpi=args.dpi, first_page=1, last_page=args.pages)
    if not images:
        print("No pages found in that PDF.")
        sys.exit(1)

    for i, image in enumerate(images):
        image = image.convert("RGB")
        if args.adaptive_threshold:
            image = adaptive_binarize(image)

        p2t_result = inference.recognize_page(image, resized_shape=args.resized_shape)
        p2t_text = "\n".join(r["latex"] for r in p2t_result["regions"]) or "(nothing detected)"
        confidence = p2t_result["confidence_mean"]
        confidence_str = f"{confidence * 100:.0f}%" if confidence is not None else "n/a"

        print(f"\n{'=' * 70}\nPage {i + 1} of {args.pdf}\n{'=' * 70}")
        print(f"-- Pix2Text (resized_shape={args.resized_shape}, confidence: {confidence_str}) --")
        print(p2t_text)

        if args.try_easyocr:
            easy_text, easy_confidence = run_easyocr(image, decoder=args.easyocr_decoder)
            easy_confidence_str = f"{easy_confidence * 100:.0f}%" if easy_confidence is not None else "n/a"
            print(f"\n-- EasyOCR (confidence: {easy_confidence_str}) --")
            print(easy_text)

        if args.try_paddleocr:
            paddle_text, paddle_confidence = run_paddleocr(image)
            paddle_confidence_str = f"{paddle_confidence * 100:.0f}%" if paddle_confidence is not None else "n/a"
            print(f"\n-- PaddleOCR (confidence: {paddle_confidence_str}) --")
            print(paddle_text)

        if args.no_claude:
            continue

        print(f"\n-- Claude ({args.model}) --")
        try:
            claude_text = call_claude(image, api_key, args.model)
            print(claude_text)
        except Exception as e:
            print(f"Claude call failed: {e}")


if __name__ == "__main__":
    main()
