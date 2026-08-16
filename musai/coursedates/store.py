"""Persist the Cronograma: the teaching window, the tab map, and the last course snapshot.

The one rule worth stating: **a professor's correction outranks the guess, permanently.**
`tabmap.guess()` runs against the tab strip every time the course is re-read, so without a
stored override a re-read would quietly undo a manual fix. `merge_tabmap` keeps the saved
decision for any section still present, and only guesses for sections it has never seen.
"""

import json
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple

from sqlmodel import Session, select

from musai.coursedates import periods, tabmap
from musai.coursedates.plan import CoursePlan, build_plan
from musai.models import Course, CourseSchedule, Semester

# The owner's own shape, used when nothing is saved yet: three parcials, whole Mon–Sun weeks,
# fifteen weeks of teaching, exams in the last week of their period.
DEFAULT_TEACHING_WEEKS = 15

DEFAULTS: Dict = {
    "starts": "", "ends": "", "periods": periods.DEFAULT_PERIODS,
    "exam_days": periods.DEFAULT_EXAM_WINDOW_DAYS, "cutoff": False, "tabmap": None,
    # Extensions, kept as a REPLAY LIST rather than baked into starts/ends.
    #
    # An extension makes the calendar non-uniform ("parcial 2 lasts six weeks, the others
    # five"), and `split_periods` cannot express that from a window and a count — so a
    # shift folded into the window would be silently lost the next time anything recomputed.
    # Storing the operations keeps them visible, individually removable, and replayable, and
    # it keeps the professor's original window intact underneath as the thing to return to.
    "shifts": [],
}


def suggest_window(semester: Optional[Semester]) -> Tuple[str, str]:
    """A teaching window biased to the owner: the semester's start, fifteen whole weeks.

    Deliberately NOT `Semester.ends_on` — that is the university's administrative close
    (2026-12-18 for 2026-2) and using it would push every deadline a month past the last
    class. The suggestion is a starting point the professor edits, and the page says so.

    ⚠️ Snapping to Monday goes **forward**, never back: a semester that opens mid-week must
    not produce a calendar whose first period begins before the university's own start date.
    """
    start = semester.starts_on if semester is not None else date.today()
    if start.weekday():
        start += timedelta(days=7 - start.weekday())
    return start.isoformat(), (start + timedelta(days=DEFAULT_TEACHING_WEEKS * 7 - 1)).isoformat()


def _row(sess: Session, course_id: int) -> CourseSchedule:
    row = sess.exec(select(CourseSchedule)
                    .where(CourseSchedule.course_id == course_id)).first()
    if row is None:
        row = CourseSchedule(course_id=course_id)
        sess.add(row)
        sess.commit()
        sess.refresh(row)
    return row


def load(sess: Session, course: Course, semester: Optional[Semester] = None) -> Dict:
    row = _row(sess, course.id)
    data = {**DEFAULTS, **json.loads(row.data_json or "{}")}
    if not data["starts"] or not data["ends"]:
        # 🔴 Resolve the semester HERE rather than falling back to "today" when the caller
        # did not pass one. The page passes it and the background job does not, so a
        # today-based default would let the job apply a different calendar from the one the
        # professor read and approved — the worst possible disagreement in this feature.
        if semester is None:
            from musai.semesters import active_semester
            semester = active_semester(sess)
        data["starts"], data["ends"] = suggest_window(semester)
        data["suggested"] = True
    data["read_at"] = row.read_at
    return data


def save_settings(sess: Session, course_id: int, **fields) -> Dict:
    row = _row(sess, course_id)
    data = {**DEFAULTS, **json.loads(row.data_json or "{}")}
    data.update({k: v for k, v in fields.items() if k in DEFAULTS})
    row.data_json = json.dumps(data, ensure_ascii=False)
    row.updated_at = datetime.utcnow()
    sess.add(row)
    sess.commit()
    return data


def save_snapshot(sess: Session, course_id: int, snapshot: Dict) -> None:
    row = _row(sess, course_id)
    row.snapshot_json = json.dumps(snapshot, ensure_ascii=False)
    row.read_at = datetime.utcnow()
    sess.add(row)
    sess.commit()


def snapshot(sess: Session, course_id: int) -> Optional[Dict]:
    row = _row(sess, course_id)
    snap = json.loads(row.snapshot_json or "{}")
    return snap if snap.get("sections") else None


def merge_tabmap(saved: Optional[Dict], guessed: tabmap.TabMap) -> tabmap.TabMap:
    """Guess, then let anything the professor already decided win.

    Matched by **section number**, not by label: a tab that gets renamed is still the same
    tab, and its activities have not moved.
    """
    if not saved:
        return guessed
    overrides = {r["section"]: r for r in saved.get("rules", [])}
    for rule in guessed.rules:
        o = overrides.get(rule.section)
        if not o or not o.get("manual"):
            continue
        rule.kind = o.get("kind", rule.kind)
        rule.period = o.get("period", rule.period)
        rule.slot = o.get("slot", rule.slot)
        rule.reason = "Ajustado por el profesor."
    return guessed


def apply_shifts(cal: periods.Calendar, shifts) -> periods.Calendar:
    """Replay stored extensions over a freshly computed calendar, oldest first.

    A shift that no longer fits — because the window or the number of parcials changed under
    it — is dropped with a note rather than raised. The professor is looking at a page, and
    one stale extension must not make the whole Cronograma unrenderable.
    """
    for s in shifts or []:
        try:
            cal = periods.shift(cal, s.get("period"), int(s.get("days", 0)))
        except (periods.PeriodError, ValueError, KeyError) as exc:
            cal.notes.append(
                f"⚠️ No se pudo aplicar el recorrido de {s.get('days')} día(s) "
                f"a {('el parcial ' + str(s['period'])) if s.get('period') else 'todo'}: {exc}")
    return cal


def compose(sess: Session, course: Course, semester: Optional[Semester] = None):
    """Everything the page needs: (settings, calendar, tab map, plan). Plan is None with
    no snapshot yet — the course has to be read once before anything can be planned."""
    data = load(sess, course, semester)
    cal = periods.split_periods(date.fromisoformat(data["starts"]),
                                date.fromisoformat(data["ends"]),
                                count=int(data["periods"]),
                                exam_window_days=int(data["exam_days"]))
    cal = apply_shifts(cal, data.get("shifts"))
    snap = snapshot(sess, course.id)
    if not snap:
        return data, cal, None, None

    tabs = [{"section": s["section"], "label": s["name"], "hidden": s.get("hidden")}
            for s in snap["sections"]]
    tmap = merge_tabmap(data.get("tabmap"), tabmap.guess(tabs, periods=int(data["periods"])))
    plan: CoursePlan = build_plan(
        course.moodle_course_id or "", snap["sections"], tmap, cal,
        include_optional=("cutoffdate",) if data.get("cutoff") else ())
    return data, cal, tmap, plan
