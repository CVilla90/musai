"""What a slow job is *made of*, so the waiting screen can tick real milestones.

🔴 **The one rule this module exists to enforce: a tick is evidence, never a timer.** Every
item below is matched against a step message the job genuinely emitted. Nothing here advances
because time passed, and there is deliberately no way to make it — `progress()` takes the step
list and nothing else, so it cannot see a clock even if a later author wanted it to.

That rule is not aesthetic. This project has been bitten three times by an instrument that
reported confidently about something it was not measuring: `restore.py`'s post-restore count
said `0` for restores that had placed 79 activities, a date audit called two live exams
"undated" because it could not read the form, and a Playwright timeout was reported as a dead
host and re-run, messaging 35 students twice. A progress bar filling on `setInterval` is the
same defect wearing nicer clothes, and it would be the one a professor watches for fifteen
minutes and believes.

**The checklist is a summary; the log is the record.** A step that matches no item still
appears in the quick log — items are the headline milestones, not a filter.

⚠️ The matchers are substrings of messages emitted in `musai/automation/backup.py`,
`restore.py`, `musai/mapping.py` and `musai/coursedates/apply.py`. If a message there is
reworded, the tick stops firing and the item sits at `skipped` — visibly wrong rather than
quietly wrong, which is the failure direction to want. `tests/test_checklists.py` pins the
pairs together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class Step:
    """One headline milestone, and the real messages that prove it happened."""

    id: str
    label: str
    match: tuple[str, ...]          # case-insensitive substrings; ANY of them ticks the item


#: How long each kind actually takes, **in words and from measurement** (COURSE_EDITING §7).
#: Never a countdown or a percentage: an estimate that overruns is worse than no estimate, and
#: a restore's spread is 15–45 minutes depending on archive size.
DURATION = {
    "map_courses": "Usually about half a minute.",
    "course_backup": "Usually under a minute.",
    "restore_check": "About thirty seconds. Nothing is written.",
    "course_restore": "About fifteen minutes for a 50 MB backup, longer for a big one. "
                      "You can close this tab — it keeps running.",
    "course_dates": "A second or two per activity, so a minute or three for a full course.",
    "credential_check": "A few seconds.",
    "activity_import": "About a minute — Moodle renders one tab per page load, and a course "
                       "has a dozen or more.",
    "gradebook_import": "Usually under a minute. Nothing in the course changes — it downloads "
                        "the gradebook and reads it.",
}

CHECKLISTS: dict[str, tuple[Step, ...]] = {
    "map_courses": (
        Step("signin", "Sign in to campusvirtual", ("signing in to campusvirtual",)),
        Step("dashboard", "Read your dashboard", ("reading the dashboard", "course tile")),
        Step("save", "Save your courses", ("course(s) added", "new ·")),
    ),
    "course_backup": (
        Step("signin", "Sign in and open the course", ("course opened",)),
        Step("build", "Build the archive in Moodle",
             ("backup file created", "landed on the private backup area", "backup file →")),
        Step("download", "Bring the file here", ("downloaded",)),
        Step("verify", "Check what is inside it", ("verified: course",)),
    ),
    "restore_check": (
        Step("archive", "Read the archive", ("archive:",)),
        Step("signin", "Sign in and open the target", ("course opened", "target reads as")),
        Step("count", "Count what a restore would destroy",
             ("target currently holds", "currently holds")),
    ),
    # The long one. Eight items over fifteen minutes is not too many — each is a real
    # milestone, and watching them arrive is the difference between waiting and worrying.
    "course_restore": (
        Step("signin", "Sign in and open the course", ("course opened",)),
        Step("upload", "Upload the archive", ("upload landed",)),
        Step("validate", "Moodle validates the backup", ("1/4 backup validated",)),
        Step("destination", "Set the destination to replace this course",
             ("2/4 destination",)),
        Step("schema", "Configure what comes across", ("3/4 schema",)),
        Step("settings", "Set and verify the course settings", ("4/4 course settings",)),
        Step("restore", "Restore — Moodle is writing the course",
             ("performing the restore", "still restoring", "moodle moved on")),
        Step("count", "Count what landed", ("course now holds", "counts zero activities",
                                            "count is still 0")),
    ),
    # ⚠️ The Cronograma reports in Spanish (`musai/coursedates/apply.py`), so its matchers are
    # Spanish while its labels are not. The first draft of this block guessed at English
    # phrases and every item would have sat at `pending` through an entire run — which is
    # precisely the failure `test_every_matcher_appears_in_the_code_that_emits_it` exists to
    # catch, and did.
    "course_dates": (
        Step("signin", "Open the course", ("abriendo el navegador", "curso abierto")),
        Step("plan", "Work out what has to change", ("actividades por escribir",)),
        # "— guardado" and not "guardado": "Verificando lo guardado…" contains the bare word
        # and would tick the write step before a single form had been saved.
        Step("write", "Write each activity's dates", ("— guardado", "— simulacro")),
        Step("verify", "Read the dates back", ("verificando lo guardado", "listo:")),
    ),
    "credential_check": (
        Step("signin", "Sign in as you", ("signing in", "signed in")),
        Step("read", "Read what the account can see", ("course tile", "dashboard")),
    ),
    # Reads the course's tab strip once and feeds BOTH the Activities tab and the Cronograma,
    # which is why "read every tab" is its own visible milestone: it is the slow part, one
    # page load per tab, and a professor watching it should see why.
    "activity_import": (
        Step("open", "Open the course", ("opening ", "pestañas encontradas")),
        Step("tabs", "Read every tab", ("§",)),
        Step("save", "Save the activities MUSAI did not know", ("already known",)),
    ),
    # The last item is the one worth watching: "downloaded" only means a file arrived, and the
    # count a professor came here to fix does not change until it has been read.
    "gradebook_import": (
        Step("signin", "Sign in to campusvirtual", ("signing in to campusvirtual",)),
        Step("open", "Open the course", ("opening the gradebook export form", "course opened")),
        Step("download", "Download the gradebook", ("export downloaded",)),
        Step("ingest", "Read it into MUSAI", ("students ·", "no change")),
    ),
}


def _matches(step_msg: str, needles: Iterable[str]) -> bool:
    low = step_msg.lower()
    return any(n in low for n in needles)


def progress(kind: str, steps: Optional[list] = None, *, running: bool = False) -> list[dict]:
    """`[{id, label, state}]` for one job. `state` ∈ done · current · pending · skipped.

    * **done** — a real step message matched this item.
    * **current** — the first unmatched item at or after the furthest thing that *did* match,
      and only while the job is running. This is the only item that animates.
    * **pending** — not reached yet.
    * **skipped** — 🔴 unmatched, but the job has visibly moved past it. Deliberately NOT
      shown as done: MUSAI did not see that step happen, and ticking it would be inventing
      the one kind of evidence this whole module exists to refuse to invent. It is drawn
      quietly, because in practice it means a message was reworded — not that Moodle failed.

    An unknown `kind` returns `[]`, so a job with no declared checklist renders as pure log
    rather than as an empty box. Adding a job kind is allowed to be a two-step process; it is
    not allowed to produce a screen that says nothing.
    """
    items = CHECKLISTS.get(kind, ())
    if not items:
        return []

    messages = [(s.get("msg") or "") for s in (steps or [])]
    matched = {
        item.id for item in items if any(_matches(m, item.match) for m in messages)
    }

    # The frontier: how far down the list there is evidence of arrival. Everything before it
    # has been passed, whether or not MUSAI recognised the message.
    frontier = -1
    for i, item in enumerate(items):
        if item.id in matched:
            frontier = i

    out, current_taken = [], False
    for i, item in enumerate(items):
        if item.id in matched:
            state = "done"
        elif i < frontier:
            state = "skipped"
        elif running and not current_taken:
            state, current_taken = "current", True
        else:
            state = "pending"
        out.append({"id": item.id, "label": item.label, "state": state})
    return out


#: Stored job kind → the kind this component is keyed by.
#:
#: 🔴 The restore pre-flight is STORED as `course_restore:check` — the suffix is what keeps
#: `jobs.running_for(COURSE_RESTORE)`'s double-restore guard from mistaking a read-only check for
#: a live restore — but it is DISPLAYED as `restore_check`. Those two names have to be reconciled
#: in exactly one place, and this is it.
#:
#: What it cost (2026-08-14): `_job_fragment` passed the display name on the first render and
#: `/work/{id}` did not, so the moment the card polled, `kind` became `course_restore:check` and
#: three things silently disappeared at once — the checklist (no entry), the duration (no entry)
#: and, worst, **the confirm button that is the only way to proceed with a restore**. A 46-second
#: pre-flight always ends on a polled render, so the button was never reachable. The owner hit this
#: on 3-LED-B and read it as the restore having been blocked.
#: ⭐ A display name derived in two places is a display name that disagrees with itself.
ALIASES = {"course_restore:check": "restore_check"}


def display_kind(stored: str) -> str:
    """The kind `work_progress.html` and the tables above are keyed by, from a stored job kind."""
    return ALIASES.get(stored or "", stored or "")


def duration(kind: str) -> str:
    """The honest expectation, in words. Empty for a kind nobody has measured."""
    return DURATION.get(kind, "")
