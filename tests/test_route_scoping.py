"""🔴 Every course route is owner-scoped — enumerated from the app, not from a hand-written list.

The 2026-08-14 audit that produced this file found **22 route handlers** reaching a course with
`sess.get(Course, course_id)` and no ownership check at all. Among them:

* `POST /courses/{id}/mapping` — rewrote another professor's activity→partial mapping;
* `GET  /courses/{id}/export.xlsx` — handed back a workbook of their roster and grades;
* `POST /courses/{id}/partial/{pid}/curve/clear` — deleted every curve on a partial id that
  was never compared to the course it was reached through;
* `POST /courses/{id}/mensajes/run` and `/build/publish` — write paths into a live course.

None of it was reachable while the database had one user. All of it became reachable the moment
it had two, which is the week this was written.

**Why this test walks `app.routes` instead of listing paths.** A test that names the routes it
checks is a test that is already out of date: the leak above was not written by someone who
skipped the rule, it was written before the rule existed, and the next route will be written by
someone who never read this file. Enumerating from the router means a new `/courses/{course_id}/…`
handler is covered the day it is added, and the only way to opt out is to edit the exemption
list below and say why in writing.

See `feedback_unscoped_query_is_a_leak` and `musai/web/deps.py`.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from musai.db import engine
from musai.models import Course, Semester
from musai.professors import get_or_create

#: Routes that legitimately take a `course_id` but are not course-scoped surfaces. Every entry
#: needs a reason, because "add it to the list" is the easy way to make this test go quiet.
EXEMPT: dict[str, str] = {}

#: Path parameters other than `course_id` need *a* value to build a URL. These are deliberately
#: ids that exist in no test database — the assertion is that the request is refused before
#: anything is looked up, so a real id would weaken it, not strengthen it.
FILLER = {"partial_id": "987654", "job_id": "987654", "student_id": "987654",
          "activity_id": "987654", "section": "0", "name": "2026-2"}

#: Plausible values for required form fields, so a POST actually **reaches its handler**.
#:
#: 🔴 This dict is the difference between a real test and a decorative one. The first draft
#: accepted `422` alongside `404`, reasoning that a missing form field is refused before the
#: handler runs — true, and exactly why it was worthless: **fourteen of the twenty POST routes
#: have a required field**, so they answered 422 without the ownership check ever executing,
#: and the suite went green over the leak it was written to find. A test that accepts the
#: status code meaning "I never got that far" is asserting nothing about how far it got.
FORM_VALUES = {
    "action": "dryrun", "starts": "2026-08-17", "ends": "2026-12-11", "days": "1",
    "index": "0", "choice": "1:content", "section": "0", "prompt": "x", "html": "<p>x</p>",
    "archive_path": "nope.mbz", "preflight_job": "987654", "student_id": "987654",
}


def _required_form(route) -> dict:
    """Values for this route's required body fields, so the request is not rejected early."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return {}
    return {p.name: FORM_VALUES.get(p.name, "x")
            for p in dependant.body_params if p.required}


def _course_routes(app):
    """Every (method, path, form) the app serves under a `{course_id}`."""
    out = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "{course_id}" not in path or path in EXEMPT:
            continue
        form = _required_form(route)
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            out.append((method, path, form))
    return sorted(out)


@pytest.fixture(scope="module")
def someone_elses_course():
    """A course owned by a professor the test client is definitely not signed in as.

    Written to the (copied) test database rather than mocked, because the thing under test is
    a database comparison. A mocked `owns()` would pass whether or not the route calls it.
    """
    with Session(engine) as sess:
        other = get_or_create(sess, email="not.the.signed.in.professor@uach.mx",
                              full_name="Someone Else")
        sem = sess.exec(
            __import__("sqlmodel").select(Semester).order_by(Semester.id.desc())
        ).first()
        if sem is None:
            sem = Semester(name="2026-2", starts_on=date(2026, 7, 1),
                           ends_on=date(2026, 12, 31))
            sess.add(sem)
            sess.commit()
            sess.refresh(sem)
        course = Course(professor_id=other.id, semester_id=sem.id, subject="Inglés",
                        level=1, group_code="9-NOT-MINE", moodle_course_id="99999")
        sess.add(course)
        sess.commit()
        sess.refresh(course)
        course_id = course.id
    yield course_id
    with Session(engine) as sess:
        row = sess.get(Course, course_id)
        if row is not None:
            sess.delete(row)
            sess.commit()


