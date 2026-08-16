"""Messaging Hub — compose one message, preview it, send it to a group.

The page is arranged around the one thing Moodle's own screen will not tell you: **who is
about to receive this.** Moodle's compose page renders `Agregado nuevo receptor 1` — a count
— and never a name list, so the recipient table here is not a convenience, it is the only
place that information exists before the message is gone.

`👓 Simulacro` walks the real path to Moodle's own *Vista previa* and stops. `✉️ Enviar`
additionally requires the group code typed by hand.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from musai.automation.messaging import PURPOSES, MessagingRefused
from musai.config import settings
from musai.db import engine
from musai.messaging import jobs, store
from musai.models import Course
from musai.web.app import templates
from musai.web.deps import current_professor, my_course, owned_job

router = APIRouter()

PURPOSE_LABELS = [
    ("bienvenida", "Bienvenida", "Criterio 2 de la evaluación docente"),
    ("seguimiento", "Seguimiento", "Criterio 6 — se necesitan al menos dos"),
    ("cierre", "Cierre de ciclo", "Criterio 9"),
    ("aviso", "Aviso suelto", "No cuenta para la evaluación"),
]


def _context(request: Request, course: Course, **extra) -> dict:
    with Session(engine, expire_on_commit=False) as sess:
        students = store.enrolled_students(sess, course.id)
        return {
            "request": request, "course": course, "students": students,
            "history": store.history(sess, course.id),
            "rubric": store.rubric_counts(sess, course.id),
            "purposes": PURPOSE_LABELS, "purpose_keys": PURPOSES,
            # Shown so the send control can render disabled rather than looking armed.
            "dry_run": settings.dry_run,
            **extra,
        }


@router.get("/courses/{course_id}/mensajes", response_class=HTMLResponse)
def page(request: Request, course_id: int):
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
    return templates.TemplateResponse("course_messages.html", _context(request, course))


@router.post("/courses/{course_id}/mensajes/run", response_class=HTMLResponse)
def run(request: Request, course_id: int,
        action: str = Form(...), purpose: str = Form("aviso"), body: str = Form(""),
        confirm: str = Form(""), only_me: str = Form(""), again: str = Form("")):
    """Start a messaging job.

    `action=send` without the typed group code does **not** silently downgrade to a dry run
    the way the Cronograma's Aplicar does. There, the harmless fallback is right: a dry run
    of a date change is useful on its own. Here it would teach the professor that the
    confirmation box is decorative, and the next time it is filled in the message goes out.
    So it refuses, loudly, and says what to type.
    """
    with Session(engine, expire_on_commit=False) as sess:
        course = my_course(request, sess, course_id)
        owner = current_professor(request, sess).email

    try:
        job_id = jobs.start(course_id, action, purpose=purpose, body=body,
                            only_me=bool(only_me), confirm=confirm, again=bool(again),
                            owner=owner)
    except MessagingRefused as exc:
        return templates.TemplateResponse("job_refused.html", {
            "request": request, "course": course, "reason": str(exc),
        })

    return templates.TemplateResponse("job_progress.html", {
        "request": request, "course": course, "job": jobs.get_job(job_id), "job_id": job_id,
    })


@router.get("/messaging-jobs/{job_id}", response_class=HTMLResponse)
def poll(request: Request, job_id: int):
    """Messaging jobs poll here rather than at `/jobs/{id}`, so a browser job from another
    feature can never be rendered with this feature's labels."""
    # 🔴 Scoped. A messaging job's result names the group, the recipient count and any
    # refusal reason; `/messaging-jobs/7` is a small integer away from a colleague's send.
    job = owned_job(request, jobs.get_job(job_id))
    course = None
    with Session(engine, expire_on_commit=False) as sess:
        cid = job.get("params", {}).get("course_id")
        if cid:
            course = my_course(request, sess, int(cid))
    return templates.TemplateResponse("message_progress.html", {
        "request": request, "course": course, "job": job, "job_id": job_id,
    })
