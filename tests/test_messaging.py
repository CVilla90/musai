"""Tests for the Messaging Hub — which is mostly tests of its refusals.

Every other MUSAI write is a course page that can be edited again. This one reaches students
and cannot be unsent, so the interesting behaviour is not "does it send" but "what does it
decline to send, and does it say why". The browser is faked rather than mocked away, so the
real flow — read the roster, decide, cross-check, tick, dispatch, preview — actually runs.

Two of these pin findings that only exist because the live path was probed first:
`#checkallonpage` says *on page*, and Moodle's compose screen reports a COUNT and never the
names — so the count is the only thing there is to cross-check, and it has to be checked
against something outside Moodle.
"""

from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session, select

from musai.automation import messaging as M
from musai.automation.messaging import MessagingRefused
from musai.messaging import store
from musai.models import (
    Course, Enrollment, MessageBatch, MessageRecipient, Semester, Student,
)

# The roster page as it really renders: teachers have no matrícula in their email, students
# are `a<matricula>@uach.mx`, and Moodle pads the table — so rows are read from checkboxes.
#
# 🔴 THE STUDENTS BELOW ARE SYNTHETIC AND MUST STAY SYNTHETIC. This fixture was originally
# pasted straight out of a live 1-LED-A roster probe, so it carried two real students' full
# names and matrículas — and it was committed on 2026-08-09. Found 2026-08-16 while auditing
# for the first push to GitHub; scrubbed before anything left the machine.
# The shape is what makes it a trap: a fixture is *most* convincing when it is real, so the
# pressure is always to paste the live page. Nothing in this test needs a real person — it
# tests parsing, exclusion and the matrícula↔user-id join, all of which hold on invented rows.
# The owner's own row stays: he is the repo owner and `professor@uach.mx` is his published address.
RAW = {
    "me": "31033",
    "rows": [
        {"checkbox": "user31033", "user_id": "31033",
         "name": "LAURA MENDEZ TORRES", "email": "professor@uach.mx"},
        {"checkbox": "user50001", "user_id": "50001",
         "name": "MARIANA JIMENEZ OCHOA", "email": "a400001@uach.mx"},
        {"checkbox": "user50002", "user_id": "50002",
         "name": "RODRIGO SALAS MENDOZA", "email": "a400002@uach.mx"},
    ],
}
MATRICULAS = ["400001", "400002"]


@pytest.fixture
def course(session: Session) -> Course:
    sem = Semester(name="2026-2", starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18),
                   is_active=True)
    session.add(sem)
    session.commit()
    session.refresh(sem)
    c = Course(semester_id=sem.id, subject="Inglés I", level=1,
               group_code=f"1-LED-{sem.id}", moodle_course_id="9023")
    session.add(c)
    session.commit()
    session.refresh(c)
    for mat, name in (("400001", "MARIANA JIMENEZ OCHOA"),
                      ("400002", "RODRIGO SALAS MENDOZA")):
        st = session.exec(select(Student).where(Student.matricula == mat)).first()
        if st is None:
            st = Student(matricula=mat, full_name=name)
            session.add(st)
            session.commit()
            session.refresh(st)
        session.add(Enrollment(student_id=st.id, course_id=c.id))
    session.commit()
    return c


# ── who gets it, and who does not ───────────────────────────────────────────────────────

def test_the_professor_is_excluded_from_his_own_message():
    r = M.build_roster(RAW, expected_matriculas=MATRICULAS)
    assert [x.full_name for x in r.included] == [
        "MARIANA JIMENEZ OCHOA", "RODRIGO SALAS MENDOZA"]
    assert r.excluded[0].excluded_reason == "es la cuenta que envía"


def test_the_matricula_is_read_off_the_course_email():
    """`a400001@uach.mx` → 400001. This is the matrícula ↔ Moodle-user-id join, and the
    roster page is the only place it appears."""
    r = M.build_roster(RAW, expected_matriculas=MATRICULAS)
    assert {x.matricula for x in r.included} == {"400001", "400002"}


def test_somebody_not_enrolled_in_MUSAI_is_excluded_with_a_reason():
    """The roster page carries NO role column, so nothing on it distinguishes a student from
    a second teacher. MUSAI's own enrolment is what does."""
    raw = {**RAW, "rows": RAW["rows"] + [
        {"checkbox": "user99", "user_id": "99", "name": "OTRA PROFESORA",
         "email": "a999999@uach.mx"}]}
    r = M.build_roster(raw, expected_matriculas=MATRICULAS)
    stray = [x for x in r.excluded if x.moodle_user_id == "99"][0]
    assert "no está inscrito" in stray.excluded_reason


