# MathScan — Technical Details

Everything technical about the project in one place. Numbers and behavior below were read from the actual code in this repo, not from memory.

---

## 1. System overview

```
Browser (Next.js on Vercel)
   │  multipart upload (PDF or images)
   ▼
FastAPI on Modal  ──►  /api/ocr/pdf | /api/ocr/images   (streams one SSE message per page)
   │                      │
   │                      ├─ Pix2Text  (math-aware OCR, always runs)
   │                      ├─ PaddleOCR (general OCR, always runs when try_fallback=True)
   │                      ├─ pick higher-confidence result
   │                      ├─ post-processing (reading order, spacing, typos)
   │                      └─ Groq Llama 3.3 70B whole-page correction (guarded)
   │
   ├─ /api/export/tex | /api/export/pdf   (Tectonic LaTeX compiler)
   └─ /api/share (POST/GET)               (modal.Dict key-value store)
```

Constraint that shaped everything: **no paid APIs or models.** Pix2Text and PaddleOCR are open source; Groq is used on its free tier.

---

## 2. Tech stack

| Layer | Choice | Version |
|---|---|---|
| Backend framework | FastAPI + Uvicorn | 0.115.0 / 0.30.6 |
| Primary OCR | Pix2Text | 1.1.2.2 |
| Fallback OCR | PaddleOCR (PP-OCRv6 det/rec, doc orientation, UVDoc, textline orientation) | requirements-fallback.txt |
| LLM correction | Groq `llama-3.3-70b-versatile` | free tier, via HTTPS |
| PDF → images | pdf2image + Poppler (`pdftoppm`/`pdfinfo`), PyMuPDF | 1.17.0 / 1.28.0 |
| Text repair | wordninja 2.0.0, pyspellchecker 0.8.1 | pure Python |
| LaTeX → PDF | Tectonic (musl "semistatic" build) | 0.16.9 |
| Hosting (API) | Modal, CPU-only container, Python 3.11, Debian slim | — |
| Frontend | Next.js 15.5, React 19, TypeScript 5.7, Tailwind 3.4, KaTeX 0.16.11 | — |
| Hosting (web) | Vercel | — |
| Tests / CI | pytest 8.3 (+ pytest-asyncio), GitHub Actions on Ubuntu / Python 3.11 | — |
| Share storage | `modal.Dict` (named `mathscan-shares`), in-memory dict fallback locally | — |

---

## 3. Backend

### 3.1 App entry (`api/main.py`)
- FastAPI app with a `lifespan` hook that loads Pix2Text **once at process start** (~30 s). `/api/health` returns `{"status":"ok","model_loaded":bool}` so traffic isn't routed during the load window.
- CORS locked to `ALLOWED_ORIGIN` env var (default `https://math-scan-lake.vercel.app`). Needed because frontend and backend are different origins.
- Routers: `ocr`, `export`, `share`. `/api/ocr/test` is a leftover single-image smoke endpoint that calls `recognize_page` with defaults (`try_fallback=False`), so it does **not** use PaddleOCR — useful to know when comparing it with the real endpoints.

### 3.2 OCR endpoints (`api/routers/ocr.py`)
- `POST /api/ocr/pdf` — one PDF (max 25 MB, max 50 pages, 200 DPI), `POST /api/ocr/images` — up to 50 images (25 MB each). Both take an `enhance_contrast` form flag.
- Both stream **Server-Sent Events**: one `data: {...}\n\n` message per page with `{page, total, result}` (or `{page, total, error}`).
- Uploads are written to `/tmp/jobs/<uuid>/` *before* the stream generator starts (FastAPI closes `UploadFile` once the endpoint function returns, so reading inside the generator would fail with "I/O operation on closed file").
- `recognize_page` is synchronous/CPU-bound, so it's dispatched with `asyncio.to_thread` to keep the event loop (and `/api/health`) responsive.
- Job directory is deleted in a `finally` block no matter how the stream ends (privacy: files are processed and deleted immediately).
- Both endpoints call `recognize_page(..., try_fallback=True)`.

