"""Tests for `musai/coursebuild/activity.py`.

The browser half is not unit-testable, so these pin the half that decides what gets written —
`plan_fields` — plus the boundaries that must hold *before* a browser exists. Every refusal
here is one that would otherwise be discovered by a half-filled form on a live course.
"""

import inspect

import pytest

from musai.coursebuild import activity
from musai.coursebuild.activity import (
    ALLOWED_FIELDS, FORBIDDEN_FIELDS, ActivityRefused, ActivitySpec, plan_fields,
)
from musai.coursebuild.render import marker

MARK = marker("workbook-p1")
HTML = f'{MARK}<div style="font-family: Verdana, sans-serif;"><p>Lee la página 24.</p></div>'


def spec(**kw) -> ActivitySpec:
    base = dict(section=2, name="📘 Workbook Activity: Page 24", intro_html=HTML)
    base.update(kw)
    return ActivitySpec(**base)


# --------------------------------------------------------------------------- the happy shape

def test_the_defaults_reproduce_carloss_own_workbook_activity():
    """Measured off `📘 Workbook Activity: My Daily Routine (Page 90)` on 2026-08-08."""
    f = plan_fields(spec())
    assert f["grade[modgrade_type]"] == "point"
    assert f["grade[modgrade_point]"] == "100"
    assert f["assignsubmission_file_enabled"] is True
    assert f["assignsubmission_file_maxfiles"] == "1"
    assert f["assignsubmission_onlinetext_enabled"] is False
    assert f["submissiondrafts"] == "0"


def test_online_text_submission_turns_the_file_upload_off():
    f = plan_fields(spec(submission="onlinetext"))
    assert f["assignsubmission_onlinetext_enabled"] is True
    assert f["assignsubmission_file_enabled"] is False
    assert "assignsubmission_file_maxfiles" not in f


def test_both_enables_both():
    f = plan_fields(spec(submission="both"))
    assert f["assignsubmission_file_enabled"] is True
    assert f["assignsubmission_onlinetext_enabled"] is True


# ------------------------------------------------------------------------------- the rails

def test_a_new_activity_is_created_HIDDEN_by_default():
    """Rail 4: a visible new activity raises calendar events for enrolled students. Revealing
    it is a separate, deliberate act by the professor."""
    assert plan_fields(spec())["visible"] == "0"
    assert plan_fields(spec(visible=True))["visible"] == "1"


def test_student_notifications_are_never_switched_on():
    assert plan_fields(spec())["sendnotifications"] == "0"
    assert "sendstudentnotifications" in FORBIDDEN_FIELDS


@pytest.mark.parametrize("date_field", [
    "duedate", "allowsubmissionsfromdate", "cutoffdate", "gradingduedate",
])
def test_this_module_refuses_to_write_ANY_date(date_field):
    """`musai/coursedates` owns every date field. Two writers for one field is how a course
    ends up with a deadline nobody chose — and the Cronograma is the one that is idempotent,
    verified twice and reversible."""
    with pytest.raises(ActivityRefused, match="Cronograma"):
        plan_fields(spec(extras={f"{date_field}[day]": "14"}))


def test_a_field_outside_the_allow_list_is_refused_before_a_browser_exists():
    """The assign form carries 111 named controls. Anything not deliberately listed is left
    exactly as Moodle's own default — including completion rules and every other
    assignsubmission_* plugin."""
    with pytest.raises(ActivityRefused, match="ALLOWED_FIELDS"):
        plan_fields(spec(extras={"assignsubmission_comments_enabled": "1"}))


@pytest.mark.parametrize("modname", sorted(ALLOWED_FIELDS))
def test_the_allow_list_and_the_forbidden_list_do_not_overlap(modname):
    bases = {k.split("[")[0] for k in ALLOWED_FIELDS[modname]}
    assert bases.isdisjoint(FORBIDDEN_FIELDS), "a field cannot be both allowed and forbidden"


# ------------------------------------------------------------------------------ forums (P3)

FORUM_HTML = marker("audiovisual-p3") + "<p>Comparte tu video.</p>"


def forum(**kw) -> ActivitySpec:
    base = dict(section=8, name="🎥 Audiovisual Presentation", intro_html=FORUM_HTML,
                modname="forum")
    base.update(kw)
    return ActivitySpec(**base)


def test_a_forum_without_ratings_is_REFUSED_because_that_is_the_watch_and_write_bug():
    """Measured 2026-08-08: `Watch and Write` sits at `assessed=0`, and this Moodle's forum form
    has no whole-forum grading fieldset at all — ratings are the only route to the gradebook.
    So a forum created with ratings off is graded on paper and scores nothing, which is the
    exact defect the Parcial 3 forum is replacing."""
    with pytest.raises(ActivityRefused, match="gradebook"):
        plan_fields(forum(aggregate="0"))


def test_a_forum_gets_a_gradebook_column_and_a_point_scale():
    f = plan_fields(forum())
    assert f["assessed"] == "1"                    # average of ratings
    assert f["scale[modgrade_type]"] == "point"
    assert f["scale[modgrade_point]"] == "100"


def test_the_forum_default_gives_every_student_their_own_discussion():
    """`eachuser` is the shape of "post your video, then comment on your classmates'"."""
    assert plan_fields(forum())["type"] == "eachuser"


def test_a_forum_is_also_created_hidden():
    assert plan_fields(forum())["visible"] == "0"


