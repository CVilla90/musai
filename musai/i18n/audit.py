"""Find every sentence a professor can read, and whether it can be read in Spanish.

Two questions, and the second one is the one that keeps the feature honest:

* **`calls()`** — which strings does a template hand to `t()`? Feeds the catalogue-completeness
  check: a call site with no Spanish entry is a red test, not a paragraph of English in the
  middle of a Spanish page.
* **`stray()`** — which visible text is *not* wrapped in `t()` at all? This is the one that
  matters. Without it, "MUSAI is translated" degrades quietly the first time someone adds a
  button, and nobody finds out until a colleague is looking at it.

⭐ Same doctrine as `tests/test_help_docs.py`: **enumerate from the system, never from your own
inventory.** A checklist of "templates I translated" is a cache of the templates and goes stale
the day someone adds one. This walks the template directory.

⚠️ It is a heuristic — HTML is not parsed, it is stripped. The bias is deliberately toward
false positives: a string it wrongly flags costs one line in `NEUTRAL`, a string it wrongly
clears is a Spanish page with an English sentence in it that nobody is looking for.

Run it as a worklist:

    python -m musai.i18n.audit            # what is still untranslated, by template
    python -m musai.i18n.audit --missing  # call sites with no Spanish yet
"""

from __future__ import annotations

import re
from pathlib import Path

from musai import i18n

TEMPLATES = Path(__file__).resolve().parents[1] / "web" / "templates"

#: `{{ t("…") }}` and `{{ t('…', n=1) }}`, including strings that wrap across lines — Jinja's
#: own string lexer is `re.S`, so a paragraph may be written the way it reads.
_CALL = re.compile(r"""\bt\(\s*(?P<q>['"])(?P<text>.*?)(?<!\\)(?P=q)""", re.S)

_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_SCRIPT = re.compile(r"<(script|style|pre|code)\b[^>]*>.*?</\1>", re.S | re.I)
_INLINE_JS = re.compile(r"<script\b[^>]*>(?P<js>.*?)</script>", re.S | re.I)
_JS_STRING = re.compile(r"""(?P<q>['"])(?P<v>(?:[^'"\\\n]|\\.)*)(?P=q)""")
#: A JS literal that is code rather than copy. Selectors, event names, URLs, CSS, media queries
#: and class lists are all strings too, and none of them are read by a human.
_JS_CODE = re.compile(r"[<>{};=#]|://|htmx:|^\.|^\(|^[a-z-]+:[a-z-]+$|px |rgba|^\s*$")
#: JS comments. Stripped before the literals are read, or a comment *about* a string counts as
#: one — `/* … "follow the system" … */` is documentation, not copy on the page.
#: ⚠️ The line-comment rule requires the `//` not to be preceded by `:`, so `"https://…"`
#: survives to be recognised as a URL rather than being cut in half.
_JS_COMMENT = re.compile(r"/\*.*?\*/|(?<!:)//[^\n]*", re.S)
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_TAG = re.compile(r"<[^>]*>", re.S)
_ENTITY = re.compile(r"&[a-zA-Z]+;|&#\d+;")

#: Attributes a professor actually reads. `title` is the tooltip on the dry-run badge and on
#: half the icons in the app; `placeholder` is the only text in an empty search box.
_ATTRS = re.compile(
    r"""\b(?:title|placeholder|alt|aria-label|data-loading)\s*=\s*"(?P<v>[^"]*)\"""", re.S)

#: A JS `confirm()` string is the last thing between a professor and a delete.
_CONFIRM = re.compile(r"""confirm\(\s*'(?P<v>[^']*)'""", re.S)

