"""The Course Hub — a page whose whole reason to exist is "type it once".

The original was hand-written HTML with the professor's phone number in three places, his
WhatsApp group in two and his photo in two. Every test here defends one half of the promise
The owner asked for: **change it in one place, and it changes everywhere** — and **no professor's
personal details are welded into the template**.
"""

import inspect
import re
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session

from musai.coursebuild import hub, hub_store
from musai.coursebuild.render import find_marker, lint
from musai.models import Course, Semester
from musai.professors import get_or_create


# ── "one place, once" — the property the whole feature exists for ────────────────────────

@pytest.mark.parametrize("field", [f for f in hub.FIELDS if f.kind != "choice"],
                         ids=lambda f: f.key)
def test_every_field_actually_reaches_the_page(field):
    """A field that renders nowhere is worse than a missing one: the professor edits it,
    sees no change, and concludes the tool is broken."""
    token = "ZZTOP" + field.key.replace("_", "")
    value = {"url": f"https://example.com/{token}.png",
             "phone": f"+52 614 {token}"}.get(field.kind, token)
    data = {**hub.example_data("es"), field.key: value}
    assert token in hub.render_hub(data), f"{field.key} is not rendered anywhere"


@pytest.mark.parametrize("field", [f for f in hub.FIELDS if f.kind == "choice"],
                         ids=lambda f: f.key)
def test_every_choice_changes_the_page(field):
    base = hub.example_data("es")
    renders = {hub.render_hub({**base, field.key: c}) for c in field.choices}
    assert len(renders) == len(field.choices), f"{field.key} choices render identically"


def test_the_phone_is_printed_in_exactly_one_place():
    """Typed once was only half of it (the owner, 2026-08-10): the page also has to *print* it
    once, or a professor checking their own page has three things to compare and no way to
    know they are one field. The old version printed it in the header pill, the contact card
    and the professor card, and this test asserted `>= 3`.

    The tappable `wa.me` link is built from the same field, so the digits legitimately appear
    a second time inside an href — hence counting the *formatted* number, which only ever
    appears as visible text.
    """
    first = "+52 614 111 2222"
    out = hub.render_hub({**hub.example_data("es"), "whatsapp_phone": first})
    assert out.count(first) == 1, "the phone number is printed more than once"

    out2 = hub.render_hub({**hub.example_data("es"), "whatsapp_phone": "+52 614 999 8888"})
    assert first not in out2, "an old phone number survived the change"


def test_the_group_link_is_printed_in_exactly_one_place():
    link = "https://chat.whatsapp.com/EXAMPLEONLY"
    out = hub.render_hub({**hub.example_data("es"), "whatsapp_group_url": link})
    assert out.count(link) == 1
    assert link not in hub.render_hub(hub.example_data("es"))


def test_the_contact_block_is_the_only_thing_that_renders_contact_details():
    """The property, stated structurally rather than by counting: strip `_contact_block` out
    of the page and no trace of either field is left anywhere else."""
    data = {**hub.example_data("es"), "whatsapp_phone": "+52 614 111 2222",
            "whatsapp_group_url": "https://chat.whatsapp.com/EXAMPLEONLY"}
    d = {**hub.DEFAULTS, **data}
    page = hub.render_hub(data)
    block = hub._contact_block(d, hub.strings(d["lang"]))
    assert block and block in page
    rest = page.replace(block, "")
    assert "+52 614 111 2222" not in rest
    assert "chat.whatsapp.com" not in rest
    assert "wa.me" not in rest


def test_help_instructions_are_printed_once_too():
    """Same defect, same fix: the professor card repeated the contact card's advice."""
    text = "Mandame un mensaje UNIQUETOKEN con tu grupo"
    out = hub.render_hub({**hub.example_data("es"), "help_instructions": text})
    assert out.count("UNIQUETOKEN") == 1


def test_one_photo_field_feeds_both_sizes():
    url = "https://example.com/me.png"
    out = hub.render_hub({**hub.example_data("es"), "photo_url": url})
    assert out.count(url) == 2                      # the small avatar and the big one
    assert 'width="54"' in out and 'width="110"' in out


