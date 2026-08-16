"""Google sign-in for the cockpit, and the default-deny gate in front of every route.

Two things live here, and the second is the important one.

1. **The OAuth round trip** (`/auth/login` → Google → `/auth/google/callback`). Ported from
   `Catedra/backend/auth.py`, with the router prefix changed from `/api/auth` to `/auth` —
   the path actually registered in the Google client (`client_secret_*.json` carries exactly
   three redirect URIs, all of them `/auth/google/callback`).

2. **`AuthGateMiddleware`** — a default-deny gate. Every path is protected *unless* it is on
   `PUBLIC_PREFIXES`. This is deliberately a middleware and not a per-route dependency: MUSAI
   has eight routers and grows a new one every few weeks, and a rail that depends on the next
   author remembering to add `Depends(require_professor)` is not a rail. A route added later
   is protected because it exists, not because someone protected it.

Three properties worth stating plainly, because each one is a way this could have been wrong:

- **It fails CLOSED.** With no `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `SESSION_SECRET`,
  the cockpit refuses to serve at all rather than falling open. A misconfigured deploy that
  silently serves the gradebook to the internet is the failure this ordering prevents; the
  landing page says exactly which value is missing, so a lockout is self-diagnosing.
- **The domain check is server-side.** Google's `hd` parameter only pre-filters the account
  chooser — it is a hint to the UI, not a gate, and a hand-built authorize URL ignores it.
  The refusal that counts is `_gate()` below, on the token Google actually returned.
- **`email_verified` is checked too.** A Google account can carry an unverified address; the
  domain suffix alone would then be assertable by someone who does not own the mailbox.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from musai.config import settings

SESSION_COOKIE = "musai_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7   # a week; a professor should not sign in every morning

# Paths served without a session. Everything not matching one of these is gated.
#   /              — the landing page (it renders the sign-in button; signed in, it is the cockpit)
#   /health        — liveness probe, no data in the payload
#   /auth/…        — the sign-in round trip itself, which by definition runs signed out
#   /webhook       — SUSAI's Meta callback. 🔴 It authenticates with the Meta app-secret
#                    signature, NOT a professor session. Gating it would silently kill SUSAI.
#   /lang/…        — the EN/ES picker, which the landing page needs signed out. It reads
#                    nothing and can only store one of two constant strings; see
#                    `musai/web/language.py::choose` for why that is a narrow enough exemption.
PUBLIC_PREFIXES: tuple[str, ...] = ("/health", "/auth/", "/webhook", "/favicon.ico", "/lang/")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_public_path(path: str) -> bool:
    """True for the landing page and the handful of prefixes that run signed out."""
    if path == "/":
        return True
    return any(path == p.rstrip("/") or path.startswith(p) for p in PUBLIC_PREFIXES)


def missing_config() -> list[str]:
    """Which of the three required values are unset. Drives the landing page's own message."""
    return [
        name
        for name, value in (
            ("GOOGLE_CLIENT_ID", settings.google_client_id),
            ("GOOGLE_CLIENT_SECRET", settings.google_client_secret),
            ("SESSION_SECRET", settings.session_secret),
        )
        if not value
    ]


def current_user(request: Request) -> dict | None:
    """The signed-in professor, or None. Never raises — templates call this."""
    try:
        user = request.session.get("user")
    except AssertionError:
        # SessionMiddleware is not installed (auth unconfigured). Signed out, by definition.
        return None
    return user if isinstance(user, dict) and user.get("email") else None


def require_professor(request: Request) -> dict:
    """Dependency form of the gate, for routes that want the professor's identity."""
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to use MUSAI.")
    return user


# A UACH student address is one letter followed by their matrícula: `a123456@uach.mx`.
# A professor's is letters: `professor@uach.mx`. Requiring 4+ digits keeps the pattern narrow —
# a professor whose username merely ends in a digit (`mgomez2`) does not match, because the
# rule needs a SINGLE leading letter and nothing but digits after it.
_STUDENT_LOCALPART = re.compile(r"^[a-z]\d{4,}$")


