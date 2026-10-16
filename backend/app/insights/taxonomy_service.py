"""Taxonomy administration for F7 (newplan 6.3): list, create, edit, archive,
unarchive.

Codes are stable identifiers stored on ``turn_insights``: they are immutable
after creation and entries are archived, never deleted, so a rename or an
archive never breaks existing labels. Every change is audited in the same
transaction as the edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.audit import record_audit_event
from app.governance.pii import redact_pii
from app.insights.taxonomy import BotQuality, Intent, Issue, TaxonomyKind
from app.models.db_models import InsightTaxonomy
from app.schemas.insights import TaxonomyCreate, TaxonomyUpdate

# Codes the rule stage and the renderer formulas depend on.
PROTECTED_CODES: dict[str, frozenset[str]] = {
    TaxonomyKind.INTENT.value: frozenset(i.value for i in Intent),
    TaxonomyKind.BOT_QUALITY.value: frozenset(q.value for q in BotQuality),
    TaxonomyKind.ISSUE.value: frozenset(i.value for i in Issue),
}
_DUPLICATE = "A taxonomy entry with this kind and code already exists."


class TaxonomyError(Exception):
    """Base error; ``status`` maps to the HTTP status code."""

    status = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TaxonomyNotFoundError(TaxonomyError):
    status = 404


class TaxonomyConflictError(TaxonomyError):
    status = 409


@dataclass(frozen=True, slots=True)
class AuditContext:
    actor: str
    request_id: str
    ip_address: str | None


def _masked(values: dict[str, Any]) -> dict[str, Any]:
    """Audit detail with free text PII-masked."""
    return {
        key: redact_pii(value)[0] if isinstance(value, str) else value
        for key, value in values.items()
    }


class TaxonomyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_entries(
        self, kind: TaxonomyKind | None, *, include_archived: bool
    ) -> list[InsightTaxonomy]:
        query = select(InsightTaxonomy).order_by(
            InsightTaxonomy.kind, InsightTaxonomy.sort_order, InsightTaxonomy.code
        )
        if kind is not None:
            query = query.where(InsightTaxonomy.kind == kind.value)
        if not include_archived:
            query = query.where(InsightTaxonomy.is_active.is_(True))
        return list((await self.db.execute(query)).scalars())

    async def _get(self, kind: TaxonomyKind, code: str) -> InsightTaxonomy:
        row = await self.db.scalar(
            select(InsightTaxonomy).where(
                InsightTaxonomy.kind == kind.value, InsightTaxonomy.code == code
            )
        )
        if row is None:
            raise TaxonomyNotFoundError("Taxonomy entry not found.")
        return row

    async def _audit(
        self, audit: AuditContext, action: str, row: InsightTaxonomy, detail: dict
    ) -> None:
        await record_audit_event(
            self.db,
            request_id=audit.request_id,
            actor=audit.actor,
            action=action,
            detail=_masked({"kind": row.kind, "code": row.code, **detail}),
            ip_address=audit.ip_address,
            commit=False,
        )

    async def create(
        self, payload: TaxonomyCreate, audit: AuditContext
    ) -> InsightTaxonomy:
        values = payload.model_dump()
        values["kind"] = payload.kind.value
        duplicate = await self.db.scalar(
            select(InsightTaxonomy.id).where(
                InsightTaxonomy.kind == values["kind"],
                InsightTaxonomy.code == payload.code,
            )
        )
        if duplicate is not None:
            raise TaxonomyConflictError(_DUPLICATE)
        row = InsightTaxonomy(**values, is_active=True, is_system=False)
        self.db.add(row)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise TaxonomyConflictError(_DUPLICATE) from exc
        await self._audit(audit, "insights_taxonomy_created", row, values)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def update(
        self,
        kind: TaxonomyKind,
        code: str,
        payload: TaxonomyUpdate,
        audit: AuditContext,
    ) -> InsightTaxonomy:
        row = await self._get(kind, code)
        changes = payload.model_dump(exclude_unset=True)
        before = {field: getattr(row, field) for field in changes}
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_at = func.now()
        await self._audit(
            audit,
            "insights_taxonomy_updated",
            row,
            {"before": before, "after": changes},
        )
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def archive(
        self, kind: TaxonomyKind, code: str, audit: AuditContext
    ) -> InsightTaxonomy:
        row = await self._get(kind, code)
        if code in PROTECTED_CODES[kind.value]:
            raise TaxonomyConflictError(
                "This code is used by the rule stage and cannot be archived."
            )
        if row.is_active:
            row.is_active = False
            row.updated_at = func.now()
            await self._audit(audit, "insights_taxonomy_archived", row, {})
            await self.db.commit()
            await self.db.refresh(row)
        return row

    async def unarchive(
        self, kind: TaxonomyKind, code: str, audit: AuditContext
    ) -> InsightTaxonomy:
        """Offer an archived entry to filters and the classifier again."""
        row = await self._get(kind, code)
        if not row.is_active:
            row.is_active = True
            row.updated_at = func.now()
            await self._audit(audit, "insights_taxonomy_unarchived", row, {})
            await self.db.commit()
            await self.db.refresh(row)
        return row
