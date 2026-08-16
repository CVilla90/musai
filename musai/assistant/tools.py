"""Read-only analytics tools exposed to the in-app AI assistant (Gemini function-calling).

RAIL: every tool here reads through ``ro_engine`` and only ever SELECTs. There is no
write path — the assistant is structurally incapable of changing grades. Each function has
a clear docstring + simple-typed args so the SDK can build the schema and auto-call it.

Grades are 0–10; passing is 7.0. "final" = the curved/overridden + extra value that uploads;
"exact" = the untouched machine grade. Course total weights partials 30/30/40.

SEMESTER SCOPING: every tool defaults to the ACTIVE semester. The owner is the coordinator, so
he may reach historic data — pass `semester` (e.g. '2026-1') to any group tool. Without that
default, a group code that repeats across semesters (they all do) resolved to whichever row
was inserted first, i.e. the oldest.

🔴 **OWNER SCOPING — added 2026-08-16, and the reason this module is a factory.** Until then
every tool here read the whole database: `list_groups` went through `courses_in`, which returns
every course in the semester regardless of who owns it, and `student_status` opened with
`select(Student)` across the entire table. A colleague asking *"how is 1-LED-A doing?"* got the
owner's group; asking *"which group is <name> in?"* searched all 186 of his students.

It survived the 2026-08-14 sweep that scoped 22 route handlers because **that sweep enumerated
`app.routes`, and a Gemini tool is not a route.** `/assistant/ask` takes no `course_id`, so
`test_route_scoping.py` had nothing to walk. Exactly the shape of *scope the system, not the
file*: the audit fixed every leak of the kind it was looking at.

So there is no module-level `TOOLS` any more. `tools_for(professor_id)` is the only way to get
a tool set, and it takes the owner as its first and required argument — you cannot call it
without answering "whose data?". **`professor_id=None` means NOBODY, never everybody**: the
tools still work, and they find nothing.
"""

from __future__ import annotations

from typing import Callable, Optional

from sqlmodel import Session, select

from musai.db import ro_engine
from musai.models import Course, Partial, PartialGrade, Semester, Student, Enrollment
from musai.professors import courses_owned_by
from musai.semesters import active_semester, resolve_semester

COURSE_WEIGHTS = [0.30, 0.30, 0.40]  # P1 · P2 · Examen Final Ordinario
PASS_MARK = 7.0


def _stats(finals: list[float]) -> dict:
    if not finals:
        return {"graded": 0}
    finals = sorted(finals)
    n = len(finals)
    passing = sum(1 for v in finals if v >= PASS_MARK)
    dist = {"<6": 0, "6-7": 0, "7-8": 0, "8-9": 0, "9-10": 0}
    for v in finals:
        if v < 6:      dist["<6"] += 1
        elif v < 7:    dist["6-7"] += 1
        elif v < 8:    dist["7-8"] += 1
        elif v < 9:    dist["8-9"] += 1
        else:          dist["9-10"] += 1
    return {
        "graded": n,
        "passing": passing,
        "pass_rate_pct": round(100 * passing / n, 1),
        "mean": round(sum(finals) / n, 2),
        "min": finals[0],
        "max": finals[-1],
        "distribution": dist,
    }


# ── the owner filter ──────────────────────────────────────────────────────────
#
# Three helpers, and everything below reaches the database through one of them. That is the
# whole design: a tool that wants a course cannot get one without naming a professor, so the
# unscoped version is not something you can write by forgetting a keyword argument.

def _my_courses(s: Session, professor_id: Optional[int], *,
                semester_id: Optional[int] = None) -> list[Course]:
    """This professor's courses. `None` owns nothing — an unclaimed course is nobody's."""
    if professor_id is None:
        return []
    return courses_owned_by(s, professor_id, semester_id=semester_id)


def _my_course(s: Session, professor_id: Optional[int], group_code: str,
               semester: str) -> Optional[Course]:
    """One of THIS professor's courses by group code, in one semester.

    Replaces `semesters.course_for`, which matches a group code against every course in the
    database. Group codes are not unique across professors (`1-LED-A` is a code the whole
    faculty uses), so the unscoped lookup did not just leak — with two professors teaching the
    same code it answered with whichever row was inserted first.
    """
    sem = resolve_semester(s, semester or None)
    if semester and not sem:
        return None
    code = (group_code or "").strip().lower()
    for c in _my_courses(s, professor_id, semester_id=sem.id if sem else None):
        if c.group_code.lower() == code:
            return c
    return None


