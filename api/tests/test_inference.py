"""
Unit test for inference.recognize_page -- no real model, no GPU, no 30s
load time. This is the payoff of keeping inference.py's model access behind
a module-level `_p2t` variable: we can swap in a fake and test the
aggregation logic (confidence averaging, region shape) in isolation.

This fakes recognize_page's ACTUAL, verified dependency:
`Pix2Text.recognize_text_formula(return_text=False)`, which returns a list
of dicts with keys type/text/score/position/line_number (position is a
numpy array with .tolist()). Verified by testing against a real photo after
the original full-page-layout approach (Pix2Text.__call__ / recognize_page's
first version) turned out to misclassify real images as "figure" and return
nothing -- see inference.py's docstring for the full story.

Run from api/: pytest
"""

from PIL import Image

import inference


class FakePosition:
    """Stands in for Pix2Text's position value: a numpy array with .tolist()."""

    def __init__(self, box):
        self._box = box

    def tolist(self):
        return self._box


class FakeP2T:
    """Stands in for the loaded Pix2Text instance. Records the last image it
    was handed so tests can check whether preprocessing actually ran before
    the image got here."""

    def __init__(self, regions):
        self._regions = regions
        self.received_image = None

    def recognize_text_formula(self, image, return_text=False, resized_shape=768):
        self.received_image = image
        return self._regions


def test_recognize_page_aggregates_confidence(monkeypatch):
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": r"x^2", "type": "isolated", "score": 0.92, "position": FakePosition([[0, 0], [10, 0], [10, 10], [0, 10]])},
                {"text": r"+y", "type": "isolated", "score": 0.40, "position": FakePosition([[20, 0], [30, 0], [30, 10], [20, 10]])},
            ]
        ),
    )

    result = inference.recognize_page(image=None)  # FakeP2T ignores `image`

    assert result["confidence_mean"] == (0.92 + 0.40) / 2
    assert len(result["regions"]) == 2
    assert result["regions"][0]["latex"] == r"x^2"
    assert result["regions"][1]["confidence"] == 0.40
    assert result["regions"][0]["bbox"] == [[0, 0], [10, 0], [10, 10], [0, 10]]


def test_recognize_page_handles_no_regions(monkeypatch):
    # A blank image can legitimately produce zero detected regions.
    monkeypatch.setattr(inference, "_p2t", FakeP2T([]))

    result = inference.recognize_page(image=None)

    # No regions -> mean is None, not a crash or a fake 0.0 that would
    # silently look like "very low confidence" (NFR-012: never fabricate a
    # confident-looking answer when we don't actually have one).
    assert result["regions"] == []
    assert result["confidence_mean"] is None


def _flat_gray_image(value: int) -> Image.Image:
    """A 20x20 image where every pixel is the same gray value -- a stand-in
    for a faint, washed-out scan (narrow histogram, nothing near black or
    white)."""
    return Image.new("L", (20, 20), color=value).convert("RGB")


def test_enhance_contrast_widens_a_narrow_histogram():
    # Pixels all sit in a narrow band (100-150), like a faint pencil scan --
    # autocontrast should stretch that band so it spans much closer to the
    # full 0-255 range.
    narrow = Image.new("L", (10, 10))
    narrow.putdata([100] * 50 + [150] * 50)
    narrow = narrow.convert("RGB")

    widened = inference.enhance_contrast(narrow)

    before_min, before_max = narrow.convert("L").getextrema()
    after_min, after_max = widened.convert("L").getextrema()
    assert after_max - after_min > before_max - before_min


def test_recognize_page_applies_contrast_only_when_requested(monkeypatch):
    fake = FakeP2T([])
    monkeypatch.setattr(inference, "_p2t", fake)
    narrow = Image.new("L", (10, 10))
    narrow.putdata([100] * 50 + [150] * 50)
    narrow = narrow.convert("RGB")

    inference.recognize_page(narrow, apply_contrast=False)
    unchanged_min, unchanged_max = fake.received_image.convert("L").getextrema()
    assert (unchanged_min, unchanged_max) == (100, 150)

    inference.recognize_page(narrow, apply_contrast=True)
    enhanced_min, enhanced_max = fake.received_image.convert("L").getextrema()
    assert enhanced_max - enhanced_min > unchanged_max - unchanged_min
