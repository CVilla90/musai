"""Map courses from Moodle, back a course up, restore an archive into it.

The three things a professor does on their first morning with MUSAI, in the order they do them.

⚠️ **Job progress lives at `/work/{id}`, not `/jobs/{id}`.** The latter is the publish router's
(`routes_build.py:120`) and is scoped to a different job kind; two pollers on one path would
each render the other's jobs as an empty box forever.

🔴 **Every route resolves its course through `deps.owned_course`.** With two professors sharing
one database, `sess.get(Course, id)` is a course-id enumeration away from acting on somebody
else's group — and the action on the other end of these routes deletes course contents.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from musai import checklists, jobs, mapping, transfer
from musai.automation.credentials import CredentialsMissing
from musai.config import settings
from musai.db import engine
from musai.models import Course, Professor
from musai.professors import courses_owned_by
from musai.semesters import ensure_current_semester, resolve_semester, semester_label
from musai.web.deps import current_professor, owned_course

router = APIRouter(tags=["transfer"])


def _templates():
    from musai.web.app import templates

    return templates


def _work_context(request: Request, job: dict, *, job_id: int | None, kind: str = "") -> dict:
    """The one place `work_progress.html` is given its context.

    Both entry points (starting a job, and polling it) go through here so the checklist, the
    duration and the poll-stop condition cannot be computed one way on the first render and a
    different way on the second — which is exactly how a card starts flickering between two
    states halfway through a fifteen-minute restore.

    🔴 …and that guarantee had a hole, because `kind` arrived as an ARGUMENT. `_job_fragment`
    passed `restore_check`; `/work/{id}` passed nothing and fell through to the stored
    `course_restore:check`. So the first render and every later one disagreed, and the restore's
    confirm button — gated on `kind == 'restore_check'` — existed only until the first poll.
    The kind is now DERIVED from the job through `checklists.display_kind`, and the argument is a
    fallback for the one case with no job at all (an error fragment). A value the two entry points
    can each supply differently is not single-sourced just because it is read in one function.
    """
    stored = (job or {}).get("kind", "")
    kind = checklists.display_kind(stored) if stored else kind
    running = bool(job and job.get("running") and not job.get("stale"))
    return {
        "request": request,
        "job": job,
        "kind": kind,
        # 🔴 Stop polling the moment the job is terminal. A poller with no stop condition
        # hammers the server forever on a page nobody closed.
        "job_id": job_id if running else None,
        "checklist": checklists.progress(kind, (job.get("result") or {}).get("steps"),
                                         running=running),
        "duration": checklists.duration(kind),
    }


def _job_fragment(request: Request, job_id: int | None, owner: str, *,
                  error: str = "", kind: str = ""):
    job = jobs.get(job_id, owner=owner) if job_id else {
        "status": "failed", "running": False, "result": {"error": error, "steps": []}}
    return _templates().TemplateResponse(
        "work_progress.html", _work_context(request, job, job_id=job_id, kind=kind))


@router.get("/work/{job_id}", response_class=HTMLResponse)
def work_progress(request: Request, job_id: int):
    """Poll one job. Returns 404 for a job that is not yours — see `jobs.get`."""
    with Session(engine) as sess:
        prof = current_professor(request, sess)
    job = jobs.get(job_id, owner=prof.email)
    if job is None:
        return HTMLResponse(
            '<div class="card p-5 text-sm text-muted">No such job.</div>', status_code=404)
    return _templates().TemplateResponse(
        "work_progress.html", _work_context(request, job, job_id=job_id))


# ── mapping ───────────────────────────────────────────────────────────────────
@router.post("/courses/map", response_class=HTMLResponse)
def map_courses(request: Request, semester: str | None = Form(None)):
    """Read this professor's live dashboard and create their `Course` rows for the semester."""
    with Session(engine) as sess:
        prof = current_professor(request, sess)
        sem = resolve_semester(sess, semester) or ensure_current_semester(sess)
        prof_id, email, sem_id, sem_name = prof.id, prof.email, sem.id, sem.name

    already = jobs.running_for(email, jobs.MAP_COURSES)
    if already:
        # Not an error — just show the run that is already going, rather than starting a second
        # browser as the same user. Two concurrent Playwright sessions on one Moodle account is
        # the bug that double-sent 35 students a message (SUSANA_WHATSAPP §2).
        return _job_fragment(request, already["id"], email, kind=jobs.MAP_COURSES)

    job_id = jobs.start(
        jobs.MAP_COURSES, owner=email,
        params={"semester": sem_name, "target": sem_id},
        work=lambda jid: _map_work(jid, prof_id, sem_id, sem_name),
    )
    return _job_fragment(request, job_id, email, kind=jobs.MAP_COURSES)