def test_a_missing_group_link_shows_coming_soon_and_is_not_clickable():
    """The owner, 2026-08-10. An empty group link used to render NOTHING, so the page looked
    finished while a student had no idea a group was coming — `validate()` could tell the
    professor, but nothing told the reader.

    🔴 Not a link, deliberately. His hand-written version used `<a href="YOUR_GROUP_LINK_HERE">`,
    which TinyMCE resolved against the site base into a real clickable link to a 404 on
    virtual3.uach.mx. A pill with no `href` says the same thing and cannot mislead.
    """
    data = {**hub.example_data("es"), "whatsapp_phone": "+52 614 000 0000",
            "whatsapp_group_url": ""}
    out = hub.render_hub(data)
    s = hub.strings("es")
    assert s["group_pending"] in out and s["group"] in out
    assert s["open_group"] not in out
    assert "chat.whatsapp.com" not in out
    # The pending state must not introduce a link of ANY kind — a placeholder href resolved
    # against Moodle's base is exactly how the broken one got shipped.
    pending = out[out.index(s["group"]):]
    assert "<a " not in pending[:pending.index("</div></div>")]

    filled = hub.render_hub({**data, "whatsapp_group_url": "https://chat.whatsapp.com/AbC"})
    assert s["group_pending"] not in filled
    assert 'href="https://chat.whatsapp.com/AbC"' in filled


def test_no_card_is_ever_left_alone_in_a_row():
    """The owner, 2026-08-10: *"the help & contact card is all alone on one side."*

    Three cards in one `auto-fit` grid lay out 2 + 1 at two columns, and two columns is what
    Moodle gives this page whenever the nav drawer is open (858px). The fix is structural, not
    a tuned breakpoint: the grid holds exactly **two** children — a column each — so there is
    no container width at which one of them is orphaned.

    Asserted on the structure rather than on a rendered width, because the property is *"the
    grid can only be 2x1 or 1x2"*, and that is true of the markup at every width at once.
    """
    out = hub.render_hub(hub.example_data("bilingual"))
    grid = out[out.index("grid-template-columns:repeat(auto-fit,minmax(min(340px"):]
    # Count only the columns' own opening divs: children of the grid, not cards inside them.
    depth, children = 0, 0
    for token in re.findall(r"<div\b|</div>", grid[grid.index(">") + 1:]):
        if token == "</div>":
            if depth == 0:
                break
            depth -= 1
        else:
            if depth == 0:
                children += 1
            depth += 1
    assert children == 2, f"the card grid must hold exactly two columns, found {children}"

    # And the taller card sits alone opposite the two shorter ones, which is what makes the
    # two columns end near the same place instead of leaving a hole under `Grading`.
    # Read off the `en` render: a bilingual label is `"Grading ~~Ponderación~~"` in the table
    # and a two-part span in the page, so the raw string is never found in the output.
    # …and compared through `fmt`, because the page holds the ESCAPED label: `Help & contact`
    # is written `Help &amp; contact`, so the raw string is not in the output either.
    english = hub.render_hub(hub.example_data("en"))
    at = {key: english.index(hub.fmt(hub.STRINGS["en"][key]))
          for key in ("grading", "where", "help")}
    assert at["grading"] < at["where"] < at["help"]


def test_the_big_photo_centres_itself_once_the_row_wraps():
    """The owner, 2026-08-10: "make sure to center it."

    Inline styles cannot carry a media query, so ONE declaration has to serve both layouts,
    and it does — because of the item beside it. `justify-content:center` distributes free
    space, and on a wide screen there is none: the text block is `flex:1` and takes it all,
    so the photo stays flush left next to the heading. Wrap the row (a phone, or Moodle's own
    narrow column) and the photo is alone on its line with the whole width to itself.

    Both halves are asserted because the regression is silent in either direction: drop the
    `justify-content` and the phone gets a photo hugging the left edge with a void beside it;
    drop the text block's `flex:1` and the DESKTOP layout starts centring too.
    """
    out = hub.render_hub({**hub.example_data("es"),
                          "photo_url": "https://example.com/me.png"})
    before_big_photo = out[:out.index('width="110"')]
    opening = before_big_photo[before_big_photo.rindex('<div style="display:flex'):]
    assert "flex-wrap:wrap" in opening and "justify-content:center" in opening
    assert 'style="flex:1;min-width:210px"' in out, \
        "the text block must still grow, or the photo centres on desktop too"


