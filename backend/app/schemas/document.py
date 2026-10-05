"""Request/response schemas for document ingestion and listing."""
from datetime import datetime

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    source_name: str
    source_type: str = Field(description="pdf, markdown, html, txt, etc.")
    content: str = Field(description="Raw text content to chunk and index")
    chunking_strategy: str = Field(
        default="auto",
        description="One of: fixed_size, semantic, structure_aware, metadata_aware, auto",
    )
    metadata: dict = Field(default_factory=dict)


class IngestResponse(BaseModel):
    document_id: str
    chunk_count: int
    strategy_used: str


class DocumentSummary(BaseModel):
    document_id: str
    source_name: str
    source_type: str
    chunk_count: int
    created_at: datetime
