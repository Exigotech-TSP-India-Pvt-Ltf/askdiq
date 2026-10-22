"""Feedback / RLHF loop admin API.

- GET    /admin/feedback                          enriched review queue (F1, F9)
- GET    /admin/feedback/analytics                trend, distribution, funnel, gaps (F2, F3, F8)
- GET    /admin/feedback/export?format=jsonl      preference pairs, streamed
- GET    /admin/feedback/pairs                    first preference pairs (preview)
- GET    /admin/feedback/golden                   promoted golden cases
- POST   /admin/feedback/golden/{case_id}/run     queue an eval run (deduped)
- POST   /admin/feedback/bulk                     bulk triage, all-or-nothing
- PATCH  /admin/feedback/{feedback_id}            triage transition / note (optimistic)
- POST   /admin/feedback/{message_id}/promote-to-test   DB-backed promotion (F4, F5)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db, get_session_factory, unbounded_session
from app.core.request_meta import get_client_ip
from app.dashboard.range_params import (
    day_params,
    optional_day_range,
    params_days,
    preset_day_ranges,
)
from app.dashboard.snapshots import (
    Gather,
    register,
    schedule_drop_snapshots,
    snapshot_response,
)
from app.dashboard.window import DayRange
from app.entities.conversations import DETAIL_SNAPSHOT_KIND
from app.feedback import analytics, service
from app.feedback import repository as repo
from app.feedback.jobs import enqueue_golden_run
from app.governance.audience import audience_scope, build_audience_filter
from app.governance.audit import record_audit_event
from app.governance.outcomes import Outcome
from app.models.db_models import ChatSession, EvalPromotedCase
from app.schemas.feedback import (
    MAX_PAGE_LIMIT,
    FeedbackAnalytics,
    FeedbackBulkRequest,
    FeedbackBulkResponse,
    FeedbackItem,
    FeedbackPage,
    FeedbackSort,
    FeedbackTriageRequest,
    GoldenCase,
    GoldenCasePage,
    GoldenRunResponse,
    PreferencePair,
    PreferencePairPreview,
    PromoteRequest,
    PromoteResponse,
    TriageStatus,
)

router = APIRouter(prefix="/feedback", tags=["feedback"])

# Stored turn outcomes a rating can sit on: the canonical ones plus the raw
# "empty_llm_output" the orchestrator records for an empty LLM reply.
FeedbackOutcome = Outcome | Literal["empty_llm_output"]

MAX_OFFSET = 10_000
MAX_SEARCH_CHARS = 200
MAX_CURSOR_CHARS = 512
CASE_ID_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"

_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in or missing service key"},
    403: {"description": "Insufficient permissions"},
}
_READ_ERRORS = {**_AUTH_ERRORS, 422: {"description": "Invalid filter or cursor"}}
_WRITE_ERRORS = {
    **_AUTH_ERRORS,
    404: {"description": "Feedback or message not found"},
    409: {"description": "Invalid transition or stale version"},
    422: {"description": "Validation error"},
}


def _naive_utc(value: datetime | None) -> datetime | None:
    """Columns store naive UTC; accept aware input and normalise it."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Reads (dashboard:read)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=FeedbackPage,
    summary="List feedback with full exchange context",
    responses=_READ_ERRORS,
)
async def list_feedback(
    audience: str = Query("overview"),
    rating: int | None = Query(None, ge=1, le=5),
    sort: FeedbackSort = Query(FeedbackSort.CREATED_AT_DESC),
    q: str | None = Query(None, max_length=MAX_SEARCH_CHARS),
    triage_status: TriageStatus | None = None,
    outcome: FeedbackOutcome | None = None,
    grounded: bool | None = None,
    has_comment: bool | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    message_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    cursor: str | None = Query(None, max_length=MAX_CURSOR_CHARS),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> FeedbackPage:
    """Review queue: one row per rating, joined to its question, answer,
    outcome, flags, sources, latency and cost in a single query. Use
    ``next_cursor`` for keyset paging (``offset`` is kept for old clients)."""
    date_from, date_to = _naive_utc(date_from), _naive_utc(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from must be before date_to"
        )
    cursor_values = None
    if cursor:
        try:
            cursor_values = repo.decode_cursor(sort, cursor)
        except repo.InvalidCursorError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid cursor: {exc}"
            ) from None
    aud_cond = await build_audience_filter(db, repo.CLIENT_IP, audience_scope(audience))
    filters = repo.FeedbackFilters(
        audience_cond=aud_cond,
        rating=rating,
        q=q,
        triage_status=triage_status,
        outcome=outcome,
        grounded=grounded,
        has_comment=has_comment,
        date_from=date_from,
        date_to=date_to,
        message_id=message_id,
    )
    rows, total, has_more = await repo.list_feedback(
        db,
        filters,
        sort=sort,
        limit=limit,
        offset=offset,
        cursor_values=cursor_values,
    )
    return FeedbackPage(
        items=[service.to_item(r) for r in rows],
        total=total,
        limit=limit,
        offset=0 if cursor_values is not None else offset,
        next_cursor=repo.encode_cursor(sort, rows[-1]) if has_more and rows else None,
    )


