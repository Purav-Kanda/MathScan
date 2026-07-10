"""
Export endpoints -- SDD 3.2 (endpoint table) + 3.5 (Export Service).

WHY this logic lives on the backend, not duplicated in the frontend: this
is the single place that knows how to turn a list of pages' LaTeX into a
real .tex file or a compiled PDF. Keeping one implementation (in Python)
instead of two (Python + TypeScript) means there's only one place to fix
bugs or change the template.
"""

import re
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportPage(BaseModel):
    latex: str


class ExportRequest(BaseModel):
    pages: list[ExportPage]


# SDD 3.5: "wraps user LaTeX in a minimal preamble." amsmath gives us
# \frac, \sum, \int etc.; amssymb/amsfonts give symbols like \in, \notin,
# \subseteq -- without these two packages, a lot of real student math
# wouldn't compile at all.
PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage{amsmath, amssymb, amsfonts}
\usepackage[margin=1in]{geometry}
\title{MathScan Export}
\date{}
\begin{document}
\maketitle
"""


def build_tex(pages: list[ExportPage]) -> str:
    # FR-030: "a single .tex file with \section{Page N} markers."
    body = "\n\n".join(
        f"\\section{{Page {i + 1}}}\n{page.latex}" for i, page in enumerate(pages)
    )
    return f"{PREAMBLE}\n{body}\n\\end{{document}}\n"


@router.post("/tex")
async def export_tex(payload: ExportRequest):
    tex_source = build_tex(payload.pages)
    return Response(content=tex_source, media_type="text/plain")


@router.post("/pdf")
async def export_pdf(payload: ExportRequest):
    tex_source = build_tex(payload.pages)

    # WHY a fresh temp directory per request: Tectonic needs real files on
    # disk to compile (it can't compile a string in memory), and a
    # TemporaryDirectory guarantees cleanup even if compilation throws --
    # no leftover .tex/.pdf/.aux files accumulating on the server over time.
    with tempfile.TemporaryDirectory() as tmp_dir:
        tex_path = Path(tmp_dir) / "document.tex"
        tex_path.write_text(tex_source, encoding="utf-8")

        # WHY Tectonic over pdflatex/full TeX Live: Tectonic ships as one
        # self-contained binary and fetches only the packages a document
        # actually uses, on demand -- vs. TeX Live's multi-GB install of
        # every package that exists. `-X compile` is Tectonic's v2+ CLI.
        try:
            result = subprocess.run(
                ["tectonic", "-X", "compile", str(tex_path), "--outdir", tmp_dir],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="Tectonic is not installed on this server. Install it and ensure it's on PATH.",
            )

        if result.returncode != 0:
            # Tectonic's own error output ("document.tex:27: Missing $
            # inserted") tells us THAT line 27 is wrong, but not what's
            # actually on it -- and by the time we're reading this, the temp
            # file is about to be deleted. Pulling the matching line out of
            # `tex_source` (which we still have in memory) turns a vague
            # "compilation failed" into "here's the exact broken text,"
            # which is what actually lets you find and fix the bad region.
            context = ""
            match = re.search(r"document\.tex:(\d+):", result.stderr)
            if match:
                bad_line = int(match.group(1))
                lines = tex_source.splitlines()
                start, end = max(0, bad_line - 3), min(len(lines), bad_line + 2)
                numbered = [
                    f"{'>>> ' if i + 1 == bad_line else '    '}{i + 1}: {lines[i]}"
                    for i in range(start, end)
                ]
                context = "\n\nLikely cause (marked with >>>):\n" + "\n".join(numbered)

            raise HTTPException(
                status_code=500,
                detail=f"PDF compilation failed: {result.stderr[-1000:]}{context}",
            )

        pdf_path = Path(tmp_dir) / "document.pdf"
        pdf_bytes = pdf_path.read_bytes()

    return Response(content=pdf_bytes, media_type="application/pdf")
