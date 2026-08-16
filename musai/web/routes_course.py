"""The course workspace — Overview, Activities & mapping, Grades, and the evidence export.

These three are the tabs that read MUSAI's own record of a course rather than driving a
browser. Everything slow lives elsewhere: `routes_transfer` (backup/restore), `routes_dates`
(Cronograma), `routes_hub` (content), `routes_messages`.

🔴 Every route resolves its course through `deps.my_course`. See `musai/web/deps.py` — the
2026-08-14 audit found this module's `POST /{id}/mapping` rewriting any course's activity→
partial mapping with no ownership check at all.
"""

import re
from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session, select

from musai import activitymap, checklists, jobs
from musai.config import settings
from musai.coursedates import store as dates_store
from musai.db import engine
from musai.models import (Activity, Course, Enrollment, Partial, PartialGrade, Professor,
                          Semester)
from musai.reporting.export import workbook_bytes
from musai.web.app import templates
from musai.web.deps import my_course, owned_course

router = APIRouter(prefix="/courses")

#: This job kind lives here rather than in `musai/jobs.py` because it belongs to this tab.
#: `jobs.py` names the kinds that several routers share; a one-router kind naming itself keeps
#: that list meaningful.
ACTIVITY_IMPORT = "activity_import"
GRADEBOOK_IMPORT = "gradebook_import"


def _work_fragment(request: Request, job_id: int, owner: str, *,
                   kind: str = ACTIVITY_IMPORT):
    """Render the shared waiting component for a job started here.

    ⚠️ `kind` used to be hardcoded to `ACTIVITY_IMPORT`, which was fine while this router ran one
    job. It is a parameter now because the wrong kind picks the wrong checklist — every item would
    sit grey for the whole run with nothing on screen saying why, which is the exact silent
    failure `test_every_matcher_appears_in_the_code_that_emits_it` exists to prevent.
    """
    from musai.web.routes_transfer import _work_context

    job = jobs.get(job_id, owner=owner)
    return templates.TemplateResponse(
        "work_progress.html", _work_context(request, job, job_id=job_id, kind=kind))

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

CATEGORIES = ["general", "special", "exam", "forum"]
SEGA_EVALUACIONES = ["PARCIAL 1", "PARCIAL 2", "EXAMEN FINAL ORDINARIO",
                     "EXTRAORDINARIO", "TÍTULO DE SUFICIENCIA"]


def _course_state(sess: Session, course: Course) -> dict:
    """What MUSAI knows about this course — the numbers every tab's badge is derived from.

    One function so the Overview's summary and the tab strip's dots cannot disagree. A dot on
    "Activities" that says work is pending, next to an Overview that says everything is mapped,
    is worse than no dot: it teaches the professor to ignore both.
    """
    activities = list(sess.exec(
        select(Activity).where(Activity.course_id == course.id).order_by(Activity.name)).all())
    partials = list(sess.exec(
        select(Partial).where(Partial.course_id == course.id).order_by(Partial.id)).all())
    students = len(sess.exec(
        select(Enrollment).where(Enrollment.course_id == course.id)).all())
    unmapped = [a for a in activities if a.partial_id is None]
    partial_ids = [p.id for p in partials]
    graded = len(sess.exec(
        select(PartialGrade).where(PartialGrade.partial_id.in_(partial_ids))).all()
    ) if partial_ids else 0

    return {
        "activities": activities,
        "partials": partials,
        "students": students,
        "unmapped": unmapped,
        "n_activities": len(activities),
        "n_unmapped": len(unmapped),
        "n_partials": len(partials),
        "n_graded": graded,
    }


def _tab_badges(state: dict) -> dict:
    """Which tabs have something waiting. A dot is a claim, so each one names its evidence."""
    badges = {}
    if state["n_unmapped"]:
        badges["activities"] = (f"{state['n_unmapped']} activity"
                                f"{'' if state['n_unmapped'] == 1 else 's'} not assigned "
                                f"to a partial")
    return badges