def _map_work(job_id: int, professor_id: int, semester_id: int, semester_name: str) -> dict:
    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine) as sess:
        prof = sess.get(Professor, professor_id)

    try:
        tiles = mapping.read_tiles(prof, headless=True, on_step=step)
    except CredentialsMissing as e:
        # No stored Moodle password. An answer, not a crash — and the message already names
        # where to fix it.
        return {"ok": False, "error": str(e), "refused": True}

    if not tiles:
        # An empty dashboard is a real state (a professor between semesters), not a crash — but
        # it is also what a silently-failed login looks like, so it is reported as neither
        # success nor failure but as a fact with the account named.
        return {"ok": False, "created": 0,
                "error": "Signed in, but the campusvirtual dashboard listed no courses. If you "
                         "do teach this semester, the portal may not have them yet."}

    with Session(engine) as sess:
        existing = courses_owned_by(sess, professor_id, semester_id=semester_id)
        plan = mapping.plan_mapping(tiles, existing)
        step(f"{len(plan.new)} new · {len(plan.updated)} updated · "
             f"{len(plan.unchanged)} unchanged")
        counts = mapping.apply_mapping(sess, plan, professor_id=professor_id,
                                       semester_id=semester_id)

    with Session(engine) as sess:
        from musai.professors import mark_used

        mark_used(sess, professor_id, "moodle", ok=True)

    step(f"{semester_name}: {counts['created']} course(s) added")
    if plan.vanished:
        # Reported, never acted on — deleting a Course takes its grades with it.
        step(f"⚠ {len(plan.vanished)} previously mapped course(s) are not on the dashboard "
             f"today; they were kept.")
    return {"ok": True, **counts, "semester": semester_name,
            "groups": [t.group_code for t in plan.new]}


# ── the transfer page ─────────────────────────────────────────────────────────
@router.get("/courses/{course_id}/transfer", response_class=HTMLResponse)
def transfer_page(request: Request, course_id: int, notice: str = "", error: str = ""):
    with Session(engine) as sess:
        prof, course = owned_course(request, sess, course_id)
        email = prof.email
        archive = transfer.last_backup_for(prof, course)
        info = transfer.describe_archive(archive) if archive else None
    return _templates().TemplateResponse(
        "course_transfer.html",
        {
            "request": request,
            "dry_run": settings.dry_run,
            "course": course,
            "professor": prof,
            "last_backup": info,
            "last_backup_path": str(archive) if archive else "",
            "notice": notice,
            "error": error,
            "recent": jobs.recent(owner=email, limit=6),
            "max_mb": transfer.MAX_UPLOAD_BYTES // 1_048_576,
        },
    )


# ── backup ────────────────────────────────────────────────────────────────────
@router.post("/courses/{course_id}/backup", response_class=HTMLResponse)
def start_backup(request: Request, course_id: int):
    with Session(engine) as sess:
        prof, course = owned_course(request, sess, course_id)
        prof_id, email, cid = prof.id, prof.email, course.id

    already = jobs.running_for(email, jobs.COURSE_BACKUP, target=cid)
    if already:
        return _job_fragment(request, already["id"], email, kind=jobs.COURSE_BACKUP)

    job_id = jobs.start(
        jobs.COURSE_BACKUP, owner=email,
        params={"target": cid, "group": course.group_code},
        work=lambda jid: _backup_work(jid, prof_id, cid),
    )
    return _job_fragment(request, job_id, email, kind=jobs.COURSE_BACKUP)


def _backup_work(job_id: int, professor_id: int, course_id: int) -> dict:
    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine) as sess:
        prof = sess.get(Professor, professor_id)
        course = sess.get(Course, course_id)

    try:
        result = transfer.run_backup(prof, course, on_step=step, headless=True)
    except transfer.TransferRefused as e:
        # A refusal is an answer, not a crash. Letting it reach the generic handler in
        # `jobs.start` renders it as "ERROR: TransferRefused: …" — the class name is noise to
        # a professor and the framing tells her something broke when in fact MUSAI declined to
        # act and nothing happened.
        return {"ok": False, "error": str(e), "refused": True}

    if result.get("file"):
        result["download"] = f"/courses/{course_id}/backup/download"
    return result


@router.get("/courses/{course_id}/backup/download")
def download_backup(request: Request, course_id: int):
    """Hand the professor the `.mbz` on disk. The file is theirs — it came out of their course."""
    from fastapi.responses import FileResponse

    with Session(engine) as sess:
        prof, course = owned_course(request, sess, course_id)
        path = transfer.last_backup_for(prof, course)
    if path is None or not path.is_file():
        return RedirectResponse(
            url=f"/courses/{course_id}/transfer?error={quote('No backup on disk for this course yet.')}",
            status_code=303)
    return FileResponse(str(path), filename=path.name,
                        media_type="application/octet-stream")


