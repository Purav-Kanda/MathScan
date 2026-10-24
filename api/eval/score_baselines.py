"""
Page-level baselines for the OCR selector project.

Reads eval/out/page_outputs.jsonl (written by _collect_one.py) and the
labelled ground truth, then scores four strategies on the same pages:
Pix2Text only, PaddleOCR only, the current rule (higher mean confidence
wins), and the oracle (best of the two per page, an upper bound that no
selector can beat).

Metric: character error rate = edit distance / length of the ground truth.
Reported pooled (total edits / total truth characters) and as the mean of
the per-page rates. Strings are only stripped, no other normalization.

Run from api/:  python eval/score_baselines.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from accuracy_benchmark import levenshtein_distance

OUTPUTS_PATH = HERE / "out" / "page_outputs.jsonl"
TRUTH_PATH = HERE / "test_set" / "ground_truth.json"


def load_rows(path: Path) -> list:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def current_rule_pick(row: dict) -> str:
    """Mirror inference.recognize_page: PaddleOCR wins only on a strictly higher confidence."""
    p2t_conf = row["pix2text"]["confidence"]
    paddle_conf = row["paddle"]["confidence"]
    if paddle_conf is not None and (p2t_conf is None or paddle_conf > p2t_conf):
        return "paddle"
    return "pix2text"


def edit_count(row: dict, engine: str, truth: str) -> int:
    return levenshtein_distance(row[engine]["text"].strip(), truth.strip())


def score_strategy(label: str, picks: list, errs: list, truth_lens: list) -> dict:
    chosen = [e[p] for e, p in zip(errs, picks)]
    pooled = sum(chosen) / sum(truth_lens)
    per_page = sum(c / n for c, n in zip(chosen, truth_lens)) / len(chosen)
    return {"strategy": label, "pooled_cer": pooled, "mean_page_cer": per_page}


def main():
    truths = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
    rows = [r for r in load_rows(OUTPUTS_PATH) if r["image"] in truths]

    errs, truth_lens = [], []
    for r in rows:
        t = truths[r["image"]].strip()
        errs.append({"pix2text": edit_count(r, "pix2text", t), "paddle": edit_count(r, "paddle", t)})
        truth_lens.append(len(t))

    rule_picks = [current_rule_pick(r) for r in rows]
    oracle_picks = [min(e, key=e.get) for e in errs]

    print(f"pages scored: {len(rows)}\n")
    print(f"{'page':38} {'truth':>5} {'p2t':>6} {'paddle':>7}  rule    oracle")
    for r, e, n, rp, op in zip(rows, errs, truth_lens, rule_picks, oracle_picks):
        print(f"{r['image']:38} {n:>5} {e['pix2text'] / n:>6.2f} {e['paddle'] / n:>7.2f}  {rp:7} {op}")

    results = [
        score_strategy("pix2text only", ["pix2text"] * len(rows), errs, truth_lens),
        score_strategy("paddle only", ["paddle"] * len(rows), errs, truth_lens),
        score_strategy("current rule", rule_picks, errs, truth_lens),
        score_strategy("oracle", oracle_picks, errs, truth_lens),
    ]
    print(f"\n{'strategy':16} {'pooled CER':>11} {'mean page CER':>14}")
    for res in results:
        print(f"{res['strategy']:16} {res['pooled_cer']:>11.3f} {res['mean_page_cer']:>14.3f}")

    disagree = sum(1 for rp, op in zip(rule_picks, oracle_picks) if rp != op)
    print(f"\npages where the rule and the oracle pick different engines: {disagree} of {len(rows)}")


if __name__ == "__main__":
    main()
