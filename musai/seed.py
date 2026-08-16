"""Seed script — loads the sample 1-LED-A ODS into the dev DB.

Usage:  python -m musai.seed
"""

from datetime import date
from pathlib import Path

from sqlmodel import Session, select

from musai.db import init_db, engine
from musai.models import Semester, Course, Partial
from musai.grading.ingest import ingest_gradebook

SAMPLE_ODS = Path(__file__).parent.parent / "samples" / \
    "1-LED-A- INGLES I- 5500- 01- 533711 Calificaciones (5).ods"

PARTIALS = [
    dict(name="Parcial 1",              sega_evaluacion="PARCIAL 1",              sega_date="02/03/2026"),
    dict(name="Parcial 2",              sega_evaluacion="PARCIAL 2",              sega_date="20/04/2026"),
    dict(name="Examen Final Ordinario", sega_evaluacion="EXAMEN FINAL ORDINARIO", sega_date="24/05/2026"),
]


def _get_or_create(sess: Session, model, unique_field: str, unique_val, **kwargs):
    stmt = select(model).where(getattr(model, unique_field) == unique_val)
    obj = sess.exec(stmt).first()
    if obj:
        return obj, False
    obj = model(**{unique_field: unique_val}, **kwargs)
    sess.add(obj)
    sess.flush()
    return obj, True


def run():
    init_db()

    if not SAMPLE_ODS.exists():
        print(f"Sample ODS not found: {SAMPLE_ODS}")
        print("Place the Moodle export at the path above and re-run.")
        return

    with Session(engine) as sess:
        # Semester
        semester, created = _get_or_create(
            sess, Semester, "name", "2026-1",
            starts_on=date(2026, 1, 20),
            ends_on=date(2026, 6, 15),
            is_active=True,
        )
        if created:
            print("Created semester 2026-1")

        # Course
        course, created = _get_or_create(
            sess, Course, "group_code", "1-LED-A",
            semester_id=semester.id,
            subject="Inglés I",
            level=1,
            moodle_course_id="7713",   # virtual3 idc for 1-LED-A (prod)
            moodle_env="prod",
            sega_group_label="1-LED-A",
        )
        if created:
            print("Created course 1-LED-A")
        elif not course.moodle_course_id:
            course.moodle_course_id = "7713"
            sess.add(course)

        # Partials
        partial_objs = []
        for p in PARTIALS:
            existing = sess.exec(
                select(Partial).where(
                    Partial.course_id == course.id,
                    Partial.sega_evaluacion == p["sega_evaluacion"],
                )
            ).first()
            if existing:
                partial_objs.append(existing)
            else:
                partial = Partial(course_id=course.id, **p)
                sess.add(partial)
                sess.flush()
                partial_objs.append(partial)
                print(f"  Created partial: {p['name']}")

        # Parse + ingest the ODS (shared with the live Moodle export adapter)
        print(f"\nParsing {SAMPLE_ODS.name}…")
        counts = ingest_gradebook(sess, course, SAMPLE_ODS)
        sess.commit()
        print(f"  students: {counts['students_new']} new, {counts['students_renamed']} renamed")
        print(f"  activities: {counts['activities_new']} new")
        print(f"  grades: {counts['grades_new']} new, {counts['grades_updated']} updated")
        print(f"\nDone. Open http://localhost:8000 to see the cockpit.")
        print(f"Map activities to partials under /courses/{course.id}")


if __name__ == "__main__":
    run()
