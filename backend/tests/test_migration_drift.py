"""Model/migration drift guards (no database: reads the Alembic scripts)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.db_models import AuditLog

BACKEND = Path(__file__).resolve().parents[1]


def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(config)


def test_single_alembic_head() -> None:
    assert len(_scripts().get_heads()) == 1


def test_every_audit_log_model_index_is_created_by_a_migration() -> None:
    versions = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (BACKEND / "alembic" / "versions").glob("*.py")
    )
    declared = {index.name for index in AuditLog.__table__.indexes}
    missing = sorted(name for name in declared if f'"{name}"' not in versions)
    assert missing == []
