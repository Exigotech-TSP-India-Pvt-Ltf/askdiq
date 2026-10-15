"""add insights tables (taxonomy, turn labels, daily rollup, classifier usage)

Revision ID: 4c1e9a7d2b65
Revises: a9b0c1d2e3f4
Create Date: 2026-10-15 12:00:00.000000

Seeds ``insight_taxonomy`` with the DeployIQ intent list (newplan section 6.3),
the HTML renderer's bot-quality codes and the generic issue list. The seed is a
frozen snapshot: later edits happen through the taxonomy API (F7), never by
changing this file.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c1e9a7d2b65"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CUSTOMER = "Customer / prospect"
OFF_SCOPE = "Off-scope request"

# (code, label, group, substantive, description)
INTENTS = [
    ("SVC", "Platform and services", CUSTOMER, True, None),
    (
        "SEC",
        "Security and compliance",
        CUSTOMER,
        True,
        "Zero Trust, Essential Eight and compliance questions",
    ),
    ("RAI", "Responsible AI and trust", CUSTOMER, True, None),
    ("CO", "Company facts", CUSTOMER, True, None),
    ("CON", "Contact and pricing", CUSTOMER, True, None),
    ("BIZ", "Partner or quote request", CUSTOMER, True, None),
    ("SUP", "Support", CUSTOMER, True, None),
    ("OOS", "Off-scope", OFF_SCOPE, True, None),
    ("INJ", "Prompt injection", OFF_SCOPE, True, None),
    ("AMB", "Ambiguous", OFF_SCOPE, True, None),
    ("OTHER", "Other", OFF_SCOPE, True, None),
    ("GRE", "Greeting", None, False, "Not a question; excluded from KPIs"),
    ("ACK", "Closing", None, False, "Thanks or goodbye; excluded from KPIs"),
]

# (code, label, color, handled)
QUALITIES = [
    ("OK", "Answered correctly", "#008080", True),
    ("CORR", "Correct refusal", "#2E8B8B", True),
    ("SCOPE", "Out of scope", "#6E7C7D", True),
    ("PART", "Partly answered", "#C8A039", False),
    ("FB", "No-info reply", "#8A8F73", False),
    ("CLAR", "Clarifying question", "#5B8FA8", False),
    ("OFF", "Off-target", "#C0362C", False),
    ("FALSE", "False action claim", "#9A3B45", False),
    ("ACK", "Closing", "#B7C4C5", True),
]

# (code, label)
ISSUES = [
    ("KB_GAP", "Info not in knowledge base"),
    ("NARROW", "Answer too narrow"),
    ("WRONG", "Wrong answer"),
    ("GENERIC", "Generic answer"),
    ("NO_ROUTE", "Support request, no route"),
    ("FALSE_ACTION", "Claimed an action it cannot do"),
    ("CLAR_LEFT", "Clarifying question, user left"),
    ("PRICING", "Pricing or quote needs follow-up"),
    ("LLM_FAIL", "Model error"),
    ("OTHER", "Other"),
]


def _seed_rows() -> list[dict]:
    rows: list[dict] = []
    for order, (code, label, group, substantive, description) in enumerate(INTENTS):
        rows.append(
            {
                "kind": "intent",
                "code": code,
                "label": label,
                "description": description,
                "color": None,
                "group": group,
                "substantive": substantive,
                "handled": False,
                "sort_order": order,
            }
        )
    for order, (code, label, color, handled) in enumerate(QUALITIES):
        rows.append(
            {
                "kind": "bot_quality",
                "code": code,
                "label": label,
                "description": None,
                "color": color,
                "group": None,
                "substantive": code != "ACK",
                "handled": handled,
                "sort_order": order,
            }
        )
    for order, (code, label) in enumerate(ISSUES):
        rows.append(
            {
                "kind": "issue",
                "code": code,
                "label": label,
                "description": None,
                "color": None,
                "group": None,
                "substantive": True,
                "handled": False,
                "sort_order": order,
            }
        )
    return rows


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    taxonomy = op.create_table(
        "insight_taxonomy",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("group", sa.String(length=64), nullable=True),
        sa.Column(
            "substantive", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("handled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('intent', 'bot_quality', 'issue')",
            name="ck_insight_taxonomy_kind",
        ),
        sa.CheckConstraint(
            "code ~ '^[A-Z][A-Z0-9_]{0,31}$'", name="ck_insight_taxonomy_code"
        ),
        sa.UniqueConstraint("kind", "code", name="uq_insight_taxonomy_kind_code"),
    )
    op.bulk_insert(taxonomy, [{**row, "is_system": True} for row in _seed_rows()])

    op.create_table(
        "turn_insights",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("bot_quality", sa.String(length=32), nullable=False),
        sa.Column("issue", sa.String(length=32), nullable=True),
        sa.Column("substantive", sa.Boolean(), nullable=False),
        sa.Column("handled", sa.Boolean(), nullable=False),
        sa.Column("friction", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("classified_by", sa.String(length=8), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column(
            "classified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "classified_by IN ('rule', 'llm', 'manual')",
            name="ck_turn_insights_classified_by",
        ),
        sa.CheckConstraint(
            "friction IS NULL OR friction IN ('hostile', 'abandoned')",
            name="ck_turn_insights_friction",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_turn_insights_confidence",
        ),
        sa.UniqueConstraint("message_id", name="uq_turn_insights_message_id"),
    )
    op.create_index("ix_turn_insights_session_id", "turn_insights", ["session_id"])
    op.create_index("ix_turn_insights_intent", "turn_insights", ["intent"])
    op.create_index("ix_turn_insights_bot_quality", "turn_insights", ["bot_quality"])
    op.create_index(
        "ix_turn_insights_classified_at", "turn_insights", ["classified_at"]
    )

    op.create_table(
        "insight_daily_rollup",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("audience", sa.String(length=16), nullable=False),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("quality", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("handled", sa.Integer(), nullable=False),
        sa.Column("substantive", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "day",
            "audience",
            "intent",
            "quality",
            "outcome",
            name="pk_insight_daily_rollup",
        ),
        sa.CheckConstraint(
            "audience IN ('users', 'testing')", name="ck_insight_daily_rollup_audience"
        ),
    )
    op.create_index(
        "ix_insight_daily_rollup_computed_at",
        "insight_daily_rollup",
        ["computed_at"],
    )

    op.create_table(
        "insight_llm_usage",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("token_details", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_insight_llm_usage_created_at", "insight_llm_usage", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_insight_llm_usage_created_at", table_name="insight_llm_usage")
    op.drop_table("insight_llm_usage")
    op.drop_index(
        "ix_insight_daily_rollup_computed_at", table_name="insight_daily_rollup"
    )
    op.drop_table("insight_daily_rollup")
    op.drop_index("ix_turn_insights_classified_at", table_name="turn_insights")
    op.drop_index("ix_turn_insights_bot_quality", table_name="turn_insights")
    op.drop_index("ix_turn_insights_intent", table_name="turn_insights")
    op.drop_index("ix_turn_insights_session_id", table_name="turn_insights")
    op.drop_table("turn_insights")
    op.drop_table("insight_taxonomy")