def _my_students(s: Session, professor_id: Optional[int]) -> list[Student]:
    """Every student enrolled in one of this professor's courses, in ANY semester.

    Deliberately not semester-scoped: `student_status` accepts a `semester` argument so the
    professor can ask how someone did previously, and narrowing the *search* to the active term
    would turn "look up last year's student" into "no such student".
    """
    courses = _my_courses(s, professor_id)
    if not courses:
        return []
    ids = {c.id for c in courses}
    rows = s.exec(select(Enrollment).where(Enrollment.course_id.in_(ids))).all()
    student_ids = {e.student_id for e in rows}
    if not student_ids:
        return []
    return list(s.exec(select(Student).where(Student.id.in_(student_ids))).all())


# ── the tool bodies ───────────────────────────────────────────────────────────
#
# Flat, module-level, professor-first. `tools_for()` wraps each one in a closure that carries
# the public docstring — the docstring is the model's contract and belongs on the callable the
# SDK actually introspects, while the SQL belongs somewhere a test can call it directly.

def _list_semesters(professor_id: Optional[int]) -> list[dict]:
    with Session(ro_engine) as s:
        current = active_semester(s)
        mine = {c.semester_id for c in _my_courses(s, professor_id)}
        if current:
            mine.add(current.id)          # the term in progress, even before it has courses
        rows = [x for x in s.exec(select(Semester)).all() if x.id in mine]
        rows = sorted(rows, key=lambda x: x.starts_on, reverse=True)
        return [{"semester": x.name, "starts_on": str(x.starts_on), "ends_on": str(x.ends_on),
                 "is_current": bool(current and x.id == current.id)} for x in rows]


def _list_groups(professor_id: Optional[int], semester: str = "") -> list[dict]:
    with Session(ro_engine) as s:
        sem = resolve_semester(s, semester or None)
        if semester and not sem:
            return [{"error": f"No semester '{semester}'. Use list_semesters."}]
        courses = _my_courses(s, professor_id, semester_id=sem.id if sem else None)
        return [{"group_code": c.group_code, "subject": c.subject, "level": c.level,
                 "semester": sem.name if sem else None} for c in courses]


def _group_status(professor_id: Optional[int], group_code: str, partial: str = "",
                  semester: str = "") -> dict:
    with Session(ro_engine) as s:
        course = _my_course(s, professor_id, group_code, semester)
        if not course:
            return {"error": f"You have no group '{group_code}' in "
                             f"{semester or 'the current semester'}. Use list_groups."}
        n_enrolled = len(s.exec(select(Enrollment).where(Enrollment.course_id == course.id)).all())
        partials = s.exec(select(Partial).where(Partial.course_id == course.id).order_by(Partial.id)).all()
        out = {"group_code": course.group_code, "subject": course.subject,
               "enrolled": n_enrolled, "partials": []}
        for p in partials:
            if partial and partial.lower() not in p.name.lower():
                continue
            finals = [pg.sega_value for pg in
                      s.exec(select(PartialGrade).where(PartialGrade.partial_id == p.id)).all()]
            out["partials"].append({"partial": p.name, **_stats(finals)})
        return out


def _partial_trend(professor_id: Optional[int], group_code: str, semester: str = "") -> dict:
    with Session(ro_engine) as s:
        course = _my_course(s, professor_id, group_code, semester)
        if not course:
            return {"error": f"You have no group '{group_code}' in "
                             f"{semester or 'the current semester'}."}
        partials = s.exec(select(Partial).where(Partial.course_id == course.id).order_by(Partial.id)).all()
        series, prev = [], None
        for p in partials:
            finals = [pg.sega_value for pg in
                      s.exec(select(PartialGrade).where(PartialGrade.partial_id == p.id)).all()]
            st = _stats(finals)
            point = {"partial": p.name, "mean": st.get("mean"), "pass_rate_pct": st.get("pass_rate_pct")}
            if prev and st.get("mean") is not None and prev.get("mean") is not None:
                point["mean_change"] = round(st["mean"] - prev["mean"], 2)
            series.append(point)
            prev = st
        return {"group_code": course.group_code, "trend": series}


