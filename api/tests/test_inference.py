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
    # WHY this test now expects ONE combined region, not two: M6.5 collapses
    # every page into a single page-level block (see
    # inference._collapse_to_page_result) so Groq can correct the whole page
    # with real context instead of one isolated region at a time. The mean
    # confidence is still the average of the ORIGINAL per-region scores --
    # only the region list itself is now collapsed to one item.
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
    assert len(result["regions"]) == 1
    assert result["regions"][0]["type"] == "page"
    assert result["regions"][0]["confidence"] == (0.92 + 0.40) / 2
    # Both original "isolated" regions get wrapped in \[ \] (the same rule
    # UploadFlow.tsx's formatRegionForExport used per-region), joined into
    # one page-level string -- GROQ_API_KEY is unset in tests, so
    # _correct_full_page is a true no-op and this combined text passes
    # through unchanged. WHY a SPACE between them (not a blank line): both
    # bounding boxes span y 0-10, i.e. the same visual line -- geometry-
    # based combining (see _assign_line_indices) treats same-line fragments
    # as one line and joins them with a space, ordered left-to-right by x.
    assert result["regions"][0]["latex"] == "\\[\nx^2\n\\] \\[\n+y\n\\]"


def test_recognize_page_orders_regions_top_to_bottom_by_bbox(monkeypatch):
    # WHY this test: a real benchmark run found the combined page text badly
    # scrambled. The fix reconstructs reading order from bounding-box
    # geometry (see _assign_line_indices) -- NOT Pix2Text's line_number
    # field, which that same benchmark proved comes back None on real pages.
    # Feeding regions in shuffled list order, with bboxes stacked vertically
    # (y 0-10, 10-20, 20-30), verifies they come back top-to-bottom.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "third", "type": "text", "score": 0.9, "position": FakePosition([[0, 20], [10, 20], [10, 30], [0, 30]])},
                {"text": "first", "type": "text", "score": 0.9, "position": FakePosition([[0, 0], [10, 0], [10, 10], [0, 10]])},
                {"text": "second", "type": "text", "score": 0.9, "position": FakePosition([[0, 10], [10, 10], [10, 20], [0, 20]])},
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    # Adjacent lines (small vertical gaps) -> single newline between them,
    # not a blank line -- see _combine_regions_to_page's docstring.
    assert result["regions"][0]["latex"] == "first\nsecond\nthird"


def test_recognize_page_orders_same_line_fragments_left_to_right(monkeypatch):
    # Two fragments on the SAME visual line (both bboxes span y 0-10) should
    # be ordered left-to-right by x and joined with a single space -- this
    # is the core of the fragmentation fix (Pix2Text often splits one line
    # into several side-by-side regions; concatenating them IS the line).
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "right", "type": "text", "score": 0.9, "position": FakePosition([[50, 0], [60, 0], [60, 10], [50, 10]])},
                {"text": "left", "type": "text", "score": 0.9, "position": FakePosition([[0, 0], [10, 0], [10, 10], [0, 10]])},
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    assert result["regions"][0]["latex"] == "left right"


def test_recognize_page_inserts_blank_line_on_large_vertical_gap(monkeypatch):
    # A large vertical gap between regions (y 0 then y 60, far more than one
    # line height) means a real blank line / paragraph break in the source,
    # not just the next line down -- that should produce a blank-line
    # separator, not get squashed onto one line.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "top", "type": "text", "score": 0.9, "position": FakePosition([[0, 0], [10, 0], [10, 10], [0, 10]])},
                {"text": "bottom", "type": "text", "score": 0.9, "position": FakePosition([[0, 60], [10, 60], [10, 70], [0, 70]])},
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    assert result["regions"][0]["latex"] == "top\n\nbottom"


