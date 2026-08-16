"""Mapping a dashboard, and the rails around a restore that deletes a course's contents.

Everything here runs without a browser. That is deliberate and it is the same doctrine
`restore.py` was built on: **every refusal must happen before anything opens.** A rail that only
fires after a fifteen-minute upload is not a rail, it is a receipt.
"""

import gzip
import io
import tarfile
import time
from datetime import date, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from musai import transfer
from musai.mapping import (Tile, apply_mapping, normalize_group_code, parse_tile, plan_mapping)
from musai.models import Course, Professor, Semester
from musai.professors import get_or_create
from musai.transfer import TransferRefused


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as sess:
        yield sess


# ── the tile parser, which must work outside the English department ──────────
def test_an_fccf_english_tile_parses_the_way_it_always_did():
    t = parse_tile("INGLES I Ciclo: PRIMER SEMESTRE Grupo: 1ED-A", idc="9023", server="virtual3")
    assert t.subject == "INGLES I"
    assert t.group_code == "1-LED-A"
    assert t.level == 1
    assert t.cycle == "PRIMER SEMESTRE"
    assert t.server == "virtual3"


def test_a_course_from_another_faculty_parses_instead_of_being_skipped():
    """🔴 `new_semester._TILE_RX` requires the literal `INGLES` and returns None otherwise.

    A Nursing professor mapping her courses through that parser gets an empty dashboard and no
    explanation — every tile silently "not recognized".
    """
    t = parse_tile("ANATOMIA HUMANA Ciclo: TERCER SEMESTRE Grupo: 3EN-B",
                   idc="6252", server="aulas1")
    assert t is not None
    assert t.subject == "ANATOMIA HUMANA"
    assert t.group_code == "3-LEN-B"
    assert t.cycle == "TERCER SEMESTRE"


def test_a_tile_with_no_recognizable_level_gets_zero_not_one():
    """0 means "this course does not state a level". A fabricated 1 is indistinguishable
    from a real one to everything downstream."""
    t = parse_tile("SEMINARIO DE TITULACION Ciclo: OPTATIVA Grupo: TITUL-A", idc="7001")
    assert t.level == 0


def test_the_roman_numeral_match_is_longest_first():
    """⚠️ `I` is a prefix of `II`, `III` and `IV`.

    `(I|II|III|IV)` matches the `I` inside `INGLES III` and reports level 1 — the same class of
    mistake that would put an INGLES I backup one confirmation away from an INGLES III course.
    """
    assert parse_tile("INGLES III Grupo: 3MH-A", idc="9067").level == 3
    assert parse_tile("INGLES II Grupo: 2ED-B", idc="9048").level == 2
    assert parse_tile("INGLES IV Grupo: 4EF-A", idc="9010").level == 4


def test_a_tile_with_no_course_id_is_not_a_course():
    assert parse_tile("INGLES I Grupo: 1ED-A", idc="") is None


def test_the_group_code_rule_only_applies_to_the_shape_it_was_measured_on():
    assert normalize_group_code("1ED-A") == "1-LED-A"
    assert normalize_group_code("3MH-A") == "3-LMH-A"
    # Anything else passes through rather than being mangled into a code SEGA never heard of.
    assert normalize_group_code("POSGRADO-2026") == "POSGRADO-2026"
    assert normalize_group_code("12ABC-D") == "12ABC-D"


# ── planning a re-map ────────────────────────────────────────────────────────
def _course(idc, code, subject="INGLES I", **kw):
    return Course(semester_id=1, subject=subject, level=1, group_code=code,
                  moodle_course_id=idc, moodle_fullname=kw.pop("raw", ""), **kw)


def test_a_first_map_is_all_new():
    tiles = [parse_tile("INGLES I Grupo: 1ED-A", idc="9023"),
             parse_tile("INGLES I Grupo: 1ED-B", idc="9026")]
    plan = plan_mapping(tiles, [])
    assert len(plan.new) == 2
    assert not plan.updated and not plan.unchanged and not plan.vanished


def test_re_mapping_the_same_dashboard_changes_nothing():
    tile = parse_tile("INGLES I Grupo: 1ED-A", idc="9023")
    existing = _course("9023", "1-LED-A", raw=tile.raw)
    plan = plan_mapping([tile], [existing])
    assert not plan.new and not plan.updated
    assert len(plan.unchanged) == 1


