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
