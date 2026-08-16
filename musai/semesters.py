"""Semester resolution — the dimension every lookup was missing.

Until 2026-08 the DB held exactly ONE semester, so every call site could get away with
``select(Course).where(Course.group_code == "1-LED-A").first()``. The moment a second
semester exists that same query silently returns whichever row was inserted first — the
OLD one. Nothing raises; the app just answers about last semester.

This module is the single place that answers "which semester?", so no call site has to
guess. Rules, in order:

  1. The semester whose [starts_on, ends_on] contains today (in the configured TZ).
  2. Else the one explicitly flagged ``is_active``.
  3. Else the most recent by ``starts_on``.

"Today" is resolved in **Chihuahua** time (``settings.timezone``), never server-local —
the cockpit may run on a UTC host, and a semester boundary must not shift by a day
depending on where the process happens to be.

NOTE: no ``from __future__ import annotations`` here. This module is imported by the
Gemini tool modules, and stringized hints break the SDK's schema builder (the tools
themselves must keep real annotations). Keeping this file consistent avoids the trap.
"""

from datetime import date, datetime
from typing import Optional, Sequence

from sqlmodel import Session, select

from musai.config import settings
from musai.models import Course, Enrollment, Semester

try:  # py3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — very old runtimes
    ZoneInfo = None  # type: ignore[assignment]


def today_local() -> date:
    """Today's date in the configured institutional timezone (America/Chihuahua)."""
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(settings.timezone)).date()
        except Exception:
            pass
    return date.today()


# ── Deriving a semester from the calendar ─────────────────────────────────────
# UACH runs two terms a year and names them after the year plus an ordinal:
#   Ene–Jun 2027  →  "2027-1"        Ago–Dic 2026  →  "2026-2"
#
# 🔴 The bounds below are GAPLESS (Jan 1–Jun 30, Jul 1–Dec 31), not the teaching dates. That is
# the whole point: `active_semester()`'s first rule is "the semester containing today", and a
# professor opening MUSAI on 20 July — after one term's classes end and before the next begins —
# must still land somewhere deterministic rather than on whichever row sorts first. The real
# teaching window (Ago 10 → Dic 18) is a property of a *course*, not of the calendar bucket, and
# a row that already exists is never rewritten to these bounds.
_TERM_BOUNDS = {
    1: ((1, 1), (6, 30)),
    2: ((7, 1), (12, 31)),
}

_TERM_LABEL = {1: "Ene–Jun", 2: "Ago–Dic"}


def semester_window(d: Optional[date] = None) -> tuple[str, date, date]:
    """`date` → `("2026-2", 2026-07-01, 2026-12-31)`. Pure: no DB, no clock, no timezone.

    Split out from `active_semester` so the answer can be tested against a fixed date instead
    of against today, and so the empty-state dashboard can *name* the semester it is about to
    create before creating it.
    """
    d = d or today_local()
    term = 1 if d.month <= 6 else 2
    (sm, sd), (em, ed) = _TERM_BOUNDS[term]
    return f"{d.year}-{term}", date(d.year, sm, sd), date(d.year, em, ed)


def semester_label(name: str) -> str:
    """`"2026-2"` → `"Ago–Dic 2026"`. What a professor calls it; `name` is what SEGA calls it."""
    try:
        year, term = name.split("-")
        return f"{_TERM_LABEL[int(term)]} {year}"
    except (ValueError, KeyError):
        return name


def ensure_current_semester(sess: Session) -> Semester:
    """The semester for today, created if the DB has never seen it. Commits.

    This is what makes a brand-new professor's first sign-in work at all: there is no seed
    step, no `--discover` CLI to run first, and no admin to ask. The calendar says which
    semester it is, and the row appears.

    ⚠️ It does **not** touch `is_active` on other semesters. That flag is only a tiebreaker for
    rule 2 of `active_semester()`, and flipping it here would silently retire a semester that a
    colleague in a different faculty is still teaching.
    """
    name, starts_on, ends_on = semester_window()
    existing = sess.exec(select(Semester).where(Semester.name == name)).first()
    if existing is not None:
        return existing
    sem = Semester(name=name, starts_on=starts_on, ends_on=ends_on, is_active=True)
    sess.add(sem)
    sess.commit()
    sess.refresh(sem)
    return sem


