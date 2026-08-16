"""SUSAI orchestration — ties an inbound WhatsApp message to identity, persistence, rate
limits, the Gemini answer, and the reply. Everything here is read-only w.r.t. school data;
it writes only Conversation/Message/UsageCounter (via store.py) and sends a WhatsApp reply.
"""

from sqlmodel import Session

from musai.ai import budget as bud
from musai.automation._log import logger
from musai.db import engine
from musai.susai import store
from musai.susai.agent import answer as gemini_answer
from musai.susai.identity import canonical, is_admin, resolve_student
from musai.susai.prof_agent import answer as prof_answer
from musai.susai.send import send_text

# Generous caps for single-user dev; PLAN §9 targets for real students are 20 msgs/day and
# 6 AI-heavy/day. Tune down before the pilot.
MSG_DAILY_CAP = 80
AI_DAILY_CAP = 30


def _actor(phone: str) -> str:
    return f"wa:{phone}"


def _spend_ok(phone: str, admin: bool) -> tuple[bool, str]:
    """Token/request budget gate. Message caps above limit CHATTER; this limits COST — one
    message can fan out into several billed tool round-trips, so both are needed."""
    with Session(engine) as sess:
        allowed, why = bud.check(sess, _actor(phone), is_admin=admin)
        sess.commit()
    return allowed, why


def _book(phone: str, result) -> None:
    """Record what the call actually cost, success or failure."""
    if result is None:
        return
    with Session(engine) as sess:
        bud.record(sess, _actor(phone), result)
        sess.commit()

UNVERIFIED_REPLY = (
    "👋 ¡Hola! Aún no reconozco este número. Para ayudarte con tus calificaciones, primero "
    "registra tu WhatsApp en la actividad «Registra tu número de WhatsApp» en Moodle (campus "
    "virtual); así verifico que eres tú. 🐝\n\n"
    "(Hi! I don't recognize this number yet — please register it in the \"Register your "
    "WhatsApp number\" activity in Moodle so I can verify it's you.)"
)
CAP_REPLY = (
    "Has alcanzado tu límite de preguntas por hoy 🙏 escríbeme de nuevo mañana.\n"
    "(You've reached today's limit — message me again tomorrow.)"
)
BUDGET_REPLY = (
    "Alcancé el límite de uso de IA de hoy 🧮 vuelve a intentarlo mañana.\n"
    "(I've hit today's AI budget — try again tomorrow.)"
)
QUOTA_REPLY = (
    "Estoy recibiendo muchas preguntas ahora mismo ⏳ dame unos segundos e inténtalo otra vez.\n"
    "(I'm a bit busy right now — try again in a few seconds.)"
)
OFFLINE_REPLY = (
    "El asistente está temporalmente fuera de línea 🛠️ inténtalo más tarde.\n"
    "(The assistant is temporarily offline — please try later.)"
)
ERROR_REPLY = (
    "Uy, tuve un problema procesando eso 😅 ¿puedes intentarlo de nuevo?\n"
    "(Sorry, I hit a snag — please try again.)"
)
NONTEXT_REPLY = (
    "Por ahora solo puedo leer mensajes de texto 🙂 escríbeme tu pregunta.\n"
    "(For now I can only read text messages — please type your question.)"
)


def _send_and_log(sender: str, conv_id: int, reply: str) -> None:
    if send_text(sender, reply) is not None:
        store.log_message(conv_id, "out", "assistant", reply)


def _respond_admin(phone: str, sender: str, text: str, wa_message_id: str | None) -> None:
    """Coordinator path — read-only analytics over all of the owner's groups (no daily caps)."""
    conv_id = store.get_or_create_conversation(phone, None, "admin")
    store.log_message(conv_id, "in", "user", text, wa_message_id)
    store.bump_usage(phone)

    allowed, _why = _spend_ok(phone, admin=True)
    if not allowed:
        logger.warning("SUSAI admin hit the daily AI budget — refusing to spend.")
        _send_and_log(sender, conv_id, BUDGET_REPLY)
        return

    history = store.recent_messages(conv_id)
    res = prof_answer(text, history)
    _book(phone, res.get("result"))
    if res["ok"]:
        store.bump_usage(phone, msg=False, ai=True)
        _send_and_log(sender, conv_id, res["answer"])
        return
    reason = res.get("reason", "")
    if reason == "no_key":
        reply = OFFLINE_REPLY
    elif reason == "quota":
        reply = QUOTA_REPLY
    else:
        reply = ERROR_REPLY
        logger.error(f"SUSAI prof error: {reason}")
    _send_and_log(sender, conv_id, reply)


def respond(sender: str, profile_name: str, text: str, wa_message_id: str | None) -> None:
    """Handle one inbound text message end-to-end."""
    phone = canonical(sender)
    if is_admin(phone):
        _respond_admin(phone, sender, text, wa_message_id)
        return
    student, _ = resolve_student(phone)
    conv_id = store.get_or_create_conversation(
        phone, student.id if student else None, "open" if student else "unverified")
    store.log_message(conv_id, "in", "user", text, wa_message_id)
    msg_count, ai_count = store.get_usage(phone)
    store.bump_usage(phone)

    if not student:
        _send_and_log(sender, conv_id, UNVERIFIED_REPLY)
        return
    if msg_count >= MSG_DAILY_CAP or ai_count >= AI_DAILY_CAP:
        _send_and_log(sender, conv_id, CAP_REPLY)
        return
    allowed, _why = _spend_ok(phone, admin=False)
    if not allowed:
        _send_and_log(sender, conv_id, CAP_REPLY)
        return

    history = store.recent_messages(conv_id)
    res = gemini_answer(student, text, history)
    _book(phone, res.get("result"))
    if res["ok"]:
        store.bump_usage(phone, msg=False, ai=True)
        _send_and_log(sender, conv_id, res["answer"])
        return

    reason = res.get("reason", "")
    if reason == "no_key":
        reply = OFFLINE_REPLY
    elif reason == "quota":
        reply = QUOTA_REPLY
    else:
        reply = ERROR_REPLY
        logger.error(f"SUSAI agent error: {reason}")
    _send_and_log(sender, conv_id, reply)


def respond_nontext(sender: str, msg_type: str) -> None:
    """Acknowledge a non-text inbound (image/audio/etc.) — text-only for now."""
    phone = canonical(sender)
    student, _ = resolve_student(phone)
    conv_id = store.get_or_create_conversation(
        phone, student.id if student else None, "open" if student else "unverified")
    store.log_message(conv_id, "in", "user", f"[{msg_type}]")
    store.bump_usage(phone)
    _send_and_log(sender, conv_id, NONTEXT_REPLY)
