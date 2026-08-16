"""Read-only, student-SCOPED tools for SUSAI (Gemini function-calling).

RAIL: every tool reads through ``ro_engine`` and only SELECTs, AND is locked to the ONE
student bound for this request via a ContextVar the agent sets. There is no `student`
argument the model could pass — it can only ever see the bound student's own data. No
write path exists.

RAIL (semester): students see the CURRENT semester ONLY, always — there is deliberately no
`semester` argument here, unlike the professor-facing analyst tools. A returning student has
enrollments in several semesters; an unscoped lookup would hand them the OLDEST one. If they
have no current enrollment they get nothing, never last semester's grades (ROADMAP,
"Semester scoping").

NOTE: do NOT add `from __future__ import annotations` here — it stringizes type hints and
breaks the google-genai schema builder (learned the hard way on the in-app analyst).
"""

import json
from contextvars import ContextVar

from sqlmodel import Session, select

from musai.db import ro_engine
from musai.models import Course, Enrollment, Partial, PartialGrade, Student
from musai.semesters import course_for_student

# Set by the agent before each Gemini turn; read by the tools below.
CURRENT_STUDENT_ID: ContextVar[int] = ContextVar("susai_student_id", default=0)
COURSE_WEIGHTS = [0.30, 0.30, 0.40]  # Parcial 1 · Parcial 2 · Examen Final Ordinario
PASS_MARK = 7.0


def _bound() -> int:
    return CURRENT_STUDENT_ID.get()


def my_grades() -> dict:
    """Get the current student's grade in every partial (both the exact machine grade and the
    final grade 0-10) plus their overall course total (partials weighted 30/30/40). Use for
    'what's my grade?', '¿cómo voy?', 'mis calificaciones'."""
    sid = _bound()
    if not sid:
        return {"error": "no student in context"}
    with Session(ro_engine) as s:
        st = s.get(Student, sid)
        course = course_for_student(s, sid)  # current semester only — rail
        if course is None:
            return {"name": st.full_name if st else None, "enrolled": False,
                    "note": "not enrolled in any course this semester"}
        partials = s.exec(select(Partial).where(Partial.course_id == course.id)
                          .order_by(Partial.id)).all()
        rows, finals = [], []
        for p in partials:
            pg = s.exec(select(PartialGrade).where(
                PartialGrade.student_id == sid, PartialGrade.partial_id == p.id)).first()
            if pg:
                rows.append({"partial": p.name, "exact": pg.value_0_10, "final": pg.sega_value})
                finals.append(pg.sega_value)
            else:
                rows.append({"partial": p.name, "exact": None, "final": None})
                finals.append(None)
        total = None
        if len(finals) == 3 and all(v is not None for v in finals):
            total = round(sum(COURSE_WEIGHTS[i] * v for i, v in enumerate(finals)), 1)
        return {"name": st.full_name if st else None, "enrolled": True,
                "group": course.group_code,
                "pass_mark": PASS_MARK, "partials": rows, "course_total": total}


def my_partial_detail(partial: str) -> dict:
    """Explain ONE of the current student's partials in detail: the component breakdown
    (general / special / exam) behind it, plus exact grade, any curve and extra credit. Use
    for 'why is my Parcial 1 low?', '¿por qué saqué eso?', 'how was it calculated?'. `partial`
    is one of 'Parcial 1', 'Parcial 2', 'Examen Final Ordinario'."""
    sid = _bound()
    if not sid:
        return {"error": "no student in context"}
    with Session(ro_engine) as s:
        course = course_for_student(s, sid)  # current semester only — rail
        if not course:
            return {"enrolled": False, "note": "not enrolled in any course this semester"}
        partials = s.exec(select(Partial).where(Partial.course_id == course.id)).all()
        p = next((x for x in partials if partial.lower() in x.name.lower()), None)
        if not p:
            return {"error": f"no partial like '{partial}'"}
        pg = s.exec(select(PartialGrade).where(
            PartialGrade.student_id == sid, PartialGrade.partial_id == p.id)).first()
        if not pg:
            return {"partial": p.name, "graded": False}
        try:
            components = json.loads(pg.components_json or "{}")
        except Exception:
            components = {}
        return {"partial": p.name, "graded": True, "exact": pg.value_0_10,
                "final": pg.sega_value, "curve_mode": pg.curve_mode,
                "extra_points": pg.extra_points or 0.0, "components": components,
                "weights": {"general": p.weight_general, "special": p.weight_special,
                            "exam": p.weight_exam}}


# Student-scoped, read-only tool set handed to Gemini.
TOOLS = [my_grades, my_partial_detail]
