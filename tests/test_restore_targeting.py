"""The targeting rails: refusing a restore whose destination is not the one that was asked for.

A restore deletes the destination's contents before it writes anything. Everything else in
`restore.py` protects the *source*; these protect the *target*, and they matter most exactly
when the target belongs to someone else — a colleague's account can reach every course they
teach, in every school, so a mistyped `idc` opens a perfectly valid course and every later check
passes.

All pure. They must refuse before a browser exists.
"""

import pytest

from musai.automation import restore as R


# ── subject_of ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,want", [
    ("1ED-A - INGLES I - 542992", "INGLES I"),
    ("1MH-B - INGLES I - 5500 - 01 - 544081", "INGLES I"),
    ("2ED-B - INGLES II - 544064", "INGLES II"),
    ("3MH-A - INGLES III - 542018", "INGLES III"),
    ("4EF-B - INGLES IV - 543871", "INGLES IV"),
    ("Curso: 1EF-A - INGLÉS I - 548311", "INGLES I"),
    ("MV71 - INGLES TECNICO LAE - 550863", None),
    ("TALLER DE LECTURA", None),
    ("", None),
    (None, None),
])
def test_subject_of(name, want):
    assert R.subject_of(name) == want


def test_roman_numerals_are_matched_longest_first():
    """🔴 `INGLES I` is a prefix of `INGLES II` and `INGLES III`.

    An alternation ordered `(I|II|III|IV)` matches the `I` inside `INGLES III` and reports the
    course as INGLES I — which would let an INGLES I backup wipe an INGLES III course while the
    check printed a match.
    """
    assert R.subject_of("3ED-A - INGLES III - 543015") != R.subject_of("1ED-A - INGLES I - 1")
    assert R.subject_of("3ED-A - INGLES III - 543015") == "INGLES III"


# ── verify_target ─────────────────────────────────────────────────────────────
BACKUP = "1ED-A - INGLES I - 542992"


def test_the_happy_path_passes():
    R.verify_target(target_name="1MH-B - INGLES I - 5500 - 01 - 544081",
                    expect_course_name="1MH-B", backup_name=BACKUP, strict=True)


def test_a_mistyped_idc_that_opens_a_valid_course_is_refused():
    with pytest.raises(R.RestoreAborted, match="Target check failed"):
        R.verify_target(target_name="1EF-B - INGLES I - 543820",
                        expect_course_name="1MH-B", backup_name=BACKUP, strict=True)


def test_an_english_one_backup_may_not_land_in_an_english_two_course():
    with pytest.raises(R.RestoreAborted, match="Subject mismatch"):
        R.verify_target(target_name="2ED-B - INGLES II - 544064",
                        expect_course_name="2ED-B", backup_name=BACKUP, strict=True)


def test_the_subject_check_fires_even_when_the_expected_name_matches():
    """The name check and the subject check answer different questions.

    `2ED-B` really is the course that was asked for. It is still the wrong subject, and the
    professor asked for the wrong one — so naming it correctly must not buy a pass.
    """
    with pytest.raises(R.RestoreAborted, match="Subject mismatch"):
        R.verify_target(target_name="2ED-B - INGLES II - 544064",
                        expect_course_name="2ED-B - INGLES II", backup_name=BACKUP, strict=True)


def test_acting_for_another_professor_requires_naming_the_target():
    with pytest.raises(R.RestoreAborted, match="requires `expect_course_name`"):
        R.verify_target(target_name="1MH-B - INGLES I - 544081",
                        expect_course_name=None, backup_name=BACKUP, strict=True)


def test_his_own_course_does_not_need_the_expected_name():
    R.verify_target(target_name="1ED-B - INGLES I - 544054",
                    expect_course_name=None, backup_name=BACKUP, strict=False)


def test_an_unreadable_subject_refuses_when_acting_for_another():
    """Unknown is not 'matching'. Same doctrine as remove.py: an uncountable list is not empty."""
    with pytest.raises(R.RestoreAborted, match="Could not read the subject"):
        R.verify_target(target_name="MV71 - INGLES TECNICO LAE - 550863",
                        expect_course_name="MV71", backup_name=BACKUP, strict=True)


def test_an_unreadable_subject_is_tolerated_on_his_own_course():
    R.verify_target(target_name="TALLER DE LECTURA",
                    expect_course_name=None, backup_name=BACKUP, strict=False)


def test_whitespace_and_case_do_not_break_the_name_check():
    R.verify_target(target_name="Curso:  1MH-B  -  INGLES I  - 544081",
                    expect_course_name="1mh-b - ingles i", backup_name=BACKUP, strict=True)


# ── the guards that must fire before a browser exists ─────────────────────────
def test_a_backup_that_might_carry_students_is_refused_for_another_professor(tmp_path,
                                                                             monkeypatch):
    from tests.test_backup import _manifest, _write_mbz

    monkeypatch.setenv("MOODLE_PWD_COLLEAGUE1", "hers")
    monkeypatch.setattr(R.settings, "uach_username", "professor", raising=False)
    monkeypatch.setattr(R.settings, "uach_password", "own", raising=False)
    p = _write_mbz(tmp_path / "withusers.mbz", _manifest("9023", users="1"))
    with pytest.raises(R.RestoreAborted, match="carries user data"):
        R.restore_course(idc="9027", backup_path=p, as_user="colleague1",
                         expect_course_name="1MH-B")


def test_an_unknown_delegate_refuses_before_anything_opens(tmp_path, monkeypatch):
    from tests.test_backup import _manifest, _write_mbz

    monkeypatch.setattr(R.settings, "uach_username", "professor", raising=False)
    monkeypatch.setattr(R.settings, "uach_password", "own", raising=False)
    p = _write_mbz(tmp_path / "b.mbz", _manifest("9023", users="0"))
    with pytest.raises(R.RestoreAborted, match="MOODLE_PWD_COLLEAGUE2"):
        R.restore_course(idc="9022", backup_path=p, as_user="colleague2",
                         expect_course_name="1MH-A")
