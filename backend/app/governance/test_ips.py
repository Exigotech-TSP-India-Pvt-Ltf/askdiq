"""Management of testing and development IPs (excluded from user analytics).

Supports:
- Defaults configured via .env (DEV_TEST_IPS).
- Dynamic overrides stored in the database (test_ip_configs table), manageable via dashboard.
- Subnet (CIDR), loopback, and exact IP matching.
"""

import asyncio
import contextlib
import ipaddress
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import ColumnElement, and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import TOPIC_CACHE_INVALIDATE, publish_event
from app.governance.audit import record_audit_event
from app.models.db_models import TestIpConfig

logger = logging.getLogger(__name__)

# Hardcoded loopback addresses that must always be considered test/dev
BUILTIN_LOOPBACKS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "unknown"}  # nosec B104  # noqa: S104 - IP literal compared, not a bind address

Audience = Literal["overview", "users", "testing"]

# Thread-safe in-memory fast path cache for effective test IPs and pre-parsed CIDRs
_cached_effective_ips: set[str] | None = None
_cached_parsed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
_effective_ips_lock = asyncio.Lock()


def invalidate_test_ips_cache() -> None:
    """Invalidate in-memory effective test IP cache upon configuration changes."""
    global _cached_effective_ips, _cached_parsed_networks  # noqa: PLW0603 - process-local cache
    _cached_effective_ips = None
    _cached_parsed_networks = []


def parse_env_test_ips() -> set[str]:
    """Extract and normalize the list of test IPs specified in settings / .env."""
    settings = get_settings()
    raw = getattr(settings, "dev_test_ips", "127.0.0.1,::1,localhost,0.0.0.0")
    items = raw if isinstance(raw, list) else str(raw).replace(";", ",").split(",")
    result = set()
    for item in items:
        cleaned = item.strip().lower()
        if cleaned:
            result.add(cleaned)
    return result | BUILTIN_LOOPBACKS


async def get_effective_test_ips(db: AsyncSession) -> set[str]:
    """Return the merged set of effective test IPs from .env and database overrides with in-memory caching."""
    global _cached_effective_ips, _cached_parsed_networks  # noqa: PLW0603 - process-local cache
    if _cached_effective_ips is not None:
        return _cached_effective_ips

    async with _effective_ips_lock:
        if _cached_effective_ips is not None:
            return _cached_effective_ips

        effective = parse_env_test_ips()
        try:
            rows = (
                await db.execute(
                    select(TestIpConfig.ip_address, TestIpConfig.is_active)
                )
            ).all()
            for ip, is_active in rows:
                clean_ip = ip.strip().lower()
                if is_active:
                    effective.add(clean_ip)
                else:
                    effective.discard(clean_ip)
        except Exception as exc:
            logger.warning("Could not query test_ip_configs table: %s", exc)

        networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for rule in effective:
            if "/" in rule:
                with contextlib.suppress(ValueError):
                    networks.append(ipaddress.ip_network(rule, strict=False))

        _cached_effective_ips = effective
        _cached_parsed_networks = networks
        return _cached_effective_ips


def is_test_ip(ip: str | None, effective_test_ips: set[str] | None = None) -> bool:
    """Check whether a client IP represents a test or development environment (fast-path <= 0.05ms)."""
    if not ip:
        return False
    val = ip.strip().lower()
    if val.startswith("client-"):
        val = val[len("client-") :]

    if val in BUILTIN_LOOPBACKS or val.startswith("127."):
        return True

    effective = (
        effective_test_ips
        if effective_test_ips is not None
        else (_cached_effective_ips or parse_env_test_ips())
    )
    if val in effective:
        return True

    # Check for CIDR block matches with pre-parsed fast-path
    if _cached_parsed_networks and effective_test_ips is None:
        try:
            addr = ipaddress.ip_address(val)
            for net in _cached_parsed_networks:
                if addr in net:
                    return True
        except ValueError:
            pass
    else:
        for rule in effective:
            if "/" in rule:
                try:
                    network = ipaddress.ip_network(rule, strict=False)
                    if ipaddress.ip_address(val) in network:
                        return True
                except ValueError:
                    pass

    return False


def get_audience_filter(
    audience: Audience,
    ip_column: ColumnElement[str | None],
    effective_ips: set[str] | None = None,
) -> ColumnElement[bool] | None:
    """Centralized filter generator constructing optimized IN / NOT IN clauses for audience scoping."""
    if audience == "overview":
        return None

    rules = list(effective_ips or (_cached_effective_ips or parse_env_test_ips()))

    if audience == "testing":
        if rules:
            return or_(ip_column.is_(None), ip_column.in_(rules))
        return ip_column.is_(None)

    # audience == "users"
    if rules:
        return and_(ip_column.isnot(None), ip_column.notin_(rules))
    return ip_column.isnot(None)


