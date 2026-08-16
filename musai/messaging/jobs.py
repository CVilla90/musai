"""Background job for the Messaging Hub.

Same shape as `coursedates/jobs.py`: a browser run takes a minute, so it cannot sit inside an
HTTP request, and the page shows the REAL steps the browser reports rather than a canned
animation. What differs is the gate — `SEND` is the only action in MUSAI whose effect reaches
students, so the refusals live *before* the thread starts, where they can still be shown to
the person who pressed the button.
"""

import json
import threading
import traceback
from datetime import datetime

from sqlmodel import Session, select

from musai.automation._log import describe_exception, logger as log
from musai.automation.messaging import MessagingRefused
from musai.config import settings
from musai.db import engine
from musai.models import Course, JobRequest

KIND = "messaging"
DRYRUN, SEND = "dryrun", "send"


def _update(job_id: int, **fields) -> None:
    with Session(engine) as sess:
        job = sess.get(JobRequest, job_id)
        if not job:
            return
        result = json.loads(job.result_json or "{}")
        steps = result.get("steps", [])
        if "step" in fields:
            steps.append({"t": datetime.utcnow().strftime("%H:%M:%S"),
                          "msg": fields.pop("step")})
        result["steps"] = steps
        result.update(fields.pop("result", {}))
        job.result_json = json.dumps(result, default=str)
        for k, v in fields.items():
            setattr(job, k, v)
        sess.add(job)
        sess.commit()


def get_job(job_id: int) -> dict | None:
    with Session(engine) as sess:
        job = sess.get(JobRequest, job_id)
        if not job:
            return None
        return {"id": job.id, "status": job.status, "owner": job.requested_by,
                "created_at": job.created_at, "finished_at": job.finished_at,
                "result": json.loads(job.result_json or "{}"),
                "params": json.loads(job.params_json or "{}")}


def _run(job_id: int, course_id: int, action: str, purpose: str, body: str,
         only_me: bool) -> None:
    from musai.automation.messaging import send_message
    from musai.messaging import store

    _update(job_id, status="running", step="Preparando…")
    try:
        with Session(engine, expire_on_commit=False) as sess:
            course = sess.get(Course, course_id)
            if course is None:
                raise RuntimeError(f"Course {course_id} not found.")
            if not course.moodle_course_id:
                raise RuntimeError(f"{course.group_code} no tiene id de curso en Moodle.")
            students = store.enrolled_students(sess, course_id)
            matriculas = [s.matricula for s in students]
            idc, label = course.moodle_course_id, course.group_code

        # `only_me` deliberately bypasses the enrolment cross-check by passing no expected
        # roster: it is a test of the PATH, on a course whose enrolment is irrelevant to it.
        result = send_message(
            idc=idc, body=body,
            expected_matriculas=[] if only_me else matriculas,
            dry_run=(action != SEND), only_me=only_me, headless=True,
            group_label=label, on_step=lambda m: _update(job_id, step=m))

        with Session(engine, expire_on_commit=False) as sess:
            course = sess.get(Course, course_id)
            batch = store.record(sess, course=course, purpose=purpose, body=body,
                                 result=result)
            result["batch_id"] = batch.id

        result = {k: v for k, v in result.items() if k != "steps"}
        result["action"] = action
        _update(job_id, status="done" if result.get("ok") else "failed",
                finished_at=datetime.utcnow(), result=result)

    except MessagingRefused as e:
        # A rail said no. Recorded as a refusal, not a crash — the distinction matters when
        # somebody reads this back asking "did it go out?".
        log.warning(f"Messaging job {job_id} refused: {e}")
        _update(job_id, status="failed", finished_at=datetime.utcnow(),
                step=f"RECHAZADO: {e}",
                result={"ok": False, "refused": True, "error": str(e), "action": action})
    except Exception as e:
        message = describe_exception(e)
        tb = traceback.format_exc()
        log.error(f"Messaging job {job_id} failed: {message}\n{tb}")
        _update(job_id, status="failed", finished_at=datetime.utcnow(),
                step=f"ERROR: {message}",
                result={"ok": False, "error": message, "traceback": tb, "action": action})


