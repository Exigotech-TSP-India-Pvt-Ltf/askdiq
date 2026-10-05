"""Request/response schemas for the main /query endpoint."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    filters: dict | None = Field(
        default=None, description="Metadata filters, e.g. {'source_type': 'pdf'}"
    )
    # Which chat session this belongs to. Omit to resume the caller's most
    # recent non-expired session (or start a new one if there isn't one) —
    # history is loaded server-side from the database, never from the client.
    session_id: str | None = None
    # Optional override for testing/demo (e.g. simulating a specific user's
    # IP). Omit in normal use — the real client IP is auto-detected server-
    # side (see app.core.request_meta.get_client_ip) and used instead.
    ip_address: str | None = None


class SourceChunk(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    score: float
    metadata: dict
    # Embedding computed once at ingestion time, carried through retrieval so
    # the post-generation groundedness check (post_checks.check_groundedness)
    # can reuse it instead of paying for a fresh embedding API call per
    # source per query. Internal only — never serialized to the API response.
    embedding: list[float] | None = Field(default=None, exclude=True)


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]
    grounded: bool
    confidence: float
    request_id: str
    latency_ms: float
    guardrail_flags: list[str] = Field(default_factory=list)
    # Filled in by the /query route after the pipeline runs, once the chat
    # session has been resolved/created — lets the client know which
    # session it's now in (e.g. after an expired one silently rotated).
    session_id: str = ""
    session_title: str = ""
    # Real DB ids for the persisted user+assistant pair, so the client can
    # target either bubble's "delete this exchange" action precisely.
    user_message_id: str = ""
    assistant_message_id: str = ""
    # The IP address this request was attributed to (see
    # app.core.request_meta.get_client_ip for exactly how it's derived —
    # direct TCP peer by default, or the first X-Forwarded-For hop when
    # TRUST_PROXY_HEADERS=true). Surfaced here so it's visible per-request,
    # not only in the DB / governance audit log.
    ip_address: str = ""
    # Whether this query was treated as a follow-up (see
    # app.retrieval.retriever.needs_conversation_context) and, if so, how
    # many prior messages were folded into the LLM prompt/retrieval — capped
    # at 6 (last 3 Q&A exchanges). 0 when not a follow-up. Surfaced here so
    # it's visible per-request without needing server logs.
    follow_up_detected: bool = False
    history_turns_sent: int = 0
