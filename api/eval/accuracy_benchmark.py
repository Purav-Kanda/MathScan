"""
Formal accuracy benchmark against a fixed, labeled test set -- answers the
SRS's NFR-010 requirement directly ("must achieve >=80% character-level
accuracy") with a real, repeatable number, not a qualitative side-by-side
comparison. This is the piece flagged as missing throughout this project's
M6 work: `eval/claude_vs_pix2text.py` is great for "does this change seem to
help," but it doesn't produce one citable percentage.

WHY character-level accuracy via edit distance (Levenshtein), not exact
string match: two transcriptions of the same handwriting can differ in
whitespace/line-break placement while still being substantively correct --
exact match would call that "wrong" even when a human reading both would
call it right. Edit distance measures how many single-character
insertions/deletions/substitutions turn the prediction into the ground
truth -- exactly what "character-level accuracy" means, and exactly the
metric the SRS names. Implemented here as plain Python (no extra pip
dependency) since it's a well-known ~15-line dynamic-programming algorithm,
not worth a new requirements.txt entry for.

HOW TO USE THIS (this is the part that needs YOUR input -- I can't create
real ground truth without seeing your actual handwriting and knowing what
it says):

1. Put real photos of handwritten notes into eval/test_set/images/
   (any of .jpg/.jpeg/.png/.webp).
2. For each image, add its correct transcription to
   eval/test_set/ground_truth.json -- a JSON object mapping the image's
   filename to a plain-text string of what the page actually says (math
   written as LaTeX-ish source is fine, e.g. "x^2 + y = 7", exact LaTeX
   syntax doesn't need to be perfect since this measures characters, not
   compiled correctness).
3. Run (from api/, with the project venv active):
       python eval/accuracy_benchmark.py
   Add --try-fallback (on by default) to match what real users actually
   get (PaddleOCR + Groq correction on low-confidence pages), or
   --no-fallback to measure Pix2Text alone.
4. Results print to the terminal and get saved to
   eval/test_set/results.json (the file you'd point to for a resume line).

WHY 30-50 images is the target sample size (per ROADMAP.md), not fewer: a
handful of images can be accidentally cherry-picked (good or bad) without
meaning to -- a real sample size is what makes "X% accuracy" a claim you
can actually defend if someone asks how you measured it.

WHY TWO METRICS (character_accuracy AND word_overlap_recall), added after
running this against 3 real pages: edit-distance-based character accuracy
is order-SENSITIVE -- it penalizes a page whose words are all correct but
scrambled just as harshly as a page with genuinely wrong content. That
turned out to matter for real: a diagram/arrow-style handwritten page
scored low on character accuracy mostly because Pix2Text's region-reading
order doesn't follow the arrows a human eye would. word_overlap_recall
(order-insensitive, multiset-based word recall) is reported alongside it
specifically to distinguish "wrong content" (both metrics low) from "right
content, bad reading order/structure" (recall high, accuracy low) --
two different bugs with two different fixes.

WHY EACH IMAGE RUNS IN ITS OWN SUBPROCESS (_recognize_one.py), not a single
shared process: a real run against 3 images crashed silently -- no Python
traceback, no summary printed, the process just died -- after 2 of 3 images
succeeded. Re-running the exact same third image alone, in its own process,
worked fine. That combination (fails only after prior images ran in the same
process; works fine in isolation) points at native-code resource buildup in
Paddle/ONNX across repeated model loads, which segfaults instead of raising
a catchable Python exception. Isolating each image into a fresh subprocess
means each one starts with clean native state, so this can't happen -- at
the cost of reloading the models per image.

WHY --workers (subprocess parallelism), added after a 16-image run took ~1
hour: each subprocess reloads the models (~30-60s) and one image can hang
long enough to hit the timeout, and doing them strictly one-at-a-time meant
all that dead time stacked up serially. Because each image already runs in
its OWN isolated subprocess (above), several can run at once with no shared
state to corrupt -- the isolation that made this slow is exactly what makes
it safe to parallelize. Default is a conservative 3 concurrent workers
(each OCR process is memory-hungry; 3 keeps RAM sane on a typical laptop --
raise it with --workers if you have the cores/RAM, lower to 1 to reproduce
the old serial behavior). A hung image now also fails fast (--timeout,
default 300s) instead of blocking the whole run for 10 minutes.
"""

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TEST_SET_DIR = Path(__file__).resolve().parent / "test_set"
IMAGES_DIR = TEST_SET_DIR / "images"
GROUND_TRUTH_PATH = TEST_SET_DIR / "ground_truth.json"
RESULTS_PATH = TEST_SET_DIR / "results.json"
WORKER_SCRIPT = Path(__file__).resolve().parent / "_recognize_one.py"