def test_the_tappable_link_is_built_from_the_printed_number():
    """No second "and now type it again without spaces" box."""
    out = hub.render_hub({**hub.example_data("es"), "whatsapp_phone": "+52 1 614 000 0000"})
    assert "https://wa.me/5216140000000" in out


def test_the_example_temario_is_the_real_course_not_a_plausible_one():
    """🔴 What the placeholder version got wrong, kept as a regression.

    Read off `scratchpad/structure_9023.json` on 2026-08-10: `Adjectives` and `Imperatives`
    are quizzes in the **First Term** tab, `Possessive*` and `Family members` are in
    **Second Term**, and `Likes & dislikes` is in the **hidden** "Other resources" tab. The
    old example advertised all five in the wrong term. A hero that is plausible and wrong is
    worse than one that is vague, because a student plans their week from it.
    """
    p1, p2, p3 = hub.TERMS_EN, hub.TERMS2_EN, hub.TERMS3_EN
    assert "Adjectives" in p1 and "Imperatives" in p1
    assert "Adjectives" not in p2 and "Imperatives" not in p2
    assert "Possessive" in p2 and "Family" in p2
    assert "Possessive" not in p3 and "Family" not in p3
    assert "Comparative" in p3 and "Superlative" in p3 and "Can" in p3
    for term in (p1, p2, p3):
        assert "ikes" not in term, "Likes & dislikes lives in the hidden tab; no student sees it"

    # And the Spanish column has to list the same number of topics as the English one, or the
    # two halves of a bilingual line are quietly describing different courses.
    for en, es in ((p1, hub.TERMS_ES), (p2, hub.TERMS2_ES), (p3, hub.TERMS3_ES)):
        assert en.count("·") == es.count("·")


def test_no_professors_personal_data_is_welded_into_the_template():
    """This module ships to colleagues. The owner's own details belong in the database.

    The prose is allowed to name him — it explains where this came from. The *values* are not.
    """
    src = Path(hub.__file__).read_text(encoding="utf-8")
    for personal in ("614 555 0101", "6145550101", "EXAMPLEGROUPCODE01",
                     "professor_pic", "pluginfile.php"):
        assert personal not in src, f"{personal!r} is hardcoded in the hub template"

    shipped = repr(hub.DEFAULTS) + repr(hub.example_data("es")) + repr(hub.example_data("en"))
    for name in ("the owner", "@uach.mx"):
        assert name not in shipped, f"{name!r} ships as a default value"


# ── Moodle safety: the same contract render.py enforces ─────────────────────────────────

@pytest.mark.parametrize("data", [hub.DEFAULTS, hub.example_data("es"), hub.example_data("en")],
                         ids=["empty", "example-es", "example-en"])
def test_output_is_moodle_safe(data):
    assert lint(hub.render_hub(data)) == []


def test_no_script_fallbacks_anywhere():
    """The original leaned on `onerror=` to swap in a placeholder when an image 404s. A
    fallback that needs JavaScript is not a fallback — this one is server-side markup."""
    out = hub.render_hub({**hub.example_data("es"), "photo_url": "https://example.com/x.png"})
    assert "onerror" not in out and "<script" not in out.lower()


