"""Recovery sign-in: the two ways in that do not depend on an account the university owns.

The thing being protected here is unusual, so it is worth naming before the tests start.
MUSAI's author owns the code, the cloud account and the AI key — but every row in the database
hangs off `cavilla@uach.mx`, **an address UACH controls.** A password reset by central IT is
not an attack and would not look like one; it would just be a morning when the sign-in button
stopped opening for the one person the app belongs to.

So two doors were added, and each is tested for the specific way it could be useless:

* **A recovery alias** — a personal Google account on an exact allow-list. Its failure mode is
  not "it doesn't let him in", it is **letting him into an empty app**: signing in under a
  second address would resolve to a second `Professor` row owning zero courses, and the login
  would succeed while the cockpit showed nothing. `test_a_recovery_alias_is_the_owner_not_a_new_user`
  is the one that matters.
* **A password door** — no third party at all, for the case the OAuth *client* is what was
  lost. Its failure modes are the ordinary ones a password door has, plus one that isn't:
  a rate limiter keyed globally would let anyone who finds the URL lock the owner out of his
  own recovery path.

And underneath both, the rule that did not move: **the gate is still `@uach.mx` only.** The
allow-list holds whole addresses, never a domain, so it cannot widen into a population.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from musai.config import settings
from musai.security import breakglass
from musai.web import auth

GMAIL = "carlosavillah90@gmail.com"
OWNER = "professor@uach.mx"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def secret() -> tuple[str, str]:
    """One real password and its real hash, computed once — scrypt is deliberately slow."""
    password = breakglass.new_password()
    return password, breakglass.hash_password(password)


@pytest.fixture(autouse=True)
def _forget_failures():
    """No test may inherit another's lockout. The limiter is process-global by design."""
    breakglass.reset_all()
    yield
    breakglass.reset_all()


def _app() -> FastAPI:
    """A miniature cockpit that includes the real auth router and the real gate."""
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/")
    def landing():
        return {"page": "landing"}

    @app.get("/whoami")
    def whoami(request: Request):
        return {"user": auth.current_user(request)}

    auth.install(app)
    return app


@pytest.fixture
def client():
    return TestClient(_app(), follow_redirects=False)


def _signed_in_as(client) -> dict | None:
    """Who the session says is acting, asking a GATED route so the gate is part of the answer.

    ⚠️ Signed out, `/whoami` does not return `{"user": null}` — it 303s to the landing page with
    an empty body. Reading that as JSON is how a negative test turns into a decode error at
    best, and at worst (`follow_redirects=True`) into a green assertion against the landing
    page. The redirect is the evidence, so it is checked rather than parsed through.
    """
    r = client.get("/whoami")
    if r.status_code == 303:
        return None
    assert r.status_code == 200, r.status_code
    return r.json()["user"]


@pytest.fixture
def configured(monkeypatch, secret):
    """Turn the password door on, the way `.env` would."""
    _, hashed = secret
    monkeypatch.setattr(settings, "break_glass_email", GMAIL)
    monkeypatch.setattr(settings, "break_glass_password_hash", hashed)


# ---------------------------------------------------------------------------
# The recovery alias — Google, but not the institutional account
# ---------------------------------------------------------------------------

def test_a_listed_recovery_address_passes_the_gate(monkeypatch):
    """The whole point: a personal Gmail gets in when the institutional account cannot.

    ⭐ And it needs no second OAuth client — MUSAI's existing one authenticates any Google
    account. `hd=` on the authorize URL only pre-filters the chooser; `_gate` is the real lock.
    """
    monkeypatch.setattr(settings, "admin_recovery_emails", GMAIL)
    email, refusal = auth._gate({"email": GMAIL, "email_verified": True})
    assert (email, refusal) == (GMAIL, None)


def test_a_recovery_alias_is_the_owner_not_a_new_user(monkeypatch):
    """🔴 The failure this feature exists to prevent, and it is NOT a refused login.

    Every course, credential and usage row is scoped to `Professor.email`. Issue the session
    under `…@gmail.com` and `get_or_create` mints a second professor who owns nothing: sign-in
    succeeds, the cockpit is empty, and the owner has "recovered" into a blank app. The alias
    authenticates; the owner acts.
    """
    monkeypatch.setattr(settings, "admin_recovery_emails", GMAIL)
    user = auth._session_user(GMAIL, {"name": "Carlos", "picture": "p.jpg"})

    assert user["email"] == OWNER, "the session identity must be the owner, not the alias"
    assert user["is_admin"] is True
    assert user["signed_in_as"] == GMAIL, "the audit trail still has to name the real door"