def _gate(info: dict) -> tuple[str | None, str | None]:
    """Decide on Google's verified claims. Returns (email, refusal_reason).

    🔴 Only `@uach.mx`, by the owner's instruction (2026-08-13) — **plus the exact addresses in
    `ADMIN_RECOVERY_EMAILS`, by his instruction of 2026-08-16.** That second instruction
    supersedes the first's "no allow-list, no exceptions", and the reason it had to is worth
    keeping: the address the original rail protected is `cavilla@uach.mx`, which **the
    university owns and MUSAI's author does not.** A gate with no way around it locks out the
    owner exactly as effectively as it locks out an attacker, and UACH resetting a staff mailbox
    is not an attack — it is Tuesday.

    The original worry (a bypass list that quietly grows until the gate means nothing) is
    answered by shape, not by discipline: this list holds **exact, whole addresses**, never a
    domain, and an entry still has to arrive with `email_verified` from Google. It cannot
    accidentally admit a population the way `allowed_email_domain = "gmail.com"` would.
    ⭐ It needs **no second OAuth client** — MUSAI's existing client already authenticates any
    Google account, and `hd=` on the authorize URL only pre-filters the account chooser.

    🔴 The domain alone is NOT enough, and this is easy to miss: **UACH students have
    `@uach.mx` addresses too.** the owner's own student account, `a123456@uach.mx`, sits in his
    Google account chooser one row below `professor@uach.mx`. MUSAI is professor-only and the
    landing page says so out loud — so the student pattern is rejected here, per the decision
    recorded in `PRODUCT_DIRECTION.md` (2026-08-08).

    ⚠️ That decision also states the caveat, and it still holds: this is a **heuristic on a
    naming convention the university controls, not us.** It is a second lock, never the only
    one, and the correct long-term answer is an explicit professor allow-list — which arrives
    with the `Professor` table (`AUTH_SETUP.md` §4 step 2). Failing toward "ask the owner" is the
    intended failure mode.
    """
    email = (info.get("email") or "").lower().strip()
    if not email:
        return None, "no_email"
    if not info.get("email_verified"):
        return None, "unverified"
    if settings.is_recovery_address(email):
        # An exact address the owner put in `.env` himself. It skips the domain rule and the
        # student-local-part rule because both are heuristics *about a population* — and this
        # is one named individual, already proven by Google.
        return email, None
    domain = settings.allowed_email_domain.lower().strip()
    if domain and not email.endswith("@" + domain):
        return None, "domain"
    if _STUDENT_LOCALPART.match(email.split("@")[0]):
        return None, "student"
    return email, None


def _session_user(email: str, info: dict | None = None, *, via: str = "google") -> dict:
    """The session payload for a verified address.

    🔴 **A recovery alias is issued a session as `admin_email`, not as itself.** Every course,
    credential and usage row in MUSAI hangs off `Professor.email`, so signing in under
    `someone@gmail.com` would resolve — via `get_or_create` — to a *new* professor row owning
    nothing. The login would succeed and the cockpit would be empty, which is the failure this
    whole feature exists to prevent. So the alias authenticates; the owner is who acts.

    `signed_in_as` keeps the audit honest: the session records which door was actually used,
    so "the owner did this" and "the owner's recovery address did this" stay distinguishable.

    ⚠️ For a non-Google door there is no display name or picture, and the empty strings matter:
    `professors.get_or_create` only overwrites those fields when the incoming value is truthy,
    so a break-glass sign-in leaves the professor's existing name and avatar untouched instead
    of blanking them.
    """
    info = info or {}
    alias = settings.is_recovery_address(email)
    identity = settings.owner_email if alias else email
    return {
        "email": identity,
        "name": info.get("name") or (identity.split("@")[0] if via == "google" else ""),
        "picture": info.get("picture") or "",
        "is_admin": settings.is_admin_email(identity),
        "via": via,
        "signed_in_as": email,
    }


