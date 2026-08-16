"""Importing a course's activities, and proposing what each one counts towards.

This module decides `Activity.partial_id` and `Activity.category`, which are the two inputs
`grading/engine.py` computes a partial grade from. A wrong proposal here does not surface as a
wrong label on a screen — it surfaces as **a wrong number in SEGA, weeks later**. So the tests
lean hard on the two properties that keep that from happening: nothing is ever saved without a
human, and nothing already assigned is ever touched.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from musai import activitymap
from musai.activitymap import (GRADABLE_MODULES, activities_in, import_activities, propose,
                               skipped_types, summarise)
from musai.models import Activity, Course, Partial, Professor, Semester


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as sess:
        yield sess


def _semester(db, name="2026-2", start=date(2026, 8, 1)) -> Semester:
    s = Semester(name=name, starts_on=start, ends_on=date(start.year, 12, 31))
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _course(db, prof, sem, code="1-LED-A", subject="Inglés I") -> Course:
    c = Course(professor_id=prof.id, semester_id=sem.id, subject=subject, level=1,
               group_code=code, moodle_course_id="9023")
    db.add(c)
    db.commit()
    db.refresh(c)
    names = ["Parcial 1", "Parcial 2", "Examen Final Ordinario"]
    for n in names:
        db.add(Partial(course_id=c.id, name=n, sega_evaluacion=n.upper()))
    db.commit()
    return c


def _prof(db, email="professor@uach.mx") -> Professor:
    p = Professor(email=email, full_name="the owner")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _snapshot(*sections) -> dict:
    """`_snapshot(("First Term", [("Workbook 1", "quiz"), ...]), ...)`"""
    out = []
    for i, (name, acts) in enumerate(sections):
        out.append({
            "section": i, "name": name, "hidden": False,
            "activities": [{"cmid": f"{i}{j}", "name": n, "modname": m, "hidden": False}
                           for j, (n, m) in enumerate(acts)],
        })
    return {"idc": "9023", "tabs": [], "sections": out}


# ── import: what counts as an activity ────────────────────────────────────────
def test_only_gradable_module_types_become_activities():
    """🔴 A book is content, not a grade. An `Activity` row is iterated by the grade engine, so
    a Book becoming one adds an ungraded item to a partial and drags every average down."""
    snap = _snapshot(("First Term", [
        ("Workbook 1", "quiz"), ("Reading log", "assign"), ("Watch and Write", "forum"),
        ("First Term", "book"), ("Syllabus", "resource"), ("Welcome", "label"),
        ("Links", "url"), ("Notes", "page"),
    ]))
    names = {a["name"] for a in activities_in(snap)}
    assert names == {"Workbook 1", "Reading log", "Watch and Write"}
    assert skipped_types(snap) == {"book": 1, "resource": 1, "label": 1, "url": 1, "page": 1}


def test_the_gradable_list_is_an_allow_list_not_a_deny_list():
    """Structural. A deny-list lets an unknown type through and into the gradebook."""
    assert "book" not in GRADABLE_MODULES and "label" not in GRADABLE_MODULES
    assert set(GRADABLE_MODULES) == {"quiz", "assign", "forum", "workshop", "lesson"}


def test_an_activity_with_no_name_is_skipped():
    """`.instancename` can come back empty; an activity called "" matches every gradebook
    column and none of them."""
    snap = _snapshot(("Tab", [("", "quiz"), ("  ", "assign"), ("Real one", "quiz")]))
    assert [a["name"] for a in activities_in(snap)] == ["Real one"]


def test_a_hidden_activity_is_imported_and_flagged_not_dropped():
    """A hidden quiz is still in the gradebook. Whether it counts is the professor's call."""
    snap = _snapshot(("Tab", [("Hidden quiz", "quiz")]))
    snap["sections"][0]["activities"][0]["hidden"] = True
    got = activities_in(snap)
    assert len(got) == 1 and got[0]["hidden"] is True


# ── import: additive, always ──────────────────────────────────────────────────
def test_import_creates_the_activities_it_finds(db):
    course = _course(db, _prof(db), _semester(db))
    result = import_activities(db, course, _snapshot(
        ("First Term", [("Workbook 1", "quiz"), ("Essay", "assign")])))
    assert result["created"] == 2
    assert db.exec(select(Activity).where(Activity.course_id == course.id)).all()


