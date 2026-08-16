"""initial

Revision ID: d5572cdb0a35
Revises:
Create Date: 2026-06-13

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "d5572cdb0a35"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False, server_default="system"),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("env", sa.String(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("detail_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    op.create_table(
        "job_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("params_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(), nullable=False, server_default="carlos"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("result_json", sa.String(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "semester",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_semester_name", "semester", ["name"])

    op.create_table(
        "student",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("matricula", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("matricula"),
    )
    op.create_index("ix_student_matricula", "student", ["matricula"])

    op.create_table(
        "usage_counter",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("msg_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_counter_phone_e164", "usage_counter", ["phone_e164"])
    op.create_index("ix_usage_counter_day", "usage_counter", ["day"])

    op.create_table(
        "course",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("professor_id", sa.Integer(), nullable=True),
        sa.Column("semester_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("group_code", sa.String(), nullable=False),
        sa.Column("moodle_course_id", sa.String(), nullable=True),
        sa.Column("moodle_env", sa.String(), nullable=False, server_default="prod"),
        sa.Column("sega_group_label", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["semester_id"], ["semester.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_semester_id", "course", ["semester_id"])
    op.create_index("ix_course_group_code", "course", ["group_code"])
    op.create_index("ix_course_professor_id", "course", ["professor_id"])

    op.create_table(
        "whatsapp_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("bound_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["student.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whatsapp_link_student_id", "whatsapp_link", ["student_id"])
    op.create_index("ix_whatsapp_link_phone_e164", "whatsapp_link", ["phone_e164"])

    op.create_table(
        "conversation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone_e164", sa.String(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["student.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_phone_e164", "conversation", ["phone_e164"])
    op.create_index("ix_conversation_student_id", "conversation", ["student_id"])

    op.create_table(
        "enrollment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["student.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enrollment_student_id", "enrollment", ["student_id"])
    op.create_index("ix_enrollment_course_id", "enrollment", ["course_id"])

    op.create_table(
        "partial",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sega_evaluacion", sa.String(), nullable=False),
        sa.Column("sega_date", sa.String(), nullable=True),
        sa.Column("weight_general", sa.Float(), nullable=False, server_default="0.6"),
        sa.Column("weight_special", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("weight_exam", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("moodle_section_ref", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_partial_course_id", "partial", ["course_id"])

    op.create_table(
        "activity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("partial_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("moodle_item_name", sa.String(), nullable=True),
        sa.Column("max_points", sa.Float(), nullable=False, server_default="100"),
        sa.Column("ai_gradable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rubric", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["course.id"]),
        sa.ForeignKeyConstraint(["partial_id"], ["partial.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activity_course_id", "activity", ["course_id"])
    op.create_index("ix_activity_partial_id", "activity", ["partial_id"])

    op.create_table(
        "grade",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="moodle_csv"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("graded_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["activity_id"], ["activity.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["student.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_grade_student_id", "grade", ["student_id"])
    op.create_index("ix_grade_activity_id", "grade", ["activity_id"])

    op.create_table(
        "partial_grade",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("partial_id", sa.Integer(), nullable=False),
        sa.Column("value_0_10", sa.Float(), nullable=False),
        sa.Column("components_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("computed_at", sa.DateTime(), nullable=False),
        sa.Column("sega_status", sa.String(), nullable=False, server_default="none"),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["partial_id"], ["partial.id"]),
        sa.ForeignKeyConstraint(["student_id"], ["student.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_partial_grade_student_id", "partial_grade", ["student_id"])
    op.create_index("ix_partial_grade_partial_id", "partial_grade", ["partial_id"])

    op.create_table(
        "message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("wa_message_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_message_conversation_id", "message", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("message")
    op.drop_table("partial_grade")
    op.drop_table("grade")
    op.drop_table("activity")
    op.drop_table("partial")
    op.drop_table("enrollment")
    op.drop_table("conversation")
    op.drop_table("whatsapp_link")
    op.drop_table("course")
    op.drop_table("usage_counter")
    op.drop_table("student")
    op.drop_table("semester")
    op.drop_table("job_request")
    op.drop_table("audit_log")