def test_recognize_page_reconstructs_fragmented_prose_sentence(monkeypatch):
    # WHY this exact scenario: this is the real bug found via
    # eval/accuracy_benchmark.py -- a real handwritten prose page came back
    # with Pix2Text detecting one continuous sentence as several small
    # regions on the same visual line, and the old code joined ALL regions
    # with a blank line unconditionally -- "Create\n\nmore
    # equitable\n\ninternational\n\norder..." instead of one real sentence.
    # Three same-line fragments (all y 0-10, increasing x) must reassemble
    # into one continuous line.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "Create a new", "type": "text", "score": 0.9, "position": FakePosition([[0, 0], [70, 0], [70, 10], [0, 10]])},
                {"text": "international economic", "type": "text", "score": 0.9, "position": FakePosition([[80, 0], [180, 0], [180, 10], [80, 10]])},
                {"text": "order", "type": "text", "score": 0.9, "position": FakePosition([[200, 0], [240, 0], [240, 10], [200, 10]])},
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    assert result["regions"][0]["latex"] == "Create a new international economic order"


def test_recognize_page_preserves_raw_order_when_no_bbox(monkeypatch):
    # The PaddleOCR fallback path returns regions with bbox=None (no
    # geometry to reconstruct reading order from). Those must keep their
    # original list order and fall back to the conservative blank-line
    # separator -- geometry-based combining has nothing to sort on, so it
    # must not scramble them. Simulated here with position=None, matching
    # what _recognize_page_paddleocr actually produces.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "a", "type": "text", "score": 0.9, "position": None},
                {"text": "b", "type": "text", "score": 0.9, "position": None},
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    assert result["regions"][0]["latex"] == "a\n\nb"


def test_strip_hallucinated_cjk_removes_only_cjk_scripts():
    # WHY this exact case: a real benchmark run against calculus_p15.jpg
    # produced a stray Chinese character '二' ("two") embedded mid-formula
    # in Pix2Text's raw output -- a known decoder failure mode on dense
    # handwritten math (see recognize_page's docstring). This app only ever
    # sees English-language coursework, so any CJK character in the output
    # is always this failure, never a correct read.
    assert "二" not in inference._strip_hallucinated_cjk("ρ1\n二\n2\nC1")
    # Legit Greek letters and math symbols this pipeline already relies on
    # must NOT be touched -- this guard is deliberately narrow to CJK/
    # Hiragana/Katakana/Hangul only.
    legit = "ρ = 1/2 < 1 ∴ diverges ∞ → ± × ÷"
    assert inference._strip_hallucinated_cjk(legit) == legit


def test_recognize_page_strips_hallucinated_cjk_from_regions(monkeypatch):
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {
                    "text": "ρ1\n二\n2",
                    "type": "text",
                    "score": 0.9,
                    "position": FakePosition([[0, 0]]),
                }
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    assert "二" not in result["regions"][0]["latex"]
    assert "ρ" in result["regions"][0]["latex"]


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


def test_fix_typos_corrects_calculus_and_polisci_domain_words():
    # WHY these exact words: the domain vocabulary was expanded after
    # building eval/test_set/ground_truth.json (16 real, hand-transcribed
    # course pages) and mining it for real domain terms -- these plausible
    # OCR-garbled variants were verified directly against pyspellchecker
    # (with vs without the boost) before being added here, same standard as
    # the original econ-notes cases above. Without the boost: "convergs" ->
    # "converge" (wrong tense), "divergs" -> "divers" (wrong word entirely),
    # "monotomic" -> "monatomic" (a chemistry term, wrong domain),
    # "terrosm" -> "terror" (wrong word). The boost fixes all four.
    assert inference._fix_typos("convergs") == "converges"
    assert inference._fix_typos("divergs") == "diverges"
    assert inference._fix_typos("monotomic") == "monotonic"
    assert inference._fix_typos("terrosm") == "terrorism"


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
    # WHY "\[\nok-ish\n\]", not bare "ok-ish": M6.5's page-level collapse
    # wraps "isolated"-type regions in \[ \] (the same rule
    # formatRegionForExport() used per-region) before combining them into
    # the page string -- the content is still Pix2Text's own "ok-ish", just
    # now formatted the way every region is once collapsed to page level.
    assert result["regions"][0]["latex"] == "\\[\nok-ish\n\\]"