### 3.3 Recognition pipeline (`api/inference.py`, ~1,200 lines)
`recognize_page(image, apply_contrast=False, resized_shape=768, try_fallback=False)`:

1. **Optional contrast enhancement** — `ImageOps.autocontrast(cutoff=1)` (histogram-adaptive, clips extreme 1% each end).
2. **Pix2Text** via `recognize_text_formula(return_text=False, resized_shape=768)`. The full-page API (`recognize_page`) was abandoned because its layout detector classified real handwritten math as "figure" and returned empty text.
3. **CJK stripping** — removes Chinese/Japanese/Korean characters (Pix2Text hallucinates these mid-formula on English notes). Deliberately narrow so Greek letters/arrows/math symbols are untouched.
4. **PaddleOCR second opinion** (when `try_fallback=True`) — always runs, *not* gated on Pix2Text confidence. Whichever result has the higher mean confidence wins. All PaddleOCR regions are typed `"text"` (it returns prose, not LaTeX). Its output goes through `_fix_missing_spaces` (wordninja, only on chunks ≥ 20 chars) then `_fix_typos` (pyspellchecker, conservative: skips tokens with digits/symbols and words ≤ 2 letters, uses a boosted domain vocabulary).
5. **Collapse to one page-level region** (`_collapse_to_page_result`): regions are joined in reading order, then corrected as a whole page, then returned as a single region of `type: "page"` with `confidence`, `region_count`, and `low_confidence_count` (regions < 0.70).

**Reading order** (`_assign_line_indices` / `_combine_regions_to_page`): derived from bounding-box geometry. Regions whose vertical centers are within 0.5× the median region height belong to one line; separators are a space (same line), a newline (next line), or a blank line (vertical gap > 1.6× median height, or no bbox, as with PaddleOCR). This replaced an earlier approach using Pix2Text's `line_number`, which turned out to be `None` on real handwritten pages.

**Confidence** is the mean of per-region scores (Pix2Text only exposes a per-region score, not per-token).

### 3.4 Groq correction pass and its hallucination guards
- One call per page (`temperature: 0`, `max_tokens: 512`, 10 s timeout) with the whole page in the prompt. No `GROQ_API_KEY` ⇒ complete no-op (this is how CI runs). Any failure ⇒ returns the original text.
- Prompt rules: fix only character-level misreads, no paraphrasing, don't touch LaTeX delimiters/braces, may remove one or two clearly spurious stray words (e.g. "lecture chopper 2" → "Chapter 2"), reply `NOCHANGE` if nothing needs fixing.
- Guards, applied in order; any failure rejects the **whole** page correction and keeps the original: refusal-phrase detection → repetition-garbage detection → `_page_correction_changed_too_much`.
- `_page_correction_changed_too_much` (final design after four iterations): diff original vs corrected **by word** with `difflib.SequenceMatcher`. Each changed chunk of ≤ 5 words is always allowed; a larger chunk must have ≥ 0.5 character similarity (`SequenceMatcher.ratio()`) to the text it replaces, otherwise the page is rejected. History: exact-word-count match (too strict) → allow ≤ 3 deletions, ban insertions (still too strict) → whole-page similarity ratio (diluted by long pages; let a fully invented line through) → per-chunk (current).
- Note: older per-region helpers (`_llm_correct_text`, `_llm_correct_latex`) still exist in the file but the pipeline now uses the page-level pass.

### 3.5 PDF preprocessing (`api/pdf_preprocessor.py`)
Splits PDFs into per-page JPEGs at 200 DPI via Poppler; raises `EncryptedPDFError` for locked PDFs.

