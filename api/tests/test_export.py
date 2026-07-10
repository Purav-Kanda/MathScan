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

from fastapi import FastAPI
from fastapi.testclient import TestClient

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
