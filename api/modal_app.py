"""
Modal deployment wrapper for the MathScan API.

WHY Modal specifically, not RunPod or a Hetzner VM (see 11_Cost_Analysis_
MathScan.md section 3.1 for the cost reasoning): Modal can host a real ASGI
app -- our existing FastAPI app, SSE streaming and all -- almost unchanged,
via @modal.asgi_app(). RunPod Serverless is built around a single
input/output "job handler" function, which doesn't naturally fit an app
with multiple REST routes and a streaming response; porting to it would
mean re-architecting routers/ocr.py's SSE endpoints, not just deploying
them. Modal costs slightly more per second of compute, but "reuse what's
already built and tested" is worth more than that difference at this stage.

WHY this is a SEPARATE file from main.py, not a modification of it: main.py
needs to stay a plain, Modal-agnostic FastAPI app so `uvicorn main:app`
still works for local development exactly as documented in
12_Code_Walkthrough_MathScan.md. This file wraps THAT app for Modal without
changing what main.py is or how it's tested (api/tests/test_export.py's
isolated TestClient pattern doesn't need to know Modal exists at all).

Deploy with (from the repo root, after `pip install modal` and `modal setup`
on your own machine -- see the deploy instructions given alongside this
file):

    modal deploy api/modal_app.py

This prints a URL like https://<workspace>--mathscan-api-fastapi-app.modal.run
-- that's the value that goes into the frontend's NEXT_PUBLIC_API_URL.
"""

import modal


def _download_model_weights():
    # WHY this runs as a build step, not left to happen on first request:
    # Pix2Text downloads its model weights from the internet the first time
    # `Pix2Text.from_config()` runs. If that happened on a cold start, every
    # scale-up-from-zero would pay BOTH the ~30s in-memory load time AND a
    # weights download over the network -- slower and less predictable than
    # it needs to be. Running it here, during the image build, bakes the
    # already-downloaded weights into the image itself: every cold start
    # after that only pays the ~30s in-memory load (main.py's lifespan),
    # never a re-download.
    from pix2text import Pix2Text

    Pix2Text.from_config()


# WHY debian_slim + explicit apt/pip installs, not a pre-built ML image:
# keeps the container image matched exactly to what's in requirements.txt
# and what was verified locally -- no surprise version drift from an
# opinionated generic base image.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "poppler-utils",  # pdf2image's real dependency -- pdftoppm/pdfinfo,
        # the same binaries that had to be manually added to PATH on
        # Windows earlier in this project; apt_install handles that here.
        "libfontconfig1",  # Tectonic/XeTeX wants this present even though
        # it bundles its own fonts -- the "Fontconfig error" seen locally
        # was harmless noise for the same reason, but installing this
        # avoids the warning cluttering server logs.
        "curl",
        # WHY these two: pix2text pulls in opencv-python (cv2) through its
        # doc_xl_layout module. opencv's Python wheel is compiled expecting
        # OpenGL and GTK shared libraries to already exist on the system --
        # normal on a desktop, absent from a minimal debian_slim server
        # image. Without these, `import cv2` fails with "libGL.so.1: cannot
        # open shared object file" -- a real error hit building this image,
        # not a hypothetical. These two packages are the actual missing
        # pieces (headless servers running opencv need this exact pair
        # close to universally).
        "libgl1",
        "libglib2.0-0",
        # WHY these three: Tectonic's prebuilt binary dynamically links
        # against Graphite2 (shaping unusual scripts), HarfBuzz (general
        # text shaping), and ICU (Unicode data) -- none of which are
        # present on a minimal debian_slim image by default. A real PDF
        # export crashed with "libgraphite2.so.3: cannot open shared
        # object file" the first time this ran on Modal, exactly the same
        # class of issue as the opencv libraries above, just for a
        # different binary. libicu-dev (rather than a version-numbered
        # runtime-only package like libicu72) is used deliberately: the
        # exact ICU package name changes across Debian releases, but
        # libicu-dev always pulls in whatever the correct runtime .so
        # package is as a dependency, so this doesn't need updating if the
        # base image's Debian version ever changes.
        "libgraphite2-3",
        "libharfbuzz0b",
        "libicu-dev",
    )
    .pip_install_from_requirements("api/requirements.txt")
    .run_function(_download_model_weights)
    # Tectonic ships as one self-contained binary with no apt package on
    # Debian. The official install script (drop-sh.fullyjustified.net) was
    # used first, but it fetches the GNU-target build, which is dynamically
    # linked against glibc and requires a newer glibc (GLIBC_2.38/2.39) than
    # Modal's debian_slim base actually has -- a real crash on a live PDF
    # export: "libc.so.6: version `GLIBC_2.38' not found". Tectonic also
    # publishes a musl-target "semistatic" build specifically so the binary
    # doesn't depend on the host's glibc version at all -- downloading that
    # release asset directly (rather than running the auto-detecting
    # install script) sidesteps the glibc mismatch entirely instead of
    # chasing a matching base-image version.
    .run_commands(
        "curl --proto '=https' --tlsv1.2 -fsSL -o /tmp/tectonic.tar.gz "
        "https://github.com/tectonic-typesetting/tectonic/releases/download/"
        "tectonic%400.16.9/tectonic-0.16.9-x86_64-unknown-linux-musl.tar.gz",
        "tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin",
        "chmod +x /usr/local/bin/tectonic",
        "rm /tmp/tectonic.tar.gz",
    )
    # Copies the actual application code (main.py, inference.py,
    # pdf_preprocessor.py, routers/) into the image. Modal 1.0+ requires
    # local source to be added explicitly -- it's not included by default
    # just because this script lives next to it. Resolved relative to the
    # repo root, same as pip_install_from_requirements above -- this
    # assumes `modal deploy api/modal_app.py` is run from the repo root,
    # not from inside api/ (the deploy instructions say so explicitly).
    #
    # WHY the ignore list: api/.venv is a local Windows virtual environment
    # sitting inside this folder (thousands of files) -- without excluding
    # it, every deploy re-uploads the entire venv even though nothing in
    # the container ever imports from it (dependencies come from
    # pip_install_from_requirements above, not this folder). __pycache__
    # and the test suite are excluded for the same reason: dead weight that
    # never runs inside the deployed container.
    .add_local_dir(
        "api",
        remote_path="/root/api",
        ignore=[".venv", "**/__pycache__", "*.pyc", "tests"],
    )
)