# The SRS's NFR-010 target this benchmark exists to answer.
ACCURACY_TARGET = 0.80


def levenshtein_distance(a: str, b: str) -> int:
    """
    Standard edit-distance dynamic program: the minimum number of single-
    character insertions, deletions, or substitutions needed to turn `a`
    into `b`. O(len(a) * len(b)) time, O(min(len(a), len(b))) memory (only
    ever keeps two rows of the DP table, not the whole grid) -- fine at the
    scale of a single page's worth of text (a few hundred to low thousands
    of characters), which is all this ever runs against.
    """
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if len(b) == 0:
        return len(a)

    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current_row = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            insert_cost = current_row[j - 1] + 1
            delete_cost = previous_row[j] + 1
            substitute_cost = previous_row[j - 1] + (0 if ca == cb else 1)
            current_row[j] = min(insert_cost, delete_cost, substitute_cost)
        previous_row = current_row
    return previous_row[-1]


def character_accuracy(predicted: str, ground_truth: str) -> float:
    """
    1 - (edit distance / length of ground truth), clamped to [0, 1]. A
    prediction that's twice as long as the ground truth and shares nothing
    with it can drive edit distance above len(ground_truth) -- clamping
    keeps that as a clean 0% instead of a confusing negative number.
    """
    if not ground_truth:
        return 1.0 if not predicted else 0.0
    distance = levenshtein_distance(predicted, ground_truth)
    return max(0.0, 1.0 - (distance / len(ground_truth)))


def _tokenize_words(text: str) -> list:
    import re

    return re.findall(r"[a-zA-Z0-9]+", text.lower())


def word_overlap_recall(predicted: str, ground_truth: str) -> float:
    """
    Second, order-INSENSITIVE metric, added after a real finding from
    running this against real notes: character_accuracy (edit distance) is
    extremely sensitive to REORDERING, not just wrongness. A real page of
    diagram-style handwritten notes (arrows, branching structure) scored a
    low character accuracy even though nearly every individual word was
    actually present in the prediction -- the region-detection model reads
    regions in its own top-to-bottom order, which doesn't match how a human
    eye follows arrows across a non-linear page. That's a real limitation
    of the OUTPUT'S reading order, not necessarily of what content was
    actually recognized -- and character_accuracy alone can't tell the two
    apart.

    This measures: what fraction of the ground truth's words (as a
    multiset, so a word appearing 3 times in the ground truth needs to
    appear at least 3 times in the prediction to count fully, not just
    once) show up ANYWHERE in the prediction, regardless of position.
    Punctuation-stripped, case-insensitive. High word-overlap-recall +
    low character_accuracy is itself a meaningful, specific finding: the
    right content was captured, but not in a usable reading order or
    structure -- a different problem than genuinely wrong/missing content
    (which shows up as BOTH metrics being low).
    """
    from collections import Counter

    truth_words = _tokenize_words(ground_truth)
    if not truth_words:
        return 1.0 if not _tokenize_words(predicted) else 0.0

    truth_counts = Counter(truth_words)
    predicted_counts = Counter(_tokenize_words(predicted))
    overlap = sum(min(count, predicted_counts[word]) for word, count in truth_counts.items())
    return overlap / len(truth_words)


