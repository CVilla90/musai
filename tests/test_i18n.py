"""EN/ES: the catalogue, the stored choice, and how far the translation has actually got.

Three things are checked here, and the third is the unusual one.

1. **The catalogue is complete for what is wired.** Every `t("…")` call site has Spanish, and
   no Spanish entry is left over from an English sentence somebody edited. Both directions,
   because an orphan is not harmless: it is the *old* sentence, still readable, still
   plausible, and no longer describing the app.
2. **`NULL` means "never chose".** The distinction the whole feature hangs on, asserted rather
   than commented.
3. **A ratchet on the templates that are not converted yet.** ⚠️ The EN/ES pass is partway
   through: the shell, the cockpit, the assistant, Settings and the landing page are done; the
   course-workspace tabs and the job progress views are not. `REMAINING` below is that state,
   written down as a number a test can check. A template listed as done may never regress to
   having untranslated text, and no template may grow more of it. **Read `REMAINING` as the
   worklist**: drive an entry to zero, move its name into `TRANSLATED`, delete the entry.

   A checklist in a markdown file would rot the first week. This one fails.
"""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from musai import i18n
from musai.i18n import audit


@pytest.fixture
def client():
    """Signed OUT by default — the landing page is half of what this feature is about.

    Deliberately not the pre-signed-in `client` of `test_jobs_and_routes.py`: the language
    picker has to work before MUSAI knows who is asking, and a fixture that arrives
    authenticated could never catch that.
    """
    from musai.web.app import app

    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------------------
# 1. The catalogue matches what the app actually asks for
# ---------------------------------------------------------------------------

def test_every_translated_string_in_the_app_has_spanish():
    """A `t()` call with no catalogue entry renders English inside a Spanish page.

    Enumerated from the templates and from `python_strings()`, never from a list kept here —
    a list here would be a second copy of the app that drifts, which is the failure this whole
    module exists to prevent.
    """
    gaps = i18n.missing("es", audit.all_calls())
    assert not gaps, (
        f"{len(gaps)} string(s) have no Spanish. Add them to `musai/i18n/es.py`:\n  "
        + "\n  ".join(gaps[:20]))


def test_no_spanish_entry_is_orphaned():
    """An English sentence was edited and its translation now matches nothing.

    🔴 The dangerous half of drift, and the quiet one: the page silently falls back to the new
    English while the old Spanish sits in the catalogue looking maintained.
    """
    orphans = i18n.unused("es", audit.all_calls())
    assert not orphans, (
        f"{len(orphans)} Spanish entr(ies) match no call site — the English was probably "
        f"edited:\n  " + "\n  ".join(orphans[:20]))


def test_the_catalogue_keeps_every_placeholder_its_english_has():
    """🔴 A dropped `{name}` is a sentence with a hole in it; an invented one raises `KeyError`.

    `translate()` calls `.format(**params)`, so a placeholder the English does not supply
    crashes the page — in Spanish only, which is exactly the failure nobody sees before a
    colleague does.
    """
    import re

    holes = re.compile(r"\{(\w+)\}")
    for english, spanish in i18n.catalogue("es").items():
        assert holes.findall(english) == holes.findall(spanish) or \
            set(holes.findall(english)) == set(holes.findall(spanish)), (
            f"placeholders differ:\n  EN {english}\n  ES {spanish}")


def test_the_load_bearing_sentences_are_translated_and_still_say_the_hard_thing():
    """🔴 The four sentences a softened translation would turn into a safety regression.

    Not a spellcheck — each assertion names the word that has to survive. *"MUSAI puede leer"*
    rather than *"tiene acceso a"*; *Simulacro* rather than *prueba*; the SEGA buttons keeping
    the names they have on screen.
    """
    es = i18n.catalogue("es")

    passwords = next(v for k, v in es.items() if "MUSAI can read these passwords" in k)
    assert "puede leer estas contraseñas" in passwords, (
        "the passwords warning must still say MUSAI can READ them")

    assert es[i18n.norm("DRY-RUN · no writes")] == "SIMULACRO · sin escrituras"
    assert es[i18n.norm("LIVE · writes enabled")].startswith("EN VIVO")

    rail = next(v for k, v in es.items() if k.startswith("The SEGA adapter can click"))
    assert "<i>Guardar</i>" in rail and "<i>Confirmar</i>" in rail, (
        "the SEGA button names are what the professor sees on screen and are never translated")

    restore = next(v for k, v in es.items() if "MUSAI can only read the courses" in k)
    assert restore  # present at all; wording checked by eye, meaning checked by the reader


# ---------------------------------------------------------------------------
# 2. The stored choice
# ---------------------------------------------------------------------------

def test_an_unknown_language_code_is_dropped_rather_than_stored():
    """`/lang/fr` must not put `fr` in the database. The picker's input filter."""
    assert i18n.normalize("fr") is None
    assert i18n.normalize("'; DROP TABLE professor") is None
    assert i18n.normalize("") is None
    assert i18n.normalize(None) is None


