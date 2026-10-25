"""
Which OCR engine's page result to keep.

Kept in its own small module, with no model or image imports, so the rule can
be unit tested without loading anything and so offline evaluation scripts can
call the exact function production uses. A trained selector would replace
pick_page_result here, behind a flag, with this rule as the fallback.
"""


def pick_page_result(primary: dict, fallback: dict) -> dict:
    """
    Return whichever page result has the higher `confidence_mean`.

    `primary` is Pix2Text's result and `fallback` is PaddleOCR's. The fallback
    has to be STRICTLY higher to win, so a tie keeps the primary. A result
    with no confidence (nothing detected) loses to one that has a confidence,
    and if neither has one the primary is returned.

    Returns one of the two dicts it was given, not a copy.
    """
    fallback_confidence = fallback["confidence_mean"]
    if fallback_confidence is None:
        return primary
    primary_confidence = primary["confidence_mean"]
    if primary_confidence is None or fallback_confidence > primary_confidence:
        return fallback
    return primary
