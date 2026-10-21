"""Document ingestion endpoints (incremental: only new/changed chunks are embedded).

Both take an optional ``source_url`` (OR-7): the form field / JSON field wins
over the file's own link (``.md`` ``url:`` front matter, ``.docx``
``dc:identifier``), which wins over the stored one. While Settings >
Knowledge links requires a URL, a new document without one is rejected with
422 ``source_url_required``. Changing an existing link by re-upload needs
``knowledge:links``; otherwise the stored link is kept and a warning returned.
"""

import asyncio
import hashlib
import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import ApiHTTPException
from app.core.request_meta import get_client_ip
from app.ingestion.docx_converter import convert_docx, screen_markdown
from app.ingestion.service import ingest_text
from app.knowledge.file_meta import FileLinkMeta, docx_link_meta, split_front_matter
from app.knowledge.ingest_links import UploadLink, resolve_upload_link
from app.knowledge.links import MAX_URL_LENGTH, LinkError, link_status
from app.knowledge.settings_service import (
    current_knowledge_settings,
    get_knowledge_settings,
)
from app.schemas.document import IngestRequest, IngestResponse

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


_INGEST_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in or missing service key"},
    403: {"description": "Requires documents:write"},
    422: {
        "description": (
            "Invalid input. `code` is `source_url_required` (a new document "
            "needs a source URL), `invalid_source_url` or "
            "`source_url_not_allowed` for link problems."
        )
    },
}


def _link_http_error(exc: LinkError) -> ApiHTTPException:
    return ApiHTTPException(422, detail=exc.message, code=exc.code)


def _to_response(result: dict) -> IngestResponse:
    """Map the ingestion service result onto the public response model."""
    fields = {k: result[k] for k in IngestResponse.model_fields if k in result}
    fields["warnings"] = [*result.get("warnings", []), *result.get("link_warnings", [])]
    fields["link_status"] = link_status(
        result.get("source_url"), current_knowledge_settings().allowed_hosts
    ).value
    return IngestResponse(**fields)


async def _upload_link(
    db: AsyncSession, field_url: str | None, file_meta: FileLinkMeta
) -> tuple[UploadLink, bool]:
    """The validated link (422 on a bad field value) and ``require_source_url``."""
    policy = await get_knowledge_settings(db)
    try:
        link = resolve_upload_link(
            (field_url or "").strip() or None, file_meta, policy.allowed_hosts
        )
    except LinkError as exc:
        raise _link_http_error(exc) from exc
    return link, policy.require_source_url


@router.post("/ingest", response_model=IngestResponse, responses=_INGEST_ERRORS)
async def ingest_json(
    request: Request,
    body: IngestRequest,
    dry_run: bool = False,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DOCUMENTS_WRITE)),
) -> IngestResponse:
    """JSON-based text ingestion endpoint (backwards compatible with CLI/scripts)."""
    req_id = getattr(request.state, "request_id", "unknown")
    client_ip = get_client_ip(request)

    effective_dry_run = dry_run or body.dry_run
    effective_force = force or body.force
    front = split_front_matter(body.content)
    link, require_url = await _upload_link(db, body.source_url, front.meta)
    content, warnings, quarantined = screen_markdown(front.body)
    warnings = [*front.warnings, *warnings]

    try:
        result = await ingest_text(
            db,
            source_name=body.source_name,
            source_type=body.source_type,
            content=content,
            warnings=warnings,
            quarantined=quarantined,
            chunking_strategy=body.chunking_strategy,
            metadata=body.metadata,
            original_filename=body.source_name,
            converted_from="md",
            force=effective_force,
            dry_run=effective_dry_run,
            actor=user.email,
            request_id=req_id,
            client_ip=client_ip,
            link=link,
            require_source_url=require_url,
            can_change_link=user.has(Permission.KNOWLEDGE_LINKS),
        )
    except LinkError as link_err:
        raise _link_http_error(link_err) from link_err
    except ValueError as val_err:
        raise HTTPException(status_code=422, detail=str(val_err)) from val_err
    except Exception as exc:
        await db.rollback()
        logger.error(
            "ingestion_failed request_id=%s error=%s", req_id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed. Reference Request ID: {req_id}",
        ) from exc

    return _to_response(result)