def next_step(course: Course, state: dict) -> dict | None:
    """The **one** thing to do next on this course, or `None` when nothing is missing.

    Python rather than a Jinja conditional, for two reasons that are the same reason: it is a
    decision, not a presentation detail, and a decision the professor will act on ought to be
    testable without rendering HTML. The Jinja draft of this was a five-branch inline `if`
    expression spanning twenty lines — unreadable, and unreachable from a test.

    Ordered by dependency, not by severity: each step is worthless until the one above it is
    done, so showing the second while the first is unmet just sends someone to a page that
    cannot work yet.
    """
    if not course.moodle_course_id:
        return {
            "title": "This course has no Moodle id yet",
            "body": "Nothing that talks to Moodle — backup, restore, dates, messages — can run "
                    "without it. Re-map your courses from the cockpit and it gets filled in.",
            "label": "Back to the cockpit", "href": "/",
        }
    if not state["n_activities"]:
        return {
            "title": "MUSAI does not know this course's activities yet",
            "body": "Import them and the partials, grades and Cronograma below start working. "
                    "Until then this course is a name and a Moodle id.",
            "label": "Go to Activities", "href": f"/courses/{course.id}/activities",
        }
    if not state["n_partials"]:
        return {
            "title": "This course has no partials",
            "body": "A partial is what a grade is computed into. Without one there is nothing "
                    "for an activity to belong to.",
            "label": "See Grades", "href": f"/courses/{course.id}/grades",
        }
    n = state["n_unmapped"]
    if n:
        return {
            "title": f"{n} activit{'y is' if n == 1 else 'ies are'} not assigned to a partial",
            "body": "A grade is computed only from activities that belong to a partial — "
                    "anything unassigned is silently left out of every calculation.",
            "label": "Assign them", "href": f"/courses/{course.id}/activities",
        }
    return None


# ── Overview ──────────────────────────────────────────────────────────────────
@router.get("/{course_id}", response_class=HTMLResponse)
def course_detail(request: Request, course_id: int):
    """The course at a glance: what MUSAI holds, and what is missing before it can help."""
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        state = _course_state(sess, course)
        semester = sess.get(Semester, course.semester_id)

    return templates.TemplateResponse("course_overview.html", {
        "request": request,
        "dry_run": settings.dry_run,
        "course": course,
        "semester": semester,
        "tab_badges": _tab_badges(state),
        "next_step": next_step(course, state),
        **state,
        **_gradebook_freshness(course, state.get('students', 0)),
    })


# ── Activities & mapping ──────────────────────────────────────────────────────
@router.get("/{course_id}/activities", response_class=HTMLResponse)
def activities_page(request: Request, course_id: int, saved: str = ""):
    """Each activity's partial and category — the input the whole grade engine runs on."""
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        state = _course_state(sess, course)
        snap = dates_store.snapshot(sess, course.id)
        proposals = activitymap.propose(sess, course, snap)
        by_activity = {p.activity_id: p for p in proposals}

    return templates.TemplateResponse("course_activities.html", {
        "request": request,
        "dry_run": settings.dry_run,
        "course": course,
        "categories": CATEGORIES,
        "saved": bool(saved),
        "proposals": by_activity,
        "proposal_summary": activitymap.summarise(proposals),
        "has_snapshot": bool(snap),
        "tab_badges": _tab_badges(state),
        **state,
    })


@router.post("/{course_id}/activities/import", response_class=HTMLResponse)
def import_activities(request: Request, course_id: int):
    """Read the course in Moodle and create the `Activity` rows it is missing.

    The same browser trip the Cronograma needs, so it saves the structure snapshot too — one
    read serves both tabs. A course has one page load per tab, so doing this twice is a minute
    of a professor's morning spent learning the same thing twice.
    """
    with Session(engine, expire_on_commit=False) as sess:
        prof, course = owned_course(request, sess, course_id)
        prof_id, email, cid, group = prof.id, prof.email, course.id, course.group_code

    already = jobs.running_for(email, ACTIVITY_IMPORT, target=cid)
    if already:
        return _work_fragment(request, already["id"], email)

    job_id = jobs.start(
        ACTIVITY_IMPORT, owner=email, params={"target": cid, "group": group},
        work=lambda jid: _import_work(jid, prof_id, cid),
    )
    return _work_fragment(request, job_id, email)


def _import_work(job_id: int, professor_id: int, course_id: int) -> dict:
    from musai.automation.credentials import CredentialsMissing, resolve_for_professor
    from musai.coursedates.discover import read_course_structure

    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine, expire_on_commit=False) as sess:
        prof = sess.get(Professor, professor_id)
        course = sess.get(Course, course_id)
        idc, group = course.moodle_course_id, course.group_code

    if not idc:
        return {"ok": False, "refused": True,
                "error": f"{group} has no Moodle course id yet. Re-map your courses from the "
                         f"cockpit first — nothing here can read a course MUSAI cannot find."}
    try:
        identity = resolve_for_professor(prof, system="moodle")
    except CredentialsMissing as e:
        # 🔴 A refusal, not a crash, and never a fallback to another account.
        return {"ok": False, "refused": True, "error": str(e)}

    # 🔴 `identity=`, never `as_user=identity.username`. The username road re-resolves the
    # password from `MOODLE_PWD_<USER>` in `.env`, which is the delegate mechanism — it would
    # refuse a professor whose password is sitting in the vault, and for the owner himself it
    # would quietly succeed for the wrong reason.
    step(f"Opening {group} (idc {idc}) as {identity.username}…")
    snapshot = read_course_structure(idc, headless=True, identity=identity, on_step=step)

    with Session(engine, expire_on_commit=False) as sess:
        course = sess.get(Course, course_id)
        dates_store.save_snapshot(sess, course_id, snapshot)
        result = activitymap.import_activities(sess, course, snapshot)

    step(f"{result['created']} new · {result['matched']} already known · "
         f"{result['found']} gradable of {sum(result['skipped'].values()) + result['found']} "
         f"things in the course")
    if result["vanished"]:
        # Reported, never acted on — an Activity row owns its grades.
        step(f"⚠ {len(result['vanished'])} activity(ies) MUSAI knows are no longer in the "
             f"course; they were kept.")
    return {"ok": True, **result}


