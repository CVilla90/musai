"""The fast copy lane (`/backup/import.php`), and the one rail it needs that restore does not.

`restore.py` and `course_import.py` do the same job with opposite dangers, and getting that
backwards is the way this module hurts someone:

    restore  WIPES the target first  → dangerous, but self-correcting. One right restore fixes
                                        a wrong one.
    import   MERGES into the target  → looks gentler, and is worse to undo. A second copy of 79
                                        activities, each with its own gradebook item, is hand
                                        work across every section of a live course.

So the tests below are mostly about **refusing**, and specifically about refusing a target that
already has content. Everything here is pure — `plan_import` takes numbers and strings and
raises before a browser exists, which is the house shape (`CLAUDE.md`) and the reason these
refusals can be tested at all without touching Moodle.

⚠️ What is deliberately NOT tested here: the wizard selectors past the course chooser. They
have never been driven against a live Moodle, they are marked as such in the module, and
`probe_wizard()` is the read-only thing that turns them into measurements. A test asserting
that a guessed selector equals itself would report confidence this code has not earned.
"""

import pytest

from musai.automation.course_import import (
    EMPTY_COURSE_ACTIVITIES,
    ImportAborted,
    plan_import,
)

SRC, TGT = "9067", "9072"
III_A = "INGLES III · 3ED-A · 2026-2"
III_B = "INGLES III · 3ED-B · 2026-2"


def _plan(**over):
    kw = dict(source_idc=SRC, target_idc=TGT, source_name=III_A, target_name=III_B,
              target_activities=1)
    kw.update(over)
    return plan_import(**kw)


# ---------------------------------------------------------------------------
# The rail this module exists for
# ---------------------------------------------------------------------------

def test_a_target_that_already_has_content_is_refused():
    """🔴 The whole point. Import ADDS; it does not replace.

    94 activities in, 94 more on top, every one of them duplicated in the gradebook. This is
    the failure that has no undo button, so it is a refusal and not a warning.
    """
    with pytest.raises(ImportAborted) as e:
        _plan(target_activities=94)
    assert "94 activities" in str(e.value)
    assert "does not replace" in str(e.value)


def test_a_fresh_course_with_only_its_default_forum_is_allowed():
    """A brand-new Moodle course ships with `Avisos`. Counting that as content would refuse
    every legitimate first import — a rail that cries wolf at the door is not a rail."""
    plan = _plan(target_activities=EMPTY_COURSE_ACTIVITIES)
    assert plan["merge"] is False


def test_an_empty_course_is_allowed():
    assert _plan(target_activities=0)["merge"] is False


def test_a_merge_is_possible_but_only_when_asked_for_by_name():
    plan = _plan(target_activities=94, allow_merge=True)
    assert plan["merge"] is True, "the plan must SAY it is a merge, not silently become one"


def test_an_uncountable_target_is_refused_not_assumed_empty():
    """🔴 A count that could not be taken is UNKNOWN, and unknown fails towards refusing.

    The tempting bug is `if target_activities and target_activities > 1` — `None` is falsy, so
    a course whose activities could not be read sails straight through the one check that
    protects it. Same family as `carries_user_data()` returning None for "could not tell".
    """
    with pytest.raises(ImportAborted) as e:
        _plan(target_activities=None)
    assert "cannot be shown to be empty" in str(e.value)


def test_an_uncountable_target_can_still_be_forced():
    assert _plan(target_activities=None, allow_merge=True)["source_idc"] == SRC


# ---------------------------------------------------------------------------
# Targeting
# ---------------------------------------------------------------------------

def test_a_course_cannot_be_imported_into_itself():
    """Moodle would accept it and double every activity in the course."""
    with pytest.raises(ImportAborted) as e:
        _plan(target_idc=SRC, target_name=III_A)
    assert "same course" in str(e.value)


def test_both_ids_are_required():
    for kw in ({"source_idc": ""}, {"target_idc": ""}, {"source_idc": "   "}):
        with pytest.raises(ImportAborted):
            _plan(**kw)


def test_a_subject_mismatch_is_refused():
    """The wrong level copied into a live course is the mistake that costs a colleague a term."""
    with pytest.raises(ImportAborted) as e:
        _plan(source_name="INGLES I · 1ED-A", target_name="INGLES III · 3ED-B")
    assert "Subject mismatch" in str(e.value)


def test_the_subject_match_is_longest_first():
    """🔴 `INGLES I` is a prefix of `INGLES III`. A naive `(I|II|III)` alternation matches the
    "I" inside "INGLES III" and reports an Inglés I source as belonging in an Inglés III
    course — which is exactly the copy this refuses."""
    with pytest.raises(ImportAborted):
        _plan(source_name="INGLES I grupo A", target_name="INGLES III grupo B")
    assert _plan(source_name="INGLES III A", target_name="INGLES III B")["subject"] == "INGLES III"


def test_an_expected_name_that_does_not_match_is_refused():
    with pytest.raises(ImportAborted) as e:
        _plan(expect_target_name="4EF-A")
    assert "Target check failed" in str(e.value)


def test_an_expected_name_that_matches_passes():
    assert _plan(expect_target_name="3ED-B")["target_idc"] == TGT


def test_an_expected_name_against_an_unreadable_target_is_refused():
    with pytest.raises(ImportAborted) as e:
        _plan(target_name=None, expect_target_name="3ED-B", allow_merge=True)
    assert "unverifiable target is a refused target" in str(e.value)


# ---------------------------------------------------------------------------
# Acting as another professor
# ---------------------------------------------------------------------------

def test_acting_as_another_professor_requires_naming_the_course():
    """Their account can open every course they teach, in every school. `strict` is set from
    `--as-user`, and it turns every "could not tell" into a refusal."""
    with pytest.raises(ImportAborted) as e:
        _plan(strict=True)
    assert "requires --expect-name" in str(e.value)


def test_strict_refuses_a_pairing_whose_subject_it_cannot_read():
    with pytest.raises(ImportAborted) as e:
        _plan(source_name="Some course", target_name="Another course",
              expect_target_name="Another", strict=True)
    assert "unverifiable pairing is a refused one" in str(e.value)


def test_strict_accepts_a_pairing_it_can_fully_verify():
    assert _plan(expect_target_name="3ED-B", strict=True)["subject"] == "INGLES III"


# ---------------------------------------------------------------------------
# What the plan reports
# ---------------------------------------------------------------------------

def test_the_plan_carries_what_was_actually_read():
    """The plan is what the audit row and the operator both read, so it holds the live values
    rather than the arguments someone believed."""
    plan = _plan(target_activities=0)
    assert plan["source_idc"] == SRC and plan["target_idc"] == TGT
    assert plan["source_name"] == III_A and plan["target_name"] == III_B
    assert plan["target_activities"] == 0
    assert plan["subject"] == "INGLES III"


def test_ids_are_normalised_so_a_stray_space_is_not_a_different_course():
    plan = _plan(source_idc=" 9067 ", target_idc="9072 ")
    assert (plan["source_idc"], plan["target_idc"]) == ("9067", "9072")


def test_the_planner_never_touches_a_browser(monkeypatch):
    """🔴 The property that makes every refusal above cheap: `plan_import` must be reachable
    with Playwright uninstalled. A rail that needs a login to fire is a rail that fires after
    the expensive, interactive part has already happened."""
    import musai.automation.course_import as ci

    def explode(*a, **k):
        raise AssertionError("plan_import must not start a browser")

    monkeypatch.setattr(ci, "sync_playwright", explode)
    assert _plan(target_activities=0)["merge"] is False
