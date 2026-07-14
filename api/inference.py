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


# WHY a lazy-loaded global, same pattern as _p2t, but NOT loaded in
# main.py's lifespan alongside Pix2Text: real testing (see
# api/eval/claude_vs_pix2text.py) found PaddleOCR meaningfully outperforms
# Pix2Text specifically on dense handwritten prose (85% confidence and
# genuinely readable output, vs Pix2Text's 6-25% and Chinese-character
# hallucinations on the same page) -- but most pages are fine with Pix2Text
# alone (real math notation, not prose). Loading PaddleOCR eagerly at
# startup would add its own real weight-download/load time to EVERY cold
# start, even for the majority of requests that never need it. Loading it
# lazily, only the first time a page's confidence is actually low enough to
# need it, means that cost is paid rarely instead of on every boot.
_paddle_reader = None


def _get_paddle_reader():
    global _paddle_reader
    if _paddle_reader is None:
        from paddleocr import PaddleOCR

        _paddle_reader = PaddleOCR(use_textline_orientation=True, lang="en")
    return _paddle_reader


def _recognize_page_paddleocr(image: Image.Image) -> dict:
    """
    Same return shape as recognize_page() below, but via PaddleOCR instead
    of Pix2Text -- see recognize_page()'s `try_fallback` parameter for when
    this actually gets called.

    WHY every region here is type="text", never "isolated"/"embedding" (the
    math-mode types Pix2Text can produce): PaddleOCR is a general OCR model,
    not a LaTeX-aware one -- it returns plain recognized text like "1+x^2",
    not real LaTeX source. Labeling it "text" means the frontend's export
    logic (UploadFlow.tsx's formatRegionForExport) escapes it as plain
    prose instead of wrapping it in math delimiters, which is the honest
    choice: this transcription is far more readable than Pix2Text's
    hallucinated failure case, but it won't be properly typeset math in the
    exported PDF the way a real Pix2Text region is. A real, known
    limitation of this fallback, not a bug.
    """
    import numpy as np

    reader = _get_paddle_reader()
    image_bgr = np.array(image)[:, :, ::-1]
    result = reader.predict(image_bgr)

    if not result:
        return {"regions": [], "confidence_mean": None}
    page = result[0]
    texts = page.get("rec_texts") or []
    scores = page.get("rec_scores") or []
    if not texts:
        return {"regions": [], "confidence_mean": None}

    regions = [
        {"latex": _fix_typos(_fix_missing_spaces(text)), "type": "text", "bbox": None, "confidence": score}
        for text, score in zip(texts, scores)
    ]
    confidence_mean = sum(scores) / len(scores) if scores else None
    return {"regions": regions, "confidence_mean": confidence_mean}


# WHY 20 characters as the per-chunk "suspiciously long" threshold: real
# single English words essentially never reach 20 characters (even
# "characteristics" is 15) -- 20 is comfortably above that, so this should
# only fire on genuinely merged multi-word runs, not normal long words. A
# real merged chunk from this fallback ("mantofgchonindvidvais") was 21
# characters. This is a reasonable starting point, not something tuned
# against a large sample yet -- worth revisiting if shorter merged runs
# (10-15 chars) turn out to be common in more real testing.
_MIN_LENGTH_FOR_SPACE_FIX = 20


def _fix_missing_spaces(text: str) -> str:
    """
    WHY this exists at all: real testing found PaddleOCR's text-line
    detector sometimes treats a whole line of cursive handwriting as one
    "word" region, and the recognizer doesn't reliably predict space
    characters within it -- a genuinely common OCR failure mode on
    handwriting specifically (word gaps are visually subtler in cursive
    script than in printed text). "amt of good which an individual is
    willing and able to buy" came back as one unbroken run of characters
    with zero spaces on a real page (see chat: 8 Sept econ notes).

    WHY wordninja specifically, not a from-scratch algorithm: this is
    exactly the same "split concatenated words back apart" problem as
    segmenting a hashtag or URL slug into real words -- wordninja solves it
    with word-frequency statistics (finding the split points that produce
    the most probable sequence of real English words), a well-established
    technique for this exact class of problem, and it's a small, pure-
    Python, no-network-at-runtime dependency.

    WHY this checks each SPACE-SEPARATED CHUNK individually, not "does the
    whole region contain a space anywhere": a real bug here at first --
    checking the whole string for any space at all meant a region like
    "mantofgchonindvidvais illigadale tobingaee" (three chunks, but the
    first one alone is 21 merged characters) was skipped entirely, because
    it technically contains spaces elsewhere. PaddleOCR's failure isn't
    "never puts spaces anywhere," it's "sometimes merges a run of several
    words into one over-long chunk while getting the rest of the line
    right" -- so the fix has to look at each chunk on its own merits, not
    the region as a whole.

    WHY only run this on long chunks, not every one: wordninja has no way
    to know a chunk was ALREADY one correct word -- running it
    indiscriminately risks incorrectly re-splitting real words or names it
    doesn't recognize. Restricting it to chunks longer than a real single
    English word plausibly gets targets exactly the failure case that's
    actually been observed, without touching text that's already fine.
    """
    import wordninja

    chunks = text.split(" ")
    fixed_chunks = []
    for chunk in chunks:
        if len(chunk) >= _MIN_LENGTH_FOR_SPACE_FIX:
            split_words = wordninja.split(chunk)
            fixed_chunks.append(" ".join(split_words) if split_words else chunk)
        else:
            fixed_chunks.append(chunk)
    return " ".join(fixed_chunks)


