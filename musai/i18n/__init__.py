"""English and Spanish for the cockpit, keyed on the English sentence itself.

MUSAI is written for professors at a Mexican university and it has been shipping in English.
This module is what lets a colleague read it in Spanish without a second copy of the app.

## Why the key is the English text and not `settings.passwords.intro`

Because of how each one fails. With short ids, English is a *translation too* — a missing key
renders `settings.passwords.intro` on the page, and every language is equally broken. Keyed on
the source sentence, a missing translation renders **the English sentence**, which is the
documented default anyway. The worst case is "not translated yet", never "not a sentence".

That is the same reasoning as `docs/help/`: prefer the failure mode that still tells the reader
something true. The cost is that editing an English string orphans its translation — so
`tests/test_i18n.py` walks every `t(...)` call site in every template and fails on a string the
catalogue does not have. Drift is a red test, not a Spanish page with an English paragraph in
the middle of it.

## What this deliberately is not

Not gettext. `.po` files compile to `.mo`, and a deploy that skips the compile step serves
English to everyone with no error anywhere — the *"missing answer wearing the costume of a
correct refusal"* shape this project has already paid for once (`docs/help/` vs `.gitignore`).
A catalogue that is a Python dict cannot be half-deployed: either the module imports or the app
does not start.

## Interpolation

`t("Your {n} courses in {sem}.", n=3, sem="Aug–Dec 2026")`. `str.format`, not `%`, because `%`
is a literal in a surprising amount of this app's prose (`{pct}% of free usage`) and a literal
`{` is not. **Values are HTML-escaped before substitution**, so a course name with an `&` in it
is safe even though the translated string itself is trusted markup.
"""

from __future__ import annotations

from typing import Optional

#: The languages MUSAI has a complete catalogue for. Adding one is a new module in this package
#: plus an entry in `_CATALOGUES`; nothing else in the app names a language.
LANGUAGES: tuple[str, ...] = ("en", "es")

#: 🔴 English, and it is a real decision rather than an accident of what got written first.
#: A professor who has never chosen sees English — see `musai/web/language.py` for why "never
#: chose" and "chose English" must stay distinguishable in the database.
DEFAULT: str = "en"

#: What the language picker calls each one. In the language itself, always: a Spanish speaker
#: hunting for their language on an English page is looking for the word "Español".
LANGUAGE_NAMES: dict[str, str] = {"en": "English", "es": "Español"}


def normalize(value: Optional[str]) -> Optional[str]:
    """A supported language code, or `None`.

    🔴 `None` is a real answer and callers must not collapse it into `DEFAULT`. It is what
    distinguishes *"never chose"* from *"chose English"*, and only that distinction lets the
    default change one day without overriding every professor who actively picked English.
    Also the input filter for the picker: an unknown code is dropped rather than stored, so a
    hand-edited URL cannot put `?lang=fr` into the database.
    """
    code = (value or "").strip().lower()
    if "-" in code:            # `es-MX`, `en-GB` — the region is not a catalogue
        code = code.split("-", 1)[0]
    return code if code in LANGUAGES else None


def norm(text: str) -> str:
    """The lookup key for a sentence: its words, single-spaced, trimmed.

    ⭐ Templates wrap. A paragraph written across four indented lines in `settings.html` is one
    sentence to the reader, and it must stay one key — otherwise re-indenting a template silently
    orphans its translation and the page half-reverts to English with no error anywhere. So the
    key is the *words*, and whitespace is not part of the contract.
    """
    return " ".join((text or "").split())


_CACHE: dict[str, dict[str, str]] = {}


def _catalogue(lang: str) -> dict[str, str]:
    if lang not in LANGUAGES or lang == DEFAULT:
        return {}
    if lang not in _CACHE:
        if lang == "es":
            from musai.i18n import es

            raw = es.CATALOGUE
        else:                                                     # pragma: no cover
            raw = {}
        # Normalised on both sides, so the catalogue can be written with the same line breaks
        # the templates use and still match.
        _CACHE[lang] = {norm(k): v for k, v in raw.items()}
    return _CACHE[lang]


def catalogue(lang: str) -> dict[str, str]:
    """Every translated string for one language. `{}` for English, which is the source."""
    return _catalogue(lang)


def translate(text: str, lang: Optional[str] = None, /, **params) -> str:
    """One string in one language, with `{name}` placeholders filled in.

    An untranslated string renders in English rather than raising: a professor mid-task should
    get an English sentence, not a 500. The test suite is where a gap is an error — a rail that
    breaks the page for the reader punishes the wrong person.
    """
    lang = lang or DEFAULT
    out = _catalogue(lang).get(norm(text), norm(text)) if lang != DEFAULT else norm(text)
    if params:
        from markupsafe import escape

        # The catalogue is ours and may carry markup; the VALUES are data and never may.
        out = out.format(**{k: escape(v) for k, v in params.items()})
    return out


def missing(lang: str, used: set[str]) -> list[str]:
    """Which of `used` this language has no translation for. The test's whole job."""
    if lang == DEFAULT:
        return []
    have = _catalogue(lang)
    return sorted(s for s in {norm(u) for u in used} if s not in have)


def unused(lang: str, used: set[str]) -> list[str]:
    """Catalogue entries nothing calls any more — an English edit that orphaned its Spanish.

    Reported as loudly as a missing one. An orphan is not harmless: it is the *old* sentence,
    still readable, still plausible, and no longer describing the app.
    """
    seen = {norm(u) for u in used}
    return sorted(s for s in _catalogue(lang) if s not in seen)
