"""Request/response schemas for the main /query endpoint."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

_OptionId = Annotated[str, StringConstraints(min_length=1, max_length=16)]


class ClarificationOption(BaseModel):
    """One choice of a clarifying question, grounded in the retrieved context."""

    id: str = Field(
        ..., max_length=16, description="Option id within its question, e.g. `O1`."
    )
    label: str = Field(..., max_length=80, description="1-5 words shown to the user.")
    description: str = Field(
        default="", max_length=200, description="One line: what choosing it means."
    )
    document_ids: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Retrieved documents that can answer this option (boosted on reply).",
    )
    sources: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Their document names (shown as a subtle `from:` hint).",
    )

    @model_validator(mode="before")
    @classmethod
    def _upgrade_single_source(cls, data: Any) -> Any:
        """Rows stored before per-option source lists had one document."""
        if isinstance(data, dict) and "document_ids" not in data:
            doc, name = data.get("document_id"), data.get("source_name")
            data = {
                **{
                    k: v
                    for k, v in data.items()
                    if k not in ("document_id", "source_name", "section")
                },
                "document_ids": [doc] if doc else [],
                "sources": [name] if name else [],
            }
        return data


class ClarificationQuestion(BaseModel):
    """One question of a clarification (shown as one tab of the question card)."""

    id: str = Field(..., max_length=16, description="Question id, e.g. `Q1`.")
    header: str = Field(
        ..., max_length=12, description="Short tab label, e.g. `Topic`."
    )
    question: str = Field(
        ..., max_length=300, description="Full sentence ending with `?`."
    )
    multi_select: bool = Field(
        default=False, description="Several options may be picked together."
    )
    options: list[ClarificationOption] = Field(..., min_length=2, max_length=4)


class Clarification(BaseModel):
    """Questions asked instead of an answer (outcome `clarify`), all in one turn."""

    preamble: str = Field(
        default="",
        max_length=300,
        description="One framing sentence shown above the questions.",
    )
    questions: list[ClarificationQuestion] = Field(..., min_length=1, max_length=5)
    original_query: str = Field(
        ..., max_length=2000, description="The question that was ambiguous."
    )
    allow_free_text: bool = Field(
        default=True,
        description="Each question also accepts an `Other` free-text answer.",
    )

    @model_validator(mode="before")
    @classmethod
    def _upgrade_single_question(cls, data: Any) -> Any:
        """Rows stored before multi-question support had one `question` + `options`."""
        if isinstance(data, dict) and "questions" not in data and "question" in data:
            data = {
                **{k: v for k, v in data.items() if k not in ("question", "options")},
                "questions": [
                    {
                        "id": "Q1",
                        "header": "Topic",
                        "question": data["question"],
                        "options": data.get("options") or [],
                    }
                ],
            }
        return data


class ClarificationAnswer(BaseModel):
    """The user's answer to one clarification question."""

    question_id: str = Field(..., min_length=1, max_length=16)
    option_ids: list[_OptionId] = Field(default_factory=list, max_length=4)
    other_text: str | None = Field(
        default=None, max_length=300, description="Free text typed under `Other`."
    )


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
                    "new_session": False,
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
    # Which chat session this belongs to. History is loaded server-side from
    # the database for that one session only, never from the client.
    session_id: str | None = Field(
        default=None,
        description=(
            "Chat session to continue. An id the caller owns always resumes "
            "that session, however long it was idle, with only its own history. "
            "An unknown or foreign id starts a fresh session. Omit (or send "
            '"") to resume the most recent session of the caller from the same IP '
            "if it was active within the idle window, otherwise a fresh one."
        ),
    )
    new_session: bool = Field(
        default=False,
        description=(
            "Start a fresh session for this question instead of resuming one "
            '("New chat"). The session is created with this first exchange. '
            "Must not be combined with `session_id` (422)."
        ),
    )
    # Optional override for testing/demo (e.g. simulating a specific user's
    # IP). Omit in normal use — the real client IP is auto-detected server-
    # side (see app.core.request_meta.get_client_ip) and used instead.
    ip_address: str | None = None
    # Reply to a clarification (response `clarification`): every answer at
    # once. Optional: a client that ignores clarifications can reply with the
    # option's name (or number, for one question) as a plain `query`.
    clarification_answers: list[ClarificationAnswer] | None = Field(
        default=None,
        max_length=5,
        description=(
            "Answers to the session's last clarification, one per question "
            "(picked option ids and/or `other_text`). The original question and "
            "these answers are rewritten into one explicit query. Omit otherwise; "
            "a plain `query` naming an option is matched too."
        ),
    )

    @model_validator(mode="after")
    def _new_session_excludes_session_id(self) -> "QueryRequest":
        if self.new_session and self.session_id:
            raise ValueError("`new_session` cannot be combined with `session_id`")
        return self


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
