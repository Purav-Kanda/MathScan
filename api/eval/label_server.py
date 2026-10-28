"""
Tiny local server for the labelling tool (label_tool.html).

WHY a server and not a plain HTML file: a page opened from disk cannot write
files, so typed labels would only live in the browser until someone remembered
to download them. This server saves labels.json on every change, writes it
atomically (temp file, then rename) so a crash cannot leave half a file, and
reloads it on the next start so progress survives closing the browser.

It listens on 127.0.0.1 only, serves just the crops folder and the tool page,
and accepts only well-formed label data for crops that exist.

Run from api/:  python eval/label_server.py
Then open:      http://127.0.0.1:8765
"""

import argparse
import json
import os
import re
import shutil
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL_PATH = HERE / "label_tool.html"
CROP_NAME = re.compile(r"^[A-Za-z0-9_\-]+\.png$")
MAX_BODY_BYTES = 5 * 1024 * 1024


def natural_key(name: str) -> list:
    """Sort 'p15' before 'p100': split digit runs out and compare them as numbers."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def list_crops(crops_dir: Path) -> list:
    return sorted((p.name for p in crops_dir.glob("*.png")), key=natural_key)


def read_labels(labels_path: Path) -> dict:
    if not labels_path.exists():
        return {}
    return json.loads(labels_path.read_text(encoding="utf-8"))


def validate_labels(data, known_crops: set) -> str | None:
    """Return an error message, or None if `data` is a valid labels mapping."""
    if not isinstance(data, dict):
        return "labels must be a JSON object"
    for name, entry in data.items():
        if name not in known_crops:
            return f"unknown crop: {name}"
        if not isinstance(entry, dict) or not isinstance(entry.get("latex"), str):
            return f"bad entry for {name}"
        if not isinstance(entry.get("skip"), bool):
            return f"bad skip flag for {name}"
    return None


def write_labels_atomically(labels_path: Path, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=labels_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_name, labels_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def make_handler(label_dir: Path):
    crops_dir = label_dir / "crops"
    labels_path = label_dir / "labels.json"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep the terminal quiet; errors are returned to the page

        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload):
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, TOOL_PATH.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/crops":
                self._send_json(200, list_crops(crops_dir))
            elif path == "/api/labels":
                try:
                    self._send_json(200, read_labels(labels_path))
                except (OSError, ValueError) as exc:
                    self._send_json(500, {"error": f"could not read labels.json: {exc}"})
            elif path.startswith("/crops/"):
                name = path[len("/crops/") :]
                target = crops_dir / name
                if CROP_NAME.match(name) and target.is_file():
                    self._send(200, target.read_bytes(), "image/png")
                else:
                    self._send_json(404, {"error": "no such crop"})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/labels":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "body missing or too large"})
                return
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self._send_json(400, {"error": "not valid JSON"})
                return
            problem = validate_labels(data, set(list_crops(crops_dir)))
            if problem:
                self._send_json(400, {"error": problem})
                return
            try:
                write_labels_atomically(labels_path, data)
            except OSError as exc:
                self._send_json(500, {"error": f"could not write labels.json: {exc}"})
                return
            self._send_json(200, {"ok": True, "entries": len(data)})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dir", type=Path, default=HERE / "label_set", help="folder holding crops/ and labels.json"
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    crops_dir = args.dir / "crops"
    if not crops_dir.is_dir() or not list_crops(crops_dir):
        raise SystemExit(f"no crops in {crops_dir}. Run: python eval/make_label_kit.py")

    labels_path = args.dir / "labels.json"
    if labels_path.exists():
        # One safety copy per server start, in case a bad session ever overwrites good work.
        shutil.copyfile(labels_path, args.dir / "labels.start-backup.json")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.dir))
    print(f"labelling tool: http://127.0.0.1:{args.port}   (Ctrl+C to stop)")
    print(f"saving to:      {labels_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
