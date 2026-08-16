"""🔴 The AI assistant answers about YOUR groups and nobody else's.

The leak this file closes, found 2026-08-16 while adding help topics to the same tool set:
every tool in `musai/assistant/tools.py` read the whole database. `list_groups` went through
`semesters.courses_in` — *every* course in the semester, whoever owns it. `student_status`
opened with `select(Student)`, i.e. the entire table. `group_status('1-LED-A')` matched a group
code against all courses, and group codes are faculty-wide: two professors teach `1-LED-A`.

**Why the 2026-08-14 sweep missed it.** That audit found 22 unscoped handlers and produced
`test_route_scoping.py`, which walks `app.routes` — deliberately, so a new route is covered the
day it is added. A Gemini tool is not a route. `/assistant/ask` takes no `course_id`, so there
was nothing for that test to walk, and the surface with the widest read in the app sat outside
the net that was built to catch exactly this. *Scope the system, not the file.*

So this file enumerates from `tools_for()` rather than naming the six tools: a seventh tool
added tomorrow is covered by `test_every_tool_is_bound_to_the_professor` without anyone
remembering this file exists.

The rail, stated once: **`professor_id=None` means nobody, never everybody.** An unowned course
matches no one. Failing towards "you see nothing" is a bug report; failing the other way is a
breach. See `feedback_unscoped_query_is_a_leak` and `feedback_scope_the_system_not_the_file`.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine

from musai.assistant import tools as T
from musai.models import (Course, Enrollment, Partial, PartialGrade, Professor, Semester,
                          Student)
from musai.professors import by_email, get_or_create

OWNER = "professor@uach.mx"
COLLEAGUE = "colleague4@uach.mx"


@pytest.fixture
def two_professors(tmp_path, monkeypatch):
    """Two professors, each with a group of the SAME code, each with one student.

    🔴 The shared group code is the point. `1-LED-A` is not the owner's name for his course, it
    is the faculty's name for a slot — so an unscoped lookup does not merely leak, it silently
    answers with whichever row was inserted first. A fixture giving them distinct codes would
    pass against the leaking version.

    On a FILE, not `:memory:` — a `StaticPool` in-memory engine is one connection shared by
    reader and writer, and the reader ends the writer's transaction. Measured 3/300 flaky on
    memory, 0/300 on a file. See `feedback_a_flaky_test_is_a_finding`.
    """
    url = f"sqlite:///{tmp_path / 'scoping.db'}"
    eng = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    # Both engines the tools might reach are pointed at this database. `ro_engine` is what the
    # tools actually open; pinning `engine` too keeps a helper that grabs the wrong one honest.
    monkeypatch.setattr("musai.db.ro_engine", eng)
    monkeypatch.setattr("musai.assistant.tools.ro_engine", eng)

    with Session(eng) as s:
        sem = Semester(name="2026-2", starts_on=date(2026, 7, 1), ends_on=date(2026, 12, 31))
        s.add(sem)
        s.commit()
        s.refresh(sem)

        made = {}
        for email, student_name, matricula in (
            (OWNER, "OWNER STUDENT", "111111"),
            (COLLEAGUE, "COLLEAGUE STUDENT", "222222"),
        ):
            prof = get_or_create(s, email=email, full_name=email)
            course = Course(professor_id=prof.id, semester_id=sem.id, subject="Inglés I",
                            level=1, group_code="1-LED-A")   # ← the same code, on purpose
            s.add(course)
            s.commit()
            s.refresh(course)
            p = Partial(course_id=course.id, name="Parcial 1", sega_evaluacion="1")
            st = Student(full_name=student_name, matricula=matricula)
            s.add(p)
            s.add(st)
            s.commit()
            s.refresh(p)
            s.refresh(st)
            s.add(Enrollment(student_id=st.id, course_id=course.id))
            s.add(PartialGrade(student_id=st.id, partial_id=p.id,
                               value_0_10=5.0, sega_value=5.0))
            s.commit()
            made[email] = {"prof_id": prof.id, "course_id": course.id, "student": student_name}
        s.commit()
    yield made
    eng.dispose()


def _named(professor_id):
    return {f.__name__: f for f in T.tools_for(professor_id)}


# ---------------------------------------------------------------------------
# The tool set is a factory, and the owner is not an argument the model can set
# ---------------------------------------------------------------------------

def test_there_is_no_unscoped_tool_set_to_import():
    """The old module-level `TOOLS` is gone, not deprecated.

    Leaving it importable would leave the leaking version one `from … import TOOLS` away, and
    that is the line somebody writes when they are wiring a new surface in a hurry.
    """
    assert not hasattr(T, "TOOLS"), "a module-level tool set is an unscoped tool set"
    with pytest.raises(TypeError):
        T.tools_for()            # the owner is required, never defaulted


def test_the_tool_signatures_survive_schema_building():
    """🔴 Gemini reads these signatures to build its function schema. Strings are not types.

    Found by a real call, 2026-08-16, and worth recording because of the shape it failed in.
    Adding `from __future__ import annotations` to `tools.py` turned every annotation into a
    string. Each tool still worked perfectly when called directly — every unit test passed —
    but the SDK could not resolve the type of a REQUIRED parameter and raised
    `isinstance() arg 2 must be a type`. `list_groups()` and `list_semesters()`, which have no
    required parameter, kept working, so the failure looked intermittent and feature-specific.

    What the professor saw: *"the tools are experiencing an internal server error"*, from an
    assistant whose tools all pass their tests. A defect that lives between the signature and
    the SDK is invisible to a test that calls the function.

    ⚠️ **Assert on the RAW signature, not on `typing.get_type_hints`.** The first version of
    this test used `get_type_hints` and passed cleanly with the bug re-introduced, because
    resolving stringified annotations back into types is exactly what that function is for. The
    SDK's own `FunctionDeclaration.from_callable` tolerates them too. What does not is the
    argument coercion at call time — so the only faithful check is the one that looks at what
    `inspect` reports before anyone helpfully resolves it.

    Verified causally rather than reasoned: same question, same model, twice. With the future
    import, `group_status` returned the isinstance error; without it, *"no grades have been
    registered yet for Parcial 1 (0 out of 33 students graded)"*.
    """
    for fn in T.tools_for(1):
        for name, param in inspect.signature(fn).parameters.items():
            assert not isinstance(param.annotation, str), (
                f"{fn.__name__}({name}) is annotated with the STRING "
                f"{param.annotation!r}, not a type. Something added `from __future__ import "
                f"annotations` to musai/assistant/tools.py — see the comment at its top.")


def test_every_tool_is_bound_to_the_professor():
    """No tool exposes `professor_id` in its signature — the model cannot name someone else.

    Enumerated from `tools_for()`, so tool number seven is covered without editing this test.
    """
    tools = T.tools_for(1)
    assert tools, "the factory returned nothing"
    for fn in tools:
        params = inspect.signature(fn).parameters
        assert "professor_id" not in params, (
            f"{fn.__name__} lets the caller choose whose data to read")
        assert fn.__doc__ and fn.__doc__.strip(), (
            f"{fn.__name__} has no docstring — the docstring IS the model's contract")


# ---------------------------------------------------------------------------
# What each professor actually sees
# ---------------------------------------------------------------------------

def test_a_shared_group_code_resolves_to_your_own_course(two_professors):
    """Both professors ask about `1-LED-A`. Each gets their own students, not the first row."""
    owner = _named(two_professors[OWNER]["prof_id"])
    colleague = _named(two_professors[COLLEAGUE]["prof_id"])

    assert owner["at_risk"]("1-LED-A")["students"][0]["name"] == "OWNER STUDENT"
    assert colleague["at_risk"]("1-LED-A")["students"][0]["name"] == "COLLEAGUE STUDENT"


def test_a_colleagues_student_is_not_findable_by_name(two_professors):
    """🔴 The roster search. `student_status` used to read the whole `student` table."""
    owner = _named(two_professors[OWNER]["prof_id"])

    assert owner["student_status"]("OWNER STUDENT")["name"] == "OWNER STUDENT"

    hidden = owner["student_status"]("COLLEAGUE STUDENT")
    assert "error" in hidden, "the assistant found a student who is not this professor's"
    assert "name" not in hidden, "even the refusal must not confirm the student exists"


def test_a_colleagues_student_is_not_findable_by_matricula(two_professors):
    """A matrícula is guessable — they are sequential — so the id path needs its own test."""
    owner = _named(two_professors[OWNER]["prof_id"])
    assert "error" in owner["student_status"]("222222")
    assert owner["student_status"]("111111")["matricula"] == "111111"


def test_the_group_list_holds_only_your_own_groups(two_professors):
    for email in (OWNER, COLLEAGUE):
        groups = _named(two_professors[email]["prof_id"])["list_groups"]()
        assert len(groups) == 1, f"{email} sees {len(groups)} groups; they own 1"


def test_group_status_refuses_a_group_you_do_not_own(two_professors, tmp_path):
    """A professor with no groups at all asks about one that exists. The answer is a refusal."""
    with Session(T.ro_engine) as s:
        stranger = get_or_create(s, email="stranger@uach.mx", full_name="stranger")
        stranger_id = stranger.id

    out = _named(stranger_id)["group_status"]("1-LED-A")
    assert "error" in out and "partials" not in out
    assert "you have no group" in out["error"].lower()


# ---------------------------------------------------------------------------
# None is nobody
# ---------------------------------------------------------------------------

def test_an_unresolvable_actor_reads_nothing(two_professors):
    """🔴 The failure direction. `tools_for(None)` must be empty-handed, not all-seeing.

    `None` is what a script, a typo, or the legacy `web:carlos` actor resolves to. The tempting
    reading — "no professor filter, so no filter" — is the leaking version of this exact line.
    """
    nobody = _named(None)
    assert nobody["list_groups"]() == []
    assert "error" in nobody["student_status"]("OWNER STUDENT")
    assert "error" in nobody["group_status"]("1-LED-A")
    assert "error" in nobody["at_risk"]("1-LED-A")
    assert nobody["partial_trend"]("1-LED-A").get("error")


def test_the_semester_list_is_the_calendar_plus_your_own_history(two_professors):
    """The one thing an unowned tool set still answers, and why that is not a leak.

    `list_semesters` returns the semesters the professor has courses in, PLUS the current one —
    the same rule as the cockpit's semester picker, which exists so a professor signing in for
    the first time is not offered an empty dropdown. A semester is a calendar row: a name and
    two dates the faculty publishes. It carries no course, no roster and no grade, and knowing
    that it is 2026-2 tells you nothing about who teaches what.

    Pinned here rather than left implicit, because "reads nothing" is the rail and this is the
    documented exception to it. An exception nobody wrote down is the one that grows.
    """
    for who in (None, two_professors[OWNER]["prof_id"]):
        rows = _named(who)["list_semesters"]()
        assert [r["semester"] for r in rows] == ["2026-2"]
        assert set(rows[0]) == {"semester", "starts_on", "ends_on", "is_current"}


def test_the_legacy_actor_is_not_a_professor(two_professors):
    """`web:carlos` is a billing key, not an address. It must resolve to nobody."""
    from musai.assistant.agent import ACTOR

    with Session(T.ro_engine) as s:
        assert by_email(s, ACTOR) is None


# ---------------------------------------------------------------------------
# The other surface that shares this tool set
# ---------------------------------------------------------------------------

def test_susai_hands_the_coordinator_a_bound_tool_set(two_professors, monkeypatch):
    """SUSAI's coordinator brain reuses these tools over WhatsApp.

    Its docstring claimed single-tenancy for two days after that stopped being true. The number
    check (`susai_admin_phone`) proves who is texting; it says nothing about whose rows those
    are, so the binding has to happen here too.
    """
    from musai.config import settings
    from musai.susai import prof_agent

    monkeypatch.setattr(settings, "admin_email", OWNER)
    tools = {f.__name__: f for f in prof_agent._tools()}
    assert tools["list_groups"]()[0]["group_code"] == "1-LED-A"
    assert tools["student_status"]("COLLEAGUE STUDENT").get("error")


def test_susai_finds_nothing_when_the_owner_has_no_row(two_professors, monkeypatch):
    """A fresh database, before the owner has ever signed in. Empty, not unfiltered."""
    from musai.config import settings
    from musai.susai import prof_agent

    monkeypatch.setattr(settings, "admin_email", "nobody@uach.mx")
    tools = {f.__name__: f for f in prof_agent._tools()}
    assert tools["list_groups"]() == []
