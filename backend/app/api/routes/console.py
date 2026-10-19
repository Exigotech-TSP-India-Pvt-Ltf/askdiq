"""Console-wide live updates (``GET /admin/live``, Server-Sent Events)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.sse import event_stream
from app.dashboard.live import console_events

router = APIRouter(prefix="/admin", tags=["Console"])

read_guard = require_permission(Permission.DASHBOARD_READ)


@router.get(
    "/live",
    summary="Console live updates (SSE)",
    description=(
        "`text/event-stream`: `ready` on connect, then `changed` frames "
        '`{"scopes": [...], "at": "<ISO time>"}` naming what changed: `data` '
        "(new chats, feedback, evaluations), `alerts`, or a precomputed "
        "aggregate kind (for example `usage.summary`). Events within "
        "`CONSOLE_STREAM_COALESCE_SECONDS` share one frame. Only names are "
        "sent; clients refetch the affected views."
    ),
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "SSE stream."},
        401: {"description": "Not signed in."},
        403: {"description": "Missing dashboard:read."},
    },
)
async def console_live(
    request: Request,
    user: AuthUser = Depends(read_guard),
) -> StreamingResponse:
    return event_stream(request, console_events())
