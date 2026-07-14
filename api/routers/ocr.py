"""
OCR endpoints -- SDD 3.2 (endpoint table) + 2.3 (Flow A: PDF, Flow B: images).

Design principle #3 from the SDD, "one input pipeline": a PDF page and an
uploaded image are both just a raster by the time they reach recognize_page().
So both routes below do the same three things -- save upload(s), turn them
into a list of (page_number, image_path), stream results per page over SSE --
they just differ in how they produce that list of images.
"""

import asyncio
import json
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image

from inference import is_loaded, recognize_page
from pdf_preprocessor import EncryptedPDFError, split_pages

router = APIRouter(prefix="/api/ocr", tags=["ocr"])

JOBS_DIR = Path("/tmp/jobs")
MAX_PDF_MB = 25
MAX_PAGES = 50
MAX_IMAGE_MB = 25  # same per-file cap as the PDF endpoint, for consistency


def _sse(payload: dict) -> str:
    # SSE wire format: "data: <json>\n\n". The double newline is not
    # decorative -- it's the field terminator the EventSource spec requires
    # to know one message ended and the next begins.
    return f"data: {json.dumps(payload)}\n\n"


@router.post("/pdf")
async def ocr_pdf(file: UploadFile = File(...), enhance_contrast: bool = Form(False)):
    if not is_loaded():
        raise HTTPException(503, "Model still loading")
    if file.content_type != "application/pdf":
        raise HTTPException(400, "Expected a PDF file")

    raw = await file.read()
    if len(raw) > MAX_PDF_MB * 1024 * 1024:
        raise HTTPException(400, f"PDF exceeds {MAX_PDF_MB}MB limit")

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = job_dir / "input.pdf"
    pdf_path.write_bytes(raw)

    async def stream():
        try:
            try:
                pages = list(split_pages(str(pdf_path), str(job_dir), dpi=200))
            except EncryptedPDFError:
                yield _sse({"error": "Encrypted or unreadable PDF"})
                return

            if len(pages) > MAX_PAGES:
                yield _sse({"error": f"PDF exceeds {MAX_PAGES} page limit"})
                return

            total = len(pages)
            for page_num, page_path in pages:
                image = Image.open(page_path).convert("RGB")
                # WHY asyncio.to_thread: recognize_page() is synchronous,
                # CPU/GPU-bound code. If we `await`ed it directly, it would
                # block the whole event loop -- meaning /api/health and every
                # other in-flight request would freeze until this page
                # finishes. to_thread hands the blocking call to a worker
                # thread so the loop keeps serving other requests. This is
                # the "async-dispatches to inference_service" step in SDD
                # Flow A.
                # try_fallback=True: the live app opts into the PaddleOCR
                # fallback (inference.py's recognize_page docstring explains
                # why it defaults to False -- CI/tests don't have PaddleOCR
                # installed, on purpose). asyncio.to_thread forwards kwargs
                # straight through to recognize_page, same as *args.
                result = await asyncio.to_thread(
                    recognize_page, image, apply_contrast=enhance_contrast, try_fallback=True
                )
                yield _sse({"page": page_num, "total": total, "result": result})
        finally:
            # FR-050: delete uploaded content immediately after inference,
            # no matter how the loop above exits (success, error, or the
            # client disconnecting mid-stream).
            shutil.rmtree(job_dir, ignore_errors=True)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/images")
async def ocr_images(files: list[UploadFile] = File(...), enhance_contrast: bool = Form(False)):
    if not is_loaded():
        raise HTTPException(503, "Model still loading")

    # WHY reject the WHOLE request here, not per-file like the size/type
    # checks below: too many files isn't "this one file is bad," it's "this
    # request itself is asking for too much work" -- same category of
    # protection as MAX_PAGES on the PDF endpoint (SRS scope: <=50
    # pages/images per job either way).
    if len(files) > MAX_PAGES:
        raise HTTPException(400, f"Cannot process more than {MAX_PAGES} images in one request")

    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # WHY read and save every file HERE, before the generator below even
    # starts: an `UploadFile` is only valid to read while FastAPI considers
    # the request "in progress." A `StreamingResponse`'s generator doesn't
    # actually start running until *after* this endpoint function returns
    # (that's what makes it a stream instead of a normal response) -- by
    # then FastAPI has already closed the uploads, so calling `f.read()`
    # inside the generator fails with "I/O operation on closed file." Saving
    # everything to plain disk files up front (same as /pdf already does)
    # sidesteps that entirely: the generator below only ever touches our
    # own saved files, never the original UploadFile objects.
    total = len(files)
    saved_pages: list[tuple[int, str | None, str | None]] = []  # (page_num, path, error)
    for page_num, f in enumerate(files):
        if not f.content_type or not f.content_type.startswith("image/"):
            saved_pages.append((page_num, None, "not an image"))
            continue
        raw = await f.read()
        # WHY check size AFTER reading, not before: there's no way to know
        # an upload's exact size without reading it (UploadFile doesn't
        # expose a reliable size header we can trust) -- but we still check
        # before writing to disk or running the (expensive) OCR model, so a
        # too-large file fails cheaply instead of wasting inference time.
        if len(raw) > MAX_IMAGE_MB * 1024 * 1024:
            saved_pages.append((page_num, None, f"exceeds {MAX_IMAGE_MB}MB limit"))
            continue
        page_path = job_dir / f"page-{page_num}.jpg"
        page_path.write_bytes(raw)
        saved_pages.append((page_num, str(page_path), None))

    async def stream():
        try:
            for page_num, page_path, error in saved_pages:
                if error:
                    yield _sse({"page": page_num, "total": total, "error": error})
                    continue
                image = Image.open(page_path).convert("RGB")
                # try_fallback=True: the live app opts into the PaddleOCR
                # fallback (inference.py's recognize_page docstring explains
                # why it defaults to False -- CI/tests don't have PaddleOCR
                # installed, on purpose). asyncio.to_thread forwards kwargs
                # straight through to recognize_page, same as *args.
                result = await asyncio.to_thread(
                    recognize_page, image, apply_contrast=enhance_contrast, try_fallback=True
                )
                yield _sse({"page": page_num, "total": total, "result": result})
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    return StreamingResponse(stream(), media_type="text/event-stream")