def test_professor_text_is_escaped_not_executed():
    evil = '<script>alert(1)</script>'
    out = hub.render_hub({**hub.example_data("es"), "bio": evil})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_the_professor_card_does_not_offer_to_expand_into_nothing():
    """A name and a phone but no bio, expectations or promise — what a colleague sends when
    asked for only those — used to render "Tap to expand ▾" above a panel that repeated the
    header word for word. The affordance has to be backed by content or not drawn at all."""
    out = hub.render_hub({**hub.DEFAULTS, "lang": "es",
                          "professor_name": "Ada Lovelace",
                          "professor_role": "English Professor",
                          "whatsapp_phone": "+52 614 111 2222"})
    assert "Ada Lovelace" in out, "the card itself must still render"
    assert "<details" not in out
    assert "<summary" not in out
    for chrome in (hub.STRINGS["es"]["expand"], hub.STRINGS["es"]["more"],
                   hub.STRINGS["es"]["collapse"]):
        assert chrome not in out, f"{chrome!r} promises a disclosure that is not there"


def test_the_professor_card_still_expands_when_there_is_something_behind_it():
    """The other half: the guard must not have removed the disclosure from a full profile."""
    for extra in ("bio", "promise", "expectations"):
        out = hub.render_hub({**hub.DEFAULTS, "lang": "es",
                              "professor_name": "Ada Lovelace", extra: "Something to say"})
        assert "<details" in out and "<summary" in out, f"{extra} should open the card"
        assert "Something to say" in out
        assert hub.STRINGS["es"]["collapse"] in out


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "http://insecure.example.com/group",
    " javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
])
def test_only_https_links_survive(bad):
    out = hub.render_hub({**hub.example_data("es"), "whatsapp_group_url": bad})
    assert "javascript:" not in out and "text/html" not in out
    assert bad.strip() not in out


def test_an_inline_data_image_is_allowed():
    """Proven to survive this Moodle's sanitizer on 2026-08-07, and it is same-origin."""
    tiny = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQ"
            "VR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    assert tiny in hub.render_hub({**hub.example_data("es"), "photo_url": tiny})


# ── graceful emptiness: a half-filled form must not render half-empty furniture ──────────

def test_a_blank_form_still_renders_something_sane():
    out = hub.render_hub({})
    assert lint(out) == []
    for s in hub.STRINGS.values():
        assert s["whatsapp"] not in out, "an empty phone must not render an empty contact row"
        assert s["open_group"] not in out


def test_a_missing_contact_is_warned_about_because_the_page_cannot_show_a_gap():
    """The block just does not render, so the preview looks finished. The professor has to
    hear about it from `validate`, or the page ships with no way to reach anyone."""
    notes = hub.validate({**hub.example_data("es"), "whatsapp_phone": "",
                          "whatsapp_group_url": ""})
    assert any("WhatsApp" in n for n in notes)
    assert any("grupo" in n for n in notes)
    filled = hub.validate({**hub.example_data("es"),
                           "whatsapp_group_url": "https://chat.whatsapp.com/X"})
    assert not any("grupo de WhatsApp de este grupo" in n for n in filled)


# ── bilingual: English first, Spanish smaller and dimmer (the owner, 2026-08-09/10) ─────────

def test_bilingual_puts_english_first_and_spanish_after_it():
    out = hub.render_hub(hub.example_data("bilingual"))
    assert lint(out) == []
    en, es = hub.STRINGS["en"]["content"], hub.STRINGS["es"]["content"]
    assert en in out and es in out
    assert out.index(en) < out.index(es), "the Spanish half came first"


def test_the_spanish_half_is_dimmed_by_opacity_not_by_a_hard_coded_grey():
    """🔴 Twice-paid-for, in Vellum and again in the published books: a fixed grey looks right
    on a white card and is almost invisible on the dark contact block. `opacity` composes
    with whatever background the label lands on; a hex does not."""
    assert "opacity" in hub.SECONDARY_STYLE
    assert "color" not in hub.SECONDARY_STYLE
    assert "font-size" in hub.SECONDARY_STYLE
    out = hub.render_hub(hub.example_data("bilingual"))
    assert f'<span style="{hub.SECONDARY_STYLE}">' in out


