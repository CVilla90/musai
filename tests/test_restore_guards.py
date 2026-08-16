"""Safety guards on the .mbz course restore.

A restore is the most destructive thing MUSAI can do: it deletes the course contents and the
gradebook with them, and it can wipe every enrolment if one wizard setting doesn't take. These
tests cover the refusals — the paths that must fail LOUDLY and, crucially, fail *before*
anything is mutated.

The wizard itself needs a live Moodle and is exercised by `--group X --file Y` (dry run), which
walks every step and stops at the review screenshot.
"""

import inspect
import re
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session

from musai.automation import restore as R
from musai.models import Activity, Course, Grade, Semester, Student


# ── file / argument validation (no browser, no DB) ──────────────────────────────────────

def test_rejects_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(R.settings, "uach_username", "u", raising=False)
    monkeypatch.setattr(R.settings, "uach_password", "p", raising=False)
    with pytest.raises(R.RestoreAborted, match="not found"):
        R.restore_course(idc="9023", backup_path=tmp_path / "nope.mbz")


def test_rejects_a_file_that_is_not_an_mbz(tmp_path, monkeypatch):
    monkeypatch.setattr(R.settings, "uach_username", "u", raising=False)
    monkeypatch.setattr(R.settings, "uach_password", "p", raising=False)
    bad = tmp_path / "grades.ods"
    bad.write_bytes(b"x")
    with pytest.raises(R.RestoreAborted, match=r"\.mbz"):
        R.restore_course(idc="9023", backup_path=bad)


def test_rejects_missing_credentials(tmp_path, monkeypatch):
    mbz = tmp_path / "english_i.mbz"
    mbz.write_bytes(b"x")
    monkeypatch.setattr(R.settings, "uach_username", "", raising=False)
    monkeypatch.setattr(R.settings, "uach_password", "", raising=False)
    with pytest.raises(R.RestoreAborted, match="credentials"):
        R.restore_course(idc="9023", backup_path=mbz)


def test_dry_run_is_the_default():
    """Rail 2. If this ever flips, a restore becomes destructive by omission."""
    import inspect
    assert inspect.signature(R.restore_course).parameters["dry_run"].default is True
    assert inspect.signature(R.restore_for_course).parameters["dry_run"].default is True


def test_keep_roles_enrolments_defaults_to_true():
    """The setting that saves already-enrolled students. 83 enrolments ride on it."""
    assert R.DEFAULT_SETTINGS["keep_roles_enrolments"] is True


# ── the wizard's fail-closed settings ───────────────────────────────────────────────────

class _FakeLocator:
    def __init__(self, count=1, value=""):
        self._count, self._value = count, value

    def count(self):
        return self._count

    def select_option(self, v):
        self._value = v

    def input_value(self):
        return self._value


class _Stubborn(_FakeLocator):
    """A <select> that ignores select_option — i.e. the setting silently doesn't take."""

    def select_option(self, v):
        pass


class _FakePage:
    def __init__(self, loc):
        self._loc = loc

    def locator(self, sel):
        return type("L", (), {"first": self._loc})()


def test_missing_setting_aborts_instead_of_continuing():
    """The original logged 'CRITICAL: could not find' and RESTORED ANYWAY, wiping enrolments."""
    page = _FakePage(_FakeLocator(count=0))
    with pytest.raises(R.RestoreAborted, match="not found"):
        R._set_and_verify(page, "setting_course_keep_roles_and_enrolments", "1", "critical one")


def test_setting_that_does_not_stick_aborts():
    page = _FakePage(_Stubborn(count=1, value="0"))
    with pytest.raises(R.RestoreAborted, match="would not take"):
        R._set_and_verify(page, "setting_course_keep_roles_and_enrolments", "1", "critical one")


def test_setting_that_takes_is_accepted():
    loc = _FakeLocator(count=1, value="0")
    R._set_and_verify(_FakePage(loc), "setting_course_keep_roles_and_enrolments", "1", "ok")
    assert loc.input_value() == "1"


def test_no_forward_button_aborts():
    with pytest.raises(R.RestoreAborted, match="No 'next' button"):
        R._click_forward(_FakePage(_FakeLocator(count=0)), "next")


# ── the gradebook guard ─────────────────────────────────────────────────────────────────

@pytest.fixture
def graded_course(session: Session, monkeypatch, tmp_path):
    """A course that already holds grades — the case where a restore destroys real work."""
    # The `engine` fixture is session-scoped, so rows outlive each test: keep ids unique.
    uid = uuid4().hex[:8]
    sem = Semester(name=f"restore-sem-{uid}", is_active=True,
                   starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18))
    session.add(sem)
    session.commit()
    session.refresh(sem)
    c = Course(group_code="3-LMH-A", subject="INGLES III", level=3, semester_id=sem.id,
               moodle_course_id="9067")
    st = Student(matricula=uid, full_name="Test Student")
    session.add(c)
    session.add(st)
    session.commit()
    session.refresh(c)
    session.refresh(st)
    a = Activity(course_id=c.id, name="First term exam", category="exam")
    session.add(a)
    session.commit()
    session.refresh(a)
    session.add(Grade(student_id=st.id, activity_id=a.id, value=8.5, source="moodle"))
    session.commit()
    monkeypatch.setattr("musai.db.engine", session.get_bind())
    return c


def _mbz(tmp_path) -> Path:
    p = tmp_path / "english_iii.mbz"
    p.write_bytes(b"fake")
    return p


