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
"""

from sqlmodel import Session, select

from musai.db import ro_engine
from musai.models import Course, Partial, PartialGrade, Semester, Student, Enrollment
from musai.semesters import (
    active_semester,
    course_for,
    course_for_student,
    courses_in,
    resolve_semester,
)

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


def list_semesters() -> list[dict]:
    """List every semester on record, newest first, flagging which one is current. Use when
    the professor asks about a previous semester or wants to compare across semesters."""
    with Session(ro_engine) as s:
        current = active_semester(s)
        rows = s.exec(select(Semester)).all()
        rows = sorted(rows, key=lambda x: x.starts_on, reverse=True)
        return [{"semester": x.name, "starts_on": str(x.starts_on), "ends_on": str(x.ends_on),
                 "is_current": bool(current and x.id == current.id)} for x in rows]


def list_groups(semester: str = "") -> list[dict]:
    """List the course groups for one semester, with subject and level. Call this first to
    discover valid group_code values (e.g. '1-LED-A'). Defaults to the CURRENT semester;
    pass `semester` (e.g. '2026-1') for a previous one."""
    with Session(ro_engine) as s:
        sem = resolve_semester(s, semester or None)
        if semester and not sem:
            return [{"error": f"No semester '{semester}'. Use list_semesters."}]
        courses = courses_in(s, semester_id=sem.id if sem else None)
        return [{"group_code": c.group_code, "subject": c.subject, "level": c.level,
                 "semester": sem.name if sem else None} for c in courses]


def group_status(group_code: str, partial: str = "", semester: str = "") -> dict:
    """Grade status for a group. Returns per-partial stats (students graded, passing count,
    pass rate %, mean, min, max, grade distribution) using each student's FINAL grade.
    If `partial` is given (e.g. 'Parcial 1', 'Parcial 2', 'Examen Final Ordinario') only that
    one is returned; otherwise all partials. Defaults to the CURRENT semester; pass `semester`
    (e.g. '2026-1') for a previous one. Use for 'how is group X doing?'."""
    with Session(ro_engine) as s:
        course = course_for(s, group_code, semester_name=semester or None)
        if not course:
            return {"error": f"No group '{group_code}' in "
                             f"{semester or 'the current semester'}. Use list_groups."}
        n_enrolled = len(s.exec(select(Enrollment).where(Enrollment.course_id == course.id)).all())
        partials = s.exec(select(Partial).where(Partial.course_id == course.id).order_by(Partial.id)).all()
        out = {"group_code": group_code, "subject": course.subject, "enrolled": n_enrolled, "partials": []}
        for p in partials:
            if partial and partial.lower() not in p.name.lower():
                continue
            finals = [pg.sega_value for pg in
                      s.exec(select(PartialGrade).where(PartialGrade.partial_id == p.id)).all()]
            out["partials"].append({"partial": p.name, **_stats(finals)})
        return out


def partial_trend(group_code: str, semester: str = "") -> dict:
    """Show how a group trends ACROSS partials over time: the mean final and pass-rate for
    each partial in order, plus the change vs the previous partial. Defaults to the CURRENT
    semester; pass `semester` for a previous one. Use for 'is X improving or declining?',
    'P1 vs P2 trend'."""
    with Session(ro_engine) as s:
        course = course_for(s, group_code, semester_name=semester or None)
        if not course:
            return {"error": f"No group '{group_code}' in {semester or 'the current semester'}."}
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
        return {"group_code": group_code, "trend": series}


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
    q = name_or_matricula.strip().lower()
    with Session(ro_engine) as s:
        students = s.exec(select(Student)).all()
        match = next((st for st in students if st.matricula == name_or_matricula.strip()), None)
        if not match:
            cands = [st for st in students if q in st.full_name.lower()]
            if len(cands) == 1:
                match = cands[0]
            elif len(cands) > 1:
                return {"ambiguous": [{"matricula": c.matricula, "name": c.full_name} for c in cands[:8]]}
        if not match:
            return {"error": f"No student matching '{name_or_matricula}'."}
        sem = resolve_semester(s, semester or None)
        if semester and not sem:
            return {"error": f"No semester '{semester}'. Use list_semesters."}
        course = course_for_student(s, match.id, semester_id=sem.id if sem else None)
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


def at_risk(group_code: str, partial: str = "", threshold: float = 7.0,
            semester: str = "") -> dict:
    """List students at risk (final grade below `threshold`, default 7.0) in a group. If
    `partial` is given, checks that partial; otherwise checks the most advanced partial that
    has grades. Returns each at-risk student's matrícula, name and grade. Defaults to the
    CURRENT semester. Use for 'who is failing / at risk in group X?'."""
    with Session(ro_engine) as s:
        course = course_for(s, group_code, semester_name=semester or None)
        if not course:
            return {"error": f"No group '{group_code}' in {semester or 'the current semester'}."}
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
        return {"group_code": group_code, "partial": target.name, "threshold": threshold,
                "at_risk_count": len(risk), "students": risk}


# The tool set handed to Gemini (all read-only).
TOOLS = [list_semesters, list_groups, group_status, partial_trend, student_status, at_risk]