#: Text that is the same in both languages, so wrapping it in `t()` would add a catalogue entry
#: whose two sides are identical — noise that makes a real gap harder to see. Product names,
#: institutions, hostnames, units, and the handful of Spanish words MUSAI shows on purpose
#: because they are what Moodle and SEGA call the thing.
NEUTRAL: frozenset[str] = frozenset({
    "MUSAI", "SUSAI", "Moodle", "SEGA", "UACH", "Gemini", "Google", "WhatsApp", "Playwright",
    "Moodle · UACH · AI", "MUSAI — Cockpit", "Moodle · campusvirtual.uach.mx",
    "SEGA · sega.uach.mx", "campusvirtual.uach.mx", "sega.uach.mx", "virtual3", "aulas1",
    "Parcial 1", "Parcial 2", "Examen Final Ordinario", "Cronograma", "idc", "cmid",
    "DRY-RUN", "LIVE", "AI", "OK", "id", "URL", ".env", "CREDENTIAL_KEY", "GEMINI_API_KEY",
    "SESSION_SECRET", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "tok", "vCPU", "GiB",
    # The wordmark and its subtitle, in both shells.
    "Moodle · UACH · Suite + AI", "MUSAI · Moodle UACH Suite + AI",
    # The faculty's own name, already Spanish; the byline; the contact address.
    "Facultad de Ciencias de la Cultura Física", "CV Labs for Education", "professor@uach.mx",
    # 🔴 The SUSAI chat exhibit on the landing page. It is a transcript of what a UACH student
    # actually types and what SUSAI actually replies — both in Spanish, in every language,
    # because translating it would show an English reader a conversation that never happens.
    "¿Cuándo cierra el Workbook Practice #2?",
    "Cierra el viernes 21 a las 23:59. Llevas 3 de 4 actividades del Parcial 2.",
    "¿Y mi calificación del parcial 1?",
    "8.4. Si quieres el desglose por actividad, te lo mando.",
})

#: Must contain a run of at least two letters to be prose at all. `—`, `·`, `↗`, `%`, `0s` and
#: a bare number are furniture, not sentences.
_HAS_WORD = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{2,}")


def templates() -> list[Path]:
    """Every template, walked from disk. Never a list someone maintains by hand."""
    return sorted(TEMPLATES.glob("*.html"))


def calls(source: str) -> set[str]:
    """Every string this template hands to `t()`, normalised to its catalogue key.

    Comments are stripped first: a `{# … #}` block explaining how `t()` works contains a `t()`
    that nothing renders, and requiring a translation for it would put a sentence in the
    catalogue that no professor can ever reach.
    """
    return {i18n.norm(m.group("text")) for m in _CALL.finditer(_COMMENT.sub(" ", source))}


def _script_prose(source: str) -> list[str]:
    """Sentences hard-coded in an inline `<script>`.

    🔴 Not an afterthought. `base.html`'s loader says *"Working…"* and *"browser jobs can take a
    minute"* from JavaScript, and the spinner is the only thing on screen while a professor
    waits — the single most-read string in the app, sitting in the one region an HTML scanner
    skips. A guard with a blind spot at exactly the place the user is looking is not a guard.

    The filter is "does this read like a sentence": a word, and either a space or terminal
    punctuation. Selectors, event names and CSS have neither.
    """
    out: list[str] = []
    for block in _INLINE_JS.finditer(source):
        js = _JS_COMMENT.sub(" ", _JINJA.sub(" ", block.group("js")))
        for m in _JS_STRING.finditer(js):
            value = m.group("v")
            if _JS_CODE.search(value) or not _HAS_WORD.search(value):
                continue
            if " " in value.strip() or value.rstrip().endswith(("…", ".", "?", "!")):
                out.append(value)
    return out


def _visible(source: str) -> list[str]:
    """The text a browser would paint, roughly, with everything translated already removed."""
    body = _COMMENT.sub(" ", source)
    js = _script_prose(body)
    body = _SCRIPT.sub(" ", body)

    # Attributes and confirm() strings are pulled out BEFORE the tags are stripped, because
    # stripping a tag takes its tooltip with it — and a tooltip is the only documentation some
    # controls have.
    extra: list[str] = []
    for pattern in (_ATTRS, _CONFIRM):
        for m in pattern.finditer(body):
            # An attribute is often half expression (`title="{{ e.detail }}"`) or a whole
            # `{% if %}`. Strip the Jinja out of the value the same way it is stripped out of
            # the body, so what is left is only the part a reader sees as words.
            extra += _JINJA.sub("\x00", m.group("v")).split("\x00")

    # Anything inside `{{ … }}` or `{% … %}` is either an expression or an already-translated
    # `t(…)` call. Replaced with a separator rather than deleted, so `{{ a }}and{{ b }}` does
    # not become the word "and" glued to its neighbours.
    body = _JINJA.sub("\x00", body)
    body = _TAG.sub("\x00", body)
    body = _ENTITY.sub(" ", body)
    return [*re.split(r"[\x00\n]", body), *extra, *js]