def test_a_row_with_no_student_email_is_excluded():
    raw = {**RAW, "rows": RAW["rows"] + [
        {"checkbox": "user77", "user_id": "77", "name": "SOPORTE", "email": "help@uach.mx"}]}
    r = M.build_roster(raw, expected_matriculas=MATRICULAS)
    assert [x for x in r.excluded if x.moodle_user_id == "77"][0].excluded_reason \
        == "no tiene correo de estudiante"


def test_only_me_selects_exactly_the_sender():
    r = M.build_roster(RAW, expected_matriculas=MATRICULAS, only_me=True)
    assert len(r.included) == 1 and r.included[0].moodle_user_id == "31033"
    assert all(x.excluded_reason == "modo sólo-a-mí" for x in r.excluded)


# ── the count cross-check (rail 3) ──────────────────────────────────────────────────────

def test_a_short_recipient_list_refuses():
    """🔴 The pagination trap, which is the whole reason this rail exists: `#checkallonpage`
    says *on page*, so on a 32-student group a naive select-all ticks 20 and nothing errors.
    MUSAI catches it only because it knows the enrolment independently."""
    r = M.build_roster(RAW, expected_matriculas=MATRICULAS)
    with pytest.raises(MessagingRefused, match="faltan 1"):
        M.check_counts(r, expected=3, only_me=False)


def test_more_recipients_than_expected_also_refuses():
    r = M.build_roster(RAW, expected_matriculas=MATRICULAS)
    with pytest.raises(MessagingRefused, match="de más"):
        M.check_counts(r, expected=1, only_me=False)


def test_an_empty_recipient_list_refuses():
    r = M.build_roster({"me": "31033", "rows": []}, expected_matriculas=MATRICULAS)
    with pytest.raises(MessagingRefused, match="vacía"):
        M.check_counts(r, expected=0, only_me=False)


def test_a_matching_count_passes():
    r = M.build_roster(RAW, expected_matriculas=MATRICULAS)
    M.check_counts(r, expected=2, only_me=False)


def test_a_STALE_musai_roster_refuses_even_though_the_counts_agree():
    """🔴 The one-sided-check bug, found by running the real dry run: MUSAI's enrolment for
    1-LED-A is last semester's (3 students) while Moodle holds the 2026-2 cohort (10). The
    intersection is 3, so counts 'agree' and seven students silently get nothing."""
    raw = {**RAW, "rows": RAW["rows"] + [
        {"checkbox": "user401", "user_id": "401", "name": "NUEVA ALUMNA",
         "email": "a409001@uach.mx"},
        {"checkbox": "user402", "user_id": "402", "name": "NUEVO ALUMNO",
         "email": "a409002@uach.mx"}]}
    r = M.build_roster(raw, expected_matriculas=MATRICULAS)
    assert len(r.included) == 2, "the counts do agree — that is the trap"
    with pytest.raises(MessagingRefused, match="sólo llegaría a 2 de 4"):
        M.check_counts(r, expected=2, only_me=False)


def test_the_stale_roster_refusal_names_who_would_be_missed():
    raw = {**RAW, "rows": RAW["rows"] + [
        {"checkbox": "user401", "user_id": "401", "name": "NUEVA ALUMNA",
         "email": "a409001@uach.mx"}]}
    r = M.build_roster(raw, expected_matriculas=MATRICULAS)
    with pytest.raises(MessagingRefused, match="NUEVA ALUMNA"):
        M.check_counts(r, expected=2, only_me=False)


def test_a_teacher_on_the_roster_is_not_mistaken_for_a_stale_enrolment():
    """Only student-shaped rows (a matrícula in the email) count as evidence of staleness;
    a colleague or an admin on the participants list is not a missing student."""
    raw = {**RAW, "rows": RAW["rows"] + [
        {"checkbox": "user77", "user_id": "77", "name": "COORDINACION",
         "email": "coord@uach.mx"}]}
    r = M.build_roster(raw, expected_matriculas=MATRICULAS)
    M.check_counts(r, expected=2, only_me=False)


