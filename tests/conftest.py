"""Shared fixtures for MUSAI tests."""

import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 THE SUITE MUST NOT BE ABLE TO WRITE TO THE REAL DEV DATABASE.
#
# This runs before any `musai` import, and that ordering is the whole mechanism: `musai.db`
# builds its engine from `settings.database_url` at import time, and roughly twenty modules
# then do `from musai.db import engine`, binding the object by value. Once that has happened
# there is no single place left to redirect — monkeypatching becomes a game of finding every
# module that captured it, and the ones you miss keep talking to the real file.
#
# Paid for on 2026-08-14: the moment `current_professor` started get-or-creating a row, one
# route test signed in as `nobody.new@uach.mx` and **wrote a professor into `musai_dev.db`**.
# Harmless in itself; the same seam is how a test would create courses, credentials or jobs in
# a database that also holds 186 real students. So the suite gets a COPY and the real file stays
# untouched.
#
# ⚠️ A copy for the SCHEMA, never for the CONTENTS. This block used to say the web tests
# "genuinely need the owner's courses to exist" — and on 2026-08-14 `musai.reset_demo` blanked the
# dev database for a demo and seven tests went red on the spot. Nothing was wrong with the code.
# The suite had been quietly asserting that the owner still had fourteen courses, so part of every
# "925 passed" was a statement about his personal data rather than about MUSAI. A test that needs
# a course must **create** one — see the `my_course` fixture below.
#
# Same family as `_no_real_delegate_passwords` and `_vault_key_is_fake` below: make the suite
# structurally unable to reach the real thing rather than careful about not reaching it.
# ─────────────────────────────────────────────────────────────────────────────
_REAL_DB = Path(__file__).resolve().parent.parent / "musai_dev.db"
if _REAL_DB.is_file() and not os.environ.get("DATABASE_URL"):
    _TEST_DB = Path(tempfile.mkdtemp(prefix="musai-tests-")) / "musai_test.db"
    shutil.copy2(_REAL_DB, _TEST_DB)
    os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
    os.environ["DATABASE_URL_READONLY"] = os.environ["DATABASE_URL"]

import pytest  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402
from sqlmodel import SQLModel, create_engine, Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

# Import all models so SQLModel.metadata is fully populated before create_all
import musai.models  # noqa: F401,E402
from musai.automation import credentials as _credentials  # noqa: E402
from musai.config import settings as _settings  # noqa: E402

# 🔴 The copy above is a snapshot of a database that is migrated by hand, so it is always
# some number of migrations behind `musai.models`. Adding one model used to turn the whole
# route suite red with `no such table` — a failure that says nothing about the code and sends
# you looking for a bug in the feature you just wrote. `create_all` only ADDS what is missing;
# it never touches an existing table, so this cannot mask a migration that is genuinely wrong.
# The migration itself is still required — `alembic upgrade head` is what Postgres will run.
if os.environ.get("DATABASE_URL", "").startswith("sqlite"):
    from musai.db import engine as _engine  # noqa: E402

    SQLModel.metadata.create_all(_engine)


def test_the_suite_is_not_pointed_at_the_real_dev_database():
    """A guard on the guard. If the redirect above ever stops working, this says so loudly
    rather than letting the suite quietly start writing to the owner's data again."""
    from musai.db import engine

    assert "musai_dev.db" not in str(engine.url), (
        f"The test suite is bound to {engine.url} — that is the real database. Something "
        f"imported `musai.db` before conftest could redirect it.")