# ── restore ───────────────────────────────────────────────────────────────────
@router.post("/courses/{course_id}/restore/check", response_class=HTMLResponse)
async def restore_check(request: Request, course_id: int, archive: UploadFile = File(None),
                        use_last: str = Form("")):
    """Upload the archive and run the read-only pre-flight. Nothing is written to Moodle.

    The upload happens here rather than at restore time on purpose: reading the archive is what
    makes the pre-flight able to say *"this is INGLES IV, 106 activities, no user data"*, and a
    file that turns out not to be a backup should cost a second, not ten minutes.
    """
    with Session(engine) as sess:
        prof, course = owned_course(request, sess, course_id)
        prof_id, email, cid = prof.id, prof.email, course.id

        try:
            if use_last:
                path = transfer.last_backup_for(prof, course)
                if path is None:
                    raise transfer.TransferRefused("There is no earlier archive on disk.")
            else:
                if archive is None or not archive.filename:
                    raise transfer.TransferRefused("Choose a `.mbz` backup file first.")
                path = transfer.save_upload(prof, archive.filename, await archive.read())
        except transfer.TransferRefused as e:
            return _job_fragment(request, None, email, error=str(e))

    job_id = jobs.start(
        jobs.COURSE_RESTORE + ":check", owner=email,
        params={"target": cid, "archive": str(path), "group": course.group_code},
        work=lambda jid: _check_work(jid, prof_id, cid, str(path)),
    )
    return _job_fragment(request, job_id, email, kind="restore_check")


def _check_work(job_id: int, professor_id: int, course_id: int, path: str) -> dict:
    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine) as sess:
        prof = sess.get(Professor, professor_id)
        course = sess.get(Course, course_id)

    info = transfer.describe_archive(path)
    step(f"Archive: {info['fullname']} · {info['activities']} activities · "
         f"{info['mb']} MB · users={'YES' if info['includes_users'] else 'no'}")

    pf = transfer.preflight(prof, course, path, headless=True, on_step=step)
    if pf.ok:
        step(f"Target reads as {pf.target_name!r} and currently holds "
             f"{pf.target_activities} activities across {pf.target_sections} sections")
    else:
        step(f"REFUSED: {pf.refusal}")
    return {
        "ok": pf.ok,
        "preflight": pf.as_dict(),
        "archive": info,
        "archive_path": path,
        "course_id": course_id,
        "confirm_url": f"/courses/{course_id}/restore" if pf.ok else "",
        "error": "" if pf.ok else pf.refusal,
    }


@router.post("/courses/{course_id}/restore", response_class=HTMLResponse)
def start_restore(request: Request, course_id: int,
                  archive_path: str = Form(...), preflight_job: int = Form(...),
                  force: str = Form("")):
    """🔴 The live one. Deletes the course's contents, then restores the archive into it."""
    with Session(engine) as sess:
        prof, course = owned_course(request, sess, course_id)
        prof_id, email, cid = prof.id, prof.email, course.id

    already = jobs.running_for(email, jobs.COURSE_RESTORE, target=cid)
    if already:
        # 🔴 The double-click guard. A second restore into a course the first is still writing
        # deletes what the first just wrote, and `restore.py`'s own post-restore count is
        # unreliable enough that neither run would report it honestly.
        return _job_fragment(request, already["id"], email, kind=jobs.COURSE_RESTORE)

    job_id = jobs.start(
        jobs.COURSE_RESTORE, owner=email,
        params={"target": cid, "group": course.group_code, "archive": archive_path},
        work=lambda jid: _restore_work(jid, prof_id, cid, archive_path, preflight_job,
                                       bool(force)),
    )
    return _job_fragment(request, job_id, email, kind=jobs.COURSE_RESTORE)


def _restore_work(job_id: int, professor_id: int, course_id: int, path: str,
                  preflight_job: int, force: bool) -> dict:
    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine) as sess:
        prof = sess.get(Professor, professor_id)
        course = sess.get(Course, course_id)

    try:
        result = transfer.run_restore(prof, course, path, preflight_job_id=preflight_job,
                                      on_step=step, headless=True, force=force)
    except transfer.TransferRefused as e:
        # A refusal is not a crash: nothing was uploaded and the course is untouched. Reported
        # as a failed job so the row and the audit trail both carry it.
        step(f"REFUSED: {e}")
        return {"ok": False, "error": str(e), "refused": True}

    result["course_url"] = result.get("course_url") or (
        f"https://{course.moodle_server or 'virtual3'}.uach.mx/course/view.php"
        f"?id={course.moodle_course_id}")
    return result


@router.get("/semester-label/{name}")
def semester_label_json(name: str):
    """Tiny helper the templates use to print `Ago–Dic 2026` next to `2026-2`."""
    return {"name": name, "label": semester_label(name)}