def test_the_bilingual_chrome_is_derived_so_it_can_never_fall_behind():
    """Two hand-typed tables for the same labels is how one loses a string. This one is
    generated, so a new label in `en`+`es` is a new bilingual label for free."""
    assert set(hub.STRINGS["bilingual"]) == set(hub.STRINGS["en"]) == set(hub.STRINGS["es"])
    for key, value in hub.STRINGS["bilingual"].items():
        assert value.startswith(hub.STRINGS["en"][key])
        assert hub.STRINGS["es"][key].removesuffix(" ↗") in value


def test_a_label_that_is_the_same_in_both_languages_is_not_glossed_against_itself():
    """"WhatsApp:" is "WhatsApp:" — printing it twice reads as a rendering fault."""
    assert hub.STRINGS["bilingual"]["whatsapp"] == "WhatsApp:"
    out = hub.render_hub({**hub.example_data("bilingual"),
                          "whatsapp_phone": "+52 614 111 2222"})
    assert out.count("WhatsApp:") == 1


@pytest.mark.parametrize("lang", ["es", "en", "bilingual"])
def test_no_gloss_marker_ever_survives_into_the_page(lang):
    """🔴 The generic form of a real bug: one `esc(...)` left where `fmt(...)` belongs, and
    the contact block printed the literal `WhatsApp: ~~WhatsApp:~~` to students. Found by
    looking at the render; kept here so the next missed call site fails a test instead.
    """
    data = {**hub.example_data(lang),
            "whatsapp_phone": "+52 614 111 2222",
            "whatsapp_group_url": "https://chat.whatsapp.com/EXAMPLEONLY",
            "photo_url": "https://example.com/me.png"}
    assert "~~" not in hub.render_hub(data)


def test_the_group_button_does_not_print_its_arrow_twice():
    out = hub.render_hub({**hub.example_data("bilingual"),
                          "whatsapp_group_url": "https://chat.whatsapp.com/EXAMPLEONLY"})
    assert out.count("↗") == 1


def test_the_gloss_works_in_the_professors_own_text_in_any_language():
    """The flag picks OUR chrome's language; the professor's sentence carries its own gloss
    wherever they typed one — including in a Spanish-language page."""
    out = hub.render_hub({**hub.example_data("es"),
                          "course_tagline": "Everything here ~~Todo aquí~~"})
    assert f'<span style="{hub.SECONDARY_STYLE}">Todo aquí</span>' in out


def test_a_gloss_cannot_smuggle_in_markup():
    out = hub.render_hub({**hub.DEFAULTS, "bio": "Hi ~~<script>alert(1)</script>~~"})
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert lint(out) == []


def test_an_unclosed_gloss_is_left_as_plain_text_rather_than_eating_the_page():
    out = hub.render_hub({**hub.DEFAULTS, "bio": "Hello ~~ still going"})
    assert "still going" in out
    assert lint(out) == []


def test_the_temario_puts_the_spanish_list_on_its_own_line():
    """The one place the gloss is a block instead of an inline span: two runs of a dozen
    topics each merge into an unreadable blob otherwise."""
    out = hub.render_hub({**hub.DEFAULTS,
                          "terms": "Term 1 | Numbers · Colors ~~Números · Colores~~"})
    # Asserted by SHAPE, not by an exact style string. The first cut spelled out the whole
    # `style=` attribute and broke the day a `margin-top` was added for breathing room — a
    # cosmetic tweak failing a test about block-vs-inline teaches the next person to edit the
    # test instead of reading it. What must hold is: a BLOCK element, carrying the dimmed
    # secondary treatment, holding the Spanish and nothing else.
    spanish = re.search(r'<div style="([^"]*)">Números · Colores</div>', out)
    assert spanish, "the Spanish list must be its own <div>, never an inline span"
    assert hub.SECONDARY_STYLE in spanish.group(1)
    assert ">Numbers · Colors</div>" in out


def test_missing_photo_becomes_initials_not_a_broken_image():
    """⚠️ `<img` is asserted against the WHOLE document, comments included, and that caught a
    real one: the hand-edit note added in 2026-08-10 spelled out `<img>` while explaining the
    photo trap, which would have put an image tag on a page that has no image."""
    out = hub.render_hub({**hub.example_data("es"), "photo_url": "",
                          "professor_name": "Ada Lovelace"})
    assert "<img" not in out
    assert ">AL<" in out