def test_recognize_page_tries_fallback_even_when_pix2text_confidence_is_high(monkeypatch):
    # WHY this test exists: the real bug found via api/eval/accuracy_benchmark.py
    # -- politics_p15.jpg scored Pix2Text confidence at ~85% while it was
    # actually hallucinating garbage (stray CJK characters, jumbled letter
    # fragments). The OLD behavior gated the fallback behind a confidence
    # threshold, so PaddleOCR never even got a chance to run on a page that
    # badly needed it. This confirms the fix: PaddleOCR now runs whenever
    # try_fallback=True, REGARDLESS of how confident Pix2Text claims to be,
    # and the higher-scoring real result wins.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [{"text": "garbage", "type": "isolated", "score": 0.85, "position": FakePosition([[0, 0]])}]
        ),
    )
    monkeypatch.setattr(
        inference,
        "_recognize_page_paddleocr",
        lambda image: {
            "regions": [{"latex": "actually correct", "type": "text", "bbox": None, "confidence": 0.90}],
            "confidence_mean": 0.90,
        },
    )

    result = inference.recognize_page(image=None, try_fallback=True)

    assert result["confidence_mean"] == 0.90
    assert result["regions"][0]["latex"] == "actually correct"


def test_recognize_page_keeps_high_confidence_pix2text_over_worse_fallback(monkeypatch):
    # WHY: running PaddleOCR unconditionally doesn't mean blindly preferring
    # it -- if Pix2Text is both confident AND actually better, its result
    # should still win, same evidence-based comparison as before.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [{"text": "genuinely good", "type": "isolated", "score": 0.90, "position": FakePosition([[0, 0]])}]
        ),
    )
    monkeypatch.setattr(
        inference,
        "_recognize_page_paddleocr",
        lambda image: {
            "regions": [{"latex": "worse read", "type": "text", "bbox": None, "confidence": 0.60}],
            "confidence_mean": 0.60,
        },
    )

    result = inference.recognize_page(image=None, try_fallback=True)

    assert result["confidence_mean"] == 0.90
    assert result["regions"][0]["latex"] == "\\[\ngenuinely good\n\\]"


def test_llm_correct_text_noop_without_api_key(monkeypatch):
    # WHY this matters: CI never sets GROQ_API_KEY, and neither does a local
    # dev checkout by default -- this confirms the function degrades to a
    # true no-op (no network call attempted at all) rather than crashing
    # when the key is absent, exactly like try_fallback's off-by-default
    # design above.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert inference._llm_correct_text("some ocr text") == "some ocr text"


def test_llm_correct_text_uses_response_when_key_present(monkeypatch):
    # WHY "some ocr txet" -> "some ocr text", not an unrelated example: this
    # has to be a realistic word-for-word OCR fix (same word count, close
    # per-word similarity) or _correction_changes_too_much's guard -- added
    # after the real "scientific"->"concise toxic" hallucination -- will
    # correctly reject it, same as it would in production. An example that
    # changes word count or swaps in an unrelated word isn't a valid test
    # of "the response gets used," it's actually testing the rejection
    # path instead.
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "some ocr text"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    assert inference._llm_correct_text("some ocr txet") == "some ocr text"


