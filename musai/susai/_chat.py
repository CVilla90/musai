"""Shared Gemini chat helper for SUSAI — conversation history + the guarded call.

All SDK details, cost caps and the retry policy live in `musai.ai.gemini`; this module only
shapes SUSAI's conversation history into the SDK's turn format. Used by both the student
agent and the professor/coordinator agent.

Historical note: this file used to end with ``text = _run() or _run()`` — an unconditional
SECOND full call (tool round-trips included) whenever the model returned no text, which
silently doubled the cost of that turn. The retry is now explicit, counted, and capped in
`ai.gemini.generate`.
"""

from musai.ai.gemini import SUSAI_STUDENT, AiResult, generate


def chat(question: str, history, system: str, tools, profile=SUSAI_STUDENT) -> dict:
    """Run one guarded Gemini turn.

    `history` = oldest→newest [{direction, body}] (or None → just the question).
    Returns {answer, ok, reason?, result} where `result` is the AiResult carrying token
    counts, so the caller can book the spend. The caller is responsible for any tool-scoping
    context (e.g. the student ContextVar) being set around this call.
    """
    try:
        from google.genai import types
    except Exception as e:
        return {"answer": "", "ok": False, "reason": f"sdk:{e}", "result": AiResult(ok=False)}

    # Gemini wants the turn sequence to start with a user message.
    hist = list(history or [])
    while hist and hist[0]["direction"] != "in":
        hist = hist[1:]

    if hist:
        contents = [
            types.Content(
                role=("user" if h["direction"] == "in" else "model"),
                parts=[types.Part(text=h["body"])],
            )
            for h in hist
        ]
    else:
        contents = question

    result = generate(system=system, contents=contents, tools=tools, profile=profile)
    if result.ok:
        return {"answer": result.text, "ok": True, "result": result}
    return {"answer": "", "ok": False, "reason": result.reason or "error", "result": result}
