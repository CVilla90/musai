"""SUSAI identity binding — resolve an inbound phone to a VERIFIED student.

Read-only (via ro_engine). A number only earns data access once it's bound to a student
through a verified `WhatsAppLink` (PLAN §7: trusted because the student registered it while
authenticated in Moodle). Unknown numbers get nothing.
"""

from sqlmodel import Session, select

from musai.config import settings
from musai.db import ro_engine
from musai.models import Student, WhatsAppLink
from musai.susai.send import normalize_recipient


def canonical(phone: str) -> str:
    """Canonical phone key for matching: digits only, Mexico `521…`→`52…`
    (reuses the same normalization SUSAI uses when sending)."""
    return normalize_recipient(phone)


def is_admin(phone: str) -> bool:
    """True if this number is the owner's (the coordinator). Compared canonically."""
    admin = settings.susai_admin_phone
    return bool(admin) and canonical(phone) == canonical(admin)


def resolve_student(phone: str):
    """Return (Student, WhatsAppLink) for a VERIFIED link matching `phone`, else (None, None).
    Compares on the canonical form so the inbound wa_id (`521…`) matches a stored `+52…`."""
    target = canonical(phone)
    with Session(ro_engine) as s:
        links = s.exec(select(WhatsAppLink).where(WhatsAppLink.verified == True)).all()  # noqa: E712
        for link in links:
            if canonical(link.phone_e164) == target:
                return s.get(Student, link.student_id), link
    return None, None
