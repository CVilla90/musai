"""Tests for the Cronograma's persistence and the page/job agreement.

The bug worth pinning here is not a crash. `compose()` is called by the page **with** the
active semester and by the background job **without** it, and the first version fell back to
"today" in the second case — so the job could have applied a different calendar from the one
the professor read and approved. That disagreement is invisible until a deadline is wrong.
"""

from datetime import date, timedelta

import pytest
from sqlmodel import Session

from musai.coursedates import store, tabmap
from musai.models import Course, Semester


@pytest.fixture
def course(session: Session) -> Course:
    sem = Semester(name="2026-2", starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18),
                   is_active=True)
    session.add(sem)
    session.commit()
    session.refresh(sem)
    c = Course(semester_id=sem.id, subject="Inglés I", level=1, group_code="1-LED-A",
               moodle_course_id="9023")
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


SNAPSHOT = {"idc": "9023", "sections": [
    {"section": 0, "name": "Introduction", "activities": []},
    {"section": 1, "name": "First Term", "activities": [
        {"cmid": "1", "modname": "quiz", "name": "Alphabet"},
        {"cmid": "2", "modname": "label", "name": ""}]},
    {"section": 2, "name": "Watch and Write", "activities": [
        {"cmid": "3", "modname": "forum", "name": "Watch and Write"}]},
    {"section": 3, "name": "Exam 1", "activities": [
        {"cmid": "4", "modname": "quiz", "name": "First term exam"}]},
    {"section": 4, "name": "Second Term", "activities": []},
    {"section": 5, "name": "Exam 2", "activities": []},
    {"section": 6, "name": "Third Term", "activities": []},
    {"section": 7, "name": "Other resources", "hidden": True, "activities": [
        {"cmid": "9", "modname": "quiz", "name": "deprecated"}]},
]}


# ------------------------------------------------------------------ the suggested window ---

def test_the_default_window_is_carloss_own_shape():
    """15 whole weeks from the semester's Monday start — which is exactly 10 ago → 22 nov."""
    sem = Semester(name="2026-2", starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18))
    assert store.suggest_window(sem) == ("2026-08-10", "2026-11-22")


def test_the_default_end_is_NOT_the_semesters_administrative_close():
    sem = Semester(name="2026-2", starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18))
    _, ends = store.suggest_window(sem)
    assert ends != "2026-12-18", "that date is a month past the last class"


def test_a_midweek_semester_start_snaps_FORWARD_to_monday():
    """Snapping back would open the course before the university's own start date."""
    sem = Semester(name="x", starts_on=date(2026, 8, 12), ends_on=date(2026, 12, 18))
    starts, _ = store.suggest_window(sem)
    assert starts == "2026-08-17"
    assert date.fromisoformat(starts) > sem.starts_on


def test_a_monday_start_is_left_alone():
    sem = Semester(name="x", starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18))
    assert store.suggest_window(sem)[0] == "2026-08-10"


# --------------------------------------------------------- the page/job agreement ---------

def test_the_page_and_the_background_job_compute_the_same_calendar(session, course):
    """The page passes the semester; the job does not. They must still agree."""
    store.save_snapshot(session, course.id, SNAPSHOT)
    semester = session.get(Semester, course.semester_id)

    page_data, page_cal, _, _ = store.compose(session, course, semester)
    job_data, job_cal, _, _ = store.compose(session, course)          # the job's call shape

    assert (page_data["starts"], page_data["ends"]) == (job_data["starts"], job_data["ends"])
    assert [(p.starts_on, p.ends_on) for p in page_cal.periods] == \
           [(p.starts_on, p.ends_on) for p in job_cal.periods]


def test_saved_settings_win_over_the_suggestion(session, course):
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, starts="2026-09-07", ends="2026-12-13")
    data, cal, _, _ = store.compose(session, course)
    assert data["starts"] == "2026-09-07"
    assert not data.get("suggested")
    assert cal.periods[0].starts_on == date(2026, 9, 7)


def test_no_snapshot_means_no_plan_rather_than_an_empty_one(session, course):
    """An empty plan would read as 'nothing to do'. There is a difference between nothing to
    do and not having looked."""
    data, cal, tmap, plan = store.compose(session, course)
    assert cal is not None and plan is None and tmap is None


# ------------------------------------------------------------------- the manual override ---

def test_a_professors_correction_survives_a_re_read(session, course):
    """The map is re-guessed from the tab strip every time; without this the correction is
    silently undone the next time anyone presses 'Leer el curso'."""
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, tabmap={"rules": [
        {"section": 2, "kind": tabmap.KIND_PERIOD, "period": 2, "slot": tabmap.SLOT_CONTENT,
         "manual": True}]})

    store.save_snapshot(session, course.id, SNAPSHOT)          # re-read the course
    _, _, tmap, _ = store.compose(session, course)
    rule = tmap.rule_for(2)
    assert (rule.kind, rule.period) == (tabmap.KIND_PERIOD, 2)
    assert "profesor" in rule.reason


