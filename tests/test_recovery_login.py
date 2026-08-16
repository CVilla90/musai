"""The one exception to "@uach.mx only": an exact personal address that signs in as the owner.

The thing being protected here is unusual, so it is worth naming before the tests start.
MUSAI's author owns the code, the cloud account and the AI key — but every row in the database
hangs off `cavilla@uach.mx`, **an address UACH controls.** A password reset by central IT is
not an attack and would not look like one; it would just be a morning when the sign-in button
stopped opening for the one person the app belongs to.

⭐ **The fix needed no second OAuth client.** MUSAI's existing client already authenticates any
Google account; `hd=` on the authorize URL only pre-filters the account chooser. `_gate()` was
the only thing refusing, so the whole change is one exact-match allow-list checked after
`email_verified` and before the domain rule.

**The failure mode being tested for is not a refused login — it is a successful one into an
empty app.** Courses, credentials and usage rows are all scoped to `Professor.email`, so a
session issued under `…@gmail.com` would hit get-or-create and mint a *second* professor
owning zero courses. The login would succeed and the cockpit would show nothing.
`test_a_recovery_alias_is_the_owner_not_a_new_user` is the one that matters.

And underneath it, the rule that did not move: **the gate is still `@uach.mx` only.** The list
holds whole addresses, never a domain, so it cannot widen into a population.
"""

import pytest

from musai.config import settings
from musai.web import auth

GMAIL = "carlosavillah90@gmail.com"
OWNER = "professor@uach.mx"


@pytest.fixture
def listed(monkeypatch):
    """Turn the exception on, the way one line in `.env` would."""
    monkeypatch.setattr(settings, "admin_recovery_emails", GMAIL)


# ---------------------------------------------------------------------------
# It ships shut
# ---------------------------------------------------------------------------

def test_the_recovery_list_is_empty_by_default():
    """Every test below needed a fixture to be true. Unconfigured, nothing changed."""
    assert settings.recovery_addresses == ()
    assert settings.is_recovery_address(GMAIL) is False
    _, refusal = auth._gate({"email": GMAIL, "email_verified": True})
    assert refusal == "domain"


# ---------------------------------------------------------------------------
# The exception
# ---------------------------------------------------------------------------

def test_a_listed_address_passes_the_gate(listed):
    email, refusal = auth._gate({"email": GMAIL, "email_verified": True})
    assert (email, refusal) == (GMAIL, None)


def test_a_recovery_alias_is_the_owner_not_a_new_user(listed):
    """🔴 The failure this feature exists to prevent, and it is NOT a refused login.

    Issue the session under `…@gmail.com` and `professors.get_or_create` mints a second
    professor who owns nothing: sign-in succeeds, the cockpit is empty, and the owner has
    "recovered" into a blank app. The alias authenticates; the owner acts.
    """
    user = auth._session_user(GMAIL, {"name": "Carlos", "picture": "p.jpg"})

    assert user["email"] == OWNER, "the session identity must be the owner, not the alias"
    assert user["is_admin"] is True
    assert user["signed_in_as"] == GMAIL, "the audit trail still has to name the real door"


def test_an_ordinary_uach_sign_in_is_completely_unchanged():
    """The owner's own address is not routed through any of the alias machinery."""
    user = auth._session_user(OWNER, {"name": "the owner", "picture": "u.jpg"})
    assert user["email"] == OWNER
    assert user["signed_in_as"] == OWNER
    assert user["via"] == "google"


# ---------------------------------------------------------------------------
# It cannot widen
# ---------------------------------------------------------------------------

def test_the_list_is_addresses_never_a_domain(monkeypatch):
    """`ADMIN_RECOVERY_EMAILS=gmail.com` must open nothing at all."""
    monkeypatch.setattr(settings, "admin_recovery_emails", "gmail.com")
    _, refusal = auth._gate({"email": GMAIL, "email_verified": True})
    assert refusal == "domain"


def test_the_match_is_the_whole_address(listed):
    """Neither a prefix, a suffix, nor a lookalike domain gets in on a listed address."""
    for near in ("x" + GMAIL, GMAIL + ".attacker.com", "carlosavillah90@gmail.com.co",
                 "carlosavillah9@gmail.com", "carlosavillah90@gmail.co"):
        _, refusal = auth._gate({"email": near, "email_verified": True})
        assert refusal == "domain", near


def test_a_listed_address_still_has_to_be_verified_by_google(listed):
    """The allow-list skips the domain rule. It does not skip proof of ownership.

    An unverified claim is just a string Google passed along — being on a list the owner wrote
    makes it a *more* attractive string to assert, not a trusted one.
    """
    _, refusal = auth._gate({"email": GMAIL, "email_verified": False})
    assert refusal == "unverified"


def test_a_student_address_is_not_rescued_by_the_exception(listed):
    """A listed alias skips the student rule; an unlisted student address still cannot pass."""
    _, refusal = auth._gate({"email": "a123456@uach.mx", "email_verified": True})
    assert refusal == "student"


def test_case_and_padding_do_not_defeat_the_match(listed):
    email, refusal = auth._gate({"email": f"  {GMAIL.upper()} ", "email_verified": True})
    assert (email, refusal) == (GMAIL, None)


def test_several_addresses_can_be_listed(monkeypatch):
    monkeypatch.setattr(settings, "admin_recovery_emails", f"  {GMAIL} , second@example.com ")
    assert settings.recovery_addresses == (GMAIL, "second@example.com")
    for ok in (GMAIL, "second@example.com"):
        assert auth._gate({"email": ok, "email_verified": True})[1] is None
    assert auth._gate({"email": "third@example.com", "email_verified": True})[1] == "domain"


# ---------------------------------------------------------------------------
# It does not widen who is an admin
# ---------------------------------------------------------------------------

def test_an_ordinary_professor_is_not_made_admin_by_any_of_this(listed):
    """The exception widens who may be the OWNER. It must not widen who is an admin."""
    assert settings.is_admin_email("colleague@uach.mx") is False
    assert settings.is_admin_email(OWNER) is True
    assert settings.is_admin_email(GMAIL) is True
    assert settings.is_admin_email("") is False
