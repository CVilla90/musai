"""Record when a course's gradebook was last ingested.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-08-14

One nullable timestamp, added because a number with no date on it lied for two months.

The owner opened 1-LED-A and saw *"Students · 10 · enrolled in MUSAI"* while the live course held
30-something. Nothing was broken: `Enrollment` rows are created **only** by
`grading/ingest.py`, from a gradebook export file, and the last one for that course predated the
2026-2 cohort. The participants page has never been read into this database at all.

🔴 The defect was not the stale count — a cached count is fine — it was that the screen presented
it as a **fact**, with no indication of when it was taken and no way to refresh it from the web.
`musai/automation/messaging.py:211` had already written this exact course up as the worked
example of the hazard. This column is what lets the cockpit say *"as of 3 Aug"* instead.

⚠️ **NULL means "never imported", which must render as those words** and never as `0` or as
today's date. A course that has never had a gradebook and a course whose gradebook is current are
opposite states; collapsing them is how the original bug reads as fine.

No backfill. The existing rows genuinely do not know when they were ingested, and inventing a
date here would manufacture exactly the false confidence the column exists to remove.
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("course", sa.Column("gradebook_ingested_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("course", "gradebook_ingested_at")
