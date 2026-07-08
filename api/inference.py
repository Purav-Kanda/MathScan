"""
Thin wrapper around Pix2Text. Kept separate from main.py/routers so the
recognition logic can be unit-tested without booting FastAPI or loading the
real (slow) model -- see tests/test_inference.py, which fakes this module's
`_p2t` global. This split is what makes the >=70% backend coverage target
(NFR-050) actually achievable instead of aspirational.
"""

from typing import Optional

from PIL import Image
from pix2text import Pix2Text

_p2t: Optional[Pix2Text] = None


def load_model() -> None:
    global _p2t
    if _p2t is None:
        _p2t = Pix2Text.from_config()


def is_loaded() -> bool:
    return _p2t is not None


def recognize_page(image: Image.Image) -> dict:
    """
    Full-page recognition: Pix2Text's page-level call does layout detection
    (find math/text regions) *and* OCR in one pass -- this is what SDD 4.2
    means by "Pix2Text's bundled detector." One call gives us both the
    per-region bounding boxes (FR-015) and the LaTeX for each.

    Returns:
        {
          "regions": [{"latex": str, "type": str, "bbox": [[x,y],...], "confidence": float|None}, ...],
          "confidence_mean": float|None
        }

    Honest caveat (worth knowing, not hiding): the SRS (FR-013) asks for
    per-token confidence. Pix2Text's public API only exposes a per-region
    `score`, not per-token log-probs -- true per-token would require calling
    the underlying TrOCR decoder directly, which is out of scope for v1.
    Per-region confidence is what FR-022's "highlight low-confidence tokens"
    actually highlights in v1: whole regions, not individual characters.
    """
    if _p2t is None:
        raise RuntimeError("Model not loaded")

    raw_regions = _p2t(image, resized_shape=768)

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
