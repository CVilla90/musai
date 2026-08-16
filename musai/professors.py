"""Who a professor is, which courses are theirs, and which passwords they have stored.

The seam `AUTH_SETUP.md` §4 step 2 has been describing since 2026-08-07. Sign-in (2026-08-13)
proved *the address*; this proves *the person* — and it exists because a second professor is
about to use the same database.

🔴 **The one rule this module is for: a professor sees their own courses and nobody else's.**
`Course.professor_id` was a nullable int pointing at nothing, so every query in the cockpit was
unscoped. With one user that is invisible; with two it hands Colleague D the owner's 186 students.
`courses_owned_by()` is therefore the only course listing the cockpit calls, and an **unowned
course belongs to nobody** — never to everybody. Failing towards "you see nothing" is a bug
report; failing the other way is a data breach.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from musai.config import settings
from musai.models import Course, Professor, ProfessorCredential
from musai.security import vault

MOODLE, SEGA = "moodle", "sega"
SYSTEMS = (MOODLE, SEGA)

#: What each system is called on screen, and where it is used. One dict so the Settings page
#: and the error messages cannot drift apart.
SYSTEM_INFO = {
    MOODLE: {
        "label": "Moodle · campusvirtual.uach.mx",
        "why": "Reads your course list, creates backups and runs restores as you.",
        "host": "campusvirtual.uach.mx",
    },
    SEGA: {
        "label": "SEGA · sega.uach.mx",
        "why": "Uploads partial grades. MUSAI only ever clicks Guardar, never Confirmar.",
        "host": "sega.uach.mx",
    },
}


# ── identity ──────────────────────────────────────────────────────────────────
def get_or_create(sess: Session, *, email: str, full_name: str = "",
                  picture: str | None = None) -> Professor:
    """The row for this signed-in address, created on first sign-in.

    No invite flow and no approval step, deliberately: `musai/web/auth.py` has already decided
    whether this address may be here at all (domain + verified + not a student local-part).
    Duplicating that judgement here would give it two places to disagree.
    """
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("A professor needs an email — that is the identity key.")

    prof = sess.exec(select(Professor).where(Professor.email == email)).first()
    if prof is None:
        prof = Professor(
            email=email,
            full_name=full_name or "",
            picture=picture,
            # The admin is named in config, not granted in the UI: a self-grantable admin flag
            # is not a flag. Everyone else starts with no powers beyond their own courses.
            is_admin=(email == (settings.admin_email or "").strip().lower()),
        )
    else:
        # Google is the source of truth for the display fields; refresh them quietly.
        if full_name:
            prof.full_name = full_name
        if picture:
            prof.picture = picture
    prof.last_seen_at = datetime.utcnow()
    sess.add(prof)
    sess.commit()
    sess.refresh(prof)
    return prof


# ── ownership ─────────────────────────────────────────────────────────────────
def courses_owned_by(sess: Session, professor_id: int, *,
                     semester_id: Optional[int] = None) -> list[Course]:
    """Every course this professor owns, optionally in one semester. Ordered by group code.

    🔴 `professor_id` is required and is never defaulted. An unowned course (NULL) matches
    nobody, so a row that was never claimed simply does not appear — rather than appearing for
    everyone, which is the same query with the comparison left out.
    """
    stmt = select(Course).where(Course.professor_id == professor_id)
    if semester_id is not None:
        stmt = stmt.where(Course.semester_id == semester_id)
    return list(sess.exec(stmt.order_by(Course.group_code)).all())


def semester_ids_with_courses(sess: Session, professor_id: int) -> set[int]:
    """Which semesters this professor actually has history in.

    Drives the semester picker: offering someone a dropdown of semesters they never taught is
    an invitation to a blank page they cannot explain.
    """
    rows = sess.exec(
        select(Course.semester_id).where(Course.professor_id == professor_id)
    ).all()
    return {r for r in rows if r is not None}


def owns(sess: Session, professor_id: int, course_id: int) -> Optional[Course]:
    """The course, only if this professor owns it. `None` is a refusal, not an absence.

    Every route that acts on a course resolves it through here, so "does this course exist?"
    and "is it yours?" are answered by one query and cannot drift apart. The caller turns a
    `None` into a 404 — deliberately not a 403, which would confirm the course exists.
    """
    course = sess.get(Course, course_id)
    if course is None or course.professor_id != professor_id:
        return None
    return course


# ── credentials ───────────────────────────────────────────────────────────────
def get_credential(sess: Session, professor_id: int,
                   system: str) -> Optional[ProfessorCredential]:
    return sess.exec(
        select(ProfessorCredential).where(
            ProfessorCredential.professor_id == professor_id,
            ProfessorCredential.system == system,
        )
    ).first()


def credential_status(sess: Session, professor_id: int) -> dict[str, dict]:
    """What the Settings page renders: per system, whether a password is stored — never which.

    Deliberately returns no secret and no way to ask for one. The page can say *"stored, last
    used 14:22"*; it can never say what was stored, because a page that can show a password is
    a page that can leak one over a shoulder.
    """
    out: dict[str, dict] = {}
    for system in SYSTEMS:
        cred = get_credential(sess, professor_id, system)
        out[system] = {
            "stored": bool(cred and cred.secret_enc),
            "username": cred.username if cred else "",
            "updated_at": cred.updated_at if cred else None,
            "last_used_at": cred.last_used_at if cred else None,
            "last_ok_at": cred.last_ok_at if cred else None,
            **SYSTEM_INFO[system],
        }
    return out


def store_credential(sess: Session, professor_id: int, system: str, *,
                     username: str, password: str) -> ProfessorCredential:
    """Encrypt and save one credential, replacing any previous one for that system.

    Raises `VaultUnavailable` when no `CREDENTIAL_KEY` is configured — it does **not** store the
    password in the clear. That branch is the one that must not exist.
    """
    if system not in SYSTEMS:
        raise ValueError(f"Unknown system {system!r}; expected one of {SYSTEMS}.")
    username = (username or "").strip()
    if not username:
        raise ValueError("A username is required — MUSAI cannot guess which account this is.")
    if not password:
        raise ValueError("A password is required. To remove a stored one, use Delete.")

    token = vault.encrypt(password)  # raises VaultUnavailable before anything is written
    cred = get_credential(sess, professor_id, system)
    if cred is None:
        cred = ProfessorCredential(professor_id=professor_id, system=system)
    cred.username = username
    cred.secret_enc = token
    cred.updated_at = datetime.utcnow()
    # A replaced password has not authenticated yet, so the old verdict must not carry over —
    # a green "verified" badge above a password that was never tried is worse than no badge.
    cred.last_ok_at = None
    sess.add(cred)
    sess.commit()
    sess.refresh(cred)
    return cred


def delete_credential(sess: Session, professor_id: int, system: str) -> bool:
    """Forget a stored credential. Returns whether there was one. Idempotent by design.

    ⚠️ This is the only way a professor can withdraw consent, so it is a real row delete rather
    than a flag — "deleted but still decryptable" is not deletion.
    """
    cred = get_credential(sess, professor_id, system)
    if cred is None:
        return False
    sess.delete(cred)
    sess.commit()
    return True


def mark_used(sess: Session, professor_id: int, system: str, *, ok: bool) -> None:
    """Record that a credential was just used, and whether it authenticated.

    `last_ok_at` is what lets the UI distinguish *"stored"* from *"stored and known to work"*.
    A password that stopped working (UACH forces a change every term) otherwise looks identical
    to one that works, right up until a restore fails halfway.
    """
    cred = get_credential(sess, professor_id, system)
    if cred is None:
        return
    now = datetime.utcnow()
    cred.last_used_at = now
    if ok:
        cred.last_ok_at = now
    sess.add(cred)
    sess.commit()
