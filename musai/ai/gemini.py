"""The single Gemini chokepoint — model, hard limits, failure policy, cost accounting.

The API key is PAID, so an unbounded agent loop costs real money rather than just time.
Every knob that can run up a bill is pinned here instead of at the call sites:

  * **Tool-call fan-out** — ``automatic_function_calling.maximum_remote_calls``. This is the
    big one: with automatic function calling, ONE user question can become many billed
    round-trips. Uncapped, a confused model (or a tool that keeps returning "not found")
    loops until the SDK's own default stops it.
  * **Output length** — ``max_output_tokens``. Output tokens cost more than input.
  * **Thinking** — ``thinking_level="LOW"`` on 3.x. We are doing lookups and short summaries,
    not proofs; high thinking is billed reasoning we don't need.
  * **Wall clock** — ``http_options.timeout``. A hung call must fail, not hang a WhatsApp
    webhook or a cockpit request.
  * **Retries** — explicit and countable. See RETRY POLICY.

RETRY POLICY (the owner's rule: clear re-attempt numbers, clear fail status, no blind retry)

  * **Terminal → never retried.** Bad key, bad request, bad model name, quota exhausted.
    Retrying these burns money or is guaranteed to fail again. Fails immediately with a
    named reason.
  * **Transient → at most ONE retry.** Server-side 500/503/UNAVAILABLE only.
  * **Empty text → at most ONE retry.** The old `_chat.py` did ``_run() or _run()``, an
    unconditional second FULL call (tool calls and all) whenever text came back blank —
    silently doubling the cost of that turn. Now it is one retry, counted, and only when the
    first attempt produced no text.

Never more than 2 attempts, ever. `AiResult.calls` reports what was actually billed.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from musai.config import settings

# ── failure classification ────────────────────────────────────────────────────
# Substrings matched against the exception text. Terminal wins over transient.
_TERMINAL = {
    "quota": ("RESOURCE_EXHAUSTED", "429"),
    "bad_request": ("INVALID_ARGUMENT", "400"),
    "auth": ("PERMISSION_DENIED", "UNAUTHENTICATED", "API key not valid", "401", "403"),
    "not_found": ("NOT_FOUND", "404", "is not found for API version"),
}
_TRANSIENT = ("UNAVAILABLE", "503", "500", "INTERNAL", "DEADLINE_EXCEEDED", "timeout")


def classify(err: str) -> tuple[str, bool]:
    """(reason, retryable) for an exception message."""
    for reason, needles in _TERMINAL.items():
        if any(n.lower() in err.lower() for n in needles):
            return reason, False
    if any(n.lower() in err.lower() for n in _TRANSIENT):
        return "transient", True
    return "error", False  # unknown → do NOT retry; unknown failures can be expensive


@dataclass(frozen=True)
class Profile:
    """A named cost envelope. Call sites pick a profile; they don't pick raw limits.

    ``model=None`` means "use settings.gemini_model". Only override it where the task
    genuinely needs a stronger model — every override is a standing bill.
    """
    name: str
    max_remote_calls: int      # cap on tool-call round trips within one request
    max_output_tokens: int
    timeout_s: int
    temperature: float = 0.2
    thinking_level: str = "LOW"
    retry_on_empty: bool = True
    model: Optional[str] = None


# Model choice. MUSAI's assistant work is structured DB lookups plus a two-sentence summary —
# no hard reasoning — so flash-lite is the right default, and that was re-tested rather than
# assumed when gemini-3.7-flash shipped on 2026-08-13.
#
# 🔴 **BENCHMARKED 2026-08-16 ON THE REAL ASSISTANT WORKLOAD** (same system prompt, same tools,
# the professor's own questions; 6 runs per config). Upgrading the model was strictly WORSE:
#
#   gemini-3.5-flash-lite   6/6 answered    2.7 s/q   1544 tok/q   ← stays the default
#   gemini-3.6-flash        4/6 answered   12.2 s/q   2338 tok/q
#   gemini-3.7-flash        2/6 answered   30.5 s/q    709 tok/q   ← two transient 5xx
#
# ⚠️ **There is no 3.7 Flash-Lite.** The newest Lite tier is 3.5-flash-lite; Lite lags the
# Flash line by roughly two releases, so "the newest model" and "the newest cheap model" are
# different questions. And a model three days old is capacity-constrained: 3.7's failures were
# server-side transients at 30–68 s, the same shape as the frozen computer-use demo, not a
# reasoning limit. ⭐ Re-benchmark it in a month; do not adopt a model on its launch post.
FLASH = "gemini-3.6-flash"

# USD per 1M tokens (input, output), paid tier, checked 2026-08-16 against
# https://ai.google.dev/gemini-api/docs/pricing. Prices move — `settings.gemini_price_*`
# override for any model not listed here.
#
# 🔴 **3.6 and 3.7 Flash carry an INTRODUCTORY price that expires 2026-12-31**, after which
# both double to (1.50, 7.50). The numbers below are the introductory ones, so any estimate
# made with them **understates the January bill by 2x**. A price table is a cache of a pricing
# page, with no invalidation — re-read the page before quoting a cost, and especially before
# adopting one of these two on the strength of being cheap.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.7-flash": (0.75, 3.75),        # → (1.50, 7.50) on 2027-01-01
    "gemini-3.6-flash": (0.75, 3.75),        # → (1.50, 7.50) on 2027-01-01
    "gemini-3.5-flash": (1.50, 9.00),        # never use: dearer than 3.7 AND older
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


def prices_for(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens for `model`, falling back to configured values."""
    if model in PRICES:
        return PRICES[model]
    return settings.gemini_price_in_per_mtok, settings.gemini_price_out_per_mtok

