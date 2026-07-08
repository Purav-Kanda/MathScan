"""
MathScan inference API entrypoint.

M0 proved Pix2Text could load and return LaTeX for one image
(kept as /api/ocr/test below). M1 adds the real multi-page pipeline:
/api/ocr/pdf and /api/ocr/images, both SSE-streamed (see routers/ocr.py).
"""

from contextlib import asynccontextmanager
from io import BytesIO

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image

import inference
from routers.ocr import router as ocr_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # WHY load here, once, at process startup -- not inside a request
    # handler: Pix2Text-MFR takes ~30s to load its weights (SDD 3.4 "Cold
    # start"). Loading once means every request after boot reuses the same
    # in-memory model. /api/health's `model_loaded` flag exists specifically
    # so infra doesn't route traffic during that ~30s window.
    print("Loading Pix2Text-MFR... (~30s)")
    inference.load_model()
    print("Model loaded.")
    yield


app = FastAPI(title="MathScan Inference API", lifespan=lifespan)
app.include_router(ocr_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "model_loaded": inference.is_loaded()}


@app.post("/api/ocr/test")
async def ocr_test(file: UploadFile = File(...)):
    """
    M0-era single-image smoke test endpoint. Kept around because it's the
    fastest way to sanity-check "is the model actually working" without
    dealing with SSE or multi-page plumbing. Real clients use /api/ocr/pdf
    or /api/ocr/images (routers/ocr.py).
    """
    if not inference.is_loaded():
        raise HTTPException(status_code=503, detail="Model still loading")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload an image file")

    raw = await file.read()
    image = Image.open(BytesIO(raw)).convert("RGB")
    result = inference.recognize_page(image)
    return {"filename": file.filename, **result}
