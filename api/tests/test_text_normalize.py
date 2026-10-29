"""
Unit tests for text_normalize.canonical_text -- the shared comparison form for
OCR output and hand-typed labels. Pure function, no models.

Run from api/: pytest
"""

import pytest

from text_normalize import canonical_text


@pytest.mark.parametrize(
    ("latex_style", "plain_style"),
    [
        (r"\frac{1}{2} \ln|u| + C", "1/2 ln|u| + c"),
        (r"\int_{0}^{1} x\, dx", "∫_0^1 x dx"),
        (r"x^{2}", "x²"),
        (r"x^{2}", "x^2"),
        (r"\sin^{2}x", "Sin^2x"),
        (r"\sqrt{x}", "sqrt(x)"),
        (r"\sqrt{x}", "√x"),
        (r"\lim_{t \to 0^+}", "lim_{t→0^+}"),
        (r"\sum_{n=1}^{\infty} \frac{1}{n^2}", "∑_{n=1}^∞ 1/n^2"),
        (r"a \cdot b", "a × b"),
        (r"\geq", "≥"),
    ],
)
def test_latex_and_plain_spellings_reduce_to_the_same_form(latex_style, plain_style):
    assert canonical_text(latex_style) == canonical_text(plain_style)


def test_display_wrapper_and_spaced_out_letters_are_removed():
    # real Pix2Text output for a crop whose label is "6 jan"-like handwriting
    assert canonical_text("\\[\n6 \\mathrm{o n}\n\\]") == "6on"


def test_whitespace_line_breaks_and_case_are_ignored():
    assert canonical_text("  Integration\n  7.5 ") == canonical_text("integration 7.5")


def test_nested_fractions_resolve():
    assert canonical_text(r"\frac{\frac{1}{2}}{x}") == "1/2/x"


def test_empty_and_whitespace_only_input_become_empty():
    assert canonical_text("") == ""
    assert canonical_text(" \n\t ") == ""


@pytest.mark.parametrize(
    "raw",
    [r"\[ \frac{1}{2}\int_{0}^{\infty} x^{2}\, dx \]", "du/dx = 2x", "let u = 1 + x²", r"\mathrm{o n}"],
)
def test_canonical_text_is_idempotent(raw):
    once = canonical_text(raw)
    assert canonical_text(once) == once
