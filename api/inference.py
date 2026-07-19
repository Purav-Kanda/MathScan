"""
Thin wrapper around Pix2Text. Kept separate from main.py/routers so the
recognition logic can be unit-tested without booting FastAPI or loading the
real (slow) model -- see tests/test_inference.py, which fakes this module's
`_p2t` global. This split is what makes the >=70% backend coverage target
(NFR-050) actually achievable instead of aspirational.
"""

import os
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


def _strip_hallucinated_cjk(text: str) -> str:
    """
    Real testing (eval/accuracy_benchmark.py, calculus_p15.jpg) found
    Pix2Text hallucinating a stray CJK character (a Chinese '二', "two")
    embedded mid-formula in an otherwise-English calculus page -- a known
    Pix2Text failure mode already documented in recognize_page's own
    docstring ("wrong-language characters, decoder repetition loops -- on
    dense multi-line handwritten prose"). Every real image this app expects
    is English-language coursework notes, so a CJK character in the output
    is never a correct read -- it's always this specific decoder failure.

    Consistent with this file's established guard philosophy (reject
    wholesale when something is definitely wrong, don't try to guess a fix):
    these characters are stripped outright rather than sent through any
    "correction" attempt.

    Deliberately narrow -- only the CJK/Hiragana/Katakana/Hangul Unicode
    blocks, not a broad "non-Latin" filter -- so this never touches
    legitimate Greek letters, arrows, or math symbols Pix2Text correctly
    produces elsewhere (rho, theta, ->, etc. are untouched; only scripts
    that have actually been observed as hallucinations are removed).
    """
    import re

    cjk_pattern = re.compile(r"[一-鿿぀-ゟ゠-ヿ가-힯]")
    return cjk_pattern.sub("", text)


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

    Each region's text runs through two post-processing passes in order:
    _fix_missing_spaces (wordninja), then _fix_typos (pyspellchecker with a
    domain-vocabulary boost). A THIRD pass, the Groq LLM correction, no
    longer runs PER REGION here -- as of M6.5 it runs ONCE per page, with
    the full page's combined context, in recognize_page's final step (see
    _correct_full_page). See that function's docstring for why: a
    per-region call has no visibility into the rest of the page, which was
    the root cause of a real observed failure (a word "corrected" to
    something that doesn't fit the actual surrounding sentence).
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
        {
            "latex": _strip_hallucinated_cjk(_fix_typos(_fix_missing_spaces(text))),
            "type": "text",
            "bbox": None,
            "confidence": score,
        }
        for text, score in zip(texts, scores)
    ]
    confidence_mean = sum(scores) / len(scores) if scores else None
    return {"regions": regions, "confidence_mean": confidence_mean}


