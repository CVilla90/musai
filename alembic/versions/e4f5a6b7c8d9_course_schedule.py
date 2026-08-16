"""course_schedule — the Cronograma's saved settings, tab map and course snapshot

Revision ID: e4f5a6b7c8d9
Revises: c3d4e5f6a7b8
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "course_schedule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id"),
    )
    op.create_index("ix_course_schedule_course_id", "course_schedule", ["course_id"])


def downgrade() -> None:
    op.drop_index("ix_course_schedule_course_id", table_name="course_schedule")
    op.drop_table("course_schedule")
