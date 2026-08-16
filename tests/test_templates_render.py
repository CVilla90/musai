"""Every template compiles, and every course page really sits inside the course shell.

Two different failures, both silent without this file:

1. **A template that does not compile** only shows up when someone loads that exact page. The
   tab strip landed on nine templates at once on 2026-08-14; a typo in the seventh would have
   waited until a professor clicked the seventh tab.
2. **A course page that forgets to extend `course_base.html`** renders perfectly — it just has
   no tab strip and no course identity on it. That is the failure DESIGN_DIRECTION §3 exists to
   prevent (*"a professor must never have to guess which course a destructive button belongs
   to"*), and it looks fine in a screenshot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "musai" / "web" / "templates"

#: Pages about one course. Each must extend the shell, name its tab and declare its accent.
COURSE_PAGES = [
    "course_overview.html", "course_activities.html", "course_grades.html",
    "course_transfer.html", "course_dates.html", "course_hub.html",
    "course_messages.html", "partial_grades.html", "sega_dryrun.html",
    "course_build.html",
]

#: Accent keys `base.html` defines a `[data-feature]` block for. A page declaring anything else
#: silently falls back to the cockpit indigo, which looks deliberate and is not.
FEATURES = {"courses", "activities", "dates", "grades", "content", "transfer", "messages"}

#: Tab keys `course_base.html` renders. An `active_tab` outside this lights nothing at all.
TABS = {"overview", "activities", "dates", "grades", "content", "transfer", "messages"}


def _env():
    from musai.web.app import templates

    return templates.env


@pytest.mark.parametrize("name", sorted(p.name for p in TEMPLATES.glob("*.html")))
def test_every_template_compiles(name):
    _env().get_template(name)


@pytest.mark.parametrize("name", COURSE_PAGES)
def test_every_course_page_sits_inside_the_course_shell(name):
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    assert '{% extends "course_base.html" %}' in source, (
        f"{name} renders a single course but does not extend course_base.html, so it has no "
        f"tab strip and no course identity header.")


@pytest.mark.parametrize("name", COURSE_PAGES)
def test_every_course_page_declares_a_known_tab_and_accent(name):
    import re

    source = (TEMPLATES / name).read_text(encoding="utf-8")

    tab = re.search(r"{%\s*set\s+active_tab\s*=\s*'([^']+)'", source)
    assert tab, f"{name} does not set `active_tab`, so no tab is marked current."
    assert tab.group(1) in TABS, f"{name} lights unknown tab {tab.group(1)!r}"

    feature = re.search(r"{%\s*block\s+feature\s*%}(\w+){%\s*endblock\s*%}", source)
    assert feature, f"{name} does not declare a feature block, so it inherits the wrong accent."
    assert feature.group(1) in FEATURES, (
        f"{name} declares accent {feature.group(1)!r}, which `base.html` has no "
        f"[data-feature] block for — it will silently render in the cockpit's indigo.")


def test_base_defines_a_block_for_every_feature_a_page_uses():
    """The other direction: a `[data-feature]` rule that no page uses is dead, and a page
    using a feature `base.html` never styles is the bug above. Pin both ends together."""
    import re

    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    declared = set(re.findall(r'\[data-feature="(\w+)"\]', base))
    # `courses` is the `:root` default rather than an override, so it has no block of its own.
    assert declared | {"courses"} == FEATURES, (
        f"base.html styles {sorted(declared | {'courses'})} but the pages use "
        f"{sorted(FEATURES)}.")


def test_the_tab_strip_lists_exactly_the_known_tabs():
    """`course_base.html`'s TABS tuple is the navigation. If it and the pages drift, a tab
    exists that nothing lights, or a page lights a tab that is not on screen."""
    import re

    source = (TEMPLATES / "course_base.html").read_text(encoding="utf-8")
    # The tab KEY is the thing that must not drift — it is what `active_tab` is compared
    # against. The label beside it goes through `t()` and changes with the reader's language.
    listed = set(re.findall(r"\(\s*'(\w+)',\s*t\('[^']+'\),", source))
    assert listed == TABS, f"course_base.html renders {sorted(listed)}, expected {sorted(TABS)}"


def test_every_btn_class_a_template_uses_is_defined_in_the_css():
    """🔴 An undefined `.btn-*` renders as **plain text**, and nothing complains.

    Found by eye on 2026-08-14: the Cronograma's *Recalcular* had carried `btn-primary` since
    before the design system existed, and there is no `.btn-primary` rule anywhere — so the
    one control that recomputes the whole calendar had been sitting there looking like a
    caption. Three other templates had the same class.

    This is the failure mode worth a test rather than an eye: it is invisible in a diff, it
    survives every functional test (the button still submits), and it only shows up if someone
    happens to load that page and notice a button that does not look like one.
    """
    import re

    css = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    defined = set(re.findall(r"\.(btn-[a-z-]+)\s*\{", css)) | {"btn"}

    # `landing.html` is a standalone document with its own stylesheet — it does not extend
    # `base.html` and must not be measured against its rules.
    pages = [p for p in TEMPLATES.glob("*.html") if p.name != "landing.html"]

    offenders = []
    for page in pages:
        # 🔴 `(?<!-)` matters: without it the pattern also matches the CUSTOM PROPERTY
        # `--btn-bg`, and the test fails on perfectly good CSS variables. Caught the moment
        # the landing page gained `--btn-bg` / `--btn-fg` / `--btn-edge` / `--btn-cast`.
        for cls in re.findall(r"(?<![-\w])(btn-[a-z-]+)", page.read_text(encoding="utf-8")):
            if cls not in defined:
                offenders.append(f"{page.name}: {cls}")

    assert not offenders, (
        "These button classes are used but never defined, so they render as plain text:\n  "
        + "\n  ".join(sorted(set(offenders))))


def test_the_global_loader_no_longer_fabricates_a_step_log():
    """🔴 DESIGN_DIRECTION §4.2 rule 1 — a tick is evidence, never a timer.

    The old loader read `data-steps` off the form and printed each line on a `setTimeout`, so
    "Signing in…" appeared on schedule whether or not anything had signed in. It is exactly the
    instrument this project has been bitten by three times. If it comes back, this fails.
    """
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "data-steps" not in base, (
        "base.html is fabricating loader steps from a timer again. Real progress belongs to "
        "`work_progress.html`, which renders steps a job actually emitted.")
