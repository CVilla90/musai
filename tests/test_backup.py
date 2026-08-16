"""The pure half of `musai/automation/backup.py`: reading a .mbz, and picking the right one.

The wizard needs a live Moodle and is exercised by `--group X` (dry run, two page loads). What
is testable here is the part that decides **which file** and **what is in it** — and those are
exactly the two questions whose wrong answers are silent:

* picking the wrong `.mbz` hands a colleague another course's content;
* misreading `users` hands a colleague your students.
"""

import io
import tarfile
from pathlib import Path

import pytest

from musai.automation import backup as B


# ── building a fake .mbz ──────────────────────────────────────────────────────
def _manifest(course_id: str = "9023", users: str = "0", *, fullname: str = "1ED-A - INGLES I",
              activities: int = 3) -> str:
    acts = "".join(f"<activity><moduleid>{i}</moduleid></activity>" for i in range(activities))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<moodle_backup>
  <information>
    <name>respaldo.mbz</name>
    <moodle_release>3.3 (Build: 20170515)</moodle_release>
    <backup_date>1786000000</backup_date>
    <original_course_id>{course_id}</original_course_id>
    <original_course_fullname>{fullname}</original_course_fullname>
    <original_course_shortname>{fullname}- 5500</original_course_shortname>
    <contents><activities>{acts}</activities></contents>
    <settings>
      <setting><level>root</level><name>filename</name><value>respaldo.mbz</value></setting>
      <setting><level>root</level><name>users</name><value>{users}</value></setting>
      <setting><level>root</level><name>anonymize</name><value>0</value></setting>
    </settings>
  </information>
  <settings>
    <setting><level>root</level><name>users</name><value>{users}</value></setting>
    <setting><level>root</level><name>anonymize</name><value>0</value></setting>
  </settings>
