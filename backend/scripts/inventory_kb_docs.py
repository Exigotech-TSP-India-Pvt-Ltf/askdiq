"""Read-only inventory report script for backend/data/real_docs (Subtask 4.2.1).

Scans:
1. Files in backend/data/real_docs
2. Manifest list in scripts/ingest_real_docs.py
3. Documents present in the PostgreSQL database table
4. Rule checks via canonical_source_name (spaces, parentheses, double extensions, non-.md)

Outputs a comprehensive inventory report to backend/reports/kb_inventory_report.json
without altering or deleting any files.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.ingestion.naming import canonical_source_name
from app.models.db_models import Document
from scripts.ingest_real_docs import DOCUMENTS as MANIFEST_DOCS

SOURCE_DIR = BASE_DIR / "data" / "real_docs"
REPORTS_DIR = BASE_DIR / "reports"


async def generate_inventory_report():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "source_directory": str(SOURCE_DIR),
        "total_files": 0,
        "manifest_count": len(MANIFEST_DOCS),
        "files": [],
        "manifest_untracked": [],
        "manifest_missing": [],
        "naming_issues": [],
        "non_markdown_files": [],
        "duplicates_or_superseded": [],
    }

    # 1. Scan filesystem
    fs_files = sorted(os.listdir(SOURCE_DIR))
    report["total_files"] = len(fs_files)

    for fname in fs_files:
        fpath = SOURCE_DIR / fname
        size = fpath.stat().st_size
        is_md = fname.lower().endswith(".md")
        in_manifest = fname in MANIFEST_DOCS

        naming_issue = None
        canonical_target = None
        try:
            canonical_target = canonical_source_name(fname)
            if canonical_target != fname and is_md:
                naming_issue = f"Canonical rule would change name from '{fname}' to '{canonical_target}'"
        except Exception as e:
            naming_issue = f"Failed canonical validation: {e!s}"

        file_info = {
            "name": fname,
            "size_bytes": size,
            "is_markdown": is_md,
            "in_manifest": in_manifest,
            "canonical_target": canonical_target,
            "naming_issue": naming_issue,
        }
        report["files"].append(file_info)

        if not is_md:
            report["non_markdown_files"].append(fname)
        if not in_manifest:
            report["manifest_untracked"].append(fname)
        if naming_issue:
            report["naming_issues"].append({"file": fname, "issue": naming_issue})

        # Identify superseded / duplicate files based on stem / name patterns
        if (
            "old" in fname.lower()
            or "(1)" in fname
            or (not is_md and (SOURCE_DIR / f"{Path(fname).stem}.md").exists())
        ):
            report["duplicates_or_superseded"].append(fname)

    for doc in MANIFEST_DOCS:
        if not (SOURCE_DIR / doc).exists():
            report["manifest_missing"].append(doc)

    # 2. Query Database table
    async with AsyncSessionLocal() as db:
        db_docs = (await db.scalars(select(Document))).all()
        report["database_documents"] = [
            {
                "id": str(d.id),
                "source_name": d.source_name,
                "created_at": d.created_at.isoformat(),
                "content_hash": d.content_hash,
                "meta": d.doc_metadata,
            }
            for d in db_docs
        ]

    out_file = REPORTS_DIR / "kb_inventory_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Inventory Report generated successfully at: {out_file}")
    print(f"Total files on disk: {len(fs_files)}")
    print(
        f"Non-markdown files: {len(report['non_markdown_files'])} -> {report['non_markdown_files']}"
    )
    print(
        f"Untracked by manifest: {len(report['manifest_untracked'])} -> {report['manifest_untracked']}"
    )
    print(
        f"Superseded/Duplicates: {len(report['duplicates_or_superseded'])} -> {report['duplicates_or_superseded']}"
    )
    print(f"Database documents: {len(report['database_documents'])}")


if __name__ == "__main__":
    asyncio.run(generate_inventory_report())