def test_a_professor_with_no_name_is_not_addressed_as_blank():
    out = hub.render_hub({**hub.example_data("es"), "professor_name": ""})
    assert hub.STRINGS["es"]["professor"] in out


# ── the parsing a non-technical professor actually types ────────────────────────────────

def test_grading_rows_parse_label_percent_note():
    out = hub.render_hub({**hub.DEFAULTS,
                          "grading": "Actividades | 60 | tareas\nExamen | 40 |"})
    assert "Actividades" in out and "60%" in out and "40%" in out
    assert "width:60%" in out and "width:40%" in out


def test_a_grading_row_without_a_number_still_renders():
    """Someone will type `Participación | siempre`. That must not blow up or show `None%`."""
    out = hub.render_hub({**hub.DEFAULTS, "grading": "Participación | siempre"})
    assert "Participación" in out and "None" not in out


def test_a_percentage_over_100_is_clamped():
    out = hub.render_hub({**hub.DEFAULTS, "grading": "Todo | 600 |"})
    assert "width:100%" in out and "width:600%" not in out


def test_extra_pipes_do_not_break_a_row():
    assert hub._rows("A | b | c | d | e", 3, 6) == [["A", "b", "c"]]


def test_rows_are_capped_so_one_paste_cannot_swamp_the_page():
    many = "\n".join(f"Parcial {i} | tema" for i in range(1, 30))
    out = hub.render_hub({**hub.DEFAULTS, "terms": many})
    assert "Parcial 8" in out and "Parcial 9" not in out


def test_warnings_flag_weights_that_do_not_total_100():
    notes = hub.validate({**hub.example_data("es"), "grading": "A | 60 |\nB | 10 |"})
    assert any("70%" in n for n in notes)
    assert not any("%," in n for n in hub.validate(hub.example_data("es")))


# ── merging the two scopes ──────────────────────────────────────────────────────────────

def test_the_course_overrides_the_profile_but_a_blank_box_does_not_wipe_it():
    merged = hub.resolve({"whatsapp_phone": "+52 614 111 2222", "institution": "UACH"},
                         {"institution": "FCCF", "whatsapp_phone": ""})
    assert merged["institution"] == "FCCF"
    assert merged["whatsapp_phone"] == "+52 614 111 2222"


def test_the_course_title_defaults_to_the_group_when_left_empty():
    course = Course(group_code="1-LED-A", subject="INGLES I", level=1, semester_id=1)
    assert hub.resolve({}, {}, course=course)["course_title"] == "INGLES I · 1-LED-A"
    assert hub.resolve({}, {"course_title": "Inglés I (A1)"},
                       course=course)["course_title"] == "Inglés I (A1)"


def test_an_unknown_theme_or_language_falls_back_instead_of_crashing():
    out = hub.render_hub({**hub.example_data("es"), "theme": "chartreuse", "lang": "fr"})
    assert lint(out) == []
    assert hub.STRINGS["es"]["content"] in out


# ── republishing edits the page instead of stacking a second one ────────────────────────

def test_the_marker_is_stable_across_every_edit():
    """publish.py finds a block by this marker and updates it in place. If it varied with the
    content, every save would leave another copy of the hub on the course page."""
    a = hub.render_hub(hub.example_data("es"))
    b = hub.render_hub({**hub.example_data("en"), "theme": "wine"})
    assert find_marker(a) == find_marker(b) == hub.BLOCK_ID


# ── storage: the scope split is enforced where it is written, not only in the form ───────

#: The address `conftest.sign_in` signs in as by default. The fixture below has to give the
#: course to *this* professor or the hub routes will refuse it — see the docstring.
SIGNED_IN = "professor@uach.mx"


