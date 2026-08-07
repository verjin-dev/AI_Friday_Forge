from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.logging import get_logger
from app.core.models import ChatRequest, ChatResponse
from app.security.rbac import ROLES
from app.workflow.runner import run_workflow, stream_workflow


logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/roles")
async def list_roles() -> list[dict]:
    return [
        {
            "name": role.name,
            "description": role.description,
            "tools": sorted(role.tools),
            "can_see_pii": role.can_see_pii,
        }
        for role in ROLES.values()
    ]


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Run the full multi-agent workflow and return the assembled response."""

    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    return await run_workflow(
        request.message,
        session_id=request.session_id,
        role=request.role,
        history=request.history,
    )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Server-sent events carrying the live agent timeline, then the response."""

    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    async def event_source() -> AsyncIterator[bytes]:
        try:
            async for event in stream_workflow(
                request.message,
                session_id=request.session_id,
                role=request.role,
                history=request.history,
            ):
                payload = json.dumps(event, default=str)
                yield f"data: {payload}\n\n".encode("utf-8")
        except Exception as exc:  # noqa: BLE001 - the stream must always terminate
            logger.exception("Chat stream failed")
            error = json.dumps({"event": "error", "message": str(exc)[:500]})
            yield f"data: {error}\n\n".encode("utf-8")

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stop nginx buffering the timeline into one burst.
            "X-Accel-Buffering": "no",
        },
    )