def start(course_id: int, action: str, *, purpose: str, body: str,
          only_me: bool = False, confirm: str = "", again: bool = False,
          owner: str = "") -> int:
    """Validate every rail that can be checked without a browser, THEN start the thread.

    Refusing here rather than inside the job is deliberate: the person who pressed the button
    is still looking at the page, and a refusal they can read is worth more than one they
    have to go find in a job log.
    """
    from musai.messaging import store

    if action not in (DRYRUN, SEND):
        raise ValueError(f"Unknown action {action!r}.")
    if not (body or "").strip():
        raise MessagingRefused("El mensaje está vacío.")

    with Session(engine, expire_on_commit=False) as sess:
        course = sess.get(Course, course_id)
        if course is None:
            raise MessagingRefused("No encuentro el curso.")

        if action == SEND:
            # 🔴 RAIL 0 — the global DRY_RUN gate, and the reason it is rail ZERO.
            #
            # On 2026-08-08 this check did not exist, and a *test* of the confirmation rail
            # — an HTTP POST written to prove that a badly-typed group code is refused —
            # sent "Hola prueba" to three real students. Every other rail held: the sender
            # was excluded, seven non-enrolled participants were excluded, the count
            # cross-check passed. None of that matters, because the run should never have
            # been able to reach Moodle in the first place.
            #
            # CLAUDE.md rail 2 already said this: "every write to a live system defaults to
            # dry-run… never flip DRY_RUN=false without the owner's explicit instruction."
            # The Cronograma honours it through a checkbox on a form. Messaging honoured it
            # nowhere, so a caller that never sees the form — a test, a script, a stray
            # curl — had nothing standing in the way.
            #
            # With DRY_RUN=true a real send is now structurally impossible, whatever any
            # client posts. Typing the group code is the SECOND key, not the only one.
            if settings.dry_run:
                raise MessagingRefused(
                    "DRY_RUN está activo, así que MUSAI no puede enviar mensajes reales. "
                    "Es a propósito: enviar es lo único que no se puede deshacer. "
                    "Cambia DRY_RUN=false en .env (y reinicia uvicorn) sólo cuando de "
                    "verdad quieras que salga.")

            # RAIL 4: type-to-confirm the group code, the way Umbral's `administrar` works.
            # Not an "are you sure" — those are clicked through without reading.
            if (confirm or "").strip().upper() != (course.group_code or "").upper():
                raise MessagingRefused(
                    f"Para enviar de verdad hay que escribir el código del grupo "
                    f"({course.group_code}) tal cual.")
            # RAIL 7: the DB record is the only thing that can notice a repeat.
            if not again:
                dup = store.recent_duplicate(sess, course_id, body)
                if dup is not None:
                    raise MessagingRefused(
                        f"Ese mismo mensaje ya se envió a {course.group_code} el "
                        f"{dup.created_at:%d/%m %H:%M} ({dup.recipient_count} destinatarios). "
                        f"Marca «enviar otra vez» si de verdad quieres repetirlo.")

        job = JobRequest(kind=KIND, status="pending",
                         # See `coursebuild.jobs.create_job` — an unnamed owner is nobody.
                         requested_by=(owner or "").strip().lower(),
                         params_json=json.dumps(
                             {"course_id": course_id, "action": action, "purpose": purpose,
                              "only_me": only_me, "body_preview": body[:120]}))
        sess.add(job)
        sess.commit()
        sess.refresh(job)
        job_id = job.id

    threading.Thread(target=_run,
                     args=(job_id, course_id, action, purpose, body, only_me),
                     daemon=True, name=f"messaging-{job_id}").start()
    return job_id