def test_only_me_is_not_blocked_by_a_stale_roster():
    """The self-test exists to prove the PATH works; it must not depend on the enrolment
    being current, or it is unavailable exactly when it is most needed."""
    r = M.build_roster(RAW, expected_matriculas=[], only_me=True)
    M.check_counts(r, expected=0, only_me=True)


# ── the body ────────────────────────────────────────────────────────────────────────────

def test_plain_text_becomes_html_because_the_form_is_format_1():
    """Measured: the compose form posts `format=1`. A plain-text body would collapse to one
    run-on paragraph, which is not what the professor previewed."""
    assert M.to_html("Hola\n\nSegundo párrafo") == "<p>Hola</p><p>Segundo párrafo</p>"


def test_a_single_newline_is_a_line_break():
    assert M.to_html("Uno\nDos") == "<p>Uno<br>Dos</p>"


def test_markup_in_the_body_is_escaped_not_rendered():
    assert "&lt;script&gt;" in M.to_html("<script>alert(1)</script>")


def test_emoji_survive():
    assert "🧪" in M.to_html("prueba 🧪")


def test_the_hash_ignores_whitespace_so_a_retyped_message_still_counts_as_the_same():
    assert M.body_hash("Hola  mundo") == M.body_hash("Hola mundo\n")


# ── the record, and the guard it exists to provide ──────────────────────────────────────

def _result(**over):
    base = {"ok": True, "dry_run": False, "only_me": False, "expected": 2, "moodle_count": 2,
            "recipients": [{"moodle_user_id": "50001", "matricula": "400001",
                            "full_name": "MARIANA JIMENEZ OCHOA"},
                           {"moodle_user_id": "50002", "matricula": "400002",
                            "full_name": "RODRIGO SALAS MENDOZA"}],
            "excluded": [{"moodle_user_id": "31033", "matricula": None,
                          "full_name": "CARLOS", "excluded_reason": "es la cuenta que envía"}]}
    base.update(over)
    return base


def test_a_batch_records_the_excluded_as_well_as_the_included(session, course):
    batch = store.record(session, course=course, purpose="bienvenida",
                         body="Hola", result=_result())
    rows = store.recipients_of(session, batch.id)
    assert sorted((r.full_name, r.included) for r in rows) == sorted(
        [("MARIANA JIMENEZ OCHOA", True), ("RODRIGO SALAS MENDOZA", True),
         ("CARLOS", False)])
    assert [r.excluded_reason for r in rows if not r.included] == ["es la cuenta que envía"]


def test_recording_learns_the_moodle_user_id_for_next_time(session, course):
    """The join MUSAI did not have. It makes reading one student's messages (v2) possible."""
    store.record(session, course=course, purpose="aviso", body="Hola", result=_result())
    st = session.exec(select(Student).where(Student.matricula == "400001")).first()
    assert st.moodle_user_id == "50001"


def test_an_identical_body_sent_recently_is_detected(session, course):
    store.record(session, course=course, purpose="aviso", body="Recuerden el examen",
                 result=_result())
    assert store.recent_duplicate(session, course.id, "Recuerden el examen") is not None
    assert store.recent_duplicate(session, course.id, "Otra cosa") is None


def test_a_DRY_RUN_does_not_count_as_already_sent(session, course):
    """It said nothing to anybody. Blocking the real send afterwards would be absurd."""
    store.record(session, course=course, purpose="aviso", body="Hola",
                 result=_result(dry_run=True))
    assert store.recent_duplicate(session, course.id, "Hola") is None


def test_a_FAILED_send_does_not_block_the_retry(session, course):
    store.record(session, course=course, purpose="aviso", body="Hola",
                 result=_result(ok=False, error="boom"))
    assert store.recent_duplicate(session, course.id, "Hola") is None


def test_the_duplicate_window_expires(session, course):
    batch = store.record(session, course=course, purpose="aviso", body="Hola",
                         result=_result())
    batch.created_at = datetime.utcnow() - timedelta(hours=48)
    session.add(batch)
    session.commit()
    assert store.recent_duplicate(session, course.id, "Hola") is None


