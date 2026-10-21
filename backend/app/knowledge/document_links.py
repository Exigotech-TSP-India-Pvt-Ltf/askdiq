"""Inline link editing on the Knowledge page (``PATCH /documents/{id}``).

Only the ``documents`` row changes: chunks never copy the URL, so nothing is
re-chunked or re-embedded, and ``updated_at`` (the content time) is kept.
Every change is audited as ``document_link_updated`` with hosts only (never
full URLs), and open consoles are told to refresh the documents views.
"""

from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiHTTPException, ErrorCode
from app.core.events import TOPIC_CONSOLE_CHANGED, publish_event
from app.core.logging_config import get_logger
from app.governance.audit import record_audit_event
from app.knowledge.links import LinkError, link_host, link_status, validate_source_url
from app.knowledge.settings_service import get_knowledge_settings
from app.models.db_models import Document
from app.schemas.knowledge import DocumentLinkRead, DocumentLinkUpdate

logger = get_logger(__name__)

AUDIT_LINK_UPDATED = "document_link_updated"
# Console live-update scope: refreshes every ``documents`` query-cache key.
LIVE_SCOPE = "documents.links"


def link_read(
    document_id: uuid.UUID,
    url: str | None,
    section_links: bool,
    allowed_hosts: list[str],
) -> DocumentLinkRead:
    return DocumentLinkRead(
        document_id=str(document_id),
        source_url=url,
        section_links=section_links,
        has_link=url is not None,
        link_status=link_status(url, allowed_hosts).value,
        link_host=link_host(url),
    )


async def update_document_link(
    db: AsyncSession,
    document_id: uuid.UUID,
    payload: DocumentLinkUpdate,
    *,
    actor: str,
    request_id: str,
    client_ip: str | None,
) -> DocumentLinkRead:
    """Validate and store the link (404 / 422 with a link ``code``)."""
    document = await db.get(Document, document_id)
    if document is None:
        raise ApiHTTPException(
            404, detail="Document not found", code=ErrorCode.NOT_FOUND
        )
    policy = await get_knowledge_settings(db)
    raw = (payload.source_url or "").strip()
    try:
        url = validate_source_url(raw, policy.allowed_hosts) if raw else None
    except LinkError as exc:
        raise ApiHTTPException(422, detail=exc.message, code=exc.code) from exc

    old_url, old_sections = document.source_url, bool(document.section_links)
    if (old_url, old_sections) != (url, payload.section_links):
        await db.execute(
            update(Document)
            .where(Document.id == document_id)
            # Keep the content time: a link edit is not a content change.
            .values(
                source_url=url,
                section_links=payload.section_links,
                updated_at=Document.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        await record_audit_event(
            db,
            request_id=request_id,
            actor=actor,
            action=AUDIT_LINK_UPDATED,
            detail={
                "document_id": str(document_id),
                "source_name": document.source_name,
                "old_host": link_host(old_url),
                "new_host": link_host(url),
                "section_links": {"from": old_sections, "to": payload.section_links},
            },
            ip_address=client_ip,
            commit=False,
        )
        await db.commit()
        await publish_event(TOPIC_CONSOLE_CHANGED, LIVE_SCOPE)
        logger.info(
            "document_link_updated",
            document_id=str(document_id),
            new_host=link_host(url),
            actor=actor,
        )
    return link_read(document_id, url, payload.section_links, policy.allowed_hosts)
