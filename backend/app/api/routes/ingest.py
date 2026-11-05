"""Document ingestion endpoints (incremental: only new/changed chunks are embedded).

Both take an optional ``source_url`` (OR-7): the form field / JSON field wins
over the file's own link (``.md`` ``route:`` front matter = SITE_BASE_URL +
route, then ``url:``; ``.docx`` ``dc:identifier``), which wins over the
stored one. While Settings > Knowledge links requires a URL, a new document
without one is rejected with 422 ``source_url_required``. Changing an
existing link by re-upload needs ``knowledge:links``; otherwise the stored
link is kept and a warning returned.

Every upload gets the website pages' treatment: clean-up, cross-page
de-duplication (lines another, more specific document already has are
dropped from the upload and listed in ``warnings``), normalisation, hash and
section-tree chunking. One document per page link: a different name with a
link another document uses is 409 ``document_link_in_use`` unless
``replace_document_id`` names that document. ``POST /ingest/preview`` shows
all of this without changing anything.
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
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
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_permission
from app.auth.permissions import Permission
from app.auth.principal import AuthUser
from app.core.config import get_settings
from app.core.database import unbounded_session
from app.core.errors import ApiHTTPException
from app.core.exceptions import DomainError
from app.core.request_meta import get_client_ip
from app.ingestion.docx_converter import convert_docx, screen_markdown
from app.ingestion.service import UploadRules, ingest_text, preview_upload
from app.knowledge.file_meta import FileLinkMeta, docx_link_meta, split_front_matter
from app.knowledge.ingest_links import UploadLink, resolve_upload_link
from app.knowledge.links import MAX_URL_LENGTH, LinkError, link_status
from app.knowledge.settings_service import (
    current_knowledge_settings,
    get_knowledge_settings,
)
from app.schemas.document import (
    REPLACE_DOCUMENT_DESCRIPTION,
    IngestPreviewResponse,
    IngestRequest,
    IngestResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

_PROBLEM = {"content": {"application/problem+json": {}}}
_INGEST_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Not signed in or missing service key", **_PROBLEM},
    403: {"description": "Requires documents:write", **_PROBLEM},
    409: {
        "description": (
            "`document_link_in_use`: another document already cites this "
            "upload's link. `existing_document` {id, name, link} names it; "
            "resend with `replace_document_id` = that id to replace it."
        ),
        **_PROBLEM,
    },
    422: {
        "description": (
            "Invalid input. `code` is `source_url_required` (a new document "
            "needs a source URL), `invalid_source_url` or "
            "`source_url_not_allowed` for link problems, "
            "`replace_document_mismatch` when `replace_document_id` is not "
            "the document using the link."
        ),
        **_PROBLEM,
    },
}
_FILE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_INGEST_ERRORS,
    413: {"description": "File too large", **_PROBLEM},
    415: {"description": "Not a .md / .docx file, or a macro / invalid .docx"},
}
_SOURCE_URL_DESCRIPTION = (
    "The public https page this document comes from (allow-list in "
    "Settings > Knowledge links). Overrides the file's own link."
)


@dataclass
class _Upload:
    """An uploaded file read, converted to Markdown and screened."""

    filename: str
    contents: bytes
    text: str
    converted_from: str
    warnings: list[str] = field(default_factory=list)
    quarantined: bool = False
    file_meta: FileLinkMeta = field(default_factory=FileLinkMeta)


def _link_http_error(exc: LinkError) -> ApiHTTPException:
    return ApiHTTPException(422, detail=exc.message, code=exc.code)


def _to_response(result: dict) -> IngestResponse:
    """Map the ingestion service result onto the public response model."""
    fields = {
        k: result[k]
        for k in IngestResponse.model_fields  # pylint: disable=not-an-iterable
        if k in result
    }
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
            (field_url or "").strip() or None,
            file_meta,
            policy.allowed_hosts,
            site_base_url=settings.site_base_url,
        )
    except LinkError as exc:
        raise _link_http_error(exc) from exc
    return link, policy.require_source_url


async def _convert_docx_upload(upload: _Upload) -> None:
    try:
        conv_result = await convert_docx(upload.contents)
    except ValueError as val_err:
        msg = str(val_err)
        if "ZIP signature" in msg or "Macro-enabled" in msg:
            raise HTTPException(status_code=415, detail=msg) from val_err
        if "exceeds limit" in msg:
            raise HTTPException(status_code=413, detail=msg) from val_err
        raise HTTPException(status_code=422, detail=msg) from val_err
    upload.text = conv_result.markdown
    upload.warnings.extend(conv_result.warnings)
    upload.quarantined = conv_result.quarantined
    upload.file_meta = await asyncio.to_thread(docx_link_meta, upload.contents)


def _read_markdown_upload(upload: _Upload) -> None:
    try:
        text_content = upload.contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422, detail="File is not valid UTF-8 text"
        ) from exc
    front = split_front_matter(text_content)
    upload.file_meta = front.meta
    upload.warnings.extend(front.warnings)
    upload.text, screen_warnings, upload.quarantined = screen_markdown(front.body)
    upload.warnings.extend(screen_warnings)


async def _read_upload(file: UploadFile) -> _Upload:
    """Size cap, then .docx conversion or .md front matter + screening."""
    filename = file.filename or "uploaded_document.md"
    contents = await file.read()
    if len(contents) > settings.ingest_max_file_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size limit of {settings.ingest_max_file_bytes} bytes",
        )
    lower_name = filename.lower()
    if lower_name.endswith(".docx"):
        upload = _Upload(filename, contents, "", "docx")
        await _convert_docx_upload(upload)
    elif lower_name.endswith((".md", ".markdown")):
        upload = _Upload(filename, contents, "", "md")
        _read_markdown_upload(upload)
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file format for '{filename}'. Only .docx and .md are supported.",
        )
    return upload


async def _run_ingest(db: AsyncSession, req_id: str, **kwargs: Any) -> dict:
    """``ingest_text`` with the routes' error mapping (problem+json codes)."""
    try:
        return await ingest_text(db, **kwargs)
    except LinkError as link_err:
        raise _link_http_error(link_err) from link_err
    except DomainError:
        await db.rollback()  # releases the advisory locks a re-check took
        raise
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


