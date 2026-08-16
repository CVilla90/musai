"""Identity, ownership and the credential vault — the rails that make MUSAI multi-professor.

The question every test here circles is the same one: **when two professors share a database,
what stops one of them seeing or acting on the other's courses?** Before 2026-08-14 the answer
was "nothing, because there was only one professor". These pin the answer down.
"""

import base64
from datetime import date, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from musai.automation.credentials import CredentialsMissing, resolve_for_professor
from musai.config import settings
from musai.models import Course, Professor, Semester
from musai.professors import (courses_owned_by, credential_status, delete_credential,
                              get_credential, get_or_create, mark_used, owns,
                              semester_ids_with_courses, store_credential)
from musai.security import vault
from musai.semesters import ensure_current_semester, semester_label, semester_window


@pytest.fixture
def db():
    """A private in-memory DB per test — ownership tests must not see each other's rows."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as sess:
        yield sess


@pytest.fixture
def two_professors(db):
    """The owner with two courses, Colleague D with one, in one semester."""
    sem = Semester(name="2026-2", starts_on=date(2026, 7, 1), ends_on=date(2026, 12, 31))
    db.add(sem)
    db.commit()
    db.refresh(sem)

    carlos = get_or_create(db, email="professor@uach.mx", full_name="the owner")
    morayma = get_or_create(db, email="colleague4@uach.mx", full_name="Colleague D")
    for prof, codes in ((carlos, ["1-LED-A", "2-LED-B"]), (morayma, ["4-LEF-A"])):
        for code in codes:
            db.add(Course(professor_id=prof.id, semester_id=sem.id, subject="Inglés",
                          level=1, group_code=code, moodle_course_id=f"90{code[-1]}0"))
    db.commit()
    return carlos, morayma, sem


# ── identity ──────────────────────────────────────────────────────────────────
def test_a_professor_row_is_created_on_first_sign_in(db):
    prof = get_or_create(db, email="Nueva.Profesora@UACH.MX", full_name="Nueva")
    assert prof.id is not None
    # The email is the identity key, so it is normalised — otherwise `Nueva@` and `nueva@`
    # would be two professors with half the courses each.
    assert prof.email == "nueva.profesora@uach.mx"


def test_signing_in_twice_does_not_create_a_second_professor(db):
    first = get_or_create(db, email="professor@uach.mx", full_name="the owner")
    second = get_or_create(db, email="professor@uach.mx", full_name="the owner")
    assert first.id == second.id
    assert second.full_name == "the owner"   # Google stays the source of truth for display


def test_admin_comes_from_config_and_is_not_self_grantable(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_email", "professor@uach.mx")
    assert get_or_create(db, email="professor@uach.mx").is_admin is True
    assert get_or_create(db, email="colleague4@uach.mx").is_admin is False


def test_the_moodle_username_guess_is_the_email_local_part(db):
    prof = get_or_create(db, email="professor@uach.mx")
    assert prof.moodle_username_guess == "professor"


# ── ownership: the leak this whole module exists to prevent ──────────────────
def test_a_professor_sees_only_their_own_courses(two_professors, db):
    carlos, morayma, sem = two_professors
    mine = [c.group_code for c in courses_owned_by(db, carlos.id, semester_id=sem.id)]
    theirs = [c.group_code for c in courses_owned_by(db, morayma.id, semester_id=sem.id)]
    assert mine == ["1-LED-A", "2-LED-B"]
    assert theirs == ["4-LEF-A"]


def test_an_unowned_course_belongs_to_nobody_not_to_everybody(two_professors, db):
    """🔴 The direction of this failure is the whole point.

    `Course.professor_id` was NULL on every row until the backfill. A scoping query written as
    "show me courses whose owner is me OR unset" looks helpful and hands every professor every
    legacy course in the database. NULL matches nobody.
    """
    carlos, morayma, sem = two_professors
    db.add(Course(professor_id=None, semester_id=sem.id, subject="Huérfano", level=1,
                  group_code="0-ORPHAN", moodle_course_id="1"))
    db.commit()

    for prof in (carlos, morayma):
        codes = [c.group_code for c in courses_owned_by(db, prof.id, semester_id=sem.id)]
        assert "0-ORPHAN" not in codes


def test_owns_refuses_another_professors_course(two_professors, db):
    carlos, morayma, sem = two_professors
    hers = courses_owned_by(db, morayma.id, semester_id=sem.id)[0]

    assert owns(db, morayma.id, hers.id) is not None
    # Not an exception and not the course — `None`, which the route turns into a 404 rather
    # than a 403, because a 403 confirms the course exists.
    assert owns(db, carlos.id, hers.id) is None


def test_owns_returns_none_for_a_course_that_does_not_exist(two_professors, db):
    carlos, _morayma, _sem = two_professors
    assert owns(db, carlos.id, 999999) is None


def test_the_semester_picker_offers_only_semesters_they_taught(two_professors, db):
    carlos, morayma, sem = two_professors
    old = Semester(name="2026-1", starts_on=date(2026, 1, 1), ends_on=date(2026, 6, 30))
    db.add(old)
    db.commit()
    db.refresh(old)
    db.add(Course(professor_id=carlos.id, semester_id=old.id, subject="Inglés", level=1,
                  group_code="1-LED-A", moodle_course_id="7713"))
    db.commit()

    assert semester_ids_with_courses(db, carlos.id) == {sem.id, old.id}
    assert semester_ids_with_courses(db, morayma.id) == {sem.id}


# ── the vault ─────────────────────────────────────────────────────────────────
def test_a_password_round_trips(db):
    token = vault.encrypt("s3cret-pa55")
    assert token != "s3cret-pa55"          # it is not merely encoded
    assert vault.decrypt(token) == "s3cret-pa55"


def test_the_token_does_not_contain_the_password(db):
    token = vault.encrypt("hunter2")
    assert "hunter2" not in token
    assert "hunter2" not in base64.b64encode(token.encode()).decode()


def test_without_a_key_the_vault_refuses_rather_than_storing_plaintext(db, monkeypatch):
    """🔴 There must be no "store it in the clear for now" branch."""
    monkeypatch.setattr(settings, "credential_key", "")
    assert vault.key_configured() is False
    with pytest.raises(vault.VaultUnavailable) as e:
        vault.encrypt("anything")
    assert "CREDENTIAL_KEY" in str(e.value)

    prof = get_or_create(db, email="professor@uach.mx")
    with pytest.raises(vault.VaultUnavailable):
        store_credential(db, prof.id, "moodle", username="professor", password="pw")
    assert get_credential(db, prof.id, "moodle") is None   # nothing was written


def test_a_malformed_key_is_named_without_being_echoed(monkeypatch):
    monkeypatch.setattr(settings, "credential_key", "not-a-fernet-key")
    with pytest.raises(vault.VaultUnavailable) as e:
        vault.encrypt("x")
    assert "not-a-fernet-key" not in str(e.value)


def test_a_rotated_key_raises_rather_than_returning_an_empty_password(monkeypatch):
    """🔴 An unreadable credential is UNKNOWN, not blank.

    `""` is a plausible-looking password and would be typed into a live login form, producing a
    failed sign-in that looks like a wrong password rather than a key problem.
    """
    token = vault.encrypt("real-password")
    monkeypatch.setattr(settings, "credential_key",
                        base64.urlsafe_b64encode(b"a-different-key-32-bytes-long!!!").decode())
    with pytest.raises(vault.VaultCorrupt):
        vault.decrypt(token)


def test_an_empty_secret_is_refused(db):
    with pytest.raises(ValueError):
        vault.encrypt("")
    with pytest.raises(vault.VaultCorrupt):
        vault.decrypt("")


# ── credential storage ────────────────────────────────────────────────────────
def test_storing_a_credential_never_keeps_the_password(db):
    prof = get_or_create(db, email="colleague4@uach.mx")
    cred = store_credential(db, prof.id, "moodle", username="colleague4", password="live-pw")
    assert "live-pw" not in cred.secret_enc
    assert "live-pw" not in repr(cred)     # keeps it out of pytest diffs and tracebacks
    assert vault.decrypt(cred.secret_enc) == "live-pw"


def test_the_status_the_settings_page_renders_carries_no_secret(db):
    prof = get_or_create(db, email="colleague4@uach.mx")
    store_credential(db, prof.id, "moodle", username="colleague4", password="live-pw")
    status = credential_status(db, prof.id)

    assert status["moodle"]["stored"] is True
    assert status["moodle"]["username"] == "colleague4"
    assert "live-pw" not in str(status)
    assert not any("secret" in k for k in status["moodle"])


def test_replacing_a_password_clears_the_it_works_verdict(db):
    """A green tick above a password that has never been tried is worse than no tick."""
    prof = get_or_create(db, email="colleague4@uach.mx")
    store_credential(db, prof.id, "moodle", username="colleague4", password="old")
    mark_used(db, prof.id, "moodle", ok=True)
    assert get_credential(db, prof.id, "moodle").last_ok_at is not None

    store_credential(db, prof.id, "moodle", username="colleague4", password="new")
    assert get_credential(db, prof.id, "moodle").last_ok_at is None


def test_delete_actually_removes_the_row(db):
    prof = get_or_create(db, email="colleague4@uach.mx")
    store_credential(db, prof.id, "moodle", username="colleague4", password="pw")

    assert delete_credential(db, prof.id, "moodle") is True
    assert get_credential(db, prof.id, "moodle") is None
    assert delete_credential(db, prof.id, "moodle") is False   # idempotent


def test_a_credential_needs_a_username(db):
    prof = get_or_create(db, email="colleague4@uach.mx")
    with pytest.raises(ValueError):
        store_credential(db, prof.id, "moodle", username="  ", password="pw")


def test_credentials_are_scoped_per_professor(db):
    carlos = get_or_create(db, email="professor@uach.mx")
    morayma = get_or_create(db, email="colleague4@uach.mx")
    store_credential(db, carlos.id, "moodle", username="professor", password="carlos-pw")

    assert get_credential(db, morayma.id, "moodle") is None
    assert credential_status(db, morayma.id)["moodle"]["stored"] is False


# ── resolving an identity for a run ───────────────────────────────────────────
def test_a_professor_with_no_stored_password_is_refused_never_defaulted(db, monkeypatch):
    """🔴 The dangerous failure is not an error; it is a run that quietly acts as the owner.

    `resolve()` with no argument returns `UACH_USERNAME`/`UACH_PASSWORD` from `.env`. If
    `resolve_for_professor` fell back to that, Colleague D's restore would run on the owner's account,
    against a course list that is not hers, and Moodle would record him as the author.
    """
    import musai.db as db_mod

    monkeypatch.setattr(settings, "uach_username", "professor")
    monkeypatch.setattr(settings, "uach_password", "carlos-real-password")
    monkeypatch.setattr(db_mod, "engine", db.get_bind())

    morayma = get_or_create(db, email="colleague4@uach.mx")
    with pytest.raises(CredentialsMissing) as e:
        resolve_for_professor(morayma, system="moodle")
    assert "colleague4@uach.mx" in str(e.value)
    assert "carlos" not in str(e.value).lower()


def test_a_stored_password_resolves_to_that_professors_own_account(db, monkeypatch):
    import musai.db as db_mod

    monkeypatch.setattr(db_mod, "engine", db.get_bind())
    morayma = get_or_create(db, email="colleague4@uach.mx")
    store_credential(db, morayma.id, "moodle", username="colleague4", password="her-pw")

    identity = resolve_for_professor(morayma, system="moodle")
    assert identity.username == "colleague4"
    assert identity.password == "her-pw"
    # Their OWN account, so no "another professor's account" warning — that belongs to the
    # `--as-user` road, where a human is acting for somebody else. Crying wolf here would
    # devalue the warning where it matters.
    assert identity.is_self is True
    assert "her-pw" not in repr(identity)


def test_an_undecryptable_credential_refuses_rather_than_trying_a_blank_password(db,
                                                                                monkeypatch):
    import musai.db as db_mod

    monkeypatch.setattr(db_mod, "engine", db.get_bind())
    morayma = get_or_create(db, email="colleague4@uach.mx")
    store_credential(db, morayma.id, "moodle", username="colleague4", password="her-pw")

    monkeypatch.setattr(settings, "credential_key",
                        base64.urlsafe_b64encode(b"rotated-key-32-bytes-long-xxxxx!").decode())
    with pytest.raises(CredentialsMissing):
        resolve_for_professor(morayma, system="moodle")


# ── the semester the calendar implies ─────────────────────────────────────────
@pytest.mark.parametrize("day,expected", [
    (date(2026, 1, 20), "2026-1"),
    (date(2026, 6, 15), "2026-1"),
    (date(2026, 6, 30), "2026-1"),
    (date(2026, 7, 1), "2026-2"),     # the gap between terms still resolves
    (date(2026, 7, 20), "2026-2"),
    (date(2026, 8, 13), "2026-2"),
    (date(2026, 12, 31), "2026-2"),
    (date(2027, 1, 1), "2027-1"),     # and rolls into the next year
])
def test_the_semester_follows_the_calendar(day, expected):
    name, starts, ends = semester_window(day)
    assert name == expected
    # 🔴 Gapless: today is always inside the window it names, so `active_semester`'s
    # "the semester containing today" rule fires instead of falling through to a tiebreak.
    assert starts <= day <= ends


def test_the_windows_do_not_overlap_or_leave_a_hole():
    _n1, _s1, e1 = semester_window(date(2026, 3, 1))
    _n2, s2, _e2 = semester_window(date(2026, 9, 1))
    assert (s2 - e1).days == 1


def test_a_semester_has_a_name_for_sega_and_a_label_for_humans():
    assert semester_label("2026-2") == "Ago–Dic 2026"
    assert semester_label("2027-1") == "Ene–Jun 2027"
    assert semester_label("weird") == "weird"   # never raises on unexpected input


def test_the_current_semester_is_created_once_and_reused(db):
    first = ensure_current_semester(db)
    second = ensure_current_semester(db)
    assert first.id == second.id
    assert first.name == semester_window()[0]


def test_an_existing_semester_keeps_its_own_teaching_dates(db):
    """The real 2026-2 runs Aug 10 → Dec 18. The generic window must not overwrite that."""
    name, _s, _e = semester_window()
    db.add(Semester(name=name, starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18),
                    is_active=True))
    db.commit()

    sem = ensure_current_semester(db)
    assert sem.starts_on == date(2026, 8, 10)
    assert sem.ends_on == date(2026, 12, 18)
