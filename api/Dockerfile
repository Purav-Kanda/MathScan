# M0: CPU-only image for local dev. M4 swaps the base image for a CUDA one
# on the Hetzner GPU box -- app code doesn't change, only this file does.
FROM python:3.11-slim

WORKDIR /app

# libglib2.0-0/libgl1: OpenCV (a Pix2Text dependency) needs these even
# headless. poppler-utils: gives pdf2image the pdftoppm/pdfinfo binaries
# it shells out to -- pdf2image is a thin wrapper around Poppler, not a
# pure-Python PDF renderer.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# --reload only matters in dev; M4's compose file overrides this command
# for production (no reload, more workers, no bind-mounted source).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
