"""usage_event — the itemised MUSAI spend ledger

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-16

One row per priced action (AI call or browser job), so "where did my monthly allowance go?"
is answerable by kind and by day. Page views are deliberately NOT recorded — see
`musai/metering.py`; a row per page view costs more compute than the page view it measures.

`micro_usd` is written once, at the rate card named in `rate_card`, and never recomputed:
Gemini's introductory prices expire and Replit's rates move, and history must not change
price after the fact.
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("detail", sqlmodel.sql.sqltypes.AutoString(), nullable=False,
                  server_default=""),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False,
                  server_default=""),
        sa.Column("seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("micro_usd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_card", sqlmodel.sql.sqltypes.AutoString(), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # The three columns every rollup filters on: one professor, one month, grouped by kind.
    op.create_index("ix_usage_event_actor", "usage_event", ["actor"])
    op.create_index("ix_usage_event_day", "usage_event", ["day"])
    op.create_index("ix_usage_event_kind", "usage_event", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_usage_event_kind", table_name="usage_event")
    op.drop_index("ix_usage_event_day", table_name="usage_event")
    op.drop_index("ix_usage_event_actor", table_name="usage_event")
    op.drop_table("usage_event")
