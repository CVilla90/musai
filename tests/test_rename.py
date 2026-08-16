"""Tests for `musai/coursebuild/rename.py` — change an activity's name and nothing else.

The module exists because `create_activity` refuses `modname='quiz'` and always rewrites the
description. So the thing worth pinning is not "does it type into a field" — it is the set of
promises that make it safe to point at a module type this project does not otherwise understand:

* it writes **one** control,
* it refuses a name that is already taken inside the section (the AMBIGUOUS-mapping hazard),
* it refuses a label, which has no name field to write, and
* it never learns to touch the fields `musai/coursedates` owns.
"""

import ast
import inspect

import pytest

from musai.coursebuild import activity, rename
from musai.coursebuild.rename import (
    NO_NAME_FIELD, RenameRefused, WATCHED_FIELDS, plan_rename, rename_activity, watched_diff,
)

# The exact controls 9067's quiz 1119440 rendered on 2026-08-12, before and after the rename.
# `timeclose` is DISABLED, and Moodle fills a disabled date group with the current wall-clock
# time — so the minute ticked over between the two reads while nothing was stored.
_BEFORE = {"timeopen[enabled]": "1", "timeopen[year]": "2026", "timeopen[month]": "1",
           "timeopen[day]": "26", "timeopen[hour]": "0", "timeopen[minute]": "0",
           "timeclose[enabled]": "0", "timeclose[year]": "2026", "timeclose[month]": "8",
           "timeclose[day]": "12", "timeclose[hour]": "12", "timeclose[minute]": "32"}
_AFTER = dict(_BEFORE, **{"timeclose[minute]": "33"})

# The real First Term section of 9067, read live 2026-08-12 — the eight quizzes step 2 corrects.
_SECTION_3 = {
    "1119432": "Simple Present I", "1119433": "Simple Present II",
    "1119434": "Simple Present III", "1119435": "Simple Present IV",
    "1119436": "Simple Present V", "1119437": "Simple Present VI",
    "1119439": "Present Continuous I", "1119440": "Present Continuous II",
}


# --- the pure rails ------------------------------------------------------------------------

def test_the_english_iii_renames_all_pass_once_the_duplicates_are_gone():
    """The real plan: the four duplicates are deleted first, so the survivors' new names are
    free. This is the ordering `ENGLISH_III.md` §2 calls 'delete before rename'."""
    survivors = {c: n for c, n in _SECTION_3.items()
                 if c not in {"1119434", "1119435", "1119436", "1119437"}}
    plan = [("1119432", "Simple Present"), ("1119433", "Adverbs of Frequency"),
            ("1119439", "Present Continuous: concepts"),
            ("1119440", "Present Continuous: -ing spelling")]
    for cmid, new in plan:
        assert plan_rename(cmid=cmid, current_name=survivors[cmid], new_name=new,
                           modname="quiz", section_names=survivors) is None


def test_renaming_before_deleting_the_duplicates_is_refused():
    """🔴 The exact hazard that fixes the phase order. `Simple Present III` is a byte-identical
    copy of `Simple Present I`; renaming I to `Simple Present` while III still exists is fine,
    but renaming III to it too would leave two — and `mapping.py` then assigns NO partial_id.
    """
    named = dict(_SECTION_3, **{"1119432": "Simple Present"})
    why = plan_rename(cmid="1119434", current_name="Simple Present III",
                      new_name="Simple Present", modname="quiz", section_names=named)
    assert why is not None and "AMBIGUOUS" in why


def test_a_rename_to_its_own_current_name_is_allowed_so_a_rerun_is_idempotent():
    named = dict(_SECTION_3, **{"1119432": "Simple Present"})
    assert plan_rename(cmid="1119432", current_name="Simple Present",
                       new_name="Simple Present", modname="quiz", section_names=named) is None


def test_a_label_is_refused_because_it_has_no_name_field_to_write():
    """Measured on 9048: a label's `modedit.php` carries no `input[name="name"]` at all."""
    why = plan_rename(cmid="1119431", current_name="", new_name="Simple present",
                      modname="label", section_names={})
    assert why is not None and "name field" in why
    assert "label" in NO_NAME_FIELD


def test_a_blank_new_name_is_refused():
    assert plan_rename(cmid="1", current_name="x", new_name="   ", modname="quiz",
                       section_names={}) is not None


def test_renaming_without_naming_the_target_is_refused_before_a_browser_opens():
    """Same rail as `delete_activity`: a mistyped cmid loads a perfectly valid form for the
    WRONG activity, and every later check would pass."""
    with pytest.raises(RenameRefused, match="expect_name"):
        rename_activity(idc="9067", section=3, cmid="1119432", new_name="Simple Present",
                        expect_name="")


def test_an_empty_new_name_is_refused_before_a_browser_opens():
    with pytest.raises(RenameRefused, match="blank|empty"):
        rename_activity(idc="9067", section=3, cmid="1119432", new_name="  ",
                        expect_name="Simple Present I")


# --- the date-watch, which fired on a clock and had to be made precise ---------------------

def test_a_disabled_date_group_ticking_over_is_not_a_change():
    """🔴 The live miss, 2026-08-12. Three of four renames reported `dates_unchanged=True` and
    the fourth reported a change — differing only in how long the save happened to take."""
    assert watched_diff(_BEFORE, _AFTER) == {}


