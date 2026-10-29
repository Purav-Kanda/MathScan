"""
Crop-level baselines for the OCR selector project.

Same idea as score_baselines.py but on the hand-labelled line crops. Both the
labels and the engine outputs go through text_normalize.canonical_text first,
so Pix2Text's LaTeX and Paddle's plain text are compared on equal footing.

Reports, per strategy (Pix2Text only, PaddleOCR only, current rule, oracle):
  - pooled CER   = total edits / total truth characters
  - mean CER     = average of the per-crop rates
  - match rate   = share of crops whose normalized output equals the label

The rule here is the production confidence rule applied to crop confidences.
Production applies it to whole pages, so this is the same logic on smaller
inputs, not the exact production setting.

Run from api/:  python eval/score_crops.py
"""

import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from accuracy_benchmark import levenshtein_distance  # noqa: E402
from score_baselines import ENGINES, load_rows, rule_pick_for  # noqa: E402
from text_normalize import canonical_text  # noqa: E402

OUTPUTS_PATH = HERE / "out" / "crop_outputs.jsonl"
LABELS_PATH = HERE / "label_set" / "labels.json"


@dataclass(frozen=True)
class CropScore:
    crop: str
    truth_len: int
    edits: dict  # engine name -> edit distance on normalized text
    rule_pick: str
    oracle_pick: str

    @property
    def page(self) -> str:
        """Source page of the crop (name without the trailing _NN.png)."""
        return self.crop.rsplit("_", 1)[0]

    def cer(self, engine: str) -> float:
        return self.edits[engine] / self.truth_len


def score_crop(row: dict, label: str) -> CropScore | None:
    """Score one crop, or None when the normalized label is empty (nothing to compare)."""
    truth = canonical_text(label)
    if not truth:
        return None
    edits = {eng: levenshtein_distance(canonical_text(row[eng]["text"]), truth) for eng in ENGINES}
    return CropScore(
        crop=row["image"],
        truth_len=len(truth),
        edits=edits,
        rule_pick=rule_pick_for(row),
        oracle_pick=min(ENGINES, key=edits.get),  # ties go to Pix2Text, like the rule
    )


def summarize(scores: list, pick) -> tuple:
    """Return (pooled CER, mean CER, exact-match rate) for a strategy `pick(CropScore) -> engine`."""
    chosen = [s.edits[pick(s)] for s in scores]
    pooled = sum(chosen) / sum(s.truth_len for s in scores)
    mean = sum(c / s.truth_len for c, s in zip(chosen, scores, strict=True)) / len(scores)
    matches = sum(1 for c in chosen if c == 0) / len(scores)
    return pooled, mean, matches


STRATEGIES = {
    "pix2text only": lambda s: "pix2text",
    "paddle only": lambda s: "paddle",
    "current rule": lambda s: s.rule_pick,
    "oracle": lambda s: s.oracle_pick,
}


def load_scores(outputs_path: Path = OUTPUTS_PATH, labels_path: Path = LABELS_PATH) -> list:
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    scores = []
    for name, row in sorted(load_rows(outputs_path).items()):
        entry = labels.get(name)
        if entry is None or entry.get("skip"):
            continue
        score = score_crop(row, entry["latex"])
        if score is not None:
            scores.append(score)
    return scores


def main():
    scores = load_scores()
    if not scores:
        sys.exit("nothing to score")

    print(f"crops scored: {len(scores)}\n")
    print(f"{'strategy':16} {'pooled CER':>11} {'mean CER':>9} {'match rate':>11}")
    for label, pick in STRATEGIES.items():
        pooled, mean, matches = summarize(scores, pick)
        print(f"{label:16} {pooled:>11.3f} {mean:>9.3f} {matches:>11.1%}")

    disagree = sum(1 for s in scores if s.rule_pick != s.oracle_pick)
    print(f"\ncrops where the rule and the oracle pick different engines: {disagree} of {len(scores)}")

    wins = defaultdict(int)
    for s in scores:
        if s.edits["pix2text"] != s.edits["paddle"]:
            wins[s.oracle_pick] += 1
        else:
            wins["tie"] += 1
    print(f"better engine per crop: {dict(wins)}")

    print(f"\n{'page':16} {'crops':>5} {'p2t':>6} {'paddle':>7} {'rule':>6} {'oracle':>7}  (pooled CER)")
    by_page = defaultdict(list)
    for s in scores:
        by_page[s.page].append(s)
    for page, group in sorted(by_page.items()):
        cols = [summarize(group, STRATEGIES[k])[0] for k in ("pix2text only", "paddle only", "current rule", "oracle")]
        print(f"{page:16} {len(group):>5} " + " ".join(f"{c:>6.2f}" for c in cols))


if __name__ == "__main__":
    main()