@router.post("/ingest", response_model=IngestResponse, responses=_INGEST_ERRORS)
async def ingest_json(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # FastAPI params
    request: Request,
    body: IngestRequest,
    dry_run: bool = False,
    force: bool = False,
    # Ingestion may write many chunks: not capped by DB_STATEMENT_TIMEOUT_MS.
    db: AsyncSession = Depends(unbounded_session),
    user: AuthUser = Depends(require_permission(Permission.DOCUMENTS_WRITE)),
) -> IngestResponse:
    """JSON-based text ingestion endpoint (backwards compatible with CLI/scripts)."""
    req_id = getattr(request.state, "request_id", "unknown")
    front = split_front_matter(body.content)
    link, require_url = await _upload_link(db, body.source_url, front.meta)
    content, warnings, quarantined = screen_markdown(front.body)
    result = await _run_ingest(
        db,
        req_id,
        source_name=body.source_name,
        source_type=body.source_type,
        content=content,
        warnings=[*front.warnings, *warnings],
        quarantined=quarantined,
        chunking_strategy=body.chunking_strategy,
        metadata=body.metadata,
        original_filename=body.source_name,
        converted_from="md",
        force=force or body.force,
        dry_run=dry_run or body.dry_run,
        actor=user.email,
        request_id=req_id,
        client_ip=get_client_ip(request),
        link=link,
        require_source_url=require_url,
        can_change_link=user.has(Permission.KNOWLEDGE_LINKS),
        rules=UploadRules(replace_document_id=body.replace_document_id),
    )
    return _to_response(result)


