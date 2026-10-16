"""Chat session management: list/create/rename/delete sessions, plus
deleting a single exchange within one. Conversation history itself lives
in ChatMessage rows and is loaded automatically by /query — see
app.core.sessions for the shared lifecycle logic.
"""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_role_or_chat
from app.auth.principal import AuthUser
from app.core import sessions as session_service
from app.core.database import get_db
from app.core.rate_limit import aenforce_feedback_rate_limit
from app.core.request_meta import resolve_client_ip
from app.schemas.session import (
    ChatSessionDetail,
    ChatSessionSummary,
    CreateSessionRequest,
    RenameSessionRequest,
    UserFeedbackRequest,
    UserFeedbackResponse,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[ChatSessionSummary])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> list[ChatSessionSummary]:
    return await session_service.list_sessions(db, user.email)


@router.post("", response_model=ChatSessionSummary, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> ChatSessionSummary:
    return await session_service.create_session(
        db,
        user.email,
        body.title,
        client_ip=resolve_client_ip(request, body.ip_address),
    )


@router.get("/{session_id}", response_model=ChatSessionDetail)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> ChatSessionDetail:
    return await session_service.get_session_detail(db, session_id, user.email)


@router.patch("/{session_id}", response_model=ChatSessionSummary)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> ChatSessionSummary:
    return await session_service.rename_session(db, session_id, user.email, body.title)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> None:
    await session_service.delete_session(db, session_id, user.email)


@router.delete(
    "/{session_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_exchange(
    session_id: str,
    message_id: str,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> None:
    await session_service.delete_exchange(db, session_id, message_id, user.email)


@router.post(
    "/{session_id}/messages/{message_id}/feedback",
    response_model=UserFeedbackResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_feedback(
    request: Request,
    session_id: str,
    message_id: str,
    body: UserFeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> UserFeedbackResponse:
    """Submit or update a rating for an assistant turn; one row per (message, user).

    Optional ``reason_tags`` (allow-listed) and ``corrected_answer`` (max 4,000
    characters, PII-masked before storage) feed the RLHF review queue."""
    client_ip = resolve_client_ip(request, body.ip_address)
    await aenforce_feedback_rate_limit(user.email, client_ip, limit=30)
    req_id = getattr(request.state, "request_id", None)

    return await session_service.record_feedback(
        db,
        session_id=session_id,
        message_id=message_id,
        actor=user.email,
        rating=body.rating,
        comment=body.comment,
        client_ip=client_ip,
        request_id=req_id,
        reason_tags=[t.value for t in body.reason_tags],
        corrected_answer=body.corrected_answer,
    )