### 3.6 Export (`api/routers/export.py`)
- `POST /api/export/tex` — builds a single `article` document (amsmath/amssymb/amsfonts, 1 in margins) with a `\section{Page N}` per page.
- `POST /api/export/pdf` — compiles with **Tectonic** in a temp dir. Tectonic aborts the whole document on the first fatal LaTeX error, so on failure the code parses `document.tex:<line>:` from stderr, comments out only that line (`% [omitted: broke PDF compilation] …`), and retries — up to `_MAX_COMPILE_RETRIES = 5`. It stops early if no line number is found or the reported line is already commented out, and then returns a 500 with error context.

### 3.7 Share links (`api/routers/share.py`, `api/share_store.py`)
- `POST /api/share` stores the pages under `secrets.token_hex(4)` (8 hex chars) and returns `{id}`; `GET /api/share/{id}` returns them or 404.
- Storage is `modal.Dict.from_name("mathscan-shares")` so shares survive container scale-to-zero and are visible across concurrent containers. If Modal isn't available (local dev) it falls back to an in-memory dict.

---

## 4. Deployment (`api/modal_app.py`)

- Image: `debian_slim(python_version="3.11")` with apt packages `poppler-utils`, `libfontconfig1`, `curl`, `libgl1`, `libglib2.0-0` (OpenCV via Pix2Text), `libgraphite2-3`, `libharfbuzz0b`, `libicu-dev` (Tectonic), `libgomp1` (Paddle OpenMP).
- Pip: `requirements.txt` (lean, used by CI) + `requirements-fallback.txt` (PaddlePaddle/PaddleOCR, deployment only).
- **Model weights baked into the image** at build time (Pix2Text and PaddleOCR), so cold starts only pay the in-memory load, never a download.
- `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` set on the image — PaddleX's pre-download connectivity check hung the Modal build.
- Tectonic installed from the **musl** release tarball (the glibc build needed GLIBC 2.38+, which the base image lacks).
- Function config: CPU-only (a GPU attempt crashed because ONNX Runtime tried `CUDAExecutionProvider` without `onnxruntime-gpu`), `scaledown_window=300` (warm 5 min, then scale to $0), `timeout=120`, `@modal.concurrent(max_inputs=4)`, secret `groq-api-key` injected as `GROQ_API_KEY`.
- `main.py` stays Modal-agnostic; `modal_app.py` just wraps it with `@modal.asgi_app()`.
- Deploy: `modal deploy api/modal_app.py` from the repo root.

---

## 5. Frontend (`web/`)

- Next.js App Router. Routes: `/` (upload + results) and `/share/[id]` (read-only shared view).
- `UploadFlow.tsx` (~790 lines) is the main component. Because the OCR endpoints are POST, it reads the SSE stream with `fetch` + `response.body.getReader()` + `TextDecoder` (not `EventSource`, which only supports GET) and updates per-page results as messages arrive.
- Client-side validation (`lib/validateFiles.ts`): PDF/PNG/JPEG/WebP only, 25 MB per file, 50 files max.
- `EditableLatexRegion` lets users edit the recognized text; `LatexPreview` renders mixed prose + math with KaTeX; confidence badges and low-confidence highlighting use the `confidence` / `low_confidence_count` fields.
- History (`lib/history.ts`): `localStorage` key `mathscan-history`, capped at 20 entries, no accounts. Only share links are server-side.
- Export downloads `.tex` or PDF via the `/api/export/*` endpoints; API base URL comes from `NEXT_PUBLIC_API_URL`.

---

## 6. Testing and CI

- **93 pytest tests**: 55 in `test_inference.py`, 6 `test_export.py`, 4 `test_pdf_preprocessor.py`, 3 `test_share.py`, 6 `test_engine_selection.py`, 19 `test_text_normalize.py`.
- Pix2Text is faked in tests (`_p2t` global), so the suite runs without model weights or GPU. PaddleOCR and Groq are never invoked in CI. PDF preprocessing tests use the real Poppler binaries; PDF export compile is not exercised in CI (needs Tectonic) — the retry logic is tested by mocking `_run_tectonic`.
- GitHub Actions (`.github/workflows/api-tests.yml`): on push/PR to `main`, Ubuntu, Python 3.11, installs Poppler + libgl1/libglib2.0, `pip install -r api/requirements.txt`, runs `pytest` in `api/`. Python version intentionally matches Modal's.

