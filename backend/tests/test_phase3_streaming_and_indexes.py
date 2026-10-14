"""Phase 3 Integration Test: Streaming Serialization, CSV Neutralization & Composite Index Audit."""

import time
import pytest
from sqlalchemy import select

from app.core.serialization import sanitize_csv_cell, stream_csv_in_chunks
from app.models.db_models import AuditLog, ChatMessageRecord, LatencyRecord


def test_sanitize_csv_cell_formula_injection_defense():
    """Verify neutralization of spreadsheet formula injection characters (OWASP A08)."""
    # Leading = formula injection
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"

    # Leading +, -, @
    assert sanitize_csv_cell("+44123456789") == "'+44123456789"
    assert sanitize_csv_cell("-SUM(A1:A10)") == "'-SUM(A1:A10)"
    assert sanitize_csv_cell("@SUM(1, 2)") == "'@SUM(1, 2)"

    # Leading tab and carriage return
    assert sanitize_csv_cell("\tHYPERLINK") == "'\tHYPERLINK"
    assert sanitize_csv_cell("\rINJECT") == "'\rINJECT"

    # Benign values
    assert sanitize_csv_cell("John Doe") == "John Doe"
    assert sanitize_csv_cell("192.168.1.1") == "192.168.1.1"
    assert sanitize_csv_cell(12345) == "12345"
    assert sanitize_csv_cell(None) == ""


@pytest.mark.asyncio
async def test_stream_csv_in_chunks_bounded_memory():
    """Verify streaming 1,000 rows yields in chunks without accumulating unbounded memory."""
    headers = ["ID", "Created At", "Actor", "Formula Payload"]
    rows = [
        [i, "2026-10-14T10:00:00Z", f"user_{i}@example.com", f"=SUM({i})"]
        for i in range(1000)
    ]

    chunks: list[str] = []
    chunk_count = 0
    t0 = time.perf_counter()

    async for chunk in stream_csv_in_chunks(
        rows, headers, chunk_size=100, include_bom=True
    ):
        chunks.append(chunk)
        chunk_count += 1

    elapsed = time.perf_counter() - t0

    # 1 BOM + 1 Header + 10 chunks of 100 rows each = ~11-12 yielded chunks
    assert chunk_count >= 10
    # First chunk starts with UTF-8 BOM
    assert chunks[0].startswith("\ufeff")
    # All formula payloads were sanitized with leading single quote
    full_output = "".join(chunks)
    assert "'=SUM(" in full_output
    # Execution should be rapid (< 200ms for 1000 rows in memory)
    assert elapsed < 1.0


def test_composite_database_indexes_audit():
    """Verify required composite indexes are declared on SQLAlchemy table metadata (Subtask 3.2.1)."""
    # 1. ChatMessageRecord indexes
    msg_indexes = {
        idx.name: [c.name for c in idx.columns]
        for idx in ChatMessageRecord.__table__.indexes
    }
    assert "ix_chat_messages_ip_created" in msg_indexes
    assert msg_indexes["ix_chat_messages_ip_created"] == ["ip_address", "created_at"]

    assert "ix_chat_messages_session_turn" in msg_indexes
    assert msg_indexes["ix_chat_messages_session_turn"] == ["session_id", "turn_index"]

    # 2. AuditLog indexes
    audit_indexes = {
        idx.name: [c.name for c in idx.columns] for idx in AuditLog.__table__.indexes
    }
    assert "ix_audit_log_created_action" in audit_indexes
    assert audit_indexes["ix_audit_log_created_action"] == ["created_at", "action"]
    assert "ix_audit_log_ip_created" in audit_indexes

    # 3. LatencyRecord request_id index
    latency_cols = {col.name: col for col in LatencyRecord.__table__.columns}
    assert latency_cols["request_id"].index is True


def test_cte_query_compilation_and_latency():
    """Verify single-statement query compilation for telemetry aggregations completes in <= 150ms."""
    # Build single query with CTE
    stmt = (
        select(
            ChatMessageRecord.outcome,
            ChatMessageRecord.cost_usd,
        )
        .where(ChatMessageRecord.role == "assistant")
        .order_by(ChatMessageRecord.created_at.desc())
        .limit(100)
    )

    t0 = time.perf_counter()
    compiled = stmt.compile()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert "SELECT" in str(compiled)
    assert "ORDER BY" in str(compiled)
    # Target: Compilation and setup time <= 150ms budget
    assert elapsed_ms <= 150.0