def test_llm_correct_text_falls_back_on_any_error(monkeypatch):
    # WHY this matters: a rate limit, timeout, or malformed response from
    # Groq's free tier should never break a user's conversion -- this
    # confirms _llm_correct_text swallows the error and returns the
    # original (pre-LLM) text instead of raising.
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    def fake_post(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.post", fake_post)

    assert inference._llm_correct_text("original text") == "original text"


def test_llm_correct_latex_noop_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert inference._llm_correct_latex(r"x^2 + y") == r"x^2 + y"


def test_llm_correct_latex_uses_response_when_key_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": r"x^2 + y^2"}}]}

    monkeypatch.setattr("httpx.post", lambda *a, **kw: FakeResponse())

    assert inference._llm_correct_latex(r"x^2 t y2") == r"x^2 + y^2"


def test_llm_correct_latex_falls_back_on_any_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    def fake_post(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.post", fake_post)

    assert inference._llm_correct_latex(r"garbled \frac{1}{") == r"garbled \frac{1}{"


def test_correction_changes_too_much_catches_the_real_observed_failure():
    # WHY these exact strings: the real handwritten line and the real
    # (wrong) Groq response from a live test -- "scientific" replaced with
    # the unrelated two-word phrase "concise toxic". See
    # _correction_changes_too_much's docstring for the full story.
    original = "if we want a scientific answer to political questions"
    hallucinated = "if we want a concise toxic answer to political questions"
    assert inference._correction_changes_too_much(original, hallucinated) is True


def test_correction_changes_too_much_catches_unrelated_word_swaps():
    # Two more real observed failures from the same live test: both keep
    # the word count the same, so only the per-word similarity check (not
    # the word-count check) catches these.
    assert inference._correction_changes_too_much(
        "that is in line with your hypothesis", "that is in line with car hypothesis"
    ) is True
    assert inference._correction_changes_too_much(
        "variables not enough", "variables not each"
    ) is True


def test_correction_changes_too_much_allows_real_ocr_misread_fixes():
    # WHY: these are the same real, verified-correct fixes from
    # test_fix_typos_corrects_real_misreads_and_preserves_capitalization --
    # confirms the guard doesn't reject the exact kind of fix it's meant to
    # allow through.
    assert inference._correction_changes_too_much("echure", "lecture") is False
    assert inference._correction_changes_too_much("Spply", "Supply") is False
    assert inference._correction_changes_too_much("demond", "demand") is False
    assert inference._correction_changes_too_much(
        "the echure was long", "the lecture was long"
    ) is False


def test_correction_changes_too_much_ignores_trailing_punctuation():
    assert inference._correction_changes_too_much("guideline", "guideline.") is False


def test_llm_correct_text_rejects_hallucinated_paraphrase_and_keeps_original(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {"message": {"content": "if we want a concise toxic answer to political questions"}}
                ]
            }

    monkeypatch.setattr("httpx.post", lambda *a, **kw: FakeResponse())

    original = "if we want a scientific answer to political questions"
    assert inference._llm_correct_text(original) == original


def test_looks_like_llm_refusal_catches_the_sentinel_token():
    assert inference._looks_like_llm_refusal("NOCHANGE") is True
    assert inference._looks_like_llm_refusal("  NOCHANGE  ") is True


def test_looks_like_llm_refusal_catches_the_real_observed_failure():
    # WHY this exact string: the real response Groq gave on a live test,
    # for a short/incomplete OCR fragment it couldn't confidently fix --
    # instead of following the "return unchanged" instruction, it wrote
    # conversational meta-text that then got exported verbatim as if it
    # were real corrected content. See _LLM_NO_CHANGE_TOKEN's comment.
    real_refusal = (
        "I'm not sure what the original LaTeX was, as the input is "
        "incomplete. Please provide the full LaTeX code for me to "
        "attempt to correct."
    )
    assert inference._looks_like_llm_refusal(real_refusal) is True


def test_looks_like_llm_refusal_leaves_real_content_alone():
    assert inference._looks_like_llm_refusal(r"x^2 + y^2 = z^2") is False
    assert inference._looks_like_llm_refusal("Lecture 2: Basics of Supply and Demand.") is False


def test_llm_correct_text_rejects_refusal_and_keeps_original(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {"message": {"content": "I'm not sure what this says, please provide more context."}}
                ]
            }

    monkeypatch.setattr("httpx.post", lambda *a, **kw: FakeResponse())

    original = "garbled fragment"
    assert inference._llm_correct_text(original) == original