# WHY this list, boosted to an artificially high frequency: pyspellchecker's
# default English dictionary ranks corrections by how common a word is in
# GENERAL English -- real testing found that actively hurts classroom-notes
# text specifically. "echure" is genuinely closer (by edit distance) to
# "lecture" than to some alternatives, but pyspellchecker picked "secure"
# anyway, purely because "secure" is a far more common word in general
# English than "lecture" is. Verified directly (not guessed): loading these
# words at a high frequency flipped "echure" -> "lecture" (was "secure"),
# and correctly resolved two other real ties ("spply" -> "supply", not
# "apply"; "demond" -> "demand", not "demon"). This is a starting list
# covering the subjects seen in real testing (calculus, econ, poli-sci
# notes) -- worth expanding as more real failures turn up, not meant to be
# exhaustive.
#
# Honest limit, also verified directly: this does NOT fix every case.
# "choper" still corrects to "chopper," not "chapter" -- "chapter" isn't
# within pyspellchecker's edit-distance-2 candidate set for "choper" at
# all, so no amount of frequency boosting can surface it as an option. That
# specific failure is a recognition-quality problem (too many characters
# wrong), not something a smarter dictionary can paper over.
_DOMAIN_VOCABULARY = [
    "lecture", "chapter", "section", "syllabus", "professor", "homework",
    "assignment", "quiz", "exam", "midterm", "semester", "textbook",
    "supply", "demand", "equilibrium", "market", "elasticity", "economics",
    "hypothesis", "variable", "equation", "theorem", "derivative",
    "integral", "function", "formula", "calculus", "algebra",
]

# WHY a module-level, lazily-built SpellChecker (not one per call): loading
# its dictionary/word-frequency data has real cost -- same "build once,
# reuse" reasoning as _p2t and _paddle_reader above.
_spellchecker = None


def _get_spellchecker():
    global _spellchecker
    if _spellchecker is None:
        from spellchecker import SpellChecker

        _spellchecker = SpellChecker()
        # WHY *500000 specifically: needs to comfortably outrank whatever
        # the highest-frequency general-English competitor is for these
        # words (e.g. "secure" beat "lecture" at pyspellchecker's normal
        # frequency) -- a large, round boost verified directly (above) to
        # actually flip the real cases tested, not a value tuned to the
        # edge of working.
        for word in _DOMAIN_VOCABULARY:
            _spellchecker.word_frequency.load_words([word] * 500000)
    return _spellchecker


