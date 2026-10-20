"""``/admin/insights/*``: Analytics (Chatbot Insights) endpoints (newplan 6.3, 6.4).

Every read route needs ``dashboard:read``; taxonomy writes need
``taxonomy:write`` (superadmin). The aggregation happens server-side and
reproduces the reference HTML's renderer formulas.
"""

import uuid
from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.concurrency import ScopedRunner, get_scoped_runner
from app.core.config import get_settings
from app.core.database import get_db, read_only_session
from app.core.exceptions import get_request_id
from app.core.http_cache import cached_response
from app.core.request_meta import get_client_ip
from app.dashboard.snapshots import Gather, register, snapshot_response
from app.insights.analytics_service import InsightsAnalyticsService, ParallelRunner
from app.insights.bundle_service import (
    BundleRequest,
    InsightsBundleService,
    normalise_parts,
)
from app.insights.taxonomy import TaxonomyKind
from app.insights.taxonomy_service import AuditContext, TaxonomyError, TaxonomyService
from app.schemas.insights import (
    CODE_PATTERN,
    DEFAULT_PAGE_SIZE,
    MAX_OFFSET,
    MAX_PAGE_SIZE,
    MAX_SEARCH_CHARS,
    PERSON_PATTERN,
    Audience,
    BundlePart,
    ConversationDetail,
    ConversationList,
    ConversationOutcome,
    ConversationPage,
    ConversationSort,
    ExperienceRating,
    InsightsBundle,
    InsightsDropoff,
    InsightsFilters,
    InsightsFlow,
    InsightsHourly,
    InsightsPeople,
    InsightsQuality,
    InsightsSummary,
    InsightsTopics,
    InsightsTrend,
    PendingClassification,
    Period,
    PersonDetail,
    SortDirection,
    TaxonomyCreate,
    TaxonomyList,
    TaxonomyRead,
    TaxonomyUpdate,
)

router = APIRouter(prefix="/admin/insights", tags=["Insights"])

_PROBLEM = {"content": {"application/problem+json": {}}}
READ_ERRORS: dict[int | str, dict] = {
    401: {"description": "Not authenticated.", **_PROBLEM},
    403: {"description": "Needs the dashboard:read permission.", **_PROBLEM},
    422: {"description": "Invalid filter or parameter.", **_PROBLEM},
    503: {"description": "The query took too long.", **_PROBLEM},
}
WRITE_ERRORS: dict[int | str, dict] = {
    401: {"description": "Not authenticated.", **_PROBLEM},
    403: {"description": "Needs the taxonomy:write permission.", **_PROBLEM},
    404: {"description": "Taxonomy entry not found.", **_PROBLEM},
    409: {"description": "Duplicate or protected code.", **_PROBLEM},
    422: {"description": "Invalid payload.", **_PROBLEM},
}
_QUERY_NAMES = {"date_from": "from", "date_to": "to"}

read_guard = require_permission(Permission.DASHBOARD_READ)
write_guard = require_permission(Permission.TAXONOMY_WRITE)