def test_llm_correct_latex_rejects_refusal_and_keeps_original(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "NOCHANGE"}}]}

    monkeypatch.setattr("httpx.post", lambda *a, **kw: FakeResponse())

    original = r"\U"
    assert inference._llm_correct_latex(original) == original


def test_looks_like_repetition_garbage_catches_the_real_observed_failure():
    # WHY this exact string: the real garbage Groq produced on a live test,
    # padding a broken-brace region instead of leaving it alone -- see
    # _looks_like_repetition_garbage's docstring and 12_Code_Walkthrough_
    # MathScan.md's M6.5 section.
    real_garbage = r"\times\vert\times\vert\times\vert\times\vert\times\vert\times\vert"
    assert inference._looks_like_repetition_garbage(real_garbage) is True


def test_looks_like_repetition_garbage_leaves_real_latex_alone():
    # Real math legitimately repeats short symbols sometimes (e.g. a
    # sequence x, y, z or a run of 1s in a matrix) -- but only a handful of
    # times, never a dozen+ identical consecutive short runs. This confirms
    # the heuristic doesn't misfire on plausible real LaTeX.
    assert inference._looks_like_repetition_garbage(r"x^2 + y^2 = z^2") is False
    assert inference._looks_like_repetition_garbage(r"A \cap B \cup C") is False


def test_llm_correct_latex_rejects_repetition_garbage_and_keeps_original(monkeypatch):
    # WHY this matters: even if Groq ignores the tightened prompt's
    # instructions, this confirms _llm_correct_latex's own safety net
    # catches it and falls back to the original text rather than exporting
    # the padded garbage.
    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {"message": {"content": r"\vert\times\vert\times\vert\times\vert\times"}}
                ]
            }

    monkeypatch.setattr("httpx.post", lambda *a, **kw: FakeResponse())

    original = r"\frac{(A-B)}{"  # a genuinely broken, unbalanced region
    assert inference._llm_correct_latex(original) == original


def test_has_balanced_braces_matches_real_cases():
    assert inference._has_balanced_braces(r"\frac{1}{2}") is True
    assert inference._has_balanced_braces(r"\frac{(A-B)}{") is False  # unclosed
    assert inference._has_balanced_braces(r"} extra close") is False
    assert inference._has_balanced_braces(r"literal \{ brace") is True  # escaped, doesn't count


def test_format_region_for_page_omits_only_the_broken_region():
    # WHY this test matters: a real live test showed ONE region with an
    # unbalanced brace causing the frontend to omit the ENTIRE page's
    # export, not just that one region -- a direct consequence of
    # collapsing everything into one block. This confirms the fix: brace-
    # checking happens PER REGION here, before combining, so only the
    # genuinely broken region gets swapped for a comment.
    good_region = {"latex": r"x^2 + y^2", "type": "isolated"}
    broken_region = {"latex": r"\frac{(A-B)}{", "type": "isolated"}

    assert inference._format_region_for_page(good_region) == "\\[\nx^2 + y^2\n\\]"
    assert (
        inference._format_region_for_page(broken_region)
        == "% [region omitted: unbalanced braces in source]"
    )


def test_combine_regions_to_page_keeps_good_regions_when_one_is_broken():
    regions = [
        {"latex": "confident", "type": "text"},
        {"latex": r"\frac{(A-B)}{", "type": "isolated"},  # unbalanced
        {"latex": r"x^2", "type": "isolated"},
    ]
    combined = inference._combine_regions_to_page(regions)

    assert "confident" in combined
    assert "\\[\nx^2\n\\]" in combined
    assert "% [region omitted: unbalanced braces in source]" in combined
    # The broken region's raw (unbalanced) text must never appear un-omitted.
    assert r"\frac{(A-B)}{" not in combined.replace(
        "% [region omitted: unbalanced braces in source]", ""
    )


