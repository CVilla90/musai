"""Blank MUSAI's **local database** so the cockpit can be demonstrated from first sign-in.

    python -m musai.reset_demo                          # dry run — prints exactly what it would delete
    python -m musai.reset_demo --apply                  # backs the DB up, then deletes
    python -m musai.reset_demo --apply --forget-moodle-password
    python -m musai.reset_demo --apply --forget-me      # full first-run: no professor row either

🔴 **This never touches Moodle.** Not one HTTP request leaves the machine: there is no Playwright
import in this module and no credential is read. Every course, activity, student and grade it
deletes is a *local copy* of something that lives on `campusvirtual.uach.mx` and is re-read by
pressing **Update from Moodle** — which is the point, because that is the demo.

⚠️ **It also never touches a file on disk.** `course_backups/`, `downloads/`, `backups/` and
`uploads/` are left exactly as they are. That is not tidiness: `course_backups/` holds
`english_iv_master_20260812.mbz`, the generic English IV archive Colleague D is going to restore her
new course from, and archives like it were paid for in 45-minute runs. A "reset" that swept the
directory clean would be the most expensive command in the project.

## What it keeps, and why

**Kept by default — the identity.** The `professor` row and the encrypted Moodle password in
`professor_credential`. Deleting them costs nothing but a retype, but the retype has to happen in
Settings *before* the demo can map a single course, and discovering that mid-demo is a bad minute.
`--forget-moodle-password` and `--forget-me` are there when the first-run experience is the thing
being shown.

**Kept by default — the record.** `audit_log` is MUSAI's own account of what it has done to live
courses: 29 restores, 62 activity deletions, 7 backups. None of it is derivable from anything else
on this machine, and the project has already been bitten by a result that went missing because it
was written after the step rather than before it. `--clear-audit` exists; think about it first.

**Kept always.** `alembic_version` (dropping it strands every migration) and `hub_profile` — the
one row holding the phone number and contact block that the course hubs print, typed once.

## Why every course-keyed table must go, not just the courses

SQLite hands out `INTEGER PRIMARY KEY` values as `max(id) + 1`, so once the `course` table is
empty the next mapped course is **id 1 again**. A surviving `course_hub` or `course_schedule` row
pointing at course 8 would then silently attach itself to whichever course later becomes 8 — a
different group, possibly a different professor's. So the child tables are deleted for
correctness, not cosmetics, and the deletion order below is child-first.

## The backup is a precondition, not a courtesy

`--apply` copies the database to `musai_dev.db.bak-demo-<stamp>` and then **opens the copy and
counts its rows** before issuing a single DELETE. If the copy is missing, empty, or does not read
back as a database, nothing is deleted and the command exits non-zero. An irreversible step needs
its marker written and *verified* in front of it.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from musai.automation._log import logger as log
from musai.config import settings

# ── what gets deleted, child-first ────────────────────────────────────────────
#: Ordered so that no row is orphaned mid-run even with `PRAGMA foreign_keys=ON`. The comment on
#: each line is what a professor loses and where it comes back from.
DERIVED: tuple[tuple[str, str], ...] = (
    ("grade",             "every imported activity grade — re-read from a gradebook export"),
    ("partial_grade",     "computed partial results — recomputed from grades"),
    ("message_recipient", "who a batch went to — the record is Moodle's message drawer"),
    ("message",           "SUSAI conversation turns"),
    ("conversation",      "SUSAI conversations"),
    ("whatsapp_link",     "student ↔ WhatsApp links"),
    ("enrollment",        "who is in each course — re-read from a gradebook export"),
    ("activity",          "the activity list per course — re-read by Import activities"),
    ("partial",           "partial definitions (Parcial 1/2, Final)"),
    ("course_hub",        "per-group hub content — keyed by course id, MUST NOT outlive it"),
    ("course_schedule",   "per-group Cronograma — keyed by course id, MUST NOT outlive it"),
    ("message_batch",     "sent-message batches"),
    ("course",            "the course rows themselves — re-created by Update from Moodle"),
    ("student",           "student names and matrículas — re-read from a gradebook export"),
    ("job_request",       "the Recent jobs list"),
    ("semester",          "semester rows — re-created on first sign-in by ensure_current_semester"),
)

#: `--clear-audit`. Deleted last so a failure earlier leaves the record intact.
RECORD: tuple[tuple[str, str], ...] = (
    ("audit_log",     "MUSAI's own record of every privileged action — NOT derivable"),
    ("ai_usage",      "Gemini token ledger"),
    # A bill is a record, not derived data. Wiping it for a demo would erase what a professor
    # was charged, and there is nothing left to reconstruct it from — the tokens are spent and
    # the rate card may since have changed. Behind `--clear-audit` with the rest of the record.
    ("usage_event",   "itemised MUSAI spend ledger — what each action cost, NOT re-derivable"),
    ("usage_counter", "SUSAI rate-limit counters"),
)

#: Never touched by any flag.
UNTOUCHABLE = ("alembic_version", "hub_profile")


def _db_path() -> Path:
    """The sqlite file behind `settings.database_url`, or exit.

    🔴 Non-sqlite is refused outright and there is deliberately no override flag. The only
    non-sqlite URL this project ever has is the Replit Postgres, and a mass DELETE aimed at it by
    a script named `reset_demo` is not a mistake worth leaving a door open for.
    """
    url = settings.database_url
    if not url.startswith("sqlite"):
        log.error(f"database_url is {url.split('://')[0]}://…, not sqlite. This script only ever "
                  f"blanks a local sqlite file — point DATABASE_URL at one or run it elsewhere.")
        sys.exit(2)

    raw = url.split("sqlite:///", 1)[-1]
    path = Path(raw.lstrip("/") if raw.startswith("/") and ":" in raw[:3] else raw).resolve()
    if not path.is_file():
        log.error(f"No database at {path} — nothing to blank.")
        sys.exit(2)
    return path


def _counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        return {t: con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def _back_up(path: Path) -> Path:
    """Copy, then **read the copy back**. Returns the backup path or exits non-zero.

    A file that exists is not a backup; a file that opens and holds the same rows is. This is the
    marker in front of the irreversible step, and it is worth the extra second because the thing
    it protects — 4,700 grades and 193 students — takes a Playwright run per course to rebuild.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = path.with_name(f"{path.name}.bak-demo-{stamp}")
    try:
        shutil.copy2(path, dest)
    except OSError as e:
        log.error(f"Could not write the backup ({e}). Nothing deleted.")
        sys.exit(3)

    if not dest.is_file() or dest.stat().st_size == 0:
        log.error(f"The backup at {dest} is missing or empty. Nothing deleted.")
        sys.exit(3)

    try:
        before, after = _counts(path), _counts(dest)
    except sqlite3.Error as e:
        log.error(f"The backup does not read back as a database ({e}). Nothing deleted.")
        sys.exit(3)

    drifted = {t: (before[t], after.get(t)) for t in before if before[t] != after.get(t)}
    if drifted:
        log.error(f"The backup disagrees with the original on {drifted}. Nothing deleted.")
        sys.exit(3)

    log.success(f"Backup verified → {dest.name} ({dest.stat().st_size // 1024} KB, "
                f"{sum(before.values())} rows)")
    return dest


