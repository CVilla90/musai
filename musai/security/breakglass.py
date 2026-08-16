"""The door that does not depend on Google — and the reason it has to exist.

MUSAI is Carlos's: his idea, his code, his cloud account, his Gemini key. But the identity that
owns every row in it is **`cavilla@uach.mx`, an account the university controls.** UACH's central
administration can reset that password, suspend the mailbox, or close it when a contract ends,
and none of that is an attack — it is ordinary IT housekeeping happening to an account that
happens to be the only key to somebody's application. The failure is silent and total: the app
keeps running perfectly, serving a sign-in button that no longer opens for its owner.

There are three ways in, and they fail independently on purpose:

1. **Google as `cavilla@uach.mx`** — the everyday path.
2. **Google as a recovery alias** (`ADMIN_RECOVERY_EMAILS`, e.g. a personal Gmail) — covers the
   institutional account being reset or closed. ⭐ **This needs no second OAuth client.** The
   client MUSAI already has will authenticate *any* Google account; `hd=` on the authorize URL
   only pre-filters the account chooser, and `_gate()` in `musai/web/auth.py` is the thing that
   actually refuses. Widening that one allow-list is the entire change.
3. **This module** — email + password, no third party involved at all. It covers the case the
   other two cannot: the **OAuth client itself** going away. That client lives in a Google Cloud
   project, and if that project was created under the institutional account, losing the account
   can lose the project — which breaks sign-in for *everyone*, not just its owner.

🔴 **All three sign in as the same person.** A recovery address is an ALIAS, not a second
account. Issuing a session under `carlosavillah90@gmail.com` would resolve to a brand-new
`Professor` row owning zero courses — a technically successful login into an empty cockpit,
which is not recovery. `musai/web/auth.py` maps every recovery identity onto `admin_email`, and
records the address that actually authenticated so the audit trail still tells the truth.

What keeps this door shut
-------------------------
* **It does not exist unless configured.** No `BREAK_GLASS_EMAIL` + `BREAK_GLASS_PASSWORD_HASH`
  ⇒ the routes answer **404**, not 401. An unconfigured door should not advertise itself.
* **The password's entropy is the control, not the rate limiter.** `new_password()` returns
  ~190 bits from `secrets`. That is not guessable at any request rate, which matters because
  the limiter below is per-process and a multi-instance deploy would multiply its budget by the
  instance count. The limiter buys log noise and slows a targeted attempt; the password is what
  makes the door safe.
* **Only a hash is ever stored** (scrypt, memory-hard). `.env` never holds the password, and a
  leaked `.env` does not open the door.
* **Wrong email and wrong password cost the same.** Both run the full KDF and compare with
  `hmac.compare_digest`, so response time does not reveal whether the address was right.
* **Every attempt is audited**, success or failure — see `musai/web/auth.py`. Nothing here ever
  logs, returns or `repr`s the password itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

# scrypt cost. n=2^15/r=8/p=1 is ~32 MB and ~60–100 ms on this hardware — slow enough to make
# offline cracking of a leaked hash expensive, fast enough that a human login does not stall.
# 🔴 `maxmem` must be passed explicitly: OpenSSL's default ceiling is 32 MB and these parameters
# need slightly more, so the default raises `MemoryError` rather than hashing.
_N, _R, _P = 2 ** 15, 8, 1
_MAXMEM = 64 * 1024 * 1024
_DKLEN = 32
_SCHEME = "scrypt"

#: A dummy hash to verify against when the *email* was wrong. Real work, constant-ish time,
#: no branch that returns early and leaks "that address is not the one".
_DECOY = ""


def new_password() -> str:
    """A password to hand the owner once. 32 url-safe chars, ~190 bits from `secrets`.

    Deliberately not memorable. This is written down in a password manager and used perhaps
    once ever; optimising it for typing would trade the only property that matters.
    """
    return secrets.token_urlsafe(24)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """`scrypt$n$r$p$salt$key`, all base64. The only form that is ever stored."""
    if not password:
        raise ValueError("Refusing to hash an empty password.")
    salt = salt if salt is not None else secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P,
                         maxmem=_MAXMEM, dklen=_DKLEN)
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")   # noqa: E731
    return f"{_SCHEME}${_N}${_R}${_P}${b64(salt)}${b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    """True if `password` produces `encoded`. Never raises — a malformed hash is just False.

    A hash that cannot be parsed means the environment is misconfigured, and the correct
    behaviour for a lock whose configuration is broken is to stay **shut**.
    """
    try:
        scheme, n, r, p, salt_b64, key_b64 = encoded.split("$")
        if scheme != _SCHEME:
            return False
        key = hashlib.scrypt(
            (password or "").encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM,
            dklen=len(base64.b64decode(key_b64)),
        )
        return hmac.compare_digest(key, base64.b64decode(key_b64))
    except Exception:
        return False


def check(email: str, password: str, *, expect_email: str, expect_hash: str) -> bool:
    """The whole credential check, in constant-ish time.

    🔴 Both halves are always evaluated. `if email != expect: return False` would answer a wrong
    address in microseconds and a wrong password in ~80 ms, which turns the door into an oracle
    for *which* address opens it.
    """
    global _DECOY
    if not expect_email or not expect_hash:
        return False
    email_ok = hmac.compare_digest(
        (email or "").strip().lower().encode("utf-8"),
        expect_email.strip().lower().encode("utf-8"),
    )
    if not _DECOY:
        _DECOY = hash_password(secrets.token_urlsafe(16))
    # Verify against the real hash only when the address matched; otherwise burn the same work
    # on a decoy so the timing is indistinguishable.
    password_ok = verify_password(password, expect_hash if email_ok else _DECOY)
    return email_ok and password_ok


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
#
# ⚠️ Per-process and in-memory: a restart forgets it, and N instances allow N times the budget.
# Stated plainly rather than implied, because a limiter believed to be global when it is not is
# worse than none. It is here to make a targeted attempt loud and slow — `new_password()`'s
# entropy is what makes the door actually safe.

WINDOW_SECONDS = 15 * 60
MAX_FAILURES = 5

_failures: dict[str, list[float]] = {}


def _recent(key: str, now: float) -> list[float]:
    hits = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
    if hits:
        _failures[key] = hits
    else:
        _failures.pop(key, None)
    return hits


def locked_out(key: str, *, now: float | None = None) -> int:
    """Seconds until `key` may try again, or 0. Called before the password is even looked at."""
    now = time.time() if now is None else now
    hits = _recent(key, now)
    if len(hits) < MAX_FAILURES:
        return 0
    return max(1, int(WINDOW_SECONDS - (now - min(hits))))


def record_failure(key: str, *, now: float | None = None) -> int:
    """Count one failed attempt. Returns how many remain before lockout."""
    now = time.time() if now is None else now
    hits = _recent(key, now)
    hits.append(now)
    _failures[key] = hits
    return max(0, MAX_FAILURES - len(hits))


def clear(key: str) -> None:
    """Forget a key's failures — called on a successful sign-in."""
    _failures.pop(key, None)


