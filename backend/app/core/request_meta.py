"""Extracting request metadata (currently: client IP) for audit/rate-limit use."""

import ipaddress

from fastapi import Request

from app.core.config import get_settings

settings = get_settings()


def get_client_ip(request: Request) -> str:
    """Best-effort client IP.

    Only trusts the X-Forwarded-For header (first/left-most hop = original
    client) when `settings.trust_proxy_headers` is enabled, i.e. this app is
    actually deployed behind a reverse proxy/load balancer that sets that
    header itself. A direct caller can set X-Forwarded-For to anything, so
    honoring it unconditionally would let any client spoof its logged IP.
    Otherwise (default), the direct TCP peer address is used.
    """
    if settings.trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            first_hop = forwarded_for.split(",")[0].strip()
            if first_hop:
                return first_hop

    return request.client.host if request.client else "unknown"


def valid_ip(value: str | None) -> str | None:
    """Return the normalised IP if `value` is a valid IPv4/IPv6 address, else None.

    A comma-separated X-Forwarded-For style value uses its first (client) hop.
    """
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.split(",")[0].strip()))
    except ValueError:
        return None


def resolve_client_ip(request: Request, supplied_ip: str | None = None) -> str:
    """End-user IP: a caller-supplied `ip_address` (the website frontend forwards
    each visitor's IP) when allowed and valid, else the detected client IP."""
    if settings.allow_client_ip_override:
        supplied = valid_ip(supplied_ip)
        if supplied:
            return supplied
    return get_client_ip(request)