def test_the_duplicate_guard_is_per_course(session, course):
    """Seven groups get the same announcement. That is the normal case, not a mistake."""
    other = Course(semester_id=course.semester_id, subject="Inglés I", level=1,
                   group_code="1-LED-B", moodle_course_id="9026")
    session.add(other)
    session.commit()
    session.refresh(other)
    store.record(session, course=course, purpose="aviso", body="Hola", result=_result())
    assert store.recent_duplicate(session, other.id, "Hola") is None


# ── the rubric, which is why `purpose` is a column ──────────────────────────────────────

def test_the_rubric_counts_only_what_students_actually_received(session, course):
    """Criterion 6 is 'al menos dos mensajes de seguimiento' — a COUNT of a KIND."""
    store.record(session, course=course, purpose="bienvenida", body="a", result=_result())
    store.record(session, course=course, purpose="seguimiento", body="b", result=_result())
    store.record(session, course=course, purpose="seguimiento", body="c", result=_result())
    store.record(session, course=course, purpose="seguimiento", body="d",
                 result=_result(dry_run=True))       # a rehearsal is not a message
    counts = store.rubric_counts(session, course.id)
    assert counts["bienvenida"] == 1
    assert counts["seguimiento"] == 2, "the dry run must not inflate the score"
    assert counts["cierre"] == 0


def test_an_unknown_purpose_falls_back_rather_than_crashing(session, course):
    batch = store.record(session, course=course, purpose="nonsense", body="x",
                         result=_result())
    assert batch.purpose == "aviso"


# ── the gate in front of the job ────────────────────────────────────────────────────────

# 🔴 The gate that did not exist on 2026-08-08, and the incident that bought it.
#
# A test written to prove the confirmation rail worked POSTed `action=send` at the live app
# with a lowercase group code, expecting a refusal. The comparison is case-insensitive, so it
# was accepted, and "Hola prueba" reached three real students. Every other rail held and none
# of them mattered: the run should never have been able to reach Moodle at all.

def test_DRY_RUN_makes_a_real_send_structurally_impossible(session, course, monkeypatch):
    """With DRY_RUN on, no combination of correctly-typed inputs can deliver anything."""
    from musai.messaging import jobs
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    monkeypatch.setattr(jobs.settings, "dry_run", True)

    with pytest.raises(MessagingRefused, match="DRY_RUN"):
        jobs.start(course.id, jobs.SEND, purpose="aviso", body="Hola",
                   confirm=course.group_code)


def test_the_dry_run_gate_is_checked_BEFORE_the_typed_code(session, course, monkeypatch):
    """Order matters for what the professor is told: 'the switch is off' is actionable,
    'you typed the code wrong' sends them to retype something that would not have worked."""
    from musai.messaging import jobs
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    monkeypatch.setattr(jobs.settings, "dry_run", True)
    with pytest.raises(MessagingRefused, match="DRY_RUN"):
        jobs.start(course.id, jobs.SEND, purpose="aviso", body="Hola", confirm="basura")


def test_the_browser_layer_refuses_too_even_called_directly(monkeypatch):
    """Defence in depth: `jobs.start` guards today's callers, this guards the next entry
    point somebody adds — a CLI, a scheduled reminder — which will not re-implement it."""
    monkeypatch.setattr(M.settings, "dry_run", True)
    with pytest.raises(MessagingRefused, match="DRY_RUN"):
        M.send_message(idc="9023", body="Hola", expected_matriculas=["1"], dry_run=False)


def test_sending_as_another_professor_refuses_without_their_password(monkeypatch):
    """`as_user` fails closed. Until 2026-08-11 this function ignored the question entirely
    and always logged in as the owner — so a message to a colleague's students would have gone
    out signed by the wrong professor, and a message cannot be unsent."""
    from musai.automation.credentials import CredentialsMissing

    monkeypatch.setattr(M.settings, "dry_run", False)
    with pytest.raises(CredentialsMissing):
        M.send_message(idc="9027", body="Hola", expected_matriculas=["1"],
                       dry_run=False, as_user="nadie")


def test_the_sender_identity_is_resolved_before_a_browser_exists(monkeypatch):
    """Order is the property. Resolving after login would surface a missing delegate password
    as a half-finished session on a live course instead of a refusal on this machine."""
    from musai.automation.credentials import CredentialsMissing

    def boom(*a, **k):                       # pragma: no cover - must never run
        raise AssertionError("playwright started before the identity was resolved")

    monkeypatch.setattr(M.settings, "dry_run", False)
    monkeypatch.setattr(M, "sync_playwright", boom)
    with pytest.raises(CredentialsMissing):
        M.send_message(idc="9027", body="Hola", expected_matriculas=["1"],
                       dry_run=False, as_user="nadie")