@pytest.fixture
def course(session: Session) -> Course:
    """A course **owned by the professor the test client signs in as.**

    🔴 The `professor_id=...` line is not decoration. Until 2026-08-14 this fixture built an
    unowned course and the three route tests below passed — because `routes_hub` reached it
    with `sess.get(Course, course_id)` and never asked whose it was. When those routes were
    scoped, the tests went red with `{"detail":"No such course."}`, which is the correct answer
    to *"may this professor edit a course belonging to nobody?"*

    Worth keeping in mind next time a green test turns red after a security fix: **the test was
    asserting the leak.** The fix here is to give the fixture an owner, never to relax the route.
    """
    uid = uuid4().hex[:6]
    sem = Semester(name=f"hub-{uid}", is_active=False,
                   starts_on=date(2026, 8, 10), ends_on=date(2026, 12, 18))
    session.add(sem)
    session.commit()
    session.refresh(sem)
    prof = get_or_create(session, email=SIGNED_IN, full_name="the owner")
    c = Course(group_code=f"1-LED-{uid}", subject="INGLES I", level=1, semester_id=sem.id,
               professor_id=prof.id)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def test_a_course_form_can_never_write_a_profile_field(session: Session, course: Course):
    """The bug this whole refactor exists to prevent: a per-course copy of the phone."""
    hub_store.save_course(session, course.id, {"course_title": "Inglés I",
                                               "whatsapp_phone": "+52 614 000 0000"})
    stored = hub_store.load_course(session, course.id)
    assert stored["course_title"] == "Inglés I"
    assert "whatsapp_phone" not in stored


def test_a_profile_form_can_never_write_a_course_field(session: Session):
    owner = f"test:{uuid4().hex[:6]}"
    hub_store.save_profile(session, {"whatsapp_phone": "+52 614 000 0000",
                                     "course_title": "Inglés I"}, owner)
    stored = hub_store.load_profile(session, owner)
    assert stored["whatsapp_phone"] == "+52 614 000 0000"
    assert "course_title" not in stored


def test_one_profile_edit_reaches_every_course(session: Session, course: Course):
    owner = f"test:{uuid4().hex[:6]}"
    hub_store.save_profile(session, {"whatsapp_phone": "+52 614 111 2222"}, owner)
    hub_store.save_course(session, course.id, {"course_title": "Inglés I"})
    merged = hub_store.load_merged(session, course, owner)
    assert merged["whatsapp_phone"] == "+52 614 111 2222"
    assert merged["course_title"] == "Inglés I"

    hub_store.save_profile(session, {"whatsapp_phone": "+52 614 333 4444"}, owner)
    assert hub_store.load_merged(session, course, owner)["whatsapp_phone"] == "+52 614 333 4444"


def test_saving_twice_updates_the_same_row(session: Session, course: Course):
    hub_store.save_course(session, course.id, {"course_title": "A"})
    hub_store.save_course(session, course.id, {"course_title": "B"})
    assert hub_store.load_course(session, course.id)["course_title"] == "B"


def test_a_corrupt_blob_renders_an_empty_form_instead_of_a_500(session: Session,
                                                               course: Course):
    from musai.models import CourseHub
    hub_store.save_course(session, course.id, {"course_title": "A"})
    row = session.exec(
        __import__("sqlmodel").select(CourseHub).where(CourseHub.course_id == course.id)
    ).first()
    row.data_json = "{not json"
    session.add(row)
    session.commit()
    assert hub_store.load_course(session, course.id) == {}


def test_default_owner_matches_the_app_actor():
    """Two constants for the same professor is how a saved profile goes missing."""
    from musai.assistant.agent import ACTOR
    assert hub_store.DEFAULT_OWNER == ACTOR


# ── the form itself ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(session: Session, monkeypatch, sign_in):
    from fastapi.testclient import TestClient

    # `app` first: every routes_* module imports `templates` back out of it, so importing a
    # route module before the app leaves it half-built when app.py registers the routers.
    from musai.web.app import app
    from musai.web import routes_hub
    monkeypatch.setattr(routes_hub, "engine", session.get_bind())
    # Signed in, because the cockpit is behind the gate as of 2026-08-13. Without this the
    # client follows a 303 to the landing page and every assertion below reads that instead —
    # including "a missing course is a 404", which quietly became a 200.
    return sign_in(TestClient(app))


