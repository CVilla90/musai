"""The waiting screen's honesty rails.

`musai/checklists.py` decides what a professor watching a fifteen-minute restore is told. The
tests here are about one property above all others: **nothing on that screen may advance
except on evidence.** The rest is about the two ways a checklist rots — a matcher that no
longer matches, and a job kind nobody declared.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from musai import checklists
from musai.checklists import CHECKLISTS, DURATION, progress

ROOT = Path(__file__).resolve().parent.parent


def _steps(*messages: str) -> list[dict]:
    return [{"t": "00:00:00", "msg": m} for m in messages]


def _state(items: list[dict], step_id: str) -> str:
    return next(i["state"] for i in items if i["id"] == step_id)


# ── the one rule ──────────────────────────────────────────────────────────────
def test_nothing_is_ticked_without_a_step_message():
    """🔴 A tick is evidence, never a timer. With no steps, nothing may be done."""
    items = progress("course_restore", [], running=True)
    assert [i["state"] for i in items].count("done") == 0


def test_progress_cannot_see_a_clock():
    """Structural, not behavioural: `progress` takes steps and a running flag and nothing
    else. A later author who wants a time-based tick has to change the signature to get one,
    which is a change a reviewer will see."""
    params = set(inspect.signature(progress).parameters) - {"self"}
    assert params == {"kind", "steps", "running"}, (
        f"progress() grew a parameter: {sorted(params)}. If one of them is a timestamp or a "
        f"duration, the checklist can now advance without evidence.")


def test_a_real_message_ticks_its_item():
    items = progress("course_restore", _steps("Course opened"), running=True)
    assert _state(items, "signin") == "done"
    assert _state(items, "upload") == "current"
    assert _state(items, "count") == "pending"


def test_an_unmatched_item_the_job_passed_is_skipped_never_done():
    """🔴 The important one. MUSAI did not see 'upload' happen, so it must not claim it did —
    even though a later step proves the job got past that point."""
    items = progress("course_restore",
                     _steps("Course opened", "1/4 Backup validated"), running=True)
    assert _state(items, "signin") == "done"
    assert _state(items, "upload") == "skipped"
    assert _state(items, "validate") == "done"


def test_only_one_item_is_ever_current():
    items = progress("course_restore", _steps("Course opened"), running=True)
    assert sum(1 for i in items if i["state"] == "current") == 1


def test_a_finished_job_has_no_current_item():
    """A pulsing 'in progress' marker on a job that ended is the same lie as a timer."""
    items = progress("course_restore", _steps("Course opened"), running=False)
    assert not any(i["state"] == "current" for i in items)


def test_an_unknown_kind_renders_as_pure_log_not_an_empty_box():
    assert progress("something_new", _steps("hello"), running=True) == []


def test_the_whole_restore_ticks_through_in_order():
    """The happy path, end to end, in the real messages `restore.py` emits."""
    items = progress("course_restore", _steps(
        "Course opened",
        "Uploading respaldo.mbz (53.0 MB)…",
        "Upload landed (53.0 MB)",
        "1/4 Backup validated",
        "2/4 Destination = replace this course's contents",
        "3/4 Schema configured",
        "4/4 Course settings set and verified",
        "Performing the restore — this takes minutes; do NOT close the browser…",
        "…still restoring (240s)",
        "Course now holds 106 activities",
    ), running=False)
    assert all(i["state"] == "done" for i in items), [i for i in items
                                                      if i["state"] != "done"]


# ── the two ways a checklist rots ─────────────────────────────────────────────
#: Which source file emits the steps for each kind. A matcher is only meaningful next to the
#: code that produces the message it is matching.
EMITTERS = {
    # A job's steps come from two places: the automation that drives the browser, and the
    # route's `_*_work` wrapper that narrates around it. The first version of this table
    # listed only the automation and failed immediately on `map_courses` — its last two
    # messages ("N new · M updated", "…: N course(s) added") are emitted by the wrapper in
    # `routes_transfer.py`. That is the table being wrong rather than the matchers, and it is
    # worth having found out here rather than from a checklist that never reached its last row.
    "map_courses": ["musai/mapping.py", "musai/web/routes_transfer.py"],
    "course_backup": ["musai/automation/backup.py", "musai/transfer.py"],
    "restore_check": ["musai/automation/restore.py", "musai/web/routes_transfer.py"],
    "course_restore": ["musai/automation/restore.py"],
    "course_dates": ["musai/coursedates/apply.py"],
    "credential_check": ["musai/mapping.py"],
    "activity_import": ["musai/coursedates/discover.py", "musai/web/routes_course.py"],
    "gradebook_import": ["musai/automation/moodle_export.py", "musai/web/routes_course.py"],
}


@pytest.mark.parametrize("kind", sorted(CHECKLISTS))
def test_every_matcher_appears_in_the_code_that_emits_it(kind):
    """🔴 A matcher that matches nothing is a tick that never fires — and it fails **silently**,
    as an item sitting at `pending` for an entire fifteen-minute restore while the job works
    perfectly. Nothing in the UI would say the checklist was broken; it would just look slow.

    This caught the `course_dates` block on the day it was written: it had been given English
    matchers ("plan", "read back") for a job that reports entirely in Spanish
    ("Curso abierto", "— guardado.", "Verificando lo guardado…"). Every item would have been
    grey forever.
    """
    sources = "\n".join(
        (ROOT / f).read_text(encoding="utf-8").lower() for f in EMITTERS[kind]
    )
    missing = [
        (item.id, needle)
        for item in CHECKLISTS[kind]
        for needle in item.match
        if needle not in sources
    ]
    assert not missing, (
        f"{kind}: these matchers appear nowhere in {EMITTERS[kind]}, so they can never "
        f"tick:\n  " + "\n  ".join(f"{i} ← {n!r}" for i, n in missing))


@pytest.mark.parametrize("kind", sorted(CHECKLISTS))
def test_every_declared_kind_states_how_long_it_takes(kind):
    """"Say the expected duration in words, from measurement" — DESIGN_DIRECTION §4.2 rule 4."""
    assert DURATION.get(kind), f"{kind} has a checklist but no worded duration."


def test_no_duration_is_a_countdown_or_a_percentage():
    """A countdown that overruns is worse than no estimate. These are sentences, on purpose."""
    for kind, text in DURATION.items():
        assert "%" not in text, f"{kind}'s duration reads like a percentage: {text!r}"
        assert not text.strip().rstrip(".").isdigit(), f"{kind}'s duration is a bare number."


def test_every_job_kind_the_app_starts_has_a_checklist():
    """`musai/jobs.py` names the kinds; a kind with no checklist shows a professor a bare log."""
    from musai import jobs

    declared = {jobs.MAP_COURSES, jobs.COURSE_BACKUP, jobs.COURSE_RESTORE,
                jobs.CREDENTIAL_CHECK, "restore_check"}
    assert declared <= set(CHECKLISTS), (
        f"No checklist for: {sorted(declared - set(CHECKLISTS))}")
