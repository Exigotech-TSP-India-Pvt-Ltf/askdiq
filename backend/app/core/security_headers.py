"""Response security headers (OWASP A02, newplan section 12.2).

A pure ASGI middleware (no ``BaseHTTPMiddleware``) so streaming responses are
not buffered. Header values come from settings; HSTS is only emitted when
``ENV=production`` because TLS terminates at nginx there.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings, get_settings

CSP_HEADER = "content-security-policy"

# Swagger UI / ReDoc load their own scripts and styles; the strict API CSP
# would blank those pages, so it is skipped for them (they are disabled in
# production anyway unless ENABLE_API_DOCS=true).
DOCS_PATHS: frozenset[str] = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})


def build_security_headers(settings: Settings) -> dict[str, str]:
    """Return the security headers to send on every HTTP response."""
    headers = {
        "x-content-type-options": "nosniff",
        "referrer-policy": settings.security_referrer_policy,
        "permissions-policy": settings.security_permissions_policy,
        "cross-origin-opener-policy": settings.security_coop,
        "x-frame-options": settings.security_frame_options,
        CSP_HEADER: settings.security_csp,
    }
    if settings.env == "production":
        headers["strict-transport-security"] = settings.security_hsts
    return headers


def apply_security_headers(
    headers: MutableHeaders,
    values: dict[str, str],
    path: str = "",
) -> None:
    """Set each header unless the route already set its own value."""
    for name, value in values.items():
        if name == CSP_HEADER and path in DOCS_PATHS:
            continue
        if name not in headers:
            headers[name] = value


class SecurityHeadersMiddleware:
    """Adds the configured security headers to every HTTP response."""

    def __init__(self, app: ASGIApp, headers: dict[str, str] | None = None) -> None:
        self.app = app
        self.headers = (
            dict(headers)
            if headers is not None
            else build_security_headers(get_settings())
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                apply_security_headers(
                    MutableHeaders(scope=message), self.headers, path
                )
            await send(message)

        await self.app(scope, receive, send_with_headers)