def test_a_renamed_group_is_an_update_not_a_duplicate():
    tile = parse_tile("INGLES I Ciclo: PRIMER SEMESTRE Grupo: 1ED-C", idc="9023")
    plan = plan_mapping([tile], [_course("9023", "1-LED-A")])
    assert not plan.new
    assert len(plan.updated) == 1


def test_a_course_missing_from_the_dashboard_is_reported_and_kept(db):
    """🔴 Never deleted. Deleting a `Course` takes its activities, grades and partial grades
    with it, and a missing tile is far more likely to be a portal hiccup than a real removal."""
    plan = plan_mapping([], [_course("9023", "1-LED-A")])
    assert len(plan.vanished) == 1
    assert not plan.new and not plan.updated

    sem = Semester(name="2026-2", starts_on=date(2026, 7, 1), ends_on=date(2026, 12, 31))
    prof = Professor(email="professor@uach.mx")
    db.add(sem)
    db.add(prof)
    db.commit()
    db.refresh(sem)
    db.refresh(prof)

    kept = _course("9023", "1-LED-A", professor_id=prof.id)
    kept.semester_id = sem.id
    db.add(kept)
    db.commit()

    counts = apply_mapping(db, plan_mapping([], [kept]), professor_id=prof.id,
                           semester_id=sem.id)
    assert counts["vanished"] == 1
    assert db.get(Course, kept.id) is not None      # still there


def test_applying_a_map_creates_the_course_with_its_default_partials(db):
    from musai.models import Partial

    sem = Semester(name="2026-2", starts_on=date(2026, 7, 1), ends_on=date(2026, 12, 31))
    prof = Professor(email="colleague4@uach.mx")
    db.add(sem)
    db.add(prof)
    db.commit()
    db.refresh(sem)
    db.refresh(prof)

    tile = parse_tile("INGLES IV Ciclo: SEPTIMO SEMESTRE Grupo: 4EF-B", idc="9012",
                      server="virtual3")
    counts = apply_mapping(db, plan_mapping([tile], []), professor_id=prof.id,
                           semester_id=sem.id)

    assert counts["created"] == 1
    course = db.exec(__import__("sqlmodel").select(Course)).first()
    assert course.professor_id == prof.id       # owned from the moment it exists
    assert course.moodle_course_id == "9012"
    assert course.moodle_server == "virtual3"
    assert course.level == 4
    assert len(db.exec(__import__("sqlmodel").select(Partial)).all()) == 3


def test_a_remap_never_reassigns_a_course_to_whoever_ran_it(db):
    sem = Semester(name="2026-2", starts_on=date(2026, 7, 1), ends_on=date(2026, 12, 31))
    carlos = Professor(email="professor@uach.mx")
    morayma = Professor(email="colleague4@uach.mx")
    db.add_all([sem, carlos, morayma])
    db.commit()
    for o in (sem, carlos, morayma):
        db.refresh(o)

    course = _course("9023", "1-LED-A", professor_id=carlos.id)
    course.semester_id = sem.id
    db.add(course)
    db.commit()

    tile = parse_tile("INGLES I Ciclo: NUEVO Grupo: 1ED-A", idc="9023")
    apply_mapping(db, plan_mapping([tile], [course]), professor_id=morayma.id,
                  semester_id=sem.id)
    db.refresh(course)
    assert course.professor_id == carlos.id


# ── uploads ───────────────────────────────────────────────────────────────────
def _mbz_bytes(*, course_id="9010", fullname="INGLES IV 4EF-A", users="0",
              activities=3) -> bytes:
    """A minimal but genuine `.mbz`: a gzipped tar whose root holds `moodle_backup.xml`."""
    acts = "".join(f"<activity><moduleid>{i}</moduleid></activity>" for i in range(activities))
    xml = (f"<moodle_backup><information>"
           f"<moodle_release>3.3</moodle_release>"
           f"<original_course_id>{course_id}</original_course_id>"
           f"<original_course_fullname>{fullname}</original_course_fullname>"
           f"<original_course_shortname>{fullname}</original_course_shortname>"
           f"<contents><activities>{acts}</activities></contents>"
           f"</information>"
           f"<settings><setting><name>users</name><value>{users}</value></setting></settings>"
           f"</moodle_backup>").encode()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo("moodle_backup.xml")
        info.size = len(xml)
        tar.addfile(info, io.BytesIO(xml))
    return gzip.compress(raw.getvalue())


@pytest.fixture
def prof(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "UPLOAD_ROOT", tmp_path / "uploads")
    return Professor(id=1, email="colleague4@uach.mx")