def test_a_real_move_on_an_enabled_date_is_still_reported():
    """The rail is made precise, never permissive: `timeopen` IS enabled on these quizzes, so
    a change there is exactly what this exists to catch."""
    moved = dict(_BEFORE, **{"timeopen[month]": "8"})
    assert watched_diff(_BEFORE, moved) == {"timeopen[month]": ("1", "8")}


def test_turning_a_date_on_or_off_always_counts():
    """The enable checkbox is the state. Switching `timeclose` on writes a real close date onto
    a quiz that had none — the single most consequential thing a stray save could do here."""
    assert "timeclose[enabled]" in watched_diff(
        _BEFORE, dict(_BEFORE, **{"timeclose[enabled]": "1"}))
    assert "timeopen[enabled]" in watched_diff(
        _BEFORE, dict(_BEFORE, **{"timeopen[enabled]": "0"}))


def test_a_group_switched_on_reports_the_values_that_came_with_it():
    """Enabling `timeclose` and setting a date is one act; reporting only the checkbox would
    hide which date got written."""
    on = dict(_BEFORE, **{"timeclose[enabled]": "1", "timeclose[month]": "11",
                          "timeclose[day]": "22"})
    changed = watched_diff(_BEFORE, on)
    assert set(changed) == {"timeclose[enabled]", "timeclose[month]", "timeclose[day]"}


def test_a_field_with_no_enable_checkbox_is_compared_normally():
    """Not every watched control is a Moodle date group; an unknown one must not be silently
    skipped just because it has no `[enabled]` sibling."""
    assert watched_diff({"duedate": "a"}, {"duedate": "b"}) == {"duedate": ("a", "b")}


# --- what the module is not allowed to become ----------------------------------------------

def test_it_writes_exactly_one_control():
    """The safety argument for pointing this at a quiz is that every other setting on the form
    — review options, grading method, attempt limits — is submitted back exactly as Moodle
    rendered it. That holds only while this module writes one field."""
    src = inspect.getsource(rename.rename_activity)
    tree = ast.parse(src)
    fills = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in {"fill", "type", "check", "uncheck", "select_option",
                                 "set_input_files"}]
    assert len(fills) == 1, f"expected one form write, found {len(fills)}"
    assert 'input[name="name"]' in src


def test_it_never_writes_the_fields_coursedates_owns():
    """COURSE_EDITING §4: no two writers may touch the same field. `timeopen`/`timeclose`
    belong to `musai/coursedates`; here they are read twice, as evidence, and never set.

    The check is on the JS, because that is the only place in this module that could reach a
    control at all: `_WATCHED_JS` must read every element it touches and assign to none.
    """
    assert set(WATCHED_FIELDS) >= {"timeopen", "timeclose"}
    js = rename._WATCHED_JS
    assert "out[el.getAttribute('name')] =" in js, "the watched fields are collected, not set"
    for forbidden in ("el.value =", "el.click()", "el.checked =", "dispatchEvent"):
        assert forbidden not in js, f"_WATCHED_JS must not {forbidden}"


def test_the_watched_field_names_are_exact_never_a_pattern():
    """🔴 COURSE_EDITING §4: *never pattern-match `*open` / `*closed` on a quiz form* — it also
    carries `attemptopen`-shaped names, and a prefix match would report the wrong control as
    the one that moved."""
    for name in WATCHED_FIELDS:
        assert name.isidentifier(), f"{name!r} looks like a pattern, not a field name"
    assert "attemptopen" not in WATCHED_FIELDS


def test_it_refuses_a_stale_section_number():
    """Everything in this course is being renumbered by inserts and section deletes. A rename
    aimed at a section the activity has moved out of must fail, not edit the neighbour."""
    src = inspect.getsource(rename.rename_activity)
    assert "is not inside section" in src


def test_the_clash_rail_runs_before_the_form_is_ever_opened():
    """Ordering, checked on the parsed body rather than by grepping prose — one test in this
    suite once passed because it matched its own docstring."""
    src = (inspect.getsource(rename.rename_activity))
    tree = ast.parse(src)
    fn = tree.body[0]
    clash_line = min(n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and "AMBIGUOUS" in n.value)
    modedit_line = min(n.lineno for n in ast.walk(fn)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and "modedit.php?update=" in n.value)
    assert clash_line < modedit_line


def test_it_defaults_to_a_dry_run():
    sig = inspect.signature(rename_activity)
    assert sig.parameters["dry_run"].default is True


def test_it_audits_even_when_it_refuses():
    """The audit row must outlive the attempt, including the refused ones."""
    src = inspect.getsource(rename.rename_activity)
    assert "finally:" in src and "_audit(" in src


def test_a_dry_run_never_reaches_the_save():
    src = (inspect.getsource(rename.rename_activity))
    tree = ast.parse(src)
    fn = tree.body[0]
    dry_return = max(n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and n.value.startswith("DRY RUN"))
    submit = min(n.lineno for n in ast.walk(fn)
                 if isinstance(n, ast.Constant) and n.value == "#id_submitbutton2"
                 and n.lineno > dry_return)
    assert dry_return < submit


def test_the_rename_did_not_get_bolted_onto_create_activity():
    """`create_activity` refuses quizzes on purpose — it requires `intro_html` and always
    writes it, so a rename routed through it would replace the professor's description."""
    assert not hasattr(activity, "rename_activity")
    assert "quiz" not in activity._BUILDERS
