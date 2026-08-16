"""Blanking the local database for a demo, and the four ways that could go badly.

The owner asked (2026-08-14) for a safe way to blank MUSAI so he could demo the cockpit from first
sign-in with his own account. "Safe" here has four separate meanings, and this file pins each:

1. **It must not reach Moodle.** Everything it deletes is a local copy; the demo *is* re-reading it.
2. **It must not delete a file.** `course_backups/english_iv_master_20260812.mbz` is the archive
   Colleague D restores her English IV course from, and a 45-minute run to rebuild.
3. **It must not be able to hit Postgres.** The only non-sqlite URL this project has is the Replit
   production database, and this script's whole job is a mass DELETE.
4. 🔴 **Nothing may be deleted before a verified backup exists.** Not a written file — a *read
   back* one. The lesson from `feedback_attempt_marker_before_irreversible`.

Plus the subtle one that is not about safety at all: SQLite reissues `INTEGER PRIMARY KEY` values
from `max(id) + 1`, so an emptied `course` table hands out id 1 again. Any surviving row keyed by
course id would re-attach itself to a different group.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from musai import reset_demo

ROOT = Path(__file__).resolve().parent.parent


def _schema_db() -> Path:
    """The database the suite is bound to — a copy, never `musai_dev.db`.

    Used only to read `sqlite_master` and `pragma foreign_key_list`, i.e. the schema. Pointed at
    the copy rather than the real file so this module obeys the same rule as the rest of the
    suite: be structurally unable to touch the real thing, not merely careful.
    """
    from musai.db import engine

    return Path(engine.url.database)


# ── the plan's shape ──────────────────────────────────────────────────────────
def test_the_plan_is_child_first_so_no_row_is_ever_orphaned():
    """Derived from the database's OWN foreign keys, not from a list kept alongside the plan.

    A hand-maintained ordering is right until someone adds a table. This reads
    `pragma foreign_key_list` off the real schema, so the test knows about a new table the moment
    it exists.
    """
    plan = [t for t, _ in reset_demo._plan(True, True, True)]
    position = {t: i for i, t in enumerate(plan)}

    con = sqlite3.connect(f"file:{_schema_db().as_posix()}?mode=ro", uri=True)
    try:
        wrong = []
        for child in plan:
            for fk in con.execute(f'pragma foreign_key_list("{child}")'):
                parent = fk[2]
                if parent in position and position[parent] < position[child]:
                    wrong.append(f"{child} is deleted after its parent {parent}")
    finally:
        con.close()

    assert not wrong, (
        "the deletion order would orphan rows (and fails outright under PRAGMA foreign_keys=ON):\n"
        + "\n".join(wrong))


def test_every_table_in_the_database_is_either_planned_or_deliberately_kept():
    """🔴 Enumerated from the schema, so a table added next month cannot silently survive a reset.

    Same shape as `test_route_scoping.py` walking `app.routes`: the inventory that matters is the
    system's, never the one written next to the code. A new table that nobody classified is a table
    that quietly outlives the blanking and then contradicts the freshly mapped courses.
    """
    con = sqlite3.connect(f"file:{_schema_db().as_posix()}?mode=ro", uri=True)
    try:
        actual = {r[0] for r in con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
    finally:
        con.close()

    classified = {t for t, _ in reset_demo._plan(True, True, True)} | set(reset_demo.UNTOUCHABLE)
    unclassified = actual - classified
    assert not unclassified, (
        f"these tables are in the database but in no group — decide, in musai/reset_demo.py, "
        f"whether a demo reset should empty them: {sorted(unclassified)}")


def test_the_tables_keyed_by_course_id_are_always_deleted_with_the_courses():
    """🔴 The id-recycling hazard, and it is a correctness bug rather than untidiness.

    `course_hub` and `course_schedule` are keyed by `course_id`. Empty the `course` table and the
    next mapped course is id 1 again, so a leftover hub for old course 8 would render as the hub of
    whatever group later becomes 8 — with a different group's WhatsApp link on it. That is exactly
    the class of mistake `project_carlos_heroes` exists to remember.
    """
    plan = [t for t, _ in reset_demo._plan(False, False, False)]
    for table in ("course_hub", "course_schedule", "activity", "enrollment", "partial"):
        assert table in plan, (
            f"{table} is keyed by course id but would survive a reset that empties `course`. "
            f"SQLite reuses ids, so those rows would re-attach to different courses.")
    assert "course" in plan


def test_the_record_and_the_identity_survive_by_default():
    """The two things a demo does not need blanked, and one of them is not re-derivable."""
    default = {t for t, _ in reset_demo._plan(False, False, False)}
    assert "audit_log" not in default, (
        "audit_log holds MUSAI's only account of 29 restores and 62 activity deletions against "
        "live courses. It must take an explicit flag.")
    assert "professor" not in default and "professor_credential" not in default, (
        "a plain reset must not force the owner to retype his Moodle password mid-demo.")

    assert "audit_log" in {t for t, _ in reset_demo._plan(True, False, False)}
    assert "professor_credential" in {t for t, _ in reset_demo._plan(False, True, False)}
    assert "professor" in {t for t, _ in reset_demo._plan(False, False, True)}


def test_alembic_version_and_the_hub_profile_are_reachable_by_no_flag():
    """Dropping `alembic_version` strands every migration; `hub_profile` is typed-once content."""
    every_flag = {t for t, _ in reset_demo._plan(True, True, True)}
    for table in reset_demo.UNTOUCHABLE:
        assert table not in every_flag, f"{table} is deletable via a flag — it must not be."
    assert "alembic_version" in reset_demo.UNTOUCHABLE
    assert "hub_profile" in reset_demo.UNTOUCHABLE


# ── it cannot reach the things it must not reach ──────────────────────────────
def test_it_refuses_a_non_sqlite_database_with_no_way_to_override():
    """🔴 The Replit Postgres is the only non-sqlite URL this project has."""
    import argparse

    src = Path(reset_demo.__file__).read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))

    parser_flags = []
    ap = argparse.ArgumentParser()
    for line in code.splitlines():
        if "add_argument(" in line:
            parser_flags.append(line)
    assert not any("force" in f or "postgres" in f.lower() for f in parser_flags), (
        "a flag was added that could point this mass DELETE at a non-sqlite database.")
    del ap

    env = dict(os.environ, DATABASE_URL="postgresql://user:pw@host/db")
    out = subprocess.run([sys.executable, "-m", "musai.reset_demo", "--apply"],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    assert out.returncode == 2, (out.stdout, out.stderr)
    assert "not sqlite" in (out.stdout + out.stderr)


def test_it_makes_no_network_call_and_imports_no_browser():
    """Structural, not behavioural: the module must not be able to reach Moodle at all.

    A comment promising "read-only" is worth nothing; an import list that contains no HTTP client
    and no Playwright is worth something. `_log` and `config` are the only musai imports it needs.
    """
    src = Path(reset_demo.__file__).read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("#", '"', "'")))
    for forbidden in ("playwright", "requests", "httpx", "urllib", "credentials",
                      "automation.backup", "automation.restore", "moodle_export"):
        assert forbidden not in code, (
            f"reset_demo imports/mentions {forbidden!r} in live code — this script must be "
            f"structurally incapable of touching Moodle.")


def test_it_deletes_no_file_on_disk():
    """`course_backups/` holds Colleague D's English IV master. Nothing here may remove a file."""
    src = Path(reset_demo.__file__).read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("#", '"', "'")))
    for forbidden in ("rmtree", "os.remove", ".unlink(", "os.rmdir"):
        assert forbidden not in code, (
            f"{forbidden} appears in reset_demo — the archives in course_backups/ cost 45-minute "
            f"runs each and one of them is the file Colleague D restores from.")


