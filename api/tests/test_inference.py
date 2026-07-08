"""
Unit test for inference.recognize_page -- no real model, no GPU, no 30s
load time. This is the payoff of keeping inference.py's model access behind
a module-level `_p2t` variable: we can swap in a fake and test the
aggregation logic (confidence averaging, region shape) in isolation.

Run from api/: pytest
"""

import inference


class FakeP2T:
    """Stands in for Pix2Text.from_config()'s return value."""

    def __call__(self, image, resized_shape=768):
        return [
            {"text": r"x^2", "type": "isolated", "position": [[0, 0], [10, 0], [10, 10], [0, 10]], "score": 0.92},
            {"text": r"+y", "type": "isolated", "position": [[20, 0], [30, 0], [30, 10], [20, 10]], "score": 0.40},
        ]


def test_recognize_page_aggregates_confidence(monkeypatch):
    monkeypatch.setattr(inference, "_p2t", FakeP2T())

    result = inference.recognize_page(image=None)  # FakeP2T ignores `image`

    assert result["confidence_mean"] == (0.92 + 0.40) / 2
    assert len(result["regions"]) == 2
    assert result["regions"][0]["latex"] == r"x^2"
    assert result["regions"][1]["confidence"] == 0.40


def test_recognize_page_handles_missing_scores(monkeypatch):
    class FakeP2TNoScore:
        def __call__(self, image, resized_shape=768):
            return [{"text": "y", "type": "isolated", "position": [[0, 0]]}]

    monkeypatch.setattr(inference, "_p2t", FakeP2TNoScore())

    result = inference.recognize_page(image=None)

    # No scores available -> mean is None, not a crash or a fake 0.0 that
    # would silently look like "very low confidence" (see NFR-012: never
    # fabricate a confident-looking answer when we don't actually have one).
    assert result["confidence_mean"] is None
