"""
Share link endpoints (M5).

WHY this exists at all, given M5 deliberately chose browser-only history
over real accounts (see 12_Code_Walkthrough_MathScan.md / ROADMAP.md M5
scope): a share link is the one piece of state that DOES have to live on
the server, because by definition it needs to be opened by someone who
isn't the person who ran the conversion -- localStorage never leaves their
device. Everything else in M5 stays anonymous and account-free; this is a
narrow, purpose-built exception, not a first step toward a database-backed
history system.

Stores full region data (latex/type/confidence per region), not the final
compiled export text -- so the page someone lands on via a share link looks
like the real app (editable regions, live preview, confidence badges), not
a flattened document.
"""

import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from share_store import get_share, save_share

router = APIRouter(prefix="/api/share", tags=["share"])


class ShareRegion(BaseModel):
    latex: str
    type: str
    confidence: float | None = None


class SharePage(BaseModel):
    page: int
    regions: list[ShareRegion]


class ShareRequest(BaseModel):
    pages: list[SharePage]


@router.post("")
async def create_share(payload: ShareRequest):
    # WHY 4 random bytes (8 hex chars) from `secrets`, not a sequential
    # counter or `random`: a share ID being guessable by incrementing
    # (share/1, share/2, ...) would let anyone browse other people's
    # conversions just by trying numbers. `secrets` draws from a
    # cryptographically secure source specifically so an ID can't be
    # predicted or enumerated -- `random` is not safe for this, even though
    # it "looks" random too.
    share_id = secrets.token_hex(4)
    save_share(share_id, payload.model_dump())
    return {"id": share_id}


@router.get("/{share_id}")
async def read_share(share_id: str):
    data = get_share(share_id)
    if data is None:
        raise HTTPException(404, "This share link doesn't exist or has expired")
    return data