def test_re_importing_changes_nothing_and_never_undoes_a_mapping(db):
    """🔴 The one that matters. A re-read after a rename must not silently discard an
    afternoon of mapping — so an existing activity is matched by name and left alone."""
    course = _course(db, _prof(db), _semester(db))
    snap = _snapshot(("First Term", [("Workbook 1", "quiz")]))
    import_activities(db, course, snap)

    act = db.exec(select(Activity).where(Activity.course_id == course.id)).first()
    partial = db.exec(select(Partial).where(Partial.course_id == course.id)).first()
    act.partial_id, act.category = partial.id, "exam"
    db.add(act)
    db.commit()

    result = import_activities(db, course, snap)
    assert result["created"] == 0 and result["matched"] == 1

    again = db.exec(select(Activity).where(Activity.course_id == course.id)).first()
    assert again.partial_id == partial.id, "a re-import cleared a human's mapping"
    assert again.category == "exam", "a re-import reset a human's category"


def test_an_activity_that_vanished_from_moodle_is_reported_not_deleted(db):
    """Deleting the row deletes its grades. Same doctrine as `mapping.apply_mapping`."""
    course = _course(db, _prof(db), _semester(db))
    import_activities(db, course, _snapshot(("T", [("Gone soon", "quiz")])))
    result = import_activities(db, course, _snapshot(("T", [("Still here", "quiz")])))

    assert result["vanished"] == ["Gone soon"]
    assert len(db.exec(select(Activity).where(Activity.course_id == course.id)).all()) == 2


def test_the_same_name_in_two_tabs_creates_one_activity(db):
    """🔴 A course really does carry one name twice (9067 had "Workbook 1" in the term tab and
    in the make-up bank). Two rows for one gradebook column counts every grade twice."""
    course = _course(db, _prof(db), _semester(db))
    result = import_activities(db, course, _snapshot(
        ("First Term", [("Workbook 1", "quiz")]),
        ("Make-up", [("Workbook 1", "quiz")]),
    ))
    rows = db.exec(select(Activity).where(Activity.course_id == course.id)).all()
    assert len(rows) == 1, f"imported {len(rows)} rows for one activity name"
    assert result["repeated"] == ["workbook 1"]


def test_names_differing_only_in_case_or_accent_are_one_activity(db):
    course = _course(db, _prof(db), _semester(db))
    import_activities(db, course, _snapshot(("T", [("Redacción 1", "assign")])))
    result = import_activities(db, course, _snapshot(("T", [("REDACCION 1", "assign")])))
    assert result["created"] == 0 and result["matched"] == 1


# ── propose: the cascade ──────────────────────────────────────────────────────
def test_propose_never_writes_anything(db):
    """The whole safety model. A proposal pre-fills a form; a human presses Save."""
    course = _course(db, _prof(db), _semester(db))
    snap = _snapshot(("First Term", [("Workbook 1", "quiz")]))
    import_activities(db, course, snap)

    proposals = propose(db, course, snap)
    assert proposals and proposals[0].partial_id is not None

    db.expire_all()
    act = db.exec(select(Activity).where(Activity.course_id == course.id)).first()
    assert act.partial_id is None, "propose() saved a mapping — it must only ever suggest"


def test_an_already_mapped_activity_gets_no_proposal(db):
    """"Never overwrite a human-confirmed mapping", enforced by never computing one."""
    course = _course(db, _prof(db), _semester(db))
    snap = _snapshot(("First Term", [("Workbook 1", "quiz"), ("Essay", "assign")]))
    import_activities(db, course, snap)
    act = db.exec(select(Activity).where(Activity.name == "Workbook 1")).first()
    act.partial_id = db.exec(select(Partial)).first().id
    db.add(act)
    db.commit()

    names = {p.name for p in propose(db, course, snap)}
    assert names == {"Essay"}


def test_structure_puts_an_activity_in_the_partial_its_tab_belongs_to(db):
    """The owner's rule: everything up to and including the Exam 1 tab is partial 1."""
    course = _course(db, _prof(db), _semester(db))
    snap = _snapshot(
        ("First Term", [("Workbook 1", "quiz")]),
        ("Exam 1", [("Exam 1", "quiz")]),
        ("Second Term", [("Workbook 2", "quiz")]),
        ("Exam 2", [("Exam 2", "quiz")]),
        ("Third Term", [("Workbook 3", "quiz")]),
    )
    import_activities(db, course, snap)
    by_name = {p.name: p for p in propose(db, course, snap)}

    assert by_name["Workbook 1"].partial_name == "Parcial 1"
    assert by_name["Workbook 2"].partial_name == "Parcial 2"
    assert by_name["Workbook 3"].partial_name == "Examen Final Ordinario"
    assert all(p.source == "structure" for p in by_name.values())