# ── the send click, and the timeout that is NOT a failure ───────────────────────────────
#
# 🔴 These pin the most expensive lesson in this module. Moodle fans a send out synchronously
# — one message row per recipient, inside the request — so 18 recipients took over 30 s and 35
# took ~90 s. The click used to run with Playwright's default 30 s auto-wait for scheduled
# navigations, so it raised *while the server was still delivering*. The exception said
# "timeout waiting for navigation", that was read as **the host is down**, and the command was
# re-run. 35 students received the message twice.
#
# Measured 2026-08-12: the same timeout fires with the host answering in 1.5 s, and every
# recipient has the message exactly once. The send had already succeeded.

class _FakeLocator:
    def __init__(self, page, selector):
        self._page, self._selector = page, selector

    def check(self):
        self._page.checked.append(self._selector)

    def click(self, **kw):
        self._page.clicks.append((self._selector, kw))


class _FakeNav:
    """`expect_navigation` as a context manager, so a timeout can be raised on exit — which
    is where the real one raises once the click inside has already happened."""

    def __init__(self, page, timeout):
        self._page, self.timeout = page, timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._page.fail_next_navigation:
            self._page.fail_next_navigation = False
            raise M.PWTimeout("Timeout waiting for navigation")
        return False


class _FakePage:
    def __init__(self):
        self.checked, self.clicks, self.nav_timeouts = [], [], []
        self.fail_next_navigation = False

    def goto(self, *a, **k):
        return None

    def wait_for_load_state(self, *a, **k):
        return None

    def wait_for_timeout(self, *a, **k):
        return None

    def screenshot(self, **k):
        return None

    def select_option(self, *a, **k):
        return None

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def expect_navigation(self, **kw):
        self.nav_timeouts.append(kw.get("timeout"))
        return _FakeNav(self, kw.get("timeout"))

    def evaluate(self, js, *args):
        if js is M._ROSTER_JS:
            return RAW
        if js is M._TICKED_JS:
            return ["user50001", "user50002"]
        if js is M._HEADING_JS:
            return {"count": 2}
        if js is M._FILL_JS:
            return "ok"
        return {}


def _fake_browser(monkeypatch, page):
    """Wire a fake Playwright + a fake `enter_course` so the real send flow runs end to end."""
    class Ctx:
        def new_page(self):
            return page

        def close(self):
            return None

    class Browser:
        def new_context(self, **k):
            return Ctx()

        def close(self):
            return None

    class Chromium:
        def launch(self, **k):
            return Browser()

    class P:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(M, "sync_playwright", lambda: P())
    monkeypatch.setattr(M, "enter_course", lambda ctx, pg, idc, as_user=None:
                        (page, "virtual3.uach.mx"))
    monkeypatch.setattr(M, "_shot", lambda page, tag: f"{tag}.png")
    monkeypatch.setattr(M, "resolve_identity",
                        lambda u: type("I", (), {"username": u or "professor", "password": "x",
                                                 "is_self": u is None,
                                                 "describe": lambda s: "carlos"})())
    monkeypatch.setattr(M.settings, "dry_run", False)


def test_a_send_whose_navigation_times_out_is_UNKNOWN_never_failed(monkeypatch):
    """🔴 The rail that would have prevented the 1MH-B double send.

    `ok` must not be False here. False means "it did not happen", and the only sensible
    response to that is to do it again — which delivers a second copy to everyone who already
    has one. The honest value is None, carrying `delivery_unknown`.
    """
    page = _FakePage()
    _fake_browser(monkeypatch, page)
    page.fail_next_navigation = False

    # Fail only the LAST navigation — the one after the send click.
    real_expect = page.expect_navigation
    seen = []

    def expect(**kw):
        seen.append(kw)
        if len(seen) == 3:            # form → preview → send
            page.fail_next_navigation = True
        return real_expect(**kw)

    monkeypatch.setattr(page, "expect_navigation", expect)

    out = M.send_message(idc="9023", body="Hola", expected_matriculas=MATRICULAS,
                         dry_run=False)

    assert out["ok"] is None, "a timeout must not be reported as a failed send"
    assert out["delivery_unknown"] is True
    assert "NO REINTENTAR" in out["error"]
    assert out["sent_at"], "the moment of the click is recorded even when the wait dies"


