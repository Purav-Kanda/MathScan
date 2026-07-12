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

This assumption — "the process stays alive and the model stays loaded" — is exactly what breaks if you deploy to a scale-to-zero serverless platform (discussed in the cost analysis file, section 3.1): a fresh cold start means paying that 30-second load cost again. Worth remembering going into M4.

`/api/ocr/test` is a leftover from M0 — a single-image, no-SSE endpoint kept around purely because it's the fastest way to manually check "is the model actually working" via a plain curl command, without dealing with multipart-multi-file-plus-streaming plumbing.

### `inference.py` — the actual OCR call

The thin wrapper around Pix2Text. Two functions matter:

**`recognize_page(image, apply_contrast=False)`** — calls `_p2t.recognize_text_formula(image, return_text=False, resized_shape=768)` and reshapes the result into `{"regions": [...], "confidence_mean": ...}`.

This function's docstring documents a real debugging path worth knowing, because it explains a design decision that looks arbitrary otherwise: Pix2Text has *two* different entry points. The "full-page" API (`Pix2Text.__call__`) runs a document-layout detector first — decides whether each region is a paragraph, title, table, or figure — and only OCRs what it labels text/title/table. On real test photos, that layout detector classified genuine handwritten equations as "figure" and returned nothing, even though the math was perfectly legible. Switching to `recognize_text_formula()` — which skips layout detection entirely and just says "this image may contain text and formulas" — fixed it immediately (99.99% confidence on the same photo that returned empty before). The trade-off, stated in the docstring: you lose automatic separation of multiple distinct math regions scattered across one page (a "Should," not a "Must," per the SRS) in exchange for OCR that actually works at all.

**`enhance_contrast(image)`** — `ImageOps.autocontrast(image, cutoff=1)`. Stretches a photo's actual histogram (darkest pixel → black, lightest → white) rather than applying a fixed brightness multiplier, so it adapts to whatever the user uploaded instead of needing manual tuning. `cutoff=1` clips the extreme 1% of pixels first, so a stray shadow or glare spot doesn't throw off the whole stretch. Only runs when `apply_contrast=True` is passed through from the frontend's opt-in checkbox — never silently applied.

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

---

## What's not built yet (this is where M4 and beyond start)

To be explicit about the boundary: there's no deployment configuration yet (no Dockerfile, no serverless function wrapper, no environment-based API URL — the frontend currently assumes the backend is reachable at a relative `/api/...` path), no enforcement of the 50-page free-tier cap or the $5/year paid tier from the cost analysis file (those are pricing decisions, not shipped code), no user accounts or history (M5), and no accuracy benchmarking or a Claude/Mathpix fallback (M6). `main.py`'s `lifespan` also still assumes a long-lived process — worth revisiting if M4 goes the serverless route from the cost analysis, since a cold start would re-pay that 30-second model load every time.