def _audit(action: str, *, actor: str, detail: dict) -> None:
    """Record a sign-in event that is not the everyday one. Never raises, never logs a secret.

    Wrapped whole: an audit write that fails must not turn a successful recovery into a 500.
    Losing the row is bad; locking the owner out *again*, at the moment he is already locked
    out, is worse.
    """
    try:
        import json

        from sqlmodel import Session

        from musai.db import engine
        from musai.models import AuditLog

        with Session(engine) as sess:
            sess.add(AuditLog(actor=actor, action=action, target="auth",
                              dry_run=False, detail_json=json.dumps(detail)))
            sess.commit()
    except Exception:                                   # pragma: no cover - best effort
        _log.warning("break-glass ▸ could not write the audit row for %s", action)


def _redirect_uri(request: Request) -> str:
    base = settings.app_base_url or str(request.base_url).rstrip("/")
    return f"{base.rstrip('/')}/auth/google/callback"


def _safe_next(raw: str | None) -> str:
    """Only same-site paths. `//evil.com` is a protocol-relative URL, not a local path."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

#: Sign-in felt like it took "a minute or more", and every component measurable from the
#: server is fast: `/auth/login` answers in ~0.4 s, Google's discovery doc, token endpoint and
#: JWKS all answer in ~0.1–0.3 s, importing the app costs ~1.1 s, and the signed-in cockpit
#: renders in ~9 ms. That rules out this process and leaves exactly one unmeasured span — the
#: browser's trip to Google and back, which includes the account chooser, any consent or
#: unverified-app interstitial, and the human choosing a row.
#:
#: ⭐ So it is measured rather than guessed. `/auth/login` stamps the session; the callback
#: reports the split. A number attributed to a remote system without an instrument pointed at
#: it is a hypothesis, and this project has paid for that mistake before.
#: ⚠️ Deliberately `uvicorn.error` and not `musai.auth.timing`. A logger of our own propagates
#: to a root logger uvicorn never attaches a handler to, so the line is formatted and then
#: dropped — the instrument reads as "sign-in is instrumented" while printing nothing. This is
#: the logger the server itself writes through, so the numbers land in the console being read.
_log = logging.getLogger("uvicorn.error")


def wants_any_account(raw: str | None) -> bool:
    """Should `/auth/login` open the chooser to every Google account?

    `?any=1`, but **only when a recovery address exists to reach**. With none configured a
    wider chooser can do nothing except surface accounts `_gate()` is about to refuse — an
    offer of a door onto a wall, and one more way to end up staring at a refusal page
    wondering which of the two rules rejected you.
    """
    return raw in ("1", "true", "yes") and bool(settings.recovery_addresses)


def authorize_kwargs(*, any_account: bool) -> dict:
    """What to send Google on the authorize URL. Pure, so the choice below is testable.

    🔴 `hd` is a **UI hint, not a gate** — and the half of that sentence that matters in
    practice is the first half. It does not secure anything (a hand-built authorize URL ignores
    it, which is why `_gate()` re-checks server-side), but it *does* filter Google's account
    chooser: with `hd=uach.mx` a personal Gmail is **not offered as a row to click.** So a
    recovery address could pass the gate and still be unreachable, because the owner never gets
    a chance to select it. Measured the hard way on 2026-08-16: the allow-list was correct, the
    login worked, and the account simply was not on the screen.

    ⚠️ Note what `hd` does *not* do: `a227222@uach.mx` is `@uach.mx`, so the filter never hid
    the owner's student account either. Its only real effect was hiding exactly the addresses
    the recovery path needs.

    `any_account` therefore drops the hint and forces the chooser open with
    `prompt=select_account` — otherwise Google silently reuses whichever account the browser is
    already signed into, which on the owner's machine is the institutional one he is trying to
    get around.
    """
    if any_account:
        return {"prompt": "select_account"}
    return {"hd": settings.allowed_email_domain or None}


@router.get("/login")
async def login(request: Request):
    if not settings.auth_configured:
        raise HTTPException(503, f"Sign-in is not configured: {', '.join(missing_config())}")
    t0 = time.monotonic()
    request.session["next"] = _safe_next(request.query_params.get("next"))
    request.session["_t_login"] = time.time()
    any_account = wants_any_account(request.query_params.get("any"))
    if any_account:
        _log.info("sign-in ▸ recovery chooser requested — sending no `hd` hint")
    resp = await oauth.google.authorize_redirect(
        request, _redirect_uri(request), **authorize_kwargs(any_account=any_account)
    )
    _log.info("sign-in ▸ /auth/login built the redirect in %.0f ms", (time.monotonic() - t0) * 1000)
    return resp


@router.get("/google/callback")
async def callback(request: Request):
    t_cb = time.monotonic()
    t_login = request.session.pop("_t_login", None)
    away = (time.time() - t_login) if t_login else None

    t_tok = time.monotonic()
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return RedirectResponse(url="/?auth_error=oauth")
    tok_ms = (time.monotonic() - t_tok) * 1000

    _log.info(
        "sign-in ▸ AWAY AT GOOGLE %s | token exchange %.0f ms | callback total %.0f ms",
        f"{away:.1f} s" if away is not None else "unknown (no /auth/login stamp)",
        tok_ms, (time.monotonic() - t_cb) * 1000,
    )
    if away is not None and away > 5:
        _log.warning(
            "sign-in ▸ %.1f s of the wait was the browser at Google, not MUSAI. If the consent "
            "screen appeared, check the OAuth app's publishing status — an app still in Testing "
            "shows an unverified-app interstitial on every single sign-in.", away)

    info = token.get("userinfo") or {}
    email, refusal = _gate(info)
    if refusal:
        # Carry the address back so the page can name it. Nothing is stored for a refusal.
        attempted = (info.get("email") or "").strip()
        suffix = f"&attempted={quote(attempted)}" if attempted else ""
        return RedirectResponse(url=f"/?auth_error={refusal}{suffix}")

    if settings.is_recovery_address(email):
        _log.warning("sign-in ▸ RECOVERY ALIAS %s signed in as %s", email, settings.owner_email)
        _audit("auth.recovery_signin", actor=settings.owner_email,
               detail={"via": "google", "signed_in_as": email})

    request.session["user"] = _session_user(email, info)
    return RedirectResponse(url=_safe_next(request.session.pop("next", "/")))


@router.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/?signed_out=1")


@router.get("/me")
async def me(request: Request):
    user = current_user(request)
    return {
        "authenticated": bool(user),
        "auth_configured": settings.auth_configured,
        "allowed_domain": settings.allowed_email_domain,
        "user": user,
    }


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

class AuthGateMiddleware(BaseHTTPMiddleware):
    """Default-deny. Anything not on `PUBLIC_PREFIXES` needs a signed-in professor."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if is_public_path(path):
            return await call_next(request)

        if not settings.auth_configured:
            # Fail closed, and say why — a 503 here is a configuration bug, not a login prompt.
            return JSONResponse(
                {"error": "auth_not_configured", "missing": missing_config()}, status_code=503
            )

        if current_user(request):
            return await call_next(request)

        # HTMX swaps the response body into the page, so a redirect here would paint the
        # landing page inside a table cell. `HX-Redirect` makes the browser navigate instead.
        if request.headers.get("HX-Request") == "true":
            return JSONResponse(
                {"error": "unauthenticated"},
                status_code=401,
                headers={"HX-Redirect": "/?auth_error=expired"},
            )
        if request.method == "GET":
            return RedirectResponse(url=f"/?next={quote(path)}", status_code=303)
        return JSONResponse({"error": "unauthenticated"}, status_code=401)


def install(app) -> None:
    """Attach the gate to an app. Called by `musai.web.app`, and by the tests on their own app.

    🔴 The order reads backwards. Starlette makes the LAST-added middleware the OUTERMOST one,
    so `SessionMiddleware` must be added *after* the gate for the gate to be able to read
    `request.session`. Swapped, every gated request 500s on a missing session.

    The session cookie is only attached when auth is configured — signing with an empty key
    would be a session store in name only. With it absent, `current_user` returns None and the
    gate 503s, which is the fail-closed behaviour we want out of a half-configured deploy.
    """
    app.add_middleware(AuthGateMiddleware)
    if settings.auth_configured:
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret,
            session_cookie=SESSION_COOKIE,
            max_age=SESSION_MAX_AGE,
            same_site="lax",   # "strict" would drop the cookie on Google's redirect back
            https_only=settings.app_base_url.startswith("https://"),
        )
