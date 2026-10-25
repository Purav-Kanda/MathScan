"""
Page-level baselines for the OCR selector project.

Reads eval/out/page_outputs.jsonl (written by _collect_one.py) and the
labelled ground truth, then scores four strategies on the same pages:
Pix2Text only, PaddleOCR only, the current rule (the same pick_page_result
function production uses), and the oracle (best of the two per page, an
upper bound that no selector can beat).

Metric: character error rate = edit distance / length of the ground truth.
Reported pooled (total edits / total truth characters) and as the mean of
the per-page rates. Strings are only stripped, no other normalization.

Run from api/:  python eval/score_baselines.py
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from accuracy_benchmark import levenshtein_distance  # noqa: E402
from engine_selection import pick_page_result  # noqa: E402

OUTPUTS_PATH = HERE / "out" / "page_outputs.jsonl"
TRUTH_PATH = HERE / "test_set" / "ground_truth.json"
ENGINES = ("pix2text", "paddle")


@dataclass(frozen=True)
class PageScore:
    image: str
    truth_len: int
    edits: dict  # engine name -> edit distance to the ground truth
    rule_pick: str
    oracle_pick: str

    def cer(self, engine: str) -> float:
        return self.edits[engine] / self.truth_len


def load_rows(path: Path) -> dict:
    """Map image name -> row. If an image appears twice (a rerun), the last row wins."""
    rows = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                rows[row["image"]] = row
    return rows


def rule_pick_for(row: dict) -> str:
    """Which engine the production rule keeps for this row."""
    primary = {"confidence_mean": row["pix2text"]["confidence"]}
    fallback = {"confidence_mean": row["paddle"]["confidence"]}
    return "paddle" if pick_page_result(primary, fallback) is fallback else "pix2text"


def score_page(row: dict, truth: str) -> PageScore:
    truth = truth.strip()
    edits = {name: levenshtein_distance(row[name]["text"].strip(), truth) for name in ENGINES}
    return PageScore(
        image=row["image"],
        truth_len=len(truth),
        edits=edits,
        rule_pick=rule_pick_for(row),
        oracle_pick=min(ENGINES, key=edits.get),
    )


def summarize(scores: list, pick) -> tuple:
    """Return (pooled CER, mean per-page CER) for a strategy `pick(PageScore) -> engine`."""
    chosen = [s.edits[pick(s)] for s in scores]
    pooled = sum(chosen) / sum(s.truth_len for s in scores)
    per_page = sum(c / s.truth_len for c, s in zip(chosen, scores, strict=True)) / len(scores)
    return pooled, per_page


def main():
    truths = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
    rows = load_rows(OUTPUTS_PATH)

    unlabelled = sorted(set(rows) - set(truths))
    if unlabelled:
        print(f"ignoring {len(unlabelled)} pages with no ground truth: {', '.join(unlabelled)}")
    empty_truth = sorted(name for name in rows if name in truths and not truths[name].strip())
    if empty_truth:
        print(f"ignoring {len(empty_truth)} pages with empty ground truth: {', '.join(empty_truth)}")
    skip = set(unlabelled) | set(empty_truth)
    scores = [score_page(row, truths[name]) for name, row in sorted(rows.items()) if name not in skip]
    if not scores:
        sys.exit("nothing to score")

    print(f"pages scored: {len(scores)}\n")
    print(f"{'page':38} {'truth':>5} {'p2t':>6} {'paddle':>7}  rule    oracle")
    for s in scores:
        print(
            f"{s.image:38} {s.truth_len:>5} {s.cer('pix2text'):>6.2f} {s.cer('paddle'):>7.2f}"
            f"  {s.rule_pick:7} {s.oracle_pick}"
        )

    strategies = {
        "pix2text only": lambda s: "pix2text",
        "paddle only": lambda s: "paddle",
        "current rule": lambda s: s.rule_pick,
        "oracle": lambda s: s.oracle_pick,
    }
    print(f"\n{'strategy':16} {'pooled CER':>11} {'mean page CER':>14}")
    for label, pick in strategies.items():
        pooled, per_page = summarize(scores, pick)
        print(f"{label:16} {pooled:>11.3f} {per_page:>14.3f}")

    disagree = sum(1 for s in scores if s.rule_pick != s.oracle_pick)
    print(f"\npages where the rule and the oracle pick different engines: {disagree} of {len(scores)}")


if __name__ == "__main__":
    main()
