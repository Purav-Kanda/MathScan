"""
Thin wrapper around Pix2Text. Kept separate from main.py/routers so the
recognition logic can be unit-tested without booting FastAPI or loading the
real (slow) model -- see tests/test_inference.py, which fakes this module's
`_p2t` global. This split is what makes the >=70% backend coverage target
(NFR-050) actually achievable instead of aspirational.
"""

from typing import Optional

from PIL import Image, ImageOps
from pix2text import Pix2Text

_p2t: Optional[Pix2Text] = None


def enhance_contrast(image: Image.Image) -> Image.Image:
    """
    FR-007 (Should): optional preprocessing for low-quality scans -- faint
    pencil marks, uneven phone-camera lighting, a slightly washed-out photo.

    WHY autocontrast specifically, not a fixed brightness/contrast multiplier:
    autocontrast looks at the actual histogram of THIS image and stretches it
    so the darkest pixel becomes black and the lightest becomes white (per
    channel), rather than applying a blind fixed adjustment that could
    overcorrect an already-good photo or undercorrect a very faint one. That
    adapts automatically to whatever the user uploaded instead of needing a
    manually-tuned constant.

    WHY cutoff=1: a scan often has a few genuinely near-black (shadow/crease)
    or near-white (glare) pixels that aren't representative of the actual
    writing. Without a cutoff, autocontrast would stretch the histogram to
    include those outliers, under-enhancing everything else. Clipping the
    extreme 1% on each end before stretching gives a more useful result on
    real photos.
    """
    return ImageOps.autocontrast(image, cutoff=1)


def load_model() -> None:
    global _p2t
    if _p2t is None:
        _p2t = Pix2Text.from_config()


def is_loaded() -> bool:
    return _p2t is not None


def recognize_page(image: Image.Image, apply_contrast: bool = False) -> dict:
    """
    Recognize math/text regions in one image.

    HOW THIS FUNCTION GOT HERE (worth knowing -- this was a real debugging
    path, not a design done up front): Pix2Text has two different entry
    points. `Pix2Text.__call__`/`recognize_page` (the "full-page" API) runs
    a document-layout detector FIRST to decide whether each region is a
    paragraph, title, table, or figure, and only OCRs regions it labels as
    text/title/table -- anything it calls "figure" is skipped, returned with
    empty text. On real test photos (including a single equation on an
    otherwise blank page), that layout detector classified everything as
    "figure" and returned nothing, even though the actual math was perfectly
    legible. Switching to `recognize_text_formula(return_text=False)` --
    which skips layout detection and treats the whole image as "may contain
    text and formulas" -- fixed it immediately: 99.99% confidence, correct
    LaTeX, on the same photo that came back empty before. So this function
    uses that method, not the full-page one.

    Trade-off, stated plainly: we lose automatic separation of *multiple
    distinct math regions scattered across a page* (a "Should," not "Must,"
    per SRS FR-015) in exchange for OCR that actually works (SRS NFR-010,
    a "Must": >=80% character accuracy). We still get a bounding box per
    detected line/block, just not a layout-aware region label.

    Returns:
        {
          "regions": [{"latex": str, "type": str, "bbox": [[x,y],...]|None, "confidence": float|None}, ...],
          "confidence_mean": float|None
        }

    Honest caveat (worth knowing, not hiding): the SRS (FR-013) asks for
    per-token confidence. Pix2Text only exposes a per-region `score`, not
    per-token log-probs -- true per-token would require calling the
    underlying TrOCR decoder directly, out of scope for v1.
    """
    if _p2t is None:
        raise RuntimeError("Model not loaded")

    if apply_contrast:
        image = enhance_contrast(image)

    raw_regions = _p2t.recognize_text_formula(image, return_text=False, resized_shape=768)

    regions = []
    scores = []
    for r in raw_regions:
        score = r.get("score")
        if score is not None:
            scores.append(score)
        position = r.get("position")
        bbox = position.tolist() if hasattr(position, "tolist") else position
        regions.append(
            {
                "latex": r.get("text", ""),
                "type": r.get("type", "unknown"),
                "bbox": bbox,
                "confidence": score,
            }
        )

    confidence_mean = sum(scores) / len(scores) if scores else None
    return {"regions": regions, "confidence_mean": confidence_mean}
