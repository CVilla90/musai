"""Regression tests for the 2026-08-07 publish failure.

The cockpit reported a publish as:

    16:43:25 ✓ Starting the browser…
    16:43:25 ✗ ERROR:

…and nothing else. Two independent defects produced that:

1. ``uvicorn --reload`` installs ``WindowsSelectorEventLoopPolicy`` on win32, and Playwright's
   sync api builds its loop from the global policy. A Windows SelectorEventLoop cannot spawn a
   subprocess, so ``chromium.launch()`` raised ``NotImplementedError``.
2. ``str(NotImplementedError())`` is ``""``, so the handler's ``f"ERROR: {e}"`` rendered the
   entire failure as the word ERROR and a colon.

The second is the one that cost the time, so it is the one pinned hardest here: a job that
fails must ALWAYS leave a non-empty, searchable message behind.
"""

import asyncio
import sys
from datetime import date

import pytest
from sqlmodel import Session, select

from musai.automation._log import describe_exception
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.coursebuild import jobs
from musai.models import Course, JobRequest, Semester


# ── describe_exception ──────────────────────────────────────────────────────────────────

def test_describe_exception_is_never_empty_for_a_bare_builtin():
    """The exact exception that produced 'ERROR: '."""
    assert describe_exception(NotImplementedError()) == "NotImplementedError"
    assert str(NotImplementedError()) == ""  # the trap being guarded against


@pytest.mark.parametrize("exc", [
    NotImplementedError(),
    RuntimeError(),
    KeyError(),
    Exception(),
    ValueError("   "),          # whitespace-only is just as invisible as empty
])
def test_describe_exception_never_returns_blank(exc):
    assert describe_exception(exc).strip()


def test_describe_exception_keeps_a_real_message_and_names_the_type():
    msg = describe_exception(RuntimeError("Course tile not found for idc=9023."))
    assert "Course tile not found for idc=9023." in msg
    assert "RuntimeError" in msg


# ── the event-loop policy guard ─────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="the policy trap is win32-only")
def test_guard_restores_a_subprocess_capable_policy():
    """Simulate `uvicorn --reload`, then assert the guard undoes it."""
    original = asyncio.get_event_loop_policy()
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        changed = ensure_subprocess_capable_loop()

        assert changed is True
        assert isinstance(asyncio.get_event_loop_policy(),
                          asyncio.WindowsProactorEventLoopPolicy)
        # Idempotent: a second call has nothing left to do.
        assert ensure_subprocess_capable_loop() is False
    finally:
        asyncio.set_event_loop_policy(original)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX loops all spawn subprocesses")
def test_guard_is_a_no_op_off_windows():
    assert ensure_subprocess_capable_loop() is False


# ── the job error path ──────────────────────────────────────────────────────────────────

@pytest.fixture
def course(session: Session, monkeypatch):
    """A minimal course, with jobs.py pointed at the in-memory test DB."""
    sem = Semester(name="test-sem", is_active=True,
                   starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18))
    session.add(sem)
    session.commit()
    session.refresh(sem)
    c = Course(group_code="1-LED-A", subject="INGLES I", level=1, semester_id=sem.id,
               moodle_course_id="9023")
    session.add(c)
    session.commit()
    session.refresh(c)
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    return c


def _run_publish_raising(monkeypatch, course, exc: BaseException) -> dict:
    """Drive jobs._run with a publish that raises `exc`, and return the stored job."""
    import musai.coursebuild.publish as publish_mod

    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(publish_mod, "publish_for_course", boom)
    job_id = jobs.create_job(course.id, "<p>x</p>", 0, True)
    jobs._run(job_id, course.id, "<p>x</p>", 0, True)
    return jobs.get_job(job_id)


def test_failed_job_never_records_a_blank_error(monkeypatch, course):
    """THE regression: the failure that rendered as 'ERROR: ' with nothing after it."""
    job = _run_publish_raising(monkeypatch, course, NotImplementedError())

    assert job["status"] == "failed"
    assert job["result"]["error"].strip(), "a failed job must leave a message behind"
    assert "NotImplementedError" in job["result"]["error"]

    last = job["result"]["steps"][-1]["msg"]
    assert last.strip() != "ERROR:"
    assert "NotImplementedError" in last


def test_failed_job_keeps_the_traceback_for_diagnosis(monkeypatch, course):
    """The message is for the professor; the traceback is for whoever debugs it next."""
    job = _run_publish_raising(monkeypatch, course, NotImplementedError())
    assert "Traceback" in job["result"]["traceback"]


def test_failed_job_preserves_a_useful_message(monkeypatch, course):
    job = _run_publish_raising(monkeypatch, course, RuntimeError("Moodle said no"))
    assert "Moodle said no" in job["result"]["error"]


def test_missing_course_fails_loudly(monkeypatch, course):
    job_id = jobs.create_job(course.id, "<p>x</p>", 0, True)
    jobs._run(job_id, 99999, "<p>x</p>", 0, True)
    job = jobs.get_job(job_id)
    assert job["status"] == "failed"
    assert "99999" in job["result"]["error"]