def test_a_forum_is_proposed_as_the_special_and_an_exam_as_the_exam(db):
    course = _course(db, _prof(db), _semester(db))
    snap = _snapshot(("First Term", [
        ("Watch and Write", "forum"), ("Exam 1", "quiz"), ("Workbook 1", "quiz")]))
    import_activities(db, course, snap)
    by_name = {p.name: p for p in propose(db, course, snap)}

    assert by_name["Watch and Write"].category == "special"
    assert by_name["Exam 1"].category == "exam"
    assert by_name["Workbook 1"].category == "general"


def test_without_a_snapshot_every_proposal_says_so_rather_than_guessing(db):
    course = _course(db, _prof(db), _semester(db))
    import_activities(db, course, _snapshot(("T", [("Workbook 1", "quiz")])))

    proposals = propose(db, course, None)
    assert len(proposals) == 1
    assert proposals[0].partial_id is None
    assert proposals[0].source == ""
    assert "not read this course" in proposals[0].why


# ── propose: memory ───────────────────────────────────────────────────────────
def test_memory_copies_a_mapping_from_another_course_of_the_same_subject(db):
    prof, sem = _prof(db), _semester(db)
    old = _course(db, prof, sem, code="1-LED-B")
    new = _course(db, prof, sem, code="1-LED-A")

    import_activities(db, old, _snapshot(("T", [("Workbook 1", "quiz")])))
    act = db.exec(select(Activity).where(Activity.course_id == old.id)).first()
    old_p2 = db.exec(select(Partial).where(Partial.course_id == old.id)
                     .order_by(Partial.id)).all()[1]
    act.partial_id, act.category = old_p2.id, "special"
    db.add(act)
    db.commit()

    import_activities(db, new, _snapshot(("T", [("Workbook 1", "quiz")])))
    p = propose(db, new, None)[0]
    assert p.source == "memory"
    assert p.category == "special"
    assert p.partial_name == "Parcial 2"
    assert "1-LED-B" in p.why


def test_memory_carries_the_partial_by_position_not_by_id(db):
    """🔴 The bug this design exists to avoid. Partial ids are per course, so copying a raw
    `partial_id` across points the new activity at a partial belonging to a DIFFERENT course —
    which the grade engine will compute from, producing a number that means nothing."""
    prof, sem = _prof(db), _semester(db)
    old = _course(db, prof, sem, code="1-LED-B")
    new = _course(db, prof, sem, code="1-LED-A")

    import_activities(db, old, _snapshot(("T", [("Workbook 1", "quiz")])))
    act = db.exec(select(Activity).where(Activity.course_id == old.id)).first()
    old_partials = db.exec(select(Partial).where(Partial.course_id == old.id)
                           .order_by(Partial.id)).all()
    act.partial_id = old_partials[0].id
    db.add(act)
    db.commit()

    import_activities(db, new, _snapshot(("T", [("Workbook 1", "quiz")])))
    p = propose(db, new, None)[0]

    new_partials = db.exec(select(Partial).where(Partial.course_id == new.id)
                           .order_by(Partial.id)).all()
    assert p.partial_id == new_partials[0].id
    assert p.partial_id != old_partials[0].id, (
        "the proposal points at the OTHER course's partial row")


def test_memory_does_not_cross_subjects(db):
    """"Workbook 1" exists in Inglés I and Inglés III and means different things."""
    prof, sem = _prof(db), _semester(db)
    other = _course(db, prof, sem, code="3-LED-B", subject="Inglés III")
    mine = _course(db, prof, sem, code="1-LED-A", subject="Inglés I")

    import_activities(db, other, _snapshot(("T", [("Workbook 1", "quiz")])))
    act = db.exec(select(Activity).where(Activity.course_id == other.id)).first()
    act.partial_id = db.exec(select(Partial).where(Partial.course_id == other.id)).first().id
    db.add(act)
    db.commit()

    import_activities(db, mine, _snapshot(("T", [("Workbook 1", "quiz")])))
    assert propose(db, mine, None)[0].source == ""