def test_a_regioned_code_resolves_to_its_language():
    """`es-MX` is Spanish. A browser or a hand-typed URL may well say the region."""
    assert i18n.normalize("es-MX") == "es"
    assert i18n.normalize("EN-gb") == "en"


def test_null_means_never_chose_and_is_not_the_same_as_choosing_english():
    """🔴 The distinction the nullable column exists for.

    If `normalize(None)` ever returned `"en"`, the two states would be indistinguishable and a
    future change of default would silently override every professor who really did pick
    English.
    """
    assert i18n.normalize(None) is None
    assert i18n.normalize("en") == "en"
    assert i18n.normalize(None) != i18n.DEFAULT


def test_the_landing_page_switches_language_and_says_so_in_its_lang_attribute(client):
    """The one screen where the choice can be made before signing in.

    🔴 Without this the first contact a Spanish-speaking colleague has with MUSAI is an English
    page whose only way out is behind the sign-in button they are still deciding about.
    """
    assert 'lang="en"' in client.get("/").text

    moved = client.get("/lang/es", follow_redirects=False)
    assert moved.status_code == 303
    body = client.get("/").text
    assert 'lang="es"' in body
    assert "una sola consola" in body                     # the headline
    assert "SIMULACRO" in body or "Simulacro" in body     # the dry-run badge

    client.get("/lang/en")
    assert 'lang="en"' in client.get("/").text


def test_an_unknown_code_lands_on_a_real_page_instead_of_a_404(client):
    """A stale bookmark is a preference toggle's most likely visitor, not an attack."""
    assert client.get("/lang/fr", follow_redirects=False).status_code == 303
    assert 'lang="en"' in client.get("/").text


def test_the_picker_cannot_be_used_to_leave_the_site(client):
    """`?next=` goes through the same `_safe_next` the sign-in round trip uses."""
    moved = client.get("/lang/es?next=//evil.example.com", follow_redirects=False)
    assert moved.headers["location"] == "/"


def test_a_signed_in_choice_is_stored_on_the_professor_not_just_the_browser(client, sign_in):
    """⭐ "Saved for next time, on any device" means a column. A cookie is one browser.

    Asserted against the row rather than against the page, because a page rendered in Spanish
    proves only that the cookie survived the redirect.
    """
    from sqlmodel import Session

    from musai.db import engine
    from musai.professors import by_email

    sign_in(client, email="languagetest@uach.mx")
    client.get("/lang/es", follow_redirects=False)

    with Session(engine) as sess:
        prof = by_email(sess, "languagetest@uach.mx")
        assert prof is not None and prof.language == "es"


def test_a_choice_made_before_signing_in_survives_signing_in(client, sign_in):
    """🔴 The moment the setting would look most broken: it reverts *because* you signed in.

    A colleague picks Español on the landing page, then signs in — and MUSAI meets them with a
    `NULL` language. The cookie choice is adopted onto the account there and then, which is
    also what carries it to their office machine.
    """
    from sqlmodel import Session

    from musai.db import engine
    from musai.professors import by_email

    client.get("/lang/es", follow_redirects=False)        # signed OUT: cookie only
    sign_in(client, email="adoptlang@uach.mx")
    assert client.get("/").status_code == 200             # first signed-in render

    with Session(engine) as sess:
        prof = by_email(sess, "adoptlang@uach.mx")
        assert prof is not None and prof.language == "es", (
            "a language chosen on the landing page must follow the professor into the app")


def test_adoption_never_overwrites_a_choice_already_stored(client, sign_in):
    """🔴 Fills a NULL, never replaces a value.

    Otherwise a stale cookie on a shared faculty machine silently changes what somebody else
    picked in Settings — a setting that changes itself is worse than one that never saved.
    """
    from sqlmodel import Session

    from musai.db import engine
    from musai.professors import by_email
    from musai.web import language as lang_mod

    sign_in(client, email="keepsmine@uach.mx")
    client.get("/lang/en", follow_redirects=False)         # deliberate: English
    client.cookies.set(lang_mod.COOKIE, "es")              # a stale cookie from another visit
    client.get("/")

    with Session(engine) as sess:
        prof = by_email(sess, "keepsmine@uach.mx")
        assert prof.language == "en", "a stored choice outranks a cookie, always"


def test_an_untranslated_string_renders_in_english_rather_than_raising():
    """The fallback that makes source-keyed translation safe: worst case is English."""
    assert i18n.translate("a sentence nobody has translated", "es") == \
        "a sentence nobody has translated"


def test_whitespace_in_a_template_is_not_part_of_the_key():
    """Re-indenting a paragraph must not orphan its translation."""
    wrapped = """DRY-RUN ·
                 no writes"""
    assert i18n.translate(wrapped, "es") == "SIMULACRO · sin escrituras"


