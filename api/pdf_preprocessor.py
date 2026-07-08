"""
PDF -> per-page JPEG splitting.

WHY one page at a time, not `convert_from_path(pdf)` for every page in one
call: SDD 3.3 requires we never hold more than one page's raster in memory.
A 50-page PDF at 200 DPI can be 20-30MB per page as raw pixels; rendering
all 50 up front risks OOM on a small VM. Pinning first_page == last_page
per call costs a little re-invocation overhead in exchange for a hard
memory ceiling -- a fine trade on a $30/month box.
"""

from pathlib import Path
from typing import Iterator, Tuple

from pdf2image import convert_from_path
from pdf2image.exceptions import PDFPageCountError
from pdf2image.pdf2image import pdfinfo_from_path


class EncryptedPDFError(Exception):
    """Raised when the PDF can't be read -- encrypted, corrupt, or not a PDF."""


def get_page_count(pdf_path: str) -> int:
    try:
        info = pdfinfo_from_path(pdf_path)
    except PDFPageCountError as e:
        # pdfinfo fails the same way for "encrypted" and "corrupt" -- from
        # the API's perspective both are "can't process this file," so we
        # collapse them into one error type (FR-006: clear rejection message).
        raise EncryptedPDFError("PDF is encrypted, corrupt, or unreadable") from e
    return info["Pages"]


def split_pages(pdf_path: str, out_dir: str, dpi: int = 200) -> Iterator[Tuple[int, str]]:
    """
    Yields (zero_indexed_page_number, jpeg_path) one page at a time.
    A generator (not a list) so the caller -- the SSE stream in routers/ocr.py --
    can push progress to the client as each page finishes, instead of waiting
    for all 50 pages to render before sending anything.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n_pages = get_page_count(pdf_path)

    for page_num in range(1, n_pages + 1):
        images = convert_from_path(pdf_path, dpi=dpi, first_page=page_num, last_page=page_num)
        image = images[0]
        page_path = out / f"page-{page_num - 1}.jpg"
        image.save(page_path, "JPEG", quality=90)
        yield page_num - 1, str(page_path)
