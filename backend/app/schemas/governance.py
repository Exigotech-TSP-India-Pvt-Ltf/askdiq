"""Response schemas for the governance/audit-log endpoint."""

from datetime import datetime

from pydantic import BaseModel


class AuditLogEntry(BaseModel):
    id: str
    request_id: str
    actor: str
    action: str
    detail: dict
    created_at: datetime
    ip_address: str | None = None
