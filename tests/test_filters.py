"""`musai/coursebuild/filters.py` — the one course *setting* MUSAI may write.

Six filters share one form and one Save button, so the interesting tests are not about the
value we mean to write. They are about the five we do not.
"""

import inspect

import pytest

from musai.coursebuild import filters
from musai.coursebuild.filters import (
    ALLOWED_STATES, FilterRefused, OFF, WRITABLE, plan_filter_changes, set_course_filters,
)


def test_the_autolink_filter_can_be_turned_off():
    assert plan_filter_changes({"activitynames": OFF}) == {"activitynames": "-1"}


def test_the_media_filter_cannot_be_touched_at_all():
    """🔴 The reason the allow-list exists. `mediaplugin` is what turns a bare YouTube URL
    into an embedded player, so it is what makes the 21 chapters of English II's term books
    carry video. Switching it off raises nothing, logs nothing and renders no error — the
    books simply stop having videos in them, and nobody finds out until a student opens one."""
    with pytest.raises(FilterRefused, match="mediaplugin"):
        plan_filter_changes({"mediaplugin": OFF})
    assert "mediaplugin" not in WRITABLE


@pytest.mark.parametrize("name", ["mathjaxloader", "algebra", "multilang", "tex",
                                  "mediaplugin", "emoticon"])
def test_every_other_filter_on_that_page_is_refused(name):
    with pytest.raises(FilterRefused):
        plan_filter_changes({name: OFF})


def test_a_filter_cannot_be_switched_ON_by_this_module():
    """Turning a filter on course-wide has effects nobody here has measured. This module
    exists to switch one specific thing off, and `1` is deliberately not an allowed state."""
    assert "1" not in ALLOWED_STATES
    with pytest.raises(FilterRefused, match="allowed state"):
        plan_filter_changes({"activitynames": "1"})


def test_zero_is_not_off_and_the_refusal_says_so():
    """🔴 The trap that makes every course in this project carry the defect while every select
    reads `0`: `0` is «Por defecto (Activado)», i.e. inherit the site default, and the site
    default is ON. Off is `-1`. A reader who assumes 0/1 gets a no-op that reports success."""
    assert ALLOWED_STATES["0"].startswith("Por defecto")
    assert OFF == "-1"
    with pytest.raises(FilterRefused, match="site default"):
        plan_filter_changes({"activitynames": "9"})


def test_an_empty_change_set_is_refused():
    with pytest.raises(FilterRefused, match="No filter changes"):
        plan_filter_changes({})


def test_the_allow_list_is_checked_before_a_browser_is_launched():
    """`set_course_filters` calls `plan_filter_changes` on its first line, so a bad request
    costs nothing and cannot half-happen. Pinned by source order rather than by running it,
    because running it would open a browser."""
    src = inspect.getsource(set_course_filters)
    body = src.split(":\n", 1)[1]
    assert body.index("plan_filter_changes") < body.index("sync_playwright")


def test_it_refuses_a_bad_filter_without_touching_the_network():
    with pytest.raises(FilterRefused):
        set_course_filters(idc="9048", changes={"tex": OFF}, dry_run=True)


def test_the_module_never_writes_the_site_wide_filter_page():
    """`filter/manage.php` with no contextid is the SITE filter page — the same path, and it
    would change every course on this Moodle. The context id is read off the course page and
    the run refuses if it is missing, rather than falling back to a bare URL."""
    src = inspect.getsource(filters)
    assert "filter/manage.php?contextid={contextid}" in src
    assert "filter/manage.php\"" not in src and "filter/manage.php'" not in src


def test_acting_as_another_professor_reaches_the_login_and_not_just_the_signature():
    """🔴 `as_user` was added 2026-08-12 so English III's filter could be turned off in Colleague A's,
    Colleague B's and Colleague C's courses — the owner's account cannot open their filter page at all.

    The dangerous failure is not an error: it is a parameter that is accepted, ignored, and the
    run silently proceeds **as the owner**, reporting success while reading his own course. So this
    pins that the value reaches `enter_course`, which is the only thing that chooses an identity
    (and which refuses rather than falling back — `credentials.resolve`).
    """
    src = inspect.getsource(set_course_filters)
    assert "as_user: str | None = None" in src
    assert "enter_course(ctx, ctx.new_page(), idc, as_user=as_user)" in src


def test_the_result_can_tell_an_audit_row_who_it_ran_as():
    """Moodle records the ACCOUNT; only MUSAI can record that the human who decided was someone
    else. `restore.py` puts `as_user` into the audit row's `on_behalf_of` and this writer has to
    be able to supply the same field, so it is returned rather than only accepted."""
    src = inspect.getsource(set_course_filters)
    head = src[:src.index("log.header")]
    assert '"as_user": as_user' in head


def test_collateral_changes_are_a_failure_not_a_warning():
    """One form, one Save, six selects. If the save moves a filter this run never targeted,
    that is the dangerous outcome, so it raises instead of appearing in a log line."""
    src = inspect.getsource(set_course_filters)
    assert "collateral" in src
    after = src[src.index("collateral = {"):]
    assert "raise FilterRefused" in after[:600]
