"""Ingest a Moodle gradebook export into the DB under one Course.

Idempotent upsert: re-running with a fresh export updates student names and grade
values in place (so a re-fetch each week stays the source of truth) without
disturbing the activity→partial mapping a professor has already made.

Used by both ``musai.seed`` (loads the bundled sample) and the Moodle export
adapter (``musai.automation.moodle_export``) after a live download.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from musai.models import Course, Student, Enrollment, Activity, Grade
from musai.grading.importer import load_gradebook, load_roster


def _is_blank_name(name: str | None, matricula: str) -> bool:
    """True if a stored name is just a placeholder we should overwrite with the real one."""
    if not name:
        return True
    n = name.strip()
    return n == "" or n == matricula or n.lower().startswith("student ")


def ingest_gradebook(sess: Session, course: Course, ods_path: str | Path) -> dict:
    """Upsert students, activities and grades from one export into ``course``.

    Returns a counts dict for logging/auditing. Commits nothing — the caller owns
    the transaction.
    """
    grades_df = load_gradebook(ods_path)
    roster_df = load_roster(ods_path)
    name_by_mat = dict(zip(roster_df["matricula"], roster_df["full_name"]))

    counts = {
        "students_new": 0, "students_renamed": 0, "enrollments_new": 0,
        "activities_new": 0,
        "grades_new": 0, "grades_updated": 0,
    }

    # ── Students + enrollments ────────────────────────────────────────────────
    # The ROSTER is the authoritative enrollment list, not the grade rows. At the start of
    # a semester the course has students but no activities at all, so a gradebook-driven
    # loop would ingest zero students. Union of both, roster first, so ordering is stable.
    matriculas = list(roster_df["matricula"])
    seen = set(matriculas)
    for mat in grades_df["matricula"].unique().tolist():
        if mat not in seen:
            matriculas.append(mat)
            seen.add(mat)

    student_map: dict[str, Student] = {}
    for mat in matriculas:
        real_name = name_by_mat.get(mat, mat)
        student = sess.exec(select(Student).where(Student.matricula == mat)).first()
        if student is None:
            student = Student(matricula=mat, full_name=real_name)
            sess.add(student)
            sess.flush()
            counts["students_new"] += 1
        elif _is_blank_name(student.full_name, mat) and real_name != mat:
            student.full_name = real_name
            sess.add(student)
            counts["students_renamed"] += 1
        student_map[mat] = student

        enr = sess.exec(
            select(Enrollment).where(
                Enrollment.student_id == student.id,
                Enrollment.course_id == course.id,
            )
        ).first()
        if enr is None:
            sess.add(Enrollment(student_id=student.id, course_id=course.id))
            counts["enrollments_new"] += 1
    sess.flush()

    # ── Activities (preserve any existing partial/category mapping) ───────────
    activity_map: dict[str, Activity] = {}
    for act_name in grades_df["activity"].unique().tolist():
        act = sess.exec(
            select(Activity).where(
                Activity.course_id == course.id,
                Activity.moodle_item_name == act_name,
            )
        ).first()
        if act is None:
            act = Activity(
                course_id=course.id,
                name=act_name,
                moodle_item_name=act_name,
                category="general",   # default; remapped via the cockpit
            )
            sess.add(act)
            sess.flush()
            counts["activities_new"] += 1
        activity_map[act_name] = act

    # ── Grades (upsert value in place) ────────────────────────────────────────
    for _, row in grades_df.iterrows():
        pct = row["pct"]
        if pct is None or (isinstance(pct, float) and math.isnan(pct)):
            continue
        student = student_map.get(row["matricula"])
        activity = activity_map.get(row["activity"])
        if not student or not activity:
            continue

        grade = sess.exec(
            select(Grade).where(
                Grade.student_id == student.id,
                Grade.activity_id == activity.id,
            )
        ).first()
        if grade is None:
            sess.add(Grade(
                student_id=student.id,
                activity_id=activity.id,
                value=float(pct),
                source="moodle_csv",
            ))
            counts["grades_new"] += 1
        elif abs(grade.value - float(pct)) > 1e-9:
            grade.value = float(pct)
            grade.graded_at = datetime.utcnow()
            sess.add(grade)
            counts["grades_updated"] += 1

    # Stamped here rather than in either caller, because there are two roads in (the CLI's
    # `moodle_export._ingest` and the cockpit's job) and a timestamp set in one of them is a
    # timestamp that silently stops being written the day somebody adds a third.
    # ⚠️ It records when the file was INGESTED, not when Moodle produced it — close enough for
    # "how old is this number", and honest about which event it names.
    course.gradebook_ingested_at = datetime.utcnow()
    sess.add(course)

    return counts