@pytest.mark.parametrize("date_field", ["assesstimestart", "assesstimefinish"])
def test_the_forums_rating_window_is_still_the_cronogramas_to_write(date_field):
    """Enabling ratings makes these two fields meaningful — which makes them dates, which makes
    them `musai/coursedates`' property, not this module's."""
    with pytest.raises(ActivityRefused, match="Cronograma"):
        plan_fields(forum(extras={f"{date_field}[day]": "16"}))


def test_forced_subscription_is_refused():
    """It mails every enrolled student. Rail 4: MUSAI writes to the course, not at students."""
    with pytest.raises(ActivityRefused, match="rail 4"):
        plan_fields(forum(extras={"forcesubscribe": "1"}))


def test_an_assign_field_cannot_leak_onto_a_forum():
    with pytest.raises(ActivityRefused, match="ALLOWED_FIELDS"):
        plan_fields(forum(extras={"submissiondrafts": "0"}))


def test_no_marker_means_no_write():
    """Without a marker a re-run cannot find what it made last time, so it would silently
    stack a second copy of the activity in the professor's course."""
    with pytest.raises(ActivityRefused, match="marker"):
        plan_fields(spec(intro_html="<p>sin marcador</p>"))


def test_the_marker_is_what_makes_a_rerun_idempotent():
    assert spec().marker == "workbook-p1"


def test_a_blank_name_is_refused():
    with pytest.raises(ActivityRefused, match="name"):
        plan_fields(spec(name="   "))


def test_description_html_must_pass_the_same_lint_as_every_other_block():
    with pytest.raises(ActivityRefused, match="lint"):
        plan_fields(spec(intro_html=f'{MARK}<img src="x" onerror="alert(1)">'))


def test_only_assign_is_supported_and_quizzes_say_why_not():
    with pytest.raises(ActivityRefused, match="Moodle-XML"):
        plan_fields(spec(modname="quiz"))


def test_an_unknown_submission_type_is_refused():
    with pytest.raises(ActivityRefused, match="submission"):
        plan_fields(spec(submission="carrier-pigeon"))


def test_grade_out_of_is_configurable_but_always_a_point_scale():
    f = plan_fields(spec(grade_point=10))
    assert f["grade[modgrade_point]"] == "10"
    assert f["grade[modgrade_type]"] == "point"


def test_an_assign_is_verified_by_NAME_because_its_marker_is_not_on_the_course_page():
    """🔴 Happened live: a label's HTML *is* its body so its marker comment always renders, but
    an assign's description only renders with `showdescription=1` (off by default). The first
    live run therefore warned "the marker was not visible" about three activities that had all
    been created correctly."""
    import inspect
    from musai.coursebuild import activity
    src = inspect.getsource(activity.create_activity)
    assert "_FIND_BY_NAME_JS" in src
    assert "showdescription" in src, "the reason must stay next to the workaround"
    # The section is part of the lookup: two parcials can hold same-named activities.
    assert "[spec.section, spec.name.strip()]" in src


def test_the_name_lookup_skips_moodles_shim_elements():
    """`[id^=module-]` also matches `module-<cmid>_shim` placeholders, which carry the same id
    prefix and would resolve to a cmid with no activity behind it."""
    from musai.coursebuild import activity
    assert "_shim" in activity._FIND_BY_NAME_JS


# ── explicit cmid targeting (COURSE_EDITING.md §5) ───────────────────────────

def test_a_spec_carries_no_cmid_by_default_so_the_name_lookup_still_runs():
    """The lookup is the normal path; cmid is the escape hatch, not the default. If this
    flipped, every ordinary create would need an id nobody has yet."""
    spec = ActivitySpec(section=2, name="X", intro_html=marker("x") + "<p>x</p>")
    assert spec.cmid is None


def test_cmid_targeting_does_not_widen_what_may_be_written():
    """The allow-list is the rail, and an escape hatch for *identity* must not become an
    escape hatch for *fields*. A cmid-targeted spec is planned exactly like any other."""
    plain = ActivitySpec(section=2, name="X", intro_html=marker("x") + "<p>x</p>")
    targeted = ActivitySpec(section=2, name="X", intro_html=marker("x") + "<p>x</p>",
                            cmid="1070980")
    assert plan_fields(plain) == plan_fields(targeted)

    with pytest.raises(ActivityRefused):
        plan_fields(ActivitySpec(section=2, name="X", intro_html=marker("x") + "<p>x</p>",
                                 cmid="1070980", extras={"duedate[day]": "3"}))


def test_renaming_is_the_case_the_name_lookup_cannot_serve():
    """A rename means the target's CURRENT name differs from `spec.name`, so searching the
    section for `spec.name` finds nothing and a re-run would create a duplicate instead of
    renaming. Pinning the reason the cmid branch exists, not just that it exists."""
    src = inspect.getsource(activity.create_activity)
    assert "if spec.cmid:" in src
    # the cmid branch must come BEFORE the marker/name lookup, or the lookup wins
    assert src.index("if spec.cmid:") < src.index("_FIND_BY_NAME_JS")


def test_a_cmid_targeted_edit_records_what_it_found_before_typing():
    """A mistyped cmid loads a valid form for the WRONG activity and every later check still
    passes. Reading the previous name is the only evidence the id meant what the caller
    thought — so it must be captured before any field is set."""
    src = inspect.getsource(activity.create_activity)
    assert 'out["previous_name"]' in src
    assert src.index('out["previous_name"]') < src.index("for name, value in fields.items()")
