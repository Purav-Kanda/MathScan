"""
Real (not mocked) tests for pdf_preprocessor.py -- these generate actual PDF
files at test time using PyMuPDF (already installed as a Pix2Text dependency)
and run them through the real pdf2image/Poppler pipeline. This is an
integration test, not a pure unit test: it requires Poppler to be installed
and on PATH (same requirement as running the app itself), but that's the
right tradeoff here -- pdf_preprocessor.py's entire job is "call Poppler
correctly," so a test that never actually calls Poppler wouldn't prove much.

Run from api/: pytest
"""

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from pdf_preprocessor import EncryptedPDFError, get_page_count, split_pages


def make_pdf(path, page_count: int = 1) -> None:
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page()
        page.insert_text((72, 72), f"Test page {i + 1}")
    doc.save(str(path))
    doc.close()


def make_encrypted_pdf(path) -> None:
    doc = fitz.open()
    doc.new_page()
    # WHY AES-256 + both passwords set: this is what actually produces a
    # PDF Poppler's pdfinfo refuses to read without a password -- matching
    # the real-world case FR-006 exists for (a student's scanner app
    # accidentally producing, or intentionally using, a protected PDF).
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )
    doc.close()


def test_get_page_count_on_real_pdf(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    make_pdf(pdf_path, page_count=3)

    assert get_page_count(str(pdf_path)) == 3


def test_get_page_count_raises_on_encrypted_pdf(tmp_path):
    pdf_path = tmp_path / "encrypted.pdf"
    make_encrypted_pdf(pdf_path)

    # This is the actual FR-006 behavior under test: an encrypted PDF must
    # raise our specific error type, not crash with Poppler's raw exception
    # or (worse) silently return a wrong page count.
    with pytest.raises(EncryptedPDFError):
        get_page_count(str(pdf_path))


def test_split_pages_produces_one_jpeg_per_page(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    make_pdf(pdf_path, page_count=2)
    out_dir = tmp_path / "pages"

    results = list(split_pages(str(pdf_path), str(out_dir), dpi=100))

    assert len(results) == 2
    # Zero-indexed page numbers, per split_pages' own docstring.
    assert [page_num for page_num, _ in results] == [0, 1]
    for _, page_path in results:
        assert Path(page_path).exists()


def test_split_pages_raises_on_encrypted_pdf(tmp_path):
    pdf_path = tmp_path / "encrypted.pdf"
    make_encrypted_pdf(pdf_path)

    with pytest.raises(EncryptedPDFError):
        list(split_pages(str(pdf_path), str(tmp_path / "pages")))
