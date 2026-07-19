# MathScan Code Walkthrough

Everything in the repo as of the last commit, file by file — what it does, why it's built that way, and the real bugs that shaped it. Written so you can read this once before M4 and actually understand every piece you're about to deploy, not just copy commands.

**How to read this:** backend first (it's the part doing real work), then frontend, then the test files, then a recap of the bugs that actually happened during this build — those bugs are where most of the "why" in this codebase comes from.

---

## The big picture: one request, start to finish

Before the file-by-file detail, the shape of a single conversion, so every file below has a place to slot into:

1. Browser: user drops files onto `UploadDropzone.tsx` → validated client-side (`lib/validateFiles.ts`) → handed up to `UploadFlow.tsx`.
2. `UploadFlow.tsx` builds a `FormData` upload and POSTs it to either `/api/ocr/pdf` or `/api/ocr/images` (FastAPI, `routers/ocr.py`).
3. Backend saves the upload(s) to disk, then either splits a PDF into per-page JPEGs (`pdf_preprocessor.py`) or uses the images directly.
4. Each page image goes through `inference.py`'s `recognize_page()` — the actual Pix2Text call — and the result streams back to the browser one page at a time over Server-Sent Events (SSE).
5. `UploadFlow.tsx` reads that stream and renders each page's regions as an `EditableLatexRegion.tsx` (textarea + live `LatexPreview.tsx`).
6. When the user clicks export, `UploadFlow.tsx` packages the (possibly edited) LaTeX and POSTs it to `/api/export/tex` or `/api/export/pdf` (`routers/export.py`), which either returns the raw `.tex` text or actually compiles a PDF via the `tectonic` binary.

Two engines are doing the real work here: **Pix2Text** (reads handwriting → LaTeX) and **Tectonic** (LaTeX → PDF). Everything else is plumbing around those two.

---

## Backend (`api/`)

### `main.py` — the entrypoint

Creates the FastAPI `app` and wires in the two routers (`ocr_router`, `export_router`). The one piece of real logic here is `lifespan()`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    inference.load_model()
    yield
```

Pix2Text takes about 30 seconds to load its weights into memory. `lifespan` runs this **once**, when the server process starts — not on every request. Every request after that reuses the same already-loaded model sitting in `inference._p2t`. `/api/health` exposes `model_loaded` specifically so a load balancer or deploy script can wait for that flag before sending real traffic — otherwise the first few users would hit a half-initialized server.

This assumption — "the process stays alive and the model stays loaded" — is exactly what breaks if you deploy to a scale-to-zero serverless platform (discussed in the cost analysis file, section 3.1): a fresh cold start means paying that 30-second load cost again. This turned out to matter for real once M4 actually shipped: Modal's `scaledown_window=300` keeps a container warm for 5 minutes after its last request specifically so a burst of activity doesn't re-pay this cost on every single request, only after a real idle gap.

**CORS** (`CORSMiddleware`, added during M4): through M3 the frontend fetched relative paths like `/api/ocr/pdf`, which only works when browser and backend share an origin. Once the backend moved to its own Modal URL and the frontend to its own Vercel URL, the browser started blocking those `fetch()` calls by default — CORS is what tells the browser "this specific origin is allowed to call this API." `ALLOWED_ORIGIN` is read from an env var (defaulting to the real deployed frontend, `https://math-scan-lake.vercel.app`) rather than hardcoded, because the exact frontend URL wasn't known until after Vercel actually deployed it — and it started as `"*"` (allow anything) for the first deploy, tightened to the real domain only once that URL was confirmed working end-to-end.

`/api/ocr/test` is a leftover from M0 — a single-image, no-SSE endpoint kept around purely because it's the fastest way to manually check "is the model actually working" via a plain curl command, without dealing with multipart-multi-file-plus-streaming plumbing.

### `inference.py` — the actual OCR call

The thin wrapper around Pix2Text — now, as of M6, a small pipeline: Pix2Text runs first, and three optional post-processing stages can kick in after it.

**`recognize_page(image, apply_contrast=False, resized_shape=768, try_fallback=False, fallback_threshold=0.70)`** — calls `_p2t.recognize_text_formula(image, return_text=False, resized_shape=768)` and reshapes the result into `{"regions": [...], "confidence_mean": ...}`.

This function's docstring documents a real debugging path worth knowing, because it explains a design decision that looks arbitrary otherwise: Pix2Text has *two* different entry points. The "full-page" API (`Pix2Text.__call__`) runs a document-layout detector first — decides whether each region is a paragraph, title, table, or figure — and only OCRs what it labels text/title/table. On real test photos, that layout detector classified genuine handwritten equations as "figure" and returned nothing, even though the math was perfectly legible. Switching to `recognize_text_formula()` — which skips layout detection entirely and just says "this image may contain text and formulas" — fixed it immediately (99.99% confidence on the same photo that returned empty before). The trade-off, stated in the docstring: you lose automatic separation of multiple distinct math regions scattered across one page (a "Should," not a "Must," per the SRS) in exchange for OCR that actually works at all.

**`enhance_contrast(image)`** — `ImageOps.autocontrast(image, cutoff=1)`. Stretches a photo's actual histogram (darkest pixel → black, lightest → white) rather than applying a fixed brightness multiplier, so it adapts to whatever the user uploaded instead of needing manual tuning. `cutoff=1` clips the extreme 1% of pixels first, so a stray shadow or glare spot doesn't throw off the whole stretch. Only runs when `apply_contrast=True` is passed through from the frontend's opt-in checkbox — never silently applied.

### M6: the confidence-fallback pipeline (`inference.py`, continued)

This section exists because of a real accuracy problem, found not by guessing but by running the app against real handwritten course notes (econ, calculus, poli-sci): Pix2Text is a *math*-specialized OCR model, and on pages that are mostly dense cursive prose (paragraphs of notes, not equations) its confidence regularly fell under 40%, and the text it produced was often wrong or nonsense. The fix is not "replace Pix2Text" — it's still the best engine for actual math — but "add a second, general-purpose OCR engine that only gets used when Pix2Text is struggling, and only keep its answer if it's demonstrably better."

**`_recognize_page_paddleocr(image)`** — lazily loads a global `PaddleOCR(use_textline_orientation=True, lang="en")` instance (`_get_paddle_reader()`, loaded once and reused, same pattern as Pix2Text's own lazy singleton) and calls its `.predict()` method. PaddleOCR is a general-purpose OCR engine, not math-aware — it doesn't know LaTeX — but it's noticeably stronger than Pix2Text specifically on ordinary handwritten prose, which is exactly the case where Pix2Text was failing. Every region it returns is run through `_fix_missing_spaces()` then `_fix_typos()` before being handed back (below), and is always labeled `"type": "text"` since PaddleOCR has no concept of math regions.

**`recognize_page`'s fallback logic**: after getting Pix2Text's result, if `try_fallback=True` *and* the page's mean confidence is `None` or below `fallback_threshold` (0.70), it also runs `_recognize_page_paddleocr()` and keeps whichever result has the higher mean confidence — Pix2Text's original answer if it still wins, PaddleOCR's if it scores higher. This is deliberately **evidence-based, not "always trust the fallback once triggered"** — `test_recognize_page_keeps_pix2text_when_fallback_scores_lower` in the test suite exists specifically to prove a below-threshold Pix2Text result can still beat a bad PaddleOCR result. `try_fallback` defaults to `False` so nothing calls real PaddleOCR unless explicitly asked to — CI never installs the ~100MB+ PaddleOCR/PaddlePaddle packages (see `requirements-fallback.txt` below), so every test that exercises `recognize_page` without opting in stays fast and mock-only. The one place `try_fallback=True` is actually passed is `routers/ocr.py`'s two live endpoints — real user uploads always get the fallback attempt; the test suite never does.

**`_fix_missing_spaces(text)`** — uses `wordninja` (word-frequency-based text segmentation) to fix a real, observed PaddleOCR failure mode: an entire line of text coming back as one long space-less run of characters (e.g. `"mantofgchonindvidvais illigadale tobingaee"` for what should have been several separate words). Only runs `wordninja.split()` on chunks at least 20 characters long with no internal space — short, already-correctly-spaced words are left alone, since `wordninja` has no way to know a token was already fine and running it indiscriminately risks mangling real short words it doesn't recognize. The 20-character check is applied per space-separated chunk, not to the whole string — an earlier version checked "does this string contain a space *anywhere*" and wrongly skipped genuinely merged chunks just because some *other* part of the same region happened to already have a space in it; this is a real caught bug, see the recap below.

**`_fix_typos(text)`** — uses `pyspellchecker` (edit-distance + word-frequency spell correction) to fix individual misread words, word by word. Two things make this more than "just call a spellchecker":
1. Each word is split into `(prefix, core, suffix)` via regex so punctuation (`Demand:` → `Demand` + `:`) doesn't get treated as part of the word, and the original capitalization pattern (all-caps, first-letter-caps, or lowercase) is reapplied to whatever correction comes back, since pyspellchecker's corrections are always lowercase internally.
2. `_get_spellchecker()` boosts a small hand-picked `_DOMAIN_VOCABULARY` list (lecture, syllabus, equilibrium, elasticity, derivative, integral, and similar academic terms) into the dictionary's word-frequency table at a very high weight (`load_words([word] * 500000)`). This exists because the *default* general-English dictionary actively works against domain text: tested directly, `spell.correction("echure")` returns `"secure"` by default (a common English word, close edit distance) instead of the intended `"lecture"` — boosting `"lecture"`'s frequency is what flips that specific, verified case to the correct answer.

Also fenced off entirely: words shorter than 3 characters, and any word that isn't purely alphabetic — a math fragment like `"1+x^2"` or `"x2"` should never be run through an English dictionary at all, since it isn't English.

**`_call_groq(prompt)`** (M6.5, added after the "free-tooling ceiling reached" conclusion earlier in this doc) — the shared low-level network call to Groq's free-tier API (`llama-3.1-8b-instant`, OpenAI-compatible REST endpoint). A complete no-op — returns `None`, no network call attempted — whenever `GROQ_API_KEY` isn't set, and swallows any failure (timeout, rate limit, malformed response) the same way rather than ever raising, so a flaky free-tier API can never break a real conversion. `modal_app.py` wires the key in as a Modal Secret (`modal.Secret.from_name("groq-api-key")`), never baked into the image; CI never sets it, so every test either explicitly mocks `httpx.post` or confirms the true-no-op path.

**`_llm_correct_text(text)` / `_llm_correct_latex(latex)`** — the original, per-region versions of Groq correction (prose and math respectively). **No longer called automatically** as of a later revision in the same M6.5 work (see `_correct_full_page` below) — kept as standalone, still-correct, still-tested functions, since the safety guards they use are shared with the page-level path. The reason they were replaced is itself a real finding worth knowing: a per-region call has *no visibility into the rest of the page*. Given just the isolated line "look for more support that is in line with your hypothesis" with one word OCR'd wrong, the model has no way to know the notes are about developing and testing a hypothesis — it has to guess at a plausible-sounding replacement blind. A live test caught it guessing wrong twice this way (a garbled word "corrected" into a fluent but unrelated word) — real, observed, and the direct motivation for the redesign below.

**Page-level correction (`_combine_regions_to_page`, `_correct_full_page`, `_collapse_to_page_result`)** — the current, live design. Every region on a page (regardless of type — Pix2Text math, PaddleOCR prose, or a mix) gets combined into ONE string first (`_combine_regions_to_page`, using `_format_region_for_page`/`_escape_latex_text` — a Python port of the wrapping/escaping rule `UploadFlow.tsx`'s `formatRegionForExport` used to apply per-region, now done server-side instead), then sent to Groq **once per page, always** (not confidence-gated — even a page Pix2Text/PaddleOCR are individually confident about can still benefit from whole-page context a per-region score can't capture) via `_correct_full_page`. The result replaces the page's entire region list with a single `"page"`-typed region (`_collapse_to_page_result`), carrying the page's overall confidence.

`_correct_full_page` applies every safety guard proven necessary by real testing, all inherited/reused, none reinvented: `_looks_like_llm_refusal` (the conversational-refusal problem), `_looks_like_repetition_garbage` (the padded-garbage-math problem), and a new page-scoped version of the hallucination check, `_page_correction_changed_too_much`. That last one is worth understanding precisely: a whole page needs *more* tolerance for word-count drift than a single line (legitimate multi-word merges/splits happen across a long page), so it allows up to a 20% word-count change — but it still catches a single bad substitution buried anywhere in an otherwise-untouched page, because it reuses the exact same per-word similarity check the line-level guard uses, applied via `difflib`'s word-level diff opcodes across the whole page. This was verified directly, not assumed: a synthetic full-page string with the real "scientific" → "concise toxic" substitution buried in the middle of ~90 words of otherwise-unchanged text still got caught, even though the *aggregate* page-level similarity would have been high enough to miss it.

WHY the frontend needed almost no changes for this: `UploadFlow.tsx` already renders `page.result.regions.map(...)` — a list with exactly one item naturally displays as one confidence badge + one editable block per page, with zero changes to the rendering loop itself. Two real changes were needed: `formatRegionForExport` gained a `type === "page"` branch (pass the already-formatted string through as-is, re-checking brace balance across the whole page but not re-wrapping/re-escaping it), and `LatexPreview.tsx` gained mixed-content rendering (`renderMixedContent`, splitting a string on `\[ \]`/`$ $` delimiters and rendering math segments through KaTeX while escaping and preserving line breaks in plain-text segments) — needed because a "page" region can now genuinely contain both real prose and embedded math in the same string, which a single `katex.renderToString()` call was never built to handle.

**What this does *not* fix, documented honestly rather than hidden**: `_fix_typos("Choper")` does not become `"Chapter"` — `"chapter"` isn't within pyspellchecker's edit-distance-2 candidate set for `"choper"` at all, a structural limit no amount of domain-vocabulary weighting can work around (`test_fix_typos_has_a_real_known_limit_not_fixed_by_domain_boost` asserts this stays broken on purpose, so a future change doesn't accidentally "fix" the test while masking the real limitation). Several other approaches were tried and empirically rejected before landing on this one: `symspellpy`'s context-aware bigram correction (`lookup_compound`) didn't help, because general English bigram frequency data doesn't favor academic vocabulary either; gating corrections by a confidence ratio didn't cleanly separate good fixes from bad ones — some correct fixes had *low* confidence ratios and the one bad fix (`echure`→`secure`) had a ratio in the same range as good ones; `language_tool_python` grammar checking was rejected outright (needs Java 17+, an unreliable rate-limited public API, disproportionate deploy risk for a narrow benefit); OpenCV adaptive thresholding as an image-preprocessing alternative was tested against real pages and made results *worse* on both OCR engines, including blanking one page's detections entirely. All of these are real, tested negative results, not untried ideas — the conclusion reached is that the free-tooling accuracy ceiling for this specific hard case (dense cursive prose) has genuinely been reached; a paid model fallback (Claude or similar) is the only further lever, deliberately deferred for cost reasons (see "What's not built yet" below).

### `pdf_preprocessor.py` — PDF → one JPEG per page

Two functions: `get_page_count()` (calls Poppler's `pdfinfo`, raises `EncryptedPDFError` if it can't read the file — encrypted and corrupt PDFs fail the same way, and from the API's perspective both just mean "can't process this," so they're collapsed into one error type) and `split_pages()`, a **generator** that yields one page at a time.

Why a generator and not "convert the whole PDF to a list of images and return it": a 50-page PDF at 200 DPI can be 20-30MB per page as raw pixels. Rendering all 50 pages into memory up front risks running out of memory on a small server. `convert_from_path(..., first_page=n, last_page=n)` renders exactly one page per call — a little re-invocation overhead, in exchange for memory usage that never exceeds one page's worth, no matter how long the PDF is. This also lets the SSE stream in `routers/ocr.py` push progress to the browser after each page finishes, instead of waiting for the whole PDF to render before sending anything.

### `routers/ocr.py` — the SSE endpoints

Two endpoints, `/api/ocr/pdf` and `/api/ocr/images`, both built around the same shape: save upload(s) to disk → build a list of `(page_number, image_path)` → stream results one page at a time.

A few details worth understanding, not just skimming:

- **`_sse()` helper** — SSE's wire format is `data: <json>\n\n`. The double newline is the actual protocol requirement (it's how the browser knows one message ended), not a stylistic choice.
- **`asyncio.to_thread(recognize_page, image, enhance_contrast)`** — `recognize_page` is synchronous, CPU/GPU-bound code. If it were `await`ed directly, it would block FastAPI's entire event loop — meaning `/api/health` and every other in-flight request would freeze until that one page finished. `to_thread` hands the blocking call to a worker thread so the loop keeps serving other requests while inference runs.
- **Files are read and saved to disk *before* the `StreamingResponse` generator starts, in both endpoints.** This fixed a real bug: a `StreamingResponse`'s generator function doesn't actually start executing until *after* the endpoint function itself returns — that's what makes it a stream instead of a normal response. But by the time the endpoint returns, FastAPI has already closed the `UploadFile` objects. Trying to `await f.read()` *inside* the generator failed with "I/O operation on closed file." Saving everything to plain disk files up front, before returning the `StreamingResponse`, means the generator only ever touches files we already own — never the original closed `UploadFile`.
- **`MAX_PAGES`, `MAX_PDF_MB`, `MAX_IMAGE_MB`** — size/count caps added during the later robustness pass. Rejecting an oversized request cheaply (400 error, no wasted inference time) rather than letting it OOM or hang.
- **`finally: shutil.rmtree(job_dir, ...)`** — deletes uploaded content immediately after inference, whether the request succeeded, failed, or the client disconnected mid-stream (which is exactly what happens when the frontend's Cancel button calls `abort()` — the generator's `finally` still runs and cleans up).

### `routers/export.py` — `.tex` and PDF export

`build_tex(pages)` wraps a list of per-page LaTeX blobs in a minimal preamble (`amsmath`, `amssymb`, `amsfonts` — without these, most real student math wouldn't compile: no `\frac`, no `\in`/`\notin`/`\subseteq`) and `\section{Page N}` markers per FR-030.

`/api/export/pdf` is the more interesting endpoint. It writes the `.tex` source to a real file inside a `tempfile.TemporaryDirectory()` (Tectonic needs actual files on disk — it can't compile a string in memory), then shells out to the `tectonic` binary via `subprocess.run(["tectonic", "-X", "compile", ...])`. Tectonic was chosen over a full TeX Live install because it's one self-contained binary that fetches only the packages a document actually needs, instead of a multi-gigabyte install of everything.

The error-handling block here is worth reading closely — it's the result of a real multi-round debugging session:

```python
match = re.search(r"document\.tex:(\d+):", result.stderr)
```

Tectonic's own error output says *that* line N is broken, but not what's actually on it — and by the time you're reading the error, the temp directory (and the broken `.tex` file inside it) has already been deleted. This line pulls the matching line back out of `tex_source`, which is still sitting in memory, and shows a few lines of context around it. That's what turns "compilation failed" into "here's the exact broken text, marked with `>>>`" — the difference between a vague crash and something you can actually fix.

### The tests (`api/tests/`)

**`test_inference.py`** fakes `inference._p2t` with a `FakeP2T` class matching Pix2Text's *real* return shape (list of dicts with `text`/`type`/`score`/`position`, where `position` has `.tolist()`) — this shape was verified against the actual library, not guessed, which is exactly what caught the earlier full-page-API bug. Also tests `enhance_contrast()` directly (does it actually widen a narrow pixel histogram) and confirms `recognize_page()` only applies it when `apply_contrast=True`.

**`test_pdf_preprocessor.py`** generates *real* PDF files at test time using PyMuPDF (`fitz`), including a genuinely AES-256-encrypted one, and runs them through the real Poppler pipeline. This is a deliberate choice: `pdf_preprocessor.py`'s entire job is "call Poppler correctly," so a test that mocks Poppler wouldn't prove the real thing works — only that the mock does what you told it to.

**`test_export.py`** tests `build_tex()` directly, plus the `/api/export/tex` endpoint via a `TestClient` mounted on a **fresh, minimal `FastAPI()`** app containing only the export router — not the real `app` from `main.py`, which would trigger the ~30-second model-loading `lifespan` on every test run. `/api/export/pdf` isn't tested this way on purpose: it requires a real Tectonic binary, which makes it an integration concern, not something every test run should depend on.

### `requirements.txt`

Pinned versions for `fastapi`, `uvicorn`, `pix2text`, `Pillow`, `pdf2image`, `pytest`, `httpx`, and `PyMuPDF` (added explicitly once tests started using it directly, rather than relying on it being pulled in indirectly by `pix2text`).

### `modal_app.py` — deploying the backend to Modal (M4)

A separate file from `main.py`, on purpose: `main.py` stays a plain, Modal-agnostic FastAPI app so `uvicorn main:app` still works for local dev exactly as documented above, and so `test_export.py`'s `TestClient` pattern never needs to know Modal exists. `modal_app.py` wraps that same app for Modal without changing what it is.

Modal was chosen over RunPod or a Hetzner VM specifically because it can host a real ASGI app — this exact FastAPI app, SSE streaming included — almost unchanged, via `@modal.asgi_app()`. RunPod Serverless is built around a single input/output "job handler" function, which doesn't naturally fit an app with multiple REST routes and a streaming response; porting to it would mean re-architecting `routers/ocr.py`'s SSE endpoints, not just deploying them.

The pieces worth understanding:

- **`image = modal.Image.debian_slim(...)` chain** — builds the container step by step: `apt_install` for system libraries, `pip_install_from_requirements` for Python packages, `run_function(_download_model_weights)` to bake Pix2Text's model weights into the image at *build* time (so a cold start only pays the ~30s in-memory load, never a weights re-download over the network), then `run_commands(...)` to install the `tectonic` binary (no apt package for it), then `add_local_dir("api", ...)` to actually copy the application code in.
- **`ignore=[".venv", "**/__pycache__", "*.pyc", "tests"]`** on `add_local_dir` — without this, a real deploy uploaded 4,214 files because it swept up the entire local Python virtual environment sitting inside `api/`. Nothing in the container ever imports from that folder (dependencies come from `pip_install_from_requirements`), so it's pure dead weight.
- **No `gpu=` parameter on `@app.function(...)`** — the original plan was a T4 GPU, but a real deploy crashed on startup: Pix2Text's ONNX-based `LatexOCR` component detected a GPU and tried `CUDAExecutionProvider`, which needs the `onnxruntime-gpu` package instead of the plain CPU-only `onnxruntime` actually installed. Running with no GPU at all matches the exact configuration that already worked locally (no GPU on the dev machine either) — slower per page than a working GPU setup would be, but known-working today. Worth revisiting in M6 if CPU latency becomes a real problem once there's actual usage to measure.
- **`scaledown_window=300`** — keep a warm container for 5 minutes after its last request, so a burst of back-to-back conversions doesn't re-pay the model-load cost on every single one. Scales fully to $0 after 5 idle minutes with nobody using it — the whole point of going serverless instead of a VM (cost analysis section 3.1).
- **Tectonic's shared libraries** — its prebuilt binary isn't statically linked against everything it needs. Two separate real crashes traced this exactly: first `libGL.so.1: cannot open shared object file` (actually an OpenCV dependency pulled in by Pix2Text's layout module, fixed with `libgl1` + `libglib2.0-0`), then `libgraphite2.so.3: cannot open shared object file` (Tectonic's own text-shaping dependencies, fixed with `libgraphite2-3`, `libharfbuzz0b`, `libicu-dev`).
- **Tectonic's GLIBC mismatch** — even after the shared-library fix above, a live PDF export crashed with `libc.so.6: version 'GLIBC_2.38' not found`. The official install script (`drop-sh.fullyjustified.net`) fetches Tectonic's GNU-target build, which is dynamically linked against a newer glibc than Modal's `debian_slim` base actually ships. Fixed by downloading Tectonic's **musl-target** release asset directly instead — a build Tectonic publishes specifically so the binary doesn't depend on the host's glibc version at all, sidestepping the mismatch entirely rather than chasing a matching base-image version.

### `share_store.py` + `routers/share.py` — share links (M5)

M5's history feature (below) was explicitly built as **anonymous, browser-only** — no accounts, no login, no server-side database of who converted what. That decision has exactly one hole: someone else, on a different device, can never see *your* result, because everything lives in your browser's `localStorage`. Share links are the narrow, purpose-built exception to that rule, not a first step toward a real accounts system.

`share_store.py` tries `modal.Dict.from_name("mathscan-shares", create_if_missing=True)` first, falling back to a plain in-memory `{}` if the `modal` import fails for any reason (no package, not logged in, running locally without Modal configured). The `modal.Dict` choice matters specifically because Modal containers are ephemeral and more than one can run at once (`@modal.concurrent(max_inputs=4)` in `modal_app.py`, plus fresh cold-starts under load) — data saved in one container's memory or local disk is invisible to a *different* container that later handles the `GET` request for the same link. `modal.Dict` is Modal's own distributed key-value store, built for exactly this "small piece of state that has to survive across containers and cold starts" case.

`routers/share.py` exposes two endpoints: `POST /api/share` (accepts the full per-page region data — latex, type, confidence for every region, not the flattened export text, so a share link looks like the real interactive app) generates an ID with `secrets.token_hex(4)` and saves it; `GET /api/share/{share_id}` returns the saved data or a 404. `secrets.token_hex` (not `random`) is a deliberate choice: `secrets` draws from a cryptographically secure random source specifically so a share ID can't be predicted or enumerated by incrementing a counter — `random` "looks" random too but isn't safe for anything where guessability matters.

### `requirements-fallback.txt` — the PaddleOCR dependency split (M6)

A second requirements file, separate from `requirements.txt`, holding just `paddlepaddle==3.2.2` and `paddleocr==3.7.0`. Two reasons this is split out rather than merged in: size (these two packages plus their model weights add up to several hundred MB, versus the featherweight `wordninja`/`pyspellchecker` that *did* go in the main file), and CI never needs real PaddleOCR at all — `test_inference.py` mocks `_recognize_page_paddleocr` via `monkeypatch`, and `try_fallback` defaults to `False` everywhere the tests call `recognize_page`. `modal_app.py`'s image installs *both* files, since the live deployed app genuinely does need the real fallback.

The version pin (`paddlepaddle==3.2.2`, not latest) isn't arbitrary — `paddlepaddle` 3.3.x has a confirmed regression breaking CPU inference (`NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support`, a PIR/oneDNN bug tracked upstream), while 3.2.2 is the exact version that was tested and confirmed working against real handwriting in this project (85% confidence, correct output on a real page).

### `.github/workflows/api-tests.yml` — continuous integration

Runs the `api/tests/` suite automatically on every push and pull request to `main`, on `ubuntu-latest`, Python 3.11 (matched deliberately to `modal_app.py`'s `python_version="3.11"`, so CI and the deployed container never quietly drift apart). Installs `poppler-utils`, `libgl1`, `libglib2.0-0` via `apt-get` before running `pytest` — the same two OpenCV-related libraries that `modal_app.py`'s image needs, and for the identical reason: `import inference` pulls in `pix2text` → `opencv-python`, which fails at import time on a bare Ubuntu runner without them. Deliberately installs only `requirements.txt` (never `requirements-fallback.txt`) and never exercises `/api/export/pdf` — both match the existing test suite's own documented scope, not new decisions invented for CI.

### `eval/claude_vs_pix2text.py` — the accuracy-comparison tool

A standalone script, **not** part of CI or the deployed app — run manually, locally, with your own virtual environment (and optionally your own Anthropic API key). Built specifically to answer "is this change actually helping?" with real evidence instead of eyeballing one screenshot. Renders real pages from a real PDF via `pdf2image.convert_from_path(..., first_page=1, last_page=args.pages)` (fixed from an early bug that rendered the *entire* PDF before slicing, which made the script appear to hang on a large file), then runs each page through whichever engines are enabled via CLI flags: Pix2Text always, `--try-easyocr` for EasyOCR (a third OCR engine tested as an ensemble candidate, found weaker than PaddleOCR overall), `--try-paddleocr` for PaddleOCR, `--adaptive-threshold` for the OpenCV preprocessing experiment (documented above as a tested, rejected idea), and `--model`/`--no-claude` for the optional paid Claude comparison (never run by default, since it costs real money per page — `COST_LOW_PER_PAGE`/`COST_HIGH_PER_PAGE` constants at the top of the file document the actual Haiku/Opus per-page cost estimates from the cost-analysis doc). This is the tool that produced every real confidence number and side-by-side comparison referenced elsewhere in this document — nothing about "PaddleOCR is better on prose" or "adaptive thresholding hurts" was asserted without running this script against real handwriting first.

### `eval/accuracy_benchmark.py` + `eval/test_set/` — the formal accuracy number

`claude_vs_pix2text.py` above answers "does this change seem to help" qualitatively; this script answers the SRS's NFR-010 requirement directly, with one real, repeatable number: character-level accuracy against a fixed, labeled test set.

`levenshtein_distance()` is a plain ~15-line dynamic-programming edit-distance implementation (no new pip dependency — well-known enough not to need one), and `character_accuracy()` turns that into `1 - (edit_distance / len(ground_truth))`, clamped to `[0, 1]`. WHY edit distance rather than exact string match: two transcriptions of the same handwriting can differ in whitespace or line-break placement while both being substantively correct — exact match would score that as entirely wrong, which isn't what "character-level accuracy" is supposed to measure.

The script runs every image in `eval/test_set/images/` through `inference.recognize_page(..., try_fallback=True)` by default — the SAME call `routers/ocr.py`'s live endpoints make, not a simplified stand-in — compares the result against the matching entry in `eval/test_set/ground_truth.json`, and writes per-image plus aggregate results to `eval/test_set/results.json`, checked against the SRS's ≥80% target.

`eval/test_set/` is seeded with 3 real pages (poli-sci prose, a calculus integral-convergence page, an econ elasticity tutorial), pulled directly from the actual course PDFs uploaded earlier in this project and hand-transcribed against the real images — not synthetic examples. `eval/test_set/README.md` documents exactly how to add more; the honest, stated goal (per `ROADMAP.md`) is 30-50 images before the resulting percentage is a genuinely defensible sample size to put in a resume line, not the 3 seeded so far.

### `web/lib/apiBase.ts` — pointing the frontend at the deployed backend

```typescript
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}
```

Every `fetch()` call in `UploadFlow.tsx` goes through `apiUrl(...)` instead of a hardcoded relative path. The `NEXT_PUBLIC_` prefix isn't optional styling — Next.js only inlines env vars with that exact prefix into the browser bundle at build time; without it, the value would only exist on the server and every client-side fetch would silently get `undefined`. Left unset, `API_BASE` falls back to `""`, which reproduces the old relative-path behavior from M0-M3 — so local dev without a `.env.local` still works.

---

## Frontend (`web/`)

### `app/layout.tsx` and `app/page.tsx`

`layout.tsx` loads the Inter font via `next/font/google` (downloaded and self-hosted at build time — no runtime request to Google's servers) and imports KaTeX's CSS globally (without it, formulas render as unstyled plain text instead of properly typeset math).

`page.tsx` has **no `"use client"` directive** — it's a Server Component, meaning it renders once on the server as plain HTML and ships less JavaScript to the browser. Only `UploadFlow` (and what it contains) needs real interactivity, so only that subtree opts into being a client component.

### `app/globals.css` + `tailwind.config.ts`

Minimal global CSS (just body color/background) since Tailwind utility classes handle everything else directly in JSX. The Tailwind config defines one custom color family, `accent` (deep teal, `#0F766E`), used deliberately sparingly — buttons, links, focus rings — so it reads as an intentional choice rather than one color lost among several.

### `components/UploadDropzone.tsx`

A **controlled** component: `files` and `onFilesChange` are passed in as props rather than kept as private internal state. The parent (`UploadFlow`) needs to know which files were picked so it can actually send them to the backend — if this component held the file list privately, there'd be no way for the parent to ever access it. Both entry points (drag-and-drop and the hidden `<input type="file">`) funnel through one `acceptFiles()` function, which runs `validateFiles()` (from `lib/validateFiles.ts`) before ever calling `onFilesChange` — so validation logic lives in exactly one place regardless of how the files arrived.

### `lib/validateFiles.ts`

Client-side size (25MB) and MIME-type checks, plus a 50-file count cap. The comment in this file states the real reason it exists even though the backend already enforces the same limits: without it, a user picks a 40MB file, waits for the entire upload to finish over the network, and only *then* learns it was rejected. Checking client-side means they find out instantly. The backend check still exists and is the actual enforcement — a browser-side check can always be bypassed, so it's a UX nicety, not a security boundary.

### `components/UploadFlow.tsx` — the orchestrator (the biggest file, worth the most attention)

This component owns almost all the app's state. Breaking down what each piece does:

**State:** `files`, `status` (`idle`/`uploading`/`done`/`error`), `pages` (raw OCR results as they stream in), `editedLatex` (a separate map, keyed by `"pageNum-regionIndex"`, holding user edits — kept deliberately separate from `pages` so the original OCR output is never overwritten, which is what makes the per-region Revert button in `EditableLatexRegion.tsx` possible), `abortController` (for Cancel), `enhanceContrast` (the contrast-toggle checkbox value).

**`handleConvert()`** — builds a `FormData` upload, POSTs to the right endpoint, then manually parses the SSE stream: `response.body.getReader()` pulls raw byte chunks, `TextDecoder` turns them into text, and the code splits on `"\n\n"` (SSE's message separator) — keeping whatever looks like an incomplete trailing message in a buffer for the next chunk, since a single network chunk might contain zero, one, or several complete SSE messages. The browser's built-in `EventSource` API can't be used here because it only supports GET requests with no body — this app needs to POST files, so the stream has to be parsed by hand.

**Cancel (FR-005):** an `AbortController` is created fresh each time `handleConvert` runs and stored in state so the Cancel button (rendered from the same component) can reach it. Calling `.abort()` makes the in-flight `fetch`/reader throw a `DOMException` named `"AbortError"` — the `catch` block specifically checks for that and treats it as "done with partial results," not a crash: since `pages` was already being appended to as each SSE message arrived, nothing needs to be salvaged, the code just stops pretending a deliberate cancel was a failure.

**Export safety functions**, in the order they run against each region's LaTeX before it's sent to the backend:
1. `fixKnownBadPatterns()` — rewrites `\fbox` → `\boxed` (amsmath's math-mode-safe equivalent; `\fbox` forces its contents into text/LR mode even inside math mode, which breaks math-only commands like `\left`/`\right` used inside it — a real compile failure this project hit and traced). Also unwraps `\textcircled{...}` when its contents include a LaTeX command (same underlying text-mode-forcing problem, no safe math-mode equivalent exists, so it degrades gracefully by dropping the circle rather than crashing).
2. `hasBalancedBraces()` — counts `{`/`}` depth (skipping escaped `\{`/`\}`) to catch a genuinely broken region *before* sending it to the compiler. This exists because of a real production bug: a garbled OCR region like `\frac{\lg6 \lg\lg6...` with no closing brace made LaTeX consume every token after it — including `\end{document}` — until the file physically ran out, crashing the *entire* export over one bad region.
3. `formatRegionForExport()` — if braces are unbalanced, the region is swapped for a harmless `% [region omitted...]` comment instead of being sent as-is; otherwise it's wrapped in `\[ \]` (isolated/display math), `$ $` (inline/embedded math), or escaped as plain text, matching what Pix2Text labeled the region's `type`.

**`buildExportPages()`** ties it together: for each page, map every region through the safety functions above, using the edited text if present (falling back to the original OCR text), and collect human-readable warnings for any region that got dropped — surfaced in the UI as an amber banner listing exactly which page/region was skipped and why, so nothing silently vanishes.

The JSX at the bottom renders: the dropzone + contrast checkbox + Convert/Cancel buttons while idle/uploading, a progress bar during upload, a thumbnail sidebar + per-region editor list once pages start arriving, and the export buttons + warning/error banners once `status === "done"`.

### `components/EditableLatexRegion.tsx`

One region's editor: a textarea plus a live `LatexPreview`. Two pieces worth noting: the preview is **debounced 250ms** (`useEffect` + `setTimeout`, cancelled and restarted on every keystroke) so KaTeX doesn't re-parse on every single keypress, while the parent is notified of every keystroke *immediately* (not debounced) via a separate `useEffect`, since export needs the latest text regardless of whether the preview has visually caught up. The Copy button uses `navigator.clipboard.writeText()` with a transient "Copied"/"Failed" label. The Revert button only appears when `source !== initialLatex` (comparing current state against the original prop is all that's needed to know whether the user has touched this region — no extra tracking state required) and resets `source` back to the untouched OCR output.

### `components/LatexPreview.tsx`

`katex.renderToString(latex, { throwOnError: false, displayMode: true })`, wrapped in a try/catch that falls back to showing the raw string if KaTeX can't parse it — one malformed region shouldn't crash the whole page. Uses `dangerouslySetInnerHTML` because KaTeX returns real HTML markup (not React components); this is safe specifically here because KaTeX sanitizes its own output, which is not something you'd do with arbitrary user text.

### `lib/history.ts` + `lib/types.ts` — browser-only history (M5)

`lib/types.ts` just extracts the `Region`/`PageResult` interfaces that used to live only inside `UploadFlow.tsx` into their own file, so `history.ts` and the new share page (below) can both import the same shapes instead of redefining them.

`lib/history.ts` is the actual M5 history feature: `saveToHistory`, `getHistory`, `deleteHistoryEntry`, `clearHistory`, all backed by a single `localStorage` key (`"mathscan-history"`) holding a JSON array of `HistoryEntry` objects, capped at `MAX_ENTRIES = 20` (`localStorage` has a real per-origin size limit in most browsers — capping keeps months of normal use from ever approaching it, instead of failing unpredictably once the quota is hit). Every function starts with `if (typeof window === "undefined") return` — Next.js renders parts of this app on the server first, where `localStorage` doesn't exist at all, so these guards make every history function a safe no-op server-side instead of crashing.

This was a deliberate choice over a real accounts system, made explicitly (see the M5 planning discussion): no database, no login screen, no session handling, for what's meant to stay a free, no-signup tool. The real, honest tradeoff: history only exists on the device/browser that created it, and clearing site data loses it — share links (above) exist specifically to cover the one thing this can't do, letting someone *else* see a result.

### `UploadFlow.tsx` additions (M5) — the history panel and share button

Two new pieces bolted onto the existing orchestrator component described above. A `useEffect` keyed on `status` calls `saveToHistory()` automatically the moment a conversion reaches `"done"` — no explicit "save" action needed, history is just what already happened. A History panel (toggled via `historyOpen` state) lists past entries and lets the user reload one (`loadFromHistory()`, which just restores `pages`/`editedLatex` from the saved entry — no network request, since everything needed is already sitting in `localStorage`) or delete individual entries / clear everything.

The Share button (`handleShare()`) POSTs the current page/region data to `/api/share`, stores the returned share URL in state, and `handleCopyShareUrl()` copies it to the clipboard the same way `EditableLatexRegion.tsx`'s existing Copy button does (`navigator.clipboard.writeText`, with a transient "Copied" label). `handleReset()` was updated to also clear the share URL/error state, so starting a new conversion doesn't leave a stale share link visible.

### `app/share/[id]/page.tsx` — the read-only share view (M5)

A separate client component/route, not a mode of the main upload page. Fetches `apiUrl(/api/share/${params.id})` on load and renders the returned pages/regions through the *same* `EditableLatexRegion` component the main app uses (with a no-op `onChange`, since a share link is meant to be read-only — no editing, no re-export) plus a small local copy of the confidence badge UI. Deliberately reuses the real region-rendering component rather than building a simplified read-only view from scratch, so a shared link looks and behaves like the actual app, not a stripped-down summary.

### `lib/download.ts`

A generic `downloadBlob(filename, blob)` helper — creates a temporary `<a>` element, clicks it, revokes the object URL afterward. Deliberately not specific to LaTeX or PDFs; the actual file content always comes from the backend.

### `lib/buildTexSource.ts`

Dead code. An early version of `.tex`-building logic that got duplicated between frontend and backend; superseded once `routers/export.py` became the single source of truth. Left in place as a one-line `export {}` stub with a comment explaining why, rather than deleted, purely because of an earlier OneDrive file-permission quirk in this session — safe to delete manually whenever convenient, nothing imports it.

---

## Real bugs that shaped this code (quick recap)

Worth knowing these happened, not just that the code looks the way it does:

- **Pix2Text's full-page API silently returned nothing** on real handwriting photos (misclassified as "figure" by its layout detector) — fixed by switching to `recognize_text_formula()`, which skips layout detection entirely.
- **`UploadFile` objects can't be read inside a `StreamingResponse` generator** — the generator runs *after* the endpoint returns, by which point FastAPI has closed the files. Fixed by reading/saving everything to disk before constructing the response.
- **`\fbox` and `\textcircled` break math-mode compilation** when their contents include math-only commands, because both force their argument into text/LR mode even inside math mode. Fixed with a `\boxed` substitution and a conditional unwrap.
- **An OCR region with an unclosed brace crashed the entire PDF export**, not just that one region, because LaTeX keeps scanning for the matching `}` past `\end{document}` until the file physically ends. Fixed with a brace-balance check before export, swapping bad regions for a harmless comment instead of sending them to the compiler.
- **Git operations on the OneDrive-synced folder fail from inside this sandbox** (`index.lock` permission errors) — not a code bug, but the reason every commit in this project has been run from your own local terminal instead of through me.
- **(M4) `add_local_dir` uploaded the entire local `.venv`** (4,214 files) to Modal because nothing told it not to — fixed by adding an `ignore=` list.
- **(M4) `import cv2` crashed with `libGL.so.1: cannot open shared object file`** during the Modal image build — OpenCV (pulled in indirectly by Pix2Text) expects OpenGL/GTK libraries that a minimal `debian_slim` image doesn't have — fixed with `libgl1` + `libglib2.0-0`.
- **(M4) Every GPU container crashed on startup** with a CUDAExecutionProvider error — Pix2Text's ONNX runtime detected the attached T4 GPU and tried to use it, but only the CPU-only `onnxruntime` package was installed — fixed by removing the GPU request entirely, matching the CPU-only config that already worked locally.
- **(M4) A live PDF export crashed with `libgraphite2.so.3: cannot open shared object file`** — Tectonic's binary dynamically links against Graphite2/HarfBuzz/ICU, absent from `debian_slim` — fixed by adding those three packages.
- **(M4) A live PDF export then crashed with `libc.so.6: version 'GLIBC_2.38' not found`** — Tectonic's official install script fetches a GNU-target build linked against a newer glibc than Modal's base image ships — fixed by downloading Tectonic's musl-target release build directly instead, which doesn't depend on the host's glibc version at all.
- **(M5/M6) The local dev sandbox's view of a just-edited file went stale (OneDrive sync lag)** — a large edit to `UploadFlow.tsx` was verified as correct via the Edit tool, but a shell command reading the same file minutes later still saw the old, shorter version. Not a code bug — a real limitation of editing files that live inside a OneDrive-synced folder from two different access paths at once. Mitigation: trust the file-editing tool's own read-back as the source of truth, not a separate shell command, when the two might disagree.
- **(M6) PaddleOCR's API changed between major versions** — `PaddleOCR.predict(cls=True)`, the documented pattern from older tutorials, threw `TypeError: predict() got an unexpected keyword argument 'cls'` against the installed 3.x version. Fixed by switching to the current `.predict()` call (no `cls` argument) and parsing its new `rec_texts`/`rec_scores` dict-based result shape instead of the old `[bbox, (text, score)]` tuple format.
- **(M6) `paddlepaddle` 3.3.x has a confirmed CPU inference regression** — `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]`, a PIR/oneDNN bug tracked in PaddlePaddle's own GitHub issues. Fixed by pinning `paddlepaddle==3.2.2` in `requirements-fallback.txt`, the version actually confirmed working against real handwriting.
- **(M6) `_fix_missing_spaces` had a real logic bug on its first version** — it checked "does the whole string contain a space *anywhere*" and skipped a genuinely merged 21-character chunk, because *other* parts of the same OCR region happened to already have spaces in them. Caught by a real pytest failure, not by inspection. Fixed by checking each space-separated chunk against the length threshold individually, not the string as a whole.
- **(M6) A Modal deploy hung indefinitely (twice, 40+ minutes each), stalling at the exact same point** — right after Pix2Text's weights finished baking into the image, before PaddleOCR's weights ever started downloading. Traced to PaddleX (PaddleOCR's inference engine) running a "checking connectivity to the model hosters" step before every download, which itself hung depending on network reachability from Modal's build environment. Fixed by setting `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True` on the image; the retried deploy succeeded in 78 seconds.
- **(M6) Multiple further accuracy-improvement ideas were tried and honestly rejected, not silently dropped** — `symspellpy` bigram context correction (doesn't favor domain vocabulary over common English), confidence-ratio gating of corrections (good and bad corrections had overlapping confidence ratios, no clean cutoff), `language_tool_python` grammar checking (needs Java 17+, unreliable public API), and OpenCV adaptive thresholding as image preprocessing (measurably hurt both OCR engines on the real test set, including blanking one page entirely). Each is a real, tested negative result — see the M6 pipeline section above for the specifics of each.
- **(M6.5) A live test showed a broken-brace math region getting "fixed" by padding it with repeated garbage** (`\vert\times\vert\times...`) instead of staying safely omitted — the model balanced the brace structure without fixing the actual (unrecoverable) content, so it slipped past the frontend's brace-balance safety check and exported as if it were real math. Fixed by tightening `_llm_correct_latex`'s prompt (explicitly forbidding brace rebalancing/padding) and adding `_looks_like_repetition_garbage` as a hard code-level guard, not just a prompt instruction.
- **(M6.5) A live test showed a short/incomplete OCR fragment triggering a conversational refusal from the model** ("I'm not sure what the original LaTeX was... please provide the full LaTeX code") that got exported verbatim as if it were real corrected content, because the "if unsure, return unchanged" instruction wasn't reliably followed. Fixed by giving the model an explicit sentinel token (`NOCHANGE`) to signal "can't confidently fix this," plus a phrase-based `_looks_like_llm_refusal` backstop in case it free-writes a refusal anyway.
- **(M6.5) A live test on a real page of poli-sci notes showed the prose correction pass silently paraphrasing real content** — "if we want a **scientific** answer" became "if we want a **concise toxic** answer," and "your hypothesis"/"not enough" became "car hypothesis"/"not each." Fluent, confident, and wrong — worse than a visibly garbled word, since it reads as legitimate. Fixed with `_correction_changes_too_much` (later generalized to `_page_correction_changed_too_much`): a word-level similarity guard, verified directly against these exact real failures before shipping, that rejects any correction where a swapped word shares fewer than half its characters with the original.
- **(M6.5) Per-region correction was replaced with page-level correction after a structural realization, not another bug**: a single line/region has no visibility into the rest of the page, which was the root cause behind the paraphrasing failure above — the model had no context to know what the notes were actually about. Collapsing every page into one Groq call with full-page context was verified (via a synthetic full-page test, not assumed) to still catch the same class of bad substitution even when diluted across ~90 words of otherwise-unchanged text, while allowing legitimate multi-word fixes real page-level context makes possible.
- **(M6.5) Collapsing every page into one block immediately caused the exact predicted-in-advance regression: one region's unrecoverable unbalanced brace omitted the ENTIRE page's export**, not just that one region — every other region on the page, math and prose alike, lost along with it. A live test confirmed this happening for real. Fixed by moving the brace-balance check to run per ORIGINAL region, server-side, before regions are combined (`_has_balanced_braces`, a Python port of the frontend's existing check) — only the genuinely broken region gets swapped for an omitted-region comment now, restoring the pre-M6.5 behavior, while the page still collapses to one block for display/correction purposes.
- **(M6.5) The page-level hallucination guard had a real blind spot for pure word INSERTIONS, not just substitutions**: a live test showed Groq inserting three brand-new words ("/", "→", "Chopper") into an otherwise-correct sentence — verified directly via `difflib` that this is categorized as an `insert` opcode, which the guard's per-word similarity check never looked at (it only checked `replace` opcodes). The aggregate word-count-drift check (allowing up to 20% page-wide drift) didn't catch it either, since 3 inserted words were a small enough fraction of a full page to stay under that threshold — the same "diluted across a long page" problem already solved for substitutions, just not yet for insertions. Fixed by rejecting ANY `insert`/`delete` opcode outright, matching the correction prompt's own explicit instruction not to add or remove words — closing the dilution loophole completely instead of just narrowing it.
- **(M6.5) Switched the Groq model from `llama-3.1-8b-instant` to `llama-3.3-70b-versatile`** after the insertion failure above suggested a real capability gap, not just a prompt-wording one — the prompt already explicitly forbade adding words. 70B is confirmed current on Groq's models list, still fast, and still cheap enough per page to stay within Groq's free tier at this project's usage volume. The safety guards stay in place regardless of model — a better model should mean they reject less often, not that they become unnecessary.

---

## What's not built yet (this is where M7 and beyond start)

M4 is done: the backend is live on Modal (`modal_app.py`), the frontend is live on Vercel with `NEXT_PUBLIC_API_URL` pointed at it, CORS is tightened to the real domain, and a real end-to-end upload/convert/export was tested successfully on the deployed URLs.

M5 is done: browser-only anonymous history (`lib/history.ts`, `localStorage`, capped at 20 entries) and server-backed share links (`share_store.py` + `routers/share.py`, backed by `modal.Dict`) are both built, tested (19/19 passing), and deployed. This was a deliberate choice over real accounts, made explicitly rather than by default.

M6 (free-tooling accuracy work) is done and its ceiling has genuinely been reached: a confidence-gated PaddleOCR fallback, `wordninja` spacing correction, and domain-boosted `pyspellchecker` typo correction are all live and verified against real handwriting. Several further approaches (context-aware spell correction, confidence-ratio gating, grammar-checking, adaptive image thresholding) were tried and empirically rejected — documented above, not just abandoned quietly. CI (`.github/workflows/api-tests.yml`) now runs the test suite automatically on every push/PR. `eval/claude_vs_pix2text.py` exists for manual, qualitative side-by-side comparisons; `eval/accuracy_benchmark.py` (new) is the formal counterpart — a fixed, labeled test set with a repeatable character-level accuracy percentage against the SRS's ≥80% target, run against the real production pipeline. Seeded so far with 3 real, hand-transcribed pages; **still needs to grow to the 30-50 image target** (`ROADMAP.md`) before the resulting number is a fully defensible sample size — the harness and a first real number exist now, the larger sample doesn't yet.

M6.5 (LLM-assisted correction, added after M6's free-tooling ceiling was reached) is also done: `_correct_full_page` runs Groq's `llama-3.3-70b-versatile` once per page, always, with the whole page's context, replacing an earlier per-region design that structurally couldn't see enough context to avoid real, observed failures (documented in the bugs recap above). Multiple real live-testing rounds surfaced and fixed genuine safety gaps (padded-garbage math, conversational refusals leaking into export, hallucinated word substitutions, an insertion-detection blind spot, one bad region silently killing an entire page's export) — each guard was verified against the actual failing case before being considered fixed, not just assumed to work.

Still not built at all: enforcement of the 20-page free cap or the one-time $5-for-1,000-pages pack from the cost analysis file (pricing decisions, not shipped code — no payment integration, no per-user page counter), and a paid Claude/Mathpix OCR fallback for the cases free tooling can't fix (explicitly deferred for cost reasons — the user chose not to spend money on an Anthropic API key at this stage; remains a documented, ready-to-build option, not a dead idea). `main.py`'s `lifespan` still assumes a long-lived process for the *local dev* case (`uvicorn main:app`), but on Modal this is mitigated by `scaledown_window=300` keeping a container warm between requests rather than reloading the model from scratch constantly.

---

## Terminology / glossary

A plain-language reference for every library, tool, and concept this project actually uses — written for looking things up quickly, not for reading start to finish. Grouped roughly by where each thing sits in the stack.

**Backend framework and hosting**

- **FastAPI** — the Python web framework this whole backend is built on. Turns Python functions into HTTP endpoints (`@app.get(...)`, `@app.post(...)`), handles request/response validation automatically via type hints, and has first-class support for async code and streaming responses.
- **Uvicorn** — the actual server process that runs a FastAPI app and listens for real HTTP connections. FastAPI defines *what* to do with a request; Uvicorn is *what's listening on the network* to hand requests to it.
- **ASGI** — "Asynchronous Server Gateway Interface," the standard Python web servers and frameworks use to talk to each other when async code (like streaming responses) is involved. `@modal.asgi_app()` in `modal_app.py` means "treat this FastAPI app as a standard ASGI app and host it."
- **Modal** — the serverless cloud platform this backend is deployed on. "Serverless" here means: no server is running (or costing money) when nobody's using the app; a container starts automatically on the first request and can shut down again after a few idle minutes (`scaledown_window`). Chosen specifically because it can host a full app with multiple routes and streaming, unlike simpler "one function in, one answer out" serverless platforms.
- **`modal.Image`** — Modal's way of describing what a container should have installed (system packages, Python packages, files) before it ever runs application code — built once, reused every time a new container starts.
- **`modal.Dict`** — Modal's own distributed key-value store; a small database-like object that's the same across every container, used here to store share links so any container can read a link saved by a different one.
- **CORS** ("Cross-Origin Resource Sharing") — a browser security rule that blocks a webpage from one domain (the Vercel frontend) from calling an API on a *different* domain (the Modal backend) unless the API explicitly allows it. `CORSMiddleware` + `ALLOWED_ORIGIN` in `main.py` is what grants that permission for exactly the real frontend URL.
- **SSE** ("Server-Sent Events") — a way for a server to push multiple messages to the browser over one HTTP connection, over time, instead of one request getting one response. Used here so each page's OCR result can appear in the browser as soon as it's ready, instead of waiting for the whole document to finish.

**OCR engines (turning an image into text/LaTeX)**

- **Pix2Text** — the primary, math-specialized OCR engine. Good at recognizing handwritten equations and turning them into LaTeX; weaker on long stretches of ordinary handwritten prose.
- **PaddleOCR** — a general-purpose OCR engine (from Baidu's PaddlePaddle ecosystem), used here as a fallback specifically for prose-heavy pages where Pix2Text's confidence is low. Not math-aware — it doesn't produce LaTeX, just plain recognized text.
- **PaddlePaddle** — the underlying deep-learning framework PaddleOCR is built on (Baidu's answer to PyTorch/TensorFlow). Not used directly in this project's code, but PaddleOCR depends on it, and a specific version bug in it (3.3.x) had to be worked around by pinning an older version.
- **EasyOCR** — a third general-purpose OCR engine, tested as an alternative fallback candidate during evaluation. Works, but scored weaker than PaddleOCR on this project's real test images, so it isn't part of the live app.
- **ONNX Runtime** — the engine Pix2Text actually runs its neural network models on. Matters here because it comes in a CPU version and a separate GPU version (`onnxruntime-gpu`) — installing the wrong one for the hardware actually available was a real deploy crash, fixed by matching CPU-only ONNX Runtime to CPU-only Modal containers.

**Text post-processing (cleaning up what the OCR engines return)**

- **`wordninja`** — a small library that splits a run of characters with no spaces into likely separate words, based on word-frequency statistics (which sequences of letters are more likely to be real English words than others). Used to fix PaddleOCR output where an entire line comes back as one merged blob of text.
- **`pyspellchecker`** — a spell-correction library using edit distance (how many single-character changes turn one word into another) combined with word frequency, to guess the most likely intended word for a misspelled one. Used to fix individual misread words in PaddleOCR's output, with a custom boost so course-specific vocabulary (lecture, syllabus, equilibrium, etc.) gets picked over more common but wrong general-English words.
- **`symspellpy`** — a different, faster spell-correction library that can also use surrounding words ("context") to pick a correction, tested as a possible improvement over `pyspellchecker` but found not to help here, since its bigram (word-pair) frequency data is general English, not academic vocabulary.
- **Groq** — a company that hosts open-source LLMs (like Llama 3.1) and serves them extremely fast, with a genuine free API tier (no credit card required). Used in this project (`_correct_full_page` in `inference.py`) for a text-only, whole-page context-aware correction pass on OCR output — cheap enough to run for free since it's sent a page's worth of already-recognized text, never an image.
- **`difflib`** (Python standard library) — the same diffing engine behind tools like `diff`; used in this project (`_correction_changes_too_much`, `_page_correction_changed_too_much`) to measure how similar two words or two whole pages of text are, as a safety check against an LLM correction pass silently rewriting content instead of fixing a genuine misread.
- **`language_tool_python`** — a Python wrapper around LanguageTool, a full grammar-checking engine (catches things a simple spellchecker can't, like wrong word choice in context). Tested and rejected here — it needs a Java 17+ runtime to run locally, and its free public API is rate-limited and unreliable, both disproportionate costs for the accuracy gain it offered.
- **Edit distance** — a way of measuring how different two strings are, by counting the minimum number of single-character insertions, deletions, or substitutions needed to turn one into the other. The core idea behind how spellcheckers rank correction candidates.

**Document generation and PDF handling**

- **Tectonic** — a self-contained LaTeX compiler (turns `.tex` source into a real PDF), used for this project's PDF export feature. Chosen over a full TeX Live install because it's one binary that only downloads the specific LaTeX packages a document actually needs, instead of several gigabytes of everything.
- **LaTeX** — the typesetting language mathematical documents are traditionally written in (`\frac{a}{b}`, `\int`, `\sum`, and so on). This is the actual output format this app produces from handwritten images.
- **KaTeX** — a JavaScript library that renders LaTeX math directly in the browser, fast, without needing a server round-trip. Used on the frontend to show a live preview of each recognized region as real typeset math instead of raw LaTeX text.
- **Poppler** (`pdftoppm`, `pdfinfo`) — a set of command-line PDF tools this project uses (via the `pdf2image` Python wrapper) to split an uploaded PDF into individual page images and to read basic info like page count.
- **`pdf2image`** — the Python library that wraps Poppler's command-line tools in a Python-friendly interface (`convert_from_path`, and so on).
- **PyMuPDF (`fitz`)** — a different PDF library, used only in this project's *test suite* to generate real PDF files (including a genuinely encrypted one) to test the Poppler pipeline against, rather than relying on hand-crafted or borrowed sample files.

**Frontend**

- **Next.js** — the React-based framework the frontend is built with. Handles routing (which URL shows which page), and supports both server-rendered pages (`app/page.tsx`, no client JavaScript needed for the static parts) and interactive client components (`UploadFlow.tsx` and everything inside it).
- **React** — the underlying UI library Next.js is built on; components, state (`useState`), and effects (`useEffect`) are all React concepts.
- **Tailwind CSS** — a utility-class CSS framework (`className="text-sm font-bold"` instead of writing separate CSS files) used for all of this project's styling.
- **`localStorage`** — a browser API that lets a webpage store small amounts of data on the user's own device, persisting across page reloads and browser restarts (until the user clears site data). This is the entire mechanism behind M5's anonymous history feature — no server, no account, just data sitting in the browser.
- **SSE parsing via `fetch`/`ReadableStream`** — the frontend manually reads the OCR streaming response using `response.body.getReader()` rather than the browser's built-in `EventSource` API, because `EventSource` can only make GET requests with no body, and this app needs to POST uploaded files.

**Testing and CI**

- **pytest** — the Python testing framework used for every backend test in `api/tests/`.
- **`monkeypatch`** — a pytest feature that temporarily replaces a function, method, or object attribute for the duration of one test, then automatically restores the original afterward. Used throughout this project's tests to swap in fake OCR models (`FakeP2T`) or fake fallback functions, so tests don't need a real 30-second model load or real network calls to run.
- **GitHub Actions** — GitHub's built-in automation platform, used here to run the full pytest suite automatically on every push and pull request (`.github/workflows/api-tests.yml`), so a broken change gets caught before it's merged instead of relying on someone remembering to run tests locally.
- **CI** ("Continuous Integration") — the general practice this represents: automatically building/testing every code change as soon as it's pushed, rather than only testing manually and occasionally.

**Security/misc**

- **`secrets` module** (Python standard library) — generates cryptographically secure random values, used here (`secrets.token_hex(4)`) to create share-link IDs that can't be guessed or enumerated. Different from the plain `random` module, which is fast but predictable enough that it shouldn't be used anywhere security or unguessability matters.
- **Environment variables** (`ALLOWED_ORIGIN`, `NEXT_PUBLIC_API_URL`, `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`) — configuration values read from the deployment environment rather than hardcoded into source code, so the same code can behave differently in local development versus production (e.g., which frontend URL is allowed to call the API) without editing and redeploying code for each environment.
