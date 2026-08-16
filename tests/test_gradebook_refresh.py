"""Refreshing a course's gradebook from the cockpit — and the number that lied for two months.

🔴 **Why this file exists (2026-08-14).** the owner opened 1-LED-A and saw *"Students · 10 · enrolled
in MUSAI"* while the live course held 30-something. Nothing was broken in the sense of a crash:
`Enrollment` rows are created in exactly ONE place — `grading/ingest.py`, from a gradebook export
file — and the participants page has never been read into the database at all. The last export for
that course predated the 2026-2 cohort.

Three separate defects sat behind one wrong number, and this file pins all three:

1. **There was no web route.** `ingest_gradebook` was reachable only from the CLI, so a professor
   looking at the wrong number had nowhere to press.
2. 🔴 **`export_gradebook_ods` read `settings.uach_*` directly** — the same defect `backup_course`
   had before English IV. Wiring a button to it would have downloaded *every* professor's gradebook
   while signed in as the owner.
3. **The count was shown as a fact with no date.** A stale cache is fine; a stale cache that looks
   current is not. `musai/automation/messaging.py:211` had already written this exact course up as
   the worked example, which is the part worth noticing — the hazard was documented and the screen
   still showed a bare number.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

# ⚠️ The app package is imported first, deliberately. `musai.web.routes_course` is imported by
# `musai.web.app` while `app` is still being built, so a test that reaches for the router module
# FIRST wins the race and gets a partially initialised module — an `AttributeError` about
# `router` that reads like a bug in the router and is really an import order in the test.
import musai.web.app  # noqa: F401  (import for its side effect: finish wiring the app)
from musai.automation.moodle_export import export_gradebook_ods
from musai.models import Course, Professor


# ── 2. the identity hole ──────────────────────────────────────────────────────
def test_the_gradebook_export_can_act_for_a_named_professor():
    """It must accept BOTH roads and thread one of them to the login.

    Source-level, for the same reason as `test_write_path_as_user.py`: this function drives a real
    browser, so observing one keyword argument behaviourally would mean faking all of Playwright.
    The thing worth pinning is the wiring.
    """
    sig = inspect.signature(export_gradebook_ods)
    assert "identity" in sig.parameters, (
        "export_gradebook_ods lost its `identity` parameter — the cockpit road. Without it the "
        "web button signs in as whatever .env holds, which is not the professor pressing it.")
    assert "as_user" in sig.parameters, "the CLI's delegate road is gone."

    src = inspect.getsource(export_gradebook_ods)
    assert "identity.username, identity.password" in src, (
        "the resolved identity no longer reaches the credentials the login uses. A parameter that "
        "is accepted and ignored is WORSE than one that is missing: the run proceeds as the owner "
        "and reports success.")
    assert "settings.uach_username" not in src, (
        "export_gradebook_ods reads settings.uach_* again — that is the bug this closed. It makes "
        "every professor's refresh run as whoever .env names.")


def test_passing_both_identity_and_as_user_is_refused():
    """🔴 They name different accounts and there is no safe way to pick one — so it must not pick.

    Checked before a browser exists, so the refusal costs nothing.
    """
    with pytest.raises(RuntimeError, match="never both"):
        export_gradebook_ods("9023", identity=object(), as_user="colleague1")


def test_the_refresh_route_resolves_the_signed_in_professors_own_credential():
    """🔴 `identity=`, never `as_user=identity.username`.

    The username road re-resolves the password from `MOODLE_PWD_<USER>` in `.env`, which is the
    *delegate* mechanism — it refuses a professor whose password is in the vault, and for the owner
    himself it would succeed for the wrong reason and hide the bug.
    """
    from musai.web.routes_course import _gradebook_work

    src = inspect.getsource(_gradebook_work)
    assert "resolve_for_professor(prof, system=\"moodle\")" in src
    assert "identity=identity" in src, "the resolved identity does not reach the export call."

    # ⚠️ Comments are stripped first. The first version of this assertion matched the *comment*
    # explaining why `as_user` must not be used — a test that cannot tell a live call from the note
    # warning against it fails on its own documentation. Same shape as the landing-page media
    # query guard in test_contrast.py.
    code = "\n".join(line for line in src.splitlines()
                     if not line.strip().startswith("#"))
    assert "as_user=" not in code, (
        "the refresh job passes as_user — that is the .env delegate road, not this professor's "
        "own vault credential.")


# ── 3. the number, and its date ───────────────────────────────────────────────
def test_ingesting_a_gradebook_stamps_when_it_happened(session, tmp_path):
    """The timestamp is what turns a bare count into a dated one. Set inside `ingest_gradebook`
    rather than in a caller, because there are two roads in and a third will be added."""
    from musai.grading.ingest import ingest_gradebook

    course = Course(semester_id=1, subject="Inglés I", level=1, group_code="T-STAMP",
                    moodle_course_id="90231")
    session.add(course)
    session.commit()
    assert course.gradebook_ingested_at is None, "a course starts with no gradebook"

    # A minimal real export: the importer needs the header row and one student row.
    ods = _tiny_gradebook(tmp_path)
    before = datetime.utcnow() - timedelta(seconds=1)
    ingest_gradebook(session, course, ods)
    session.commit()

    assert course.gradebook_ingested_at is not None, (
        "ingest ran but stamped nothing — the Overview tile is back to showing an undated number.")
    assert course.gradebook_ingested_at >= before


def test_a_course_that_never_had_a_gradebook_says_so_rather_than_showing_zero_days():
    """🔴 `None` is *"never imported"*, not `0 days old`.

    A course that has never had a gradebook and one imported this morning are opposite states.
    Collapsing them is precisely how the original bug reads as fine.
    """
    from musai.web.routes_course import _gradebook_freshness

    blank = Course(semester_id=1, subject="Inglés I", level=1, group_code="X",
                   gradebook_ingested_at=None)
    fresh = _gradebook_freshness(blank, students=0)
    assert fresh["gradebook_ingested_at"] is None
    assert fresh["gradebook_age_days"] is None, "never-imported must not report an age"
    assert fresh["gradebook_stale"] is False, (
        "a never-imported course must not ALSO raise the stale warning — one clear statement, "
        "not two competing ones.")
    assert fresh["gradebook_ingested_label"] == ""
    assert fresh["gradebook_state"] == "never"


def test_students_with_no_timestamp_is_unknown_and_never_never():
    """🔴 The falsehood the first draft printed.

    Every course predating the `gradebook_ingested_at` column has enrolments and a NULL date — the
    migration deliberately does not invent one. Rendering that as *"Never imported. MUSAI does not
    know who is enrolled"* on a page that lists ten students is not a cosmetic slip: it is the
    screen contradicting itself, and it would have shipped on all seven of the owner's courses.

    Same family as `feedback_unreadable_is_not_a_finding` — absent evidence is UNKNOWN.
    """
    from musai.web.routes_course import _gradebook_freshness

    course = Course(semester_id=1, subject="Inglés I", level=1, group_code="1-LED-A",
                    gradebook_ingested_at=None)
    assert _gradebook_freshness(course, students=10)["gradebook_state"] == "undated"
    assert _gradebook_freshness(course, students=0)["gradebook_state"] == "never", (
        "only a course with nobody in it may say 'never'.")


def test_an_old_gradebook_is_called_stale_and_a_fresh_one_is_not():
    from musai.web.routes_course import GRADEBOOK_STALE_DAYS, _gradebook_freshness

    def freshness(days):
        return _gradebook_freshness(Course(
            semester_id=1, subject="Inglés I", level=1, group_code="X",
            gradebook_ingested_at=datetime.utcnow() - timedelta(days=days)))

    assert freshness(1)["gradebook_stale"] is False
    assert freshness(GRADEBOOK_STALE_DAYS + 1)["gradebook_stale"] is True
    # The exact number of days is printed, so it has to be right and not just "old".
    assert freshness(63)["gradebook_age_days"] == 63


def test_the_date_label_does_not_use_a_glibc_only_format():
    """⚠️ `strftime("%-d")` raises on Windows, which is the only machine this runs on. Formatting
    happens in Python so the Grades tab cannot 500 on a platform difference."""
    from musai.web.routes_course import _gradebook_freshness

    label = _gradebook_freshness(Course(
        semester_id=1, subject="Inglés I", level=1, group_code="X",
        gradebook_ingested_at=datetime(2026, 8, 3)))["gradebook_ingested_label"]
    assert label == "3 Aug", label


# ── 1. the route exists and is on the right tab ───────────────────────────────
def test_the_grades_tab_offers_the_refresh_and_explains_what_the_count_is(sign_in, my_course):
    """The button lives on Grades, not behind a new Participants tab: students, activities and
    grades all arrive in one export, so one action refreshes all three."""
    from musai.web.app import app

    # ⚠️ This used to assert "the test DB copy should hold the owner's courses" — which is a fact
    # about his machine, not about MUSAI, and it stopped being true the moment the dev database was
    # blanked for a demo. `my_course` creates what the test needs.
    _prof_id, cid = my_course

    client = sign_in(TestClient(app))
    page = client.get(f"/courses/{cid}/grades")
    assert page.status_code == 200
    body = page.text

    assert f'hx-post="/courses/{cid}/gradebook/refresh"' in body, (
        "the Grades tab has no refresh control — a professor seeing a wrong student count still "
        "has nowhere to press.")
    assert "not from the course's participants list" in body, (
        "the page no longer says what the count actually is. That sentence is the fix: the number "
        "was never wrong, the label was.")


def test_the_overview_students_tile_links_somewhere_and_carries_its_age(sign_in, my_course):
    from musai.web.app import app

    _prof_id, cid = my_course

    client = sign_in(TestClient(app))
    body = client.get(f"/courses/{cid}").text
    # All three honest labels, one of which must be present. A bare number is the failure.
    assert any(s in body for s in ("as of ", "date unknown", "never imported")), (
        "the Students tile shows a bare number again — it must say when it was taken, or say "
        "that it does not know.")
    assert f'href="/courses/{cid}/grades"' in body, (
        "the Students tile is unlinked again — the number is wrong and there is nowhere to go.")


# ── helpers ───────────────────────────────────────────────────────────────────
def _engine():
    from musai.db import engine

    return engine


def _me(sess) -> Professor:
    prof = sess.exec(select(Professor).where(Professor.email == "professor@uach.mx")).first()
    if prof is None:
        prof = Professor(email="professor@uach.mx", full_name="the owner",
                         created_at=datetime.utcnow(), last_seen_at=datetime.utcnow())
        sess.add(prof)
        sess.commit()
        sess.refresh(prof)
    return prof


def _tiny_gradebook(tmp_path):
    """The smallest CSV the real importer accepts, so this exercises the real parser."""
    import csv

    path = tmp_path / "tiny.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        # "Número de ID" is the matrícula column and the importer refuses a file without it
        # (`importer.py:118`) — which is the right refusal, and worth exercising the real parser
        # rather than stubbing it.
        w.writerow(["Nombre", "Apellido(s)", "Número de ID", "Dirección de correo",
                    "Tarea: Workbook (Real)", "Total del curso (Real)"])
        w.writerow(["Ana", "López", "123456", "a123456@uach.mx", "8.00", "8.00"])
    return path