@router.post("/ingest/file", response_model=IngestResponse, responses=_FILE_ERRORS)
async def ingest_file(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals  # FastAPI params
    request: Request,
    file: UploadFile = File(...),
    chunking_strategy: str = Form("auto"),
    force: bool = Form(False),
    dry_run: bool = Form(False),
    source_url: str | None = Form(
        None, max_length=MAX_URL_LENGTH, description=_SOURCE_URL_DESCRIPTION
    ),
    replace_document_id: uuid.UUID | None = Form(
        None, description=REPLACE_DOCUMENT_DESCRIPTION
    ),
    force_query: bool = Query(False, alias="force"),
    dry_run_query: bool = Query(False, alias="dry_run"),
    # Ingestion may write many chunks: not capped by DB_STATEMENT_TIMEOUT_MS.
    db: AsyncSession = Depends(unbounded_session),
    user: AuthUser = Depends(require_permission(Permission.DOCUMENTS_WRITE)),
) -> IngestResponse:
    """Server-side file ingestion endpoint supporting .md, .markdown, and .docx formats.

    ``dry_run`` / ``force`` are accepted as form fields or query parameters
    (the dashboard sends query parameters); either one set to true applies.
    Re-uploading the same content (same normalized hash) is ``unchanged``
    with nothing embedded; changed content updates the same document and
    removes its old chunks.
    """
    req_id = getattr(request.state, "request_id", "unknown")
    upload = await _read_upload(file)
    link, require_url = await _upload_link(db, source_url, upload.file_meta)
    result = await _run_ingest(
        db,
        req_id,
        source_name=upload.filename,
        source_type="markdown",
        content=upload.text,
        chunking_strategy=chunking_strategy,
        original_filename=upload.filename,
        converted_from=upload.converted_from,
        original_sha256=hashlib.sha256(upload.contents).hexdigest(),
        original_size_bytes=len(upload.contents),
        warnings=upload.warnings,
        quarantined=upload.quarantined,
        force=force or force_query,
        dry_run=dry_run or dry_run_query,
        actor=user.email,
        request_id=req_id,
        client_ip=get_client_ip(request),
        link=link,
        require_source_url=require_url,
        can_change_link=user.has(Permission.KNOWLEDGE_LINKS),
        rules=UploadRules(replace_document_id=replace_document_id),
    )
    return _to_response(result)


@router.post(
    "/ingest/preview",
    response_model=IngestPreviewResponse,
    responses={k: v for k, v in _FILE_ERRORS.items() if k != status.HTTP_409_CONFLICT},
    summary="Preview an upload: link, matched document, content status (no changes)",
)
async def ingest_preview(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # FastAPI params
    request: Request,
    file: UploadFile = File(...),
    chunking_strategy: str = Form("auto"),
    source_url: str | None = Form(
        None, max_length=MAX_URL_LENGTH, description=_SOURCE_URL_DESCRIPTION
    ),
    replace_document_id: uuid.UUID | None = Form(
        None, description=REPLACE_DOCUMENT_DESCRIPTION
    ),
    db: AsyncSession = Depends(unbounded_session),
    user: AuthUser = Depends(require_permission(Permission.DOCUMENTS_WRITE)),
) -> IngestPreviewResponse:
    """Same fields as ``/ingest/file``; nothing is stored or embedded.

    Runs the real conversion, clean-up, de-duplication, normalisation, hash
    and chunking, then reports the link the upload would store and its
    source, the document it matches (by name, else by link), whether its
    content is new / unchanged / changed, a chunk estimate and warnings. A
    link conflict or replace mismatch is a warning here (409 / 422 on
    ``/ingest/file``).
    """
    req_id = getattr(request.state, "request_id", "unknown")
    upload = await _read_upload(file)
    link, require_url = await _upload_link(db, source_url, upload.file_meta)
    try:
        preview = await preview_upload(
            db,
            source_name=upload.filename,
            content=upload.text,
            link=link,
            rules=UploadRules(replace_document_id=replace_document_id),
            require_source_url=require_url,
            can_change_link=user.has(Permission.KNOWLEDGE_LINKS),
            converted_from=upload.converted_from,
            chunking_strategy=chunking_strategy,
            warnings=upload.warnings,
            quarantined=upload.quarantined,
        )
    except ValueError as val_err:
        raise HTTPException(status_code=422, detail=str(val_err)) from val_err
    logger.info("ingest_preview request_id=%s", req_id)
    return IngestPreviewResponse(**preview)