def _fix_typos(text: str) -> str:
    """
    WHY this exists: real testing found PaddleOCR getting individual
    characters wrong even when spacing was correct -- "Lecture" -> "echure",
    "Chapter" -> "Choper", "Supply" -> "Spply" (see chat: 8 Sept econ
    notes). This is a DIFFERENT failure mode than _fix_missing_spaces above
    (missing word BOUNDARIES vs wrong CHARACTERS within a word), so it's a
    separate pass, run after that one.

    WHY this is deliberately conservative, not "replace anything the
    dictionary doesn't recognize": pyspellchecker's dictionary is general
    English, not econ/math/whatever-subject-specific vocabulary. A word
    it doesn't recognize might be a real misspelling OR a real technical
    term it just doesn't know -- guessing wrong on the latter would make
    output WORSE, not better (silently swapping a correct-but-unusual word
    for a common wrong one). Kept narrow on purpose:
      - skip anything containing a digit or non-letter character (this
        fallback's whole job is prose; math-looking fragments shouldn't be
        "corrected" as English words at all)
      - skip very short words (1-2 letters) -- too easy to misfire on
        real abbreviations/initials
      - only replace when pyspellchecker both flags the word as unknown
        AND has a confident correction to offer; otherwise leave it as-is
        rather than guess
    """
    import re

    spell = _get_spellchecker()
    fixed_words = []
    for word in text.split(" "):
        # WHY strip leading/trailing punctuation before checking, then
        # reattach it after: "Demand:" would be flagged unknown purely
        # because of the colon, even though "Demand" alone is a real,
        # correctly spelled word. `core` is just the letters; `prefix`/
        # `suffix` are whatever non-letter characters surrounded them.
        match = re.match(r"^([^A-Za-z]*)([A-Za-z]*)([^A-Za-z]*)$", word)
        if not match:
            # Contains letters mixed with digits/symbols mid-word (e.g. a
            # math-looking fragment) -- per the docstring above, this
            # fallback's job is prose, so anything that isn't cleanly
            # "punctuation + letters + punctuation" is left untouched
            # rather than guessed at.
            fixed_words.append(word)
            continue
        prefix, core, suffix = match.groups()

        if len(core) <= 2 or core.lower() not in spell.unknown([core.lower()]):
            fixed_words.append(word)
            continue

        correction = spell.correction(core.lower())
        if not correction or correction == core.lower():
            fixed_words.append(word)
            continue

        # Preserve the original word's capitalization style (spellchecker
        # always returns lowercase) -- "Lecture" should correct to
        # "Lecture", not "lecture".
        if core.isupper():
            correction = correction.upper()
        elif core[0].isupper():
            correction = correction.capitalize()
        fixed_words.append(f"{prefix}{correction}{suffix}")
    return " ".join(fixed_words)


def recognize_page(
    image: Image.Image,
    apply_contrast: bool = False,
    resized_shape: int = 768,
    try_fallback: bool = False,
    fallback_threshold: float = 0.70,
) -> dict:
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

    WHY try_fallback defaults to False, not True: api/tests/test_inference.py
    fakes low-confidence pages (e.g. a 0.66 mean, below fallback_threshold's
    default 0.70) to test the confidence-averaging logic in isolation --
    with try_fallback defaulting on, those tests would also try to import
    and run real PaddleOCR, which isn't installed in the CI environment on
    purpose (it's a heavy, opt-in dependency -- see requirements-fallback.txt).
    routers/ocr.py's real endpoints explicitly pass try_fallback=True; tests
    and any other caller get today's exact Pix2Text-only behavior unless
    they ask for the fallback.
    """
    if _p2t is None:
        raise RuntimeError("Model not loaded")

    if apply_contrast:
        image = enhance_contrast(image)

    # WHY resized_shape is now a parameter, not hardcoded 768: real testing
    # (6 real class-notes PDFs, see api/eval/claude_vs_pix2text.py) found
    # Pix2Text badly hallucinating -- wrong-language characters, decoder
    # repetition loops -- on dense multi-line handwritten prose, not just
    # isolated equations. One free (no API cost) hypothesis worth testing
    # before reaching for a paid fallback: 768px might be discarding real
    # detail on a busy page before OCR ever sees it. Keeping the default at
    # 768 preserves today's exact behavior for the live app; the eval
    # script can override this to test whether a higher value actually
    # helps on the pages that are currently failing.
    raw_regions = _p2t.recognize_text_formula(image, return_text=False, resized_shape=resized_shape)

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
    result = {"regions": regions, "confidence_mean": confidence_mean}

    # WHY compare against fallback_threshold and PICK THE HIGHER of the two
    # confidences, not just "use PaddleOCR whenever confidence is low": on
    # a page that's real math notation Pix2Text mis-scored for some other
    # reason, Pix2Text may still be the better read even below threshold --
    # this keeps the choice evidence-based per page rather than assuming
    # one model always wins once triggered. Real testing (calculus page 3,
    # api/eval/claude_vs_pix2text.py) found Pix2Text at 6-25% confidence
    # producing Chinese-character hallucinations on dense handwritten prose,
    # while PaddleOCR read the same page at 85% confidence with genuinely
    # correct content -- but that comparison, not a blind swap, is what
    # this mirrors.
    needs_fallback = confidence_mean is None or confidence_mean < fallback_threshold
    if try_fallback and needs_fallback:
        fallback_result = _recognize_page_paddleocr(image)
        fallback_confidence = fallback_result["confidence_mean"]
        if fallback_confidence is not None and (
            confidence_mean is None or fallback_confidence > confidence_mean
        ):
            return fallback_result

    return result