@router.post("/ingest/file", response_model=IngestResponse, responses=_INGEST_ERRORS)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    chunking_strategy: str = Form("auto"),
    force: bool = Form(False),
    dry_run: bool = Form(False),
    source_url: str | None = Form(
        None,
        max_length=MAX_URL_LENGTH,
        description=(
            "The public https page this document comes from (allow-list in "
            "Settings > Knowledge links). Overrides the file's own link."
        ),
    ),
    force_query: bool = Query(False, alias="force"),
    dry_run_query: bool = Query(False, alias="dry_run"),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(require_permission(Permission.DOCUMENTS_WRITE)),
) -> IngestResponse:
    """Server-side file ingestion endpoint supporting .md, .markdown, and .docx formats.

    ``dry_run`` / ``force`` are accepted as form fields or query parameters
    (the dashboard sends query parameters); either one set to true applies.
    """
    force = force or force_query
    dry_run = dry_run or dry_run_query
    req_id = getattr(request.state, "request_id", "unknown")
    client_ip = get_client_ip(request)
    filename = file.filename or "uploaded_document.md"

    # Stream file into memory enforcing ingest_max_file_bytes cap
    contents = await file.read()
    if len(contents) > settings.ingest_max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size limit of {settings.ingest_max_file_bytes} bytes",
        )

    raw_sha256 = hashlib.sha256(contents).hexdigest()
    lower_name = filename.lower()
    warnings: list[str] = []
    quarantined = False
    file_meta = FileLinkMeta()

    if lower_name.endswith(".docx"):
        converted_from = "docx"
        try:
            conv_result = await convert_docx(contents)
            text_content = conv_result.markdown
            warnings.extend(conv_result.warnings)
            quarantined = conv_result.quarantined
        except ValueError as val_err:
            msg = str(val_err)
            if "ZIP signature" in msg or "Macro-enabled" in msg:
                raise HTTPException(status_code=415, detail=msg) from val_err
            if "exceeds limit" in msg:
                raise HTTPException(status_code=413, detail=msg) from val_err
            raise HTTPException(status_code=422, detail=msg) from val_err
        file_meta = await asyncio.to_thread(docx_link_meta, contents)
    elif lower_name.endswith((".md", ".markdown")):
        converted_from = "md"
        try:
            text_content = contents.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422, detail="File is not valid UTF-8 text"
            ) from exc
        front = split_front_matter(text_content)
        file_meta = front.meta
        warnings.extend(front.warnings)
        text_content, screen_warnings, quarantined = screen_markdown(front.body)
        warnings.extend(screen_warnings)
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file format for '{filename}'. Only .docx and .md are supported.",
        )

    link, require_url = await _upload_link(db, source_url, file_meta)
    try:
        result = await ingest_text(
            db,
            source_name=filename,
            source_type="markdown",
            content=text_content,
            chunking_strategy=chunking_strategy,
            original_filename=filename,
            converted_from=converted_from,
            original_sha256=raw_sha256,
            original_size_bytes=len(contents),
            warnings=warnings,
            quarantined=quarantined,
            force=force,
            dry_run=dry_run,
            actor=user.email,
            request_id=req_id,
            client_ip=client_ip,
            link=link,
            require_source_url=require_url,
            can_change_link=user.has(Permission.KNOWLEDGE_LINKS),
        )
    except LinkError as link_err:
        raise _link_http_error(link_err) from link_err
    except ValueError as val_err:
        raise HTTPException(status_code=422, detail=str(val_err)) from val_err
    except Exception as exc:
        await db.rollback()
        logger.error(
            "ingestion_failed request_id=%s error=%s", req_id, exc, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed. Reference Request ID: {req_id}",
        ) from exc

    return _to_response(result)
