"""Which account a run acts as, and the refusals around acting as somebody else.

The default — the owner's own `.env` credentials — must keep working untouched. Everything else
here is about making "I am about to act as another professor" impossible to do by accident, and
impossible to do silently.
"""

import pytest

from musai.automation import credentials as C


@pytest.fixture(autouse=True)
def _own_account(monkeypatch):
    monkeypatch.setattr(C.settings, "uach_username", "professor", raising=False)
    monkeypatch.setattr(C.settings, "uach_password", "own-secret", raising=False)
    for k in [k for k in list(__import__("os").environ) if k.startswith(C.ENV_PREFIX)]:
        monkeypatch.delenv(k, raising=False)


def test_the_default_is_carloss_own_account(tmp_path):
    i = C.resolve(env_file=tmp_path / "nope.env")
    assert i.username == "professor" and i.is_self and i.password == "own-secret"


def test_naming_his_own_username_is_still_his_own_account(tmp_path):
    assert C.resolve("PrOfessor", env_file=tmp_path / "nope.env").is_self


def test_another_professor_without_a_password_refuses_and_says_which_variable(tmp_path):
    with pytest.raises(C.CredentialsMissing) as e:
        C.resolve("colleague1", env_file=tmp_path / "nope.env")
    assert "MOODLE_PWD_COLLEAGUE1" in str(e.value)
    assert "consent" in str(e.value).lower()


def test_it_never_silently_falls_back_to_his_own_login(tmp_path):
    """The dangerous failure is not an error — it is a restore that runs as the wrong person."""
    with pytest.raises(C.CredentialsMissing):
        C.resolve("colleague2", env_file=tmp_path / "nope.env")


def test_the_environment_supplies_another_professors_password(monkeypatch, tmp_path):
    monkeypatch.setenv("MOODLE_PWD_COLLEAGUE1", "hers")
    i = C.resolve("colleague1", env_file=tmp_path / "nope.env")
    assert i.username == "colleague1" and i.password == "hers" and not i.is_self
    assert i.source == "MOODLE_PWD_COLLEAGUE1"


def test_dotenv_supplies_it_too_without_polluting_the_environment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MOODLE_PWD_COLLEAGUE2=hers\n", encoding="utf-8")
    i = C.resolve("colleague2", env_file=env)
    assert i.password == "hers" and not i.is_self
    import os
    assert "MOODLE_PWD_COLLEAGUE2" not in os.environ


def test_the_environment_wins_over_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("MOODLE_PWD_COLLEAGUE2=from-file\n", encoding="utf-8")
    monkeypatch.setenv("MOODLE_PWD_COLLEAGUE2", "from-env")
    assert C.resolve("colleague2", env_file=env).password == "from-env"


def test_a_password_never_appears_in_a_repr_or_a_description(monkeypatch, tmp_path):
    monkeypatch.setenv("MOODLE_PWD_COLLEAGUE1", "super-secret-value")
    i = C.resolve("colleague1", env_file=tmp_path / "nope.env")
    assert "super-secret-value" not in repr(i)
    assert "super-secret-value" not in i.describe()
    assert "ANOTHER PROFESSOR" in i.describe()


def test_a_hostile_username_cannot_build_an_env_var_name(tmp_path):
    with pytest.raises(C.CredentialsMissing, match="not a plausible"):
        C.resolve("../../etc/passwd", env_file=tmp_path / "nope.env")


def test_known_delegates_lists_names_never_values(monkeypatch, tmp_path):
    monkeypatch.setenv("MOODLE_PWD_COLLEAGUE1", "hers")
    monkeypatch.setenv("MOODLE_PWD_COLLEAGUE2", "hers-too")
    got = C.known_delegates(env_file=tmp_path / "nope.env")
    assert got == ["colleague1", "colleague2"]
