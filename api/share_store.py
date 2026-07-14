"""
Storage for share links (M5).

WHY this needs real shared persistence, not just a local dict or disk file:
Modal containers are ephemeral (scale to zero, api/modal_app.py's
scaledown_window) and more than one container can be running at once
(@modal.concurrent(max_inputs=4), or a fresh cold-start container spinning
up under load). Data saved in one container's memory, or written to its
local disk, is invisible to a different container that later handles the
GET request for the same share link -- a real problem for something whose
entire point is "someone else, later, opens this link." modal.Dict is
Modal's own distributed key-value store, built for exactly this "small
piece of state that needs to survive across containers and cold starts"
case -- the natural choice given this project is already committed to
Modal, without standing up a separate database for one small feature.

WHY this is a separate module, not imported directly in main.py: main.py
needs to stay Modal-agnostic (see modal_app.py's docstring) so local dev
via `uvicorn main:app` still works without a Modal account configured. This
module tries modal.Dict first and falls back to a plain in-memory dict if
that fails for any reason (no `modal` package, not logged in, offline) --
so local development works out of the box, it just won't persist a share
link across a server restart, which is an acceptable tradeoff for dev.
"""

from typing import Optional

try:
    import modal

    _store = modal.Dict.from_name("mathscan-shares", create_if_missing=True)
except Exception:
    _store = {}  # local-dev fallback: in-memory, cleared on every restart


def save_share(share_id: str, data: dict) -> None:
    _store[share_id] = data


def get_share(share_id: str) -> Optional[dict]:
    try:
        return _store[share_id]
    except KeyError:
        return None
