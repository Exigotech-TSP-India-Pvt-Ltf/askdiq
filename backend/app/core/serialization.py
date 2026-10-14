"""Streaming Data Serialization and CSV Formula Injection Neutralization (OWASP A08).

Provides safe CSV cell sanitization to prevent DDE/Formula injection in spreadsheet
software (Excel, Google Sheets, LibreOffice) and bounded-memory streaming helpers.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncGenerator, Sequence
from typing import Any

# Dangerous leading characters in spreadsheets that can execute formulas or macros
DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(val: Any) -> str:
    """Neutralize spreadsheet formula injection by prefixing dangerous characters with a single quote.

    Targets OWASP A08: Software and Data Integrity Failures (CSV Injection).
    """
    if val is None:
        return ""
    text = str(val)
    if text.startswith(DANGEROUS_CSV_PREFIXES):
        return f"'{text}"
    return text


async def stream_csv_in_chunks(
    rows_stream: AsyncGenerator[Sequence[Any], None] | Sequence[Sequence[Any]],
    headers: Sequence[str],
    chunk_size: int = 100,
    include_bom: bool = True,
) -> AsyncGenerator[str, None]:
    """Streams CSV rows in bounded chunks to ensure flat server memory consumption (< 5MB)."""
    out = io.StringIO()
    writer = csv.writer(out)

    # 1. Yield UTF-8 BOM for Microsoft Excel compatibility
    if include_bom:
        yield "\ufeff"

    # 2. Yield header row
    sanitized_headers = [sanitize_csv_cell(h) for h in headers]
    writer.writerow(sanitized_headers)
    yield out.getvalue()
    out.seek(0)
    out.truncate(0)

    # 3. Stream rows in bounded chunks
    current_chunk = 0
    if hasattr(rows_stream, "__aiter__"):
        async for row in rows_stream:
            sanitized_row = [sanitize_csv_cell(cell) for cell in row]
            writer.writerow(sanitized_row)
            current_chunk += 1
            if current_chunk >= chunk_size:
                yield out.getvalue()
                out.seek(0)
                out.truncate(0)
                current_chunk = 0
    else:
        for row in rows_stream:
            sanitized_row = [sanitize_csv_cell(cell) for cell in row]
            writer.writerow(sanitized_row)
            current_chunk += 1
            if current_chunk >= chunk_size:
                yield out.getvalue()
                out.seek(0)
                out.truncate(0)
                current_chunk = 0

    # Flush remainder
    remaining = out.getvalue()
    if remaining:
        yield remaining
