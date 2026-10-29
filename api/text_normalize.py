"""
Reduce OCR output and hand-typed labels to one comparable form.

WHY this exists: Pix2Text writes LaTeX (`\\frac{1}{2}`, `\\[ ... \\]` wrappers,
letters spaced out like `I n t`), PaddleOCR writes plain text (`1/2`), and a
human labeller types plain math (`1/2`). Comparing raw strings would reward
whichever engine happens to share the labeller's notation. Mapping everything
to one canonical form first makes the comparison about the characters read,
not about notation.

WHAT it throws away, on purpose: case, all whitespace and line breaks (so
layout is not scored), and every kind of bracket (LaTeX grouping braces and
the labeller's parentheses mean the same thing for this purpose). It keeps
letters, digits and operator symbols. It is lossy and imperfect, so use it for
comparing outputs against each other, never to display text.
"""

import re
import unicodedata

_SUPERSCRIPT_DIGITS = str.maketrans(
    {"⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9"}
)

# LaTeX commands that stand for a single symbol.
_COMMAND_SYMBOLS = {
    "int": "∫", "sum": "∑", "infty": "∞", "sqrt": "√", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "pm": "±", "partial": "∂", "therefore": "∴", "in": "∈",
    "to": "→", "rightarrow": "→", "cdot": "*", "times": "*", "pi": "π", "theta": "θ", "alpha": "α",
    "beta": "β", "lambda": "λ", "delta": "δ", "sigma": "σ", "mu": "μ",
}  # fmt: skip

# Commands that only change size, font or spacing: dropped, their arguments are kept.
_STYLE_COMMANDS = {
    "left", "right", "big", "bigg", "Big", "Bigg", "quad", "qquad", "displaystyle", "limits",
    "mathrm", "mathbf", "mathit", "text", "operatorname", "boldsymbol",
}  # fmt: skip

_DISPLAY_DELIMITERS = re.compile(r"\\\[|\\\]|\\\(|\\\)|\$")
_FRACTION = re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_SPACING_ESCAPES = re.compile(r"\\[,;:! ]|\\\\")
_BRACKETS = re.compile(r"[{}()\[\]]")
_SYMBOL_ALIASES = str.maketrans({"−": "-", "–": "-", "—": "-", "×": "*", "·": "*", "⋅": "*", "÷": "/"})


def _command_to_text(match: re.Match) -> str:
    name = match.group(1)
    if name in _STYLE_COMMANDS:
        return ""
    return _COMMAND_SYMBOLS.get(name, name)  # unknown commands keep their name: \sin -> sin


def canonical_text(text: str) -> str:
    """Return the comparison form of `text`. Safe to apply twice (idempotent)."""
    text = unicodedata.normalize("NFKC", text.translate(_SUPERSCRIPT_DIGITS))
    text = _DISPLAY_DELIMITERS.sub("", text)

    previous = None
    while previous != text:  # innermost fractions first, so nesting resolves in a few passes
        previous = text
        text = _FRACTION.sub(r"\1/\2", text)

    text = _SPACING_ESCAPES.sub("", text)
    text = _COMMAND.sub(_command_to_text, text)
    text = _BRACKETS.sub("", text)
    text = text.translate(_SYMBOL_ALIASES).casefold()
    text = re.sub(r"\s+", "", text)
    return text.replace("sqrt", "√")  # the plain-text spelling of the same symbol