def reset_all() -> None:
    """Drop every counter. For tests, so one test's lockout cannot fail the next."""
    _failures.clear()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _main() -> None:
    """`python -m musai.security.breakglass` — print a password and its hash, once.

    The password is shown exactly here and never stored anywhere by MUSAI. If it is lost, the
    answer is to run this again and replace the hash; there is no recovery for the recovery.
    """
    password = new_password()
    encoded = hash_password(password)
    print()
    print("  MUSAI break-glass credentials")
    print("  " + "─" * 68)
    print()
    print("  Store this PASSWORD in your password manager. It is shown once and MUSAI")
    print("  never keeps a copy — only the hash below, which cannot be reversed.")
    print()
    print(f"      password:  {password}")
    print()
    print("  Put these three lines in .env (local) and in Replit Secrets (deployed).")
    print("  The hash is safe to paste into a config file; the password is not.")
    print()
    print("      BREAK_GLASS_EMAIL=your.personal@gmail.com")
    print(f"      BREAK_GLASS_PASSWORD_HASH={encoded}")
    print("      ADMIN_RECOVERY_EMAILS=your.personal@gmail.com")
    print()
    print("  Then the door is at  /auth/break-glass  — it 404s until both values are set.")
    print()


if __name__ == "__main__":   # pragma: no cover
    _main()