# ── end to end, on a throwaway copy ──────────────────────────────────────────
#: What `db_copy` guarantees exists. Small, but at least one row in every table the assertions
#: name, so the numbers below are the fixture's own and not a snapshot of the owner's machine.
SEED = {"semester": 1, "course": 2, "student": 3, "enrollment": 3, "activity": 2,
        "grade": 4, "course_hub": 1, "course_schedule": 1, "job_request": 2,
        "professor": 1, "professor_credential": 1, "audit_log": 2, "hub_profile": 1,
        "alembic_version": 1}


@pytest.fixture
def db_copy(tmp_path) -> Path:
    """A throwaway database with the real schema and a **known** seed.

    🔴 The first version of this fixture copied `musai_dev.db` and the tests asserted
    `_count("course") == 14` and `_count("grade") == 4739`. Those numbers were the owner's live data,
    so the moment this very script blanked it for a demo, four of these tests went red while the
    code they test was perfect. A test that asserts a fact about the developer's machine reports on
    the machine, not on MUSAI — the same defect as `_no_real_delegate_passwords`, one layer over.

    Built with the real models rather than raw INSERTs so NOT NULL columns and defaults come from
    the schema, and a migration that adds a required column fails here loudly instead of drifting.
    """
    from sqlmodel import Session, SQLModel, create_engine

    from musai.models import (Activity, AuditLog, Course, CourseHub, CourseSchedule, Enrollment,
                              Grade, HubProfile, JobRequest, Professor, ProfessorCredential,
                              Semester, Student)

    dest = tmp_path / "musai_demo_seed.db"
    eng = create_engine(f"sqlite:///{dest.as_posix()}")
    SQLModel.metadata.create_all(eng)

    from datetime import date, datetime

    with Session(eng) as s:
        s.add(HubProfile(owner="web:test", data_json="{}"))
        prof = Professor(email="professor@uach.mx", full_name="the owner")
        s.add(prof)
        s.commit()
        s.refresh(prof)
        s.add(ProfessorCredential(professor_id=prof.id, system="moodle", username="professor",
                                  secret_enc="not-a-real-token"))
        sem = Semester(name="2026-2", starts_on=date(2026, 7, 1), ends_on=date(2026, 12, 31),
                       is_active=True)
        s.add(sem)
        s.commit()
        s.refresh(sem)

        courses = []
        for group, idc in (("1-LED-A", "9023"), ("3-LED-B", "9072")):
            c = Course(semester_id=sem.id, professor_id=prof.id, subject="INGLES", level=1,
                       group_code=group, moodle_course_id=idc, moodle_env="prod")
            s.add(c)
            courses.append(c)
        s.commit()
        for c in courses:
            s.refresh(c)

        s.add(CourseHub(course_id=courses[0].id, data_json="{}"))
        s.add(CourseSchedule(course_id=courses[0].id))

        students = [Student(full_name=f"Alumno {i}", matricula=f"a{i:06d}") for i in range(3)]
        for st in students:
            s.add(st)
        s.commit()
        for st in students:
            s.refresh(st)
        for st in students:
            s.add(Enrollment(course_id=courses[0].id, student_id=st.id))

        acts = [Activity(course_id=courses[0].id, name=f"Task {i}", category="general",
                         max_points=10.0) for i in range(2)]
        for a in acts:
            s.add(a)
        s.commit()
        for a in acts:
            s.refresh(a)
        for a in acts:
            for st in students[:2]:
                s.add(Grade(activity_id=a.id, student_id=st.id, value=80.0))

        for kind in ("map_courses", "course_backup"):
            s.add(JobRequest(kind=kind, requested_by=prof.email, status="done"))
        for action in ("course_backup", "course_restore"):
            s.add(AuditLog(action=action, actor="carlos", dry_run=False,
                           created_at=datetime.utcnow()))
        s.commit()
    eng.dispose()

    # Alembic's own table, which `create_all` does not make. Seeded so "migrations survive a
    # reset" is a real assertion rather than a check against a table that is not there.
    con = sqlite3.connect(dest)
    con.execute("create table if not exists alembic_version (version_num varchar(32) not null)")
    con.execute("insert into alembic_version values ('b7c8d9e0f1a2')")
    con.commit()
    con.close()

    # The fixture must actually hold what it claims, or every assertion below is vacuous.
    wrong = {t: (_count(dest, t), n) for t, n in SEED.items() if _count(dest, t) != n}
    assert not wrong, f"the seed does not match SEED (actual, expected): {wrong}"
    return dest