@router.get(
    "/analytics",
    response_model=FeedbackAnalytics,
    response_model_exclude_none=True,
    summary="Feedback KPIs, trend, funnel, disagreement and knowledge gaps",
    responses={**_READ_ERRORS, 304: {"description": "ETag matched"}},
)
async def get_feedback_analytics(
    request: Request,
    audience: str = Query("overview"),
    days: DayRange | None = Depends(optional_day_range),
    runner: ScopedRunner = Depends(get_scoped_runner),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> Response:
    """Aggregates ignore the list filters, so ``total`` stays stable (F3).
    ``by_intent`` is present only once turn insights exist.

    Optional ``from`` / ``to`` (inclusive UTC days) limit every section to
    ratings given in that range; without them totals are all time and the
    trend covers the last ``FEEDBACK_TREND_DAYS``. Precomputed per audience
    and range (``dashboard_snapshots``); ``X-Data-As-Of`` is its age."""
    params = {"audience": audience_scope(audience), **day_params(days)}
    return await snapshot_response(
        request,
        FEEDBACK_ANALYTICS_KIND,
        params,
        runner.gather,
        lambda gather: _feedback_analytics(gather, params, session_factory),
    )


FEEDBACK_ANALYTICS_KIND = "feedback.analytics"


def _feedback_changed() -> None:
    """A triage / promotion changed the counts: drop the analytics snapshots
    (the next read recomputes them; the live stream notifies open consoles).
    Conversation details show each turn's ratings and promoted case."""
    schedule_drop_snapshots(FEEDBACK_ANALYTICS_KIND)
    schedule_drop_snapshots(DETAIL_SNAPSHOT_KIND)


async def _feedback_analytics_snapshot(gather: Gather, params: dict) -> dict:
    return await _feedback_analytics(gather, params, get_session_factory())


async def _feedback_analytics(
    gather: Gather,
    params: dict,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict:
    scope = params["audience"]

    async def filters(db: AsyncSession) -> tuple[Any, Any]:
        return (
            await build_audience_filter(db, repo.CLIENT_IP, scope),
            await build_audience_filter(db, ChatSession.created_ip, scope),
        )

    ((aud_feedback, aud_queries),) = await gather(filters)
    days = params_days(params)
    return await analytics.compute_analytics(
        aud_feedback,
        aud_queries,
        session_factory,
        window=(days.start, days.end) if days else None,
    )


def _feedback_analytics_defaults() -> list[dict]:
    """The Feedback page's views: each audience, all time and every preset."""
    return [
        {"audience": audience, **day_params(days)}
        for audience in ("overview", "users", "testing")
        for days in preset_day_ranges()
    ]


register(
    FEEDBACK_ANALYTICS_KIND, _feedback_analytics_snapshot, _feedback_analytics_defaults
)


@router.get(
    "/golden",
    response_model=GoldenCasePage,
    summary="List golden cases promoted from feedback",
    responses=_READ_ERRORS,
)
async def list_golden_cases(
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    days: DayRange | None = Depends(optional_day_range),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> GoldenCasePage:
    """Optional ``from`` / ``to`` (inclusive UTC days) keep cases promoted then."""
    rows, total = await repo.list_promoted_cases(
        db, limit, offset, window=(days.start, days.end) if days else None
    )
    return GoldenCasePage(
        items=[_golden(r) for r in rows], total=total, limit=limit, offset=offset
    )


def _golden(r: EvalPromotedCase) -> GoldenCase:
    return GoldenCase(
        id=r.id,
        query=r.query,
        case_type=r.case_type,
        expect_grounded=r.expect_grounded,
        expected_outcome=r.expected_outcome,
        source_message_id=str(r.source_message_id) if r.source_message_id else None,
        feedback_id=str(r.feedback_id) if r.feedback_id else None,
        reference_answer=r.reference_answer,
        reason_tags=list(r.reason_tags or []),
        promoted_by=r.promoted_by,
        promoted_at=r.promoted_at,
        last_run_at=r.last_run_at,
        last_run_passed=r.last_run_passed,
        last_run_detail=r.last_run_detail,
    )


# ---------------------------------------------------------------------------
# Export (feedback:export)
# ---------------------------------------------------------------------------

MAX_PREVIEW_PAIRS = 100


@router.get(
    "/pairs",
    response_model=PreferencePairPreview,
    summary="Preview the first preference pairs of the export",
    responses=_READ_ERRORS,
)
async def preview_feedback_pairs(
    limit: int = Query(50, ge=1, le=MAX_PREVIEW_PAIRS),
    complete_only: bool = False,
    days: DayRange | None = Depends(optional_day_range),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.FEEDBACK_EXPORT)),
) -> PreferencePairPreview:
    """The first ``limit`` pairs of ``GET /export`` with the same defaults
    (oldest first, dismissed excluded, PII-masked), as one bounded JSON
    page. Optional ``from`` / ``to`` (inclusive UTC days)."""
    pairs, truncated = await service.preview_pairs(
        db,
        complete_only=complete_only,
        limit=limit,
        window=(days.start, days.end) if days else None,
    )
    return PreferencePairPreview(
        pairs=[PreferencePair(**p) for p in pairs], truncated=truncated
    )


@router.get(
    "/export",
    summary="Stream preference pairs as JSONL",
    response_class=StreamingResponse,
    responses={
        **_READ_ERRORS,
        200: {
            "description": "One JSON object per line: prompt, chosen, rejected, "
            "rating, reason_tags, source_ids, triage_status, created_at, "
            "complete, missing",
            "content": {"application/x-ndjson": {}},
        },
    },
)
async def export_feedback_pairs(
    request: Request,
    format: Literal["jsonl"] = Query("jsonl"),  # noqa: A002 - public query name
    triage_status: TriageStatus | None = None,
    include_dismissed: bool = False,
    complete_only: bool = False,
    days: DayRange | None = Depends(optional_day_range),
    db: AsyncSession = Depends(unbounded_session),  # long streamed export: no API cap
    user: AuthUser = Depends(require_permission(Permission.FEEDBACK_EXPORT)),
) -> StreamingResponse:
    """Pairs are streamed off a server-side cursor (flat memory). Prompt and
    answer text are PII-masked; incomplete pairs carry ``complete: false``.
    Optional ``from`` / ``to`` (inclusive UTC days) keep ratings given then."""
    await record_audit_event(
        db,
        request_id=f"feedback-export-{uuid.uuid4().hex[:12]}",
        actor=user.email,
        action="feedback_exported",
        detail={
            "format": format,
            "triage_status": triage_status.value if triage_status else None,
            "include_dismissed": include_dismissed,
            "complete_only": complete_only,
        },
        ip_address=get_client_ip(request),
        commit=True,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        service.export_pairs_jsonl(
            db,
            triage_status=triage_status,
            include_dismissed=include_dismissed,
            complete_only=complete_only,
            batch_size=get_settings().feedback_export_batch_size,
            window=(days.start, days.end) if days else None,
        ),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="feedback_pairs_{stamp}.jsonl"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Triage writes (feedback:triage)
# ---------------------------------------------------------------------------


@router.post(
    "/golden/{case_id}/run",
    response_model=GoldenRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an evaluation run of one golden case",
    responses={**_AUTH_ERRORS, 404: {"description": "Case not found"}},
)
async def run_golden_case(
    case_id: str = Path(..., pattern=CASE_ID_PATTERN),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.FEEDBACK_TRIAGE)),
) -> GoldenRunResponse:
    """Idempotent: while a run for the case is queued or running, another
    request returns ``already_queued`` instead of adding a second job."""
    if await db.get(EvalPromotedCase, case_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Golden case not found")
    job_id = await enqueue_golden_run(case_id, user.email)
    await record_audit_event(
        db,
        request_id=f"golden-run-{uuid.uuid4().hex[:12]}",
        actor=user.email,
        action="golden_case_run_requested",
        detail={"case_id": case_id, "job_id": str(job_id) if job_id else None},
        commit=True,
    )
    return GoldenRunResponse(
        case_id=case_id,
        status="queued" if job_id else "already_queued",
        job_id=str(job_id) if job_id else None,
    )


@router.post(
    "/bulk",
    response_model=FeedbackBulkResponse,
    summary="Mark up to 200 feedback rows reviewed, dismissed or promoted",
    responses=_WRITE_ERRORS,
)
async def bulk_triage_feedback(
    body: FeedbackBulkRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.FEEDBACK_TRIAGE)),
) -> FeedbackBulkResponse:
    """One transaction: if any id is missing (404) or cannot make the
    transition (409), nothing is changed."""
    result = await service.bulk_triage(db, body, user.email)
    _feedback_changed()
    return result


