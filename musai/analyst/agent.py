"""In-app AI analyst — Gemini with read-only function-calling over the gradebook.

The professor-facing cousin of SUSAI. It can only *read* (its tools go through
``ro_engine`` and only SELECT), so it can never change a grade.
"""

from __future__ import annotations

from musai.config import settings
from musai.analyst.tools import TOOLS

#: ⚠️ The professor is deliberately NOT named here. This used to interpolate a real name, which
#: bought the model nothing — every tool is already scoped to the signed-in professor — and put
#: a person into a prompt that ships in a public repo. If a name is ever wanted on screen, it
#: belongs in the page, not in the system prompt.
SYSTEM = (
    "You are MUSAI's analytics assistant for a UACH English professor. "
    "You have READ-ONLY access to their gradebook through the provided tools — always use them "
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


#: 🔴 **Legacy fallback only.** Until 2026-08-16 this literal was the actor for every AI call
#: in the app, which meant every professor shared one budget and one bill: the first colleague
#: to sign in would spend the owner's daily tokens and appear in his usage. `routes_build.py`
#: was resolving `current_professor(...).email` two lines above using this constant instead.
#: Callers now pass `actor=`; this remains only so rows written before the fix still resolve
#: and so `hub_store.DEFAULT_OWNER` keeps matching something real.
ACTOR = "web:carlos"

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
    # 🔴 Do NOT tell the professor to rephrase. Measured 2026-08-16: on "which group is
    # <name> in?" for a student who is not in the database, the tools had ALREADY returned
    # `{"error": "No student matching …"}` — the complete answer — and only the summary was
    # missing. "Try rephrasing" pointed at the one action that could never work. When there is
    # a tool result to show, `ask()` shows it instead of this line.
    "empty": "I pulled the data but couldn't summarise it. The raw lookup is above; "
             "rephrasing usually will not help — check that the data is imported.",
    "daily_tokens": "Today's AI token budget for this account is used up. It resets tomorrow.",
    "daily_requests": "Today's AI request budget for this account is used up. It resets "
                      "tomorrow.",
    "monthly_allowance": "This month's free MUSAI usage is used up. It resets on the 1st — "
                         "see Settings ▸ Usage for where it went.",
}


def ask(question: str, *, actor: str = ACTOR, is_admin: bool = True) -> dict:
    """Answer one analytics question. Returns {answer, tools, ok, usage, spend}.

    Every call is budget-checked before it spends and accounted after, through
    `musai.ai.budget` (the daily cap) and `musai.metering` (the monthly bill). Failures are
    named, and none of them are retried here — the single permitted retry lives inside
    `ai.gemini.generate`.

    `actor` is the signed-in professor's email. It defaults to the legacy key so that a
    direct call from a script or a test still books somewhere rather than crashing, but every
    route passes the real one: **the default is a fallback, not the normal path.**
    """
    import time

    from sqlmodel import Session

    from musai import metering
    from musai.ai import budget as bud
    from musai.ai.gemini import ANALYST, generate
    from musai.db import engine

    with Session(engine) as sess:
        allowed, why = bud.check(sess, actor, is_admin=is_admin)
        if allowed:
            allowed, why = metering.check(sess, actor, is_admin=is_admin)
        sess.commit()
        if not allowed:
            return {"answer": _MESSAGES[why], "tools": [], "ok": False,
                    "usage": bud.summary(sess, actor, is_admin=is_admin),
                    "spend": metering.month_to_date(sess, actor, is_admin=is_admin)}

    t0 = time.monotonic()
    result = generate(system=SYSTEM, contents=question, tools=TOOLS, profile=ANALYST)
    elapsed = time.monotonic() - t0

    with Session(engine) as sess:
        bud.record(sess, actor, result)
        # Billed even when the answer was empty: the tokens were spent either way, and a
        # ledger that only records successes understates exactly the runs worth noticing.
        metering.record(sess, actor, "analyst", seconds=elapsed,
                        tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                        model=result.model or "")
        sess.commit()
        usage = bud.summary(sess, actor, is_admin=is_admin)
        spend = metering.month_to_date(sess, actor, is_admin=is_admin)

    if result.ok:
        return {"answer": result.text, "tools": result.tools, "ok": True,
                "usage": usage, "spend": spend}

    reason = result.reason or "error"
    answer = _MESSAGES.get(reason, f"Analyst error: {reason}")

    # A turn that called tools and then fell silent still HAS the data — the model just did not
    # write the sentence. Showing what the tools returned turns a dead end into an answer the
    # professor can act on ("No student matching 'OMAR …'" tells them the roster is not
    # imported); hiding it behind a generic apology is how a working lookup reads as a bug.
    if reason == "empty" and result.last_tool_result:
        answer = f"{result.last_tool_result}\n\n({answer})"

    # An empty answer still cost tokens, so surface it as a soft failure, not a hard one.
    return {"answer": answer, "tools": result.tools, "ok": reason == "empty",
            "usage": usage, "spend": spend}
