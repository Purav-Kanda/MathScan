"""
Unit tests for engine_selection.pick_page_result -- the rule that decides whether
Pix2Text's page or PaddleOCR's page is kept. Pure function, no models.

Run from api/: pytest
"""

import pytest

from engine_selection import pick_page_result


def _page_with_confidence(value):
    return {"regions": [], "confidence_mean": value}


@pytest.mark.parametrize(
    ("primary_conf", "fallback_conf", "fallback_wins"),
    [
        (0.40, 0.90, True),  # fallback clearly higher
        (0.90, 0.40, False),  # primary clearly higher
        (0.70, 0.70, False),  # a tie keeps the primary: fallback must be strictly higher
        (0.40, None, False),  # fallback detected nothing
        (None, 0.40, True),  # primary detected nothing
        (None, None, False),  # neither detected anything: keep the primary
    ],
)
def test_pick_page_result_needs_a_strictly_higher_confidence_to_switch(
    primary_conf, fallback_conf, fallback_wins
):
    primary = _page_with_confidence(primary_conf)
    fallback = _page_with_confidence(fallback_conf)

    picked = pick_page_result(primary, fallback)

    assert picked is (fallback if fallback_wins else primary)