---

## 7. Evaluation

`api/eval/accuracy_benchmark.py` runs the production pipeline on `eval/test_set/` and reports:
- **Character accuracy** = `1 − Levenshtein(pred, truth) / len(truth)`, clamped to ≥ 0.
- **Word-overlap recall** = fraction of ground-truth words (as a multiset, case/punctuation-insensitive) present anywhere in the prediction — order-insensitive, so it separates "wrong content" from "right content, wrong order".
- Each image runs in its **own subprocess** (`_recognize_one.py`) because running many images in one process caused a silent native crash. Subprocess output goes to temp files (not pipes) to avoid a Windows pipe-handle deadlock; temp-dir cleanup ignores errors so a locked file can't crash the run.

**Results**

| Run | Test set | Char accuracy | Word recall |
|---|---|---|---|
| Earlier | 16 images of the author's own (cursive) notes | 33.0% | 50.5% |
| Current (`results.json`) | 7 of 14 images, legible external notes | **71.4%** | **73.0%** |

Per-image (current): statistics 91.8, discrete-math sets 82.9, compiler design 75.3, operating systems 71.7, DSA intro 66.9, discrete-math logic 58.0, algorithms 53.5. The 80% target (NFR-010) is not met. The 14-image set came from openly shared GitHub notes (sources in `eval/test_set/README.md`); 7 images were run manually via `_recognize_one.py` and 7 remain unscored.

### 7.1 OCR engine selector experiment

**Question.** MathScan runs Pix2Text and PaddleOCR on every page and keeps the result with the higher confidence. I tested whether a small trained model that chooses between the two outputs would beat that rule.

**Result.** No. I did not build the selector, because the measured upper bound on its gain is too small to justify it.

**How it was tested**
- **Metric.** Character error rate (CER) = edit distance / length of the ground truth. Reported pooled (total edits over total characters) and as a mean per item.
- **Oracle.** Picks the better of the two engines per item using the true answer. No selector can beat it, so it is the ceiling on what a selector could gain.
- **Page level.** 14 external handwriting pages.
- **Crop level.** 79 hand-labelled line crops cut from 11 pages of the author's own calculus notes.
- **Normalizer.** Pix2Text outputs LaTeX, Paddle outputs plain text, and the labels are plain math. Comparing raw strings would favour whichever engine shares the label notation, so everything goes through `canonical_text` first. It strips `\[ \]`, turns `\frac{1}{2}` into `1/2`, drops spacing and case, and treats brackets as equal. It is lossy on purpose and is for comparison only.

**Page level (14 pages)**

| Strategy | Pooled CER | Mean page CER |
|---|---|---|
| Pix2Text only | 0.892 | 0.914 |
| PaddleOCR only | 0.265 | 0.272 |
| Current rule | 0.265 | 0.272 |
| Oracle | 0.265 | 0.272 |

Paddle won every page, so the rule already equals the oracle there.

**Crop level (79 crops, 11 pages)**

| Strategy | Pooled CER | Mean CER | Exact match |
|---|---|---|---|
| Pix2Text only | 0.786 | 0.740 | 5.1% |
| PaddleOCR only | 0.656 | 0.642 | 1.3% |
| Current rule | 0.618 | 0.567 | 2.5% |
| Oracle | 0.578 | 0.489 | 6.3% |

The rule and the oracle pick different engines on 16 of 79 crops. Paddle is the better engine on 45 crops, Pix2Text on 33, and 1 is a tie.