def test_a_correction_moves_that_tabs_activities_too(session, course):
    store.save_snapshot(session, course.id, SNAPSHOT)
    _, _, _, before = store.compose(session, course)
    forum = [a for a in before.activities if a.section == 2][0]
    assert forum.section == 2                       # a forum: no dates either way

    store.save_settings(session, course.id, tabmap={"rules": [
        {"section": 1, "kind": tabmap.KIND_PERIOD, "period": 3, "slot": tabmap.SLOT_CONTENT,
         "manual": True}]})
    _, cal, _, after = store.compose(session, course)
    quiz = [a for a in after.activities if a.cmid == "1"][0]
    assert quiz.period == 3
    assert quiz.changes[0].when == cal.periods[2].content_window()[0]


def test_an_override_for_a_section_that_no_longer_exists_is_ignored(session, course):
    """Courses get restored and re-cut; a stale override must not resurrect a dead tab."""
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, tabmap={"rules": [
        {"section": 99, "kind": tabmap.KIND_SKIP, "period": None, "slot": "content",
         "manual": True}]})
    _, _, tmap, _ = store.compose(session, course)
    assert tmap.rule_for(99) is None
    assert len(tmap.rules) == len(SNAPSHOT["sections"])


def test_a_guess_is_not_treated_as_a_manual_decision(session, course):
    """Only entries flagged `manual` override; a round-tripped guess must stay re-guessable."""
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, tabmap={"rules": [
        {"section": 1, "kind": tabmap.KIND_PERIOD, "period": 3, "slot": "content"}]})
    _, _, tmap, _ = store.compose(session, course)
    assert tmap.rule_for(1).period == 1, "an unflagged rule is not an override"


def test_the_hidden_bank_stays_skipped_and_out_of_the_writable_set(session, course):
    store.save_snapshot(session, course.id, SNAPSHOT)
    _, _, _, plan = store.compose(session, course)
    assert not any(a.section == 7 for a in plan.writable)


# ------------------------------------------------------------------ extensions (shift) ---

def test_a_shift_is_stored_as_an_operation_not_baked_into_the_window(session, course):
    """An extension makes the calendar non-uniform, and `split_periods` cannot express that
    from (start, end, count) — so folding it into the window would silently lose it on the
    next recompute."""
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, starts="2026-08-10", ends="2026-11-22",
                        shifts=[{"period": 2, "days": 7}])
    data, cal, _, _ = store.compose(session, course)

    assert data["ends"] == "2026-11-22", "the professor's own window is left intact"
    assert cal.periods[0].ends_on == date(2026, 9, 13), "parcial 1 does not move"
    assert cal.periods[1].ends_on == date(2026, 10, 25), "parcial 2 gains its week"
    assert cal.periods[2].starts_on == date(2026, 10, 26), "parcial 3 slides to stay contiguous"


def test_shifts_replay_in_order(session, course):
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, starts="2026-08-10", ends="2026-11-22",
                        shifts=[{"period": 1, "days": 7}, {"period": 1, "days": 7}])
    _, cal, _, _ = store.compose(session, course)
    assert cal.periods[0].ends_on == date(2026, 9, 27), "two weeks, cumulative"


def test_a_whole_calendar_shift_moves_the_start_too(session, course):
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, starts="2026-08-10", ends="2026-11-22",
                        shifts=[{"period": None, "days": 7}])
    _, cal, _, _ = store.compose(session, course)
    assert cal.periods[0].starts_on == date(2026, 8, 17)


def test_a_shift_that_no_longer_fits_is_dropped_with_a_note_not_raised(session, course):
    """The professor is looking at a page. One stale extension must not make the whole
    Cronograma unrenderable."""
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, starts="2026-08-10", ends="2026-11-22",
                        periods=3, shifts=[{"period": 9, "days": 7}])
    _, cal, _, _ = store.compose(session, course)
    assert cal is not None
    assert any("No se pudo aplicar" in n for n in cal.notes)


def test_a_shift_that_would_invert_a_period_is_refused_not_applied(session, course):
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, starts="2026-08-10", ends="2026-11-22",
                        shifts=[{"period": 1, "days": -999}])
    _, cal, _, _ = store.compose(session, course)
    assert cal.periods[0].ends_on == date(2026, 9, 13), "unchanged"
    assert any("No se pudo aplicar" in n for n in cal.notes)


def test_the_plan_is_cut_from_the_SHIFTED_calendar(session, course):
    """The whole point: an extension has to reach the dates that get written."""
    store.save_snapshot(session, course.id, SNAPSHOT)
    store.save_settings(session, course.id, starts="2026-08-10", ends="2026-11-22")
    _, _, _, before = store.compose(session, course)
    exam = [a for a in before.activities if a.cmid == "4"][0]
    was = exam.changes[0].when

    store.save_settings(session, course.id, shifts=[{"period": 1, "days": 7}])
    _, _, _, after = store.compose(session, course)
    exam2 = [a for a in after.activities if a.cmid == "4"][0]
    assert exam2.changes[0].when > was
