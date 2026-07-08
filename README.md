# MathScan

Handwritten math (photo/PDF) → LaTeX. See `09_SRS_MathScan.md` and `10_SDD_MathScan.md` for full spec, `ROADMAP.md` for the compressed build schedule.

**Status:** M0 code complete (needs your local verification run — see below), M1 (PDF + multi-image OCR endpoints) code complete.

## Repo layout (grows as milestones land)
```
api/      FastAPI inference backend
  main.py               app entrypoint, /api/health, /api/ocr/test
  inference.py          Pix2Text model load + recognize_page()
  pdf_preprocessor.py   PDF -> per-page JPEG splitting
  routers/ocr.py        /api/ocr/pdf, /api/ocr/images (SSE streaming)
  tests/                pytest unit tests
web/      Next.js frontend (starts M2)
infra/    Docker / deploy config (starts M4)
```

## One-time setup you need to do (I can't run this from here)
1. **Delete `api/.venv`** if it exists in this folder — I created it while testing in a Linux sandbox and it's useless on Windows (wrong binaries). Just delete the folder in Explorer; it's already in `.gitignore` either way.
2. **`git init`** in this folder yourself (`git init`, `git add -A`, `git commit -m "M0+M1: scaffold + OCR pipeline"`). I tried to do this from my sandbox but OneDrive's file-sync locks blocked git's file operations on this mount — a 10-second job for you locally, not worth debugging remotely.

## Running the API locally
```
cd api
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```
You'll also need **Poppler** on your PATH for PDF splitting to work (`pdftoppm`/`pdfinfo`):
- Windows: download from https://github.com/oschwartz10612/poppler-windows/releases, add its `bin/` to PATH.
- macOS: `brew install poppler`.

Then:
1. `GET http://localhost:8000/api/health` — wait for `"model_loaded": true` (~30s on first boot).
2. `POST http://localhost:8000/api/ocr/test` with an image file — quick single-image smoke test.
3. `POST http://localhost:8000/api/ocr/pdf` with a multi-page PDF, or `POST /api/ocr/images` with multiple image files — both stream back Server-Sent Events, one message per page.

Run tests: `cd api && pytest` (currently covers `inference.py`'s confidence aggregation — no GPU or model load needed).
