# MathScan — Compressed Build Roadmap

**Status: shipped.** Backend and frontend are both deployed and live (see root `README.md` for URLs); CI runs the test suite on every push. Two milestones below were completed with a different implementation than originally planned (noted inline) — the plan changed, the scope didn't.

Original SDD roadmap assumed an 8-week runway (M0-M7 by Aug 2). Deadline was **July 31, 2026** — 25 days from kickoff. Same scope, faster pace. No features cut.

| Milestone | Dates | Deliverable |
|---|---|---|
| M0 | Jul 6-8 | Repo skeleton, FastAPI hello-world in Docker, Pix2Text-MFR returns LaTeX for one test image |
| M1 | Jul 9-12 | `/api/ocr/pdf` + `/api/ocr/images` working end-to-end, SSE progress |
| M2 | Jul 13-17 | Next.js `/`, `/upload`, `/job/[id]` render with live SSE progress |
| M3 | Jul 18-20 | LaTeX edit + Tectonic export + PDF/`.tex` download |
| M4 | Jul 21-22 | Public URL live — shipped on **Modal + Vercel**, not Hetzner as originally planned (Modal's `@modal.asgi_app()` hosted the existing SSE endpoints with far less rework than a VM would've needed) |
| M5 | Jul 23-25 | History, share links, confidence highlighting |
| M6 | Jul 26-28 | Accuracy benchmark harness built and run for real (see root README) — shipped with **PaddleOCR + Groq's free Llama 3.3 70B** as the fallback/correction layer instead of Mathpix, to keep the whole pipeline on the no-paid-APIs constraint. 80% target not yet met (currently 71.4% char accuracy on a partial 7-image run) |
| M7 | Jul 29-31 | Soft launch (Show HN / Reddit), monitor for P0s |

Each milestone starts with a short concept lesson (the "why" behind the design) before we write code. Dropped from the original doc: M8 (LoRA finetune) and M9 (marketing polish) — both were already labeled post-MVP stretch goals in the SDD, not part of v1 launch criteria.

## Working agreement
- Code lives in this repo; you run/test locally in VS Code, I write files directly here.
- Every session: mini-lesson on the concept → code → explanation of key design choices baked into that code.
- Task list tracks milestone progress across our sessions.

## Functionality to-dos (app value + resume signal)

Not new milestones, slotted into the existing ones — added after a resume-focused brainstorm:

- **CI/CD** — GitHub Actions workflow running `pytest` on every push. Not in the original scope; cheap to add (~1 session), high resume signal since most student projects skip it entirely. **Shipped.**
- **Accuracy evaluation harness (M6) — built.** `eval/accuracy_benchmark.py` runs a labeled test set (`eval/test_set/`) through the real production pipeline (`recognize_page`, matching what live users get, including the PaddleOCR/Groq fallback), each image isolated in its own subprocess (a real crash found the hard way — see the script's own module docstring), and computes both character-level accuracy (Levenshtein edit distance, the SRS's NFR-010 metric, ≥80% target) and a second order-insensitive word-overlap recall metric, with per-image and aggregate results saved to `eval/test_set/results.json`. The test set's composition changed significantly from the original plan: it started as 16 real pages of the project author's own (fairly cursive) handwriting, which scored 33.0%/50.5%; those were later removed at the author's explicit request in favor of 14 externally-sourced, clearly-legible real handwritten notes (see `eval/test_set/README.md` for full sourcing/attribution), which scored 71.4%/73.0% on the 7 images actually run so far. **Still needs to grow toward the full 30-50 image target** and to finish scoring the remaining images in the current set before either number is a fully defensible sample size for a resume line.
- **PaddleOCR + Groq fallback (M6)** — shipped in place of the originally-planned Claude/Mathpix fallback, to keep the whole pipeline free-tier. PaddleOCR always runs as a second opinion (not just on low Pix2Text confidence, after a real bug where Pix2Text was confidently wrong), and Groq's Llama 3.3 70B does a whole-page correction pass with a hallucination guard that went through three real revisions.
- **Auth + history (M5)** — shipped as local-storage-based history instead of full auth + DB-backed sessions, to avoid an account system for what's primarily a portfolio/demo tool. Share links (which need to be readable by someone other than the uploader) are the one thing that's server-side.