**What this shows**
1. The existing rule is a good design. On handwritten math crops it beats either engine used alone.
2. A perfect selector would gain only 0.04 pooled CER over the rule (0.618 to 0.578). That is too small to prove with 79 crops from one writer.
3. The bottleneck is recognition, not selection. Even the oracle has a CER of 0.578 and only 6.3% exact matches.

**Code added**
- `api/engine_selection.py`: the selection rule, pulled out of `inference.py` into a pure function (`pick_page_result`). Behaviour unchanged. A future selector would plug in here, with the old rule as the fallback.
- `api/text_normalize.py`: the shared comparison form described above.
- `api/eval/_collect_one.py`, `collect_batch.py`: collect both engines' outputs, one process per batch of 8 (long-lived processes crashed natively before). Resumable, skips finished images.
- `api/eval/score_baselines.py` (page level), `score_crops.py` (crop level): compute the baselines and the oracle.
- `api/eval/make_label_kit.py`, `label_server.py`, `label_tool.html`: cut pages into ink-band crops, plus a local labelling tool with autosave, atomic writes and a backup.
- Tests: `test_engine_selection.py` (6 cases) and `test_text_normalize.py` (19 cases).

**Design choices**
- Measure before building: the oracle gap was checked first, so no model was trained for a 0.04 ceiling.
- Page-level labels are plain text and favour Paddle, which is why the normalizer and the crop-level test on handwritten math were added.
- Pages stand in for the writer, so any future split keeps crops from the same page together.

**Limitations**
- 79 crops from 11 pages of a single writer.
- The Groq correction pass is not included in these numbers.
- The crop-level rule uses crop confidences, while production applies it to whole pages.
- The normalizer is lossy, so absolute CER values are approximate. Comparisons between strategies use the same normalizer, so they are fair.
- Production latency on Modal was not measured.

**Next (not started).** Fine-tune the recognizer on a public handwritten-math dataset, using the 79 crops as a held-out test set. The number to beat is 0.618 pooled CER.

---

## 8. Notable bugs found and fixed

1. **Confidently wrong OCR** — Pix2Text scored ~85% on hallucinated garbage, so a confidence-gated fallback never fired. Fix: always run PaddleOCR and keep the higher-confidence result. A spellchecker "gibberish detector" was tried first and rejected after calibration showed it scored backwards.
2. **Reading order silently broken** — relied on `line_number`, which was `None` in production; output stayed one word per line. Fix: bbox-geometry line grouping.
3. **Hallucination guard evolved four times** (see §3.4), each revision driven by a real failure.
4. **PDF export failed entirely on one bad region** — fixed with comment-out-and-retry.
5. **Deployment issues** — missing shared libraries for OpenCV and Tectonic, glibc mismatch, CUDA provider crash, PaddleX connectivity-check hang during image build.
6. **Benchmark isolation and Windows hangs** — per-image subprocesses (native crash), parallel workers contending for CPU, pipe-handle deadlock, and cleanup `PermissionError`.
7. **CI failure** — `test_share.py` round-trip test needed the new `region_count` / `low_confidence_count` fields.

---

## 9. Known limitations and things worth checking

- **Accuracy ceiling**: free-tier models only; messy/cursive handwriting scores far lower than clean print-style writing, and set-theory/rare notation is weak.
- **Latency**: on a CPU-only Windows laptop some pages took over a minute per Pix2Text pass (one page 4+ minutes), and PaddleOCR now runs on every request. Modal's function `timeout=120` may be tight for such pages — I haven't measured Modal's actual per-page latency, so treat this as a risk to verify, not a confirmed problem.
- **Groq `max_tokens=512`**: a long page's corrected text could be truncated; the guards would likely reject a truncated response, which means long pages might silently skip correction. Not verified.
- Sample size is below the 30–50 image target, so the headline accuracy is not a fully defensible estimate.
- `/api/ocr/test` skips the PaddleOCR fallback, so it under-represents production quality.
- Share links have no expiry or auth (8-hex-char IDs); history is per-browser only.