# Tuned for what each surface actually needs. Deliberately tight — raise them on evidence,
# not on a hunch.
ASSISTANT = Profile("assistant", max_remote_calls=6, max_output_tokens=1200, timeout_s=45)
SUSAI_STUDENT = Profile(
    "susai_student", max_remote_calls=3, max_output_tokens=400, timeout_s=30, temperature=0.3
)
SUSAI_ADMIN = Profile(
    "susai_admin", max_remote_calls=6, max_output_tokens=800, timeout_s=40, temperature=0.3
)


@dataclass
class AiResult:
    ok: bool
    text: str = ""
    reason: Optional[str] = None
    tools: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0          # billed attempts actually made
    model: str = ""
    #: The last value a tool actually returned this turn, as text. Carried ONLY so that a turn
    #: which ends with no summary can still show the professor what was retrieved: the tools
    #: had the answer, and the model simply failed to say it. Never shown on a successful turn.
    last_tool_result: str = ""

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    def estimated_usd(self) -> Optional[float]:
        """Cost estimate, or None if this model has no known price (we don't guess money)."""
        pin, pout = prices_for(self.model)
        if pin <= 0 and pout <= 0:
            return None
        return (self.tokens_in / 1_000_000) * pin + (self.tokens_out / 1_000_000) * pout


def _tool_names(resp) -> list[str]:
    names: list[str] = []
    try:
        for content in (resp.automatic_function_calling_history or []):
            for part in (getattr(content, "parts", None) or []):
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    names.append(fc.name)
    except Exception:
        pass
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _last_tool_result(resp) -> str:
    """The last function RESPONSE in the auto-calling history, flattened to short text.

    🔴 Exists because of a real, misleading failure. When the turn ends with no text part the
    professor was told *"I pulled the data but didn't form a summary — try rephrasing the
    question."* On a lookup for a student who is not in the database, the tool had already
    returned `{"error": "No student matching 'OMAR …'"}` — a complete and actionable answer —
    and rephrasing could never have produced anything else. The generic message did not just
    fail to help, it pointed at the one action guaranteed to waste the professor's time.
    """
    try:
        for content in reversed(resp.automatic_function_calling_history or []):
            for part in reversed(getattr(content, "parts", None) or []):
                fr = getattr(part, "function_response", None)
                if fr is None:
                    continue
                payload = getattr(fr, "response", None)
                if payload is None:
                    continue
                text = str(payload)
                return text[:400] + ("…" if len(text) > 400 else "")
    except Exception:
        pass
    return ""


def _usage(resp) -> tuple[int, int]:
    """(input_tokens, output_tokens) from the response, 0s if unavailable."""
    um = getattr(resp, "usage_metadata", None)
    if not um:
        return 0, 0
    tin = getattr(um, "prompt_token_count", 0) or 0
    tout = (getattr(um, "candidates_token_count", 0) or 0) + \
           (getattr(um, "thoughts_token_count", 0) or 0)
    return int(tin), int(tout)


def generate(
    *,
    system: str,
    contents: Any,
    tools: Optional[Sequence[Callable]] = None,
    profile: Profile = ASSISTANT,
    response_schema: Optional[Any] = None,
) -> AiResult:
    """One guarded Gemini turn. Never raises; always returns an AiResult.

    Cost is bounded by construction: at most 2 attempts, each capped at
    ``profile.max_remote_calls`` tool round-trips and ``profile.max_output_tokens`` output.
    """
    if not settings.gemini_api_key:
        return AiResult(ok=False, reason="no_key")
    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        return AiResult(ok=False, reason=f"sdk:{e}")

    model = profile.model or settings.gemini_model
    cfg_kwargs: dict = dict(
        system_instruction=system,
        temperature=profile.temperature,
        max_output_tokens=profile.max_output_tokens,
        http_options=types.HttpOptions(timeout=profile.timeout_s * 1000),
    )
    if tools:
        cfg_kwargs["tools"] = list(tools)
        cfg_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
            maximum_remote_calls=profile.max_remote_calls
        )
    if response_schema is not None:
        # Structured output: the API constrains decoding to the schema, so malformed JSON
        # stops being a failure mode we have to parse defensively around.
        cfg_kwargs["response_mime_type"] = "application/json"
        cfg_kwargs["response_schema"] = response_schema
    # thinking_level is a 3.x feature; on an older model the SDK/API rejects it, so fall
    # back rather than hard-fail (a wrong GEMINI_MODEL shouldn't take the assistant down).
    try:
        cfg_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=profile.thinking_level
        )
        cfg = types.GenerateContentConfig(**cfg_kwargs)
    except Exception:
        cfg_kwargs.pop("thinking_config", None)
        cfg = types.GenerateContentConfig(**cfg_kwargs)

    client = genai.Client(api_key=settings.gemini_api_key)
    result = AiResult(ok=False, model=model)
    attempted_empty_retry = False

    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=cfg)
        except Exception as e:
            reason, retryable = classify(str(e))
            result.calls += 1
            result.reason = reason
            if retryable and attempt == 1:
                continue
            return result

        result.calls += 1
        tin, tout = _usage(resp)
        result.tokens_in += tin
        result.tokens_out += tout
        result.tools = _tool_names(resp) or result.tools
        result.last_tool_result = _last_tool_result(resp) or result.last_tool_result
        text = (getattr(resp, "text", None) or "").strip()

        if text:
            result.ok = True
            result.text = text
            result.reason = None
            return result

        # Empty text: one retry at most, and only if the profile allows it.
        if profile.retry_on_empty and not attempted_empty_retry and attempt == 1:
            attempted_empty_retry = True
            continue
        result.reason = "empty"
        return result

    return result
