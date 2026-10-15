"""Print one document's stored chunks (heading, char count, content).

Run from backend/:
    python scripts/show_chunking.py [source_name]

Defaults to e8-learn-more.md. Use it to sanity-check chunking after a change.
"""

import asyncio
import sys
from pathlib import Path

# Running `python scripts/show_chunking.py` puts scripts/ (not backend/)
# on sys.path[0], so `app` isn't importable without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.db_models import Chunk, Document

DEFAULT_SOURCE = "e8-learn-more.md"


async def show_one_document(source_name: str = DEFAULT_SOURCE):
    async with AsyncSessionLocal() as db:
        # Select one real document
        result = await db.execute(
            select(Document).where(Document.source_name == source_name)
        )

        document = result.scalar_one()

        # Get all chunks for this document in order
        result = await db.execute(
            select(Chunk)
            .where(Chunk.document_id == document.id)
            .order_by(Chunk.chunk_index)
        )

        chunks = result.scalars().all()

        print("\n" + "=" * 100)
        print(f"DOCUMENT: {document.source_name}")
        print(f"TOTAL CHUNKS: {len(chunks)}")
        print("STRATEGY: structure_aware")
        print("=" * 100)

        for chunk in chunks:
            metadata = chunk.chunk_metadata or {}

            section = (
                metadata.get("section_heading")
                or metadata.get("section")
                or metadata.get("heading")
                or "N/A"
            )

            content = chunk.content or ""

            print("\n" + "-" * 100)
            print(f"CHUNK {chunk.chunk_index}")
            print(f"SECTION: {section}")
            print(f"CHARACTERS: {len(content)}")
            print("-" * 100)

            print(content)

            print("-" * 100)


if __name__ == "__main__":
    asyncio.run(show_one_document(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE))