def test_the_send_click_does_not_block_on_the_navigation_it_triggers(monkeypatch):
    """`no_wait_after=True` is what makes the generous navigation timeout the one that governs.

    Without it Playwright's click auto-waits for scheduled navigations using its own default
    30 s, so the outer `expect_navigation(timeout=…)` never gets a say — which is exactly how a
    90-second server-side fan-out looked like a dead host.
    """
    page = _FakePage()
    _fake_browser(monkeypatch, page)

    M.send_message(idc="9023", body="Hola", expected_matriculas=MATRICULAS, dry_run=False)

    send_clicks = [kw for sel, kw in page.clicks if 'name="send"' in sel]
    assert send_clicks, "the send button was never clicked"
    assert send_clicks[0].get("no_wait_after") is True


def test_the_send_wait_scales_with_how_many_people_are_being_written(monkeypatch):
    """Moodle writes one row per recipient inside the request, so the wait cannot be a constant
    tuned to a small group. The floor is 180 s; a big cohort gets more."""
    page = _FakePage()
    _fake_browser(monkeypatch, page)

    M.send_message(idc="9023", body="Hola", expected_matriculas=MATRICULAS, dry_run=False)

    assert page.nav_timeouts[-1] >= 180_000


def test_a_completed_send_is_still_reported_as_ok(monkeypatch):
    """The unknown branch must not swallow the ordinary success — otherwise every send would
    need a manual drawer audit and the rail would be switched off."""
    page = _FakePage()
    _fake_browser(monkeypatch, page)

    out = M.send_message(idc="9023", body="Hola", expected_matriculas=MATRICULAS,
                         dry_run=False)
    assert out["ok"] is True
    assert not out.get("delivery_unknown")


def test_a_simulacro_is_unaffected_by_the_gate(monkeypatch, session, course):
    """The gate stops delivery, not rehearsal. A rail that blocks the safe action too is a
    rail people switch off and leave off."""
    from musai.messaging import jobs
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    monkeypatch.setattr(jobs.settings, "dry_run", True)
    monkeypatch.setattr(jobs.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: None})())
    assert jobs.start(course.id, jobs.DRYRUN, purpose="aviso", body="Hola") > 0


def test_sending_needs_the_group_code_typed_exactly(session, course, monkeypatch):
    from musai.messaging import jobs
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    monkeypatch.setattr(jobs.settings, "dry_run", False)   # past rail 0, onto rail 4

    with pytest.raises(MessagingRefused, match="código del grupo"):
        jobs.start(course.id, jobs.SEND, purpose="aviso", body="Hola", confirm="")
    with pytest.raises(MessagingRefused, match="código del grupo"):
        jobs.start(course.id, jobs.SEND, purpose="aviso", body="Hola", confirm="1-LED-Z")


def test_the_typed_code_is_case_insensitive_and_that_is_deliberate(session, course,
                                                                   monkeypatch):
    """Documented rather than changed. Case-sensitivity would not have prevented the
    incident — nothing about typing invites care if the switch behind it is already open.
    The gate is DRY_RUN; this box only confirms WHICH group."""
    from musai.messaging import jobs
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    monkeypatch.setattr(jobs.settings, "dry_run", False)
    monkeypatch.setattr(jobs.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: None})())
    assert jobs.start(course.id, jobs.SEND, purpose="aviso", body="Hola",
                      confirm=course.group_code.lower()) > 0


def test_an_empty_body_is_refused_before_a_browser_exists(session, course, monkeypatch):
    from musai.messaging import jobs
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    with pytest.raises(MessagingRefused, match="vacío"):
        jobs.start(course.id, jobs.DRYRUN, purpose="aviso", body="   ")


def test_a_dry_run_does_not_need_the_confirmation(session, course, monkeypatch):
    """The gate guards the irreversible action, not the rehearsal — a rail nobody can
    practise with is a rail people learn to route around."""
    from musai.messaging import jobs
    monkeypatch.setattr(jobs, "engine", session.get_bind())
    started = {}
    monkeypatch.setattr(jobs.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda s: started.setdefault(
                            "yes", True)})())
    assert jobs.start(course.id, jobs.DRYRUN, purpose="aviso", body="Hola") > 0