def _run(db: Path, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db.as_posix()}",
               DATABASE_URL_READONLY=f"sqlite:///{db.as_posix()}")
    return subprocess.run([sys.executable, "-m", "musai.reset_demo", *args],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def _count(db: Path, table: str) -> int:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute(f'select count(*) from "{table}"').fetchone()[0]
    finally:
        con.close()


def test_a_dry_run_writes_absolutely_nothing(db_copy):
    before = db_copy.stat().st_mtime_ns, _count(db_copy, "course"), _count(db_copy, "grade")
    out = _run(db_copy)
    assert out.returncode == 0, out.stderr
    assert "DRY RUN" in out.stdout
    assert (db_copy.stat().st_mtime_ns, _count(db_copy, "course"),
            _count(db_copy, "grade")) == before
    assert not list(db_copy.parent.glob("*.bak-demo-*")), (
        "a dry run wrote a backup — it should not have touched the disk at all.")


def test_apply_blanks_the_cockpit_and_keeps_the_identity_and_the_record(db_copy):
    out = _run(db_copy, "--apply")
    assert out.returncode == 0, out.stdout + out.stderr

    for table in ("course", "student", "grade", "activity", "enrollment", "semester",
                  "job_request", "course_hub", "course_schedule"):
        assert _count(db_copy, table) == 0, f"{table} still holds rows after --apply"

    assert _count(db_copy, "professor") == SEED["professor"], (
        "the professor row was dropped without --forget-me")
    assert _count(db_copy, "professor_credential") == SEED["professor_credential"], (
        "the Moodle password was dropped without --forget-moodle-password — the owner would hit "
        "Settings mid-demo.")
    assert _count(db_copy, "audit_log") == SEED["audit_log"], (
        "the audit record was cleared without --clear-audit")
    assert _count(db_copy, "alembic_version") == SEED["alembic_version"], (
        "migrations were stranded — alembic_version must be untouchable by any flag")
    assert _count(db_copy, "hub_profile") == SEED["hub_profile"]


def test_apply_verifies_a_readable_backup_before_deleting_anything(db_copy):
    """🔴 The backup is a precondition. It must exist, open, and agree with the original."""
    out = _run(db_copy, "--apply")
    assert out.returncode == 0
    assert "Backup verified" in out.stdout

    backups = list(db_copy.parent.glob("*.bak-demo-*"))
    assert len(backups) == 1, f"expected exactly one backup, found {backups}"
    for table, n in SEED.items():
        assert _count(backups[0], table) == n, (
            f"the backup holds {_count(backups[0], table)} {table} row(s), not the {n} that were "
            f"there before the delete — it is not an undo.")


def test_nothing_is_deleted_when_the_backup_cannot_be_written(db_copy, monkeypatch):
    """In-process, because the point is what happens *after* the copy fails."""
    monkeypatch.setattr(reset_demo.settings, "database_url",
                        f"sqlite:///{db_copy.as_posix()}", raising=False)

    def refuse(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(reset_demo.shutil, "copy2", refuse)
    monkeypatch.setattr(sys, "argv", ["reset_demo", "--apply"])

    with pytest.raises(SystemExit) as exc:
        reset_demo.main()
    assert exc.value.code == 3
    for table, n in SEED.items():
        assert _count(db_copy, table) == n, (
            f"{table} lost rows even though the backup failed — that is the unrecoverable case.")


def test_forget_me_leaves_a_database_a_first_sign_in_can_populate(db_copy):
    out = _run(db_copy, "--apply", "--forget-me")
    assert out.returncode == 0, out.stdout + out.stderr
    assert _count(db_copy, "professor") == 0
    assert _count(db_copy, "professor_credential") == 0
    assert _count(db_copy, "alembic_version") == 1, (
        "even a full forget must leave the migration state alone.")