def _student_status(professor_id: Optional[int], name_or_matricula: str,
                    semester: str = "") -> dict:
    q = name_or_matricula.strip().lower()
    with Session(ro_engine) as s:
        students = _my_students(s, professor_id)
        match = next((st for st in students if st.matricula == name_or_matricula.strip()), None)
        if not match:
            cands = [st for st in students if q in st.full_name.lower()]
            if len(cands) == 1:
                match = cands[0]
            elif len(cands) > 1:
                return {"ambiguous": [{"matricula": c.matricula, "name": c.full_name} for c in cands[:8]]}
        if not match:
            # 🔴 The wording matters. Scoped to one professor, "no student matching X" is now
            # also the answer for a real student who belongs to a colleague — and the honest
            # version of that is "not in YOUR groups", not "no such person".
            return {"error": f"No student matching '{name_or_matricula}' in your groups."}
        sem = resolve_semester(s, semester or None)
        if semester and not sem:
            return {"error": f"No semester '{semester}'. Use list_semesters."}
        course = _course_for_my_student(s, professor_id, match.id,
                                        semester_id=sem.id if sem else None)
        if not course:
            return {"matricula": match.matricula, "name": match.full_name,
                    "error": f"{match.full_name} is not enrolled in any of your groups in "
                             f"{sem.name if sem else 'the current semester'}."}
        partials = s.exec(select(Partial).where(Partial.course_id == course.id)
                          .order_by(Partial.id)).all()
        rows, finals_in_order = [], []
        for p in partials:
            pg = s.exec(select(PartialGrade).where(
                PartialGrade.student_id == match.id, PartialGrade.partial_id == p.id)).first()
            if pg:
                rows.append({"partial": p.name, "exact": pg.value_0_10, "final": pg.sega_value,
                             "curve": pg.curve_mode, "extra": pg.extra_points or 0.0})
                finals_in_order.append(pg.sega_value)
            else:
                rows.append({"partial": p.name, "exact": None, "final": None})
                finals_in_order.append(None)
        course_total = None
        if finals_in_order and all(v is not None for v in finals_in_order) and len(finals_in_order) == 3:
            course_total = round(sum(COURSE_WEIGHTS[i] * v for i, v in enumerate(finals_in_order)), 1)
        return {"matricula": match.matricula, "name": match.full_name,
                "group": course.group_code if course else None,
                "semester": sem.name if sem else None,
                "partials": rows, "course_total": course_total}


def _course_for_my_student(s: Session, professor_id: Optional[int], student_id: int, *,
                           semester_id: Optional[int]) -> Optional[Course]:
    """Which of MY courses this student sits in. Replaces `semesters.course_for_student`.

    A student can be enrolled with several professors — that is normal, they take four
    subjects. The unscoped helper returns `courses[0]`, i.e. an arbitrary one of them, which is
    both a leak and a wrong answer to "which group is she in?" as the asker meant it.
    """
    mine = {c.id: c for c in _my_courses(s, professor_id, semester_id=semester_id)}
    if not mine:
        return None
    rows = s.exec(select(Enrollment).where(Enrollment.student_id == student_id)).all()
    for e in rows:
        if e.course_id in mine:
            return mine[e.course_id]
    return None


def _at_risk(professor_id: Optional[int], group_code: str, partial: str = "",
             threshold: float = 7.0, semester: str = "") -> dict:
    with Session(ro_engine) as s:
        course = _my_course(s, professor_id, group_code, semester)
        if not course:
            return {"error": f"You have no group '{group_code}' in "
                             f"{semester or 'the current semester'}."}
        partials = s.exec(select(Partial).where(Partial.course_id == course.id).order_by(Partial.id)).all()
        target = None
        for p in partials:
            if partial and partial.lower() in p.name.lower():
                target = p; break
            if not partial and s.exec(select(PartialGrade).where(PartialGrade.partial_id == p.id)).first():
                target = p  # keep advancing to the latest graded partial
        if not target:
            return {"error": "No graded partial found."}
        risk = []
        for pg in s.exec(select(PartialGrade).where(PartialGrade.partial_id == target.id)).all():
            if pg.sega_value < threshold:
                st = s.get(Student, pg.student_id)
                risk.append({"matricula": st.matricula if st else "?",
                             "name": st.full_name if st else "?", "final": pg.sega_value})
        risk.sort(key=lambda r: r["final"])
        return {"group_code": course.group_code, "partial": target.name, "threshold": threshold,
                "at_risk_count": len(risk), "students": risk}


# ── the tool set ──────────────────────────────────────────────────────────────