def extract_predicted_text(result: dict) -> str:
    """
    Pulls the actual recognized text out of recognize_page()'s return
    shape. As of M6.5, a page collapses to a single "page"-type region
    (see inference._collapse_to_page_result) -- this just joins whatever
    regions come back, so it still works correctly against older result
    shapes too (multiple regions) if this is ever run against a version
    of inference.py from before that change.
    """
    regions = result.get("regions", [])
    return "\n\n".join(r.get("latex", "") for r in regions if r.get("latex"))


def load_ground_truth() -> dict:
    if not GROUND_TRUTH_PATH.exists():
        print(f"No ground truth file found at {GROUND_TRUTH_PATH}.")
        print("See this script's module docstring for the expected format.")
        sys.exit(1)
    with open(GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _recognize_via_subprocess(
    image_path: Path, apply_contrast: bool, try_fallback: bool, resized_shape: int, timeout: int
) -> dict:
    """
    Runs _recognize_one.py as a fresh subprocess for a single image (see the
    module docstring for why). Returns the parsed result dict on success, or
    raises RuntimeError with a clear reason (crash, timeout, or malformed
    output) so the caller can skip just this one image instead of losing the
    whole benchmark run.

    WHY stdout/stderr go to TEMP FILES, not PIPE (`capture_output=True`) --
    found via a real, reproducible Windows hang: every single image timed
    out at the full --timeout value (tried both 900s and 180s), with zero
    output captured, even though running the exact same command by hand in
    a terminal finished in seconds. That combination -- identical command,
    hangs only when piped -- is a known Windows subprocess pitfall: if the
    child spawns a grandchild process that inherits the parent's stdout/
    stderr pipe HANDLES (which Paddle's C++ extension JIT step can do --
    it printed "No ccache found... recompiling all source files may be
    required" right before every hang), the grandchild can keep those pipe
    write-ends open even after the tracked child process exits. Python's
    subprocess.run(capture_output=True) then blocks waiting for the pipes
    to actually close (EOF), which never happens until the grandchild also
    exits -- so it just sits there until --timeout fires, regardless of
    whether the real work finished long ago. Redirecting to files instead
    of pipes sidesteps this whole category of deadlock: a file handle
    being held open by a grandchild doesn't block anyone from reading what
    was already written to it.

    WHY manual mkdtemp()/rmtree(ignore_errors=True), not the
    TemporaryDirectory() context manager -- found via a second real bug,
    right after the fix above: when an image genuinely DOES time out
    (subprocess.run kills the tracked child, but a grandchild -- e.g. the
    same Paddle JIT-compile process -- can outlive it), that grandchild
    can still be holding stdout.txt/stderr.txt open on Windows. The
    TemporaryDirectory context manager's own cleanup then fails with
    PermissionError while trying to delete a file that's still in use --
    and since that happens while __exit__ is unwinding the RuntimeError
    we just raised for the timeout, the PermissionError replaces it and
    escapes uncaught, crashing the entire benchmark run instead of just
    recording that one image as failed. ignore_errors=True on the
    cleanup means a leftover locked file is skipped (leaking a harmless
    temp file until Windows cleans %TEMP% on its own) rather than taking
    down the whole run.
    """
    # Marker string must match _recognize_one.py's RESULT_MARKER constant exactly.
    # Not imported directly to avoid relative-import fragility when this script is
    # invoked as `python eval/accuracy_benchmark.py` (eval/ isn't a package on
    # sys.path in that case) -- it's a plain string, kept in sync by convention.
    result_marker = "RESULT_JSON:"

    cmd = [sys.executable, str(WORKER_SCRIPT), str(image_path), "--resized-shape", str(resized_shape)]
    if apply_contrast:
        cmd.append("--apply-contrast")
    if not try_fallback:
        cmd.append("--no-fallback")

    tmp_dir = tempfile.mkdtemp()
    try:
        stdout_path = Path(tmp_dir) / "stdout.txt"
        stderr_path = Path(tmp_dir) / "stderr.txt"
        try:
            with open(stdout_path, "w", encoding="utf-8") as out_f, open(stderr_path, "w", encoding="utf-8") as err_f:
                subprocess.run(cmd, stdout=out_f, stderr=err_f, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"timed out after {exc.timeout}s") from exc

        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    for line in stdout_text.splitlines():
        if line.startswith(result_marker):
            return json.loads(line[len(result_marker) :])

    # No RESULT_JSON line found -- the worker crashed or exited before
    # printing its result. Surface the tail of stderr (the most likely
    # place a native crash message would land) so the failure is
    # diagnosable instead of just silently missing.
    stderr_tail = "\n".join(stderr_text.strip().splitlines()[-15:])
    raise RuntimeError(f"subprocess exited with no result produced.\n{stderr_tail}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--try-fallback",
        dest="try_fallback",
        action="store_true",
        default=True,
        help="Match production behavior (PaddleOCR fallback + Groq correction on low-confidence pages). "
        "On by default -- this is what real users actually get, so it's the more honest number to report.",
    )
    parser.add_argument(
        "--no-fallback",
        dest="try_fallback",
        action="store_false",
        help="Measure Pix2Text alone, no PaddleOCR/Groq fallback -- useful for isolating how much the "
        "fallback pipeline specifically is contributing to the overall number.",
    )
    parser.add_argument(
        "--apply-contrast",
        action="store_true",
        help="Run every image through inference.py's enhance_contrast() first, matching the app's "
        "opt-in checkbox -- off by default, matching the app's own default.",
    )
    parser.add_argument(
        "--resized-shape",
        type=int,
        default=768,
        help="Passed straight to Pix2Text via recognize_page's resized_shape parameter. Default (768) "
        "matches the live app. This is a free (no API cost) knob flagged in inference.py's own "
        "docstring as an untested hypothesis for dense/busy handwritten pages -- try e.g. 1024 or "
        "1536 and compare mean_accuracy against a 768 baseline run to see if it actually helps before "
        "changing the app's default.",
    )
    parser.add_argument(
        "--output-suffix",
        default="",
        help="Appended to results.json's filename (e.g. --output-suffix _1024 writes "
        "results_1024.json) so runs with different settings don't overwrite each other -- "
        "useful when comparing --resized-shape/--apply-contrast combinations side by side.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="How many image subprocesses to run at once (default 3). Each still runs in its "
        "own isolated subprocess -- this just runs several in parallel to cut wall-clock time. "
        "Each OCR process is memory-hungry, so raise this only if you have the cores/RAM; use "
        "--workers 1 for the old strictly-serial behavior.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-image seconds before a subprocess is killed and that image counted as failed "
        "(default 300). A single hung image (e.g. a Pix2Text decoder repetition loop) then fails "
        "fast instead of blocking the whole run.",
    )
    args = parser.parse_args()

    ground_truth = load_ground_truth()
    if not ground_truth:
        print(f"{GROUND_TRUTH_PATH} exists but is empty -- add at least one entry.")
        sys.exit(1)

    # Only images that actually exist on disk -- skip (with a note) any
    # ground-truth entry whose image file is missing, before dispatching work.
    to_run = []
    for filename, expected_text in ground_truth.items():
        image_path = IMAGES_DIR / filename
        if not image_path.exists():
            print(f"SKIPPING {filename}: not found in {IMAGES_DIR}")
            continue
        to_run.append((filename, expected_text, image_path))

    workers = max(1, args.workers)
    print(
        f"Running {len(to_run)} image(s), {workers} at a time, each in its own subprocess "
        f"(see module docstring for why)..."
    )

    def _score_one(job):
        """Runs one image end-to-end (subprocess -> metrics). Returns a
        (filename, result_dict_or_None, error_or_None) tuple so the parent
        loop can record a success or a failure without either one aborting
        the others -- each runs in its own isolated subprocess anyway."""
        filename, expected_text, image_path = job
        try:
            result = _recognize_via_subprocess(
                image_path,
                apply_contrast=args.apply_contrast,
                try_fallback=args.try_fallback,
                resized_shape=args.resized_shape,
                timeout=args.timeout,
            )
        except (RuntimeError, json.JSONDecodeError) as exc:
            return (filename, None, str(exc))

        predicted_text = extract_predicted_text(result)
        return (
            filename,
            {
                "filename": filename,
                "accuracy": character_accuracy(predicted_text, expected_text),
                "word_overlap_recall": word_overlap_recall(predicted_text, expected_text),
                "confidence_mean": result.get("confidence_mean"),
                "predicted_text": predicted_text,
                "expected_text": expected_text,
            },
            None,
        )

    # Collected keyed by filename, then re-ordered to match ground_truth
    # below -- parallel completion order is nondeterministic, but the saved
    # report should always list images in a stable, comparable order.
    scored = {}
    failed_images = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for filename, per_image, error in executor.map(_score_one, to_run):
            if error is not None:
                print(f"{filename}: FAILED -- {error}")
                failed_images.append(filename)
                continue
            scored[filename] = per_image
            print(
                f"{filename}: {per_image['accuracy'] * 100:.1f}% character accuracy, "
                f"{per_image['word_overlap_recall'] * 100:.1f}% word-overlap recall"
            )

    # Stable order: follow the ground-truth file's ordering, skipping failures.
    per_image_results = [scored[fn] for fn, _, _ in to_run if fn in scored]

    if failed_images:
        print(f"\n{len(failed_images)} image(s) failed and were excluded from the results below: {failed_images}")

    if not per_image_results:
        print("No test images were actually found/scored -- nothing to report.")
        sys.exit(1)

    mean_accuracy = sum(r["accuracy"] for r in per_image_results) / len(per_image_results)
    mean_word_overlap_recall = sum(r["word_overlap_recall"] for r in per_image_results) / len(per_image_results)
    passed = mean_accuracy >= ACCURACY_TARGET

    print(f"\n{'=' * 70}")
    print(f"Mean character accuracy across {len(per_image_results)} image(s): {mean_accuracy * 100:.1f}%")
    print(f"Mean word-overlap recall (order-insensitive): {mean_word_overlap_recall * 100:.1f}%")
    print(f"SRS target (NFR-010): >={ACCURACY_TARGET * 100:.0f}% -- {'PASSED' if passed else 'NOT MET'}")
    if mean_word_overlap_recall - mean_accuracy > 0.15:
        print(
            "NOTE: word-overlap recall is notably higher than character accuracy -- most of the "
            "right words are being found, but reading order/structure is dragging edit-distance "
            "down. That's a different problem than wrong content (see per-image predicted_text)."
        )
    print(f"{'=' * 70}")

    output_path = (
        RESULTS_PATH
        if not args.output_suffix
        else RESULTS_PATH.with_name(f"{RESULTS_PATH.stem}{args.output_suffix}{RESULTS_PATH.suffix}")
    )
    output_path.write_text(
        json.dumps(
            {
                "mean_accuracy": mean_accuracy,
                "mean_word_overlap_recall": mean_word_overlap_recall,
                "target": ACCURACY_TARGET,
                "passed": passed,
                "sample_size": len(per_image_results),
                "failed_images": failed_images,
                "try_fallback": args.try_fallback,
                "apply_contrast": args.apply_contrast,
                "resized_shape": args.resized_shape,
                "workers": workers,
                "timeout": args.timeout,
                "per_image": per_image_results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Full results saved to {output_path}")


if __name__ == "__main__":
    main()
