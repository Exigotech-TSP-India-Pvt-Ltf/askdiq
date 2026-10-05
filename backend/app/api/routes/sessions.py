"""Chat session management: list/create/rename/delete sessions, plus
deleting a single exchange within one. Conversation history itself lives
in ChatMessage rows and is loaded automatically by /query — see
app.core.sessions for the shared lifecycle logic.
"""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import sessions as session_service
from app.core.database import get_db
from app.core.request_meta import get_client_ip
from app.governance.auth import get_current_actor
from app.schemas.session import (
    ChatSessionDetail,
    ChatSessionSummary,
    CreateSessionRequest,
    RenameSessionRequest,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[ChatSessionSummary])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> list[ChatSessionSummary]:
    return await session_service.list_sessions(db, actor)


@router.post("", response_model=ChatSessionSummary, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> ChatSessionSummary:
    return await session_service.create_session(
        db, actor, body.title, client_ip=get_client_ip(request)
    )


@router.get("/{session_id}", response_model=ChatSessionDetail)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> ChatSessionDetail:
    return await session_service.get_session_detail(db, session_id, actor)


@router.patch("/{session_id}", response_model=ChatSessionSummary)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> ChatSessionSummary:
    return await session_service.rename_session(db, session_id, actor, body.title)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> None:
    await session_service.delete_session(db, session_id, actor)


@router.delete(
    "/{session_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_exchange(
    session_id: str,
    message_id: str,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(get_current_actor),
) -> None:
    await session_service.delete_exchange(db, session_id, message_id, actor)
