"""In-app AI analyst — Gemini with read-only function-calling over the gradebook.

The professor-facing cousin of SUSAI. It can only *read* (its tools go through
``ro_engine`` and only SELECT), so it can never change a grade.
"""

from __future__ import annotations

from musai.config import settings
from musai.analyst.tools import TOOLS

SYSTEM = (
    "You are MUSAI's analytics assistant for Professor the owner (UACH English courses). "
    "You have READ-ONLY access to his gradebook through the provided tools — always use them "
    "to get real numbers; never invent grades, students, or groups. "
    "Grades are on a 0-10 scale; passing is 7.0. Each group has three partials: 'Parcial 1', "
    "'Parcial 2', and 'Examen Final Ordinario'; the course total weights them 30% / 30% / 40%. "
    "'final' is the grade that uploads (after curve + extra credit); 'exact' is the raw machine "
    "grade. Be concise and concrete: lead with the answer and the key numbers, and use short "
    "lists when helpful. If something is outside the gradebook data, say so briefly. "
    "Detect and reply in the user's language (English or Spanish). "
    "ALWAYS finish your turn with a written answer for the professor: after calling tools, "
    "summarize what you found in plain language — never end without a text reply. "
    "If the user does not name a group and only one group exists, use it automatically "
    "(call list_groups to check)."
)


ACTOR = "web:carlos"  # single-user cockpit today; becomes the signed-in professor later

_MESSAGES = {
    "no_key": "No Gemini API key set (GEMINI_API_KEY in MUSAI/.env). The analyst is offline.",
    "quota": "Gemini says the API quota is exhausted. Check billing/limits in the Google AI "
             "console — MUSAI will not retry automatically.",
    "auth": "Gemini rejected the API key. Check GEMINI_API_KEY in MUSAI/.env.",
    "not_found": f"The configured model was not found. Check GEMINI_MODEL in .env "
                 f"(currently '{settings.gemini_model}').",
    "bad_request": "Gemini rejected the request as malformed — this is a MUSAI bug, not a "
                   "usage problem. Check the logs.",
    "transient": "Gemini had a server-side error and the single retry also failed. Try again "
                 "in a moment.",
    "empty": "I pulled the data but didn't form a summary — try rephrasing the question.",
    "daily_tokens": "Today's AI token budget for this account is used up. It resets tomorrow.",
    "daily_requests": "Today's AI request budget for this account is used up. It resets "
                      "tomorrow.",
}


def ask(question: str) -> dict:
    """Answer one analytics question. Returns {answer, tools, ok, usage}.

    Every call is budget-checked before it spends and accounted after, through
    `musai.ai.budget`. Failures are named, and none of them are retried here — the single
    permitted retry lives inside `ai.gemini.generate`.
    """
    from sqlmodel import Session

    from musai.ai import budget as bud
    from musai.ai.gemini import ANALYST, generate
    from musai.db import engine

    with Session(engine) as sess:
        allowed, why = bud.check(sess, ACTOR, is_admin=True)
        sess.commit()
        if not allowed:
            return {"answer": _MESSAGES[why], "tools": [], "ok": False,
                    "usage": bud.summary(sess, ACTOR, is_admin=True)}

    result = generate(system=SYSTEM, contents=question, tools=TOOLS, profile=ANALYST)

    with Session(engine) as sess:
        bud.record(sess, ACTOR, result)
        sess.commit()
        usage = bud.summary(sess, ACTOR, is_admin=True)

    if result.ok:
        return {"answer": result.text, "tools": result.tools, "ok": True, "usage": usage}

    reason = result.reason or "error"
    answer = _MESSAGES.get(reason, f"Analyst error: {reason}")
    # An empty answer still cost tokens, so surface it as a soft failure, not a hard one.
    return {"answer": answer, "tools": result.tools, "ok": reason == "empty", "usage": usage}
