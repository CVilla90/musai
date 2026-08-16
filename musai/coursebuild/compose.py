"""Natural language -> a validated content block. The only AI step in the builder.

The model chooses WHAT to say. It does not choose markup, colours (it picks a palette
*name*, we own the hex), or where the block goes (that's a dropdown). Output is constrained
by a response schema, so "the model returned broken JSON" is not a failure mode we have to
defend against — the API constrains decoding.

Cheap by design: ~60 output tokens per attempt, on flash-lite. Iterating wording ten times
costs a fraction of a cent, which is what makes preview-before-publish affordable.
"""

import json
from typing import Optional

from musai.ai.gemini import SUSAI_STUDENT, AiResult, Profile, generate
from musai.coursebuild.render import BLOCK_TYPES, PALETTES, render_checked

# Small envelope: this is a short structured emission, not an essay. Reusing the student
# profile's tight caps rather than the analyst's roomier ones.
COMPOSE = Profile(
    "coursebuild_compose",
    max_remote_calls=0,      # no tools — pure generation
    max_output_tokens=500,
    timeout_s=30,
    temperature=0.7,         # some variety is desirable for copy
    retry_on_empty=True,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": BLOCK_TYPES},
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "body": {"type": "string"},
        "emoji": {"type": "string"},
        "accent": {"type": "string", "enum": sorted(PALETTES)},
        "items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type", "title", "accent"],
}

SYSTEM = (
    "You write short course content for a university Moodle page, for Professor the owner "
    "the professor's English courses at UACH (Universidad Autónoma de Chihuahua). You return ONLY a "
    "content block as JSON — never HTML, never CSS, never markdown. "
    "'banner' is a prominent welcome/announcement box; 'notice' is a single compact line. "
    "Keep `title` under 60 characters and `subtitle` under 80. `body` is optional and at most "
    "two short sentences. `items` is optional, at most 4 short bullet points — use it only if "
    "the request implies a list. `emoji` is at most one emoji, or omitted. "
    "`accent` picks a colour mood: amber = warm/welcome, indigo = neutral/official, "
    "teal = positive/success, rose = urgent/deadline, slate = quiet/administrative. "
    "Write in the SAME language the professor used in their request. Match a university "
    "register: warm but professional, never salesy, no exclamation-mark spam."
)


def compose(request: str, *, course_label: str = "", extra_context: str = "") -> dict:
    """Turn a professor's sentence into {block, html, ok, reason, result}.

    `result` is the AiResult, so the caller books the spend against the actor's budget.
    """
    prompt = request.strip()
    if course_label:
        prompt = f"Course/group: {course_label}\n\nRequest: {prompt}"
    if extra_context:
        prompt = f"{prompt}\n\nContext the professor supplied: {extra_context}"

    result: AiResult = generate(
        system=SYSTEM, contents=prompt, profile=COMPOSE, response_schema=SCHEMA
    )
    if not result.ok:
        return {"ok": False, "reason": result.reason or "error", "block": None,
                "html": "", "result": result}

    try:
        block = json.loads(result.text)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "bad_json", "block": None, "html": "",
                "result": result}

    if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
        return {"ok": False, "reason": "bad_block", "block": block, "html": "",
                "result": result}

    try:
        html = render_checked(block)
    except ValueError as e:
        # The renderer refused — a bug in OUR renderer, not in the model's words.
        return {"ok": False, "reason": f"unsafe_html:{e}", "block": block, "html": "",
                "result": result}

    return {"ok": True, "reason": None, "block": block, "html": html, "result": result}


def compose_or_none(request: str, **kw) -> Optional[dict]:
    out = compose(request, **kw)
    return out if out["ok"] else None
