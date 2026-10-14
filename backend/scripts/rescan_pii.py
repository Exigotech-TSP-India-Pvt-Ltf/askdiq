"""CLI tool to rescan stored document chunks for newly introduced PII entities.

Read-only by default: analyzes all stored chunks in PostgreSQL, checks for unredacted
Australian and enterprise PII entities, and reports affected documents needing re-upload.
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.governance.pii import redact_pii
from app.models.db_models import ChunkRecord, DocumentRecord


async def scan_chunks(verbose: bool = False) -> int:
    settings = get_settings()
    allowlist = settings.pii_allowlist

    print("=" * 60)
    print("AskDIQ PII Rescan Report (Read-Only)")
    print("=" * 60)

    findings: dict[str, list[dict]] = {}

    async with AsyncSessionLocal() as session:
        # Load all documents and chunks
        docs_res = await session.execute(select(DocumentRecord))
        docs = {d.id: d.source_name for d in docs_res.scalars().all()}

        chunks_res = await session.execute(select(ChunkRecord))
        chunks = chunks_res.scalars().all()

        print(f"Scanning {len(chunks)} chunks across {len(docs)} documents...\n")

        for chunk in chunks:
            doc_name = docs.get(chunk.document_id, f"unknown-doc-{chunk.document_id}")
            _redacted, found = redact_pii(chunk.text, allowlist=allowlist)

            # Check if any new PII was found that was not previously redacted
            unredacted_pii = [
                t for t in found if f"[REDACTED_{t.upper()}]" not in chunk.text
            ]
            if unredacted_pii:
                if doc_name not in findings:
                    findings[doc_name] = []
                findings[doc_name].append(
                    {
                        "chunk_id": chunk.id,
                        "types": unredacted_pii,
                        "sample": chunk.text[:120].replace("\n", " "),
                    }
                )

    if not findings:
        print("✓ Zero unredacted PII detected across all knowledge chunks.")
        print("Knowledge base is fully compliant with Australian PII policies.")
        return 0

    print(f"⚠️ Found potential unredacted PII in {len(findings)} document(s):\n")
    for doc_name, issues in findings.items():
        print(f"Document: {doc_name}")
        print(f"  Affected chunks: {len(issues)}")
        for issue in issues:
            types_str = ", ".join(issue["types"])
            print(f"    - Chunk #{issue['chunk_id']} [{types_str}]")
            if verbose:
                print(f"      Preview: {issue['sample']}...")
        print()

    print(
        "Recommendation: Re-upload the affected documents to apply new canonical redaction."
    )
    return len(findings)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan knowledge base chunks for Australian PII."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show chunk text previews"
    )
    args = parser.parse_args()

    count = asyncio.run(scan_chunks(verbose=args.verbose))
    sys.exit(0 if count == 0 else 1)


if __name__ == "__main__":
    main()