def test_the_recovery_list_is_addresses_never_a_domain(monkeypatch):
    """`ADMIN_RECOVERY_EMAILS=gmail.com` must open nothing. A domain here would open the planet."""
    monkeypatch.setattr(settings, "admin_recovery_emails", "gmail.com")
    _, refusal = auth._gate({"email": GMAIL, "email_verified": True})
    assert refusal == "domain"


def test_the_recovery_match_is_the_whole_address(monkeypatch):
    """Neither a prefix, a suffix, nor a lookalike domain gets in on a listed address."""
    monkeypatch.setattr(settings, "admin_recovery_emails", GMAIL)
    for near in ("x" + GMAIL, GMAIL + ".attacker.com", "carlosavillah90@gmail.com.co",
                 "carlosavillah9@gmail.com"):
        _, refusal = auth._gate({"email": near, "email_verified": True})
        assert refusal == "domain", near


def test_a_recovery_address_still_has_to_be_verified_by_google(monkeypatch):
    """The allow-list skips the domain rule. It does not skip proof of ownership.

    An unverified claim is just a string Google passed along — being on a list the owner wrote
    makes it a *more* attractive string to assert, not a trusted one.
    """
    monkeypatch.setattr(settings, "admin_recovery_emails", GMAIL)
    _, refusal = auth._gate({"email": GMAIL, "email_verified": False})
    assert refusal == "unverified"


def test_the_recovery_list_is_empty_by_default():
    """It ships shut. Everything above needed a monkeypatch to be true."""
    assert settings.recovery_addresses == ()
    assert settings.is_recovery_address(GMAIL) is False
    _, refusal = auth._gate({"email": GMAIL, "email_verified": True})
    assert refusal == "domain"


def test_a_student_address_is_not_rescued_by_the_recovery_path(monkeypatch):
    """A listed alias skips the student rule; an unlisted student address still cannot pass."""
    monkeypatch.setattr(settings, "admin_recovery_emails", GMAIL)
    _, refusal = auth._gate({"email": "a123456@uach.mx", "email_verified": True})
    assert refusal == "student"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def test_a_password_round_trips_through_its_hash(secret):
    password, hashed = secret
    assert breakglass.verify_password(password, hashed) is True
    assert breakglass.verify_password(password + "x", hashed) is False


def test_the_hash_does_not_contain_the_password(secret):
    password, hashed = secret
    assert password not in hashed
    assert hashed.startswith("scrypt$")


def test_the_same_password_hashes_differently_every_time():
    """Salted. Two identical passwords must not produce the same stored string."""
    a, b = breakglass.hash_password("same"), breakglass.hash_password("same")
    assert a != b
    assert breakglass.verify_password("same", a) and breakglass.verify_password("same", b)


def test_a_generated_password_is_long_enough_to_be_the_real_control():
    """The rate limiter is per-process and evadable. This is what actually holds the door."""
    passwords = {breakglass.new_password() for _ in range(20)}
    assert len(passwords) == 20, "generated passwords must not repeat"
    assert all(len(p) >= 30 for p in passwords)


@pytest.mark.parametrize("junk", ["", "not-a-hash", "scrypt$bad", "bcrypt$1$2$3$4$5",
                                  "scrypt$x$8$1$AAAA$BBBB", "$$$$$"])
def test_a_broken_hash_is_false_and_never_an_exception(junk):
    """🔴 A lock whose configuration is corrupt must stay SHUT, not raise into a 500 — and not
    fall open. A traceback out of the verifier is also an oracle about the stored value."""
    assert breakglass.verify_password("anything", junk) is False


def test_the_right_password_with_the_wrong_email_fails(secret):
    password, hashed = secret
    assert breakglass.check("someone@else.com", password,
                            expect_email=GMAIL, expect_hash=hashed) is False
    assert breakglass.check(GMAIL, password, expect_email=GMAIL, expect_hash=hashed) is True


def test_the_email_check_ignores_case_and_padding(secret):
    password, hashed = secret
    assert breakglass.check(f"  {GMAIL.upper()} ", password,
                            expect_email=GMAIL, expect_hash=hashed) is True


def test_an_unconfigured_door_refuses_every_credential(secret):
    """No hash configured ⇒ nothing opens it, including an empty password."""
    password, hashed = secret
    assert breakglass.check(GMAIL, password, expect_email="", expect_hash=hashed) is False
    assert breakglass.check(GMAIL, password, expect_email=GMAIL, expect_hash="") is False
    assert breakglass.check("", "", expect_email="", expect_hash="") is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_five_failures_lock_the_bucket_out():
    for _ in range(breakglass.MAX_FAILURES):
        assert breakglass.locked_out("1.2.3.4") == 0
        breakglass.record_failure("1.2.3.4")
    assert breakglass.locked_out("1.2.3.4") > 0


