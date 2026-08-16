"""In-app AI assistant — Gemini with read-only function-calling over the gradebook.

The professor-facing cousin of SUSAI. It can only *read* (its tools go through
``ro_engine`` and only SELECT), so it can never change a grade.
"""

from __future__ import annotations

from musai.config import settings
from musai.assistant.tools import tools_for

#: ⚠️ The professor is deliberately NOT named here. This used to interpolate a real name, which
#: bought the model nothing — every tool is already scoped to the signed-in professor — and put
#: a person into a prompt that ships in a public repo. If a name is ever wanted on screen, it
#: belongs in the page, not in the system prompt.
SYSTEM = (
    "You are MUSAI's assistant for a UACH English professor. You answer two kinds of question: "
    "about their GRADEBOOK, and about MUSAI ITSELF — what it can do, how a screen works, how to "
    "perform a task. "
    # 🔴 The second rail, added 2026-08-16 with the help corpus. The existing one says never
    # invent a grade; it did not say never invent a FEATURE, and the two fail differently. A
    # wrong number is one the professor can sanity-check against her own course. A wrong
    # procedure is followed — to a button that is not there, or to something destructive
    # described as safe. So the help tools return verbatim text and this says, in the only place
    # the model reads, that verbatim text is the only thing it may answer from.
    "For any question about MUSAI or about how to do something in Moodle, you MUST call "
    "list_help_topics and then read_help_topic, and answer ONLY from the text those return, "
    "citing the topic id in brackets like [dry-run]. NEVER describe a MUSAI or Moodle procedure "
    "that is not in a topic you have just read — not from your own knowledge of Moodle, which is "
    "of a different version. If no topic covers the question, say so plainly and stop: that is a "
    "correct answer, not a failure. "
    "You have READ-ONLY access to their gradebook through the provided tools — always use them "
    "to get real numbers; never invent grades, students, or groups. "
    "Every tool is already scoped to this professor's own courses, so a group or student the "
    "tools do not find is not theirs — report that, never widen the search. "
    "Grades are on a 0-10 scale; passing is 7.0. Each group has three partials: 'Parcial 1', "
    "'Parcial 2', and 'Examen Final Ordinario'; the course total weights them 30% / 30% / 40%. "
    "'final' is the grade that uploads (after curve + extra credit); 'exact' is the raw machine "
    "grade. Be concise and concrete: lead with the answer and the key numbers, and use short "
    "lists when helpful. If something is outside the gradebook data, say so briefly. "
    "ALWAYS finish your turn with a written answer for the professor: after calling tools, "
    "summarize what you found in plain language — never end without a text reply. "
    "If the user does not name a group and only one group exists, use it automatically "
    "(call list_groups to check)."
)

#: 🔴 The language instruction, and it is deliberately not *"detect and reply in the user's
#: language"* any more. Detection is per message, so a professor working in Spanish who typed
#: one English group name got an English answer, and the answer language flickered with the
#: question. It also disagreed with the screen around it: the page can be Spanish while the
#: reply is English, and nothing in the app explains why. This follows the professor's stored
#: choice — the same one the interface follows — so there is exactly one answer to "what
#: language is MUSAI in?".
#:
#: ⚠️ The *question* may still be in either language, and must keep working: a Spanish-speaking
#: professor pasting an English activity name is normal, not an instruction to switch.
_REPLY_IN = {
    "en": "Always reply in English, whatever language the question is written in. ",
    "es": "Always reply in Spanish (Mexican, informal 'tú'), whatever language the question is "
          "written in. Keep group codes, activity names, and the names of Moodle and SEGA "
          "buttons exactly as they are — they are what the professor sees on screen. ",
}


def system_for(lang: str) -> str:
    """The system prompt for one professor, in the language they chose to read MUSAI in."""
    return SYSTEM + _REPLY_IN.get(lang, _REPLY_IN["en"])


#: 🔴 **Legacy fallback only.** Until 2026-08-16 this literal was the actor for every AI call
#: in the app, which meant every professor shared one budget and one bill: the first colleague
#: to sign in would spend the owner's daily tokens and appear in his usage. `routes_build.py`
#: was resolving `current_professor(...).email` two lines above using this constant instead.
#: Callers now pass `actor=`; this remains only so rows written before the fix still resolve
#: and so `hub_store.DEFAULT_OWNER` keeps matching something real.
ACTOR = "web:carlos"

