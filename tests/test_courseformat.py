"""The course-format writer, and the quiz shell — the two capabilities Moodle 4.5 needed.

Both refuse before a browser exists, which is the property worth testing: a refusal that costs
a millisecond instead of a login and a half-filled form on a colleague's live course.
"""

import inspect

import pytest

from musai.coursebuild import courseformat
from musai.coursebuild.activity import (
    ActivityRefused, ActivitySpec, ALLOWED_FIELDS, FORBIDDEN_FIELDS, plan_fields,
)
from musai.coursebuild.courseformat import (
    ALLOWED_FORMATS, FormatRefused, plan_format_change, set_course_format,
)


# ── the format writer ────────────────────────────────────────────────────────
def test_onetopic_is_planned_without_a_browser():
    assert plan_format_change(new_format="onetopic",
                              expect_shortname="EGB1B - INGLES I - 544151") == {
        "format": "onetopic"}


def test_the_way_back_is_the_same_code_path():
    """A writer that can get you into a state but not out of it is half a writer."""
    assert plan_format_change(new_format="topics", expect_shortname="X") == {"format": "topics"}


@pytest.mark.parametrize("fmt", ["singleactivity", "social", "tiles", "grid", "", "TOPICS"])
def test_formats_that_look_like_a_wipe_are_refused(fmt):
    """🔴 `singleactivity` hides every section but one and `social` replaces the course with a
    forum. Both are one typo from a course that looks destroyed, on a colleague's account."""
    with pytest.raises(FormatRefused, match="ALLOWED_FORMATS"):
        plan_format_change(new_format=fmt, expect_shortname="X")


def test_a_course_cannot_be_reformatted_without_naming_it():
    """An idc is a number. The delegated account's dashboard carries nine courses across two
    different Moodles; a number aimed at the wrong one reformats a live semester."""
    with pytest.raises(FormatRefused, match="expect_shortname"):
        plan_format_change(new_format="onetopic", expect_shortname="   ")


def test_the_allow_list_explains_itself():
    """Each entry carries the reason it is allowed, so removing one means deleting a
    sentence rather than a string."""
    assert set(ALLOWED_FORMATS) == {"onetopic", "topics"}
    assert all(isinstance(v, str) and len(v) > 20 for v in ALLOWED_FORMATS.values())


def test_dry_run_is_the_default():
    assert inspect.signature(set_course_format).parameters["dry_run"].default is True


def test_the_dry_run_never_submits():
    """⚠️ Anchored on the dry-run block's own `return`, not on a neighbouring comment. The
    first version sliced up to a comment string, and rewording that comment made the test
    raise ValueError instead of failing — a test that breaks when prose moves is testing
    the prose."""
    src = inspect.getsource(set_course_format)
    start = src.index("if dry_run:")
    dry = src[start:src.index("return out", start)]
    assert "click" not in dry, "the dry run reached a mutating control"
    assert "#id_saveanddisplay" not in dry


def _code_only(fn) -> str:
    """The function's source with comment lines dropped.

    Needed because the prose that explains a rail names the very thing the rail forbids —
    the comment above the save click says `input[type=submit]` .first is the wrong button,
    which is exactly the string a naive scan would flag as the bug it is warning about.
    """
    return "\n".join(line for line in inspect.getsource(fn).splitlines()
                     if not line.strip().startswith("#"))


def test_the_save_button_is_matched_by_id_not_position():
    """🔴 `editsection.php` carries three submits and the FIRST is the course search box.
    `course/edit.php` on 4.5 carries `updatecourseformat`, `saveanddisplay` and `cancel` —
    and no `saveandreturn` at all, so 3.3's habits do not transfer."""
    src = _code_only(set_course_format)
    assert "#id_saveanddisplay" in src
    assert 'input[type="submit"]' not in src and "input[type=submit]" not in src
    assert "saveandreturn" not in src


def test_the_readback_refetches_the_form_rather_than_reading_the_redirect():
    """A course page rendered under a new format looks different whether or not the setting
    stuck. The authoritative reading of any field is that form, re-fetched."""
    src = inspect.getsource(set_course_format)
    tail = src[src.index("Submitted"):]
    assert "course/edit.php" in tail, "the read-back does not re-fetch the settings form"
    assert tail.index("course/edit.php") < tail.index('out["ok"] =')


def test_a_timeout_is_unknown_not_failed():
    """`ok=False` invites a retry; `ok=None` invites a verification. Only one of those is
    safe when the operation may already have succeeded server-side."""
    src = inspect.getsource(set_course_format)
    block = src[src.index("except PWTimeout"):]
    assert 'out["ok"] = None' in block
    assert 'out["ok"] = False' not in block


def test_the_spanish_labels_are_recorded_but_never_used_to_find_the_option():
    """A label is a translation; a value is an identifier. The 4.5 install calls onetopic
    «Formato un_tópico», which is what a human sees and what a selector must never match."""
    assert courseformat.FORMAT_LABELS_ES["onetopic"] == "Formato un_tópico"
    # The selector matches on `o.value`; the human-facing label appears nowhere in it.
    assert "un_tópico" not in courseformat._SET_FORMAT_JS
    assert "o.value === wanted" in courseformat._SET_FORMAT_JS


# ── the quiz shell ───────────────────────────────────────────────────────────
def _quiz(**kw):
    base = dict(section=0, name="Examen Parcial 1", modname="quiz",
                intro_html="<!-- musai:block:enf-exam-1 --><p>Examen del primer parcial.</p>")
    base.update(kw)
    return ActivitySpec(**base)


def test_a_blank_quiz_can_now_be_planned():
    """The capability ENFERMERIA_PILOT §10 called impossible. Measured on the live 4.5 form:
    229 controls, and exactly two of them are ours."""
    assert plan_fields(_quiz(visible=True)) == {"name": "Examen Parcial 1", "visible": "1"}


def test_a_quiz_writes_only_name_and_visibility():
    """🔴 The omissions are the design. Attempts, time limit and grading method are exam
    policy belonging to the professor who writes the questions."""
    assert ALLOWED_FIELDS["quiz"] == frozenset({"name", "visible"})


def test_a_quiz_grade_cannot_be_set():
    """A quiz's maximum is the sum of its question marks and Moodle recomputes it. Writing a
    number here puts a value in the gradebook that the questions then contradict.

    Leaving the field alone is silent — only a caller who deliberately set it gets told."""
    with pytest.raises(ActivityRefused, match="sum of the question marks"):
        plan_fields(_quiz(grade_point=50, visible=True))
    assert plan_fields(_quiz(visible=True))["name"] == "Examen Parcial 1"


def test_quiz_dates_are_still_forbidden():
    """Dates belong to musai/coursedates, on every module type without exception."""
    with pytest.raises(ActivityRefused):
        plan_fields(_quiz(visible=True, extras={"timeopen": "1"}))
    assert "duedate" in FORBIDDEN_FIELDS


def test_a_quiz_still_needs_a_marker_so_a_rerun_does_not_duplicate_it():
    with pytest.raises(ActivityRefused, match="marker"):
        plan_fields(_quiz(intro_html="<p>no marker here</p>", visible=True))


def test_an_unknown_module_type_names_quiz_as_available_now():
    """The old refusal told the reader quizzes were out of reach. It is not true any more,
    and a message that lies about the tool's own capabilities sends people to build a
    workaround they do not need."""
    with pytest.raises(ActivityRefused) as e:
        plan_fields(_quiz(modname="glossary", visible=True))
    assert "quiz" in str(e.value)
