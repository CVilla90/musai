"""Which Moodle account a run acts as — and the refusals around acting as somebody else.

MUSAI has always logged in as one person: `UACH_USERNAME` / `UACH_PASSWORD` from `.env`. That is
The owner's own account, his own machine, his own automation, and nothing here changes it — the
default is unchanged and every existing caller keeps it.

**Acting as a colleague is a different thing and this module keeps it looking different.** Moodle
records the *colleague* as the author of every action, not the owner; MUSAI must therefore record
what Moodle cannot — that the human who decided was someone else. Hence `on_behalf_of` on the
audit row, and hence the refusals below rather than a silent fallback to the owner's own login.

Ported from `moodle_suite/automation/upload_course_backup.py`, which drove 17 courses across four
professor accounts. 🔴 That script once carried **four professors' plaintext passwords in
source**, three of them not the owner's. It reads `MOODLE_PWD_<USERNAME>` from the environment now,
and so does this. **No password is ever a parameter, a default, a log line or a repr.**

Where to put the value: `.env` (gitignored) as `MOODLE_PWD_COLLEAGUE1=…`, or a shell export. Both
are read; the environment wins, so a one-off run can override without editing a file.

🔴 **A stored password is not consent, and last semester's consent is not this semester's.**
Nothing here can check that — it is the owner's to obtain, per professor, per semester
(PRODUCT_DIRECTION: coordinator mode is *act, never read*, with per-semester consent).
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from musai.config import settings

ENV_PREFIX = "MOODLE_PWD_"
_SAFE_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")

# Where delegate passwords are read from, as a module attribute rather than a hardcoded default,
# so it can be *pointed somewhere else*. 🔴 `tests/conftest.py` aims it at a nonexistent path and
# strips `MOODLE_PWD_*` from the environment for every test — because the day the owner really set
# `MOODLE_PWD_COLLEAGUE2`, a unit test asserting "an unknown delegate is refused" stopped being true,
# stopped refusing, and **launched a browser to log into Moodle as Colleague C**. It took 62 seconds
# and reached a live system. A test suite must be structurally unable to authenticate as a real
# professor; that is not a property you can get by remembering to mock.
DEFAULT_ENV_FILE = ".env"


def _env_file(env_file: str | Path | None) -> Path:
    return Path(env_file if env_file is not None else DEFAULT_ENV_FILE)


class CredentialsMissing(RuntimeError):
    """No usable password for the requested account. Nothing has been attempted."""


@dataclass(frozen=True)
class MoodleIdentity:
    """Who a run logs in as. `password` is never included in `describe()` or any log line."""

    username: str
    password: str
    is_self: bool
    source: str  # where the password came from, e.g. "UACH_PASSWORD" — never the value

    def describe(self) -> str:
        who = "own account" if self.is_self else "🔴 ANOTHER PROFESSOR'S ACCOUNT"
        return f"{self.username} ({who}, password from {self.source})"

    def __repr__(self) -> str:  # keep the secret out of tracebacks and pytest diffs
        return (f"MoodleIdentity(username={self.username!r}, is_self={self.is_self}, "
                f"source={self.source!r}, password=<hidden>)")


def env_var_for(username: str) -> str:
    return f"{ENV_PREFIX}{username.upper().replace('-', '_').replace('.', '_')}"


def _from_dotenv(key: str, env_file: str | Path | None = None) -> str:
    """Read one key out of `.env` without importing it into the process environment.

    `pydantic-settings` parses `.env` for its *declared* fields only, and `MOODLE_PWD_*` are not
    declared (nor should they be — the set of professors is data, not schema). So read the file.
    ⚠️ Like `settings`, this is relative to the CWD: run from the project root or it finds
    nothing, which surfaces as "credentials missing" and is really a cwd problem.
    """
    path = _env_file(env_file)
    if not path.is_file():
        return ""
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - python-dotenv ships with pydantic-settings
        return ""
    return (dotenv_values(path) or {}).get(key) or ""


def resolve(as_user: str | None = None, *, env_file: str | Path | None = None) -> MoodleIdentity:
    """The identity to log in as. `None` (the default) is the owner's own account, unchanged."""
    own_user = (settings.uach_username or "").strip()
    own_pwd = settings.uach_password or ""

    if as_user is None or not as_user.strip():
        if not own_user or not own_pwd:
            raise CredentialsMissing(
                "UACH credentials missing (UACH_USERNAME / UACH_PASSWORD in .env). Run from the "
                "project root — settings reads .env relative to CWD.")
        return MoodleIdentity(own_user, own_pwd, True, "UACH_PASSWORD")

    as_user = as_user.strip()
    if not _SAFE_USERNAME.match(as_user):
        raise CredentialsMissing(
            f"{as_user!r} is not a plausible Moodle username. Refusing to build an environment "
            f"variable name out of it.")

    if as_user.casefold() == own_user.casefold():
        if not own_pwd:
            raise CredentialsMissing("UACH_PASSWORD is empty in .env.")
        return MoodleIdentity(own_user, own_pwd, True, "UACH_PASSWORD")

    var = env_var_for(as_user)
    pwd = os.environ.get(var) or _from_dotenv(var, env_file)
    if not pwd:
        raise CredentialsMissing(
            f"No password for {as_user!r}. Set {var} in .env (gitignored) or export it.\n"
            f"🔴 This is another professor's account: MUSAI will act as them and Moodle will "
            f"record them, not you, as the author. Get their consent for this semester first."
        )
    return MoodleIdentity(as_user, pwd, False, var)


