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


def test_fix_missing_spaces_splits_a_real_merged_chunk():
    # WHY this exact string: a real region from the live app's PaddleOCR
    # fallback came back exactly like this on a real photo of handwritten
    # econ notes -- three space-separated chunks, but the FIRST one alone
    # is 21 merged characters ("amt of good which an individual is" with no
    # internal spaces). A first version of this test/fix checked "does the
    # whole string contain a space anywhere" and wrongly skipped this
    # entirely, since it does contain spaces elsewhere -- this uses the
    # actual observed failure, not a synthetic example, specifically to
    # catch that class of bug.
    merged = "mantofgchonindvidvais illigadale tobingaee"
    fixed = inference._fix_missing_spaces(merged)

    # Not asserting an exact split -- wordninja's output depends on its
    # word-frequency model and could shift slightly across versions. What
    # matters is that the long first chunk actually got split, not left as
    # one 21-character run.
    assert fixed != merged
    first_chunk = fixed.split(" ")[0]
    assert len(first_chunk) < len("mantofgchonindvidvais")


def test_fix_missing_spaces_leaves_short_or_already_spaced_text_alone():
    # WHY: wordninja has no way to know a token was already correctly
    # spaced -- running it indiscriminately risks mangling real short words
    # or names it doesn't recognize. Confirms the length/no-space guard
    # actually gates this, not just that wordninja "usually" leaves things
    # alone.
    assert inference._fix_missing_spaces("Demand") == "Demand"
    assert inference._fix_missing_spaces("already has spaces here") == "already has spaces here"


def test_fix_typos_corrects_real_misreads_and_preserves_capitalization():
    # WHY these exact words: real PaddleOCR misreads from the live app on
    # real handwriting (see chat: 8 Sept econ notes). These specific
    # results were verified by actually running pyspellchecker with the
    # domain vocabulary boost, not assumed -- see _DOMAIN_VOCABULARY's
    # docstring-equivalent comment for the "echure" case, where the
    # DEFAULT dictionary picks "secure" and the domain boost is what flips
    # it to the correct "lecture."
    assert inference._fix_typos("echure") == "lecture"
    assert inference._fix_typos("Spply") == "Supply"
    assert inference._fix_typos("demond") == "demand"
    # Capitalization of the ORIGINAL word should be preserved even though
    # pyspellchecker's corrections are always lowercase internally.
    assert inference._fix_typos("olher") == "other"


def test_fix_typos_has_a_real_known_limit_not_fixed_by_domain_boost():
    # WHY this test exists, not just a "does it work" test: "Choper" (meant
    # to be "Chapter") does NOT get fixed by any of this -- "chapter" isn't
    # within pyspellchecker's edit-distance-2 candidate set for "choper" at
    # all, a structural limit no frequency boost can work around. This
    # documents that honestly as an expected result, not a regression, so
    # a future change doesn't "fix" this test by accident while hiding a
    # real limitation.
    assert inference._fix_typos("Choper") != "Chapter"


def test_fix_typos_preserves_punctuation():
    # WHY this matters: a word like "Demand:" would be flagged unknown by
    # pyspellchecker purely because of the trailing colon, even though
    # "Demand" alone is a real, correctly spelled word -- this confirms the
    # colon survives and "Demand" itself isn't incorrectly "corrected."
    assert inference._fix_typos("Demand:") == "Demand:"


def test_fix_typos_leaves_math_looking_fragments_alone():
    # WHY: this fallback's whole job is prose -- a fragment mixing letters
    # and digits/symbols (math notation, not a real word) should never be
    # run through an English dictionary correction at all, per _fix_typos'
    # docstring.
    assert inference._fix_typos("1+x^2") == "1+x^2"
    assert inference._fix_typos("x2") == "x2"


def test_recognize_page_ignores_fallback_by_default(monkeypatch):
    # WHY this test matters: try_fallback defaults to False specifically so
    # tests never touch real PaddleOCR (not installed in CI on purpose --
    # see requirements-fallback.txt). This confirms that default actually
    # holds even when confidence is low enough that a fallback WOULD
    # trigger if it were on -- monkeypatching _recognize_page_paddleocr to
    # raise proves it's never called.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T([{"text": "x", "type": "isolated", "score": 0.10, "position": FakePosition([[0, 0]])}]),
    )

    def _boom(image):
        raise AssertionError("fallback should not run when try_fallback=False")

    monkeypatch.setattr(inference, "_recognize_page_paddleocr", _boom)

    result = inference.recognize_page(image=None)  # try_fallback defaults False
    assert result["confidence_mean"] == 0.10


def test_recognize_page_uses_fallback_when_it_scores_higher(monkeypatch):
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T([{"text": "garbled", "type": "isolated", "score": 0.10, "position": FakePosition([[0, 0]])}]),
    )
    monkeypatch.setattr(
        inference,
        "_recognize_page_paddleocr",
        lambda image: {
            "regions": [{"latex": "much better", "type": "text", "bbox": None, "confidence": 0.85}],
            "confidence_mean": 0.85,
        },
    )

    result = inference.recognize_page(image=None, try_fallback=True)

    assert result["confidence_mean"] == 0.85
    assert result["regions"][0]["latex"] == "much better"


def test_recognize_page_keeps_pix2text_when_fallback_scores_lower(monkeypatch):
    # WHY this matters: the fallback should be evidence-based (pick
    # whichever result actually scores higher), not "always trust
    # PaddleOCR once triggered" -- this covers the case where Pix2Text,
    # even below threshold, still beats what PaddleOCR returns.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T([{"text": "ok-ish", "type": "isolated", "score": 0.50, "position": FakePosition([[0, 0]])}]),
    )
    monkeypatch.setattr(
        inference,
        "_recognize_page_paddleocr",
        lambda image: {
            "regions": [{"latex": "worse", "type": "text", "bbox": None, "confidence": 0.20}],
            "confidence_mean": 0.20,
        },
    )

    result = inference.recognize_page(image=None, try_fallback=True)

    assert result["confidence_mean"] == 0.50
    assert result["regions"][0]["latex"] == "ok-ish"


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