def test_the_form_shows_both_scopes_clearly(client, course: Course):
    body = client.get(f"/courses/{course.id}/hub").text
    assert "se escribe una vez" in body and course.group_code in body
    for field in hub.FIELDS:
        assert f'name="{field.key}"' in body, f"{field.key} has no box in the form"


def test_an_empty_form_can_be_filled_from_an_example(client, course: Course):
    body = client.get(f"/courses/{course.id}/hub?example=en").text
    assert "Everything important in one place" in body


def test_saving_shows_the_page_back_with_the_new_values(client, course: Course):
    payload = {f.key: "" for f in hub.FIELDS}
    payload.update(professor_name="Ada Lovelace", whatsapp_phone="+52 614 111 2222",
                   course_title="Inglés I", lang="es", theme="green")
    body = client.post(f"/courses/{course.id}/hub/save", data=payload).text
    assert "Guardado" in body and "+52 614 111 2222" in body

    reopened = client.get(f"/courses/{course.id}/hub").text
    assert 'value="Ada Lovelace"' in reopened


def test_a_missing_course_is_a_404_not_a_traceback(client):
    assert client.get("/courses/999999/hub").status_code == 404


# ── the TEMPLATE profile: the real card, with obviously fake values in it ────────────────

TEMPLATE = {**hub.DEFAULTS, **hub.placeholder_profile(), "lang": "bilingual",
            "course_title": "English II (A2)", "content_badge": "A2",
            "grading": "Activities | 60 | Weekly tasks\nExam | 40 | Term exam",
            "where_to_find": "Grades | Moodle → Grades"}


def test_a_template_shows_the_ORDINARY_page_not_a_special_notice():
    """🔴 the owner, 2026-08-11, rejecting the alternative. A course meant to be copied to another
    professor must render the page their students will eventually see — professor card, contact
    card and all — with `Nombre Apellido` and `+52 614 000 0000` sitting where the real values
    go. The version this replaced dropped both cards and drew a separate "three things to fill
    in" panel instead, which showed the receiving professor a layout that was not the one they
    were inheriting."""
    page = hub.render_hub(TEMPLATE)
    for fragment in ("Meet your professor", "Help &amp; contact", hub.PLACEHOLDER_NAME,
                     "+52 614 000 0000", "wa.me/526140000000"):
        assert fragment in page, f"{fragment!r} is missing from the template page"


def test_the_template_profile_is_literal_and_never_read_from_the_database():
    """The whole safety property. `placeholder_profile()` is pure, so a template physically
    cannot pick up a real professor's name, photo or phone from `hub_store`."""
    src = inspect.getsource(hub.placeholder_profile)
    assert "load_profile" not in src and "Session" not in src and "engine" not in src
    assert hub.placeholder_profile() == hub.placeholder_profile()


def test_a_template_never_carries_a_photo():
    """An initials avatar reads as unfinished. Somebody else's face does not — and a `data:`
    URI would travel through backup->restore into every copy."""
    assert hub.placeholder_profile()["photo_url"] == ""
    assert 'src="data:' not in hub.render_hub(TEMPLATE)


def test_the_placeholder_phone_is_obviously_fake_but_still_renders_as_a_link():
    """It has to look like the real thing to be worth replacing, and it has to be unmistakably
    not-real so nobody ships it. `000 0000` is both."""
    assert "000 0000" in hub.placeholder_profile()["whatsapp_phone"]
    assert "wa.me/526140000000" in hub.render_hub(TEMPLATE)


def test_the_bio_is_not_typed_twice():
    """Derived from `example_data("bilingual")`, per the module's own "typed once" rule — two
    copies of a bio is how one of them goes stale."""
    assert hub.placeholder_profile()["bio"] == hub.example_data("bilingual")["bio"]


def test_the_template_page_is_still_moodle_safe():
    hub.render_hub_checked(TEMPLATE)          # raises if render.lint flags anything
