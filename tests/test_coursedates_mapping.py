"""Tests for feeding `Activity.partial_id` from the Cronograma tab map.

The bug this suite exists to keep dead is not a crash. It is the **silent** one: an activity
mapped into the wrong parcial produces a plausible-looking grade that is wrong, and nothing
in the system notices. So most of what is pinned here is refusal — the cases where the right
answer is to report and decline rather than to pick.
"""

from datetime import date

import pytest
from sqlmodel import Session, select

from musai.coursedates import mapping, tabmap
from musai.models import Activity, Course, Partial, Semester

# ── the shape of a real course, cut down ────────────────────────────────────────────────
TABS = [
    {"section": 0, "label": "Introduction"},
    {"section": 1, "label": "First Term"},
    {"section": 2, "label": "Exam 1"},
    {"section": 3, "label": "Second Term"},
    {"section": 4, "label": "Exam 2"},
    {"section": 5, "label": "Third Term"},
    {"section": 6, "label": "Exam 3"},
    {"section": 7, "label": "Make-up Exam"},
    {"section": 8, "label": "English: Exploratory Exam"},
    {"section": 9, "label": "Other resources", "hidden": True},
]


def _snapshot(**overrides) -> dict:
    sections = [
        {"section": 0, "name": "Introduction", "activities": []},
        {"section": 1, "name": "First Term", "activities": [
            {"cmid": "1", "modname": "quiz", "name": "Alphabet"},
            {"cmid": "2", "modname": "label", "name": ""},
            {"cmid": "3", "modname": "book", "name": "First Term"}]},
        {"section": 2, "name": "Exam 1", "activities": [
            {"cmid": "4", "modname": "quiz", "name": "First term exam"}]},
        {"section": 3, "name": "Second Term", "activities": [
            {"cmid": "5", "modname": "quiz", "name": "Some & Any"}]},
        {"section": 4, "name": "Exam 2", "activities": [
            {"cmid": "6", "modname": "quiz", "name": "Second term exam"}]},
        {"section": 5, "name": "Third Term", "activities": [
            {"cmid": "7", "modname": "assign", "name": "TypeRacer Practice Challenge"}]},
        {"section": 6, "name": "Exam 3", "activities": [
            {"cmid": "8", "modname": "quiz", "name": "Third term exam"}]},
        {"section": 7, "name": "Make-up Exam", "activities": [
            {"cmid": "9", "modname": "quiz", "name": "English I: Make-up Exam"}]},
        {"section": 8, "name": "English: Exploratory Exam", "activities": [
            {"cmid": "10", "modname": "quiz", "name": "Exploratory"}]},
        {"section": 9, "name": "Other resources", "hidden": True, "activities": [
            {"cmid": "11", "modname": "quiz", "name": "First partial exam"}]},
    ]
    snap = {"idc": "9023", "sections": sections}
    snap.update(overrides)
    return snap


@pytest.fixture
def course(session: Session) -> Course:
    sem = Semester(name="2026-2", starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18),
                   is_active=True)
    session.add(sem)
    session.commit()
    session.refresh(sem)
    c = Course(semester_id=sem.id, subject="Inglés I", level=1,
               group_code=f"1-LED-{sem.id}", moodle_course_id="9023")
    session.add(c)
    session.commit()
    session.refresh(c)
    for name, sega in (("Parcial 1", "PARCIAL 1"), ("Parcial 2", "PARCIAL 2"),
                       ("Examen Final Ordinario", "EXAMEN FINAL ORDINARIO")):
        session.add(Partial(course_id=c.id, name=name, sega_evaluacion=sega))
    session.commit()
    return c


def _add(session, course, *names) -> None:
    for n in names:
        session.add(Activity(course_id=course.id, name=n, moodle_item_name=n,
                             category="general"))
    session.commit()


def _map(session, course, **kw):
    return mapping.map_activities(session, course, snapshot=_snapshot(),
                                  tab_map=tabmap.guess(TABS, 3), **kw)


# ── normalize: the join that was broken ─────────────────────────────────────────────────

def test_the_accesshide_type_word_is_stripped():
    """`.instancename` carried a screen-reader span naming the type, so all 80 names read
    as 'Alphabet Examen' and matched nothing. Fixed at the source; still rescued here."""
    assert mapping.normalize("Alphabet Examen") == mapping.normalize("Alphabet")
    assert mapping.normalize("Watch and Write Foro") == mapping.normalize("Watch and Write")
    assert mapping.normalize("First Term Libro") == mapping.normalize("First Term")