@pytest.fixture(autouse=True)
def _auth_secrets_are_fake(monkeypatch):
    """🔴 No test may see the real Google client secret, or depend on `.env` holding one.

    Same lesson as `_no_real_delegate_passwords` below, applied to sign-in: on 2026-08-13 the
    real `GOOGLE_CLIENT_SECRET` and `SESSION_SECRET` landed in `.env`. From that moment a test
    asserting *"an unconfigured app refuses to serve"* would have been testing the owner's local
    file rather than the code — green until he cleared a variable, red for a reason no reader
    could find in the repo.

    Pin all four to known fakes for every test, whether it thinks it needs them or not. A test
    that wants the unconfigured case sets it explicitly with its own monkeypatch.
    """
    monkeypatch.setattr(_settings, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(_settings, "google_client_secret", "test-client-secret")
    monkeypatch.setattr(_settings, "session_secret", "test-session-secret-not-the-real-one")
    monkeypatch.setattr(_settings, "allowed_email_domain", "uach.mx")
    monkeypatch.setattr(_settings, "admin_email", "professor@uach.mx")
    monkeypatch.setattr(_settings, "app_base_url", "")


@pytest.fixture(autouse=True)
def _no_real_delegate_passwords(monkeypatch, tmp_path):
    """🔴 No test may authenticate as a real professor. Structurally, not by convention.

    Paid for on 2026-08-10: the moment the owner actually set `MOODLE_PWD_COLLEAGUE2` in `.env`, the
    test asserting *"an unknown delegate refuses before anything opens"* stopped being true. It
    resolved a real password, walked past the refusal it was written to prove, and **launched a
    browser to log into Moodle as Colleague C** — 62 seconds of a unit-test run spent talking to a live
    system on a colleague's account.

    The bug was not the assertion. It was that a test could see `.env` at all: the suite's
    behaviour depended on a file whose contents change when a human does unrelated work, so a
    green suite silently became a suite that reaches production. Point the reader at nothing and
    strip the variables, for every test, whether it thinks it needs it or not.
    """
    monkeypatch.setattr(_credentials, "DEFAULT_ENV_FILE", tmp_path / "no-such.env")
    for key in [k for k in os.environ if k.startswith(_credentials.ENV_PREFIX)]:
        monkeypatch.delenv(key, raising=False)


#: A valid Fernet key, hardcoded — a *test* key, and its being printed here in the open is the
#: point. It decodes to the ASCII `musai-test-key-never-the-real-1!`, so anything encrypted
#: under it is self-evidently test data. Every test uses this and never `CREDENTIAL_KEY`.
TEST_CREDENTIAL_KEY = "bXVzYWktdGVzdC1rZXktbmV2ZXItdGhlLXJlYWwtMSE="


@pytest.fixture(autouse=True)
def _vault_key_is_fake(monkeypatch):
    """🔴 No test may decrypt a real stored password, or depend on `CREDENTIAL_KEY` existing.

    The third instance of the same lesson (`_no_real_delegate_passwords`,
    `_auth_secrets_are_fake`), and the one with the worst failure mode: the vault now holds
    **other professors' live Moodle passwords**, and `.env` on this machine holds the key that
    opens them. A test that reads `settings.credential_key` is one careless `print` away from a
    colleague's password in a pytest diff — and a test asserting *"an unconfigured vault
    refuses"* would quietly stop refusing the day the key was set, exactly as the delegate-
    password test did on 2026-08-10.

    So the suite runs on a key it chose. A test that wants the unconfigured case clears it
    explicitly.
    """
    monkeypatch.setattr(_settings, "credential_key", TEST_CREDENTIAL_KEY)


TEST_SESSION_SECRET = "test-session-secret-not-the-real-one"


@pytest.fixture
def sign_in(monkeypatch):
    """Put a valid professor session on a `TestClient`, on a key the SUITE chose.

    Every cockpit route is behind `AuthGateMiddleware` now, so a test that drives one has to
    arrive signed in — otherwise it silently follows a 303 to the landing page and asserts
    against *that*, which is how four hub tests started passing `200` for a course that does
    not exist.

    The key is forced to a fake, rather than reused from the app, on purpose: `musai.web.app`
    installs `SessionMiddleware` at import time with whatever `.env` held. Signing with that
    would make the suite's behaviour depend on a file a human edits for unrelated reasons —
    the same defect that `_no_real_delegate_passwords` exists to prevent. Here the suite
    substitutes its own key and never reads the real one.
    """
    from starlette.middleware import Middleware
    from starlette.middleware.sessions import SessionMiddleware

    from musai.web import auth as _auth

    def _sign_in(client, email: str = "professor@uach.mx", name: str = "the owner"):
        app = client.app
        rebuilt, found = [], False
        for mw in app.user_middleware:
            if mw.cls is SessionMiddleware:
                rebuilt.append(Middleware(SessionMiddleware,
                                          **{**mw.kwargs, "secret_key": TEST_SESSION_SECRET}))
                found = True
            else:
                rebuilt.append(mw)
        if not found:
            # `.env` had no SESSION_SECRET when the app was imported. Index 0 is OUTERMOST,
            # so this must be inserted, not appended — the gate has to read the session.
            rebuilt.insert(0, Middleware(
                SessionMiddleware, secret_key=TEST_SESSION_SECRET,
                session_cookie=_auth.SESSION_COOKIE, max_age=_auth.SESSION_MAX_AGE,
                same_site="lax", https_only=False,
            ))
        monkeypatch.setattr(app, "user_middleware", rebuilt)
        monkeypatch.setattr(app, "middleware_stack", None)   # forces a rebuild on next call

        payload = base64.b64encode(
            json.dumps({"user": {"email": email, "name": name, "picture": "",
                                 "is_admin": True}}).encode()
        )
        cookie = TimestampSigner(TEST_SESSION_SECRET).sign(payload).decode()
        client.cookies.set(_auth.SESSION_COOKIE, cookie)
        return client

    return _sign_in


@pytest.fixture
def my_course():
    """A `Course` owned by the signed-in professor in the *app* database, created if absent.

    🔴 Why this exists: several web tests used to do
    `select(Course).where(Course.professor_id == prof.id)).first()` and then use `.id` — which
    reads as "borrow one of the owner's courses" and is really "assert the owner has a course". The day
    `musai.reset_demo` blanked the dev database those tests raised
    `AttributeError: 'NoneType' object has no attribute 'id'`, which is the least informative
    possible way to be told that a fixture was implicit.

    Returns `(professor_id, course_id)`. Idempotent: reuses the professor's first course when the
    copied database already has one, so it costs nothing on a populated machine and works on an
    empty one.
    """
    from sqlmodel import Session, select

    from musai.db import engine as app_engine
    from musai.models import Course, Professor, Semester
    from musai.semesters import ensure_current_semester

    with Session(app_engine, expire_on_commit=False) as sess:
        prof = sess.exec(select(Professor).where(
            Professor.email == "professor@uach.mx")).first()
        if prof is None:
            prof = Professor(email="professor@uach.mx", full_name="the owner")
            sess.add(prof)
            sess.commit()
            sess.refresh(prof)

        course = sess.exec(select(Course).where(Course.professor_id == prof.id)).first()
        if course is None:
            sem = ensure_current_semester(sess)
            course = Course(semester_id=sem.id, professor_id=prof.id, subject="INGLES I",
                            level=1, group_code="1-LED-A", moodle_course_id="9023",
                            moodle_env="prod")
            sess.add(course)
            sess.commit()
            sess.refresh(course)
        return prof.id, course.id


@pytest.fixture(scope="session")
def engine():
    """In-memory SQLite engine for unit tests. No Postgres needed."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    SQLModel.metadata.drop_all(eng)


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess
