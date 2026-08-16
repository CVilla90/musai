"""Partial-grade routes — compute, curve/override, review, and SEGA dry-run."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from musai.config import settings
from musai.db import engine
from musai.models import (
    Course, Partial, Activity, Grade, Student, Enrollment, PartialGrade
)
from musai.grading.engine import ActivityResult, compute_partial, pct_to_10, components_to_json
from musai.grading.curve import square_root, STANDARD, PROTOCOL_LABELS
from musai.audit import log as audit_log
from musai.web.app import templates
from musai.web.deps import my_course

router = APIRouter(prefix="/courses")


def _owned_partial(sess: Session, course: Course, partial_id: int) -> Partial:
    """The partial, only if it belongs to THIS course.

    🔴 Scoping the course is not enough on a two-id route. `/courses/<mine>/partial/<theirs>/
    curve/clear` deleted every curve on `PartialGrade.partial_id == theirs` — the course check
    passed, and the partial id was never compared to it. Both ids have to be checked, or the
    one that is checked is only a decoration on the one that is not.
    """
    partial = sess.get(Partial, partial_id)
    if partial is None or partial.course_id != course.id:
        raise HTTPException(404, "No such partial.")
    return partial


def _auto_final(computed: float) -> float:
    """The standard (square-root) curved value for one computed grade."""
    return square_root([computed])[0]


def _final_for(mode: str, computed: float, stored_final: float | None) -> float:
    """Resolve the SEGA-bound grade from the curve mode (auto tracks the live computed)."""
    if mode == "auto":
        return _auto_final(computed)
    if mode == "manual" and stored_final is not None:
        return stored_final
    return computed


def _compute_for_course(sess: Session, course: Course, partial: Partial) -> list[dict]:
    """Per-student rows: exact computed grade + the curved/overridden final."""
    enrollments = sess.exec(
        select(Enrollment).where(Enrollment.course_id == course.id)
    ).all()
    activities = sess.exec(
        select(Activity).where(Activity.course_id == course.id, Activity.partial_id == partial.id)
    ).all()
    activity_ids = [a.id for a in activities]

    rows = []
    for enr in enrollments:
        student = sess.get(Student, enr.student_id)
        if not student:
            continue
        grades = sess.exec(
            select(Grade).where(
                Grade.student_id == student.id,
                Grade.activity_id.in_(activity_ids),
            )
        ).all() if activity_ids else []
        grade_by_activity = {g.activity_id: g for g in grades}

        results = [
            ActivityResult(
                activity_id=act.id, category=act.category, name=act.name,
                value_pct=(grade_by_activity[act.id].value if act.id in grade_by_activity else None),
            )
            for act in activities
        ]
        total_pct, components = compute_partial(
            results,
            weight_general=partial.weight_general,
            weight_special=partial.weight_special,
            weight_exam=partial.weight_exam,
        )
        grade_10 = pct_to_10(total_pct) if total_pct is not None else None

        existing = sess.exec(
            select(PartialGrade).where(
                PartialGrade.student_id == student.id,
                PartialGrade.partial_id == partial.id,
            )
        ).first()
        mode = existing.curve_mode if existing else "none"
        extra = (existing.extra_points or 0.0) if existing else 0.0
        if grade_10 is not None:
            curve_base = _final_for(mode, grade_10, existing.final_value_0_10 if existing else None)
            final_10 = round(max(0.0, min(10.0, curve_base + extra)), 1)
        else:
            curve_base = final_10 = None

        rows.append({
            "student": student,
            "total_pct": round(total_pct, 2) if total_pct is not None else None,
            "grade_10": grade_10,
            "curve_base": curve_base,
            "extra": extra,
            "extra_note": existing.extra_note if existing else None,
            "final_10": final_10,
            "curve_mode": mode,
            "curve_note": existing.curve_note if existing else None,
            "components": components,
            "sega_status": existing.sega_status if existing else "none",
        })

    rows.sort(key=lambda r: r["student"].full_name)
    return rows


@router.get("/{course_id}/partial/{partial_id}", response_class=HTMLResponse)
def partial_view(request: Request, course_id: int, partial_id: int):
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        partial = _owned_partial(sess, course, partial_id)

        rows = _compute_for_course(sess, course, partial)
        mapped_count = len(sess.exec(
            select(Activity).where(
                Activity.course_id == course_id, Activity.partial_id == partial_id,
            )
        ).all())

    n_auto = sum(1 for r in rows if r["curve_mode"] == "auto")
    n_manual = sum(1 for r in rows if r["curve_mode"] == "manual")
    n_extra = sum(1 for r in rows if r["extra"])
    n_pass = sum(1 for r in rows if r["final_10"] is not None and r["final_10"] >= 7.0)
    graded = [r for r in rows if r["final_10"] is not None]
    return templates.TemplateResponse("partial_grades.html", {
        "request": request,
        "dry_run": settings.dry_run,
        "course": course,
        "partial": partial,
        "rows": rows,
        "mapped_count": mapped_count,
        "curve_label": PROTOCOL_LABELS[STANDARD],
        "n_auto": n_auto,
        "n_manual": n_manual,
        "n_curved": n_auto + n_manual,
        "n_extra": n_extra,
        "n_pass": n_pass,
        "n_graded": len(graded),
    })


def _persist(sess: Session, course: Course, partial: Partial, *, mode_setter) -> int:
    """Recompute + persist PartialGrade rows; `mode_setter(pg, computed)` adjusts curve."""
    rows = _compute_for_course(sess, course, partial)
    n = 0
    for row in rows:
        if row["grade_10"] is None:
            continue
        student = row["student"]
        pg = sess.exec(
            select(PartialGrade).where(
                PartialGrade.student_id == student.id,
                PartialGrade.partial_id == partial.id,
            )
        ).first()
        if pg is None:
            pg = PartialGrade(student_id=student.id, partial_id=partial.id,
                              value_0_10=row["grade_10"])
            sess.add(pg)
        pg.value_0_10 = row["grade_10"]
        pg.components_json = components_to_json(row["components"])
        pg.computed_at = datetime.utcnow()
        mode_setter(pg, row["grade_10"])
        n += 1
    return n


@router.post("/{course_id}/partial/{partial_id}/compute", response_class=RedirectResponse)
def compute_and_save(request: Request, course_id: int, partial_id: int):
    """(Re-)compute exact grades; keep each row's existing curve mode in sync."""
    def setter(pg: PartialGrade, computed: float):
        # Recompute auto finals against the new exact grade; leave manual/none alone.
        if pg.curve_mode == "auto":
            pg.final_value_0_10 = _auto_final(computed)
        elif pg.curve_mode == "none":
            pg.final_value_0_10 = None

    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        partial = _owned_partial(sess, course, partial_id)
        n = _persist(sess, course, partial, mode_setter=setter)
        audit_log(sess, "compute_partial", actor="carlos",
                  target=f"course:{course_id} partial:{partial_id}",
                  env=course.moodle_env, dry_run=False, detail={"students": n})
        sess.commit()
    return RedirectResponse(f"/courses/{course_id}/partial/{partial_id}", status_code=303)


@router.post("/{course_id}/partial/{partial_id}/curve/auto", response_class=RedirectResponse)
def apply_auto_curve(request: Request, course_id: int, partial_id: int):
    """Apply the standard (square-root) curve to every non-manual row."""
    def setter(pg: PartialGrade, computed: float):
        if pg.curve_mode != "manual":
            pg.curve_mode = "auto"
            pg.final_value_0_10 = _auto_final(computed)
            pg.curve_note = f"{PROTOCOL_LABELS[STANDARD]} (auto)"

    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        partial = _owned_partial(sess, course, partial_id)
        n = _persist(sess, course, partial, mode_setter=setter)
        audit_log(sess, "curve_auto", actor="carlos",
                  target=f"course:{course_id} partial:{partial_id}",
                  env=course.moodle_env, dry_run=False,
                  detail={"protocol": STANDARD, "students": n})
        sess.commit()
    return RedirectResponse(f"/courses/{course_id}/partial/{partial_id}", status_code=303)


@router.post("/{course_id}/partial/{partial_id}/curve/clear", response_class=RedirectResponse)
def clear_curve(request: Request, course_id: int, partial_id: int):
    """Discard ALL curves/overrides — every row back to the exact grade."""
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        partial = _owned_partial(sess, course, partial_id)
        pgs = sess.exec(select(PartialGrade).where(PartialGrade.partial_id == partial_id)).all()
        for pg in pgs:
            pg.curve_mode = "none"
            pg.final_value_0_10 = None
            pg.curve_note = None
            sess.add(pg)
        audit_log(sess, "curve_clear", actor="carlos",
                  target=f"course:{course_id} partial:{partial_id}",
                  env=course.moodle_env, dry_run=False, detail={"students": len(pgs)})
        sess.commit()
    return RedirectResponse(f"/courses/{course_id}/partial/{partial_id}", status_code=303)


