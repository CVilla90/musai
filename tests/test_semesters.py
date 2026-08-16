"""Semester scoping — the dimension that was missing from every lookup.

These tests exist because the bug class is SILENT: with two semesters holding the same
group code, an unscoped `.first()` returns the older row and nothing raises. So each test
builds BOTH semesters and asserts we get the newer one.
"""

from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from musai.models import Course, Enrollment, Semester, Student
from musai.new_semester import normalize_group_code, parse_tile
from musai.semesters import (
    active_semester,
    course_for,
    course_for_student,
    courses_for_student,
    courses_in,
    resolve_semester,
)

PAST = ("2026-1", date(2026, 1, 20), date(2026, 6, 15))
CURRENT = ("2026-2", date(2026, 8, 10), date(2026, 12, 18))


@pytest.fixture
def db(monkeypatch):
    """A fresh two-semester DB with the same group code in both."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        old = Semester(name=PAST[0], starts_on=PAST[1], ends_on=PAST[2], is_active=True)
        new = Semester(name=CURRENT[0], starts_on=CURRENT[1], ends_on=CURRENT[2], is_active=False)
        s.add(old)
        s.add(new)
        s.flush()
        # Same group_code in both semesters — the collision that breaks unscoped lookups.
        for sem, idc in ((old, "7713"), (new, "9023")):
            s.add(Course(semester_id=sem.id, subject="Inglés I", level=1,
                         group_code="1-LED-A", moodle_course_id=idc))
        s.flush()
        s.commit()
        yield s


def _course(db, semester_name):
    sem = resolve_semester(db, semester_name)
    return course_for(db, "1-LED-A", semester_id=sem.id)


# ── active semester resolution ────────────────────────────────────────────────
def test_date_range_beats_a_stale_is_active_flag(db, monkeypatch):
    """2026-1 is still flagged is_active (it was never turned off). Today's date must win."""
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 9, 1))
    assert active_semester(db).name == "2026-2"


def test_falls_back_to_is_active_between_semesters(db, monkeypatch):
    """On a date inside no semester, the explicit flag decides."""
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 7, 1))
    assert active_semester(db).name == "2026-1"


def test_falls_back_to_most_recent_when_nothing_matches(db, monkeypatch):
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 7, 1))
    for s in db.exec(__import__("sqlmodel").select(Semester)).all():
        s.is_active = False
        db.add(s)
    db.commit()
    assert active_semester(db).name == "2026-2"


# ── course lookups ────────────────────────────────────────────────────────────
def test_course_for_returns_current_semester_not_the_older_row(db, monkeypatch):
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 9, 1))
    course = course_for(db, "1-LED-A")
    assert course.moodle_course_id == "9023", "resolved last semester's Moodle course"


def test_course_for_can_reach_a_named_past_semester(db, monkeypatch):
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 9, 1))
    assert course_for(db, "1-LED-A", semester_name="2026-1").moodle_course_id == "7713"


def test_unknown_semester_name_returns_none_rather_than_widening(db):
    assert course_for(db, "1-LED-A", semester_name="1999-9") is None


def test_courses_in_does_not_mix_semesters(db, monkeypatch):
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 9, 1))
    codes = [c.group_code for c in courses_in(db)]
    assert codes == ["1-LED-A"], "listed the same group code once per semester"


# ── student lookups (the SUSAI rail) ──────────────────────────────────────────
def test_returning_student_gets_this_semesters_course(db, monkeypatch):
    """A student enrolled in BOTH semesters must resolve to the current one."""
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 9, 1))
    st = Student(matricula="348521", full_name="ARLETH")
    db.add(st)
    db.flush()
    for c in _all_courses(db):
        db.add(Enrollment(student_id=st.id, course_id=c.id))
    db.commit()

    assert len(courses_for_student(db, st.id, current_only=False)) == 2
    assert course_for_student(db, st.id).moodle_course_id == "9023"


def test_student_not_enrolled_this_semester_gets_nothing(db, monkeypatch):
    """Graduated/dropped student: no data beats LAST semester's data."""
    monkeypatch.setattr("musai.semesters.today_local", lambda: date(2026, 9, 1))
    st = Student(matricula="111111", full_name="OLD STUDENT")
    db.add(st)
    db.flush()
    db.add(Enrollment(student_id=st.id, course_id=_course(db, "2026-1").id))
    db.commit()

    assert course_for_student(db, st.id) is None


def _all_courses(db):
    from sqlmodel import select
    return list(db.exec(select(Course)).all())


# ── tile parsing (Moodle label → MUSAI group code) ────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("1ED-A", "1-LED-A"),
    ("1EF-A", "1-LEF-A"),
    ("1ED-B", "1-LED-B"),
    ("2ED-A", "2-LED-A"),
    ("3MH-A", "3-LMH-A"),
    ("3ED-B", "3-LED-B"),
])
def test_normalize_group_code(raw, expected):
    assert normalize_group_code(raw) == expected


def test_normalize_passes_through_unknown_shapes():
    assert normalize_group_code("WEIRD-99") == "WEIRD-99"


@pytest.mark.parametrize("text,code,level,subject", [
    ("INGLES I Ciclo: PRIMER SEMESTRE Grupo: 1ED-A", "1-LED-A", 1, "Inglés I"),
    ("INGLES II Ciclo: SEGUNDO SEMESTRE Grupo: 2ED-B", "2-LED-B", 2, "Inglés II"),
    ("INGLES III Ciclo: TERCER SEMESTRE Grupo: 3MH-A", "3-LMH-A", 3, "Inglés III"),
])
def test_parse_tile(text, code, level, subject):
    got = parse_tile(text)
    assert got == {"subject": subject, "level": level, "group_code": code}


def test_parse_tile_does_not_confuse_ingles_i_with_iii():
    """The classic Roman-numeral prefix trap: 'INGLES III' must not read as 'INGLES I'."""
    assert parse_tile("INGLES III Ciclo: X Grupo: 3ED-B")["level"] == 3
    assert parse_tile("INGLES I Ciclo: X Grupo: 1ED-A")["level"] == 1


def test_parse_tile_rejects_a_non_english_course():
    assert parse_tile("BIOLOGIA I Ciclo: PRIMER SEMESTRE Grupo: 1BI-A") is None
