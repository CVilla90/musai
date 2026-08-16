"""professor.language — the remembered EN/ES choice

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-08-16

🔴 **Nullable, with NO server default, and that is the entire point of this migration.**

`NULL` means *"this professor has never chosen a language"*. It does not mean *"English"*.
Defaulting the column to `'en'` would take every professor who has never seen the picker and
record a decision they did not make — and then, the day MUSAI's default changes, there would be
no way left to tell them apart from the ones who genuinely chose English.

MUSAI has paid for exactly this twice: a course whose NULL owner was read as "everybody's", and
a landing page documented "creamy-light by default" while a dark-mode machine served ink every
time. ⭐ A stored choice outranks a changed default forever; a NULL is what makes that possible.
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "professor",
        sa.Column("language", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("professor", "language")
