"""`/auth/*`: SSO sign-in, current user, logout and access requests (B2.4.1).

The Next.js proxy is the only caller (see ``app.auth.deps`` for the header
contract). ``/auth/sso/*`` is public to users (no session yet) but still
needs the proxy's service key, and both sign-in steps are rate-limited per
client IP. Everything else needs a session.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    Security,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.audit import AuditContext
from app.auth.deps import (
    current_user,
    require_role_or_chat,
    require_service_key,
    session_header,
)
from app.auth.oidc import (
    OidcClient,
    OidcError,
    OidcStateError,
    TokenValidationError,
    get_oidc_client,
)
from app.auth.principal import AuthUser
from app.auth.service import AuthService
from app.core.config import get_settings
from app.core.database import get_db
from app.core.http_cache import cached_response
from app.core.request_meta import get_client_ip
from app.core.state import get_state_backend, sub_window_seconds
from app.models.db_models import AccessRequest
from app.schemas.auth import (
    AccessRequestCreate,
    AccessRequestRead,
    AccessRequestStatusRead,
    MeRead,
    SessionIssuedRead,
    SsoCallbackRequest,
    SsoLoginRead,
    UserIdentityRead,
)

router = APIRouter(prefix="/auth", tags=["Auth"])

LOGIN_WINDOW_SECONDS = 60
NO_STORE = "no-store"
DETAIL_RATE_LIMITED = "Too many sign-in attempts. Try again shortly."
DETAIL_STATE = "Sign-in link expired or was already used. Start again."
DETAIL_TOKEN = "Sign-in failed."  # noqa: S105 - message text, not a secret
DETAIL_IDP = "Identity provider unavailable."

_ERR_401 = {"description": "Missing or wrong service key, or no valid session."}
_ERR_403 = {"description": "Account suspended, or CSRF check failed."}
_ERR_409 = {"description": "Conflict with the current state."}
_ERR_422 = {"description": "Validation error."}
_ERR_429 = {"description": "Too many sign-in attempts from this IP."}


async def login_rate_limit(request: Request) -> None:
    """Per-client-IP cap on both sign-in steps (shared across workers)."""
    ip = get_client_ip(request)
    count = await get_state_backend().incr_window(
        f"rl:auth-login:{ip}", LOGIN_WINDOW_SECONDS
    )
    if count > get_settings().auth_login_rate_limit_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            DETAIL_RATE_LIMITED,
            headers={"Retry-After": str(sub_window_seconds(LOGIN_WINDOW_SECONDS))},
        )


def me_payload(user: AuthUser) -> MeRead:
    return MeRead(
        user=UserIdentityRead(id=user.id, email=user.email, name=user.name),
        role=user.role,
        status=user.status,
        permissions=sorted(user.permissions),
        version=user.version,
        access_contact_email=get_settings().access_contact_email or None,
    )


def access_request_read(row: AccessRequest) -> AccessRequestRead:
    return AccessRequestRead(
        id=row.id,
        status=row.status,
        requested_role=row.requested_role,
        justification=row.justification,
        created_at=row.created_at,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
    )


@router.get(
    "/sso/login",
    response_model=SsoLoginRead,
    summary="Start SSO sign-in",
    description=(
        "Creates single-use `state`, `nonce` and a PKCE verifier (stored "
        "server-side) and returns the Entra authorize URL. Rate-limited per IP."
    ),
    responses={401: _ERR_401, 429: _ERR_429, 502: {"description": DETAIL_IDP}},
    dependencies=[Depends(require_service_key), Depends(login_rate_limit)],
)
async def sso_login(
    response: Response, oidc: OidcClient = Depends(get_oidc_client)
) -> SsoLoginRead:
    try:
        authorize = await oidc.build_authorize_url()
    except OidcError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, DETAIL_IDP) from exc
    response.headers["Cache-Control"] = NO_STORE
    return SsoLoginRead(authorize_url=authorize.url)


@router.post(
    "/sso/callback",
    response_model=SessionIssuedRead,
    summary="Complete SSO sign-in",
    description=(
        "Called by the Next `/auth/callback` handler with the IdP's `code` and "
        "`state`. Validates the ID token, upserts the user on `oid`, applies "
        "the status rules and returns a new session id for the httpOnly "
        "cookie. Send the previous cookie value in `X-Session` to rotate it. "
        "Unknown, expired or replayed `state` gives 400; a suspended account "
        "gives 403 with no session. Every outcome is audited."
    ),
    responses={
        400: {"description": DETAIL_STATE},
        401: {"description": f"{DETAIL_TOKEN} (invalid ID token) or bad service key."},
        403: {"description": "Account suspended or identity conflict."},
        422: _ERR_422,
        429: _ERR_429,
        502: {"description": DETAIL_IDP},
    },
    dependencies=[Depends(require_service_key), Depends(login_rate_limit)],
)
async def sso_callback(
    body: SsoCallbackRequest,
    request: Request,
    response: Response,
    previous_session: str | None = Security(session_header),
    db: AsyncSession = Depends(get_db),
    oidc: OidcClient = Depends(get_oidc_client),
) -> SessionIssuedRead:
    service = AuthService(db)
    ctx = AuditContext.from_request(request)
    try:
        identity = await oidc.complete_login(body.code, body.state)
    except OidcStateError as exc:
        await service.record_login_denied(ctx, "invalid_state")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, DETAIL_STATE) from exc
    except TokenValidationError as exc:
        await service.record_login_denied(ctx, f"invalid_token: {exc.reason}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, DETAIL_TOKEN) from exc
    except OidcError as exc:
        await service.record_login_denied(ctx, f"idp_error: {exc.reason}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, DETAIL_IDP) from exc

    result = await service.sign_in(
        identity,
        ctx,
        user_agent=request.headers.get("user-agent"),
        replaces=previous_session,
    )
    settings = get_settings()
    response.headers["Cache-Control"] = NO_STORE
    return SessionIssuedRead(
        session=result.session.token,
        cookie_name=settings.session_cookie_name,
        cookie_secure=settings.session_cookie_secure,
        expires_at=result.session.expires_at,
        me=me_payload(result.user),
    )


@router.get(
    "/me",
    response_model=MeRead,
    summary="Current user, role, status and permissions",
    description=(
        "Any signed-in user, including pending, rejected and suspended ones "
        "(so the UI can show the right page). Sends a weak ETag; "
        "`If-None-Match` gives 304 while nothing changed."
    ),
    responses={304: {"description": "Not modified."}, 401: _ERR_401},
)
async def get_me(request: Request, user: AuthUser = Depends(current_user)) -> Response:
    response = cached_response(request, me_payload(user), max_age=0)
    response.headers["Vary"] = "X-API-Key, X-Session"
    return response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out (revokes the server-side session)",
    responses={401: _ERR_401, 403: _ERR_403},
)
async def logout(
    request: Request,
    token: str | None = Security(session_header),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(current_user),
) -> Response:
    await AuthService(db).logout(user, token or "", AuditContext.from_request(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/access-request",
    response_model=AccessRequestStatusRead,
    summary="My dashboard access status and latest request",
    responses={401: _ERR_401, 403: _ERR_403},
)
async def get_access_request(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> AccessRequestStatusRead:
    service = AuthService(db)
    state = await service.access_request_state(user)
    return AccessRequestStatusRead(
        user_status=state.user.status,
        role=state.user.role,
        request=access_request_read(state.request) if state.request else None,
        access_contact_email=service.access_contact_email,
    )


@router.post(
    "/access-request",
    response_model=AccessRequestRead,
    status_code=status.HTTP_201_CREATED,
    summary="Request dashboard access",
    description=(
        "For signed-in users without dashboard access (pending or rejected). "
        "One open request per user."
    ),
    responses={
        401: _ERR_401,
        403: _ERR_403,
        409: {"description": "Already active, or a request is already pending."},
        422: _ERR_422,
    },
)
async def create_access_request(
    body: AccessRequestCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_role_or_chat),
) -> AccessRequestRead:
    row = await AuthService(db).request_access(
        user,
        body.requested_role,
        body.justification,
        AuditContext.from_request(request),
    )
    return access_request_read(row)
