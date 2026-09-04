"""HTTP routes. Every /v1 route requires the caller's ABS Bearer token."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .abs import AbsError, AbsNotFound
from .epub import EpubExtractionError
from .kokoro import KokoroError
from .playlist import render_playlist
from .session import ITEM_ID_PATTERN, VOICE_PATTERN, SessionManager, UnknownSessionError

log = logging.getLogger("narrator.routes")
router = APIRouter()

UPSTREAM_ERRORS = (AbsError, KokoroError, EpubExtractionError)


def _manager(request: Request) -> SessionManager:
    return request.app.state.sessions


async def _require_user(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
    if not token or not await request.app.state.abs.validate_user_token(token):
        raise HTTPException(
            status_code=401, detail="Bearer token of the narrator's ABS user required"
        )


Manager = Annotated[SessionManager, Depends(_manager)]
UserAuth = Depends(_require_user)


class CreateSessionRequest(BaseModel):
    model_config = {"populate_by_name": True}

    itemId: str = Field(pattern=ITEM_ID_PATTERN)
    voice: str | None = Field(default=None, pattern=VOICE_PATTERN)
    from_: float | None = Field(default=None, alias="from", ge=0.0, le=1.0)


class ProgressRequest(BaseModel):
    positionSec: float = Field(ge=0.0)
    flush: bool = False  # bypass the ABS write throttle (pause, seek, close)


@router.get("/healthz")
async def healthz(manager: Manager) -> dict:
    return await manager.health()


@router.post("/v1/sessions", status_code=201, dependencies=[UserAuth])
async def create_session(body: CreateSessionRequest, manager: Manager) -> dict:
    try:
        return await manager.create_session(body.itemId, body.voice, body.from_)
    except AbsNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except EpubExtractionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (AbsError, KokoroError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/v1/sessions/{session_id}/playlist.m3u8", dependencies=[UserAuth])
async def playlist(
    session_id: str,
    manager: Manager,
    block_msn: Annotated[int | None, Query(alias="_HLS_msn", ge=0)] = None,
) -> PlainTextResponse:
    try:
        start_segment, durations, ended = await manager.playlist(session_id, block_msn)
    except UnknownSessionError as e:
        raise HTTPException(status_code=404, detail="unknown session") from e
    except UPSTREAM_ERRORS as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not durations:
        raise HTTPException(status_code=503, detail="first segments still synthesizing")
    return PlainTextResponse(
        render_playlist(start_segment, durations, ended),
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/v1/sessions/{session_id}/seg/{index}.mp3", dependencies=[UserAuth])
async def segment(session_id: str, index: int, manager: Manager) -> FileResponse:
    try:
        path = await manager.segment(session_id, index)
    except UnknownSessionError as e:
        raise HTTPException(status_code=404, detail="unknown session or segment") from e
    except UPSTREAM_ERRORS as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/v1/sessions/{session_id}/progress", dependencies=[UserAuth])
async def progress(session_id: str, body: ProgressRequest, manager: Manager) -> dict:
    try:
        percent = await manager.progress(session_id, body.positionSec, flush=body.flush)
    except UnknownSessionError as e:
        raise HTTPException(status_code=404, detail="unknown session") from e
    except UPSTREAM_ERRORS as e:
        log.warning("progress write failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"bookPercent": percent}


@router.delete("/v1/sessions/{session_id}", status_code=204, dependencies=[UserAuth])
async def close_session(session_id: str, manager: Manager) -> None:
    await manager.close(session_id)
