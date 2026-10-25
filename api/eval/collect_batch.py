"""
Run _collect_one.py over a folder of images in small batches, resumable.

Each batch is one worker process, so the OCR models load once per batch and
not once per image. Rows are appended to the output JSONL as soon as a batch
finishes, and images already in that file are skipped, so a crash or Ctrl+C
costs at most one batch.

Run from api/:
    python eval/collect_batch.py eval/label_set/crops eval/out/crop_outputs.jsonl
    python eval/collect_batch.py eval/label_set/crops eval/out/crop_outputs.jsonl --limit 2
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE / "_collect_one.py"
MARKER = "RESULT_JSON:"


def finished_names(out_path: Path) -> set:
    if not out_path.exists():
        return set()
    with open(out_path, encoding="utf-8") as fh:
        return {json.loads(line)["image"] for line in fh if line.strip()}


def run_batch(batch: list, out_path: Path) -> int:
    """Run one worker process over `batch`, append its rows, return rows written."""
    error_log = out_path.with_suffix(".log")
    # stdout goes to a temp file, not a pipe: the repo's benchmark notes record
    # a Windows pipe-handle deadlock with chatty OCR libraries.
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8") as sink,
        open(error_log, "a", encoding="utf-8") as err,
    ):
        proc = subprocess.run(
            [sys.executable, str(WORKER), *[str(p) for p in batch]], stdout=sink, stderr=err
        )
        sink.seek(0)
        rows = [ln[len(MARKER) :].strip() for ln in sink if ln.startswith(MARKER)]

    if proc.returncode != 0:
        print(f"  worker exited with code {proc.returncode}; keeping the {len(rows)} rows it finished")
    with open(out_path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(row + "\n")
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("images_dir", type=Path)
    parser.add_argument("out_path", type=Path)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None, help="process at most this many pending images")
    args = parser.parse_args()

    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    skip = finished_names(args.out_path)
    pending = [p for p in sorted(args.images_dir.glob("*.png")) if p.name not in skip]
    if args.limit is not None:
        pending = pending[: args.limit]
    print(f"{len(skip)} already done, {len(pending)} to run")

    written = 0
    for start in range(0, len(pending), args.batch_size):
        batch = pending[start : start + args.batch_size]
        got = run_batch(batch, args.out_path)
        written += got
        batch_no = start // args.batch_size + 1
        print(f"batch {batch_no}: {got}/{len(batch)} rows ({written}/{len(pending)} total)")

    left = len(pending) - written
    print("all done" if left == 0 else f"{left} images missing a row; rerun this command to retry them")


if __name__ == "__main__":
    main()
