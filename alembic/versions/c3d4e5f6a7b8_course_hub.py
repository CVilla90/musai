"""hub_profile + course_hub — the course home page's two dicts

Local SQLite gets these from ``init_db()``/``create_all``; this migration is what makes
Postgres (Replit) match.

Both tables hold a JSON blob rather than a column per field, on purpose: the hub's field list
grows every time a colleague asks for something, and none of those additions should require a
migration against a live database.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hub_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("data_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hub_profile_owner", "hub_profile", ["owner"], unique=True)

    op.create_table(
        "course_hub",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("data_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_hub_course_id", "course_hub", ["course_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_course_hub_course_id", table_name="course_hub")
    op.drop_table("course_hub")
    op.drop_index("ix_hub_profile_owner", table_name="hub_profile")
    op.drop_table("hub_profile")
