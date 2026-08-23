"""The README's promise, enforced across the whole engine rather than one corner of it.

`README.md` says, of this repository:

    > Nothing here identifies a person.

and the source keeps that promise by convention: real colleagues appear as **Colleague A / B /
C**, never by name (`backup.py`, `restore.py`, `hub.py`, `discover.py` all do this).

🔴 **The only test enforcing it looked at the help corpus.** `test_help_docs.py` has
`test_no_topic_names_a_person`, which walks `CORPUS` — the Markdown topics — and nothing else.
So on 2026-08-22 a batch of new writers and comments went in naming a real colleague six times
across five files, and the full suite stayed green. The property was real, the convention was
followed everywhere it had been noticed, and **the guard was scoped to the file where someone
had once been burned** rather than to the property.

⭐ *Scope the system, not the file.* A rail that depends on the next author remembering the
convention is not a rail.

⚠️ This deliberately checks SOURCE, not the working notes. `ENFERMERIA_PILOT.md`,
`HANDOFF.md`, `RUNBOOK.md` and `scratchpad/` name real people on purpose — they are the private
operational record and are gitignored. This test guards what actually ships.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The people this project's operational notes name. Anyone added here must also be findable
#: in the notes under the same spelling, or the guard drifts away from what it guards.
#:
#: ⚠️ First names only, matched on a word boundary — a surname would give false negatives on
#: the possessive forms these comments actually use ("Colleague B's EGB3A").
PRIVATE_NAMES = ("Carlos", "Susana", "Morayma", "Hanna", "Dalia", "Gonzalo")

#: Moodle usernames of real accounts. These are credentials-adjacent: `MOODLE_PWD_<USER>` is
#: literally derived from them, so a username in shipped source is half of a login.
PRIVATE_USERNAMES = ("cavilla", "hlara", "smoreno", "arvega")

_NAME_RX = re.compile(r"\b(" + "|".join(PRIVATE_NAMES) + r")\b")
_USER_RX = re.compile(r"\b(" + "|".join(PRIVATE_USERNAMES) + r")\b", re.I)

#: Where the promise applies: the shipped engine and its tests.
SHIPPED_DIRS = ("musai", "tests")

#: Lines that legitimately carry a name because they exist to FORBID it. Without this, the
#: guards would be unable to name what they guard against.
_GUARD_FILES = {"test_no_person_named_in_source.py", "test_help_docs.py"}

#: 🔴 Two real exceptions, both about the OWNER's own recovery address, both load-bearing.
#: `auth.py` and `test_recovery_login.py` name `cavilla@uach.mx` because the break-glass rail
#: allow-lists that exact address — the string IS the security control, and replacing it with
#: a placeholder would make the rail unreadable. The owner is not a third party here.
_ADDRESS_EXCEPTIONS = {"musai/web/auth.py", "tests/test_recovery_login.py"}


def _python_files():
    for d in SHIPPED_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _offences(rx, allow_addresses: bool):
    out = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        if path.name in _GUARD_FILES:
            continue
        if allow_addresses and rel in _ADDRESS_EXCEPTIONS:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = rx.search(line)
            if m:
                out.append(f"{rel}:{i} names {m.group(0)!r} — use Colleague A/B/C")
    return out


def test_no_shipped_source_names_a_real_person():
    """The regression this file exists for: six occurrences across five files, suite green."""
    assert not _offences(_NAME_RX, allow_addresses=True), "\n".join(
        _offences(_NAME_RX, allow_addresses=True))


def test_no_shipped_source_carries_a_real_moodle_username():
    """A username is half a credential here — `credentials.resolve` reads
    `MOODLE_PWD_<USERNAME>` straight out of `.env`."""
    assert not _offences(_USER_RX, allow_addresses=True), "\n".join(
        _offences(_USER_RX, allow_addresses=True))


def test_the_anonymisation_convention_is_actually_in_use():
    """A guard that passes because nobody writes comments any more is not a guard. The
    convention it protects must be visibly alive in the shipped source."""
    hits = [p.relative_to(ROOT).as_posix() for p in _python_files()
            if re.search(r"\bColleague [ABC]\b", p.read_text(encoding="utf-8"))]
    assert len(hits) >= 3, f"the Colleague A/B/C convention has all but vanished: {hits}"


def test_the_readme_still_makes_the_promise_this_enforces():
    """⚠️ If someone deletes the promise, this whole file becomes a rule nobody agreed to.
    Then the test should be reconsidered — not silently kept enforcing a dead policy."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Nothing here identifies a person" in readme, (
        "README no longer promises anonymity — decide deliberately whether these guards "
        "still represent the project's intent before deleting them")


@pytest.mark.parametrize("name", PRIVATE_NAMES)
def test_the_pattern_would_actually_catch_each_name(name):
    """The positive control. A guard is only worth having if it fires on the thing it is
    looking for — `test_help_docs`'s version passed for a year without anyone checking that
    its regex still matched."""
    assert _NAME_RX.search(f"# measured on {name}'s EGB3A course")
