"""Symmetric encryption for stored credentials. The key lives in the environment, never in the DB.

MUSAI now holds **other professors' Moodle and SEGA passwords**, because a professor who signs
in with Google still has to be able to act on their own courses, and Moodle has no delegation
mechanism — the only way to act as someone is to log in as them. That is a real escalation of
what this project stores, and the shape of it is deliberate:

* **Reversible, not hashed.** A password MUSAI must replay into a login form cannot be a hash.
  So this is encryption, and the honest description is *"MUSAI can read your password"* — which
  is what the Settings page says out loud rather than implying a one-way store.
* **The key is `CREDENTIAL_KEY` in `.env` / Replit Secrets.** A database dump on its own decrypts
  to nothing. If the key is lost, every stored credential is permanently unreadable and everyone
  re-enters theirs — that is the correct failure, not a reason for a fallback.
* 🔴 **It fails CLOSED.** No key configured ⇒ `VaultUnavailable`, and storing is refused. There
  is deliberately no "store it in the clear for now" path: that branch is how plaintext
  passwords end up in a database, and this project has already paid for four of them sitting in
  source (`automation/credentials.py`).
* **Nothing here logs, prints or `repr`s a secret.** Errors name the *key* that is missing, or
  the *record* that would not decrypt — never the value.

Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) is used rather than anything hand-rolled.
It is authenticated, so a tampered or foreign token raises instead of returning garbage.
"""

from __future__ import annotations

from musai.config import settings


class VaultUnavailable(RuntimeError):
    """No usable encryption key. Nothing has been stored or read."""


class VaultCorrupt(RuntimeError):
    """A stored token could not be decrypted with the configured key.

    Almost always means the key was rotated or replaced, not that the data is damaged: a
    Fernet token is authenticated, so the *wrong key* and *tampered bytes* look identical.
    Either way the answer is the same — the professor re-enters the password.
    """


def generate_key() -> str:
    """A fresh key, printable, for pasting into `.env` as `CREDENTIAL_KEY`."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def key_configured() -> bool:
    """True if a key is present and usable. Never raises — the UI asks this to explain itself."""
    try:
        _fernet()
        return True
    except VaultUnavailable:
        return False


def _fernet():
    """The cipher, or a refusal naming exactly what to set. Built per call, not cached.

    Not cached on purpose: a cached cipher survives an `.env` fix and keeps reporting the old
    state, which is precisely the class of bug `README.md` already warns about for the sign-in
    values ("`.env` changes need a FULL restart"). Fernet construction is microseconds.
    """
    from cryptography.fernet import Fernet

    raw = (settings.credential_key or "").strip()
    if not raw:
        raise VaultUnavailable(
            "CREDENTIAL_KEY is not set, so stored passwords cannot be encrypted or read. "
            "Generate one with:\n"
            "  python -c \"from musai.security.vault import generate_key; print(generate_key())\"\n"
            "and put it in .env as CREDENTIAL_KEY=…  (a FULL restart is needed — --reload does "
            "not reread .env).")
    try:
        return Fernet(raw.encode())
    except Exception as e:  # malformed key — say so without echoing it
        raise VaultUnavailable(
            f"CREDENTIAL_KEY is set but is not a valid Fernet key ({type(e).__name__}). It must "
            f"be 32 url-safe base64-encoded bytes; regenerate it with "
            f"musai.security.vault.generate_key().") from e


def encrypt(plaintext: str) -> str:
    """Password → storable token. Refuses an empty value rather than storing an empty secret."""
    if not plaintext:
        raise ValueError("Refusing to encrypt an empty secret — delete the credential instead.")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Stored token → password. Raises rather than returning "" for an unreadable record.

    🔴 The empty string is a plausible-looking password and would be typed into a live login
    form. An unreadable credential is UNKNOWN, not blank — the same doctrine
    `feedback_unreadable_is_not_a_finding` was written for.
    """
    from cryptography.fernet import InvalidToken

    if not token:
        raise VaultCorrupt("This credential has no stored secret. Re-enter the password.")
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise VaultCorrupt(
            "A stored password could not be decrypted with the current CREDENTIAL_KEY — the key "
            "has almost certainly changed. Re-enter the password in Settings ▸ Passwords."
        ) from e