def resolve_for_professor(professor, *, system: str = "moodle") -> MoodleIdentity:
    """The identity for a **signed-in professor's own** stored credential. Never a fallback.

    Added 2026-08-14, when MUSAI stopped being single-user. The `.env` road above answers
    *"which account does this machine act as?"*; this answers *"which account does this person
    act as?"* — and for a colleague sitting in the owner's office those are different questions
    with the same shape, which is exactly how a run ends up authored by the wrong person.

    🔴 **There is no fallback here, in either direction.** No stored credential ⇒ refuse. A
    credential that will not decrypt ⇒ refuse. Falling back to `UACH_PASSWORD` would run
    Colleague D's restore as the owner, into a course list that is not hers, and Moodle would record
    him as the author of every write. `is_self=True` because it genuinely is *their own*
    account — the `🔴 ANOTHER PROFESSOR'S ACCOUNT` warning belongs to the `--as-user` road,
    where a human is acting for somebody else, and printing it here would cry wolf.

    ⚠️ Deliberately takes a `Professor` row rather than an email, so a caller cannot pass an
    address that was never authenticated.
    """
    from sqlmodel import Session

    from musai.db import engine
    from musai.professors import get_credential
    from musai.security.vault import VaultCorrupt, VaultUnavailable

    if professor is None or not getattr(professor, "id", None):
        raise CredentialsMissing("No signed-in professor to resolve credentials for.")

    with Session(engine) as sess:
        cred = get_credential(sess, professor.id, system)
        if cred is None or not cred.secret_enc:
            raise CredentialsMissing(
                f"No {system} password stored for {professor.email}. Add it in "
                f"Settings ▸ Passwords — MUSAI signs in to {system} as you, so it needs your "
                f"own account. It will never use somebody else's.")
        username = (cred.username or "").strip()
        try:
            password = _decrypt(cred.secret_enc)
        except (VaultUnavailable, VaultCorrupt) as e:
            raise CredentialsMissing(str(e)) from e

    if not username or not password:
        raise CredentialsMissing(
            f"The stored {system} credential for {professor.email} is incomplete. Re-enter it "
            f"in Settings ▸ Passwords.")
    return MoodleIdentity(username, password, True, f"vault:{system}")


def _decrypt(token: str) -> str:
    """Indirection so tests can prove the vault is reached, and so the import stays local."""
    from musai.security.vault import decrypt

    return decrypt(token)


def known_delegates(*, env_file: str | Path | None = None) -> list[str]:
    """Usernames a password is currently available for. Names only — never values."""
    keys = set(os.environ)
    path = _env_file(env_file)
    if path.is_file():
        try:
            from dotenv import dotenv_values
            keys |= {k for k, v in (dotenv_values(path) or {}).items() if v}
        except ImportError:  # pragma: no cover
            pass
    return sorted(k[len(ENV_PREFIX):].lower() for k in keys if k.startswith(ENV_PREFIX))
