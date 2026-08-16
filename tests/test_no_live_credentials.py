"""The suite must be structurally incapable of authenticating as a real professor.

Not "no test currently does it" — *no test can*. `conftest._no_real_delegate_passwords` is
autouse, so these hold for every test in the suite, including ones written later by someone who
never read this file.

Why it exists: on 2026-08-10 the owner set `MOODLE_PWD_COLLEAGUE2` in `.env` for a real run. A unit test
asserting that an unknown delegate is refused then resolved a real password, walked past the
refusal it was written to prove, and opened a browser against live Moodle as Colleague C. The suite's
behaviour depended on a file that changes when a human does unrelated work.
"""

import os

from musai.automation import credentials as C


def test_no_delegate_password_is_visible_to_a_test():
    assert C.known_delegates() == []


def test_the_environment_carries_no_delegate_passwords():
    assert [k for k in os.environ if k.startswith(C.ENV_PREFIX)] == []


def test_the_default_env_file_a_test_sees_does_not_exist():
    """The real `.env` must be out of reach even for code that passes no `env_file`."""
    from pathlib import Path
    assert not Path(C.DEFAULT_ENV_FILE).is_file()


def test_resolving_a_real_colleague_refuses_inside_the_suite():
    """`colleague2` and `colleague1` are real accounts with real passwords on the owner's machine."""
    import pytest
    for user in ("colleague2", "colleague1", "colleague3"):
        with pytest.raises(C.CredentialsMissing):
            C.resolve(user)