_MESSAGES = {
    "no_key": "No Gemini API key set (GEMINI_API_KEY in MUSAI/.env). The assistant is offline.",
    "quota": "Gemini says the API quota is exhausted. Check billing/limits in the Google AI "
             "console — MUSAI will not retry automatically.",
    "auth": "Gemini rejected the API key. Check GEMINI_API_KEY in MUSAI/.env.",
    # ⚠️ `{model}` is a placeholder rather than an f-string. Interpolated at import time, the
    # sentence changes whenever `.env` changes — and since the catalogue is keyed on the English
    # sentence, editing `GEMINI_MODEL` would silently orphan its translation.
    "not_found": "The configured model was not found. Check GEMINI_MODEL in .env "
                 "(currently '{model}').",
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


def _say(key: str, lang: str) -> str:
    """One of MUSAI's own messages, in the professor's language.

    🔴 These are the sentences that appear *instead of* an answer — no key, no budget left,
    the model fell silent. They are the ones a professor most needs to understand, and they
    are the ones a translation is most likely to skip, because they live in Python rather than
    in a template. `musai/i18n/audit.py::python_strings` is what makes forgetting them a red
    test.
    """
    from musai import i18n

    return i18n.translate(_MESSAGES[key], lang, model=settings.gemini_model)


def i18n_error(reason: str, lang: str) -> str:
    """The catch-all for a failure `_MESSAGES` has no sentence for.

    Deliberately keeps the raw `reason` verbatim: it is a diagnostic token like `"safety"` or
    `"deadline"`, and translating it would make the one string that identifies the fault
    unsearchable in the logs it came from.
    """
    from musai import i18n

    return i18n.translate("Assistant error: {reason}", lang, reason=reason)


def ask(question: str, *, actor: str = ACTOR, is_admin: bool = True,
        lang: str = "en") -> dict:
    """Answer one analytics question. Returns {answer, tools, ok, usage, spend}.

    Every call is budget-checked before it spends and accounted after, through
    `musai.ai.budget` (the daily cap) and `musai.metering` (the monthly bill). Failures are
    named, and none of them are retried here — the single permitted retry lives inside
    `ai.gemini.generate`.

    `actor` is the signed-in professor's email. It defaults to the legacy key so that a
    direct call from a script or a test still books somewhere rather than crashing, but every
    route passes the real one: **the default is a fallback, not the normal path.**

    `lang` is the language the professor reads MUSAI in — it steers both the model's reply and
    MUSAI's own messages, so the answer never disagrees with the page around it.
    """
    import time

    from sqlmodel import Session

    from musai import metering
    from musai.ai import budget as bud
    from musai.ai.gemini import ASSISTANT, generate
    from musai.db import engine

    from musai.professors import by_email

    with Session(engine) as sess:
        allowed, why = bud.check(sess, actor, is_admin=is_admin)
        if allowed:
            allowed, why = metering.check(sess, actor, is_admin=is_admin)
        sess.commit()
        if not allowed:
            return {"answer": _say(why, lang), "tools": [], "ok": False,
                    "usage": bud.summary(sess, actor, is_admin=is_admin),
                    "spend": metering.month_to_date(sess, actor, is_admin=is_admin)}
        # 🔴 Whose data may this turn read? Resolved here, once, from the same string the call
        # is billed to — so the budget and the scope can never name two different people.
        # An actor with no `Professor` row (the legacy `web:carlos`, a script, a typo) resolves
        # to `None`, and `tools_for(None)` finds nothing. **Failing towards an empty answer is
        # a bug report; failing the other way is a data breach.**
        me = by_email(sess, actor)
        professor_id = me.id if me else None

    t0 = time.monotonic()
    result = generate(system=system_for(lang), contents=question,
                      tools=tools_for(professor_id), profile=ASSISTANT)
    elapsed = time.monotonic() - t0

    with Session(engine) as sess:
        bud.record(sess, actor, result)
        # Billed even when the answer was empty: the tokens were spent either way, and a
        # ledger that only records successes understates exactly the runs worth noticing.
        #
        # ⭐ `detail` carries the TOOL NAMES, not the question. It is the only record of what
        # professors actually use the assistant for, and it cannot be reconstructed later — a
        # question not classified when it was asked is gone. Tool names are the honest way to
        # get that: `student_status` says someone looked a student up without keeping a word
        # of what they typed, so the admin panel can answer "what is this app used for?"
        # without anyone's questions being readable. Storing the question text would be the
        # easy version and the wrong one.
        metering.record(sess, actor, "assistant", seconds=elapsed,
                        detail=",".join(result.tools),
                        tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                        model=result.model or "")
        sess.commit()
        usage = bud.summary(sess, actor, is_admin=is_admin)
        spend = metering.month_to_date(sess, actor, is_admin=is_admin)

    if result.ok:
        return {"answer": result.text, "tools": result.tools, "ok": True,
                "usage": usage, "spend": spend}

    reason = result.reason or "error"
    answer = (_say(reason, lang) if reason in _MESSAGES
              else i18n_error(reason, lang))

    # A turn that called tools and then fell silent still HAS the data — the model just did not
    # write the sentence. Showing what the tools returned turns a dead end into an answer the
    # professor can act on ("No student matching 'OMAR …'" tells them the roster is not
    # imported); hiding it behind a generic apology is how a working lookup reads as a bug.
    if reason == "empty" and result.last_tool_result:
        answer = f"{result.last_tool_result}\n\n({answer})"

    # An empty answer still cost tokens, so surface it as a soft failure, not a hard one.
    return {"answer": answer, "tools": result.tools, "ok": reason == "empty",
            "usage": usage, "spend": spend}
