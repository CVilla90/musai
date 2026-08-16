"""Messaging Hub: message_batch, message_recipient, and student.moodle_user_id.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-08-08

`message_batch` exists in v1 even though v1 only writes to it: Moodle offers no marker that
makes a re-send idempotent, so MUSAI's own record is the only way it can know it already
sent something.

`student.moodle_user_id` is the join that never existed — MUSAI keys students by matrícula,
Moodle by user id, and only the course participants page carries both.
"""

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("student", sa.Column("moodle_user_id", sa.String(), nullable=True))
    op.create_index("ix_student_moodle_user_id", "student", ["moodle_user_id"])

    op.create_table(
        "message_batch",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("course_id", sa.Integer(), sa.ForeignKey("course.id"), nullable=False),
        sa.Column("semester_id", sa.Integer(), sa.ForeignKey("semester.id"), nullable=True),
        sa.Column("purpose", sa.String(), nullable=False, server_default="aviso"),
        sa.Column("body", sa.String(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(), nullable=False, server_default="carlos"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("only_me", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("moodle_count", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("body_hash", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_message_batch_course_id", "message_batch", ["course_id"])
    op.create_index("ix_message_batch_semester_id", "message_batch", ["semester_id"])
    op.create_index("ix_message_batch_purpose", "message_batch", ["purpose"])
    op.create_index("ix_message_batch_body_hash", "message_batch", ["body_hash"])

    op.create_table(
        "message_recipient",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("message_batch.id"),
                  nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("student.id"), nullable=True),
        sa.Column("moodle_user_id", sa.String(), nullable=True),
        sa.Column("matricula", sa.String(), nullable=True),
        sa.Column("full_name", sa.String(), nullable=False, server_default=""),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("excluded_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_message_recipient_batch_id", "message_recipient", ["batch_id"])
    op.create_index("ix_message_recipient_student_id", "message_recipient", ["student_id"])
    op.create_index("ix_message_recipient_moodle_user_id", "message_recipient",
                    ["moodle_user_id"])


def downgrade() -> None:
    op.drop_table("message_recipient")
    op.drop_table("message_batch")
    op.drop_index("ix_student_moodle_user_id", table_name="student")
    op.drop_column("student", "moodle_user_id")
