"""Block JSON -> Moodle-safe HTML.

Constraints this file exists to enforce (this Moodle strips anything else):

  * **Inline `style=` only.** No <style> blocks, no classes, no external CSS.
  * **No JavaScript**, no <script>, no event attributes.
  * **Table-free layout.** Flexbox/grid degrade unpredictably inside Moodle's own CSS;
    simple block elements with padding survive everywhere.
  * **All text escaped.** The model's output is untrusted input, not markup.

Every renderer takes a validated dict and returns a string. Adding a block type means
adding a function here and an entry in RENDERERS — never loosening the escaping.
"""

import html
import re
from typing import Any

# A stable marker so MUSAI can find a block it created and UPDATE it rather than
# appending a duplicate on a re-run. Survives losing MUSAI's DB, because it lives in the
# content itself. Invisible to students.
MARKER_PREFIX = "musai:block:"


def marker(block_id: str) -> str:
    return f"<!-- {MARKER_PREFIX}{_slug(block_id)} -->"


def find_marker(html_text: str) -> str | None:
    m = re.search(rf"<!--\s*{re.escape(MARKER_PREFIX)}([a-z0-9\-]+)\s*-->", html_text or "")
    return m.group(1) if m else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9\-]+", "-", (s or "").lower()).strip("-")[:60] or "block"


def _esc(s: Any) -> str:
    """Escape untrusted text. The model's words are content, never markup."""
    return html.escape(str(s or ""), quote=True)


# Palettes are ours, not the model's — it picks a NAME, we pick the hex. That keeps output
# on-brand and stops "creative" colour choices with unreadable contrast.
PALETTES = {
    "amber":  ("#FFF8E7", "#F59E0B", "#78350F", "#92400E"),
    "indigo": ("#EEF2FF", "#4F46E5", "#1E1B4B", "#3730A3"),
    "teal":   ("#ECFDF5", "#14B8A6", "#134E4A", "#0F766E"),
    "rose":   ("#FFF1F2", "#F43F5E", "#4C0519", "#9F1239"),
    "slate":  ("#F8FAFC", "#64748B", "#0F172A", "#334155"),
}
DEFAULT_PALETTE = "indigo"


def palette(name: str) -> tuple[str, str, str, str]:
    """(background, accent, ink, subtle_ink) for a palette name."""
    return PALETTES.get((name or "").lower(), PALETTES[DEFAULT_PALETTE])


def banner(block: dict) -> str:
    """A course banner / welcome label.

    Fields: title, subtitle?, body?, accent?, emoji?, items?[str]
    """
    bg, accent, ink, subtle = palette(block.get("accent"))
    title = _esc(block.get("title"))
    subtitle = _esc(block.get("subtitle"))
    body = _esc(block.get("body"))
    emoji = _esc(block.get("emoji"))
    items = [i for i in (block.get("items") or []) if str(i).strip()][:6]

    parts = [
        marker(block.get("id") or title),
        f'<div style="background:{bg};border-left:6px solid {accent};'
        f'border-radius:10px;padding:20px 22px;margin:6px 0;'
        f'font-family:Arial,Helvetica,sans-serif;line-height:1.5">',
    ]
    heading = f"{emoji} {title}".strip() if emoji else title
    parts.append(
        f'<div style="font-size:22px;font-weight:700;color:{ink};margin:0 0 4px 0">'
        f"{heading}</div>"
    )
    if subtitle:
        parts.append(
            f'<div style="font-size:15px;font-weight:600;color:{subtle};margin:0 0 10px 0">'
            f"{subtitle}</div>"
        )
    if body:
        parts.append(
            f'<div style="font-size:14px;color:{ink};margin:0 0 8px 0">{body}</div>'
        )
    if items:
        parts.append(f'<ul style="margin:8px 0 0 0;padding-left:20px;color:{ink};font-size:14px">')
        for it in items:
            parts.append(f'<li style="margin:3px 0">{_esc(it)}</li>')
        parts.append("</ul>")
    parts.append("</div>")
    return "".join(parts)


def notice(block: dict) -> str:
    """A compact one-line notice — same contract, smaller footprint."""
    bg, accent, ink, _ = palette(block.get("accent"))
    emoji = _esc(block.get("emoji"))
    text = _esc(block.get("title") or block.get("body"))
    label = f"{emoji} {text}".strip() if emoji else text
    return (
        f'{marker(block.get("id") or text)}'
        f'<div style="background:{bg};border-left:4px solid {accent};border-radius:8px;'
        f'padding:12px 16px;margin:6px 0;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;color:{ink}">{label}</div>'
    )


RENDERERS = {"banner": banner, "notice": notice}
BLOCK_TYPES = sorted(RENDERERS)


def render(block: dict) -> str:
    kind = (block or {}).get("type")
    fn = RENDERERS.get(kind)
    if not fn:
        raise ValueError(f"Unknown block type {kind!r}. Known: {BLOCK_TYPES}")
    return fn(block)


# ── the Moodle-safety lint ────────────────────────────────────────────────────
# 🔴 Checked against the WHOLE document, escaped text included, and that is deliberate — see
# `test_lint_fails_closed_on_suspicious_escaped_text`. This lint guards SHORT generated
# banners, where nobody legitimately writes " onclick=" and failing closed keeps it a blunt,
# trustworthy instrument.
#
# ⚠️ Do not "fix" the false positive here. It IS a real false positive — ` one = ` and
# ` only = ` both match `\son\w+\s*=` — but the artifact that trips it is long hand-authored
# prose, and that artifact has its own gate: `book.lint_chapter_html`, which searches tag
# interiors only for exactly this reason (First Term chapter 11, *"Near + one = this"*,
# 2026-08-10). Two trust levels, two lints; loosening this one collapses them into one.
_FORBIDDEN = (
    (re.compile(r"<script", re.I), "contains <script>"),
    (re.compile(r"<style", re.I), "contains <style> (this Moodle only keeps inline style=)"),
    (re.compile(r"\son\w+\s*=", re.I), "contains an inline event handler (onclick=…)"),
    (re.compile(r"javascript:", re.I), "contains a javascript: URL"),
    (re.compile(r"<iframe", re.I), "contains <iframe> (unverified against this sanitizer)"),
    (re.compile(r'class\s*=\s*"', re.I), "uses class= (theme CSS is not guaranteed present)"),
)


def lint(html_text: str) -> list[str]:
    """Problems that would get stripped or misbehave in Moodle. Empty list = good."""
    return [why for rx, why in _FORBIDDEN if rx.search(html_text or "")]


def render_checked(block: dict) -> str:
    """Render and refuse to return anything the sanitizer would maul."""
    out = render(block)
    problems = lint(out)
    if problems:
        raise ValueError("Rendered HTML is not Moodle-safe: " + "; ".join(problems))
    return out