def test_memory_never_reads_another_professors_course(db):
    """Same scoping rule as everything else: a colleague's mapping is not yours to read."""
    carlos, sem = _prof(db), _semester(db)
    morayma = _prof(db, email="colleague4@uach.mx")
    hers = _course(db, morayma, sem, code="4-LEF-A")
    his = _course(db, carlos, sem, code="1-LED-A")
    # Same subject on purpose — subject is not what should keep these apart.
    hers.subject = his.subject = "Inglés I"
    db.add(hers)
    db.add(his)
    db.commit()

    import_activities(db, hers, _snapshot(("T", [("Workbook 1", "quiz")])))
    act = db.exec(select(Activity).where(Activity.course_id == hers.id)).first()
    act.partial_id = db.exec(select(Partial).where(Partial.course_id == hers.id)).first().id
    db.add(act)
    db.commit()

    import_activities(db, his, _snapshot(("T", [("Workbook 1", "quiz")])))
    assert propose(db, his, None)[0].source == ""


def test_memory_prefers_the_most_recent_semester(db):
    """Last year's answer must not beat this term's sibling."""
    prof = _prof(db)
    old_sem = _semester(db, name="2025-2", start=date(2025, 8, 1))
    new_sem = _semester(db, name="2026-2", start=date(2026, 8, 1))
    old = _course(db, prof, old_sem, code="1-LED-OLD")
    recent = _course(db, prof, new_sem, code="1-LED-B")
    target = _course(db, prof, new_sem, code="1-LED-A")

    for course, index in ((old, 0), (recent, 2)):
        import_activities(db, course, _snapshot(("T", [("Workbook 1", "quiz")])))
        act = db.exec(select(Activity).where(Activity.course_id == course.id)).first()
        partials = db.exec(select(Partial).where(Partial.course_id == course.id)
                           .order_by(Partial.id)).all()
        act.partial_id = partials[index].id
        db.add(act)
        db.commit()

    import_activities(db, target, _snapshot(("T", [("Workbook 1", "quiz")])))
    p = propose(db, target, None)[0]
    assert "1-LED-B" in p.why, f"took the mapping from the wrong course: {p.why}"
    assert p.partial_name == "Examen Final Ordinario"


def test_memory_beats_structure(db):
    """Cascade order: a mapping a human confirmed elsewhere outranks a guess from a tab name."""
    prof, sem = _prof(db), _semester(db)
    old = _course(db, prof, sem, code="1-LED-B")
    new = _course(db, prof, sem, code="1-LED-A")
    snap = _snapshot(("First Term", [("Workbook 1", "quiz")]),
                     ("Exam 1", [("Exam 1", "quiz")]),
                     ("Second Term", [("Workbook 2", "quiz")]))

    import_activities(db, old, snap)
    act = db.exec(select(Activity).where(Activity.course_id == old.id,
                                         Activity.name == "Workbook 1")).first()
    third = db.exec(select(Partial).where(Partial.course_id == old.id)
                    .order_by(Partial.id)).all()[2]
    act.partial_id = third.id
    db.add(act)
    db.commit()

    import_activities(db, new, snap)
    p = {x.name: x for x in propose(db, new, snap)}["Workbook 1"]
    assert p.source == "memory"
    assert p.partial_name == "Examen Final Ordinario", (
        "structure overrode a mapping the professor had already confirmed elsewhere")


# ── the summary a professor reads ─────────────────────────────────────────────
def test_the_summary_adds_up(db):
    course = _course(db, _prof(db), _semester(db))
    snap = _snapshot(("First Term", [("A", "quiz"), ("B", "quiz")]))
    import_activities(db, course, snap)
    s = summarise(propose(db, course, snap))
    assert s["total"] == 2
    assert s["confident"] == s["memory"] + s["structure"]
    assert s["confident"] + s["unknown"] == s["total"]


def test_a_course_with_no_partials_proposes_nothing(db):
    """Nothing to map to. Returning proposals pointing at no partial would be noise."""
    prof, sem = _prof(db), _semester(db)
    course = Course(professor_id=prof.id, semester_id=sem.id, subject="Biología", level=1,
                    group_code="1-BIO-A", moodle_course_id="1")
    db.add(course)
    db.commit()
    db.refresh(course)
    import_activities(db, course, _snapshot(("T", [("Práctica 1", "assign")])))
    assert propose(db, course, None) == []