def insights_filters(
    period: Period = Query(Period.ALL, description="all, 7d, 30d or custom."),
    date_from: date | None = Query(
        None, alias="from", description="First day (custom period)."
    ),
    date_to: date | None = Query(
        None, alias="to", description="Last day, inclusive (custom period)."
    ),
    user_type: str | None = Query(None, max_length=64),
    outcome: ConversationOutcome | None = Query(None),
    rating: ExperienceRating | None = Query(None),
    issue: str | None = Query(None, max_length=32, description="Issue code."),
    intent: str | None = Query(None, max_length=32, description="Intent code."),
    quality: str | None = Query(None, max_length=32, description="Bot-quality code."),
    person: str | None = Query(
        None, max_length=16, description="person_id (stable pseudonym)."
    ),
    visitor: str | None = Query(
        None,
        max_length=80,
        description="Visitors-page id: `client-<ip>` or a user UUID.",
    ),
    hour: int | None = Query(
        None, ge=0, le=23, description="Hour of day (0-23) in `tz`."
    ),
    q: str | None = Query(None, max_length=MAX_SEARCH_CHARS),
    audience: Audience = Query(Audience.OVERVIEW),
    tz: str | None = Query(
        None,
        max_length=64,
        description="IANA time zone for days and hours (default INSIGHTS_TIMEZONE).",
    ),
) -> InsightsFilters:
    """Shared filters; intent and quality keep a conversation if any turn matches."""
    try:
        return InsightsFilters(
            period=period,
            date_from=date_from,
            date_to=date_to,
            user_type=user_type,
            outcome=outcome,
            rating=rating,
            issue=issue,
            intent=intent,
            quality=quality,
            person=person,
            visitor=visitor,
            hour=hour,
            q=q,
            audience=audience,
            tz=tz or get_settings().insights_timezone,
        )
    except ValidationError as exc:
        raise RequestValidationError(
            [
                {
                    "loc": (
                        "query",
                        *(_QUERY_NAMES.get(str(p), str(p)) for p in err["loc"]),
                    ),
                    "msg": err["msg"],
                    "type": err["type"],
                }
                for err in exc.errors()
            ]
        ) from None


def get_parallel_runner(
    scoped: ScopedRunner = Depends(get_scoped_runner),
) -> ParallelRunner:
    """Parallel aggregation, one short-lived session per task.

    Built on the injectable ``get_scoped_runner`` (like every other fan-out
    route), so overriding it routes these tasks too.
    """
    return scoped.gather


def get_service(
    db: AsyncSession = Depends(read_only_session),
    runner: ParallelRunner = Depends(get_parallel_runner),
) -> InsightsAnalyticsService:
    return InsightsAnalyticsService(db, runner)