def active_semester(sess: Session) -> Optional[Semester]:
    """The semester MUSAI considers 'now'. See module docstring for the rules."""
    semesters: Sequence[Semester] = sess.exec(select(Semester)).all()
    if not semesters:
        return None

    today = today_local()
    containing = [s for s in semesters if s.starts_on <= today <= s.ends_on]
    if containing:
        # Most recently started wins if ranges ever overlap.
        return max(containing, key=lambda s: s.starts_on)

    flagged = [s for s in semesters if s.is_active]
    if flagged:
        return max(flagged, key=lambda s: s.starts_on)

    return max(semesters, key=lambda s: s.starts_on)


def resolve_semester(sess: Session, name: Optional[str] = None) -> Optional[Semester]:
    """Look a semester up by name (e.g. '2026-2'); fall back to the active one."""
    if name:
        found = sess.exec(select(Semester).where(Semester.name == name)).first()
        if found:
            return found
        return None
    return active_semester(sess)


def active_semester_id(sess: Session) -> Optional[int]:
    sem = active_semester(sess)
    return sem.id if sem else None


def course_for(
    sess: Session,
    group_code: str,
    *,
    semester_id: Optional[int] = None,
    semester_name: Optional[str] = None,
) -> Optional[Course]:
    """Find one course by group code, scoped to a semester.

    Defaults to the active semester. Pass ``semester_name`` to reach a historic one
    (the coordinator may; students may not — see ``courses_for_student``).

    Falls back to an unscoped match ONLY when the DB has no semester rows at all, so
    a fresh/empty install still behaves.
    """
    sid = semester_id
    if sid is None:
        sem = resolve_semester(sess, semester_name)
        if sem is None and semester_name:
            return None  # asked for a semester that doesn't exist — don't silently widen
        sid = sem.id if sem else None

    stmt = select(Course).where(Course.group_code == group_code)
    if sid is not None:
        stmt = stmt.where(Course.semester_id == sid)
    return sess.exec(stmt).first()


def courses_in(
    sess: Session,
    *,
    semester_id: Optional[int] = None,
    semester_name: Optional[str] = None,
) -> list[Course]:
    """Every course in one semester (default: active), ordered by group code."""
    sid = semester_id
    if sid is None:
        sem = resolve_semester(sess, semester_name)
        if sem is None and semester_name:
            return []
        sid = sem.id if sem else None

    stmt = select(Course).order_by(Course.group_code)
    if sid is not None:
        stmt = stmt.where(Course.semester_id == sid)
    return list(sess.exec(stmt).all())


def courses_for_student(
    sess: Session,
    student_id: int,
    *,
    semester_id: Optional[int] = None,
    current_only: bool = True,
) -> list[Course]:
    """Courses this student is enrolled in.

    ``current_only=True`` (the default, and what SUSAI must always use) restricts to the
    active semester: a student asking over WhatsApp must never be shown a previous
    semester's grades (ROADMAP, "Semester scoping"). A returning student has enrollments
    in several semesters, so an unscoped ``.first()`` would hand them the oldest one.
    """
    sid = semester_id
    if sid is None and current_only:
        sid = active_semester_id(sess)

    stmt = (
        select(Course)
        .join(Enrollment, Enrollment.course_id == Course.id)
        .where(Enrollment.student_id == student_id)
    )
    if sid is not None:
        stmt = stmt.where(Course.semester_id == sid)
    return list(sess.exec(stmt.order_by(Course.group_code)).all())


def course_for_student(
    sess: Session,
    student_id: int,
    *,
    semester_id: Optional[int] = None,
    current_only: bool = True,
) -> Optional[Course]:
    """The student's course for the given semester, or None if they have none.

    None is a meaningful answer: a student who was enrolled last semester but not this
    one gets no data, rather than stale data.
    """
    courses = courses_for_student(
        sess, student_id, semester_id=semester_id, current_only=current_only
    )
    return courses[0] if courses else None