@router.post("/{course_id}/mapping", response_class=RedirectResponse)
async def save_mapping(request: Request, course_id: int):
    """Save activity → partial + category assignments (submitted as a flat form)."""
    form = await request.form()
    with Session(engine, expire_on_commit=False) as sess:
        # 🔴 The ownership check comes FIRST, before the form is applied. Until 2026-08-14
        # this route took a course id off the URL and rewrote that course's activity→partial
        # mapping with no check at all — a blind write into a colleague's gradebook shape,
        # reachable by changing one digit.
        my_course(request, sess, course_id)
        activities = sess.exec(
            select(Activity).where(Activity.course_id == course_id)
        ).all()
        for act in activities:
            partial_id_raw = form.get(f"partial_{act.id}", "")
            category = form.get(f"category_{act.id}", "general")
            act.partial_id = int(partial_id_raw) if partial_id_raw else None
            act.category = category
            sess.add(act)
        sess.commit()
    return RedirectResponse(f"/courses/{course_id}/activities?saved=1", status_code=303)


# ── Gradebook refresh ─────────────────────────────────────────────────────────
@router.post("/{course_id}/gradebook/refresh", response_class=HTMLResponse)
def refresh_gradebook(request: Request, course_id: int):
    """Download this course's Moodle gradebook and read it into MUSAI.

    🔴 **This is the only way enrolment gets into MUSAI, and until now it was CLI-only.**
    `Enrollment` rows are created in exactly one place — `grading/ingest.py`, from a gradebook
    export — and the participants page has never been read into the database at all. So the
    Overview tile reading *"Students · 10"* was never a roster; it was the size of the last file
    ingested. The owner hit this on 1-LED-A: 10 in MUSAI, 30-something in the live course, and no
    button anywhere in the cockpit to fix it. `musai/automation/messaging.py:211` had already
    written up that exact course as the worked example of the hazard.

    Read-only against Moodle: one login, one course open, one export download. It writes only
    MUSAI's own database, which is why there is no dry-run branch and no confirmation.
    """
    with Session(engine, expire_on_commit=False) as sess:
        prof, course = owned_course(request, sess, course_id)
        prof_id, email, cid, group = prof.id, prof.email, course.id, course.group_code

    already = jobs.running_for(email, GRADEBOOK_IMPORT, target=cid)
    if already:
        # Not an error. Two concurrent Playwright sessions on one Moodle account is the bug that
        # double-sent 35 students, so a second press shows the run that is already going.
        return _work_fragment(request, already["id"], email, kind=GRADEBOOK_IMPORT)

    job_id = jobs.start(
        GRADEBOOK_IMPORT, owner=email, params={"target": cid, "group": group},
        work=lambda jid: _gradebook_work(jid, prof_id, cid),
    )
    return _work_fragment(request, job_id, email, kind=GRADEBOOK_IMPORT)


