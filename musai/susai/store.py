"""SUSAI conversation store — the ONLY tables SUSAI writes: Conversation, Message,
UsageCounter. All go through `ro_engine`, which on Postgres is the `susai_ro` role that
holds exactly these INSERT/UPDATE grants (and SELECT elsewhere). On SQLite dev it's the
same file. SUSAI can never reach grading/upload tables — the rail is structural.
"""

from datetime import date, datetime

from sqlmodel import Session, select

from musai.db import ro_engine
from musai.models import Conversation, Message, UsageCounter


def get_or_create_conversation(phone: str, student_id: int | None = None,
                               status: str = "open") -> int:
    """Find the conversation for `phone` (or create it), refreshing the bound student +
    status. Returns the conversation id."""
    with Session(ro_engine) as s:
        conv = s.exec(select(Conversation).where(Conversation.phone_e164 == phone)).first()
        if conv is None:
            conv = Conversation(phone_e164=phone, student_id=student_id, status=status)
        else:
            if student_id is not None:
                conv.student_id = student_id
            conv.status = status
            conv.updated_at = datetime.utcnow()
        s.add(conv)
        s.commit()
        s.refresh(conv)
        return conv.id


def log_message(conversation_id: int, direction: str, role: str, body: str,
                wa_message_id: str | None = None) -> None:
    """Persist one inbound ("in"/"user") or outbound ("out"/"assistant") message."""
    with Session(ro_engine) as s:
        s.add(Message(conversation_id=conversation_id, direction=direction, role=role,
                      body=body, wa_message_id=wa_message_id))
        s.commit()


def recent_messages(conversation_id: int, limit: int = 12) -> list[dict]:
    """The last `limit` messages of a conversation, oldest→newest, as plain dicts
    ({direction, body}) so they survive the closed session. Fed back to Gemini as history."""
    with Session(ro_engine) as s:
        rows = s.exec(select(Message).where(Message.conversation_id == conversation_id)
                      .order_by(Message.id.desc()).limit(limit)).all()
    return [{"direction": m.direction, "body": m.body} for m in reversed(rows)]


def get_usage(phone: str) -> tuple[int, int]:
    """Today's (msg_count, ai_count) for this phone."""
    with Session(ro_engine) as s:
        uc = s.exec(select(UsageCounter).where(
            UsageCounter.phone_e164 == phone, UsageCounter.day == date.today())).first()
        return (uc.msg_count, uc.ai_count) if uc else (0, 0)


def bump_usage(phone: str, *, msg: bool = True, ai: bool = False) -> None:
    """Increment today's counters. A message that triggers a Gemini call counts once as a
    message and once as AI: call ``bump_usage(phone)`` on receipt, then
    ``bump_usage(phone, msg=False, ai=True)`` on a successful answer."""
    with Session(ro_engine) as s:
        uc = s.exec(select(UsageCounter).where(
            UsageCounter.phone_e164 == phone, UsageCounter.day == date.today())).first()
        if uc is None:
            uc = UsageCounter(phone_e164=phone, day=date.today(), msg_count=0, ai_count=0)
        if msg:
            uc.msg_count += 1
        if ai:
            uc.ai_count += 1
        s.add(uc)
        s.commit()