def test_a_real_mbz_is_accepted_and_read(prof):
    path = transfer.save_upload(prof, "respaldo.mbz", _mbz_bytes())
    assert path.is_file()
    info = transfer.describe_archive(path)
    assert info["course_id"] == "9010"
    assert info["activities"] == 3
    assert info["includes_users"] is False


def test_a_file_that_is_not_an_mbz_is_refused_by_name(prof):
    with pytest.raises(TransferRefused) as e:
        transfer.save_upload(prof, "notes.pdf", _mbz_bytes())
    assert ".mbz" in str(e.value)


def test_an_html_page_saved_as_an_mbz_is_refused_before_it_is_kept(prof):
    """A real failure mode on this Moodle — an expired session serves HTML where the archive
    should be. Caught here instead of fifteen minutes into a restore."""
    with pytest.raises(TransferRefused) as e:
        transfer.save_upload(prof, "respaldo.mbz", b"<!DOCTYPE html><html>Session expired")
    assert "gzip" in str(e.value)
    assert list(transfer.upload_dir(prof).glob("*")) == []


def test_a_gzip_that_is_not_a_moodle_backup_is_not_kept(prof):
    with pytest.raises(TransferRefused) as e:
        transfer.save_upload(prof, "random.mbz", gzip.compress(b"just some bytes"))
    assert "moodle_backup.xml" in str(e.value)
    assert list(transfer.upload_dir(prof).glob("*")) == []


