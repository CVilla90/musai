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
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from musai.config import settings
from musai.security import breakglass

SESSION_COOKIE = "musai_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7   # a week; a professor should not sign in every morning

# Paths served without a session. Everything not matching one of these is gated.
#   /              — the landing page (it renders the sign-in button; signed in, it is the cockpit)
#   /health        — liveness probe, no data in the payload
#   /auth/…        — the sign-in round trip itself, which by definition runs signed out
#   /webhook       — SUSAI's Meta callback. 🔴 It authenticates with the Meta app-secret
#                    signature, NOT a professor session. Gating it would silently kill SUSAI.
PUBLIC_PREFIXES: tuple[str, ...] = ("/health", "/auth/", "/webhook", "/favicon.ico")

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


@router.get("/login")
async def login(request: Request):
    if not settings.auth_configured:
        raise HTTPException(503, f"Sign-in is not configured: {', '.join(missing_config())}")
    t0 = time.monotonic()
    request.session["next"] = _safe_next(request.query_params.get("next"))
    request.session["_t_login"] = time.time()
    resp = await oauth.google.authorize_redirect(
        request, _redirect_uri(request), hd=settings.allowed_email_domain or None
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


# ---------------------------------------------------------------------------
# The break-glass door
# ---------------------------------------------------------------------------
#
# Deliberately self-contained: no Jinja environment, no base template, no stylesheet. This is
# the page that has to work on the day something else does not, and every dependency it takes
# is one more thing that can be broken at the moment it is needed. It is also unlisted — nothing
# links here — but the *unlisted* part is not the security. The password is.

def _break_glass_page(error: str = "") -> str:
    note = (f'<p class="err" role="alert">{error}</p>' if error else
            '<p class="hint">This door exists because the everyday one depends on an account '
            'the university controls. Use it only when Google sign-in cannot let you in.</p>')
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>MUSAI — recovery sign-in</title>
<style>
  :root {{ color-scheme: light dark; --bg:#faf7f2; --fg:#1d1b18; --mut:#6b6560;
           --line:#ddd6cc; --card:#fff; --err:#a3341f; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#16151a; --fg:#eceaf3; --mut:#9a95a5; --line:#33313c; --card:#1e1d24;
             --err:#ff9b83; }} }}
  * {{ box-sizing:border-box }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center; padding:24px;
          background:var(--bg); color:var(--fg);
          font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif }}
  form {{ width:100%; max-width:400px; background:var(--card); border:1px solid var(--line);
          border-radius:14px; padding:28px }}
  h1 {{ margin:0 0 4px; font-size:19px; letter-spacing:-.01em }}
  .sub {{ margin:0 0 20px; color:var(--mut); font-size:13px }}
  label {{ display:block; margin:14px 0 5px; font-size:12.5px; font-weight:600;
           text-transform:uppercase; letter-spacing:.06em; color:var(--mut) }}
  input {{ width:100%; padding:10px 12px; font-size:15px; font-family:inherit;
           border:1px solid var(--line); border-radius:8px;
           background:var(--bg); color:var(--fg) }}
  input:focus {{ outline:2px solid #7a6ff0; outline-offset:1px }}
  button {{ width:100%; margin-top:20px; padding:11px; font-size:15px; font-weight:600;
            font-family:inherit; border:0; border-radius:8px; cursor:pointer;
            background:#3f3a52; color:#fff }}
  .hint, .err {{ font-size:12.5px; margin:16px 0 0; padding-top:14px;
                 border-top:1px solid var(--line) }}
  .hint {{ color:var(--mut) }}
  .err {{ color:var(--err); font-weight:600 }}
  a {{ color:var(--mut); font-size:12.5px }}
</style></head><body>
<form method="post" action="/auth/break-glass">
  <h1>Recovery sign-in</h1>
  <p class="sub">MUSAI &middot; for the owner, when Google cannot be used</p>
  <label for="e">Email</label>
  <input id="e" name="email" type="email" autocomplete="username" autofocus required>
  <label for="p">Password</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button>
  {note}
  <p style="margin:12px 0 0"><a href="/">&larr; Back to the normal sign-in</a></p>
</form></body></html>"""


def _client_key(request: Request) -> str:
    """The rate-limit bucket: the caller's address.

    ⚠️ Per-IP rather than global, and that is the deliberate trade. A global counter would be
    harder to evade — and would let anyone who finds this URL lock the owner out of his own
    recovery path by failing five times. For a door whose entire purpose is *availability to
    one person*, a limiter that an attacker can turn into a denial of service is worse than a
    limiter they can sidestep with a second IP.
    """
    return request.client.host if request.client else "unknown"


@router.get("/break-glass", response_class=HTMLResponse)
async def break_glass_form(request: Request):
    if not settings.break_glass_configured:
        raise HTTPException(404, "Not found.")
    return HTMLResponse(_break_glass_page())


@router.post("/break-glass", response_class=HTMLResponse)
async def break_glass_submit(request: Request,
                             email: str = Form(default=""),
                             password: str = Form(default="")):
    if not settings.break_glass_configured:
        raise HTTPException(404, "Not found.")

    key = _client_key(request)
    wait = breakglass.locked_out(key)
    if wait:
        _log.warning("break-glass ▸ %s is locked out for another %ds", key, wait)
        return HTMLResponse(
            _break_glass_page(error=f"Too many attempts. Try again in {wait // 60 + 1} minutes."),
            status_code=429)

    if not breakglass.check(email, password,
                            expect_email=settings.break_glass_email,
                            expect_hash=settings.break_glass_password_hash):
        left = breakglass.record_failure(key)
        _log.warning("break-glass ▸ FAILED attempt from %s — %d left before lockout", key, left)
        # 🔴 The attempted address is deliberately NOT recorded. It is attacker-controlled free
        # text, and the commonest way a login form gets a *password* typed into it is the user
        # being one field off. An audit row must never be able to become a place secrets land.
        _audit("auth.break_glass_failed", actor="unknown",
               detail={"from": key, "attempts_left": left})
        return HTMLResponse(
            _break_glass_page(error="That email and password did not match."), status_code=401)

    breakglass.clear(key)
    user = _session_user(settings.owner_email, via="break-glass")
    user["signed_in_as"] = settings.break_glass_email.strip().lower()
    request.session["user"] = user
    _log.warning("break-glass ▸ SIGN-IN as %s from %s — the recovery door was used",
                 settings.owner_email, key)
    _audit("auth.break_glass_signin", actor=settings.owner_email, detail={"from": key})
    return RedirectResponse(url="/", status_code=303)


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

        if not settings.sign_in_available:
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

    The session cookie is only attached when a `SESSION_SECRET` exists — signing with an empty
    key would be a session store in name only. With it absent, `current_user` returns None and
    the gate 503s, which is the fail-closed behaviour we want out of a half-configured deploy.

    ⚠️ The condition is `session_secret`, not `auth_configured`, and the difference is the
    whole point of the break-glass door: the scenario it covers is **Google being the broken
    part**. Keying the session store on the Google client would mean that in exactly that
    scenario the recovery page could verify the password and then have nowhere to put the
    session — a door that opens onto a wall.
    """
    app.add_middleware(AuthGateMiddleware)
    if settings.session_secret:
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret,
            session_cookie=SESSION_COOKIE,
            max_age=SESSION_MAX_AGE,
            same_site="lax",   # "strict" would drop the cookie on Google's redirect back
            https_only=settings.app_base_url.startswith("https://"),
        )