def test_refuses_when_the_course_already_has_grades(graded_course, tmp_path, monkeypatch):
    """🔴 A restore WIPES the gradebook. Never silently."""
    called = []
    monkeypatch.setattr(R, "restore_course", lambda **kw: called.append(kw) or {"ok": True})

    with pytest.raises(R.RestoreAborted, match="WIPES the gradebook"):
        R.restore_for_course(graded_course, _mbz(tmp_path), dry_run=True)

    assert not called, "must refuse BEFORE opening a browser"


def test_force_overrides_the_grade_guard(graded_course, tmp_path, monkeypatch):
    monkeypatch.setattr(R, "restore_course",
                        lambda **kw: {"ok": True, "dry_run": kw["dry_run"], "steps": []})
    out = R.restore_for_course(graded_course, _mbz(tmp_path), dry_run=True, force=True)
    assert out["ok"]


def test_course_without_moodle_id_is_refused(session: Session, tmp_path, monkeypatch):
    sem = Semester(name="s2", is_active=False,
                   starts_on=date(2026, 1, 20), ends_on=date(2026, 6, 15))
    session.add(sem)
    session.commit()
    session.refresh(sem)
    c = Course(group_code="1-LED-A", subject="INGLES I", level=1, semester_id=sem.id,
               moodle_course_id=None)
    session.add(c)
    session.commit()
    session.refresh(c)
    monkeypatch.setattr("musai.db.engine", session.get_bind())
    with pytest.raises(R.RestoreAborted, match="no moodle_course_id"):
        R.restore_for_course(c, _mbz(tmp_path), dry_run=True)


# ── counting what landed ────────────────────────────────────────────────────────────────

class _SectionPage:
    """A course page that shows ONE section at a time — the format the restore produced."""

    def __init__(self, mods_by_section: dict):
        self.mods = mods_by_section
        self.current = None

    def goto(self, url, **kw):
        self.current = int(url.split("section=")[1])

    def wait_for_load_state(self, *a, **kw):
        pass

    def evaluate(self, js, *a):
        return self.mods.get(self.current, 0)


def test_counts_activities_across_all_sections():
    """🔴 The landing page reports 0 for this course; a real restore put 80 in 12 sections."""
    real = {1: 26, 2: 1, 3: 1, 4: 23, 5: 1, 6: 1, 7: 14, 8: 1, 9: 1, 10: 1, 11: 1, 13: 9}
    total, per_section = R._count_activities(_SectionPage(real), "h", "9023")
    assert total == 80
    assert per_section == real


def test_a_wide_gap_between_sections_does_not_truncate_the_count():
    """Any 'stop after N empty sections' shortcut undercounts, and an undercount reports a
    SUCCESSFUL restore as a failed one. Sections 2-12 empty here, 13 populated."""
    total, _ = R._count_activities(_SectionPage({1: 5, 13: 9}), "h", "9023")
    assert total == 14


def test_an_empty_course_counts_zero():
    total, per = R._count_activities(_SectionPage({}), "h", "9023")
    assert total == 0 and per == {}


# ── the port left no credentials behind ─────────────────────────────────────────────────

def test_no_hardcoded_passwords_in_the_port():
    """The source this was ported from held four professors' plaintext passwords.

    This used to assert on the four literals — which made *this file* the last plaintext
    copy of them, and a test that leaks the secret it guards is not a guard. It now matches
    the SHAPE of an embedded credential instead, which also catches the next one nobody has
    seen yet.
    """
    src = Path(R.__file__).read_text(encoding="utf-8")
    embedded = re.findall(r'["\'](?:password|passwd|pwd)["\']\s*:\s*["\'][^"\']+["\']', src,
                          re.I)
    assert not embedded, f"credential literal in the port: {embedded}"
    assert "settings.uach_username" in src and "settings.uach_password" in src


# ── the post-restore count, and why a zero is not a failure yet ──────────────────────────

def test_a_zero_count_is_retried_before_it_is_called_a_failure():
    """🔴 Measured on 9046, 2026-08-11: Moodle left the review page after 875 s, the count ran
    immediately and returned 0, and the run reported FAILED — about a restore that had placed
    all 79 activities, confirmed minutes later from a fresh login.

    A false FAILED is the dangerous direction here, because the obvious response is to run the
    restore again, and a restore DELETES the target's contents first. So the zero must be
    re-counted, not reported."""
    src = inspect.getsource(R.restore_course)
    tail = src[src.index("mods, per_section = _count_activities"):]
    assert "while mods == 0" in tail
    assert "SETTLE_TIMEOUT_S" in tail
    assert R.SETTLE_TIMEOUT_S >= 60


def test_the_failure_message_tells_you_to_count_before_re_running():
    """The message a human acts on. If it just says "zero activities", the next step is a
    re-run; the re-run is what destroys a course that actually succeeded."""
    src = inspect.getsource(R.restore_course)
    # ⚠️ Join the implicit string concatenation before matching. The message is written across
    # several source lines, so a phrase like "deletes the target" exists in the RENDERED text
    # and not in the source — an assertion against raw source fails on a reflow and teaches the
    # next person to weaken it.
    zero_branch = re.sub(r'"\s*\n\s*"', "", src[src.index("counts ZERO activities"):][:700])
    assert "fresh login" in zero_branch
    assert "restore deletes the target first" in zero_branch.lower()
    # 🔴 And it must say the count itself is untrustworthy, not merely that the course is empty.
    # Measured 2026-08-11: this in-session count was wrong on two of three restores, and the
    # dangerous reading of "zero activities" is "the restore failed, run it again".
    assert "do not re-run" in zero_branch.lower()
    assert "verify_english_ii" in zero_branch


def test_how_long_it_took_to_settle_is_recorded():
    """`settle_seconds` is the evidence that the retry was needed at all — without it the fix
    is invisible and the next person removes it as dead code."""
    assert "settle_seconds" in inspect.getsource(R.restore_course)
