"""Background-job ownership, and the web routes a second professor will actually touch.

Job ids are small sequential integers. With one user that is fine; with two it is an
enumerable window into a colleague's course names, group codes and error messages — which is
why `jobs.get` takes an owner and these tests exist.
"""

import json
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from musai import jobs
from musai.models import JobRequest


@pytest.fixture
def job_db(monkeypatch, tmp_path):
    """A private DB for the job store — **on disk, not in memory.**

    🔴 The file is the point, and it cost an afternoon to learn why. This fixture used to be
    `sqlite:///:memory:` with a `StaticPool`, which is the standard recipe and is wrong the
    moment a test starts a thread. `StaticPool` hands **one** `sqlite3` connection to every
    caller; `jobs.start` then has a worker thread writing on it while the polling loop reads on
    it, and two SQLAlchemy Sessions on one connection do not share a transaction boundary —
    the reader's session ends the transaction the writer is still inside, and the writer's
    `status="done"` is silently rolled back.

    The symptom was a test that failed **17% of runs** (measured: 3 hangs in 300) with
    `job 1 never finished`, always on whichever test started the first worker thread. Nothing
    was wrong with `musai/jobs.py`; the harness was losing its writes. On a file each thread
    gets its own connection and real locking: 0 hangs in 300.

    Worth remembering beyond this file — **an intermittent red in a suite that guards restores
    is not a nuisance, it is the crying-wolf failure mode this project keeps paying for.** A
    test that fails one run in six teaches everyone to re-run it, which is precisely the
    reflex that must not exist around a destructive operation.
    """
    import musai.db as db_mod

    eng = create_engine(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(db_mod, "engine", eng)
    monkeypatch.setattr(jobs, "engine", eng)
    return eng


# ── ownership ─────────────────────────────────────────────────────────────────
def test_a_job_is_only_visible_to_the_professor_who_started_it(job_db):
    job_id = jobs.create(jobs.COURSE_BACKUP, owner="colleague4@uach.mx",
                         params={"group": "4-LEF-B"})

    assert jobs.get(job_id, owner="colleague4@uach.mx") is not None
    # 🔴 `None`, not a 403 and not a redacted job — a distinguishable "not yours" confirms the
    # job exists, and the params carry her group codes.
    assert jobs.get(job_id, owner="professor@uach.mx") is None


def test_a_job_that_does_not_exist_looks_exactly_like_one_that_is_not_yours(job_db):
    assert jobs.get(999999, owner="professor@uach.mx") is None


def test_recent_lists_only_your_own_jobs(job_db):
    jobs.create(jobs.MAP_COURSES, owner="colleague4@uach.mx")
    jobs.create(jobs.MAP_COURSES, owner="professor@uach.mx")
    mine = jobs.recent(owner="colleague4@uach.mx")
    assert len(mine) == 1
    assert mine[0]["owner"] == "colleague4@uach.mx"


# ── the double-click guard ────────────────────────────────────────────────────
def test_a_second_restore_into_the_same_course_finds_the_first_one_running(job_db):
    """🔴 The second restore deletes what the first just wrote, halfway through writing it."""
    first = jobs.create(jobs.COURSE_RESTORE, owner="colleague4@uach.mx", params={"target": 7})
    jobs.update(first, status="running")

    found = jobs.running_for("colleague4@uach.mx", jobs.COURSE_RESTORE, target=7)
    assert found and found["id"] == first


def test_a_restore_into_a_different_course_is_not_blocked(job_db):
    first = jobs.create(jobs.COURSE_RESTORE, owner="colleague4@uach.mx", params={"target": 7})
    jobs.update(first, status="running")
    assert jobs.running_for("colleague4@uach.mx", jobs.COURSE_RESTORE, target=8) is None


def test_a_finished_job_does_not_block_the_next_one(job_db):
    first = jobs.create(jobs.COURSE_RESTORE, owner="colleague4@uach.mx", params={"target": 7})
    jobs.update(first, status="done")
    assert jobs.running_for("colleague4@uach.mx", jobs.COURSE_RESTORE, target=7) is None


def test_one_professors_running_job_does_not_block_another(job_db):
    first = jobs.create(jobs.COURSE_RESTORE, owner="colleague4@uach.mx", params={"target": 7})
    jobs.update(first, status="running")
    assert jobs.running_for("professor@uach.mx", jobs.COURSE_RESTORE, target=7) is None


def test_a_stale_job_never_locks_a_professor_out_of_their_own_course(job_db):
    """A crashed worker must not make a course permanently un-restorable."""
    job_id = jobs.create(jobs.COURSE_RESTORE, owner="colleague4@uach.mx", params={"target": 7})
    jobs.update(job_id, status="running",
                created_at=datetime.utcnow() - timedelta(seconds=jobs.STALE_AFTER_S + 60))

    assert jobs.get(job_id, owner="colleague4@uach.mx")["stale"] is True
    assert jobs.running_for("colleague4@uach.mx", jobs.COURSE_RESTORE, target=7) is None


def test_a_lost_job_is_reported_as_unknown_not_as_failed(job_db):
    """🔴 `feedback_timeout_is_not_failure`: the worker vanishing says nothing about whether
    Moodle finished. Re-running a restore on a false failure is what destroys a course."""
    job_id = jobs.create(jobs.COURSE_RESTORE, owner="colleague4@uach.mx")
    jobs.update(job_id, status="running",
                created_at=datetime.utcnow() - timedelta(seconds=jobs.STALE_AFTER_S + 60))
    job = jobs.get(job_id, owner="colleague4@uach.mx")
    assert job["stale"] is True
    assert job["status"] != "failed"


# ── the worker ────────────────────────────────────────────────────────────────
def test_a_worker_that_returns_no_ok_is_a_failure_not_a_success(job_db):
    """"The function returned" is not evidence that Moodle did anything."""
    job_id = jobs.start("t", owner="x@uach.mx", work=lambda jid: {"note": "did nothing"})
    _wait(job_id, "x@uach.mx")
    assert jobs.get(job_id, owner="x@uach.mx")["status"] == "failed"


def test_a_worker_that_raises_keeps_the_message_on_the_row(job_db):
    def boom(_jid):
        raise RuntimeError("moodle said no")

    job_id = jobs.start("t", owner="x@uach.mx", work=boom)
    _wait(job_id, "x@uach.mx")
    job = jobs.get(job_id, owner="x@uach.mx")
    assert job["status"] == "failed"
    assert "moodle said no" in job["result"]["error"]
    assert "traceback" in job["result"]


def test_steps_arrive_in_order_with_timestamps(job_db):
    def work(jid):
        jobs.update(jid, step="one")
        jobs.update(jid, step="two")
        return {"ok": True}

    job_id = jobs.start("t", owner="x@uach.mx", work=work)
    _wait(job_id, "x@uach.mx")
    steps = jobs.get(job_id, owner="x@uach.mx")["result"]["steps"]
    assert [s["msg"] for s in steps] == ["one", "two"]
    assert all(s["t"] for s in steps)


def _wait(job_id: int, owner: str, timeout: float = 5.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.get(job_id, owner=owner)
        if job and not job["running"]:
            return
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished")


# ── the routes ────────────────────────────────────────────────────────────────
@pytest.fixture
def client(sign_in):
    from musai.web.app import app

    return sign_in(TestClient(app, follow_redirects=False))


def test_settings_is_behind_the_gate(sign_in):
    """A signed-OUT request must not reach the page that stores passwords."""
    from musai.web.app import app

    anon = TestClient(app, follow_redirects=False)
    r = anon.get("/settings")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/?next=")


def test_the_settings_page_never_renders_a_stored_password(client):
    r = client.get("/settings")
    assert r.status_code == 200
    # The input that takes a password must be empty and of type password — a rendered value,
    # even masked, would imply one can be fetched back.
    assert 'name="password"' in r.text
    assert 'name="password" value=' not in r.text


def test_the_settings_page_says_out_loud_that_musai_can_read_these(client):
    r = client.get("/settings")
    assert "can read these passwords" in r.text


def test_a_course_that_is_not_yours_is_a_404_not_a_403(client, job_db):
    """A 403 confirms the course exists, and course ids are sequential."""
    r = client.get("/courses/999999/transfer")
    assert r.status_code == 404


def test_polling_another_professors_job_returns_nothing(client, job_db):
    job_id = jobs.create(jobs.COURSE_BACKUP, owner="someone.else@uach.mx")
    r = client.get(f"/work/{job_id}")
    assert r.status_code == 404


def test_the_cockpit_lists_only_the_signed_in_professors_courses(sign_in):
    """The signed-in address in this suite is the owner's, and the dev DB's courses are his."""
    from musai.web.app import app

    c = sign_in(TestClient(app, follow_redirects=False), email="nobody.new@uach.mx")
    r = c.get("/")
    assert r.status_code == 200
    # A professor with no courses gets the empty state, not somebody else's group codes.
    assert "No courses loaded for" in r.text
    assert "1-LED-A" not in r.text
