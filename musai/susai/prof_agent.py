"""SUSAI professor/coordinator brain — the WhatsApp cousin of the in-app assistant.

When the owner (the admin/coordinator) texts SUSAI, he gets READ-ONLY analytics over his own
course groups, reusing the assistant's professor-facing tools.

🔴 **"Single-tenant for now: all courses in the DB are his" stopped being true on 2026-08-14**,
and this docstring went on saying it. The tools it hands out were unscoped, so a colleague's
groups and roster were one WhatsApp message away — from a number check that is correct, over
data that was not. `_tools()` now resolves the owner's `Professor` row and binds the set to it.

The identity is `settings.owner_email`, not the phone: `susai_admin_phone` proves *who is
texting*, `admin_email` is *whose rows those are*. If that row does not exist yet — a fresh
database, before the owner's first sign-in — the tools find nothing, which is the right
failure. See `musai/assistant/tools.py`.
"""

from musai.ai.gemini import SUSAI_ADMIN
from musai.assistant.tools import tools_for
from musai.susai._chat import chat

SYSTEM = (
    "You are SUSAI 🐝, the WhatsApp assistant for Professor the owner — you are talking to HIM "
    "(the coordinator), not a student. You have READ-ONLY access to all of his course groups "
    "through the tools (list_groups, group_status, partial_trend, student_status, at_risk). "
    "Always use them for real numbers — never invent grades, students, or groups. Grades are "
    "0-10 (passing 7.0); each group has 'Parcial 1', 'Parcial 2' and 'Examen Final Ordinario', "
    "and the course total weights them 30/30/40. 'final' is the grade that counts (after curve + "
    "extra credit); 'exact' is the raw machine grade. A grade returned by a tool is real and "
    "already recorded — state it plainly, no 'so far' hedging. Be concise and concrete: lead "
    "with the answer and the key numbers, short lists when useful, an occasional emoji 🙂. ALWAYS "
    "reply in the SAME language as his most recent message — Spanish if he writes in Spanish, "
    "English if he writes in English. The messages above are your ongoing chat — use that "
    "context. You can NEVER modify grades. ALWAYS finish with a written reply — never end on a "
    "silent tool call."
)


def _tools():
    """The owner's read-only tool set, resolved per message rather than at import.

    At import time there may be no database yet (SUSAI starts in its own process), and a tool
    set frozen at startup would keep answering from whoever the owner was when the module
    loaded. Two SELECTs on the read-only engine per message is not the cost worth optimising.
    """
    from sqlmodel import Session

    from musai.config import settings
    from musai.db import ro_engine
    from musai.professors import by_email

    with Session(ro_engine) as sess:
        me = by_email(sess, settings.owner_email)
        return tools_for(me.id if me else None)


def answer(question: str, history: list[dict] | None = None) -> dict:
    """Answer one coordinator question with read-only analytics over all of his courses."""
    return chat(question, history, SYSTEM, _tools(), profile=SUSAI_ADMIN)