def test_page_correction_now_allows_the_historical_chopper_insertion_case():
    # WHY this is a DELIBERATE policy reversal, not a regression: this exact
    # case (Groq inserting "/", "→", "Chopper" into an otherwise-correct
    # sentence) used to be the one thing this guard existed to reject. Per
    # an explicit later product decision, prioritizing actually-fixed pages
    # over zero-insertion-risk means this now needs to be ALLOWED, since
    # rejecting it means the whole page's correction gets thrown out for
    # one small, plausible-looking addition. The character-similarity ratio
    # for this case is ~0.96 -- nowhere near "unrelated content."
    page_original = (
        "Sept lecture 2 : Basics of Supply and Demand . 2.1 to 2.4 2.1 : "
        "Supply and Demand man to for hon in d vi diva is illegal "
        "tobingaee holding constant other factors ."
    )
    page_hallucinated = page_original.replace("lecture 2", "lecture / → Chopper 2")
    assert inference._page_correction_changed_too_much(page_original, page_hallucinated) is False


def test_page_correction_changed_too_much_allows_word_for_word_swaps():
    page_original = "Sept lecture 2 : Basics of Supply and Demund ."
    page_fixed = "Sept lecture 2 : Basics of Supply and Demand ."
    assert inference._page_correction_changed_too_much(page_original, page_fixed) is False


def test_page_correction_changed_too_much_allows_the_lecture_chopper_fix():
    # WHY this matters: raw OCR text hallucinating "lecture chopper 2" for
    # handwritten "Chapter 2" needs Groq to both swap a word AND drop a
    # stray one -- exactly the kind of multi-word fix the old word-count-
    # exact-match rule couldn't distinguish from a bad hallucination.
    page_original = "Sept lecture chopper 2 : Basics of Supply and Demand ."
    page_fixed = "Sept Chapter 2 : Basics of Supply and Demand ."
    assert inference._page_correction_changed_too_much(page_original, page_fixed) is False


def test_page_correction_changed_too_much_allows_a_grammar_fixing_insertion():
    # WHY: the explicit product decision was "I don't mind Groq adding
    # words to make the grammar make sense" -- this is that exact case.
    page_original = "The cat sat mat"
    page_fixed = "The cat sat on the mat"
    assert inference._page_correction_changed_too_much(page_original, page_fixed) is False


def test_page_correction_changed_too_much_still_rejects_a_wholesale_unrelated_rewrite():
    # WHY a floor still has to exist even under the looser policy: if Groq
    # effectively ignores the page and returns unrelated text (a
    # similarity ratio of ~0.29 here), that's not "fixing OCR errors" by
    # any reasonable definition -- it's the model failing to engage with
    # the actual content at all, which every layer of correction in this
    # file (refusal detection, repetition detection, this) exists to catch.
    page_original = "Sept lecture 2 : Basics of Supply and Demand . 2.1 to 2.4"
    page_hallucinated = "The quick brown fox jumps over the lazy dog repeatedly in the forest today"
    assert inference._page_correction_changed_too_much(page_original, page_hallucinated) is True


def test_page_correction_rejects_a_single_line_replaced_with_unrelated_content():
    # WHY this exact case: a real live export replaced a genuinely garbled
    # but real definition line ("Demand: amt of good which an individual
    # is willing and able to buy during a given time period") with a
    # completely different, fluent-sounding but WRONG phrase ("man to for
    # hon in d vi diva is illegitamate tobingaee"). The whole-page-ratio-
    # only version of this guard (a prior revision) missed this: one bad
    # line out of a long page barely moves the page-wide similarity ratio.
    # Confirms the per-chunk rewrite catches it -- this specific replace
    # opcode is 8+ words with low chunk-level similarity, well past the
    # 5-word freely-allowed size.
    page_original = (
        "8 Sept lecture / -> Chapter 2 : Basics of Supply and Demand . "
        "2.1 to 2.4 2.1 : Supply and Demand Demand : amt of good which "
        "an individual is willing and able to buy during a given time "
        "period , holding constant other factors ."
    )
    page_hallucinated = (
        "1 Sept lecture / -> Chapter 2 : Basics of Supply and Demand . "
        "2.1 to 2.4 2.1 : Supply and Demand man to for hon in d vi diva "
        "is illegitamate tobingaee holding constant other factors ."
    )
    assert inference._page_correction_changed_too_much(page_original, page_hallucinated) is True