def _gradebook_work(job_id: int, professor_id: int, course_id: int) -> dict:
    from musai.automation.credentials import CredentialsMissing, resolve_for_professor
    from musai.automation.moodle_export import export_gradebook_ods
    from musai.grading.ingest import ingest_gradebook

    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine, expire_on_commit=False) as sess:
        course = sess.get(Course, course_id)
        prof = sess.get(Professor, professor_id)
        idc, group, subject = course.moodle_course_id, course.group_code, course.subject
        before = len(course.enrollments)

    if not idc:
        return {"ok": False, "refused": True,
                "error": f"{group} has no Moodle course id yet. Re-map your courses from the "
                         f"cockpit first — nothing here can read a course MUSAI cannot find."}
    try:
        identity = resolve_for_professor(prof, system="moodle")
    except CredentialsMissing as e:
        # 🔴 A refusal, not a crash, and never a fallback to another account.
        return {"ok": False, "refused": True, "error": str(e)}

    # 🔴 `identity=`, never `as_user=identity.username` — same reasoning as `_import_work` above.
    # Until 2026-08-14 `export_gradebook_ods` read `settings.uach_*` directly and had neither
    # parameter, so this button would have downloaded every professor's gradebook as the owner.
    ods = export_gradebook_ods(idc, materia=subject, grupo=group, headless=True,
                               identity=identity, on_step=step)

    with Session(engine, expire_on_commit=False) as sess:
        course = sess.get(Course, course_id)
        counts = ingest_gradebook(sess, course, ods)
        sess.commit()
        after = len(course.enrollments)
        stamped = course.gradebook_ingested_at

    # ⚠️ Reports the ENROLMENT DELTA, not just the file's row count. "10 → 33 students" is the
    # sentence a professor came here for; "23 new enrollments" makes them do the arithmetic, and
    # a bare "done" tells them nothing about whether the thing they noticed is fixed.
    if after == before and not counts["grades_new"] and not counts["grades_updated"]:
        step(f"No change — still {after} students, same grades.")
    else:
        step(f"{before} → {after} students · {counts['grades_new']} new / "
             f"{counts['grades_updated']} updated grades")
    return {"ok": True, "students_before": before, "students_after": after,
            "ingested_at": stamped, "file": str(ods), **counts}


#: A gradebook older than this is called out on screen. Chosen as "older than a fortnight", the
#: scale on which a cohort actually changes: enrolments move in the first weeks of a semester, and
#: a count taken before that is the one that misleads.
GRADEBOOK_STALE_DAYS = 14


def _gradebook_freshness(course: Course, students: int = 0) -> dict:
    """How old this course's student count is, in the words the page prints.

    🔴 **THREE states, not two.** The first version had two and printed a falsehood: every course
    that existed before this column was added has enrolments but a NULL timestamp (the migration
    deliberately does not backfill a date it cannot know), so 1-LED-A would have rendered
    *"Never imported. MUSAI does not know who is enrolled"* directly above a table listing ten
    students. The states are:

    * **dated** — a timestamp, so the page can say *"as of 3 Aug"* and warn when it is old;
    * **undated** — students but no timestamp: MUSAI holds a roster and genuinely does not know
      when it arrived. That is *unknown*, and saying "never" instead is the same class of error as
      calling an unreadable field a finding;
    * **never** — no timestamp and nobody enrolled, which is the only case that may say "never".

    ⚠️ Formatted here rather than in Jinja because `strftime("%-d")` is glibc-only — on Windows it
    raises, which would have turned the Grades tab into a 500 on the one machine this runs on.
    """
    at = course.gradebook_ingested_at
    if at is None:
        return {"gradebook_ingested_at": None, "gradebook_ingested_label": "",
                "gradebook_age_days": None, "gradebook_stale": False,
                "gradebook_state": "undated" if students else "never"}
    age = (datetime.utcnow() - at).days
    return {
        "gradebook_ingested_at": at,
        "gradebook_ingested_label": f"{at.day} {at.strftime('%b')}",
        "gradebook_age_days": age,
        "gradebook_stale": age >= GRADEBOOK_STALE_DAYS,
        "gradebook_state": "dated",
    }


# ── Grades ────────────────────────────────────────────────────────────────────
@router.get("/{course_id}/grades", response_class=HTMLResponse)
def grades_page(request: Request, course_id: int):
    """The partials of this course, as the way in to each one's grade sheet.

    Exists because the tab strip needs somewhere to point. Before this, a partial was only
    reachable from the old course page's card list, so "Grades" had no home of its own and
    the seventh tab would have had to lie about where it goes.
    """
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        state = _course_state(sess, course)
        rows = []
        for p in state["partials"]:
            mapped = [a for a in state["activities"] if a.partial_id == p.id]
            computed = sess.exec(
                select(PartialGrade).where(PartialGrade.partial_id == p.id)).all()
            rows.append({
                "partial": p,
                "n_activities": len(mapped),
                "n_computed": len(computed),
                "n_uploaded": sum(1 for g in computed if g.sega_status == "saved"),
            })

    return templates.TemplateResponse("course_grades.html", {
        "request": request,
        "dry_run": settings.dry_run,
        "course": course,
        "rows": rows,
        "tab_badges": _tab_badges(state),
        **state,
        **_gradebook_freshness(course, state.get('students', 0)),
    })


@router.get("/{course_id}/export.xlsx")
def export_xlsx(request: Request, course_id: int):
    """Download the styled evidence workbook for this group.

    🔴 Scoped like every other course route, and this one more urgently than most: the
    workbook contains the roster — names, matrículas and grades of real students.
    """
    with Session(engine) as sess:
        course = my_course(request, sess, course_id)
        group = course.group_code
    data = workbook_bytes(course_id)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", group)
    return Response(
        content=data,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="MUSAI_{safe}_resumen.xlsx"'},
    )
