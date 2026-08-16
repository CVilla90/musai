"""One place that answers "who is acting, and may they touch this course?".

`auth.current_user()` returns the signed-in *session* — an email and a display name. Every
route that reads or writes data needs the *row*, because `Course.professor_id` is what scopes
the cockpit, and it needs it resolved the same way every time.

🔴 **`owned_course()` is the only way a route should reach a `Course`.** `sess.get(Course, id)`
is one keystroke shorter and skips the ownership check, which with two professors in one
database is the difference between a cockpit and a leak. It answers `404` for a course that
belongs to someone else — deliberately not `403`, which would confirm the course exists and
turn sequential ids into a directory of the faculty's courses.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlmodel import Session

from musai.models import Course, Professor
from musai.web import auth as auth_mod


def current_professor(request: Request, sess: Session) -> Professor:
    """The `Professor` row for the signed-in session, created on first sign-in.

    Get-or-create rather than create-only-at-callback, so a session issued before this table
    existed still resolves instead of 500ing — and so the row cannot go missing while a valid
    cookie survives.
    """
    from musai.professors import get_or_create

    user = auth_mod.current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to use MUSAI.")
    return get_or_create(
        sess,
        email=user["email"],
        full_name=user.get("name") or "",
        picture=user.get("picture") or None,
    )


def owned_course(request: Request, sess: Session, course_id: int) -> tuple[Professor, Course]:
    """`(professor, course)` — or a 404 if the course is not theirs. Never returns someone
    else's course, and never distinguishes "does not exist" from "not yours"."""
    from musai.professors import owns

    prof = current_professor(request, sess)
    course = owns(sess, prof.id, course_id)
    if course is None:
        raise HTTPException(404, "No such course.")
    return prof, course


def my_course(request: Request, sess: Session, course_id: int) -> Course:
    """`owned_course` for the routes that only need the course.

    Exists so that scoping a route costs **fewer** characters than not scoping it. The six
    route modules audited on 2026-08-14 had all reached for `sess.get(Course, course_id)`
    because it was the shortest thing that worked; a rail that is more effort than the unsafe
    path loses that race every time.
    """
    return owned_course(request, sess, course_id)[1]


def owned_job(request: Request, job: dict | None, *, sess: Session | None = None) -> dict:
    """A legacy job dict (`coursebuild` / `coursedates` / `messaging`), only if it is theirs.

    🔴 Those three modules predate `musai/jobs.py` and its ownership check, and their pollers
    are open routes taking a small sequential integer. The job's `result` carries course names,
    activity names and error messages, so an unscoped `/jobs/41` is a readable window into a
    colleague's course. `requested_by` on a job created before this check reads `"carlos"`,
    which matches no email and therefore fails closed.
    """
    if job is None:
        raise HTTPException(404, "No such job.")
    owner = (job.get("owner") or "").strip().lower()
    user = auth_mod.current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to use MUSAI.")
    if owner != (user.get("email") or "").strip().lower():
        raise HTTPException(404, "No such job.")
    return job