app = modal.App("mathscan-api", image=image)


@app.function(
    # WHY no `gpu=` here, even though the original plan was a T4 (see the
    # comment history / cost analysis 3.1): a real deploy crash traced this
    # exactly. Pix2Text's LatexOCR component runs on ONNX Runtime, and when
    # a GPU is present it tries to use CUDAExecutionProvider -- which
    # requires the `onnxruntime-gpu` package (matched to the container's
    # CUDA version) instead of the plain CPU-only `onnxruntime` that's
    # actually installed via requirements.txt. Every GPU container crashed
    # on startup with "CUDAExecutionProvider ... but the available
    # execution providers are ['CPUExecutionProvider']" -- confirmed from
    # Modal's live container logs, not a guess.
    #
    # Running with no GPU at all sidesteps this entirely, and isn't a new,
    # untested code path: it's the exact same configuration that already
    # worked correctly during local development (the dev machine has no
    # GPU either, so Pix2Text auto-selected CPU there too, without any
    # special handling needed). CPU inference will be slower per page than
    # a working GPU setup would be, but it's a known-working configuration
    # today, and CPU-only Modal billing is also cheaper per second than GPU
    # -- worth revisiting (installing onnxruntime-gpu properly, matched to
    # Modal's CUDA version) in M6 if CPU latency turns out to be a real
    # problem once there's actual usage to measure.
    scaledown_window=300,  # keep a container warm for 5 minutes after its
    # last request, so a burst of back-to-back conversions (a multi-page
    # PDF, or a few students in the same study session) doesn't re-pay the
    # ~30s model load on every single one. Scales fully to $0 after 5
    # idle minutes with nobody using it.
    timeout=120,  # generous ceiling for a slow multi-page job.
)
@modal.concurrent(max_inputs=4)  # let one warm container's event loop
# serve a few overlapping requests instead of forcing a fresh (cold)
# container for every single simultaneous user.
@modal.asgi_app()
def fastapi_app():
    # Imported INSIDE the function, not at module top-level: this code
    # executes inside the Modal container, where /root/api (added above)
    # is where the actual application lives -- the import has to happen
    # after that path is on sys.path, which is only true once this
    # function is actually running inside the deployed container.
    import sys

    sys.path.insert(0, "/root/api")
    from main import app as web_app

    return web_app