# WHY Groq specifically, not Claude/OpenAI: this project already concluded
# (see recognize_page's fallback docstring below, and 12_Code_Walkthrough_
# MathScan.md) that a paid, per-page API cost was worth deferring given zero
# revenue and a student budget. Groq's free tier -- no credit card, generous
# daily request limits, an OpenAI-compatible REST API -- removes that
# specific blocker for a TEXT-ONLY correction call: a few hundred tokens of
# already-recognized text, not an image, so it fits comfortably inside a
# free tier that a full image-based OCR replacement never would.
# WHY llama-3.3-70b-versatile, not the smaller/faster llama-3.1-8b-instant
# this project started with: real live testing showed the 8B model actually
# INSERTING invented words into otherwise-correct sentences (see
# _page_correction_changed_too_much's docstring for the exact case) -- a
# capability gap, not a prompt-wording problem, since the prompt already
# explicitly forbade this. 70B is Groq's next tier up, confirmed current on
# Groq's own models list (console.groq.com/docs/models) as of this change,
# still fast (280 tokens/sec) and still cheap enough per page (a page's
# worth of text is a few hundred tokens) to stay well within Groq's free
# tier at this project's usage volume. The safety guards in this file
# (refusal/repetition/hallucination detection) stay in place regardless --
# a better model reduces how often they need to reject something, it
# doesn't replace them.
_GROQ_MODEL = "llama-3.3-70b-versatile"
_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def _call_groq(prompt: str) -> Optional[str]:
    """
    Low-level Groq call shared by _llm_correct_text (prose, PaddleOCR
    output) and _llm_correct_latex (math, Pix2Text output) below -- both
    build their own prompt and call this; this function only owns the
    network call and its failure handling.

    Returns None (never raises) whenever GROQ_API_KEY isn't set, or on ANY
    failure (timeout, rate limit, malformed response, no network) -- every
    caller is expected to fall back to its original, un-corrected input in
    that case. See _llm_correct_text's docstring for the full reasoning:
    nothing about this step is required for the app to work, and CI never
    sets GROQ_API_KEY, so this is a real no-op in every test run.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None

    import httpx

    try:
        response = httpx.post(
            _GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": _GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 512,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        corrected = response.json()["choices"][0]["message"]["content"].strip()
        return corrected or None
    except Exception:
        return None


# WHY a literal sentinel token ("NOCHANGE"), not just an instruction like
# "if unsure, return the text unchanged": a real observed failure showed
# the model doesn't reliably follow that soft instruction -- given a very
# short/incomplete fragment, it responded with conversational meta-text
# ("I'm not sure what the original was, please provide more context...")
# instead of either fixing the text or returning it unchanged. That
# response got treated as "the corrected text" and exported verbatim --
# a chatbot's refusal ending up in a student's LaTeX export. An explicit,
# easy-to-detect sentinel gives the model exactly one unambiguous way to
# say "I can't/won't confidently fix this," which _looks_like_llm_refusal
# below checks for as a hard gate, not a soft hope.
_LLM_NO_CHANGE_TOKEN = "NOCHANGE"

# Second line of defense in case the model ignores the sentinel instruction
# entirely and free-writes a refusal anyway (also a real observed failure,
# not hypothetical) -- these are common phrasings a small instruction-tuned
# model reaches for when it wants to decline/ask for clarification instead
# of just answering. Checked case-insensitively as a substring match.
_LLM_REFUSAL_PHRASES = [
    "i'm not sure", "i am not sure", "please provide", "as an ai",
    "i cannot", "i can't", "i don't have enough", "unable to determine",
    "without more context", "could you provide", "i need more information",
    "i don't know what the original", "not enough information",
]


def _looks_like_llm_refusal(text: str) -> bool:
    lowered = text.lower()
    return text.strip() == _LLM_NO_CHANGE_TOKEN or any(phrase in lowered for phrase in _LLM_REFUSAL_PHRASES)


# WHY this word-level similarity check exists at all -- another REAL
# observed failure, this time on prose, not math: a live test showed the
# actual handwritten line "if we want a SCIENTIFIC answer to political
# questions" come back as "if we want a CONCISE TOXIC answer to political
# questions". That is not a garbled-character OCR misread being fixed --
# "scientific" and "concise toxic" share almost no letters and "toxic"
# isn't even a plausible visual/phonetic misread of anything in the
# original. The model paraphrased instead of correcting: fluent, confident,
# and wrong, which is worse than a visibly garbled word because a student
# skimming their notes would likely never notice. Two more real examples
# from the same test: "your hypothesis" -> "car hypothesis", "not enough"
# -> "not each". This is the prose equivalent of the LaTeX repetition-
# garbage problem, and gets the same treatment: a hard, code-level guard,
# not just a prompt instruction (which the earlier, looser prompt already
# proved isn't reliably followed on its own).
def _correction_changes_too_much(original: str, corrected: str) -> bool:
    """
    Rejects a correction if it doesn't look like a word-for-word OCR-misread
    fix. Two checks:

    1. Word count must match exactly. A genuine OCR-misread correction
       swaps one word for another word -- it doesn't add or remove words.
       The real "scientific" -> "concise toxic" failure turned ONE word
       into TWO, which this catches immediately regardless of content.

    2. For each word that changed, its character-level similarity to the
       original word (via difflib's SequenceMatcher, which is what Python's
       standard library already uses for diffing) must be reasonably high.
       Real, correct OCR fixes verified in this project's own tests
       ("echure"->"lecture", "spply"->"supply", "demond"->"demand") all
       share most of their letters with the original and score well above
       the 0.5 threshold used here. The real bad substitutions ("your"->
       "car", "enough"->"each") share few or no letters in similar
       positions and score well below it. Trailing punctuation is stripped
       before comparing so "guideline." vs "guideline" isn't flagged as a
       changed word.
    """
    import difflib
    import itertools

    orig_words = original.split()
    corr_words = corrected.split()

    if len(orig_words) != len(corr_words):
        return True

    matcher = difflib.SequenceMatcher(a=orig_words, b=corr_words, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        for orig_word, corr_word in itertools.zip_longest(
            orig_words[i1:i2], corr_words[j1:j2], fillvalue=""
        ):
            o = orig_word.strip(".,;:!?()[]").lower()
            c = corr_word.strip(".,;:!?()[]").lower()
            if o == c:
                continue
            similarity = difflib.SequenceMatcher(a=o, b=c).ratio()
            if similarity < 0.5:
                return True
    return False


def _llm_correct_text(text: str) -> str:
    """
    NOTE (M6.5): no longer called automatically per-region inside
    _recognize_page_paddleocr -- see this module's "page-level correction"
    section below (_correct_full_page). Kept as a standalone, still-correct,
    still-tested function (its guards are shared with the page-level path)
    in case single-region correction is ever useful again, e.g. for a
    future feature that corrects just one edited region on demand.

    WHY this exists on top of _fix_typos: pyspellchecker is pure statistics
    (edit distance + word frequency) -- it has no idea what the surrounding
    sentence is ABOUT, which is exactly why symspellpy's context-aware
    correction was tried and rejected earlier in this project (its bigram
    frequency data is general English, not academic vocabulary, so it still
    doesn't favor "lecture" over "secure" in context). A real LLM can
    actually read the sentence and use meaning, not just letter statistics,
    to pick the right word or catch a wrong-but-validly-spelled word a
    dictionary-based checker has no way to flag at all.

    WHY the result is checked against BOTH _looks_like_llm_refusal AND
    _correction_changes_too_much before being used: two separate real live
    tests each found a different failure mode -- a conversational refusal
    getting exported verbatim, and a fluent-but-wrong paraphrase replacing
    real content (see _correction_changes_too_much's docstring for the
    "scientific" -> "concise toxic" case). Both are checked because they're
    independent risks; a change can pass one and still fail the other.
    """
    prompt = (
        "The following text was OCR'd from a photo of handwritten "
        "university course notes (economics, calculus, or political "
        "science). Some individual WORDS may be misread due to OCR/"
        "handwriting recognition errors -- a letter or two swapped, "
        "merged, or dropped within a word.\n\n"
        "Fix ONLY words that are obvious character-level OCR misreads: "
        "the corrected word must share most of its letters with the "
        "original word. Do NOT paraphrase. Do NOT substitute a word for a "
        "different, unrelated word just because it fits the context "
        "better -- only fix it if it looks like a misread of the SAME "
        "word. Do NOT change how many words are in the text, and do NOT "
        "add or remove words.\n\n"
        f"If nothing needs fixing, or you are not confident a word is a "
        f"misread (as opposed to intentional), respond with EXACTLY this "
        f"single word and nothing else: {_LLM_NO_CHANGE_TOKEN}\n\n"
        "Otherwise return ONLY the corrected text, same number of words, "
        "same structure, no explanation.\n\n"
        f"Text: {text}"
    )
    corrected = _call_groq(prompt)
    if not corrected or _looks_like_llm_refusal(corrected) or _correction_changes_too_much(text, corrected):
        return text
    return corrected


# WHY this is a SEPARATE function from _llm_correct_text, not the same one
# reused for math regions too: correcting garbled LaTeX is a fundamentally
# riskier operation than correcting garbled prose. For prose, surrounding
# sentence context makes the right word obvious ("echure" in a sentence
# about "the professor's echure" is clearly "lecture") -- the model is
# disambiguating between real candidate words. For math notation, the model
# has NO image, only already-garbled text/LaTeX -- it cannot verify what the
# handwritten equation actually said, so a wrong "correction" isn't just a
# missed fix, it's confidently-wrong math that reads as more plausible than
# the visibly-garbled original. The prompt below is deliberately
# conservative about this: fix obvious OCR noise (misrecognized single
# symbols, stray spacing inside a command), but leave the mathematical
# structure alone whenever genuinely unsure, rather than reach for its best
# guess the way the prose prompt is allowed to.
_LATEX_CORRECTION_MAX_CONFIDENCE = 0.85


def _looks_like_repetition_garbage(text: str) -> bool:
    """
    Safety net on top of _llm_correct_latex's prompt, added after a REAL
    observed failure (not a hypothetical): given a region with genuinely
    unbalanced braces that the frontend's hasBalancedBraces() check would
    otherwise safely drop with a visible "region omitted" warning, one live
    test showed the model "fixing" the brace balance by padding the
    expression with the same short token sequence repeated many times --
    e.g. `\\vert\\times\\vert\\times\\vert\\times...` -- which is never how
    real handwritten math looks, no matter how messy the original photo
    was. That output is syntactically valid LaTeX, so it slipped past the
    brace-balance safety check and got exported as if it were a real
    recovered equation -- worse than the honest omission it replaced.

    WHY this specific heuristic (a short substring repeated 4+ times in a
    row), not something fancier: cheap, essentially zero false-positive
    risk on real LaTeX (genuine repeated symbols in a real matrix or
    sequence are a handful of repeats at most, never a dozen+ consecutive
    identical short runs), and it's the exact shape of the real failure
    observed. Catching it here means _llm_correct_latex can fall back to
    the original (still-garbled, but at least honestly so) text instead of
    returning something that LOOKS more legitimate than it is.
    """
    import re

    return re.search(r"(.{2,12}?)\1{3,}", text) is not None


def _llm_correct_latex(latex: str) -> str:
    """
    NOTE (M6.5): no longer called automatically per-region inside
    recognize_page -- see this module's "page-level correction" section
    below (_correct_full_page), which runs once per page on ALL regions
    combined instead. _LATEX_CORRECTION_MAX_CONFIDENCE is unused in the
    live code path now for the same reason. Kept as a standalone,
    still-correct, still-tested function (its guards, _looks_like_llm_
    refusal and _looks_like_repetition_garbage, are shared with the
    page-level path) in case single-region correction is useful again
    later.

    The prompt below was tightened after a real observed failure: an
    earlier, looser version let the model "balance" broken brace structure
    by inventing/padding content -- see _looks_like_repetition_garbage's
    docstring for the specific case. This version explicitly forbids that,
    and _looks_like_repetition_garbage is a second line of defense in case
    the model doesn't follow the instruction perfectly.
    """
    prompt = (
        "The following LaTeX was OCR'd from a photo of handwritten math "
        "notation (set theory, calculus, or algebra) and the recognizer "
        "was NOT confident about it. It may contain garbled symbols, "
        "misplaced spacing, or nonsense tokens from a misread.\n\n"
        "Fix ONLY obvious, local OCR noise: a single misrecognized symbol "
        "(e.g. a stray 'n' that should be '\\cap'), stray spacing inside a "
        "command, an obviously wrong individual character.\n\n"
        "Do NOT do any of the following, even if it seems helpful:\n"
        "- Do not add, remove, or rebalance braces/brackets to make the "
        "LaTeX 'look' more valid -- if the brace structure is broken, "
        "leave it broken exactly as given.\n"
        "- Do not invent, pad, or repeat content to fill in a part of the "
        "expression you cannot read.\n"
        "- Do not guess at or rewrite the overall mathematical meaning or "
        "structure if you are not highly confident what it should be.\n\n"
        f"If you are not highly confident about a fix, respond with "
        f"EXACTLY this single word and nothing else: {_LLM_NO_CHANGE_TOKEN}. "
        f"Do not explain, apologize, or ask a question -- just that one "
        f"word. Otherwise return ONLY the corrected LaTeX, no "
        f"explanation.\n\n"
        f"LaTeX: {latex}"
    )
    corrected = _call_groq(prompt)
    if not corrected or _looks_like_llm_refusal(corrected) or _looks_like_repetition_garbage(corrected):
        return latex
    return corrected


# ---------------------------------------------------------------------------
# M6.5: page-level correction (replaces the per-region calls above)
#
# WHY this whole section exists: real live testing of the per-region design
# (_llm_correct_text/_llm_correct_latex called separately for each line or
# math region) surfaced a structural problem, not just a prompt-wording one
# -- a single line has NO visibility into the rest of the page. Given just
# the isolated line "look for more support that is in line with your
# hypothesis" with one word swapped for a garbled OCR read, the model has to
# guess at a plausible-sounding replacement with no way to know what the
# surrounding notes were actually about. Given the WHOLE page -- the same
# notes are clearly about developing and testing a hypothesis -- the correct
# word is obvious from context, the way a human proofreader would use it.
# This section keeps every safety guard already proven necessary
# (_looks_like_llm_refusal, _looks_like_repetition_garbage) and adds one
# more (_page_correction_changed_too_much) sized for whole-page text instead
# of a single line/region.
# ---------------------------------------------------------------------------


def _escape_latex_text(text: str) -> str:
    """
    Python port of web/components/UploadFlow.tsx's escapeLatexText -- kept
    behaviorally identical (same five characters) on purpose. As of M6.5,
    the fully export-ready page string is assembled server-side (see
    _combine_regions_to_page below) instead of leaving text-region escaping
    to the frontend, so this logic now needs to exist on both sides: here
    for what recognize_page returns, and in UploadFlow.tsx for anything
    still using the older per-region path (e.g. loaded from pre-M6.5
    browser history).
    """
    import re

    return re.sub(r"([%&#_$])", r"\\\1", text)


def _has_balanced_braces(latex: str) -> bool:
    """
    Python port of UploadFlow.tsx's hasBalancedBraces -- kept behaviorally
    identical (same simple "is the previous character a backslash" escape
    check) on purpose.

    WHY this now runs server-side, PER ORIGINAL REGION, before regions are
    combined into one page -- not just once on the whole combined page at
    export time (which is still there too, as defense-in-depth): a real
    live test showed one region with a genuinely unrecoverable unbalanced
    brace (Pix2Text hallucinating on messy math) causing the frontend's
    page-level brace check to omit the ENTIRE page's export -- every other
    region on that page, math and prose alike, lost -- not just the one
    broken region. That's a direct, predictable consequence of collapsing
    everything into one block, and it needed a real fix, not just being
    accepted as a tradeoff: checking per-region here, before combining,
    means only the genuinely broken region gets dropped (the exact same
    behavior the app had before M6.5), while every other region on the page
    still makes it into the page and its export.
    """
    depth = 0
    for i, ch in enumerate(latex):
        is_escaped = i > 0 and latex[i - 1] == "\\"
        if ch == "{" and not is_escaped:
            depth += 1
        elif ch == "}" and not is_escaped:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _format_region_for_page(region: dict) -> str:
    """
    Wraps ONE region's latex per its type -- the exact same rule
    UploadFlow.tsx's formatRegionForExport() already applied per-region at
    export time (isolated -> \\[ \\], embedding -> $ $, text -> escaped
    plain prose). Ported here because M6.5 needs the WHOLE page combined
    into one export-ready string BEFORE it ever reaches Groq or the
    frontend, not after.

    Math regions (isolated/embedding) are brace-checked BEFORE being
    wrapped -- see _has_balanced_braces' docstring for why this has to
    happen per-region, not just once on the final combined page.
    """
    latex = region.get("latex", "")
    region_type = region.get("type", "unknown")
    if region_type == "isolated":
        if not _has_balanced_braces(latex):
            return "% [region omitted: unbalanced braces in source]"
        return f"\\[\n{latex}\n\\]"
    if region_type == "embedding":
        if not _has_balanced_braces(latex):
            return "% [region omitted: unbalanced braces in source]"
        return f"${latex}$"
    return _escape_latex_text(latex)


def _combine_regions_to_page(regions: list) -> str:
    """
    Joins every region on a page into one page-level string, in the order
    given (see recognize_page's region-sorting step for how that order is
    determined).

    WHY THE SEPARATOR BETWEEN REGIONS VARIES, not always a blank line: a
    real benchmark run (eval/accuracy_benchmark.py) found this was
    previously joining with "\n\n" (a blank line) between EVERY region,
    unconditionally. Pix2Text frequently detects one sentence as many small
    regions -- one or two words each -- so a real prose page like "Create a
    new international economic order where it helps..." came out as
    "Create\n\nmore equitable\n\ninternational\n\norder\n\n...", one word
    per "paragraph." Edit-distance-based accuracy punishes that heavily
    (dozens of extra characters per line) even when every individual word
    is correct -- confirmed by word_overlap_recall (order/spacing-
    insensitive) scoring far higher than character_accuracy on the exact
    same pages.

    Fix: use each region's `line_number` (Pix2Text's own reading-order
    signal, also used to sort regions -- see recognize_page) to tell real
    line breaks from same-line continuations:
      - same line_number as the previous region -> join with a single
        space (these are side-by-side fragments of one visual line, e.g.
        "Create" then "a new international..." -- concatenating them IS
        the sentence).
      - line_number exactly one more than the previous -> join with a
        single newline (a genuine new line of the page, same paragraph).
      - a bigger jump, or line_number missing (e.g. the PaddleOCR fallback
        path, which doesn't provide it) -> join with a blank line, the
        previous, more conservative behavior -- safe default when there's
        no real signal to do better.
    """
    kept = [r for r in regions if r.get("latex", "").strip()]
    if not kept:
        return ""

    pieces = []
    previous_line_number = None
    for region in kept:
        formatted = _format_region_for_page(region)
        line_number = region.get("line_number")
        if not pieces:
            pieces.append(formatted)
        else:
            if line_number is None or previous_line_number is None:
                separator = "\n\n"
            else:
                gap = line_number - previous_line_number
                if gap == 0:
                    separator = " "
                elif gap == 1:
                    separator = "\n"
                else:
                    separator = "\n\n"
            pieces.append(separator + formatted)
        previous_line_number = line_number
    return "".join(pieces)


def _page_correction_changed_too_much(original: str, corrected: str) -> bool:
    """
    Page-level counterpart to _correction_changes_too_much (the per-line
    guard).

    WHY every 'insert'/'delete' opcode is rejected outright, not just
    checked against an aggregate word-count-drift percentage: an earlier
    version of this function ONLY checked total word-count drift (allowing
    up to 20%) plus per-word similarity on 'replace' opcodes. A real live
    test found the gap this left: the model INSERTED three brand-new words
    ("/", "→", "Chopper") into an otherwise-correct sentence --
    difflib categorizes a pure insertion as an 'insert' opcode, not
    'replace', so the per-word similarity check never even looked at it.
    The 3-word insertion was only ~11% of that page's total word count,
    comfortably under the 20% aggregate threshold -- diluted by the rest of
    the unchanged page, the exact same "diluted across a long page" problem
    already solved for hallucinated word SWAPS, just not yet for word
    ADDITIONS. Verified directly against the real strings before fixing
    (see api/tests/test_inference.py) that this was genuinely an 'insert'
    opcode, not a 'replace'.

    The fix is simple and strict, and matches the prompt's own contract
    (_correct_full_page explicitly tells the model not to add or remove
    words): ANY insert or delete -- any word-count change at all, anywhere
    in the page -- rejects the whole correction. Only 'replace' opcodes
    (an existing word swapped for a different word, count unchanged) are
    still allowed, gated by the same per-word character-similarity check as
    before. This is stricter than the original per-line guard's exact-
    word-count-match rule in spirit but identical in effect: no case where
    the corrected page has a different word count than the original is
    ever accepted, full stop -- there is no longer an aggregate-percentage
    loophole for either insertions or deletions to hide in.
    """
    import difflib
    import itertools

    orig_words = original.split()
    corr_words = corrected.split()

    matcher = difflib.SequenceMatcher(a=orig_words, b=corr_words, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("insert", "delete"):
            return True
        # tag == "replace": same length or not, still a word-for-word swap
        # attempt -- gated by per-word similarity, same as the per-line
        # guard (_correction_changes_too_much).
        for orig_word, corr_word in itertools.zip_longest(
            orig_words[i1:i2], corr_words[j1:j2], fillvalue=""
        ):
            o = orig_word.strip(".,;:!?()[]").lower()
            c = corr_word.strip(".,;:!?()[]").lower()
            if o == c:
                continue
            similarity = difflib.SequenceMatcher(a=o, b=c).ratio()
            if similarity < 0.5:
                return True
    return False


def _correct_full_page(page_text: str) -> str:
    """
    Runs ONCE per page, ALWAYS (not confidence-gated) -- replacing the
    earlier per-region _llm_correct_text/_llm_correct_latex calls. See this
    section's module-level comment above for why: giving Groq the whole
    page's context at once is what actually fixes context-dependent
    misreads, which a single isolated line/region structurally cannot.

    "Always," not gated by confidence, is a deliberate product choice: even
    a page Pix2Text/PaddleOCR are individually confident about can still
    benefit from whole-page context a per-region score can't capture.
    GROQ_API_KEY missing still makes this a complete no-op (via _call_groq),
    so nothing about the app breaks or costs anything if it's never set.

    Applies every safety guard proven necessary by real testing, in order:
    refusal detection, repetition-garbage detection (a padded-garbage math
    sub-expression is still possible inside a page-level response), and the
    page-scoped hallucination-similarity check. Any one of them rejects the
    WHOLE page's correction and falls back to the original combined text --
    consistent with every other guard in this file: reject wholesale rather
    than try to salvage part of a response that failed a safety check.
    """
    if not page_text.strip():
        return page_text

    prompt = (
        "The following is a full page of OCR'd content from a photo of "
        "handwritten university course notes. It mixes plain prose with "
        "LaTeX math wrapped in \\[ ... \\] (display) or $ ... $ (inline) "
        "delimiters. Some individual words or symbols may be misread due "
        "to OCR/handwriting recognition errors.\n\n"
        "Use the context of the WHOLE page to fix obvious misreads -- a "
        "word that doesn't fit the sentence it's in, given what the rest "
        "of the notes are about. Fix ONLY words/symbols that look like "
        "character-level misreads of what was probably actually written. "
        "Do NOT paraphrase. Do NOT substitute a different, unrelated word "
        "just because it reads better -- only fix it if it looks like a "
        "misread of the SAME word. Do NOT add, remove, or rebalance LaTeX "
        "braces/brackets/delimiters. Do NOT invent or pad content you "
        "cannot read. Preserve the exact line structure, paragraph "
        "breaks, and all \\[ \\] / $ $ delimiters exactly as given.\n\n"
        f"If nothing on the page needs fixing, respond with EXACTLY this "
        f"single word and nothing else: {_LLM_NO_CHANGE_TOKEN}\n\n"
        "Otherwise return ONLY the corrected page, no explanation.\n\n"
        f"Page:\n{page_text}"
    )
    corrected = _call_groq(prompt)
    if not corrected:
        return page_text
    if _looks_like_llm_refusal(corrected):
        return page_text
    if _looks_like_repetition_garbage(corrected):
        return page_text
    if _page_correction_changed_too_much(page_text, corrected):
        return page_text
    return corrected


def _collapse_to_page_result(result: dict) -> dict:
    """
    Final step of recognize_page (called just before it returns): replaces
    a page's list of individual regions with ONE combined, page-level
    region, after running the whole page through _correct_full_page.

    WHY one region instead of many: this is the actual product decision
    behind M6.5 -- Groq gets to see (and correct) the whole page as real
    context, and the frontend shows one confidence badge + one editable
    block per page instead of a badge per line. Because the frontend
    already renders `page.result.regions.map(...)`, returning a list with
    exactly one item is enough to get that page-level display with NO
    frontend rendering-loop changes needed -- only the export formatting
    (UploadFlow.tsx's formatRegionForExport, which now needs to recognize
    type "page" and pass it through as-is, since it already contains its
    own delimiters/escaping) and the live preview (LatexPreview.tsx, which
    now needs to render MIXED prose+math content, not just pure math)
    needed real changes.

    WHY skipped entirely when there are no regions: an empty page (nothing
    detected) should still show "No math detected on this page" in the
    frontend, which checks regions.length === 0. Wrapping an empty page
    into a "page"-type region with empty text would silently break that
    message, and there's nothing for Groq to correct on a blank page
    anyway.
    """
    regions = result["regions"]
    if not regions:
        return result

    page_text = _combine_regions_to_page(regions)
    corrected_page_text = _correct_full_page(page_text)

    return {
        "regions": [
            {
                "latex": corrected_page_text,
                "type": "page",
                "bbox": None,
                "confidence": result["confidence_mean"],
            }
        ],
        "confidence_mean": result["confidence_mean"],
    }


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
    for index, r in enumerate(raw_regions):
        score = r.get("score")
        if score is not None:
            scores.append(score)
        position = r.get("position")
        bbox = position.tolist() if hasattr(position, "tolist") else position
        # WHY no per-region _llm_correct_latex call here anymore (M6.5): it
        # ran once per LOW-confidence region in an earlier version of this
        # function. As of M6.5, Groq correction runs ONCE per page instead,
        # on the whole page's combined content -- see _correct_full_page,
        # called from this function's final step, below.
        regions.append(
            {
                "latex": _strip_hallucinated_cjk(r.get("text", "")),
                "type": r.get("type", "unknown"),
                "bbox": bbox,
                "confidence": score,
                # WHY line_number is kept (not part of the documented public
                # result shape above): used to sort regions into real
                # reading order below, AND passed through to
                # _combine_regions_to_page so it can tell same-line
                # continuations from real new lines when choosing how to
                # join regions (space vs newline vs blank line) -- see that
                # function's docstring. Only ever read internally; the
                # single "page"-type region recognize_page ultimately
                # returns is a fresh dict built by _collapse_to_page_result,
                # so this never actually reaches API callers.
                "line_number": r.get("line_number"),
                "_raw_index": index,
            }
        )

    # WHY sort by (line_number, left-x) before combining into page text: a
    # real benchmark run against real handwritten pages found the combined
    # page text badly scrambled -- e.g. a diagram-style poli-sci page with
    # arrows/branches came out with words in an order nothing like the
    # source, and edit-distance-based accuracy punished that severely even
    # when nearly every individual word was correctly read (confirmed via
    # eval/accuracy_benchmark.py's word_overlap_recall metric scoring far
    # higher than character_accuracy on the same pages). The root cause:
    # Pix2Text's recognize_text_formula already computes a `line_number` per
    # region (its own best guess at reading order, grouping regions on the
    # same visual line) but this function was discarding it entirely and
    # just using whatever order the raw list came back in. Sorting by
    # (line_number, then left-x for regions sharing a line) restores real
    # top-to-bottom, left-to-right reading order using information Pix2Text
    # was already computing for free -- no new dependency, no API cost.
    # Regions with no line_number (e.g. the PaddleOCR fallback path, or unit
    # test fakes that don't set it) fall back to their original list
    # position via _raw_index, so behavior is unchanged when line_number
    # isn't available.
    def _region_sort_key(region: dict):
        line_number = region["line_number"]
        region_bbox = region["bbox"]
        left_x = region_bbox[0][0] if region_bbox else 0
        if line_number is None:
            return (0, region["_raw_index"], 0)
        return (1, line_number, left_x)

    regions.sort(key=_region_sort_key)
    for region in regions:
        del region["_raw_index"]

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
            result = fallback_result

    # M6.5: always the final step, regardless of which result (Pix2Text's
    # own, or the PaddleOCR fallback's) won above -- see
    # _collapse_to_page_result's docstring for the full reasoning.
    return _collapse_to_page_result(result)
