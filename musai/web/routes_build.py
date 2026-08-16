"""Course Builder — describe content in words, preview it, publish it into Moodle.

Deliberately NOT part of `/assistant`. That agent is structurally read-only (one of the
three rails); bolting a write path onto it would dissolve the guarantee. Reading and writing
live on separate surfaces.

Compose is cheap and local (~$0.0003, no browser). Publish is a slow browser job that
mutates a live course, and defaults to dry-run.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from musai import metering
from musai.ai import budget as bud
from musai.config import settings
from musai.coursebuild import jobs
from musai.coursebuild.compose import compose
from musai.coursebuild.render import PALETTES, lint
from musai.db import engine
from musai.models import Course
from musai.web.app import templates
from musai.web.deps import current_professor, my_course, owned_job

router = APIRouter()

# Moodle's General section is 0; topic tabs follow. Kept explicit rather than scraped —
# where content lands is the professor's decision, not the model's.
SECTIONS = [(0, "General"), (1, "Tema 1"), (2, "Tema 2"), (3, "Tema 3"), (4, "Tema 4")]


def _course(request: Request, sess: Session, course_id: int) -> Course:
    """Owner-scoped. Was `sess.get(Course, course_id)` until 2026-08-14 — which let any
    signed-in professor publish AI-composed HTML into any course by id."""
    return my_course(request, sess, course_id)


@router.get("/courses/{course_id}/build", response_class=HTMLResponse)
def build_page(request: Request, course_id: int):
    with Session(engine, expire_on_commit=False) as sess:
        course = _course(request, sess, course_id)
        prof = current_professor(request, sess)
        usage = bud.summary(sess, prof.email, is_admin=prof.is_admin)
        spend = metering.month_to_date(sess, prof.email, is_admin=prof.is_admin)
    return templates.TemplateResponse("course_build.html", {
        "request": request, "dry_run": settings.dry_run, "course": course,
        "sections": SECTIONS, "palettes": sorted(PALETTES), "usage": usage, "spend": spend,
        "compose_cost": metering.price_micro_usd(
            requests=1, seconds=6.0, tokens_in=2500, tokens_out=900),
    })


@router.post("/courses/{course_id}/build/preview", response_class=HTMLResponse)
def build_preview(request: Request, course_id: int, prompt: str = Form(...),
                  section: int = Form(0), lucky: str = Form(""), live: str = Form("")):
    """Compose a block and render it. With `lucky`, publish straight away (dry-run honored)."""
    import time

    with Session(engine, expire_on_commit=False) as sess:
        course = _course(request, sess, course_id)
        prof = current_professor(request, sess)
        owner, is_admin = prof.email, bool(prof.is_admin)
        allowed, why = bud.check(sess, owner, is_admin=is_admin)
        if allowed:
            allowed, why = metering.check(sess, owner, is_admin=is_admin)
        sess.commit()

    if not allowed:
        reached = ("This month's free MUSAI usage" if why == "monthly_allowance"
                   else f"Daily AI budget ({why})")
        return templates.TemplateResponse("course_build_result.html", {
            "request": request, "course": course, "section": section,
            "error": f"{reached} is used up. See Settings ▸ Usage.",
        })

    t0 = time.monotonic()
    out = compose(prompt.strip(), course_label=f"{course.group_code} ({course.subject})"
                  if course else "")
    elapsed = time.monotonic() - t0
    with Session(engine) as sess:
        bud.record(sess, owner, out["result"])
        metering.record(sess, owner, "build_compose", seconds=elapsed,
                        detail=course.group_code if course else "",
                        tokens_in=out["result"].tokens_in, tokens_out=out["result"].tokens_out,
                        model=getattr(out["result"], "model", "") or "")
        sess.commit()
        usage = bud.summary(sess, owner, is_admin=is_admin)

    if not out["ok"]:
        return templates.TemplateResponse("course_build_result.html", {
            "request": request, "course": course, "section": section, "usage": usage,
            "error": f"Could not compose that ({out['reason']}). Try rephrasing.",
        })

    job_id = job = None
    if lucky:
        # "I'm feeling lucky" — straight to publish, still through the job queue so the page
        # shows real progress instead of freezing.
        job_id = jobs.start_publish(course.id, out["html"], section,
                                    settings.dry_run and not live, owner=owner)
        job = jobs.get_job(job_id)

    return templates.TemplateResponse("course_build_result.html", {
        "request": request, "course": course, "section": section, "usage": usage,
        "prompt": prompt.strip(), "block": out["block"], "html": out["html"],
        "lucky_job": job_id, "job": job, "job_id": job_id,
    })


@router.post("/courses/{course_id}/build/publish", response_class=HTMLResponse)
def build_publish(request: Request, course_id: int, html: str = Form(...),
                  section: int = Form(0), prompt: str = Form(""), live: str = Form("")):
    """Kick off a publish job and return a poller. Returns in milliseconds, not minutes.

    `live` is a per-action override of the global DRY_RUN — so going live no longer means
    editing .env and restarting uvicorn (which would ALSO arm the SEGA uploader).
    """
    with Session(engine, expire_on_commit=False) as sess:
        course = _course(request, sess, course_id)
        owner = current_professor(request, sess).email

    problems = lint(html)
    if problems:
        return templates.TemplateResponse("job_progress.html", {
            "request": request, "course": course,
            "job": {"status": "failed", "result": {
                "ok": False, "error": "Refused — not Moodle-safe: " + "; ".join(problems),
                "steps": []}}})

    dry_run = settings.dry_run and not live
    job_id = jobs.start_publish(course.id, html, section, dry_run, owner=owner)
    return templates.TemplateResponse("job_progress.html", {
        "request": request, "course": course, "job": jobs.get_job(job_id),
        "job_id": job_id,
    })


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_progress(request: Request, job_id: int):
    """Poll fragment — the real steps the browser job has reached so far.

    Publish jobs only, now. The Cronograma used to poll here through a job store of its own
    (`coursedates/jobs.py`, removed 2026-08-14) and moved to `musai/jobs.py` + `/work/{id}`
    with everything else — three near-identical job stores meant three places to add an
    ownership check to, and two of them had been missed.
    """
    return templates.TemplateResponse("job_progress.html", {
        "request": request, "job": owned_job(request, jobs.get_job(job_id)),
        "job_id": job_id, "course": None,
    })
