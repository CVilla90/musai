"""Ingesting a course that has students but NO activities yet.

This is the shape of every course at the start of a semester, before the professor
restores content into it. The original ingest built its student list from the grade rows,
so an empty gradebook ingested zero students — the roster was parsed and then ignored.
The fixture is a real export pulled from 1-LED-A (idc 9023) on 2026-08-06.
"""

from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from musai.grading.ingest import ingest_gradebook
from musai.models import Activity, Course, Enrollment, Grade, Semester, Student

FIXTURE = Path(__file__).parent.parent / "samples" / "empty_course_roster_only.ods"


@pytest.fixture
def course_session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        sem = Semester(name="2026-2", starts_on=date(2026, 8, 10),
                       ends_on=date(2026, 12, 18), is_active=True)
        s.add(sem)
        s.flush()
        course = Course(semester_id=sem.id, subject="Inglés I", level=1,
                        group_code="1-LED-A", moodle_course_id="9023")
        s.add(course)
        s.flush()
        yield s, course


@pytest.mark.skipif(not FIXTURE.exists(), reason="roster-only fixture not present")
def test_students_are_ingested_from_an_activity_less_course(course_session):
    sess, course = course_session
    counts = ingest_gradebook(sess, course, FIXTURE)
    sess.commit()

    students = sess.exec(select(Student)).all()
    assert len(students) == 3, "roster was parsed but the students were dropped"
    assert counts["students_new"] == 3
    assert counts["enrollments_new"] == 3
    assert counts["activities_new"] == 0
    assert counts["grades_new"] == 0

    # Real names, not matrícula placeholders.
    names = sorted(s.full_name for s in students)
    assert all(not n.isdigit() for n in names)
    assert any("CARAVEO" in n for n in names)


@pytest.mark.skipif(not FIXTURE.exists(), reason="roster-only fixture not present")
def test_enrollments_land_on_the_right_course(course_session):
    sess, course = course_session
    ingest_gradebook(sess, course, FIXTURE)
    sess.commit()

    enrollments = sess.exec(select(Enrollment)).all()
    assert len(enrollments) == 3
    assert {e.course_id for e in enrollments} == {course.id}


@pytest.mark.skipif(not FIXTURE.exists(), reason="roster-only fixture not present")
def test_reingest_is_idempotent(course_session):
    """A re-fetch during the semester must not duplicate students or enrollments."""
    sess, course = course_session
    ingest_gradebook(sess, course, FIXTURE)
    sess.commit()
    second = ingest_gradebook(sess, course, FIXTURE)
    sess.commit()

    assert second["students_new"] == 0
    assert second["enrollments_new"] == 0
    assert len(sess.exec(select(Student)).all()) == 3
    assert len(sess.exec(select(Enrollment)).all()) == 3
    assert len(sess.exec(select(Activity)).all()) == 0
    assert len(sess.exec(select(Grade)).all()) == 0