def stray(source: str) -> list[str]:
    """Visible text in this template that no `t()` covers. The untranslated worklist."""
    out: list[str] = []
    for chunk in _visible(source):
        text = i18n.norm(chunk)
        # `MUSAI —` is the page-title separator left behind once `{{ t("Cockpit") }}` is
        # stripped out. Trimming the joining punctuation is what lets a product name in
        # NEUTRAL cover it without listing every arrangement of dots and dashes around it.
        bare = text.strip(" —–·-|:,.")
        if not text or text in NEUTRAL or bare in NEUTRAL or not _HAS_WORD.search(text):
            continue
        out.append(text)
    return out


def python_strings() -> set[str]:
    """User-facing sentences that live in Python and reach the page through `t(variable)`.

    🔴 The audit's one structural blind spot, closed by hand. `calls()` finds string
    *literals*, so `{{ t(row.label) }}` looks like a translated string to a reader and like
    nothing at all to the scanner — the label would render English on a Spanish page with every
    check still green. That is the same shape as `docs/help/` being gitignored: correct-looking
    output from a broken build.

    So every table of professor-facing text declared in Python is named here, and the test
    requires Spanish for all of it. Adding a new one and forgetting this list is the remaining
    way to regress — which is why the tables are read from the modules that own them rather
    than copied, and why each entry says which template renders it.
    """
    from musai import metering, professors
    from musai.assistant import agent

    out: set[str] = set()
    # settings.html ▸ Passwords — what each stored credential is used for.
    out |= {info["why"] for info in professors.SYSTEM_INFO.values()}
    # settings.html ▸ Usage — the metered-kind names and their one-line explanations, plus the
    # notes on the "what things cost" table.
    for label, blurb in metering.KINDS.values():
        out |= {label, blurb}
    out |= {name for name, _kw, _note in metering.TYPICAL if name not in metering.KINDS}
    out |= {note for _n, _kw, note in metering.TYPICAL if note}
    # assistant_reply.html — every way the assistant can decline to answer. 🔴 A refusal that
    # stays English on a Spanish page is the one message a professor most needs to understand.
    out |= set(agent._MESSAGES.values())
    out.add("Assistant error: {reason}")
    return {i18n.norm(s) for s in out if s}


def all_calls() -> set[str]:
    """Every translatable string in the app: template literals plus the Python-side tables."""
    used: set[str] = set(python_strings())
    for path in templates():
        used |= calls(path.read_text(encoding="utf-8"))
    return used


def all_stray() -> dict[str, list[str]]:
    """Untranslated visible text, per template. Empty dict is the goal."""
    out: dict[str, list[str]] = {}
    for path in templates():
        found = stray(path.read_text(encoding="utf-8"))
        if found:
            out[path.name] = found
    return out


def _main() -> None:                                              # pragma: no cover - a tool
    import sys

    if "--missing" in sys.argv:
        gaps = i18n.missing("es", all_calls())
        print(f"{len(gaps)} call site(s) with no Spanish:")
        for text in gaps:
            print(f"  {text}")
        orphans = i18n.unused("es", all_calls())
        print(f"\n{len(orphans)} catalogue entr(ies) nothing calls:")
        for text in orphans:
            print(f"  {text}")
        return

    found = all_stray()
    total = sum(len(v) for v in found.values())
    for name, items in found.items():
        print(f"\n── {name} ({len(items)})")
        for text in items:
            print(f"   {text}")
    print(f"\n{total} untranslated string(s) in {len(found)} template(s).")


if __name__ == "__main__":                                        # pragma: no cover
    _main()