def test_a_type_word_inside_the_name_is_not_stripped():
    """Only a TRAILING type word is a suffix. 'Examen de práctica' is a real activity name."""
    assert mapping.normalize("Examen de practica") == "examen de practica"


def test_the_curly_apostrophe_is_folded():
    """A real activity is `(S1) Possessive Case ’s / s’`, and the export and the page do not
    agree on which apostrophe character that is."""
    assert mapping.normalize("Possessive Case ’s") == mapping.normalize("Possessive Case 's")


def test_accents_and_spacing_fold():
    assert mapping.normalize("  Recuperación   final ") == "recuperacion final"


# ── period → partial, on a faculty whose third slot is not called "Parcial 3" ────────────

def test_period_three_maps_to_examen_final_ordinario(session, course):
    """SEGA's third slot is named EXAMEN FINAL ORDINARIO, so a name match on 'Parcial 3'
    finds nothing and position has to win."""
    partials = session.exec(select(Partial).where(Partial.course_id == course.id)).all()
    by = mapping.partials_by_period(partials, 3)
    assert by[1].name == "Parcial 1"
    assert by[2].name == "Parcial 2"
    assert by[3].name == "Examen Final Ordinario"


def test_a_course_that_really_names_them_1_2_3_is_not_renumbered(session, course):
    for p in session.exec(select(Partial).where(Partial.course_id == course.id)).all():
        if p.name == "Examen Final Ordinario":
            p.name = "Parcial 3"
            session.add(p)
    session.commit()
    partials = session.exec(select(Partial).where(Partial.course_id == course.id)).all()
    assert mapping.partials_by_period(partials, 3)[3].name == "Parcial 3"


# ── the mapping itself ──────────────────────────────────────────────────────────────────

def test_content_and_its_exam_land_in_the_same_parcial(session, course):
    _add(session, course, "Alphabet", "First term exam")
    rep = _map(session, course)
    got = {m.name: (m.partial_name, m.suggested_category) for m in rep.matches}
    assert got["Alphabet"] == ("Parcial 1", "general")
    assert got["First term exam"] == ("Parcial 1", "exam")


def test_the_hidden_bank_is_skipped_not_mapped(session, course):
    """Nine of these exist in 1-LED-A. Dating or grading them would resurrect deprecated
    material; the hidden flag is a measurement and it wins over the name."""
    _add(session, course, "First partial exam")
    rep = _map(session, course)
    assert rep.by_status(mapping.SKIPPED)[0].name == "First partial exam"
    assert rep.by_status(mapping.SKIPPED)[0].partial_id is None


def test_the_exploratory_exam_is_skipped(session, course):
    _add(session, course, "Exploratory")
    assert len(_map(session, course).by_status(mapping.SKIPPED)) == 1


def test_the_makeup_is_flagged_for_review_not_guessed(session, course):
    """A make-up replaces *a* parcial and only the professor knows which."""
    _add(session, course, "English I: Make-up Exam")
    m = _map(session, course).by_status(mapping.REVIEW)[0]
    assert m.partial_id is None and "profesor" in m.reason


def test_a_gradebook_row_with_no_activity_on_the_page_is_unmatched(session, course):
    """Usually means the gradebook is last semester's — a fact worth reporting, not hiding."""
    _add(session, course, "A quiz that was deleted")
    rep = _map(session, course)
    assert rep.by_status(mapping.UNMATCHED)[0].name == "A quiz that was deleted"
    assert rep.notes and "semestre anterior" in rep.notes[0]


def test_a_duplicated_name_refuses_rather_than_picks(session, course):
    """The same quiz left in the bank AND in a term tab. Choosing one silently would put a
    grade in a parcial by iteration order."""
    snap = _snapshot()
    snap["sections"][9]["activities"].append(
        {"cmid": "12", "modname": "quiz", "name": "Alphabet"})
    _add(session, course, "Alphabet")
    rep = mapping.map_activities(session, course, snapshot=snap,
                                 tab_map=tabmap.guess(TABS, 3))
    m = rep.by_status(mapping.AMBIGUOUS)[0]
    assert m.partial_id is None and "2 pestañas" in m.reason