def validate_test_ip_string(ip_address: str, description: str | None = None) -> str:
    """Validate test IP / CIDR format and bounds (Task 2.2)."""

    if not ip_address or not ip_address.strip():
        raise HTTPException(status_code=422, detail="IP address cannot be empty.")

    clean = ip_address.strip()
    if len(clean) > 64:
        raise HTTPException(
            status_code=422,
            detail="IP address exceeds maximum length of 64 characters.",
        )

    if any(ord(c) < 32 or ord(c) == 127 for c in clean):
        raise HTTPException(
            status_code=422, detail="IP address contains invalid control characters."
        )

    if description is not None:
        if len(description) > 200:
            raise HTTPException(
                status_code=422,
                detail="Description exceeds maximum length of 200 characters.",
            )
        if any(ord(c) < 32 or ord(c) == 127 for c in description):
            raise HTTPException(
                status_code=422,
                detail="Description contains invalid control characters.",
            )

    lower = clean.lower()
    if lower == "localhost" or lower.startswith("client-"):
        return lower

    # Try IP address or CIDR network
    try:
        if "/" in clean:
            ipaddress.ip_network(clean, strict=False)
        else:
            ipaddress.ip_address(clean)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"'{clean}' is not a valid IPv4/IPv6 address or CIDR network block.",
        ) from None

    return lower


async def get_test_ip_details(db: AsyncSession) -> dict:
    """Return configured test IPs with defaults, overrides, and active status."""
    default_ips = sorted(parse_env_test_ips())
    db_rows = (
        (
            await db.execute(
                select(TestIpConfig).order_by(TestIpConfig.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    custom_ips = [
        {
            "id": str(r.id),
            "ip_address": r.ip_address,
            "description": r.description,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in db_rows
    ]
    effective_ips = sorted(await get_effective_test_ips(db))
    return {
        "default_ips": default_ips,
        "custom_ips": custom_ips,
        "effective_ips": effective_ips,
        "classification_note": (
            "Historical query metrics, sessions, and cost distributions are "
            "dynamically re-classified when test IP rules change."
        ),
    }


async def add_or_update_test_ip(
    db: AsyncSession,
    ip_address: str,
    description: str | None = None,
    is_active: bool = True,
    actor: str = "system",
    request_id: str | None = None,
    client_ip: str | None = None,
) -> dict:
    """Add or update an IP address override in test_ip_configs (validated & audited)."""

    from app.governance.audience import invalidate_audience_cache  # noqa: PLC0415 - audience<->test_ips import cycle

    clean_ip = validate_test_ip_string(ip_address, description)
    settings = get_settings()

    row = (
        await db.execute(
            select(TestIpConfig).where(TestIpConfig.ip_address == clean_ip)
        )
    ).scalar_one_or_none()

    previous: dict | None = (
        {"description": row.description, "is_active": row.is_active} if row else None
    )
    if not row:
        count = (
            await db.execute(select(func.count()).select_from(TestIpConfig))
        ).scalar() or 0
        if count >= settings.test_ip_max_entries:
            raise HTTPException(
                status_code=422,
                detail=f"Maximum test IP entries limit ({settings.test_ip_max_entries}) reached.",
            )

        row = TestIpConfig(
            ip_address=clean_ip,
            description=description,
            is_active=is_active,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )

        db.add(row)
    else:
        row.description = description if description is not None else row.description
        row.is_active = is_active

    await record_audit_event(
        db,
        request_id=request_id or "admin-test-ip-action",
        actor=actor,
        action="test_ip_updated" if previous else "test_ip_added",
        detail={
            "ip_address": clean_ip,
            "description": description,
            "is_active": is_active,
            **({"previous": previous} if previous else {}),
        },
        ip_address=client_ip,
        commit=False,
    )

    await db.commit()
    await db.refresh(row)
    invalidate_audience_cache()
    await publish_event(TOPIC_CACHE_INVALIDATE, "audience")

    return {
        "id": str(row.id),
        "ip_address": row.ip_address,
        "description": row.description,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def delete_test_ip(
    db: AsyncSession,
    ip_address: str,
    actor: str = "system",
    request_id: str | None = None,
    client_ip: str | None = None,
) -> bool:
    """Delete an IP address override from test_ip_configs (validated & audited)."""
    from app.governance.audience import invalidate_audience_cache  # noqa: PLC0415 - audience<->test_ips import cycle

    clean_ip = validate_test_ip_string(ip_address)
    result = await db.execute(
        delete(TestIpConfig).where(TestIpConfig.ip_address == clean_ip)
    )

    if (result.rowcount or 0) > 0:
        await record_audit_event(
            db,
            request_id=request_id or "admin-test-ip-action",
            actor=actor,
            action="test_ip_removed",
            detail={"ip_address": clean_ip},
            ip_address=client_ip,
            commit=False,
        )
        await db.commit()
        invalidate_audience_cache()
        await publish_event(TOPIC_CACHE_INVALIDATE, "audience")
        return True

    await db.commit()
    return False
