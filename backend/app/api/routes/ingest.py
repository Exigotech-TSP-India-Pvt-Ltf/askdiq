"""Document ingestion endpoints (incremental: only new/changed chunks are embedded)."""

import hashlib
import logging

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
from app.core.request_meta import get_client_ip
from app.ingestion.docx_converter import convert_docx, screen_markdown
from app.ingestion.service import ingest_text
from app.schemas.document import IngestRequest, IngestResponse

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()


def _to_response(result: dict) -> IngestResponse:
    """Map the ingestion service result onto the public response model."""
    return IngestResponse(
        **{k: result[k] for k in IngestResponse.model_fields if k in result}
    )


@router.post("/ingest", response_model=IngestResponse)
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
    content, warnings, quarantined = screen_markdown(body.content)

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
        )
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


@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    chunking_strategy: str = Form("auto"),
    force: bool = Form(False),
    dry_run: bool = Form(False),
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
    elif lower_name.endswith((".md", ".markdown")):
        converted_from = "md"
        try:
            text_content = contents.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=422, detail="File is not valid UTF-8 text"
            ) from exc
        text_content, screen_warnings, quarantined = screen_markdown(text_content)
        warnings.extend(screen_warnings)
    else:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file format for '{filename}'. Only .docx and .md are supported.",
        )

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
        )
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
