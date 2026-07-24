# MathScan — Compressed Build Roadmap

Original SDD roadmap assumed an 8-week runway (M0-M7 by Aug 2). Deadline is now **July 31, 2026** — 25 days from kickoff. Same scope, faster pace. No features cut.

| Milestone | Dates | Deliverable |
|---|---|---|
| M0 | Jul 6-8 | Repo skeleton, FastAPI hello-world in Docker, Pix2Text-MFR returns LaTeX for one test image |
| M1 | Jul 9-12 | `/api/ocr/pdf` + `/api/ocr/images` working end-to-end, SSE progress |
| M2 | Jul 13-17 | Next.js `/`, `/upload`, `/job/[id]` render with live SSE progress |
| M3 | Jul 18-20 | LaTeX edit + Tectonic export + PDF/`.tex` download |
| M4 | Jul 21-22 | Public URL live (Hetzner + Vercel) |
| M5 | Jul 23-25 | History, share links, confidence highlighting |
| M6 | Jul 26-28 | 80%+ accuracy on 50-sample test set, Mathpix fallback wired |
| M7 | Jul 29-31 | Soft launch (Show HN / Reddit), monitor for P0s |

Each milestone starts with a short concept lesson (the "why" behind the design) before we write code. Dropped from the original doc: M8 (LoRA finetune) and M9 (marketing polish) — both were already labeled post-MVP stretch goals in the SDD, not part of v1 launch criteria.

## Working agreement
- Code lives in this repo; you run/test locally in VS Code, I write files directly here.
- Every session: mini-lesson on the concept → code → explanation of key design choices baked into that code.
- Task list tracks milestone progress across our sessions.

## Functionality to-dos (app value + resume signal)

Not new milestones, slotted into the existing ones — added after a resume-focused brainstorm:

- **CI/CD** — GitHub Actions workflow running `pytest` on every push. Not in the original scope; cheap to add (~1 session), high resume signal since most student projects skip it entirely.
- **Accuracy evaluation harness (M6) — built.** `eval/accuracy_benchmark.py` runs a labeled test set (`eval/test_set/`) through the real production pipeline (`recognize_page`, matching what live users get, including the PaddleOCR/Groq fallback), each image isolated in its own subprocess (a real crash found the hard way — see the script's own module docstring), and computes both character-level accuracy (Levenshtein edit distance, the SRS's NFR-010 metric, ≥80% target) and a second order-insensitive word-overlap recall metric, with per-image and aggregate results saved to `eval/test_set/results.json`. Seeded with 11 real pages (poli-sci, calculus, and econ notes, pulled from the actual course PDFs uploaded earlier this project, transcribed by hand against the real images) — up from the original 3. **Still needs to grow toward the full 30-50 image target** (see `eval/test_set/README.md` for how to add more) before the resulting percentage is a fully defensible sample size for a resume line — but the harness, the real numbers, and a real fix for the biggest accuracy bug found so far (page text getting fragmented into one word per line during export) now exist where none of that did before.
- **Confidence-based Claude fallback (M6, ties into the hybrid cost analysis in section 4.4)** — route low-confidence pages to Claude instead of trusting Pix2Text blindly. Real multi-model routing, and directly fixes garbled output on messy handwriting.
- **Auth + history (M5)** — sessions, DB schema, protected routes. Standard full-stack signal, already scoped as part of M5.
