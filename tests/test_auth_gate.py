"""The sign-in gate: who gets refused, and what stays reachable while signed out.

Two failure modes are being tested for, and they pull in opposite directions.

**Falling open** — a cockpit route that answers without a session. The gate is default-deny
middleware rather than a per-route dependency precisely so that a router added next month is
covered without anyone remembering; `test_a_brand_new_route_is_gated_without_being_told` is the
test that keeps that true.

**Falling shut on the wrong thing** — gating SUSAI's `/webhook` would silently kill the student
assistant, and Meta would just stop delivering. It authenticates with the app-secret signature,
not a professor session, so it has to stay public.

Every test here runs against `auth.install()`, the same function `musai.web.app` calls, so the
ordering trap it documents is covered by the tests rather than by a comment alone.
"""

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from musai.config import settings
from musai.web import auth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _build_app() -> FastAPI:
    """A miniature cockpit: one public route, one gated route, the real gate."""
    app = FastAPI()

    @app.get("/")
    def landing():
        return {"page": "landing"}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/webhook")
    def webhook():
        return {"susai": "reachable"}

    @app.get("/courses/1")
    def course():
        return {"course": 1}

    @app.post("/courses/1/publish")
    def publish():
        return {"published": True}

    auth.install(app)
    return app


def _signed_in(client: TestClient, email: str = "professor@uach.mx") -> None:
    """Forge the session cookie exactly the way Starlette's SessionMiddleware signs it.

    Deliberately not "call the login route with a mocked Google": that would test the mock.
    This puts a real, correctly-signed cookie on the wire and lets the real middleware read it.
    """
    payload = base64.b64encode(json.dumps({"user": {"email": email, "name": "C"}}).encode())
    cookie = TimestampSigner(settings.session_secret).sign(payload).decode()
    client.cookies.set(auth.SESSION_COOKIE, cookie)


@pytest.fixture
def client():
    return TestClient(_build_app(), follow_redirects=False)


# ---------------------------------------------------------------------------
# The domain gate
# ---------------------------------------------------------------------------

def test_a_uach_account_passes_the_gate():
    email, refusal = auth._gate({"email": "professor@uach.mx", "email_verified": True})
    assert (email, refusal) == ("professor@uach.mx", None)


def test_a_non_uach_account_is_refused():
    """🔴 the owner's instruction, 2026-08-13: only @uach.mx. No allow-list, no exceptions."""
    _, refusal = auth._gate({"email": "carlosavillah90@gmail.com", "email_verified": True})
    assert refusal == "domain"


def test_a_student_uach_address_is_refused():
    """🔴 The domain gate alone does NOT keep students out — they are `@uach.mx` too.

    The owner's own student account `a123456@uach.mx` sits one row below `professor@uach.mx` in his
    Google chooser. The landing page promises "students never sign in here"; this is what makes
    that true rather than decorative. Decision recorded in PRODUCT_DIRECTION.md 2026-08-08.
    """
    _, refusal = auth._gate({"email": "a123456@uach.mx", "email_verified": True})
    assert refusal == "student"


def test_a_professor_whose_username_contains_digits_still_passes():
    """The student rule is ONE leading letter then digits — nothing wider.

    A gate that refused every professor with a digit in their address would be a rail that
    cries wolf at the door, which is worse than the leak it prevents.
    """
    for ok in ("professor@uach.mx", "mgomez2@uach.mx", "ab1234@uach.mx", "a12@uach.mx"):
        email, refusal = auth._gate({"email": ok, "email_verified": True})
        assert refusal is None, f"{ok} should be allowed, got {refusal}"


def test_an_unverified_address_is_refused_even_on_the_right_domain():
    """Google will hand back an address the account holder never proved they own."""
    _, refusal = auth._gate({"email": "someone@uach.mx", "email_verified": False})
    assert refusal == "unverified"


def test_the_domain_check_is_a_suffix_not_a_substring():
    """`uach.mx.attacker.com` and `notuach.mx` both contain the domain. Neither is it."""
    for bad in ("professor@uach.mx.attacker.com", "professor@notuach.mx", "uach.mx@gmail.com"):
        _, refusal = auth._gate({"email": bad, "email_verified": True})
        assert refusal == "domain", bad


