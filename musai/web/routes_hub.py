"""Course Hub — the course's home page, edited as a form instead of as HTML.

Separate from `/build` on purpose. That surface is *generative*: describe something, an AI
writes it, you publish it once. This one is a **document the professor owns and comes back
to** — change the WhatsApp group in August, fix a weight in October. No AI is involved and
nothing here costs money.

The publish path is shared with the builder (`coursebuild.jobs`), so the hub inherits the
dry-run rail, the progress poller and — because the block marker is constant — the
edit-in-place behaviour: republishing updates the same Moodle label instead of stacking a
second copy of the page.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from musai.config import settings
from musai.coursebuild import hub, hub_store, jobs
from musai.coursebuild.render import lint
from musai.db import engine
from musai.models import Course
from musai.web.app import templates
from musai.web.deps import current_professor, my_course

router = APIRouter()

# The hub belongs at the very top of the course, which is Moodle's section 0.
HUB_SECTION = 0


def _context(request: Request, course: Course, data: dict, **extra) -> dict:
    return {
        "request": request, "course": course, "dry_run": settings.dry_run,
        "data": data, "fields": hub.FIELDS,
        "profile_fields": [f for f in hub.FIELDS if f.scope == "profile"],
        "course_fields": [f for f in hub.FIELDS if f.scope == "course"],
        "warnings": hub.validate(data), "html": hub.render_hub(data),
        "choice_labels": hub.CHOICE_LABELS, "section": HUB_SECTION, **extra,
    }


async def _submitted(request: Request) -> dict:
    form = await request.form()
    return {f.key: str(form.get(f.key, "") or "") for f in hub.FIELDS}


@router.get("/courses/{course_id}/hub", response_class=HTMLResponse)
def hub_page(request: Request, course_id: int, example: str = ""):
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        owner = hub_store.profile_owner_for(course)
        data = hub_store.load_merged(sess, course, owner)
    if example:
        # "Ver ejemplo" — fills every box with sample text so an empty form is never the
        # first thing a professor sees. Nothing is saved until they press Guardar.
        known = {c for c in hub.BY_KEY["lang"].choices}
        data = hub.resolve(hub.example_data(example if example in known else "es"),
                           course=course)
    return templates.TemplateResponse("course_hub.html", _context(request, course, data))


@router.post("/courses/{course_id}/hub/save", response_class=HTMLResponse)
async def hub_save(request: Request, course_id: int):
    """Save both scopes and hand back the preview. One button, one mental model."""
    submitted = await _submitted(request)
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        owner = hub_store.profile_owner_for(course)
        hub_store.save_profile(sess, submitted, owner)
        hub_store.save_course(sess, course_id, submitted)
        data = hub_store.load_merged(sess, course, owner)
    return templates.TemplateResponse(
        "course_hub_preview.html", _context(request, course, data, saved=True))


@router.post("/courses/{course_id}/hub/publish", response_class=HTMLResponse)
def hub_publish(request: Request, course_id: int, live: str = Form(""),
                overwrite: str = Form("")):
    """Publish what is SAVED — never what is merely typed into the form.

    Deliberate: the page students end up seeing is the one the professor pressed Guardar on
    and then looked at, not whatever half-edited state a stray click submitted.

    Target is the **section summary**, not a label: a course's home page IS section 0's
    summary. Publishing the hub as an activity put it *underneath* the professor's existing
    hand-written page instead of replacing it (found live 2026-08-07). `overwrite` is the
    second key for that door — without it a live run refuses to destroy a summary MUSAI did
    not write, and hands back the backup path instead.
    """
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        owner = current_professor(request, sess).email
        data = hub_store.load_merged(sess, course, hub_store.profile_owner_for(course))

    html = hub.render_hub(data)
    problems = lint(html)
    if problems:
        return templates.TemplateResponse("job_progress.html", {
            "request": request, "course": course,
            "job": {"status": "failed", "result": {
                "ok": False, "error": "Refused — not Moodle-safe: " + "; ".join(problems),
                "steps": []}}})

    dry_run = settings.dry_run and not live
    job_id = jobs.start_publish(course.id, html, HUB_SECTION, dry_run,
                                target=jobs.SECTION_SUMMARY,
                                overwrite_foreign=bool(overwrite), owner=owner)
    return templates.TemplateResponse("job_progress.html", {
        "request": request, "course": course, "job": jobs.get_job(job_id), "job_id": job_id,
    })
