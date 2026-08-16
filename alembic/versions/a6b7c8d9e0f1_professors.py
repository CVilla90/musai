"""Professor identity, encrypted credentials, and the Moodle mapping columns on course.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-08-14

MUSAI stops being single-user here. Three things land together because none of them is useful
alone:

* **`professor`** — one row per signed-in `@uach.mx` address. `Course.professor_id` has existed
  as a nullable int since the first migration and pointed at nothing; this is the table it was
  always meant to point at. The FK is added now that a target exists.
* **`professor_credential`** — the professor's own Moodle/SEGA login, encrypted with a key that
  lives in the environment and never in this database. 🔴 `secret_enc` is a Fernet token; a dump
  of this table decrypts to nothing on its own.
* **four columns on `course`** — what the Moodle mapper reads off a dashboard tile, so a later
  run can reach the course without walking the portal again, and so a restore has a stored name
  to compare a fresh read against.

⚠️ **`professor_id` is deliberately left NULLABLE.** Every existing course has a NULL owner and
this migration does not guess who that is — `python -m musai.backfill_owners --apply` does it,
visibly, with a dry run first. A migration that silently assigned fourteen live courses to
whichever email happened to be in `.env` is not a migration anyone should have to audit.
"""

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "professor",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False, server_default=""),
        sa.Column("picture", sa.String(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_coordinator", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
    )
    # Unique, not merely indexed: the email IS the identity, and two rows for one address would
    # split a professor's courses between them with no error anywhere.
    op.create_index("ix_professor_email", "professor", ["email"], unique=True)

    op.create_table(
        "professor_credential",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("professor_id", sa.Integer(), sa.ForeignKey("professor.id"), nullable=False),
        sa.Column("system", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False, server_default=""),
        # 🔴 A Fernet token, never a password. See musai/security/vault.py.
        sa.Column("secret_enc", sa.String(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_professor_credential_professor_id", "professor_credential",
                    ["professor_id"])
    op.create_index("ix_professor_credential_system", "professor_credential", ["system"])
    # One credential per (professor, system) — a second row for the same pair is not a second
    # account, it is an ambiguity about which password to type into a live login form.
    op.create_index("ix_professor_credential_unique", "professor_credential",
                    ["professor_id", "system"], unique=True)

    for name, col_type in (("moodle_server", sa.String()),
                           ("moodle_fullname", sa.String()),
                           ("cycle", sa.String()),
                           ("mapped_at", sa.DateTime())):
        op.add_column("course", sa.Column(name, col_type, nullable=True))

    # SQLite cannot add a constraint to an existing table without a rebuild; `batch_alter_table`
    # does the rebuild on SQLite and a plain ALTER everywhere else.
    with op.batch_alter_table("course") as batch:
        batch.create_foreign_key("fk_course_professor", "professor", ["professor_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("course") as batch:
        batch.drop_constraint("fk_course_professor", type_="foreignkey")
    for name in ("mapped_at", "cycle", "moodle_fullname", "moodle_server"):
        op.drop_column("course", name)
    op.drop_table("professor_credential")
    op.drop_index("ix_professor_email", table_name="professor")
    op.drop_table("professor")