@router.patch(
    "/{feedback_id}",
    response_model=FeedbackItem,
    summary="Triage one feedback row (status transition and/or note)",
    responses=_WRITE_ERRORS,
)
async def triage_feedback(
    feedback_id: uuid.UUID,
    body: FeedbackTriageRequest,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.FEEDBACK_TRIAGE)),
) -> FeedbackItem:
    """Transitions: new -> reviewed | promoted | dismissed; reviewed ->
    promoted | dismissed; dismissed -> new. Send the ``version`` you read;
    a stale version returns 409."""
    result = await service.triage(db, feedback_id, body, user.email)
    _feedback_changed()
    return result


@router.post(
    "/{message_id}/promote-to-test",
    response_model=PromoteResponse,
    summary="Promote a rated answer to a golden regression case",
    responses={**_WRITE_ERRORS, 400: {"description": "Invalid message id"}},
)
async def promote_feedback_to_test(
    message_id: str,
    body: PromoteRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.FEEDBACK_TRIAGE)),
) -> PromoteResponse:
    """Writes the case to ``eval_promoted_cases`` (durable) and marks the
    message's open ratings as promoted. Repeat calls return the same case."""
    try:
        mid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid message ID format"
        ) from None
    result = await service.promote_message(
        db, mid, user.email, body.review_note if body else None
    )
    _feedback_changed()
    return result
