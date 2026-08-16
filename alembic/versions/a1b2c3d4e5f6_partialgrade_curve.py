"""partial_grade curve + extra-credit columns

Revision ID: a1b2c3d4e5f6
Revises: d5572cdb0a35
Create Date: 2026-06-13

Adds the explicit human-adjustment fields to partial_grade. The machine grade
(value_0_10) is unchanged; final_value_0_10 is the curve/override base, and
extra_points is additive human extra-credit. Uploaded = clamp(base + extra).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "d5572cdb0a35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("partial_grade",
                  sa.Column("curve_mode", sa.String(), nullable=False, server_default="none"))
    op.add_column("partial_grade",
                  sa.Column("final_value_0_10", sa.Float(), nullable=True))
    op.add_column("partial_grade",
                  sa.Column("curve_note", sa.String(), nullable=True))
    op.add_column("partial_grade",
                  sa.Column("extra_points", sa.Float(), nullable=False, server_default="0"))
    op.add_column("partial_grade",
                  sa.Column("extra_note", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("partial_grade", "extra_note")
    op.drop_column("partial_grade", "extra_points")
    op.drop_column("partial_grade", "curve_note")
    op.drop_column("partial_grade", "final_value_0_10")
    op.drop_column("partial_grade", "curve_mode")