def tools_for(professor_id: Optional[int]) -> list[Callable]:
    """The read-only tool set, bound to ONE professor. `None` binds to nobody.

    Every callable returned here closes over `professor_id`, so the model has no argument with
    which to ask about somebody else's group — the owner is not in the schema it sees, and
    cannot be. That is the difference between a filter and a rail.

    The docstrings below are the model's contract, not documentation *about* the contract.
    `student_status`'s in particular was measured, not written: see the note at its end.
    """

    def list_semesters() -> list[dict]:
        """List every semester on record, newest first, flagging which one is current. Use when
        the professor asks about a previous semester or wants to compare across semesters."""
        return _list_semesters(professor_id)

    def list_groups(semester: str = "") -> list[dict]:
        """List the course groups for one semester, with subject and level. Call this first to
        discover valid group_code values (e.g. '1-LED-A'). Defaults to the CURRENT semester;
        pass `semester` (e.g. '2026-1') for a previous one."""
        return _list_groups(professor_id, semester)

    def group_status(group_code: str, partial: str = "", semester: str = "") -> dict:
        """Grade status for a group. Returns per-partial stats (students graded, passing count,
        pass rate %, mean, min, max, grade distribution) using each student's FINAL grade.
        If `partial` is given (e.g. 'Parcial 1', 'Parcial 2', 'Examen Final Ordinario') only that
        one is returned; otherwise all partials. Defaults to the CURRENT semester; pass `semester`
        (e.g. '2026-1') for a previous one. Use for 'how is group X doing?'."""
        return _group_status(professor_id, group_code, partial, semester)

    def partial_trend(group_code: str, semester: str = "") -> dict:
        """Show how a group trends ACROSS partials over time: the mean final and pass-rate for
        each partial in order, plus the change vs the previous partial. Defaults to the CURRENT
        semester; pass `semester` for a previous one. Use for 'is X improving or declining?',
        'P1 vs P2 trend'."""
        return _partial_trend(professor_id, group_code, semester)

    def student_status(name_or_matricula: str, semester: str = "") -> dict:
        """Look up ONE student and return WHICH GROUP they are in, plus their grade in every
        partial (exact, curve, extra, final) and their course total (30/30/40).

        Accepts a matrícula (digits) or a full or partial NAME. This searches EVERY student across
        ALL of the professor's groups by itself — do NOT call list_groups first, and never call this
        once per group. A single call answers "which group is <name> in?" outright: the reply
        carries a `group` field. If the name matches nobody the reply is {"error": ...}, and that is
        a COMPLETE answer — report it and stop. If it matches several the reply is
        {"ambiguous": [...]}. Defaults to the CURRENT semester; pass `semester` (e.g. '2026-1') to
        see how the same student did previously.

        Use for 'how is <student> doing?', 'what's <id>'s grade?', 'which group is <name> in?'.

        🔴 The docstring IS the tool contract, and the vague version cost real answers. It said
        only "look up ONE student", never that the search is global or that the reply names the
        group — so on "which group is <name> in?" the model called list_groups, then groped
        group-by-group and called list_semesters to widen, exhausted `max_remote_calls`, and the
        turn ended holding an unexecuted function_call with no text part. The professor saw
        "I pulled the data but didn't form a summary". Measured 2026-08-16, 6 runs per config:
        **3/6 answered with the old wording, 6/6 with this one — on the same cheap model, with 41%
        FEWER tokens and less than half the latency.** Raising the call cap also reached 6/6 but
        took 10.3 s/question against 2.7 s, because it buys groping room instead of removing the
        need to grope. ⭐ Upgrading the model was strictly worse: gemini-3.7-flash scored 2/6 at
        30.5 s/question. A tool whose description understates it is not a model problem."""
        return _student_status(professor_id, name_or_matricula, semester)

    def at_risk(group_code: str, partial: str = "", threshold: float = 7.0,
                semester: str = "") -> dict:
        """List students at risk (final grade below `threshold`, default 7.0) in a group. If
        `partial` is given, checks that partial; otherwise checks the most advanced partial that
        has grades. Returns each at-risk student's matrícula, name and grade. Defaults to the
        CURRENT semester. Use for 'who is failing / at risk in group X?'."""
        return _at_risk(professor_id, group_code, partial, threshold, semester)

    return [list_semesters, list_groups, group_status, partial_trend, student_status, at_risk]
