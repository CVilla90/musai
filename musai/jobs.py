"""Background browser jobs, owned by the professor who started them.

**This module is the honest answer to "nothing in this app should take more than a minute."**
It cannot make Moodle faster — a restore is a queued PHP job on UACH's server and takes what it
takes (COURSE_EDITING §7: ~15 minutes for a 50 MB archive). What it can do is make sure *the
professor* never waits on it: the POST returns in well under a second with a job id, the browser
work happens on a worker thread, and the page renders the real steps as they arrive. She can
start all her restores, close the tab, and come back.

Generalised from `musai/coursebuild/jobs.py`, which proved the shape on publish jobs and is
hard-wired to one kind. The differences here are the two that multi-professor forces:

* 🔴 **Every job has an owner and `get()` refuses to answer about anyone else's.** Job ids are
  small integers; without the ownership check, `/jobs/41` is an enumerable window into a
  colleague's course names and error messages.
* **The worker resolves its own DB objects.** Passing an ORM instance across a thread boundary
  hands the worker a row bound to a session that is already closed.

⚠️ Threads, not processes, because the cockpit and the local runner are the same process on
The owner's machine today (PLAN §2). The `JobRequest` row is already the handoff point for the day
they split.
"""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime
from typing import Callable, Optional

from sqlmodel import Session, select

from musai.automation._log import describe_exception, logger as log
from musai.db import engine
from musai.models import JobRequest

MAP_COURSES = "map_courses"
COURSE_BACKUP = "course_backup"
COURSE_RESTORE = "course_restore"
CREDENTIAL_CHECK = "credential_check"

TERMINAL = ("done", "failed")

#: A job older than this with nothing new on it is presumed dead — the process was restarted
#: mid-run, which on a laptop that sleeps is the normal way a job ends. Reported as "unknown",
#: never as "failed": a restore whose worker vanished may well have completed on Moodle's side,
#: and `feedback_timeout_is_not_failure` was paid for by exactly that inference.
STALE_AFTER_S = 3600


def _now() -> datetime:
    return datetime.utcnow()


def create(kind: str, *, owner: str, params: Optional[dict] = None) -> int:
    with Session(engine) as sess:
        job = JobRequest(kind=kind, requested_by=owner, status="pending",
                         params_json=json.dumps(params or {}, default=str))
        sess.add(job)
        sess.commit()
        sess.refresh(job)
        return job.id


def update(job_id: int, **fields) -> None:
    """Append a step and/or merge result fields. Safe to call from the worker thread."""
    with Session(engine) as sess:
        job = sess.get(JobRequest, job_id)
        if not job:
            return
        result = json.loads(job.result_json or "{}")
        steps = result.get("steps", [])
        if "step" in fields:
            steps.append({"t": _now().strftime("%H:%M:%S"), "msg": fields.pop("step")})
        result["steps"] = steps
        result.update(fields.pop("result", {}))
        job.result_json = json.dumps(result, default=str)
        for k, v in fields.items():
            setattr(job, k, v)
        sess.add(job)
        sess.commit()


def _shape(job: JobRequest) -> dict:
    result = json.loads(job.result_json or "{}")
    running = job.status not in TERMINAL
    age = (_now() - (job.created_at or _now())).total_seconds()
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "owner": job.requested_by,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "result": result,
        "params": json.loads(job.params_json or "{}"),
        "running": running,
        # Distinguished from `failed` on purpose — see STALE_AFTER_S.
        "stale": running and age > STALE_AFTER_S,
        "age_s": int(age),
    }


def get(job_id: int, *, owner: str) -> Optional[dict]:
    """One job, **only if `owner` started it**. `None` covers both "no such job" and "not yours".

    Collapsing those two into one answer is deliberate: a distinguishable "not yours" confirms
    the job exists, and job ids are sequential.
    """
    with Session(engine) as sess:
        job = sess.get(JobRequest, job_id)
        if job is None or job.requested_by != owner:
            return None
        return _shape(job)


def recent(*, owner: str, kind: Optional[str] = None, limit: int = 10) -> list[dict]:
    with Session(engine) as sess:
        stmt = select(JobRequest).where(JobRequest.requested_by == owner)
        if kind:
            stmt = stmt.where(JobRequest.kind == kind)
        rows = sess.exec(stmt.order_by(JobRequest.id.desc()).limit(limit)).all()
        return [_shape(r) for r in rows]


def running_for(owner: str, kind: str, *, target: Optional[str] = None) -> Optional[dict]:
    """A job of this kind already in flight for this professor, if any.

    🔴 The guard against the double-click that starts two restores into the same course. The
    second one would wipe what the first had just written, halfway through writing it — and
    `restore.py`'s own post-restore count is unreliable enough that neither run would report it
    honestly. A stale job does not block: it is presumed dead, so a genuinely stuck row can
    never lock a professor out of their own course forever.
    """
    for job in recent(owner=owner, kind=kind, limit=25):
        if not job["running"] or job["stale"]:
            continue
        if target is None or str(job["params"].get("target")) == str(target):
            return job
    return None


def start(kind: str, *, owner: str, params: Optional[dict] = None,
          work: Callable[[int], dict]) -> int:
    """Create the row, run `work(job_id)` on a worker thread, return the id immediately.

    `work` receives the job id so it can report steps, and returns the result dict. Its `ok`
    key decides `done` vs `failed`; a `work` that returns no `ok` is treated as failed rather
    than as success, because "the function returned" is not evidence that Moodle did anything.
    """
    job_id = create(kind, owner=owner, params=params)

    def runner() -> None:
        update(job_id, status="running")
        try:
            result = work(job_id) or {}
            update(job_id,
                   status="done" if result.get("ok") else "failed",
                   finished_at=_now(),
                   result={k: v for k, v in result.items() if k != "steps"})
        except Exception as e:
            # This thread is the end of the line — nothing above it will ever print a
            # traceback, so it is logged here AND kept on the row. `describe_exception`
            # because a bare `{e}` once turned a NotImplementedError into the message "ERROR: ".
            message = describe_exception(e)
            tb = traceback.format_exc()
            log.error(f"{kind} job {job_id} failed: {message}\n{tb}")
            update(job_id, status="failed", finished_at=_now(), step=f"ERROR: {message}",
                   result={"ok": False, "error": message, "traceback": tb})

    threading.Thread(target=runner, daemon=True, name=f"{kind}-{job_id}").start()
    return job_id
