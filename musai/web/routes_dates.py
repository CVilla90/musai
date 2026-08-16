"""Cronograma — set every activity's availability dates, tab by tab.

The professor's mental model is *"whatever is under this tab happens in this period"*, so the
page is built around the tab list and never around individual activities. What it writes is
still per activity and per type, because a quiz, an assignment and a book do not share a
single date field between them.

Three surfaces, in the order they are used:

1. **the window** — start, end, how many parcials, how long an exam stays open;
2. **the tab map** — guessed, then corrected by hand, and the correction is what persists;
3. **the plan** — every activity with its proposed dates, reviewed *before* anything runs.

`Simulacro` opens all 54 forms and saves nothing; `Aplicar` is the same run with the save
click enabled, and it needs its own checkbox (rail 2).
"""

from datetime import date

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from musai import checklists, jobs
from musai.config import settings
from musai.coursedates import periods, store, tabmap
from musai.db import engine
from musai.models import Course
from musai.semesters import active_semester
from musai.web.app import templates
from musai.web.deps import current_professor, my_course, owned_course

router = APIRouter()

#: The Cronograma's two write modes. `read` used to be a third, and is now the shared
#: `activity_import` job in `routes_course.py` — reading the tab strip is the identical
#: browser trip the Activities tab needs, and a course costs one page load per tab.
DRYRUN, APPLY = "dryrun", "apply"
COURSE_DATES = "course_dates"

# Value encoding for the per-tab dropdown. A single string keeps the form trivial, and the
# labels are what a professor would say out loud.
SPECIAL_CHOICES = [
    (tabmap.KIND_ALWAYS_OPEN, "Siempre abierto"),
    (tabmap.KIND_MAKEUP, "Recuperación (al final)"),
    (tabmap.KIND_SKIP, "No tocar"),
]


def _choices(count: int):
    out = []
    for i in range(1, count + 1):
        out.append((f"{i}:content", f"Parcial {i} · contenido"))
        out.append((f"{i}:exam", f"Parcial {i} · examen"))
    return out + SPECIAL_CHOICES


def _value(rule) -> str:
    if rule.kind == tabmap.KIND_PERIOD:
        return f"{rule.period}:{rule.slot}"
    return rule.kind


def _context(request: Request, course: Course, **extra) -> dict:
    with Session(engine, expire_on_commit=False) as sess:
        semester = active_semester(sess)
        try:
            data, cal, tmap, plan = store.compose(sess, course, semester)
            error = None
        except periods.PeriodError as exc:
            data = store.load(sess, course, semester)
            cal, tmap, plan, error = None, None, None, str(exc)
    return {
        "request": request, "course": course, "data": data, "calendar": cal,
        "tabmap": tmap, "plan": plan, "error": error, "dry_run": settings.dry_run,
        "choices": _choices(int(data["periods"])), "value_of": _value,
        "semester": semester, **extra,
    }


@router.get("/courses/{course_id}/cronograma", response_class=HTMLResponse)
def page(request: Request, course_id: int):
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
    return templates.TemplateResponse("course_dates.html", _context(request, course))


@router.post("/courses/{course_id}/cronograma/settings", response_class=HTMLResponse)
def save_settings(request: Request, course_id: int,
                  starts: str = Form(...), ends: str = Form(...),
                  periods_: int = Form(3, alias="periods"),
                  exam_days: int = Form(7), cutoff: str = Form("")):
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        store.save_settings(sess, course_id, starts=starts, ends=ends,
                            periods=int(periods_), exam_days=int(exam_days),
                            cutoff=bool(cutoff))
    return templates.TemplateResponse("course_dates_plan.html",
                                      _context(request, course, saved=True))


@router.post("/courses/{course_id}/cronograma/tab", response_class=HTMLResponse)
def save_tab(request: Request, course_id: int,
             section: int = Form(...), choice: str = Form(...)):
    """Record ONE tab's assignment as a manual decision.

    `manual: True` is the whole point — the map is re-guessed from the tab strip on every
    re-read, and without the flag a re-read would silently undo this.
    """
    kind, period, slot = tabmap.KIND_PERIOD, None, tabmap.SLOT_CONTENT
    if ":" in choice:
        p, slot = choice.split(":", 1)
        period = int(p)
    else:
        kind = choice

    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        data = store.load(sess, course)
        saved = data.get("tabmap") or {"rules": []}
        rules = [r for r in saved.get("rules", []) if r["section"] != section]
        rules.append({"section": section, "kind": kind, "period": period, "slot": slot,
                      "manual": True})
        store.save_settings(sess, course_id, tabmap={"rules": rules})
    return templates.TemplateResponse("course_dates_plan.html", _context(request, course))