def _plan(clear_audit: bool, forget_password: bool, forget_me: bool):
    plan = list(DERIVED)
    if clear_audit:
        plan += list(RECORD)
    if forget_password or forget_me:
        plan.append(("professor_credential", "your encrypted Moodle password — retype in Settings"))
    if forget_me:
        plan.append(("professor", "your professor row — recreated on the next Google sign-in"))
    return plan


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Blank MUSAI's local database for a demo (dry run by default). "
                    "Never touches Moodle and never deletes a file in course_backups/.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete (default: dry run, nothing written)")
    ap.add_argument("--clear-audit", action="store_true",
                    help="Also clear audit_log / ai_usage / usage_counter — NOT re-derivable")
    ap.add_argument("--forget-moodle-password", action="store_true",
                    help="Also drop the stored Moodle credential (retype it in Settings)")
    ap.add_argument("--forget-me", action="store_true",
                    help="Also drop the professor row — demo the true first sign-in")
    args = ap.parse_args()

    path = _db_path()
    counts = _counts(path)
    plan = _plan(args.clear_audit, args.forget_moodle_password, args.forget_me)

    log.header("Reset MUSAI's local database for a demo")
    log.info(f"Database  {path}")
    log.info("Moodle    untouched — this script makes no network request at all")
    log.info("Files     course_backups/, downloads/, backups/, uploads/ all untouched")

    total = 0
    log.header("Would delete" if not args.apply else "Deleting")
    for table, why in plan:
        n = counts.get(table, 0)
        total += n
        (log.step if n else log.info)(f"{n:>6}  {table:<22} {why}")

    kept = [(t, counts.get(t, 0)) for t in UNTOUCHABLE]
    kept += [(t, counts.get(t, 0)) for t, _ in RECORD if not args.clear_audit]
    if not (args.forget_moodle_password or args.forget_me):
        kept.append(("professor_credential", counts.get("professor_credential", 0)))
    if not args.forget_me:
        kept.append(("professor", counts.get("professor", 0)))

    log.header("Keeping")
    for table, n in kept:
        log.info(f"{n:>6}  {table}")

    if total == 0:
        log.success("\nAlready blank — nothing to delete.")
        return

    if not args.apply:
        log.warning(f"\nDRY RUN — nothing written. {total} row(s) would go.")
        log.info("Re-run with --apply. It backs the database up and verifies the copy first.")
        return

    dest = _back_up(path)

    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        deleted = {}
        for table, _ in plan:
            if counts.get(table):
                deleted[table] = con.execute(f'delete from "{table}"').rowcount
        con.commit()
    except sqlite3.Error as e:
        con.rollback()
        log.error(f"Rolled back on {e}. The database is unchanged; the backup is at {dest.name}.")
        sys.exit(4)
    finally:
        con.close()

    after = _counts(path)
    left = {t: after[t] for t, _ in plan if after.get(t)}
    if left:
        log.error(f"These tables still hold rows: {left}")
        sys.exit(4)

    log.success(f"{sum(deleted.values())} row(s) deleted across {len(deleted)} table(s).")
    log.info(f"Undo:  copy {dest.name} back over {path.name} (stop the server first).")
    log.header("The demo, from here")
    log.info("1. Sign in with Google as professor@uach.mx — the professor row and semester appear")
    if args.forget_moodle_password or args.forget_me:
        log.info("2. Settings → store your Moodle password (Check it — a few seconds)")
    log.info("3. Update from Moodle — reads your dashboard, creates your 7 courses (~30 s)")
    log.info("4. A course → Activities → Import activities (~1 min per course)")
    log.info("5. A course → Grades → Refresh from Moodle — students, activities and grades")
    log.info("6. Backup & restore → Create a backup — additive, confirms first, under a minute")


if __name__ == "__main__":
    main()