def test_a_label_or_book_is_never_a_mapping_target(session, course):
    """Section 1 holds a book called 'First Term'. Nothing gradeable may match it."""
    _add(session, course, "First Term")
    assert _map(session, course).by_status(mapping.UNMATCHED)[0].name == "First Term"


# ── the write, and its rails ────────────────────────────────────────────────────────────

def test_dry_run_is_the_default_and_writes_nothing(session, course):
    _add(session, course, "Alphabet")
    rep = _map(session, course)
    assert rep.dry_run and rep.written == 0
    act = session.exec(select(Activity).where(Activity.course_id == course.id)).first()
    assert act.partial_id is None


def test_apply_writes_the_partial(session, course):
    _add(session, course, "Alphabet")
    rep = _map(session, course, apply=True)
    assert rep.written == 1
    act = session.exec(select(Activity).where(Activity.course_id == course.id)).first()
    assert act.partial_id == rep.matches[0].partial_id


def test_re_running_is_a_no_op(session, course):
    """A tool that is re-run must settle, or its report stops being worth reading."""
    _add(session, course, "Alphabet")
    _map(session, course, apply=True)
    again = _map(session, course, apply=True)
    assert again.written == 0
    assert again.by_status(mapping.ALREADY)


def test_moving_an_activity_to_a_DIFFERENT_parcial_needs_regrade(session, course):
    """`PartialGrade` rows were computed from the old mapping; moving silently invalidates
    them without recomputing."""
    _add(session, course, "Alphabet")
    others = session.exec(select(Partial).where(Partial.course_id == course.id)).all()
    act = session.exec(select(Activity).where(Activity.course_id == course.id)).first()
    act.partial_id = [p for p in others if p.name == "Parcial 2"][0].id
    session.add(act)
    session.commit()

    rep = _map(session, course, apply=True)
    assert rep.written == 0
    m = rep.by_status(mapping.CHANGED)[0]
    assert "regrade" in m.reason
    session.refresh(act)
    assert act.partial_id == [p for p in others if p.name == "Parcial 2"][0].id

    moved = _map(session, course, apply=True, regrade=True)
    assert moved.written == 1
    session.refresh(act)
    assert act.partial_id == [p for p in others if p.name == "Parcial 1"][0].id


def test_the_category_is_suggested_but_NOT_written_by_default(session, course):
    """`category` picks the weight (general .60 / special .20 / exam .20). A silently
    recategorized activity is a silently wrong grade."""
    _add(session, course, "First term exam")
    rep = _map(session, course, apply=True)
    assert rep.matches[0].suggested_category == "exam"
    act = session.exec(select(Activity).where(Activity.course_id == course.id)).first()
    assert act.category == "general", "the weight must not move on its own"


def test_set_category_writes_it_when_asked(session, course):
    _add(session, course, "First term exam")
    _map(session, course, apply=True, set_category=True)
    act = session.exec(select(Activity).where(Activity.course_id == course.id)).first()
    assert act.category == "exam"


# ── the half of the job that mapping does NOT do, and says so ───────────────────────────

def test_it_warns_that_an_all_general_course_caps_every_student_at_6(session, course):
    """Measured, not reasoned: `compute_partial` is .60/.20/.20 with no renormalization, so
    a perfect all-general student returns 60.0. Mapping the parcial does not fix that, and
    a mapping report that stayed quiet about it would read as 'done'."""
    _add(session, course, "Alphabet", "Some & Any")
    rep = _map(session, course, apply=True)
    assert any("6.0" in n for n in rep.notes)


def test_the_warning_clears_once_an_exam_is_categorized(session, course):
    _add(session, course, "Alphabet", "First term exam")
    rep = _map(session, course, apply=True, set_category=True)
    assert not any("6.0" in n for n in rep.notes)


def test_it_names_the_special_bucket_candidates_without_assigning_them(session, course):
    _add(session, course, "TypeRacer Practice Challenge")
    rep = _map(session, course, apply=True)
    special = [n for n in rep.notes if "special" in n]
    assert special and "TypeRacer Practice Challenge" in special[0]
    act = session.exec(select(Activity).where(Activity.course_id == course.id)).first()
    assert act.category == "general"


def test_a_course_with_no_partials_reports_rather_than_crashes(session, course):
    for p in session.exec(select(Partial).where(Partial.course_id == course.id)).all():
        session.delete(p)
    session.commit()
    _add(session, course, "Alphabet")
    rep = _map(session, course, apply=True)
    assert rep.written == 0 and rep.notes
