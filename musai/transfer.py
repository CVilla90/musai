"""Course backup and restore, driven by a signed-in professor from the cockpit.

The CLI half of this has existed since 2026-08-10 (`musai/automation/backup.py`,
`restore.py`) and has run live across seventeen courses. This module is what it takes to put a
**button** in front of it, which is a different problem: the CLI is driven by someone who read
`RUNBOOK.md`, and a button is pressed by a colleague who has never seen this codebase.

🔴 **A restore DELETES the destination course's contents before writing the new ones.** That is
Moodle's wizard, not a MUSAI choice — *"Eliminar los contenidos de este curso y después
restaurar"* is the option that makes a copy behave like a copy. Enrolments survive
(`keep_roles_and_enrolments = Sí`, set **and read back**); everything else in the course does
not. So the rails below are not ceremony.

| rail | what it stops | where it lives |
|---|---|---|
| the target is a `Course` row **the professor owns** | acting on a colleague's course | `web/deps.owned_course` |
| the professor's own stored Moodle password, no fallback | a run authored by the wrong person | `credentials.resolve_for_professor` |
| `keep_roles_and_enrolments`, set and verified | unenrolling every student | `restore._set_and_verify` |
| the archive must say `users=no` | moving one course's students into another | `preflight` + `_guard_archive` |
| the subject must match, and unknown ⇒ refuse | INGLES I landing in INGLES III | `restore.verify_target` |
| MUSAI must hold no grades for the course | wiping a gradebook mid-semester | `preflight` |
| **a live restore requires a fresh pre-flight** | a restore nobody looked at first | `consume_preflight` |

That last one is the one this module adds. Rail 2 says *dry-run by default*, and a dry-run
restore technically satisfies it — but it uploads the whole 50 MB archive and walks the entire
wizard before stopping, so it costs ten minutes and tells the professor nothing she could not
be told in thirty seconds. `check_target()` already existed for exactly this reason and its
docstring says so. So the cheap read-only check **is** the dry run here, and the live path is
structurally unreachable without one: no pre-flight token, no restore.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from musai.audit import log as audit_log
from musai.automation.backup import BackupAborted, backup_course, inspect_mbz
from musai.automation.credentials import CredentialsMissing, resolve_for_professor
from musai.automation.restore import RestoreAborted, check_target, restore_course
from musai.db import engine
from musai.models import Activity, Course, Grade, JobRequest, Professor

#: Where uploaded archives land. Per professor, so two people uploading `respaldo-moodle2-…`
#: in the same minute cannot overwrite each other — Moodle's filenames are not unique across
#: accounts and both would be "the newest .mbz".
UPLOAD_ROOT = Path(__file__).resolve().parent.parent / "uploads"

#: Moodle's own upload ceiling on this instance, measured rather than assumed
#: (COURSE_EDITING §7). Rejecting oversize input here costs a second; letting it through costs
#: the full upload and then an error page.
MAX_UPLOAD_BYTES = 500 * 1024 * 1024

#: How long a pre-flight stays valid. It is a *reading of a live course* — how many activities
#: are about to be destroyed, what the course is currently called — and
#: `feedback_reread_before_you_write` is the entry that says an old measurement is a cache with
#: no invalidation. Fifteen minutes is long enough to read the screen and short enough that
#: nobody restores against yesterday's answer.
PREFLIGHT_TTL_S = 15 * 60

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class TransferRefused(RuntimeError):
    """A precondition failed. Nothing has been uploaded, created or destroyed."""


@dataclass
class Preflight:
    """The read-only answer to "what would this restore do?". Cheap, and the gate for the live run."""

    ok: bool
    course_id: int
    idc: str
    target_name: str = ""
    target_activities: int = 0
    target_sections: int = 0
    grades_held: int = 0
    backup_name: str = ""
    backup_activities: Optional[int] = None
    backup_includes_users: Optional[bool] = None
    refusal: str = ""
    checked_at: float = 0.0

    def as_dict(self) -> dict:
        return {**self.__dict__}


# ── the professor's own account ───────────────────────────────────────────────
def identity_for(professor: Professor):
    """Their stored Moodle login, or a refusal that says where to fix it. Never a fallback."""
    try:
        return resolve_for_professor(professor, system="moodle")
    except CredentialsMissing as e:
        raise TransferRefused(str(e)) from e


# ── uploads ───────────────────────────────────────────────────────────────────
def upload_dir(professor: Professor) -> Path:
    safe = _SAFE_NAME.sub("_", (professor.email or "unknown").lower())
    d = UPLOAD_ROOT / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(professor: Professor, filename: str, data: bytes) -> Path:
    """Write an uploaded `.mbz` to this professor's own folder and verify it is really one.

    ⚠️ This is a genuine change in what MUSAI holds. The CLI hands Moodle a path on the
    professor's disk and never touches the bytes (`restore.py` says so in its docstring); a web
    upload means the archive passes through the server. That is fine while the cockpit and the
    browser are the same machine, and it is the thing to revisit before this ever runs in the
    cloud — a 50 MB course archive crossing the internet twice is not what PLAN §2 has in mind.
    """
    if not data:
        raise TransferRefused("The upload was empty — nothing was saved.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise TransferRefused(
            f"{len(data) / 1_048_576:.0f} MB exceeds Moodle's {MAX_UPLOAD_BYTES // 1_048_576} MB "
            f"upload limit, so this could not be restored even if it were accepted here.")

    name = _SAFE_NAME.sub("_", Path(filename or "backup.mbz").name)
    if not name.lower().endswith(".mbz"):
        raise TransferRefused(
            f"{name} is not a Moodle course backup. A backup is a `.mbz` file — the one Moodle "
            f"produces under *Copia de respaldo*.")

    # 🔴 The gzip magic is checked before the file is kept. An HTML error page saved under a
    # `.mbz` name is a real failure mode on this Moodle (`backup.py` guards the same thing on
    # download), and it would otherwise surface fifteen minutes into a restore.
    if data[:2] != b"\x1f\x8b":
        raise TransferRefused(
            f"{name} is not a gzip archive, so it is not a Moodle backup — it may be an HTML "
            f"page saved under the wrong name. Nothing was saved.")

    path = upload_dir(professor) / f"{int(time.time())}_{name}"
    path.write_bytes(data)

    manifest = inspect_mbz(path)
    if not manifest.get("ok"):
        path.unlink(missing_ok=True)
        raise TransferRefused(
            f"{name} does not contain a readable `moodle_backup.xml`, so it is not a Moodle "
            f"course backup ({manifest.get('error')}). It was not kept.")
    return path


def _guard_archive(manifest: dict, *, into_course: Course) -> None:
    """Refuse an archive that would carry somebody's students into this course.

    🔴 `includes_users` is tri-state and `None` means **could not tell**, which is refused the
    same as `True`. `backup.carries_user_data()` documents this and every caller is required to
    read an unknown as a yes: restoring student data into a colleague's group is not a mistake
    that announces itself, it just quietly enrols the wrong people.
    """
    if manifest.get("includes_users") is not False:
        raise TransferRefused(
            f"This archive either carries user data or does not say that it doesn't "
            f"(users={manifest.get('includes_users')!r}). Restoring it into "
            f"{into_course.group_code} could move another course's students in. Refusing.\n"
            f"A backup made from a teacher account has no user-data option at all, so it is "
            f"safe by construction — re-make the backup rather than overriding this.")


# ── pre-flight: the cheap, read-only "what would happen?" ─────────────────────
def preflight(professor: Professor, course: Course, backup_path: str | Path | None = None,
              *, headless: bool = True, on_step=None) -> Preflight:
    """Open the course read-only and report what a restore would destroy. No upload, no wizard.

    Roughly thirty seconds: a login, a course open, and one page load per section. It answers
    the two questions the professor actually needs before pressing a button that cannot be
    undone — *is this the right course?* and *what is in it right now?*
    """
    idc = str(course.moodle_course_id or "").strip()
    if not idc:
        raise TransferRefused(
            f"{course.group_code} has no Moodle course id. Re-map your courses from the "
            f"dashboard first.")

    identity = identity_for(professor)
    manifest = inspect_mbz(backup_path) if backup_path else {}
    if manifest:
        _guard_archive(manifest, into_course=course)

    # 🔴 A restore wipes the gradebook with everything else. At semester start this is 0 and
    # costs nothing; a month in it is the number that should stop the run.
    with Session(engine) as sess:
        act_ids = list(sess.exec(
            select(Activity.id).where(Activity.course_id == course.id)).all())
        grades_held = 0
        if act_ids:
            grades_held = len(list(sess.exec(
                select(Grade.id).where(Grade.activity_id.in_(act_ids))).all()))

    if on_step:
        on_step(f"Opening {course.group_code} (idc {idc}) as {identity.username}…")

    try:
        info = check_target(
            idc=idc,
            backup_path=backup_path,
            identity=identity,
            # The live name is compared against what the mapper stored for this course. The
            # `--as-user` road needs a human to type `expect_course_name`; here the course was
            # picked from her own mapped list, so the expectation is data, not a typed string.
            expect_course_name=None,
            headless=headless,
        )
    except RestoreAborted as e:
        return Preflight(ok=False, course_id=course.id, idc=idc, refusal=str(e),
                         grades_held=grades_held, checked_at=time.time(),
                         backup_name=manifest.get("fullname") or "",
                         backup_activities=manifest.get("activities"),
                         backup_includes_users=manifest.get("includes_users"))

    return Preflight(
        ok=bool(info.get("allowed")),
        course_id=course.id,
        idc=idc,
        target_name=info.get("target_name") or "",
        target_activities=int(info.get("target_activities") or 0),
        target_sections=len(info.get("target_sections") or {}),
        grades_held=grades_held,
        backup_name=manifest.get("fullname") or "",
        backup_activities=manifest.get("activities"),
        backup_includes_users=manifest.get("includes_users"),
        checked_at=time.time(),
    )


def consume_preflight(job_id: int, *, owner: str, course_id: int) -> Preflight:
    """Fetch a pre-flight result and prove it licenses **this** restore. Raises otherwise.

    🔴 This is what makes the live path unreachable without a look first. Four things are
    checked, and each one is a real way the token could be the wrong token: it must belong to
    this professor, it must have succeeded, it must name **this** course, and it must be recent.
    A pre-flight against 9048 does not authorise a restore into 9046.
    """
    with Session(engine) as sess:
        job = sess.get(JobRequest, job_id)
        if job is None or job.requested_by != owner:
            raise TransferRefused(
                "That safety check does not exist. Run the check again before restoring.")
        result = json.loads(job.result_json or "{}")

    pf = Preflight(**{k: v for k, v in result.get("preflight", {}).items()
                      if k in Preflight.__dataclass_fields__})
    if not pf.ok:
        raise TransferRefused(
            f"The safety check refused this restore, so it cannot proceed.\n{pf.refusal}")
    if pf.course_id != course_id:
        raise TransferRefused(
            "That safety check was run against a different course. Run it again on this one — "
            "a restore deletes the destination, so the check has to be about the course you "
            "are restoring into.")
    age = time.time() - (pf.checked_at or 0)
    if age > PREFLIGHT_TTL_S:
        raise TransferRefused(
            f"The safety check is {int(age // 60)} minutes old. It measured what was in the "
            f"course at the time, and that is a cache with no invalidation — run it again.")
    return pf


# ── the two jobs ──────────────────────────────────────────────────────────────
def run_backup(professor: Professor, course: Course, *, on_step=None,
               headless: bool = True) -> dict:
    """Create a `.mbz` for this course and bring it to disk. Additive — writes nothing to the course.

    The cheap half of the pair: a backup adds a file to the professor's private backup area and
    changes nothing else, which is why it needs no pre-flight and no confirmation. It is also
    genuinely fast — 50 MB built in seconds on this instance — so it is the one part of this
    feature that really does fit in a minute.
    """
    identity = identity_for(professor)
    idc = str(course.moodle_course_id or "").strip()
    if not idc:
        raise TransferRefused(f"{course.group_code} has no Moodle course id — re-map first.")

    result = backup_course(
        idc=idc,
        dry_run=False,       # a backup is additive; a dry run here produces nothing to download
        headless=headless,
        identity=identity,
        group_label=course.group_code,
        out_dir=upload_dir(professor),
        download=True,
        on_step=on_step,
    )
    _audit("course_backup", professor, course, result, dry_run=False)
    _mark(professor, ok=bool(result.get("ok")))
    return result


def run_restore(professor: Professor, course: Course, backup_path: str | Path, *,
                preflight_job_id: int, on_step=None, headless: bool = True,
                force: bool = False) -> dict:
    """Restore an archive into this professor's course. 🔴 Destroys the course's contents first.

    Every refusal here happens **before** a browser exists, so a refused restore costs seconds
    and leaves the course untouched.
    """
    idc = str(course.moodle_course_id or "").strip()
    if not idc:
        raise TransferRefused(f"{course.group_code} has no Moodle course id — re-map first.")

    path = Path(backup_path)
    if not path.is_file():
        raise TransferRefused(
            "The uploaded backup is no longer on disk. Upload it again — MUSAI keeps it only "
            "until the restore runs.")

    manifest = inspect_mbz(path)
    if not manifest.get("ok"):
        raise TransferRefused(f"That file is not a readable Moodle backup: {manifest.get('error')}")
    _guard_archive(manifest, into_course=course)

    pf = consume_preflight(preflight_job_id, owner=professor.email, course_id=course.id)
    if pf.grades_held and not force:
        raise TransferRefused(
            f"MUSAI holds {pf.grades_held} grades for {course.group_code}. A restore wipes the "
            f"gradebook. Re-fetch them first, or tick the override if you accept losing them.")

    identity = identity_for(professor)
    result = restore_course(
        idc=idc,
        backup_path=path,
        dry_run=False,
        headless=headless,
        identity=identity,
        group_label=course.group_code,
        # The live name the pre-flight just read, fed back in as the expectation. This is the
        # `expect_course_name` rail with the human typing removed: the string comes from a read
        # of the same course minutes ago, so it catches a course that changed underneath us.
        expect_course_name=pf.target_name or None,
        on_step=on_step,
    )
    _audit("course_restore", professor, course, result, dry_run=False,
           extra={"preflight": pf.as_dict(), "forced": force,
                  "archive": manifest.get("fullname"),
                  "archive_activities": manifest.get("activities")})
    _mark(professor, ok=bool(result.get("ok")))
    return result


# ── bookkeeping ───────────────────────────────────────────────────────────────
def _audit(action: str, professor: Professor, course: Course, result: dict, *,
           dry_run: bool, extra: dict | None = None) -> None:
    """Record who actually decided.

    🔴 `actor` is the **signed-in professor's email**, not the hardcoded `"carlos"` every other
    call site still passes (`AUTH_SETUP.md` §4 step 3). This pair of actions is where that
    matters first: two people now share one database, and an audit row that says `carlos` about
    Colleague D's restore is worse than no row — it is a wrong answer to the only question the log
    exists to answer.
    """
    detail = {k: v for k, v in result.items() if k != "steps"}
    detail.update(extra or {})
    detail["moodle_account"] = result.get("as_user")
    with Session(engine) as sess:
        audit_log(sess, action, actor=professor.email,
                  target=f"course:{course.id} idc:{course.moodle_course_id} {course.group_code}",
                  env=course.moodle_env, dry_run=dry_run, detail=detail)
        sess.commit()


def _mark(professor: Professor, *, ok: bool) -> None:
    """Record that the stored Moodle password was just used, and whether it worked."""
    from musai.professors import mark_used

    try:
        with Session(engine) as sess:
            mark_used(sess, professor.id, "moodle", ok=ok)
    except Exception:
        pass  # bookkeeping must never be the reason a completed restore reports failure


def cleanup_uploads(professor: Professor, *, keep: int = 5) -> int:
    """Drop all but the newest few uploads. Course archives are 20–75 MB each.

    Called after a successful restore rather than on a timer: the file is the only copy of what
    was restored until Moodle finishes, and deleting it mid-run would be exactly the kind of
    tidy-up that costs an afternoon.
    """
    d = upload_dir(professor)
    files = sorted(d.glob("*.mbz"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def last_backup_for(professor: Professor, course: Course) -> Optional[Path]:
    """The most recent archive on disk for this course, if any. Used to offer a re-download."""
    idc = str(course.moodle_course_id or "")
    if not idc:
        return None
    files = [p for p in upload_dir(professor).glob("*.mbz") if f"-course-{idc}-" in p.name]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def describe_archive(path: str | Path) -> dict:
    """Manifest for display: which course, how many activities, does it carry users."""
    m = inspect_mbz(path)
    return {
        "name": Path(path).name,
        "ok": m.get("ok"),
        "course_id": m.get("course_id"),
        "fullname": m.get("fullname"),
        "activities": m.get("activities"),
        "includes_users": m.get("includes_users"),
        "mb": round((m.get("bytes") or 0) / 1_048_576, 1),
        "moodle_release": m.get("moodle_release"),
        "error": m.get("error"),
    }
