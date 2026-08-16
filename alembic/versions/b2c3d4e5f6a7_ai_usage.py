"""ai_usage — per-actor daily Gemini spend ledger

Local SQLite gets this table from ``init_db()``/``create_all``; this migration is what makes
Postgres (Replit) match.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-06
"""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_usage_actor", "ai_usage", ["actor"])
    op.create_index("ix_ai_usage_day", "ai_usage", ["day"])
    # One row per actor per day — the ledger's whole contract.
    op.create_index("ix_ai_usage_actor_day", "ai_usage", ["actor", "day"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_ai_usage_actor_day", table_name="ai_usage")
    op.drop_index("ix_ai_usage_day", table_name="ai_usage")
    op.drop_index("ix_ai_usage_actor", table_name="ai_usage")
    op.drop_table("ai_usage")
