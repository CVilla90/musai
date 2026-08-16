"""The restore confirm button must survive a poll, and a backup must be confirmed first.

🔴 **Why this file exists (2026-08-14).** the owner ran the restore pre-flight on 3-LED-B. It
succeeded — 46 s, *"Target reads as '3ED-B - INGLES III - 544070' and currently holds 94
activities across 12 sections"* — and **the confirm button never appeared.** He read that as the
restore having been safely blocked. It had not been blocked; it had been made unreachable.

The pre-flight job is STORED as `course_restore:check` (the suffix keeps
`jobs.running_for(COURSE_RESTORE)`'s double-restore guard from mistaking a read-only check for a
live restore) but DISPLAYED as `restore_check`. `_job_fragment` passed the display name on the
first render; `/work/{id}` passed nothing and fell through to the stored name. So from the first
poll onward `kind` was `course_restore:check`, and three things vanished together:

* the checklist (no `CHECKLISTS` entry for that name),
* the worded duration (no `DURATION` entry),
* 🔴 the confirm form, gated on `kind == 'restore_check'` — the only way to proceed.

Any job slower than one poll interval always ends on a polled render, so the button was never
reachable for a real pre-flight. ⭐ **A display name derived in two places is a display name that
disagrees with itself.**
"""

from __future__ import annotations

import re
from pathlib import Path

# ⚠️ App package first — `musai.web.routes_course` is imported by `musai.web.app` while the app is
# still being built, so reaching for the router module first yields a half-initialised one.
import musai.web.app  # noqa: F401
from musai import checklists, jobs

TEMPLATES = Path(__file__).resolve().parent.parent / "musai" / "web" / "templates"


def _strip_jinja_comments(src: str) -> str:
    """`{# … #}` removed.

    🔴 Third time today a source-level assertion matched the COMMENT explaining the rule instead
    of the code breaking it: the landing page's media-query guard, the refresh job's `as_user`
    guard, and this file's `confirm(` guard all failed on their own documentation. A test that
    cannot tell a live construct from prose about it is a test that fires on being explained.
    """
    return re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)


# ── the kind, reconciled in one place ─────────────────────────────────────────
def test_the_stored_preflight_kind_resolves_to_the_displayed_one():
    stored = jobs.COURSE_RESTORE + ":check"
    assert checklists.display_kind(stored) == "restore_check", (
        f"{stored!r} no longer maps to the name work_progress.html keys on. The restore confirm "
        f"button, the checklist and the duration all disappear together when this drifts.")


def test_every_stored_job_kind_has_a_checklist_and_a_duration():
    """The guard that would have caught the original bug on the day it was written.

    Every kind a job can be stored under must resolve — through `display_kind` — to something the
    waiting component actually knows. A kind that resolves to nothing renders a card with no
    checklist and no expectation, and nothing on screen says it is broken.
    """
    from musai.web import routes_course

    stored_kinds = {
        jobs.MAP_COURSES, jobs.COURSE_BACKUP, jobs.COURSE_RESTORE,
        jobs.COURSE_RESTORE + ":check", jobs.CREDENTIAL_CHECK,
        routes_course.ACTIVITY_IMPORT, routes_course.GRADEBOOK_IMPORT,
    }
    missing = []
    for stored in sorted(stored_kinds):
        shown = checklists.display_kind(stored)
        if shown not in checklists.CHECKLISTS or not checklists.duration(shown):
            missing.append(f"{stored} → {shown}")
    assert not missing, (
        "these stored job kinds resolve to a name the waiting component does not know, so their "
        "cards render with no checklist and no stated duration:\n  " + "\n  ".join(missing))


def test_the_poll_route_derives_the_kind_instead_of_being_told_it():
    """🔴 The fix has to be that `/work/{id}` cannot get this wrong, not that it remembers to.

    Pinned at the source: `_work_context` must derive the kind from the job. Passing it in from
    each call site is what let the two entry points disagree, and a future author adding a third
    entry point would reintroduce it.
    """
    import inspect

    from musai.web.routes_transfer import _work_context

    # 🔴 Docstring and comments stripped FIRST. Without this the assertion passes on the prose in
    # the docstring that explains the fix — verified by reverting the one real line and watching
    # this test stay green, which is precisely how a guard becomes decoration.
    src = inspect.getsource(_work_context)
    code = re.sub(r'"""_?.*?"""', "", src, flags=re.DOTALL)
    code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))

    assert "checklists.display_kind" in code, (
        "_work_context no longer normalises the stored kind — the pre-flight's confirm button "
        "goes back to vanishing on the first poll.")
    assert not re.search(r"kind\s*=\s*kind\s+or\s", code), (
        "the kind is being taken from the ARGUMENT again. That is the original bug: "
        "`_job_fragment` supplies the display name and `/work/{id}` does not, so the two renders "
        "disagree and the restore's confirm button exists only until the first poll.")


# ── the confirm form itself ───────────────────────────────────────────────────
def _work_progress() -> str:
    return (TEMPLATES / "work_progress.html").read_text(encoding="utf-8")