@router.post("/{course_id}/partial/{partial_id}/override", response_class=RedirectResponse)
def set_override(request: Request, course_id: int, partial_id: int,
                 student_id: int = Form(...), final: str = Form(""), reason: str = Form("")):
    """Per-student manual override (or clear, when `final` is blank)."""
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        _owned_partial(sess, course, partial_id)
        pg = sess.exec(select(PartialGrade).where(
            PartialGrade.student_id == student_id, PartialGrade.partial_id == partial_id,
        )).first()
        if pg is not None:
            if final.strip() == "":
                pg.curve_mode = "none"
                pg.final_value_0_10 = None
                pg.curve_note = None
            else:
                try:
                    val = max(0.0, min(10.0, round(float(final.replace(",", ".")), 1)))
                except ValueError:
                    return RedirectResponse(f"/courses/{course_id}/partial/{partial_id}", status_code=303)
                pg.curve_mode = "manual"
                pg.final_value_0_10 = val
                pg.curve_note = reason.strip() or "Manual override"
            sess.add(pg)
            audit_log(sess, "curve_override", actor="carlos",
                      target=f"student:{student_id} partial:{partial_id}",
                      env=course.moodle_env, dry_run=False,
                      detail={"final": pg.final_value_0_10, "reason": pg.curve_note})
            sess.commit()
    return RedirectResponse(f"/courses/{course_id}/partial/{partial_id}", status_code=303)


@router.post("/{course_id}/partial/{partial_id}/extra", response_class=RedirectResponse)
def set_extra(request: Request, course_id: int, partial_id: int,
              student_id: int = Form(...), extra: str = Form(""), reason: str = Form("")):
    """Per-student additive extra-credit (e.g. cultural participation). Blank/0 clears it."""
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        _owned_partial(sess, course, partial_id)
        pg = sess.exec(select(PartialGrade).where(
            PartialGrade.student_id == student_id, PartialGrade.partial_id == partial_id,
        )).first()
        if pg is not None:
            try:
                pts = round(float(extra.replace(",", ".")), 1) if extra.strip() else 0.0
            except ValueError:
                pts = 0.0
            pts = max(0.0, min(10.0, pts))
            pg.extra_points = pts
            pg.extra_note = (reason.strip() or "Extra credit") if pts else None
            sess.add(pg)
            audit_log(sess, "extra_credit", actor="carlos",
                      target=f"student:{student_id} partial:{partial_id}",
                      env=course.moodle_env, dry_run=False,
                      detail={"extra": pts, "reason": pg.extra_note})
            sess.commit()
    return RedirectResponse(f"/courses/{course_id}/partial/{partial_id}", status_code=303)


@router.get("/{course_id}/partial/{partial_id}/dryrun", response_class=HTMLResponse)
def sega_dryrun(request: Request, course_id: int, partial_id: int):
    """Show the SEGA dry-run diff: the curved/final grades that would be uploaded."""
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        partial = _owned_partial(sess, course, partial_id)

        pg_rows = sess.exec(
            select(PartialGrade).where(PartialGrade.partial_id == partial_id)
        ).all()
        results = []
        for pg in pg_rows:
            student = sess.get(Student, pg.student_id)
            results.append({
                "matricula": student.matricula if student else "?",
                "name": student.full_name if student else "?",
                "computed": pg.value_0_10,
                "grade_10": pg.sega_value,            # what actually uploads (curve + extra)
                "curve_mode": pg.curve_mode,
                "curve_note": pg.curve_note,
                "extra": pg.extra_points or 0.0,
                "extra_note": pg.extra_note,
                "sega_status": pg.sega_status,
            })
        results.sort(key=lambda r: r["name"])

        audit_log(sess, "sega_dryrun", actor="carlos",
                  target=f"course:{course_id} partial:{partial_id}",
                  env=course.moodle_env, dry_run=True, detail={"students": len(results)})
        sess.commit()

    return templates.TemplateResponse("sega_dryrun.html", {
        "request": request,
        "dry_run": settings.dry_run,
        "course": course,
        "partial": partial,
        "results": results,
    })