@router.post("/courses/{course_id}/cronograma/shift", response_class=HTMLResponse)
def add_shift(request: Request, course_id: int,
              days: int = Form(...), period: str = Form("")):
    """Extend one parcial (or slide the whole calendar) by N days.

    This is the operation that actually repeats. The owner's own account of the manual version:
    *"some students will ask for an extension, so I would have to go back and extend the
    whole thing, every tab, every quiz, assignment…"*

    An empty `period` means the whole calendar. Adding a shift changes no dates in Moodle by
    itself — it re-cuts the plan, and the plan still has to be run.
    """
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        data = store.load(sess, course)
        shifts = list(data.get("shifts") or [])
        shifts.append({"period": int(period) if period else None, "days": int(days)})
        store.save_settings(sess, course_id, shifts=shifts)
    return templates.TemplateResponse("course_dates_plan.html", _context(request, course))


@router.post("/courses/{course_id}/cronograma/shift/drop", response_class=HTMLResponse)
def drop_shift(request: Request, course_id: int, index: int = Form(...)):
    """Remove one extension. Undo has to be as cheap as the action, or nobody experiments."""
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        shifts = list(store.load(sess, course).get("shifts") or [])
        if 0 <= index < len(shifts):
            shifts.pop(index)
        store.save_settings(sess, course_id, shifts=shifts)
    return templates.TemplateResponse("course_dates_plan.html", _context(request, course))


@router.post("/courses/{course_id}/cronograma/run", response_class=HTMLResponse)
def run(request: Request, course_id: int, action: str = Form(...), live: str = Form("")):
    """Start the browser job that writes the dates. `apply` additionally needs the live box.

    Runs on `musai/jobs.py` like every other slow thing in the cockpit, which is what gives it
    the ownership check, the stale-vs-failed distinction and the shared waiting component. It
    used to have a job store of its own (`coursedates/jobs.py`, now removed) — three
    near-identical stores meant three places to add an owner to, and two of them were missed.
    """
    with Session(engine, expire_on_commit=False) as sess:
        prof, course = owned_course(request, sess, course_id)
        prof_id, email, cid, group = prof.id, prof.email, course.id, course.group_code

    if action == APPLY and not live:
        action = DRYRUN               # fail safe: an unticked box is a dry run, not a write

    already = jobs.running_for(email, COURSE_DATES, target=cid)
    if already:
        # Two browser sessions writing dates to one course, as the same Moodle user, is the
        # shape of the bug that double-sent 35 students a message.
        return _work(request, already["id"], email)

    job_id = jobs.start(
        COURSE_DATES, owner=email,
        params={"target": cid, "group": group, "action": action},
        work=lambda jid: _dates_work(jid, prof_id, cid, action),
    )
    return _work(request, job_id, email)


def _work(request: Request, job_id: int, owner: str):
    from musai.web.routes_transfer import _work_context

    job = jobs.get(job_id, owner=owner)
    return templates.TemplateResponse(
        "work_progress.html", _work_context(request, job, job_id=job_id, kind=COURSE_DATES))


def _dates_work(job_id: int, professor_id: int, course_id: int, action: str) -> dict:
    """Cut the plan from the SAVED snapshot and write it, activity by activity.

    🔴 Reads the plan fresh here rather than taking it from the request. The professor may have
    adjusted a tab or added a shift between loading the page and pressing the button, and a plan
    carried through the form would be a **cache of a decision she has already changed** — which
    on this surface means writing last-minute-corrected dates as the uncorrected ones.
    """
    from musai.automation.credentials import CredentialsMissing, resolve_for_professor
    from musai.coursedates.apply import apply_plan
    from musai.models import Professor

    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine, expire_on_commit=False) as sess:
        prof = sess.get(Professor, professor_id)
        course = sess.get(Course, course_id)
        idc, group = course.moodle_course_id, course.group_code
        if not idc:
            return {"ok": False, "refused": True,
                    "error": f"{group} no tiene id de curso en Moodle."}
        try:
            _data, _cal, _tmap, plan = store.compose(sess, course)
        except periods.PeriodError as exc:
            return {"ok": False, "refused": True, "error": str(exc)}

    if plan is None:
        return {"ok": False, "refused": True,
                "error": "Primero hay que leer el curso — MUSAI no sabe qué actividades tiene."}

    # 🔴 The signed-in professor's OWN account, from the vault, with no fallback.
    #
    # Until 2026-08-14 this path called `apply_plan` with no identity at all, which resolves to
    # `UACH_USERNAME`/`UACH_PASSWORD` in `.env` — the owner's account. On his own machine, with one
    # user, that was invisibly correct. The moment a colleague signs in and presses *Aplicar* on
    # her own course, it becomes a browser logging in as the owner to write dates into a course he
    # does not teach: it either fails with a permissions error she cannot interpret, or it
    # succeeds and Moodle records HIM as the author of every date in her gradebook.
    #
    # Note `identity=` and not `as_user=`: the username road looks up MOODLE_PWD_<USER> in .env
    # and would refuse a professor whose password is in the vault.
    try:
        identity = resolve_for_professor(prof, system="moodle")
    except CredentialsMissing as e:
        return {"ok": False, "refused": True, "error": str(e)}

    step(f"Abriendo {group} (idc {idc}) como {identity.username}…")
    result = apply_plan(idc=idc, plan=plan, dry_run=(action != APPLY), headless=True,
                        group_label=group, identity=identity, on_step=step)
    result = {k: v for k, v in result.items() if k != "steps"}
    result["action"] = action
    return result