def test_the_restore_confirm_form_is_gated_on_the_displayed_kind_only():
    """It must key on `restore_check`, the name `display_kind` produces — not the stored one."""
    src = _work_progress()
    assert "kind == 'restore_check'" in src, (
        "the confirm block's condition changed; if it now keys on the stored kind this test is "
        "the wrong shape, but the two names still have to be reconciled in checklists.ALIASES.")
    assert 'hx-post="{{ r.confirm_url }}"' in src, "the confirm form lost its action."


def test_a_finished_preflight_renders_the_confirm_button_when_POLLED(sign_in, my_course):
    """🔴 The end-to-end regression, driven through the exact path that was broken.

    Not a source-level check: the bug was invisible in every individual file. The template's
    condition was right, `_check_work`'s result was right, and `_job_fragment` passed the right
    kind — the failure only existed on the **`/work/{id}` poll**, which is the render a professor
    actually ends on for any job slower than one poll interval.

    Uses a fabricated finished job rather than a live pre-flight, deliberately: a real one signs
    in to Moodle as the owner, and nothing about the button's presence needs a browser to prove.
    """
    from fastapi.testclient import TestClient

    from musai.web.app import app

    # ⚠️ `my_course` creates its own course rather than borrowing one of the owner's. This test used
    # to read his first course out of the copied database and died with `NoneType has no attribute
    # 'id'` the day it was blanked for a demo.
    _prof_id, cid = my_course

    job_id = jobs.create(jobs.COURSE_RESTORE + ":check", owner="professor@uach.mx",
                         params={"target": cid})
    jobs.update(job_id, status="done", result={
        "ok": True,
        "preflight": {"ok": True, "target_name": "3ED-B - INGLES III - 544070",
                      "target_activities": 94, "target_sections": 12, "idc": "9072",
                      "grades_held": 0},
        "archive": {"fullname": "3ED-B - INGLES III - 544070", "activities": 94, "mb": 13.2},
        "archive_path": "course_backups/english_iii_master_20260812.mbz",
        "confirm_url": f"/courses/{cid}/restore",
        "error": "",
    })

    client = sign_in(TestClient(app))
    body = client.get(f"/work/{job_id}").text

    assert "restore now" in body, (
        "a FINISHED pre-flight polled through /work/{id} still renders no confirm button. That is "
        "the 3-LED-B bug: the check passes, says exactly what it would replace, and then offers "
        "no way to proceed — which reads as MUSAI having blocked the restore.")
    assert f'hx-post="/courses/{cid}/restore"' in body, "the confirm form has no action."
    assert 'name="preflight_job"' in body, "the confirm form would post without its token."
    # And the rest of the card must survive the same fix.
    assert "Read this before continuing" in body, "the destructive warning panel vanished."


def test_the_confirm_form_still_carries_the_preflight_token():
    """🔴 `consume_preflight` refuses unless the token is this professor's, passed, about THIS
    course and under 15 minutes old. A confirm form that stopped sending it would turn every
    restore into a refusal — or, if the check were ever relaxed, into an unchecked restore."""
    src = _work_progress()
    assert 'name="preflight_job"' in src
    assert 'name="archive_path"' in src


# ── the backup confirmation ───────────────────────────────────────────────────
def _transfer_page() -> str:
    return (TEMPLATES / "course_transfer.html").read_text(encoding="utf-8")


def test_creating_a_backup_takes_two_deliberate_steps():
    """The owner's instruction, 2026-08-14: confirm before the job starts.

    A backup is additive and needs no rail, but one click used to sign in to Moodle as him, queue
    a job on UACH's server and leave a 60 MB archive in his private area — with nothing on the
    button naming the course it was about to open.
    """
    src = _transfer_page()
    post = 'hx-post="/courses/{{ course.id }}/backup"'
    assert post in src, "the backup control lost its action entirely."
    assert src.count(post) == 1, (
        "there are two controls posting to the backup route — one of them is the un-confirmed "
        "button this change removed.")

    # The posting control must sit INSIDE the disclosure, so it cannot be reached in one click.
    before = src.split(post)[0]
    assert "<details" in before, (
        "the button that starts the backup is no longer behind a confirmation step.")
    assert "Yes — back up" in src, "the confirming button lost the wording that makes it a choice."


def test_the_backup_confirmation_names_the_course_it_will_open():
    """A confirmation that does not say WHICH course is a speed bump, not a confirmation.

    ⭐ The same lesson as the transfer page's destructive panel: the identity of the target is the
    one fact a professor needs before pressing, and `/courses/9/backup` in the markup is not it.
    """
    src = _strip_jinja_comments(_transfer_page())
    panel = src.split("<details")[1].split("</details>")[0]
    assert "{{ course.group_code }}" in panel, "the confirmation does not name the group."
    assert "{{ course.moodle_course_id }}" in panel, (
        "the confirmation does not show the Moodle idc — the only unambiguous identifier, and the "
        "one that tells you the button is aimed where you think it is.")


def test_the_backup_confirmation_does_not_use_a_blocking_dialog():
    """⚠️ No `confirm()`. A JS modal blocks the page, and MUSAI's own tooling drives this browser
    under automation, where a modal stops every subsequent command."""
    src = _strip_jinja_comments(_transfer_page())
    assert "confirm(" not in src, (
        "a blocking JS dialog was introduced — it halts the page and breaks browser automation.")
