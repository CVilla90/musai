"""Claim every pre-existing course for one professor. Run once, when ownership lands.

`Course.professor_id` was a nullable int pointing at nothing until 2026-08-14, so every course
in the database — the owner's fourteen across two semesters — has a NULL owner. The cockpit now
scopes by owner, and `professors.courses_owned_by` deliberately treats NULL as *nobody's*
rather than *everybody's*. Without this script that is the safe failure (his dashboard goes
blank) rather than the dangerous one (his 186 students show up on a colleague's screen), but it
is still a failure.

    python -m musai.backfill_owners                      # dry run, prints the plan
    python -m musai.backfill_owners --apply
    python -m musai.backfill_owners --email professor@uach.mx --apply

⚠️ **Only ever claims rows that are currently unowned.** A course that already has an owner is
left alone and reported, because the one thing worse than an unowned course is a course quietly
reassigned to whoever ran a script.
"""

import argparse
import sys

from sqlmodel import Session, select

from musai.automation._log import logger as log
from musai.config import settings
from musai.db import engine, init_db
from musai.models import Course, Professor, Semester
from musai.professors import get_or_create


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Assign every unowned course to one professor (dry run by default).")
    ap.add_argument("--email", default=settings.admin_email,
                    help=f"Owner's UACH email (default: {settings.admin_email})")
    ap.add_argument("--apply", action="store_true", help="Actually write (default: dry run)")
    args = ap.parse_args()

    email = (args.email or "").strip().lower()
    if not email.endswith("@" + settings.allowed_email_domain):
        log.error(f"{email!r} is not an @{settings.allowed_email_domain} address. "
                  f"That is the only kind of account that can sign in, so it is the only kind "
                  f"that can own a course.")
        sys.exit(1)

    init_db()
    with Session(engine, expire_on_commit=False) as sess:
        semesters = {s.id: s.name for s in sess.exec(select(Semester)).all()}
        courses = list(sess.exec(select(Course).order_by(Course.semester_id,
                                                         Course.group_code)).all())
        unowned = [c for c in courses if c.professor_id is None]
        owned = [c for c in courses if c.professor_id is not None]

        log.header(f"Backfill course owners → {email}")
        log.info(f"{len(courses)} course(s) total: {len(unowned)} unowned, "
                 f"{len(owned)} already owned")

        for c in unowned:
            log.step(f"CLAIM  {semesters.get(c.semester_id, '?'):<8} {c.group_code:<10} "
                     f"idc={c.moodle_course_id}")
        for c in owned:
            existing = sess.get(Professor, c.professor_id)
            log.info(f"KEEP   {semesters.get(c.semester_id, '?'):<8} {c.group_code:<10} "
                     f"already owned by {existing.email if existing else c.professor_id}")

        if not unowned:
            log.success("Nothing to do — every course already has an owner.")
            return

        if not args.apply:
            log.warning("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return

        prof = get_or_create(sess, email=email)
        for c in unowned:
            c.professor_id = prof.id
            sess.add(c)
        sess.commit()
        log.success(f"{len(unowned)} course(s) now owned by {prof.email} (professor id "
                    f"{prof.id}).")


if __name__ == "__main__":
    main()
