# MathScan

Turn a photo or PDF of handwritten notes into editable LaTeX, plain text, and a typeset PDF — built specifically for math-heavy course notes (calculus, discrete math, CS, stats), not just general prose.

**Live app:** https://math-scan-lake.vercel.app
**API health check:** https://purav-kanda--mathscan-api-fastapi-app.modal.run/api/health

Built as a portfolio project under a hard constraint: **no paid APIs or paid models anywhere in the pipeline.** Every model used is free-tier or open-source.

## What it does

1. Upload a photo (or PDF) of handwritten notes.
2. The backend recognizes the page using a math-aware OCR model, with a general-OCR fallback for pages that are mostly prose rather than equations.
3. A free-tier LLM correction pass cleans up obvious misreads using the whole page's context (not just line-by-line).
4. Results stream back live per page. You can edit the recognized LaTeX/text directly, then export the whole set as a compiled PDF or raw `.tex`.
5. Share links let you send a read-only view of a conversion to someone else, with no account required.

## Architecture

```
api/      FastAPI backend, deployed on Modal (CPU-only container, scales to zero)
  inference.py         Pix2Text + PaddleOCR + Groq correction pipeline
  routers/ocr.py        /api/ocr/pdf, /api/ocr/images (SSE streaming per page)
  routers/export.py     LaTeX -> PDF export via Tectonic, resilient to single-region failures
  pdf_preprocessor.py    PDF -> per-page image splitting
  eval/                  accuracy benchmark harness + labeled test set
  tests/                 pytest unit tests, run in CI on every push
web/      Next.js 15 / React 19 frontend, deployed on Vercel
  KaTeX for live LaTeX rendering, local-storage-based history, server-side share links only
```

**Why these choices, briefly:**
- **Modal over a traditional VM or GPU instance** — hosts the existing FastAPI app's SSE streaming endpoints with almost no changes (`@modal.asgi_app()`), and runs CPU-only because Pix2Text's ONNX runtime needs a CUDA package the container didn't have — CPU inference is a known-working, cheaper-per-second configuration.
- **Pix2Text as the primary model** — it's math-formula-aware and outputs real LaTeX natively, unlike a general OCR engine. It underperforms on dense handwritten prose, though, so...
- **PaddleOCR always runs as a second opinion** (not just when Pix2Text reports low confidence) — a real bug found in testing: Pix2Text reported ~85% confidence on a page it was actually hallucinating garbage on. Confidently-wrong beats a confidence gate, so the fallback now always runs and whichever result scores higher wins. Costs more compute per request; worth it for correctness.
- **Groq's free-tier Llama 3.3 70B for correction**, run once per full page (not per line) so it has context to catch a misread word that doesn't fit its sentence. Its hallucination guard went through three real revisions before landing on a per-edited-chunk similarity check — small edits (≤5 words) are always allowed, larger replaced chunks must still resemble what they replaced, or the whole page's correction is rejected.
- **Tectonic instead of full TeX Live** for PDF export, to keep the container image small. Export is resilient to a single garbled region: instead of failing the whole document on one bad line of LaTeX, it comments out just the offending line (reported by Tectonic's own error output) and retries, up to 5 times.

## Accuracy benchmark

`api/eval/accuracy_benchmark.py` runs the exact production pipeline (same fallback and correction logic real users get) against a hand-labeled test set of real handwritten notes, computing:
- **Character accuracy** — Levenshtein edit distance against the transcribed ground truth.
- **Word-overlap recall** — an order-insensitive metric, added after finding that edit distance alone punishes correct-but-reordered text as harshly as genuinely wrong content.

**Current result: 71.4% mean character accuracy, 73.0% mean word-overlap recall**, from a partial run (7 of the 14 images in the current test set — see `api/eval/test_set/results.json` for exactly which ones and why). This is measured against real, clearly-legible photographed handwriting sourced from openly-shared student notes on GitHub (see `api/eval/test_set/README.md` for full sourcing and attribution). An earlier run against the project author's own (more cursive) handwriting scored 33.0%/50.5% — a real, honest difference in difficulty, not a regression.

Target per the original spec is 80% character accuracy; not yet met. The test set is still short of the 30-50 image sample size needed to treat any single percentage as fully defensible.

## Real bugs found and fixed along the way

A few worth mentioning because they were genuine, non-obvious issues caught through actual testing, not just code review:

- **Confidently-wrong OCR**: Pix2Text scored ~85% confidence on a hallucinated garbage page, so a confidence-gated fallback never triggered on the one page that needed it. Fixed by always running the fallback and comparing results directly.
- **Reading order silently broken in production**: an early fix relied on a `line_number` field that came back empty (`None`) on real handwritten pages — the fix never engaged, and the bug (fragmented, one-word-per-line output) persisted until caught by direct inspection of real output. Replaced with geometry-based line grouping from bounding boxes.
- **PDF export failing entirely on one bad region**: Tectonic aborts the whole document on the first fatal LaTeX error. Fixed with a retry loop that comments out just the reported bad line.
- **A Windows-specific benchmark hang**: `accuracy_benchmark.py`'s subprocess-per-image isolation hung to the exact configured timeout on every image, with zero output — a known Windows pitfall where a grandchild process (Paddle's C++ extension JIT step) can keep stdout/stderr pipe handles open after the tracked child exits. Fixed by redirecting to temp files instead of pipes.

## Running it locally

```
cd api
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```
Needs **Poppler** on PATH for PDF splitting (`pdftoppm`/`pdfinfo`):
- Windows: https://github.com/oschwartz10612/poppler-windows/releases
- macOS: `brew install poppler`

```
cd web
npm install
npm run dev
```

Run the backend test suite: `cd api && pytest` (also runs in CI on every push via GitHub Actions).

Run the accuracy benchmark: see `api/eval/test_set/README.md` for the labeled test set format and how to add more images.

## Known limitations

- Accuracy on messy/cursive handwriting is meaningfully lower than on clean, print-style handwriting — see the benchmark section above.
- No paid OCR/LLM fallback exists (by design) — there's a real ceiling on accuracy this pipeline can't push past without a paid or fine-tuned model.
- Test set is still below the target sample size for a fully citable accuracy number.