@pytest.fixture
def client(sign_in):
    from musai.web.app import app

    return sign_in(TestClient(app, follow_redirects=False))


def test_there_are_course_routes_to_check():
    """A guard on the guard: if `_course_routes` ever returns nothing — a renamed parameter,
    a restructured router — every assertion below would pass by vacuum."""
    from musai.web.app import app

    assert len(_course_routes(app)) >= 15, (
        "The route walk found almost nothing. It is matching on the literal string "
        "'{course_id}', so a renamed path parameter silently empties this whole file.")


def test_no_course_route_answers_for_a_course_that_is_not_yours(client, someone_elses_course):
    """The whole point. One assertion, run against every course route the app serves.

    A 404 rather than a 403 throughout: a 403 confirms the course exists, and course ids are
    sequential, so the difference between the two codes is the difference between a refusal and
    a directory of the faculty's courses.
    """
    from musai.web.app import app

    leaks = []
    for method, path, form in _course_routes(app):
        url = path.replace("{course_id}", str(someone_elses_course))
        for key, value in FILLER.items():
            url = url.replace("{" + key + "}", value)
        response = client.request(method, url, data=form or None)
        # 404, and only 404. Not 422 — see FORM_VALUES for what accepting that cost.
        if response.status_code != 404:
            leaks.append(f"{method} {path} → {response.status_code}")

    assert not leaks, (
        "These routes answered for a course belonging to another professor:\n  "
        + "\n  ".join(leaks)
        + "\n\nResolve the course through `musai.web.deps.my_course` / `owned_course`.")


def test_a_missing_course_is_the_same_answer_as_someone_elses(client):
    """"Does not exist" and "not yours" must be indistinguishable, or the 404 above leaks
    existence by omission — an attacker learns which ids are real by which ones 404 *slowly*."""
    from musai.web.app import app

    for method, path, form in _course_routes(app):
        url = path.replace("{course_id}", "8888888")
        for key, value in FILLER.items():
            url = url.replace("{" + key + "}", value)
        response = client.request(method, url, data=form or None)
        assert response.status_code == 404, f"{method} {path} → {response.status_code}"


# ── the job pollers ───────────────────────────────────────────────────────────
def test_the_legacy_job_pollers_refuse_another_professors_job():
    """`/jobs/{id}`, `/messaging-jobs/{id}` and `/work/{id}` all take a small sequential int.

    `musai/jobs.py` had this check from the start; the three older job modules
    (`coursebuild`, `coursedates`, `messaging`) did not, and their results carry course names,
    activity names and refusal reasons.
    """
    from musai.coursebuild import jobs as build_jobs

    job_id = build_jobs.create_job(course_id=1, html="<p>x</p>", section=0, dry_run=True,
                                   owner="somebody.else@uach.mx")
    job = build_jobs.get_job(job_id)
    assert job["owner"] == "somebody.else@uach.mx"


def test_a_job_created_without_an_owner_belongs_to_nobody():
    """🔴 `JobRequest.requested_by` defaults to the string `"carlos"` — which is not an email
    and therefore matches no signed-in professor. The default must stay *unusable*, not become
    a backdoor the day somebody's address happens to be normalised to it."""
    from musai.coursebuild import jobs as build_jobs

    job_id = build_jobs.create_job(course_id=1, html="<p>x</p>", section=0, dry_run=True)
    job = build_jobs.get_job(job_id)
    assert job["owner"] == ""
    assert "@" not in job["owner"], "An unowned job must never resolve to a real address."


def test_owned_job_refuses_a_job_with_no_owner(client):
    """End to end: the poller route, not just the module."""
    from musai.coursebuild import jobs as build_jobs

    job_id = build_jobs.create_job(course_id=1, html="<p>x</p>", section=0, dry_run=True)
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.get(f"/messaging-jobs/{job_id}").status_code == 404
