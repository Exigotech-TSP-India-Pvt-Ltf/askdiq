"""Request/response schemas for the main /query endpoint."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClarificationOption(BaseModel):
    """One choice of a clarifying question, grounded in a retrieved document."""

    id: str = Field(..., max_length=16, description="Stable option id, e.g. `T1`.")
    label: str = Field(..., max_length=120, description="Short text shown to the user.")
    document_id: str | None = Field(
        default=None, description="Retrieved document the option comes from."
    )
    source_name: str | None = Field(default=None, description="Document title.")
    section: str | None = Field(default=None, description="Section heading, if any.")


class Clarification(BaseModel):
    """A clarifying question returned instead of an answer (outcome `clarify`)."""

    question: str = Field(..., max_length=300)
    options: list[ClarificationOption] = Field(..., min_length=2, max_length=4)
    original_query: str = Field(
        ..., max_length=2000, description="The question that was ambiguous."
    )
    allow_free_text: bool = Field(
        default=True,
        description="The user may type their own answer instead of picking one.",
    )


class ClarificationChoice(BaseModel):
    """The option a user picked in reply to a clarifying question."""

    original_query: str = Field(..., min_length=1, max_length=2000)
    option_id: str | None = Field(default=None, max_length=16)
    option_label: str = Field(..., min_length=1, max_length=120)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)
    # Server-side only (loaded from the stored message): lets the harness see
    # that the previous assistant turn asked a clarifying question.
    outcome: str | None = Field(default=None, exclude=True)
    clarification: Clarification | None = Field(default=None, exclude=True)


class QueryRequest(BaseModel):
    # Swagger "Try it out" pre-fills this example; it deliberately omits
    # `filters`, whose generic placeholder used to empty every answer.
    # `session_id` is "" rather than null: FastAPI drops null values from the
    # served OpenAPI document, which hid the field; "" means "no session".
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "query": "What is the Trust Center?",
                    "session_id": "",
                    "ip_address": "203.0.113.10",
                }
            ]
        }
    )

    query: str = Field(..., min_length=1, max_length=2000)
    filters: dict | None = Field(
        default=None,
        description=(
            "Optional metadata filters. Only allow-listed keys with scalar values "
            "are applied (source_type, corpus, ingestion_source); others are ignored."
        ),
    )
    # Which chat session this belongs to. Omit to resume the caller's most
    # recent non-expired session (or start a new one if there isn't one) —
    # history is loaded server-side from the database, never from the client.
    session_id: str | None = None
    # Optional override for testing/demo (e.g. simulating a specific user's
    # IP). Omit in normal use — the real client IP is auto-detected server-
    # side (see app.core.request_meta.get_client_ip) and used instead.
    ip_address: str | None = None
    # Reply to a clarifying question (response `clarification`): the picked
    # option and the original question, so the next answer uses both. Optional:
    # a client that ignores clarifications can reply with the option's number
    # or text as a plain `query` and the server matches it.
    clarification_choice: ClarificationChoice | None = Field(
        default=None,
        description=(
            "Reply to a clarifying question: the picked option plus the original "
            "question. Omit otherwise; a plain `query` with the option's number "
            "or text is matched against the last clarifying question too."
        ),
    )


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
    # Actual token usage reported by the Azure OpenAI response for this
    # generation call (app.harness.prompts.call_llm) — not an estimate.
    # None when no LLM call happened (guardrail block, fallback answer,
    # conversational small talk) or the API didn't report usage.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    embedding_tokens: int | None = None
    guardrail_tokens: int | None = None
    cost_usd: float | None = None
    outcome: str = "answered"
    token_details: dict | None = None
    # Set only when outcome == "clarify": the question and its 2-4 options.
    # `answer` still carries the same question with the options as a numbered
    # list, so clients that ignore this field show a sensible reply.
    clarification: Clarification | None = None
