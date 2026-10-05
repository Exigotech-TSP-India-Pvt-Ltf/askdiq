import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.db_models import Document, Chunk


async def show_one_document():
    async with AsyncSessionLocal() as db:

        # Select one real document
        result = await db.execute(
            select(Document)
            .where(Document.source_name == "responsible-ai.md")
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
    asyncio.run(show_one_document())