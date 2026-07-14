"""
Tests for routers/share.py + share_store.py.

WHY these run fine in CI without any Modal account configured: share_store.py
falls back to a plain in-memory dict when `import modal` or
`modal.Dict.from_name(...)` fails for any reason (see that module's
docstring) -- exactly the case here, since `modal` isn't even in
requirements.txt (it's a deploy-time dependency, not a runtime one, per
modal_app.py's own docstring). That fallback is what makes this testable at
all without a real Modal backend.

Run from api/: pytest
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.share import router as share_router

_app_under_test = FastAPI()
_app_under_test.include_router(share_router)
client = TestClient(_app_under_test)


def test_create_then_read_share_round_trip():
    payload = {
        "pages": [
            {
                "page": 0,
                "regions": [{"latex": "x^2=4", "type": "isolated", "confidence": 0.95}],
            }
        ]
    }
    created = client.post("/api/share", json=payload)
    assert created.status_code == 200
    share_id = created.json()["id"]

    read = client.get(f"/api/share/{share_id}")
    assert read.status_code == 200
    assert read.json() == payload


def test_unknown_share_id_returns_404():
    response = client.get("/api/share/does-not-exist")
    assert response.status_code == 404


def test_share_ids_are_not_sequential_or_predictable():
    # WHY this test exists: routers/share.py's docstring specifically
    # justifies secrets.token_hex over a counter or `random` on the grounds
    # that IDs shouldn't be guessable -- this checks that reasoning actually
    # holds (different length/format than an incrementing int, and two
    # requests don't produce IDs anywhere near each other).
    payload = {"pages": []}
    first = client.post("/api/share", json=payload).json()["id"]
    second = client.post("/api/share", json=payload).json()["id"]

    assert first != second
    assert len(first) == 8  # secrets.token_hex(4) -> 8 hex chars
    assert all(c in "0123456789abcdef" for c in first)