def get_bundle_service(
    db: AsyncSession = Depends(read_only_session),
    runner: ParallelRunner = Depends(get_parallel_runner),
) -> InsightsBundleService:
    return InsightsBundleService(db, runner)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/bundle",
    response_model=InsightsBundle,
    summary="Every Analytics widget in one response",
    responses={
        **READ_ERRORS,
        304: {"description": "Not modified (``If-None-Match``)."},
    },
)
async def get_bundle(
    request: Request,
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    sort: ConversationSort = Query(ConversationSort.START),
    direction: SortDirection = Query(SortDirection.DESC, alias="dir"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    parts: list[BundlePart] | None = Query(
        None, description="Widgets to return (default: all); others are null."
    ),
    service: InsightsBundleService = Depends(get_bundle_service),
) -> Response:
    """KPIs, trend, hours, flow, topics, quality, drop-off, people, the
    conversations page (``sort``/``dir``/``limit``/``offset``) and the
    classification backlog for one filter set, each equal to its own
    endpoint. Answered from in-memory facts kept current with the data (a
    change shows within seconds); ``ETag`` / 304 and ``X-Data-As-Of``."""
    page = ConversationPage(sort=sort, direction=direction, limit=limit, offset=offset)
    req = BundleRequest(filters, page, normalise_parts(parts))
    return await service.bundle(request, req)


@router.get(
    "/summary",
    response_model=InsightsSummary,
    summary="KPI strip",
    responses=READ_ERRORS,
)
async def get_summary(
    request: Request,
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> Response:
    """Conversations, substantive questions, handled %, served %, people and
    average questions per conversation (the HTML's ``metrics()``)."""
    summary = InsightsSummary.model_validate(await service.summary(filters))
    return cached_response(request, summary)


@router.get(
    "/trend",
    response_model=InsightsTrend,
    summary="Daily questions and conversations",
    responses=READ_ERRORS,
)
async def get_trend(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> InsightsTrend:
    return await service.trend(filters)


@router.get(
    "/hourly",
    response_model=InsightsHourly,
    summary="Questions by hour of day",
    responses=READ_ERRORS,
)
async def get_hourly(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> InsightsHourly:
    return await service.hourly(filters)


@router.get(
    "/flow",
    response_model=InsightsFlow,
    summary="User type to outcome flow",
    responses=READ_ERRORS,
)
async def get_flow(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> InsightsFlow:
    return await service.flow(filters)


@router.get(
    "/topics",
    response_model=InsightsTopics,
    summary="Questions and handled % per intent",
    responses=READ_ERRORS,
)
async def get_topics(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> InsightsTopics:
    return await service.topics(filters)


@router.get(
    "/quality",
    response_model=InsightsQuality,
    summary="How questions were answered",
    responses=READ_ERRORS,
)
async def get_quality(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> InsightsQuality:
    return await service.quality(filters)


@router.get(
    "/dropoff",
    response_model=InsightsDropoff,
    summary="Where conversations dropped",
    responses=READ_ERRORS,
)
async def get_dropoff(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> InsightsDropoff:
    """Issue counts over Partly served and Not served conversations."""
    return await service.dropoff(filters)


@router.get(
    "/people",
    response_model=InsightsPeople,
    summary="Conversations per probable person",
    responses=READ_ERRORS,
)
async def get_people(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> InsightsPeople:
    """People are pseudonyms (P01... numbered over the audience); no e-mail or IP."""
    return await service.people(filters)


@router.get(
    "/people/{person_id}",
    response_model=PersonDetail,
    summary="The visitors and sessions behind one probable person",
    responses={
        **READ_ERRORS,
        404: {"description": "No sessions for this person.", **_PROBLEM},
    },
)
async def get_person(
    request: Request,
    person_id: str = Path(pattern=PERSON_PATTERN, description="person_id."),
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsBundleService = Depends(get_bundle_service),
) -> Response:
    """How the person is grouped (account e-mail, network or session; the
    value itself is never returned) and the Visitors-page ids of the IPs
    their sessions came from. Only the ``audience`` and ``tz`` filters apply."""
    response = await service.person(request, person_id, filters)
    if response is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found.")
    return response


@router.get(
    "/conversations",
    response_model=ConversationList,
    summary="Conversations table",
    responses=READ_ERRORS,
)
async def list_conversations(
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    sort: ConversationSort = Query(ConversationSort.START),
    direction: SortDirection = Query(SortDirection.DESC, alias="dir"),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    service: InsightsAnalyticsService = Depends(get_service),
) -> ConversationList:
    page = ConversationPage(sort=sort, direction=direction, limit=limit, offset=offset)
    return await service.conversations(filters, page)


@router.get(
    "/conversations/{conv_id}",
    response_model=ConversationDetail,
    summary="One conversation with its turns",
    responses={
        **READ_ERRORS,
        404: {"description": "Conversation not found.", **_PROBLEM},
    },
)
async def get_conversation(
    conv_id: uuid.UUID,
    user: AuthUser = Depends(read_guard),
    filters: InsightsFilters = Depends(insights_filters),
    service: InsightsAnalyticsService = Depends(get_service),
) -> ConversationDetail:
    """Per-turn detail (time, intent, handled, quality, issue). Only the
    ``audience`` and ``tz`` filters apply."""
    detail = await service.conversation(conv_id, filters)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return detail


@router.get(
    "/pending",
    response_model=PendingClassification,
    summary="Turns pending classification",
    responses=READ_ERRORS,
)
async def get_pending(
    request: Request,
    user: AuthUser = Depends(read_guard),
    runner: ScopedRunner = Depends(get_scoped_runner),
    service: InsightsAnalyticsService = Depends(get_service),
) -> Response:
    """Precomputed (``dashboard_snapshots``), recomputed within seconds of
    new turns or labels; ``X-Data-As-Of`` is its age."""

    async def build(_gather: Gather) -> dict:
        return _pending_json(await service.pending())

    return await snapshot_response(request, PENDING_KIND, {}, runner.gather, build)


PENDING_KIND = "insights.pending"


async def _pending_snapshot(gather: Gather, _params: dict) -> dict:
    (payload,) = await gather(lambda db: InsightsAnalyticsService(db, gather).pending())
    return _pending_json(payload)


def _pending_json(payload: PendingClassification) -> dict:
    return PendingClassification.model_validate(payload).model_dump(mode="json")


register(PENDING_KIND, _pending_snapshot, [{}])


# ---------------------------------------------------------------------------
# Taxonomy (F7)
# ---------------------------------------------------------------------------


def _audit(request: Request, user: AuthUser) -> AuditContext:
    return AuditContext(
        actor=user.email,
        request_id=get_request_id(request),
        ip_address=get_client_ip(request),
    )


def _taxonomy_http_error(exc: TaxonomyError) -> HTTPException:
    return HTTPException(exc.status, exc.detail)


@router.get(
    "/taxonomy",
    response_model=TaxonomyList,
    summary="List taxonomy entries",
    responses=READ_ERRORS,
)
async def list_taxonomy(
    user: AuthUser = Depends(read_guard),
    kind: TaxonomyKind | None = Query(None),
    include_archived: bool = Query(False),
    db: AsyncSession = Depends(read_only_session),
) -> TaxonomyList:
    rows = await TaxonomyService(db).list_entries(
        kind, include_archived=include_archived
    )
    return TaxonomyList(items=[TaxonomyRead.model_validate(r) for r in rows])


@router.post(
    "/taxonomy",
    response_model=TaxonomyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a taxonomy entry",
    responses=WRITE_ERRORS,
)
async def create_taxonomy(
    request: Request,
    payload: TaxonomyCreate,
    user: AuthUser = Depends(write_guard),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyRead:
    """The code is immutable once created. Audited."""
    try:
        row = await TaxonomyService(db).create(payload, _audit(request, user))
    except TaxonomyError as exc:
        raise _taxonomy_http_error(exc) from None
    return TaxonomyRead.model_validate(row)


@router.patch(
    "/taxonomy/{kind}/{code}",
    response_model=TaxonomyRead,
    summary="Edit a taxonomy entry",
    responses=WRITE_ERRORS,
)
async def update_taxonomy(
    request: Request,
    payload: TaxonomyUpdate,
    user: AuthUser = Depends(write_guard),
    kind: TaxonomyKind = Path(description="Taxonomy kind."),
    code: str = Path(pattern=CODE_PATTERN, description="Stable code."),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyRead:
    """Label, description, colour, group, order and the substantive /
    handled flags are editable; existing labels keep their code, so renaming
    never breaks them. Audited."""
    try:
        row = await TaxonomyService(db).update(
            kind, code, payload, _audit(request, user)
        )
    except TaxonomyError as exc:
        raise _taxonomy_http_error(exc) from None
    return TaxonomyRead.model_validate(row)


@router.post(
    "/taxonomy/{kind}/{code}/archive",
    response_model=TaxonomyRead,
    summary="Archive a taxonomy entry",
    responses=WRITE_ERRORS,
)
async def archive_taxonomy(
    request: Request,
    user: AuthUser = Depends(write_guard),
    kind: TaxonomyKind = Path(description="Taxonomy kind."),
    code: str = Path(pattern=CODE_PATTERN, description="Stable code."),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyRead:
    """Archived entries stop being offered to the classifier; labels already
    stored keep their code and label. Entries are never deleted. Audited."""
    try:
        row = await TaxonomyService(db).archive(kind, code, _audit(request, user))
    except TaxonomyError as exc:
        raise _taxonomy_http_error(exc) from None
    return TaxonomyRead.model_validate(row)


@router.post(
    "/taxonomy/{kind}/{code}/unarchive",
    response_model=TaxonomyRead,
    summary="Unarchive a taxonomy entry",
    responses=WRITE_ERRORS,
)
async def unarchive_taxonomy(
    request: Request,
    user: AuthUser = Depends(write_guard),
    kind: TaxonomyKind = Path(description="Taxonomy kind."),
    code: str = Path(pattern=CODE_PATTERN, description="Stable code."),
    db: AsyncSession = Depends(get_db),
) -> TaxonomyRead:
    """Makes an archived entry active again (filters and classifier). A no-op
    on an active entry. Audited."""
    try:
        row = await TaxonomyService(db).unarchive(kind, code, _audit(request, user))
    except TaxonomyError as exc:
        raise _taxonomy_http_error(exc) from None
    return TaxonomyRead.model_validate(row)