def test_recognize_page_always_collapses_to_one_page_level_region(monkeypatch):
    # WHY this test replaces the old per-region gating test (M6.5): Groq
    # correction no longer runs per-region at all -- it runs ONCE per page,
    # ALWAYS, regardless of any individual region's confidence. This
    # confirms recognize_page calls the page-level collapse/correction path
    # (via _correct_full_page) even when every region is individually
    # confident, and that it runs on the COMBINED page text, not per-region.
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "confident", "type": "isolated", "score": 0.95, "position": FakePosition([[0, 0]])},
                {"text": "also confident", "type": "isolated", "score": 0.99, "position": FakePosition([[0, 0]])},
            ]
        ),
    )
    calls = []

    def fake_correct_full_page(page_text):
        calls.append(page_text)
        return "CORRECTED PAGE"

    monkeypatch.setattr(inference, "_correct_full_page", fake_correct_full_page)

    result = inference.recognize_page(image=None)

    # Called once, with BOTH regions already combined into one string --
    # not called per-region, and not skipped just because confidence is high.
    # (Both fakes share bbox [[0,0]], so geometry-based combining treats them
    # as one visual line and joins with a space -- see _assign_line_indices.)
    assert calls == ["\\[\nconfident\n\\] \\[\nalso confident\n\\]"]
    assert len(result["regions"]) == 1
    assert result["regions"][0]["type"] == "page"
    assert result["regions"][0]["latex"] == "CORRECTED PAGE"


def test_recognize_page_reports_low_confidence_breakdown(monkeypatch):
    # WHY this test: collapsing to one page-level region means the single
    # `confidence` field is an AVERAGE -- a page that's mostly clean with
    # one bad region can still average out to a reassuring-looking score.
    # region_count/low_confidence_count let the frontend show the honest
    # breakdown ("2 of 3 sections below 70%") instead of just the average.
    # Threshold matches the app's existing 0.70 default (the frontend's
    # ConfidenceBadge cutoff).
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "a", "type": "text", "score": 0.95, "position": FakePosition([[0, 0]])},
                {"text": "b", "type": "text", "score": 0.40, "position": FakePosition([[0, 0]])},
                {"text": "c", "type": "text", "score": 0.20, "position": FakePosition([[0, 0]])},
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    assert result["regions"][0]["region_count"] == 3
    assert result["regions"][0]["low_confidence_count"] == 2


def test_recognize_page_low_confidence_count_is_zero_when_all_confident(monkeypatch):
    monkeypatch.setattr(
        inference,
        "_p2t",
        FakeP2T(
            [
                {"text": "a", "type": "text", "score": 0.95, "position": FakePosition([[0, 0]])},
                {"text": "b", "type": "text", "score": 0.99, "position": FakePosition([[0, 0]])},
            ]
        ),
    )

    result = inference.recognize_page(image=None)

    assert result["regions"][0]["region_count"] == 2
    assert result["regions"][0]["low_confidence_count"] == 0


def test_recognize_page_skips_page_collapse_when_there_are_no_regions(monkeypatch):
    # WHY: an empty page (nothing detected) should keep regions == [] so the
    # frontend's "No math detected on this page" message still shows --
    # wrapping an empty page into a "page"-type region would silently break
    # that, and there's nothing for Groq to correct on a blank page anyway.
    monkeypatch.setattr(inference, "_p2t", FakeP2T([]))

    result = inference.recognize_page(image=None)

    assert result["regions"] == []


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
