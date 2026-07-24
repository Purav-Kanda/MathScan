"""
Tests for routers/export.py. Split into two kinds deliberately:

1. Unit tests on build_tex() directly -- fast, no server needed, checks the
   actual string output is structured correctly (sections, preamble).
2. An endpoint test via FastAPI's TestClient for /api/export/tex -- this one
   doesn't need Tectonic at all (it's just text), so it's safe to run even
   on a machine without Tectonic installed. We deliberately do NOT test
   /api/export/pdf here the same way, since that requires a real Tectonic
   binary and a real LaTeX compile -- an integration concern, not something
   to depend on for every test run (a CI box without Tectonic installed
   would fail a test that has nothing to do with the code being correct).

Run from api/: pytest
"""

import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import export as export_module
from routers.export import ExportPage, build_tex
from routers.export import router as export_router

# WHY a fresh minimal FastAPI app instead of importing `app` from main.py:
# main.py's `app` carries the real lifespan (loads the actual Pix2Text
# model on startup -- a real, ~30s, network-touching operation). This test
# only exercises export.py's pure string-building logic and one endpoint
# that never touches the model at all, so there's no reason to couple it to
# main.py's startup behavior -- mounting just the router under test keeps
# this fast and isolated.
_app_under_test = FastAPI()
_app_under_test.include_router(export_router)
client = TestClient(_app_under_test)


def test_build_tex_includes_preamble_and_sections():
    tex = build_tex([ExportPage(latex="x^2=4"), ExportPage(latex="y=mx+b")])

    assert "\\documentclass" in tex
    assert "\\usepackage{amsmath" in tex
    assert "\\section{Page 1}" in tex
    assert "\\section{Page 2}" in tex
    assert "x^2=4" in tex
    assert "y=mx+b" in tex
    assert tex.strip().endswith("\\end{document}")


def test_build_tex_handles_zero_pages():
    # An edge case worth checking explicitly: what if every page failed
    # OCR, or the user deselected everything? Should still produce a valid
    # (if empty) document, not crash.
    tex = build_tex([])
    assert "\\begin{document}" in tex
    assert "\\end{document}" in tex


def test_export_tex_endpoint_returns_plain_text():
    response = client.post("/api/export/tex", json={"pages": [{"latex": "x=1"}]})

    assert response.status_code == 200
    assert "x=1" in response.text
    assert "\\section{Page 1}" in response.text


# WHY these three tests monkeypatch export_module._run_tectonic instead of
# calling the real binary: same reasoning as this file's own module
# docstring for why /api/export/pdf isn't tested via a real compile --
# these tests are about the *retry-on-failure* logic added after a real
# export failed outright (one garbled OCR region -- \textcircled{\div}
# inside display math -- aborted the whole document), not about Tectonic
# itself. Faking the compiler's pass/fail behavior lets that retry logic be
# tested precisely, on any machine, without Tectonic installed at all.


def test_export_pdf_retries_and_comments_out_the_line_that_broke_compilation(monkeypatch):
    # Figure out the real line number "GARBLED" lands on from build_tex
    # itself, rather than hardcoding one -- keeps this test correct even if
    # the preamble grows or shrinks by a line later.
    tex_preview = build_tex([ExportPage(latex="GARBLED")])
    bad_line_no = next(
        i for i, line in enumerate(tex_preview.splitlines(), start=1) if "GARBLED" in line
    )

    calls = []

    def fake_run_tectonic(tex_source, tmp_dir):
        calls.append(tex_source)
        if len(calls) == 1:
            # First attempt: simulate Tectonic's real failure mode on a
            # region it can't parse, pointing at the exact line.
            return subprocess.CompletedProcess(
                args=["tectonic"],
                returncode=1,
                stdout="",
                stderr=f"document.tex:{bad_line_no}: Missing $ inserted",
            )
        # Second attempt (after the bad line got commented out): simulate a
        # real successful compile by writing the PDF Tectonic would have
        # produced, since export_pdf reads it straight off disk afterward.
        (Path(tmp_dir) / "document.pdf").write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(args=["tectonic"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(export_module, "_run_tectonic", fake_run_tectonic)

    response = client.post("/api/export/pdf", json={"pages": [{"latex": "GARBLED"}]})

    assert response.status_code == 200
    assert response.content == b"%PDF-fake"
    assert len(calls) == 2

    retried_lines = calls[1].splitlines()
    # The bad line should now be commented out (LaTeX ignores it), but the
    # original text stays visible in the comment -- easier to spot in the
    # downloaded .tex than a silently vanished line.
    assert retried_lines[bad_line_no - 1] == "% [omitted: broke PDF compilation] GARBLED"


def test_export_pdf_returns_500_with_context_when_no_line_number_in_stderr(monkeypatch):
    def fake_run_tectonic(tex_source, tmp_dir):
        return subprocess.CompletedProcess(
            args=["tectonic"],
            returncode=1,
            stdout="",
            stderr="some fatal error with no document.tex:N location info",
        )

    monkeypatch.setattr(export_module, "_run_tectonic", fake_run_tectonic)

    response = client.post("/api/export/pdf", json={"pages": [{"latex": "x=1"}]})

    assert response.status_code == 500
    assert "PDF compilation failed" in response.json()["detail"]


def test_export_pdf_gives_up_once_the_reported_line_is_already_commented_out(monkeypatch):
    # WHY this scenario matters: without the "already commented out" check
    # in export_pdf's retry loop, a compiler that keeps blaming the same
    # line number (e.g. a genuinely unfixable structural problem, not a
    # single bad region) would retry forever instead of failing loudly.
    call_count = {"n": 0}

    def fake_run_tectonic(tex_source, tmp_dir):
        call_count["n"] += 1
        return subprocess.CompletedProcess(
            args=["tectonic"],
            returncode=1,
            stdout="",
            stderr="document.tex:1: Missing $ inserted",
        )

    monkeypatch.setattr(export_module, "_run_tectonic", fake_run_tectonic)

    response = client.post("/api/export/pdf", json={"pages": [{"latex": "x=1"}]})

    assert response.status_code == 500
    # One failing attempt, one retry with line 1 commented out (still
    # fails), then the loop recognizes line 1 is already commented and
    # stops instead of retrying a third time.
    assert call_count["n"] == 2