def test_the_email_is_lowercased_before_it_is_judged():
    email, refusal = auth._gate({"email": "  PROFessor@UACH.MX ", "email_verified": True})
    assert (email, refusal) == ("professor@uach.mx", None)


# ---------------------------------------------------------------------------
# What is reachable signed out
# ---------------------------------------------------------------------------

def test_the_landing_page_is_public(client):
    assert client.get("/").status_code == 200


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


def test_the_susai_webhook_is_public(client):
    """🔴 Meta authenticates with the app-secret signature, not a professor session.

    Gating this would take SUSAI off the air with no error anyone would see.
    """
    r = client.get("/webhook")
    assert r.status_code == 200 and r.json() == {"susai": "reachable"}


def test_a_cockpit_route_redirects_to_the_landing_page(client):
    r = client.get("/courses/1")
    assert r.status_code == 303
    assert r.headers["location"] == "/?next=/courses/1"


def test_a_post_is_refused_rather_than_redirected(client):
    """A 303 on a POST would replay it as a GET and look like it worked."""
    assert client.post("/courses/1/publish").status_code == 401


def test_htmx_gets_a_redirect_header_not_a_page(client):
    """Without HX-Redirect the landing page gets swapped into whatever div made the call."""
    r = client.get("/courses/1", headers={"HX-Request": "true"})
    assert r.status_code == 401
    assert r.headers["HX-Redirect"] == "/?auth_error=expired"


def test_a_brand_new_route_is_gated_without_being_told():
    """The point of default-deny: nobody has to remember to protect the next router."""
    app = FastAPI()

    @app.get("/")
    def landing():
        return {}

    auth.install(app)

    @app.get("/a-route-added-after-the-gate-was-installed")
    def late():
        return {"secret": "roster"}

    r = TestClient(app, follow_redirects=False).get("/a-route-added-after-the-gate-was-installed")
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# Signed in
# ---------------------------------------------------------------------------

def test_a_signed_in_professor_reaches_the_cockpit(client):
    _signed_in(client)
    r = client.get("/courses/1")
    assert r.status_code == 200 and r.json() == {"course": 1}


def test_a_cookie_signed_with_the_wrong_key_does_not_open_the_gate(client):
    """The cookie is the credential; an unsigned or foreign-signed one is not one."""
    payload = base64.b64encode(json.dumps({"user": {"email": "professor@uach.mx"}}).encode())
    client.cookies.set(auth.SESSION_COOKIE, TimestampSigner("some-other-key").sign(payload).decode())
    assert client.get("/courses/1").status_code == 303


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------

def test_an_unconfigured_app_seals_itself_instead_of_falling_open(monkeypatch):
    """🔴 The direction of this failure is the whole point.

    A deploy with no `GOOGLE_CLIENT_SECRET` must serve nothing, not everything. The landing
    page stays up so the 503 is diagnosable, and it names the missing variable itself.
    """
    monkeypatch.setattr(settings, "google_client_secret", "")
    c = TestClient(_build_app(), follow_redirects=False)

    assert c.get("/").status_code == 200          # still diagnosable
    r = c.get("/courses/1")
    assert r.status_code == 503
    assert r.json()["missing"] == ["GOOGLE_CLIENT_SECRET"]


def test_missing_config_names_every_unset_value(monkeypatch):
    for name in ("google_client_id", "google_client_secret", "session_secret"):
        monkeypatch.setattr(settings, name, "")
    assert auth.missing_config() == [
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET"
    ]


# ---------------------------------------------------------------------------
# Open redirect
# ---------------------------------------------------------------------------

def test_next_only_ever_points_back_at_this_site():
    """`//evil.com` is a protocol-relative URL. `startswith("/")` alone lets it through."""
    for hostile in ("//evil.com", "https://evil.com", "http://evil.com", None, "", "javascript:x"):
        assert auth._safe_next(hostile) == "/"
    assert auth._safe_next("/courses/9067/dates") == "/courses/9067/dates"


def test_public_paths_are_matched_as_prefixes_not_substrings():
    assert auth.is_public_path("/") and auth.is_public_path("/health")
    assert auth.is_public_path("/auth/login") and auth.is_public_path("/webhook")
    # A route that merely *contains* a public word is not public.
    assert not auth.is_public_path("/courses/1/health")
    assert not auth.is_public_path("/courses")
