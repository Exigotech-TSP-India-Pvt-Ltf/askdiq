#!/usr/bin/env python3
"""Reconcile legacy document names and detect case-variant collisions (Subtask 1.4.8).

Usage:
    python scripts/reconcile_document_names.py          # Dry-run report (default)
    python scripts/reconcile_document_names.py --apply  # Apply safe renames
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure backend root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, update

from app.core.database import AsyncSessionLocal, engine
from app.governance.audit import record_audit_event
from app.ingestion.naming import canonical_source_name
from app.models.db_models import Chunk, Document


async def reconcile(apply: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        # 1. Fetch all documents with their chunk counts
        stmt = (
            select(Document, func.count(Chunk.id).label("chunk_count"))
            .join(Chunk, Chunk.document_id == Document.id, isouter=True)
            .group_by(Document.id)
            .order_by(Document.created_at.asc())
        )
        rows = (await db.execute(stmt)).all()

        existing_names_lower = {doc.source_name.lower(): doc for doc, _ in rows}

        rename_safe: list[tuple[Document, str, int]] = []
        collisions: list[tuple[Document, Document, int]] = []
        already_canonical: list[Document] = []

        for doc, chunk_count in rows:
            try:
                target_canonical = canonical_source_name(doc.source_name)
            except Exception:
                target_canonical = doc.source_name

            if doc.source_name == target_canonical:
                already_canonical.append(doc)
                continue

            target_lower = target_canonical.lower()
            if (
                target_lower in existing_names_lower
                and existing_names_lower[target_lower].id != doc.id
            ):
                # Collision with an existing document
                collisions.append(
                    (doc, existing_names_lower[target_lower], chunk_count)
                )
            else:
                rename_safe.append((doc, target_canonical, chunk_count))

        print("\n" + "=" * 75)
        print("         DOCUMENT NAME RECONCILIATION REPORT")
        print("=" * 75)
        print(f"Total documents inspected: {len(rows)}")
        print(f"Already canonical:        {len(already_canonical)}")
        print(f"Rename-safe (e.g. .docx): {len(rename_safe)}")
        print(f"Collisions detected:      {len(collisions)}")
        print("=" * 75)

        if collisions:
            print("\n[!] COLLISIONS DETECTED (HUMAN INTERVENTION REQUIRED):")
            for doc, collides_with, count in collisions:
                print(f"  - Document ID:   {doc.id}")
                print(
                    f"    Current Name:  {doc.source_name} ({count} chunks, created {doc.created_at})"
                )
                print(
                    f"    Collides With: {collides_with.source_name} (ID: {collides_with.id})"
                )
                print(
                    "    Action: Keep preferred document and delete obsolete copy via audited API."
                )

        if rename_safe:
            print("\n[*] RENAME-SAFE DOCUMENTS:")
            for doc, target, count in rename_safe:
                print(
                    f"  - [{doc.id}] '{doc.source_name}' -> '{target}' ({count} chunks)"
                )

        if not apply:
            print("\n[INFO] Running in DRY-RUN mode. No database changes were made.")
            print(
                "       To apply renames, re-run with: python scripts/reconcile_document_names.py --apply\n"
            )
            await engine.dispose()
            return

        if rename_safe and apply:
            print("\nApplying safe renames to database...")
            for doc, target, count in rename_safe:
                old_name = doc.source_name
                await db.execute(
                    update(Document)
                    .where(Document.id == doc.id)
                    .values(source_name=target)
                )
                await record_audit_event(
                    db,
                    request_id=f"reconcile-{doc.id}",
                    actor="cli:reconcile",
                    action="document_rename",
                    detail={
                        "document_id": str(doc.id),
                        "old_source_name": old_name,
                        "new_source_name": target,
                        "chunk_count": count,
                    },
                    commit=False,
                )
            await db.commit()
            print(f"[SUCCESS] Applied {len(rename_safe)} document renames.\n")

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Reconcile legacy document names.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply rename-safe changes to database (dry-run by default).",
    )
    args = parser.parse_args()
    asyncio.run(reconcile(apply=args.apply))


if __name__ == "__main__":
    main()