def test_one_address_lockout_does_not_touch_another():
    """⚠️ Per-IP on purpose. A global counter would let anyone who finds this URL deny the
    owner his own recovery path — for this door, availability to one person IS the goal."""
    for _ in range(breakglass.MAX_FAILURES):
        breakglass.record_failure("1.2.3.4")
    assert breakglass.locked_out("1.2.3.4") > 0
    assert breakglass.locked_out("5.6.7.8") == 0


def test_the_window_expires():
    now = 1_000_000.0
    for _ in range(breakglass.MAX_FAILURES):
        breakglass.record_failure("1.2.3.4", now=now)
    assert breakglass.locked_out("1.2.3.4", now=now) > 0
    assert breakglass.locked_out("1.2.3.4", now=now + breakglass.WINDOW_SECONDS + 1) == 0


def test_a_successful_sign_in_forgets_the_failures():
    for _ in range(breakglass.MAX_FAILURES - 1):
        breakglass.record_failure("1.2.3.4")
    breakglass.clear("1.2.3.4")
    assert breakglass.locked_out("1.2.3.4") == 0


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------

def test_an_unconfigured_door_does_not_exist(client):
    """404, not 401. A door nobody set up should not announce that it is there."""
    assert client.get("/auth/break-glass").status_code == 404
    assert client.post("/auth/break-glass",
                       data={"email": GMAIL, "password": "x"}).status_code == 404


def test_the_form_renders_when_configured(client, configured):
    r = client.get("/auth/break-glass")
    assert r.status_code == 200
    assert "Recovery sign-in" in r.text


def test_the_page_never_shows_the_hash_or_the_address(client, configured, secret):
    """A recovery page that names the account is a page that tells an attacker the username."""
    password, hashed = secret
    body = client.get("/auth/break-glass").text
    assert hashed not in body
    assert password not in body
    assert GMAIL not in body


def test_the_right_credentials_sign_the_owner_in(client, configured, secret):
    password, _ = secret
    r = client.post("/auth/break-glass", data={"email": GMAIL, "password": password})
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    who = _signed_in_as(client)
    assert who["email"] == OWNER
    assert who["is_admin"] is True
    assert who["via"] == "break-glass"
    assert who["signed_in_as"] == GMAIL


def test_a_wrong_password_signs_nobody_in(client, configured):
    r = client.post("/auth/break-glass", data={"email": GMAIL, "password": "wrong"})
    assert r.status_code == 401
    assert _signed_in_as(client) is None


def test_the_right_password_on_the_wrong_email_signs_nobody_in(client, configured, secret):
    password, _ = secret
    r = client.post("/auth/break-glass",
                    data={"email": "attacker@gmail.com", "password": password})
    assert r.status_code == 401
    assert _signed_in_as(client) is None


def test_repeated_failures_start_answering_429(client, configured):
    for _ in range(breakglass.MAX_FAILURES):
        client.post("/auth/break-glass", data={"email": GMAIL, "password": "wrong"})
    r = client.post("/auth/break-glass", data={"email": GMAIL, "password": "wrong"})
    assert r.status_code == 429
    assert "Try again in" in r.text


def test_the_break_glass_door_is_reachable_signed_out():
    """It lives under `/auth/`, which the gate treats as public — otherwise the recovery page
    would require the session it exists to issue."""
    assert auth.is_public_path("/auth/break-glass") is True


# ---------------------------------------------------------------------------
# Fail-closed, still
# ---------------------------------------------------------------------------

def test_no_session_secret_means_no_way_in_at_all(monkeypatch, secret):
    """Both doors need a signable cookie. Without one the app must 503, not fall open."""
    _, hashed = secret
    monkeypatch.setattr(settings, "session_secret", "")
    monkeypatch.setattr(settings, "break_glass_email", GMAIL)
    monkeypatch.setattr(settings, "break_glass_password_hash", hashed)
    assert settings.sign_in_available is False


def test_the_break_glass_door_alone_keeps_the_app_serving(monkeypatch, secret):
    """🔴 The scenario the door is FOR: the OAuth client is gone.

    If the gate asked `auth_configured`, that deploy would 503 at every route — including the
    recovery page's own redirect target — and the password would verify into a wall.
    """
    _, hashed = secret
    monkeypatch.setattr(settings, "google_client_id", "")
    monkeypatch.setattr(settings, "google_client_secret", "")
    monkeypatch.setattr(settings, "break_glass_email", GMAIL)
    monkeypatch.setattr(settings, "break_glass_password_hash", hashed)
    assert settings.auth_configured is False
    assert settings.sign_in_available is True


def test_an_ordinary_professor_is_not_made_admin_by_any_of_this():
    """The recovery paths widen who may be the OWNER. They must not widen who is an admin."""
    assert settings.is_admin_email("colleague@uach.mx") is False
    assert settings.is_admin_email(OWNER) is True
    assert settings.is_admin_email("") is False
