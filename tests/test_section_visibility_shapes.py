"""The section hide/show link finder, run against the hrefs both Moodles really render.

🔴 Why this file executes the JS instead of grepping it. `structure.py`'s section-visibility
finder used to REQUIRE `section=<n>` in the href. That was measured on Moodle 3.3 and is
correct there. Moodle 4.5 (aulas1) renders the same control without it:

    3.3   /course/view.php?id=9048&section=10&sesskey=…&show=10
    4.5   /course/view.php?id=6253&sesskey=…&hide=1              ← no section=N
    both  /course/mod.php?sesskey=…&hide=<cmid>                  ← an ACTIVITY, not a section

So on aulas1 every section hide refused with "no link found" — a refusal that names Moodle
while describing our own selector. The old test asserted the substring `'[?&]section=' + n`
was present, so it passed for exactly as long as the bug existed: **a source-substring check
cannot tell a requirement from a defect.** These run the shipped string against a real DOM and
ask what it decides.

`page.set_content()` only — no navigation, no network, no credentials.
"""

import pytest

from musai.coursebuild import structure

pytestmark = pytest.mark.usefixtures()


@pytest.fixture(scope="module")
def evaluate_js():
    """Run one of structure.py's JS snippets against a fixed DOM, in a real engine."""
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="playwright not installed").sync_playwright
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:                                # pragma: no cover - env-dependent
        pytest.skip(f"chromium unavailable: {exc}")

    page = browser.new_page()

    def run(html: str, js: str, arg):
        page.set_content(html)                              # local only; never navigates
        return page.evaluate(js, arg)

    yield run
    browser.close()
    pw.stop()


# The activity control and the section controls as Moodle 4.5 renders them, measured
# 2026-08-22 on course 6253 with editing on. `Avisos` is cmid 6252 — note that it collides
# numerically with a *course* id, which is exactly the sort of coincidence the mods guard is
# there for.
DOM_45 = """
<div id="section-0"><h3 class="sectionname">General</h3>
  <div id="module-6252">
    <a href="https://aulas1.uach.mx/course/mod.php?sesskey=K&hide=6252">Ocultar</a>
  </div>
</div>
<div id="section-1"><h3 class="sectionname">Nueva secci&oacute;n</h3></div>
<a href="https://aulas1.uach.mx/course/view.php?id=6253&sesskey=K&hide=1">Ocultar</a>
<a href="https://aulas1.uach.mx/course/view.php?id=6253&sesskey=K&hide=2">Ocultar</a>
"""

# Moodle 3.3 as virtual3 renders it, measured 2026-08-11 on 9048 §10 (already hidden, so it
# offers *show*).
DOM_33 = """
<div id="section-10"><h3 class="sectionname">PARTIAL TEST 1</h3></div>
<div id="module-7712345">
  <a href="https://virtual3.uach.mx/course/mod.php?sesskey=K&show=7712345">Mostrar</a>
</div>
<a href="https://virtual3.uach.mx/course/view.php?id=9048&section=10&sesskey=K&show=10">M</a>
"""


def test_finds_the_45_section_link_that_has_no_section_parameter(evaluate_js):
    """The regression. 4.5's href carries only `hide=1`; the finder must still match it."""
    got = evaluate_js(DOM_45, structure._FIND_SECTION_VISIBILITY_LINK_JS, [1, "hide"])
    assert got is not None, "4.5 section link not found — the 3.3-only selector is back"
    assert "course/view.php" in got and "hide=1" in got


def test_still_finds_the_33_section_link_that_does(evaluate_js):
    """Fixing 4.5 must not un-fix 3.3 — seven live courses still run on it."""
    got = evaluate_js(DOM_33, structure._FIND_SECTION_VISIBILITY_LINK_JS, [10, "show"])
    assert got is not None and "section=10" in got and "show=10" in got


def test_an_activitys_link_is_never_returned_as_a_sections(evaluate_js):
    """🔴 The failure this guard exists for: picking the activity control here would hide a
    student's activity instead of a tab, and report success. `Avisos` is cmid 6252 and lives
    on mod.php; asking for section 6252 must find nothing."""
    got = evaluate_js(DOM_45, structure._FIND_SECTION_VISIBILITY_LINK_JS, [6252, "hide"])
    assert got is None


def test_a_shown_section_reads_as_shown(evaluate_js):
    """Moodle offers *Ocultar* for a visible section, so the offer IS the state."""
    assert evaluate_js(DOM_45, structure._READ_SECTION_VISIBLE_JS, 1) == structure.VISIBLE_SHOWN