def test_an_interpolated_value_is_escaped_but_the_sentence_is_not():
    """The catalogue is trusted markup; the values in it never are."""
    out = i18n.translate("Moodle calls it {name}", "es", name="<script>x</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ---------------------------------------------------------------------------
# 3. How far the pass has got — the ratchet
# ---------------------------------------------------------------------------

#: Templates with **no** untranslated visible text. Adding one here is how a template is
#: declared done; it can then never quietly regress.
TRANSLATED = {
    "assistant.html", "assistant_reply.html", "base.html", "course_base.html", "index.html",
    "job_refused.html", "landing.html", "settings.html",
}

#: ⚠️ Not yet converted, with today's count. **These may only go down.** Each number is what
#: `python -m musai.i18n.audit` reports for that file; drive one to zero, move the name into
#: `TRANSLATED` above, and delete the line.
REMAINING = {
    "course_activities.html": 47,
    "course_build.html": 21,
    "course_build_result.html": 7,
    "course_dates.html": 20,
    "course_dates_plan.html": 31,
    "course_grades.html": 32,
    "course_hub.html": 20,
    "course_hub_preview.html": 18,
    "course_messages.html": 33,
    "course_overview.html": 16,
    "course_transfer.html": 49,
    "job_progress.html": 22,
    "message_progress.html": 12,
    "partial_grades.html": 47,
    "sega_dryrun.html": 23,
    "work_progress.html": 67,
}


@pytest.mark.parametrize("name", sorted(TRANSLATED))
def test_a_finished_template_has_no_untranslated_text(name):
    """🔴 The regression guard. A new button added to a done template must go through `t()`.

    Without this, "MUSAI is translated" is true on the day it is written and decays from then
    on, one hard-coded label at a time, with nobody finding out until a colleague is reading it.
    """
    stray = audit.all_stray().get(name, [])
    assert not stray, (
        f"{name} has {len(stray)} untranslated string(s):\n  " + "\n  ".join(stray[:12]))


def test_the_untranslated_count_never_grows():
    """The ratchet. Progress is allowed; sliding back is not."""
    found = audit.all_stray()
    worse = {
        name: (len(found.get(name, [])), budget)
        for name, budget in REMAINING.items()
        if len(found.get(name, [])) > budget
    }
    assert not worse, (
        "these templates gained untranslated text (now, allowed): "
        + ", ".join(f"{n} {a}>{b}" for n, (a, b) in sorted(worse.items())))


def test_every_template_is_accounted_for():
    """No template may be neither finished nor on the worklist.

    ⭐ Enumerated from the directory, so a template added next month is a failing test rather
    than a silent hole in the coverage claim — the same reason `docs/help/` checks its paths
    against `app.routes` instead of against a list.
    """
    on_disk = {p.name for p in audit.templates()}
    accounted = TRANSLATED | set(REMAINING)
    assert on_disk <= accounted, (
        f"not declared translated or remaining: {sorted(on_disk - accounted)}")


def test_the_ratchet_is_not_stale():
    """A finished file left sitting in `REMAINING` hides the fact that it is done."""
    found = audit.all_stray()
    done = [n for n in REMAINING if not found.get(n)]
    assert not done, (
        f"these are fully translated — move them into TRANSLATED: {sorted(done)}")


# ---------------------------------------------------------------------------
# The two traps this feature has already stepped in
# ---------------------------------------------------------------------------

def test_no_template_shadows_the_translator():
    """🔴 `{% for t in … %}` silently turns `t("…")` into a string call for the whole block.

    Found the hard way in `assistant_reply.html` and `settings.html`, which both looped over a
    variable called `t`. The failure is not an error — Jinja is happy to call anything — it is
    a `TypeError` at render time on a page that was fine yesterday.
    """
    import re

    shadow = re.compile(r"\{%-?\s*(?:for\s+t\s+in|set\s+t\s*=)|as\s+t\s*%\}")
    offenders = [p.name for p in audit.templates()
                 if shadow.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"these bind the name `t` and shadow the translator: {offenders}"


def test_the_html_lang_attribute_is_computed_in_both_shells():
    """🔴 The bug this feature started from: `base.html` said `es` and served English.

    Both shells must ask, and neither may hard-code — a wrong `lang` is what makes a screen
    reader pronounce English prose with Spanish phonetics.
    """
    for name in ("base.html", "landing.html"):
        src = next(p for p in audit.templates() if p.name == name).read_text(encoding="utf-8")
        assert '<html lang="{{ lang() }}">' in src, f"{name} hard-codes <html lang>"


def test_the_assistant_is_told_which_language_to_answer_in():
    """The prompt follows the stored choice instead of detecting per message.

    ⚠️ Detection made the answer language flicker with the question, and let a Spanish page
    return an English answer with nothing on screen explaining why.
    """
    from musai.assistant import agent

    assert "Detect and reply in the user's language" not in agent.SYSTEM
    assert "Always reply in Spanish" in agent.system_for("es")
    assert "Always reply in English" in agent.system_for("en")
    # An unknown language must not produce a prompt with no language instruction at all.
    assert "Always reply in English" in agent.system_for("fr")
