"""Background publish jobs, so a 60-second browser run isn't held open inside an HTTP request.

The first cut ran Playwright synchronously inside the POST handler. That works, but it is
the wrong shape: the request hangs for a minute, the browser may time it out, there is no
way to cancel, and — worst for UX — the page cannot show what is actually happening, only a
canned animation. `JobRequest` already existed in `models.py` for exactly this ("Phase 4:
cockpit → local runner job queue").

Flow: POST creates a JobRequest and returns immediately → a worker thread runs the browser
and appends each REAL step to the job row → the page polls and renders those steps.

Threads (not a separate process) because the local runner and the cockpit are the same
process on the owner's machine today. When the runner splits out per PLAN §2, the queue row is
already the handoff point — a runner elsewhere polls `status="pending"` instead.
"""

import json
import threading
import traceback
from datetime import datetime

from sqlmodel import Session, select

from musai.automation._log import describe_exception, logger as log
from musai.db import engine
from musai.models import Course, JobRequest

KIND = "coursebuild_publish"

# Two places a block can land, and they are genuinely different Moodle objects:
#   LABEL           — an activity inside a section. Right for content *in* a course.
#   SECTION_SUMMARY — the section's own description, i.e. the course's front page. Right for
#                     the Course Hub, which IS the landing page rather than a thing on it.
LABEL, SECTION_SUMMARY = "label", "section_summary"


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


def create_job(course_id: int, html: str, section: int, dry_run: bool,
               target: str = LABEL, overwrite_foreign: bool = False,
               owner: str = "") -> int:
    with Session(engine) as sess:
        job = JobRequest(
            kind=KIND,
            params_json=json.dumps({"course_id": course_id, "section": section,
                                    "dry_run": dry_run, "html_len": len(html),
                                    "target": target,
                                    "overwrite_foreign": overwrite_foreign}),
            status="pending",
            # 🔴 Explicitly overriding the model's `"carlos"` default. A job whose caller did
            # not name an owner belongs to nobody and `deps.owned_job` will refuse to render
            # it — which is the correct failure. The default would silently hand it to whoever
            # happens to be signed in the day someone types that string into the column.
            requested_by=(owner or "").strip().lower(),
        )
        sess.add(job)
        sess.commit()
        sess.refresh(job)
        return job.id


def get_job(job_id: int) -> dict | None:
    with Session(engine) as sess:
        job = sess.get(JobRequest, job_id)
        if not job:
            return None
        return {
            "id": job.id,
            "status": job.status,
            "owner": job.requested_by,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "result": json.loads(job.result_json or "{}"),
            "params": json.loads(job.params_json or "{}"),
        }


def _run(job_id: int, course_id: int, html: str, section: int, dry_run: bool,
         target: str = LABEL, overwrite_foreign: bool = False) -> None:
    from musai.coursebuild.publish import publish_for_course
    from musai.coursebuild.publish_section import publish_summary_for_course

    _update(job_id, status="running", step="Starting the browser…")
    try:
        with Session(engine, expire_on_commit=False) as sess:
            course = sess.get(Course, course_id)
        if course is None:
            raise RuntimeError(f"Course {course_id} not found.")

        if target == SECTION_SUMMARY:
            result = publish_summary_for_course(
                course, html, section=section, dry_run=dry_run, headless=True,
                overwrite_foreign=overwrite_foreign,
                on_step=lambda m: _update(job_id, step=m),
            )
        else:
            result = publish_for_course(
                course, html, section=section, dry_run=dry_run, headless=True,
                on_step=lambda m: _update(job_id, step=m),
            )
        _update(
            job_id,
            status="done" if result.get("ok") else "failed",
            finished_at=datetime.utcnow(),
            result={k: v for k, v in result.items() if k != "steps"},
        )
    except Exception as e:
        # This thread is the end of the line — nothing above it will ever print a traceback,
        # so it gets logged here and kept on the row. `describe_exception` because a bare
        # `{e}` once turned a NotImplementedError into the message "ERROR: ".
        message = describe_exception(e)
        tb = traceback.format_exc()
        log.error(f"Publish job {job_id} failed: {message}\n{tb}")
        _update(job_id, status="failed", finished_at=datetime.utcnow(),
                step=f"ERROR: {message}",
                result={"ok": False, "error": message, "traceback": tb})


def start_publish(course_id: int, html: str, section: int, dry_run: bool,
                  target: str = LABEL, overwrite_foreign: bool = False,
                  owner: str = "") -> int:
    """Create the job and kick off the worker. Returns the job id to poll."""
    job_id = create_job(course_id, html, section, dry_run, target, overwrite_foreign, owner)
    t = threading.Thread(
        target=_run,
        args=(job_id, course_id, html, section, dry_run, target, overwrite_foreign),
        daemon=True, name=f"publish-{job_id}",
    )
    t.start()
    return job_id


def recent(limit: int = 10) -> list[dict]:
    with Session(engine) as sess:
        rows = sess.exec(
            select(JobRequest).where(JobRequest.kind == KIND)
            .order_by(JobRequest.id.desc()).limit(limit)
        ).all()
        return [{"id": r.id, "status": r.status, "created_at": r.created_at} for r in rows]