def test_a_hidden_section_reads_as_hidden(evaluate_js):
    assert evaluate_js(DOM_33, structure._READ_SECTION_VISIBLE_JS, 10) == structure.VISIBLE_HIDDEN


def test_a_section_with_no_control_at_all_is_unknown_not_visible(evaluate_js):
    """Neither link ⇒ null ⇒ the caller refuses. Defaulting to 'visible' here would let a
    hide report success against a section it never touched."""
    assert evaluate_js(DOM_45, structure._READ_SECTION_VISIBLE_JS, 9) is None


def test_the_activity_control_does_not_leak_into_the_section_state_read(evaluate_js):
    """Section 6252 does not exist; only the activity cmid 6252 does. If the path guard were
    dropped, this would read as SHOWN and a hide would be aimed at the forum."""
    assert evaluate_js(DOM_45, structure._READ_SECTION_VISIBLE_JS, 6252) is None


# ── the single-section view, which looks right and is not ────────────────────
#
# Measured 2026-08-22 on aulas1 course 6253 (Moodle 4.5, `topics`), same account, same
# moment, by `scratchpad/probe_45_section_view.py`:
#
#   /course/view.php?id=6253&sesskey=…&edit=on            5 sections · 9 editsection links
#                                                          · hide=N present for every section
#   /course/view.php?id=6253&sesskey=…&edit=on&section=1  1 section  · 0 editsection links
#                                                          · 0 hide/show links
#
# 🔴 `#section-N` is present in BOTH. The obvious check — "is the section on this page" —
# cannot tell them apart, and `editing_on(section=N)` navigated into the stripped one by
# default. It cost a dry run five failures (the whole banner write and all four section
# hides), each surfacing as a refusal that named Moodle while describing our own navigation.

FULL_PAGE_45 = """
<div id="section-0"><h3 class="sectionname">General</h3>
  <a href="https://aulas1.uach.mx/course/editsection.php?id=31258&sr=0">Editar</a>
</div>
<div id="section-1"><h3 class="sectionname">Nueva secci&oacute;n</h3>
  <a href="https://aulas1.uach.mx/course/editsection.php?id=31259&sr=1">Editar</a>
</div>
<a href="https://aulas1.uach.mx/course/view.php?id=6253&sesskey=K&hide=1">Ocultar</a>
"""

# The trap: the container is here, everything that acts on it is not.
SINGLE_SECTION_VIEW_45 = """
<div id="section-1"><h3 class="sectionname">Nueva secci&oacute;n</h3></div>
"""


def test_the_full_page_is_recognised_as_editable(evaluate_js):
    from musai.coursebuild.publish import _SECTION_IS_EDITABLE_JS
    got = evaluate_js(FULL_PAGE_45, _SECTION_IS_EDITABLE_JS, 1)
    assert got["present"] and got["editable"]
    assert got["has_edit_link"] and got["has_visibility_toggle"]


def test_the_single_section_view_is_present_but_NOT_editable(evaluate_js):
    """The regression that matters: `present` is True and `editable` is False, and only the
    second one is the right question to gate on."""
    from musai.coursebuild.publish import _SECTION_IS_EDITABLE_JS
    got = evaluate_js(SINGLE_SECTION_VIEW_45, _SECTION_IS_EDITABLE_JS, 1)
    assert got["present"] is True, "the container IS rendered — that is the trap"
    assert got["editable"] is False, (
        "the stripped single-section view was judged editable; editing_on will stay on a "
        "page with no controls and every section write will refuse")


def test_a_section_absent_from_the_page_is_neither(evaluate_js):
    from musai.coursebuild.publish import _SECTION_IS_EDITABLE_JS
    got = evaluate_js(FULL_PAGE_45, _SECTION_IS_EDITABLE_JS, 9)
    assert got["present"] is False and got["editable"] is False


def test_editing_on_prefers_the_full_page_and_falls_back_only_when_needed():
    """`&section=N` exists for onetopic on 3.3, where only the displayed tab is in the DOM.
    It must be the fallback, reached after measuring, never the default."""
    import inspect
    from musai.coursebuild.publish import editing_on
    src = inspect.getsource(editing_on)
    body = src[src.index("sesskey = "):]
    first_goto = body.index("vpage.goto(base")
    section_goto = body.index('f"{base}&section={section}"')
    assert first_goto < section_goto, "the per-section URL is being tried first"
    assert "_SECTION_IS_EDITABLE_JS" in body, "the fallback is not gated on a measurement"
    assert body.index("_SECTION_IS_EDITABLE_JS") < section_goto