def test_an_oversize_upload_is_refused_against_moodles_measured_limit(prof, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_UPLOAD_BYTES", 1024)
    with pytest.raises(TransferRefused) as e:
        transfer.save_upload(prof, "big.mbz", _mbz_bytes() + b"\x00" * 2048)
    assert "limit" in str(e.value)


def test_an_empty_upload_is_refused(prof):
    with pytest.raises(TransferRefused):
        transfer.save_upload(prof, "empty.mbz", b"")


def test_two_professors_uploads_do_not_collide(tmp_path, monkeypatch):
    """Moodle's filenames are not unique across accounts — both would be "the newest .mbz"."""
    monkeypatch.setattr(transfer, "UPLOAD_ROOT", tmp_path / "uploads")
    a = transfer.upload_dir(Professor(id=1, email="professor@uach.mx"))
    b = transfer.upload_dir(Professor(id=2, email="colleague4@uach.mx"))
    assert a != b


# ── the archive guard: whose students are in this file? ──────────────────────
def _course_row():
    return Course(id=7, semester_id=1, subject="Inglés IV", level=4, group_code="4-LEF-B",
                  moodle_course_id="9012")


def test_an_archive_carrying_user_data_is_refused():
    with pytest.raises(TransferRefused) as e:
        transfer._guard_archive({"includes_users": True}, into_course=_course_row())
    assert "4-LEF-B" in str(e.value)


def test_an_archive_that_cannot_say_is_refused_the_same_as_one_that_carries_users():
    """🔴 `None` means "could not tell". `backup.carries_user_data` documents that callers must
    read an unknown as a yes — an unknown is not a no."""
    with pytest.raises(TransferRefused):
        transfer._guard_archive({"includes_users": None}, into_course=_course_row())
    with pytest.raises(TransferRefused):
        transfer._guard_archive({}, into_course=_course_row())


def test_a_clean_archive_passes():
    transfer._guard_archive({"includes_users": False}, into_course=_course_row())


# ── the pre-flight token: a live restore cannot run without a fresh look ─────
def _stash_preflight(db, monkeypatch, **overrides):
    """Write a JobRequest holding a pre-flight result, the way `_check_work` does.

    ⚠️ Both engines are redirected. `musai.transfer` does `from musai.db import engine` at
    import time, so patching `musai.db.engine` alone leaves the module still talking to the
    real dev database — the test then passes or fails for reasons having nothing to do with
    what it is about.
    """
    import json

    import musai.db as db_mod
    from musai.models import JobRequest

    monkeypatch.setattr(db_mod, "engine", db.get_bind())
    monkeypatch.setattr(transfer, "engine", db.get_bind())
    pf = {"ok": True, "course_id": 7, "idc": "9012", "target_name": "INGLES IV 4EF-B",
          "target_activities": 12, "target_sections": 4, "grades_held": 0,
          "checked_at": time.time(), "refusal": ""}
    pf.update(overrides)
    job = JobRequest(kind="course_restore:check", requested_by="colleague4@uach.mx",
                     status="done", result_json=json.dumps({"preflight": pf}))
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id


def test_a_fresh_passing_preflight_licenses_the_restore(db, monkeypatch):
    job_id = _stash_preflight(db, monkeypatch)
    pf = transfer.consume_preflight(job_id, owner="colleague4@uach.mx", course_id=7)
    assert pf.ok and pf.target_name == "INGLES IV 4EF-B"


def test_a_preflight_for_a_different_course_does_not_license_this_one(db, monkeypatch):
    """🔴 A check against 9048 must not authorise a restore into 9046."""
    job_id = _stash_preflight(db, monkeypatch)
    with pytest.raises(TransferRefused) as e:
        transfer.consume_preflight(job_id, owner="colleague4@uach.mx", course_id=8)
    assert "different course" in str(e.value)


def test_another_professors_preflight_is_not_visible_at_all(db, monkeypatch):
    job_id = _stash_preflight(db, monkeypatch)
    with pytest.raises(TransferRefused):
        transfer.consume_preflight(job_id, owner="professor@uach.mx", course_id=7)


def test_a_stale_preflight_is_refused(db, monkeypatch):
    """It measured what was in the course at the time — a cache with no invalidation."""
    job_id = _stash_preflight(db, monkeypatch,
                              checked_at=time.time() - transfer.PREFLIGHT_TTL_S - 60)
    with pytest.raises(TransferRefused) as e:
        transfer.consume_preflight(job_id, owner="colleague4@uach.mx", course_id=7)
    assert "old" in str(e.value)


def test_a_refused_preflight_cannot_be_used_to_restore(db, monkeypatch):
    job_id = _stash_preflight(db, monkeypatch, ok=False, refusal="Subject mismatch")
    with pytest.raises(TransferRefused) as e:
        transfer.consume_preflight(job_id, owner="colleague4@uach.mx", course_id=7)
    assert "Subject mismatch" in str(e.value)


def test_a_preflight_that_never_existed_is_refused(db, monkeypatch):
    import musai.db as db_mod

    monkeypatch.setattr(db_mod, "engine", db.get_bind())
    with pytest.raises(TransferRefused):
        transfer.consume_preflight(424242, owner="colleague4@uach.mx", course_id=7)


# ── the restore's own refusals, all before a browser exists ─────────────────
def test_a_course_with_no_moodle_id_cannot_be_restored_into(prof, db, monkeypatch):
    course = Course(id=7, semester_id=1, subject="Inglés", level=1, group_code="X",
                    moodle_course_id=None)
    with pytest.raises(TransferRefused) as e:
        transfer.run_restore(prof, course, "whatever.mbz", preflight_job_id=1)
    assert "re-map" in str(e.value).lower()


def test_a_missing_upload_is_refused_rather_than_opening_a_browser(prof, tmp_path):
    with pytest.raises(TransferRefused) as e:
        transfer.run_restore(prof, _course_row(), tmp_path / "gone.mbz", preflight_job_id=1)
    assert "no longer on disk" in str(e.value)


def test_a_restore_is_refused_while_musai_holds_grades(prof, db, monkeypatch, tmp_path):
    """🔴 A restore wipes the gradebook with everything else."""
    path = transfer.save_upload(prof, "ok.mbz", _mbz_bytes(course_id="9012"))
    job_id = _stash_preflight(db, monkeypatch, grades_held=41)
    with pytest.raises(TransferRefused) as e:
        transfer.run_restore(prof, _course_row(), path, preflight_job_id=job_id)
    assert "41 grades" in str(e.value)


def test_the_grade_guard_can_be_overridden_deliberately(prof, db, monkeypatch):
    """`force=True` gets past the gradebook guard — and then still needs a Moodle password,
    which is the next refusal. The point is that the guard was the thing that moved."""
    path = transfer.save_upload(prof, "ok.mbz", _mbz_bytes(course_id="9012"))
    job_id = _stash_preflight(db, monkeypatch, grades_held=41)
    with pytest.raises(TransferRefused) as e:
        transfer.run_restore(prof, _course_row(), path, preflight_job_id=job_id, force=True)
    assert "grades" not in str(e.value)
    assert "password" in str(e.value).lower()


def test_a_dirty_archive_is_refused_before_the_preflight_is_even_consulted(prof, db,
                                                                          monkeypatch):
    path = transfer.save_upload(prof, "dirty.mbz", _mbz_bytes(users="1"))
    with pytest.raises(TransferRefused) as e:
        transfer.run_restore(prof, _course_row(), path, preflight_job_id=999999)
    assert "user data" in str(e.value)