</moodle_backup>"""


def _write_mbz(path: Path, xml: str, *, member: str = "moodle_backup.xml") -> Path:
    raw = xml.encode("utf-8")
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    return path


# ── inspect_mbz ───────────────────────────────────────────────────────────────
def test_reads_the_course_identity_out_of_the_archive(tmp_path):
    p = _write_mbz(tmp_path / "b.mbz", _manifest("9023", "0"))
    info = B.inspect_mbz(p)
    assert info["ok"]
    assert info["course_id"] == "9023"
    assert info["fullname"] == "1ED-A - INGLES I"
    assert info["activities"] == 3
    assert info["includes_users"] is False


def test_a_backup_with_user_data_is_reported_as_such(tmp_path):
    p = _write_mbz(tmp_path / "b.mbz", _manifest("9023", "1"))
    assert B.inspect_mbz(p)["includes_users"] is True
    assert B.carries_user_data(p) is True


def test_a_per_activity_userinfo_cannot_overwrite_the_root_answer(tmp_path):
    """🔴 `.//settings/setting` walks the per-activity settings too.

    A course backup carries one `users` at the root and a `<mod>_<id>_userinfo` per activity. If
    a later element could overwrite the first, one oddly-named activity setting would flip the
    verdict on the only question this function exists to answer.
    """
    xml = _manifest("9023", "1").replace(
        "</moodle_backup>",
        "<settings><setting><level>activity</level><name>users</name>"
        "<value>0</value></setting></settings></moodle_backup>")
    assert B.inspect_mbz(_write_mbz(tmp_path / "b.mbz", xml))["includes_users"] is True


def test_an_unreadable_file_is_not_a_backup(tmp_path):
    p = tmp_path / "b.mbz"
    p.write_bytes(b"not a gzip at all")
    info = B.inspect_mbz(p)
    assert info["ok"] is False and "error" in info


def test_a_tar_without_a_manifest_is_not_a_backup(tmp_path):
    p = _write_mbz(tmp_path / "b.mbz", "<x/>", member="something_else.xml")
    info = B.inspect_mbz(p)
    assert info["ok"] is False
    assert "moodle_backup.xml" in info["error"]


def test_a_missing_file_reports_rather_than_raises(tmp_path):
    info = B.inspect_mbz(tmp_path / "nope.mbz")
    assert info["ok"] is False and info["bytes"] == 0


def test_carries_user_data_returns_none_when_it_cannot_tell(tmp_path):
    """None means 'unknown', and callers must read it as 'assume yes'."""
    xml = _manifest("9023", "0").replace("<name>users</name>", "<name>usuarios</name>")
    assert B.carries_user_data(_write_mbz(tmp_path / "b.mbz", xml)) is None


# ── picking the file this run produced ────────────────────────────────────────
BASE = "https://virtual3.uach.mx/pluginfile.php/1238962/user/backup/"
OLD_9023 = BASE + "respaldo-moodle2-course-9023-1ed-a-20260810-1128-nu.mbz?forcedownload=1"
NEW_9023 = BASE + "respaldo-moodle2-course-9023-1ed-a-20260810-1146-nu.mbz?forcedownload=1"
OTHER = BASE + "respaldo-moodle2-course-9048-2ed-b-20260810-1200-nu.mbz?forcedownload=1"


def test_takes_the_file_that_appeared():
    assert B._pick_new_backup([OLD_9023], [OLD_9023, NEW_9023], "9023") == NEW_9023


def test_never_takes_the_newest_when_the_newest_is_another_course():
    """🔴 The private backup area is per-user and cumulative, not per-course.

    A 2-LED-B backup made a minute later is the newest file in it. "Newest wins" would hand
    9048's content to whoever asked for 9023.
    """
    assert B._pick_new_backup([OLD_9023], [OLD_9023, OTHER], "9023") == OLD_9023


def test_falls_back_to_the_newest_of_this_course_when_two_appeared():
    """A failed earlier run leaves a file behind, so 'exactly one is new' is not guaranteed."""
    got = B._pick_new_backup([], [OLD_9023, NEW_9023, OTHER], "9023")
    assert got == NEW_9023


def test_refuses_when_nothing_for_this_course_is_there():
    with pytest.raises(B.BackupAborted, match="No backup for course 9023"):
        B._pick_new_backup([OTHER], [OTHER], "9023")


def test_the_filename_survives_url_escaping():
    href = BASE + "respaldo%2Dmoodle2%2Dcourse%2D9023%2Da%2D20260810%2D1146%2Dnu.mbz?f=1"
    assert B._name_of(href).endswith("-20260810-1146-nu.mbz")


def test_credentials_are_checked_before_a_browser_exists(monkeypatch):
    monkeypatch.setattr(B.settings, "uach_username", "", raising=False)
    monkeypatch.setattr(B.settings, "uach_password", "", raising=False)
    with pytest.raises(B.BackupAborted, match="credentials missing"):
        B.backup_course(idc="9023")


# ── the forward control, across Moodle versions ───────────────────────────────
# 🔴 Paid for on 2026-08-13: Colleague B's EGB1A backup finished on `aulas1` (Moodle 4.5) and the
# page said «El proceso de respaldo ha completado exitosamente» at 100 %, while `_wait_for_build`
# waited out all 20 minutes and raised a timeout. Its success test is
# `if has_continue and (ok_rx.search(body) or …)` — so the WIDGET selector is the gate, and it
# only knew 3.3's `<input type="submit">`. The archive existed the whole time.
#
# These pin the two halves separately, because they failed for different reasons: the text
# signal was right on both versions, and the selector was not.
def test_continue_selector_covers_both_input_and_button_shapes():
    """3.3 renders the forward control as an input; 4.5 renders it as a button."""
    sel = B._CONTINUE_SEL
    assert 'input[type="submit"][value*="Continuar"]' in sel, "Moodle 3.3 shape (virtual3)"
    assert 'button:has-text("Continuar")' in sel, "Moodle 4.5 shape (aulas1) — the one that bit"
    # English instances of either version.
    assert "Continue" in sel


def test_the_success_text_moodle_45_actually_prints_is_recognised():
    """The exact sentence off `aulas1`'s completed page, which matched all along."""
    import re
    ok_rx = re.compile(r"(respaldo|backup).{0,40}(exitosa|exitosamente|successfully)", re.I)
    assert ok_rx.search("El proceso de respaldo ha completado exitosamente.")
    assert ok_rx.search("The backup file was successfully created.")
