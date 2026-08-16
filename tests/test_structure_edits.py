"""Tests for `musai/coursebuild/structure.py` — renaming a tab, hiding an activity.

The browser work is proven live; what is pinned here are the boundaries that must survive
somebody editing this module later, and the two JS snippets whose *shape* encodes a trap.
"""

import inspect

import pytest

from musai.coursebuild import structure
from musai.coursebuild.structure import StructureRefused, rename_section


def test_a_blank_tab_name_is_refused_before_a_browser_opens():
    with pytest.raises(StructureRefused, match="name"):
        rename_section(idc="9023", section=2, new_name="   ")


def test_there_is_no_delete_in_this_module():
    """The owner's rule for REMOVE: prefer hide over delete, and build deletion last and paranoid.
    Hiding keeps every submission and grade behind the activity; deleting does not."""
    names = [n for n, _ in inspect.getmembers(structure, inspect.isfunction)]
    assert not [n for n in names if "delete" in n or "remove" in n]
    assert "delete=" not in inspect.getsource(structure)


def test_there_is_no_section_MOVE_in_this_module():
    """A move renumbers every later section, and both `Activity.partial_id` and the Cronograma's
    manual tab overrides are keyed by section number — it would surface as a wrong grade."""
    names = [n for n, _ in inspect.getmembers(structure, inspect.isfunction)]
    assert not [n for n in names if "move" in n]


def test_the_name_customize_checkbox_is_CLICKED_not_assigned():
    """Moodle disables the name input while "use default name" is ticked, and a disabled input
    is never submitted — so `.checked = true` renames nothing and reports success. Same trap
    as the Cronograma's date-enable checkboxes."""
    src = structure._SET_SECTION_NAME_JS
    assert "box.click()" in src
    assert ".checked =" not in src
    assert "still disabled" in src, "the disabled case must be an explicit refusal"


def test_the_real_field_names_are_the_bracketed_ones():
    """Measured 2026-08-08: the form uses `name[value]` / `name[customize]`, not the
    underscore names the convention suggests. The first dry run wrote the right field only
    because of an `#id_name_value` fallback, while the READ — which had no fallback — came
    back empty. The mismatch is what exposed it."""
    for src in (structure._SET_SECTION_NAME_JS, structure._READ_SECTION_NAME_JS):
        assert 'name[value]' in src
        assert 'name[customize]' in src


def test_the_customize_checkbox_is_selected_by_TYPE_not_by_name_alone():
    """`name[customize]` matches two elements: a hidden `value=0` that posts when the box is
    unticked, and the real checkbox. `querySelector` returns the hidden one, and a hidden input
    cannot be clicked — so the write would quietly not happen."""
    assert 'input[type="checkbox"][name="name[customize]"]' in structure._NAME_FIELDS_JS


def test_reading_the_name_back_is_what_makes_the_rename_verifiable():
    """A rename to the name it already has is a no-op, not a write."""
    src = inspect.getsource(structure.rename_section)
    assert "_READ_SECTION_NAME_JS" in src
    assert "Already named that" in src


def test_the_visibility_link_is_READ_off_the_page_never_built():
    """The href carries a per-session sesskey and the path has moved between Moodle versions."""
    src = structure._FIND_VISIBILITY_LINK_JS
    assert "querySelectorAll('a[href]')" in src
    assert "sesskey=" not in src, "building the URL would hardcode a session key"


def test_hiding_verifies_by_re_reading_the_course_page():
    """A navigation succeeds whether or not Moodle honoured it, so the click is not evidence."""
    src = inspect.getsource(structure.set_activity_visibility)
    assert "_ACTIVITY_STATE_JS" in src
    assert 'out["after"]' in src
    assert "still reads" in src, "a visibility change that did not take must raise"


def test_acting_on_a_cmid_the_section_does_not_show_is_refused():
    src = inspect.getsource(structure.set_activity_visibility)
    assert "a wrong cmid is a wrong activity" in src


def test_the_section_form_is_submitted_by_id_not_by_position():
    """This form's first submit is the course SEARCH box — the third page in this project with
    that shape, after the delete-confirm and the restore page."""
    src = inspect.getsource(structure.rename_section)
    assert '"#id_submitbutton"' in src
    assert ".first.click" in src


def test_renaming_never_touches_the_section_summary():
    """The summary IS the course home page (`publish_section.py` owns it, with a rail that
    refuses to overwrite a professor's hand-written one). A rename that also wrote the summary
    would walk straight past that rail, so this module must not have an editor writer at all."""
    src = inspect.getsource(structure.rename_section)
    for editor_call in ("tinyMCE", "setContent", "introeditor", "summary_editor"):
        assert editor_call not in src, f"rename_section must not touch {editor_call}"
    # The only field it writes is the tab name.
    assert "name_value" in structure._SET_SECTION_NAME_JS
    assert "summary" not in structure._SET_SECTION_NAME_JS


def test_visibility_is_read_from_the_settings_form_not_the_course_page():
    """🔴 The bug this pins actually happened: this Moodle renders no class or badge that
    separates a hidden activity from a visible one for a teacher in editing mode, so a
    course-page check reported three *successful* hides as failures. A verification that cries
    wolf costs the same trust as one that misses a real fault."""
    assert "select[name=\"visible\"]" in structure._MODEDIT_VISIBLE_JS
    assert "hidden" not in structure._ACTIVITY_STATE_JS, (
        "the course-page reader must not claim to know hidden/shown")
    src = inspect.getsource(structure.set_activity_visibility)
    assert "_read_visible" in src


def test_the_course_page_read_still_proves_the_cmid_is_in_that_section():
    """Dropping the hidden/shown guess must not drop the "is this the right activity" check."""
    src = inspect.getsource(structure.set_activity_visibility)
    assert "_ACTIVITY_STATE_JS" in src
    assert "a wrong cmid is a wrong activity" in src


def test_the_visibility_link_is_looked_for_on_the_COURSE_page_not_the_settings_form():
    """🔴 The regression that ate a live run (2026-08-09).

    `_read_visible` navigates to `modedit.php` to get the authoritative visibility — and leaves
    the page there. The show/hide link lives in the course page's action menu, so without an
    `editing_on` in between, the lookup runs on the settings form and refuses every time with
    *"No show=<cmid> link on the page"*.

    That refusal was on record as an open question about **two courses behaving differently**.
    They do not: the code was looking at the wrong page, and only the runs that no-oped early
    (activity already in the wanted state) ever passed. Pinned by ordering, because the failure
    is entirely one of ordering.
    """
    import ast

    tree = ast.parse(inspect.getsource(structure.set_activity_visibility))
    read_at = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_read_visible"]
    editing_at = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "editing_on"]
    lookup_at = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "evaluate"
                 and any(isinstance(a, ast.Name) and a.id == "_FIND_VISIBILITY_LINK_JS"
                         for a in n.args)]
    assert read_at and lookup_at, "the function no longer has the shape this test describes"
    first_read, lookup = min(read_at), max(lookup_at)
    assert any(first_read < e < lookup for e in editing_at), (
        "set_activity_visibility reads visibility from modedit.php and then looks for the "
        "action-menu link without returning to the course page")


# ── revealing a whole tab ────────────────────────────────────────────────────────────────

def test_a_section_cannot_be_revealed_without_naming_it():
    """Section numbers shift under every insert and delete, so a bare number cannot identify
    a tab — and revealing the wrong one publishes something a professor deliberately hid."""
    with pytest.raises(StructureRefused, match="expect_name"):
        structure.set_section_visibility(idc="9048", section=10, visible=True, expect_name="")


def test_the_section_link_finder_requires_the_number_to_agree_with_itself():
    """🔴 An activity's hide/show link and a section's live on the same page and differ only
    in what the id means. The finder demands `section=<n>` AND `hide|show=<n>` in the same
    href, and rejects an id that matches a module on the page — three independent checks,
    because picking an activity's link here would hide a student's assignment instead of a
    tab, and report success."""
    js = structure._FIND_SECTION_VISIBILITY_LINK_JS
    assert "m[1] !== String(n)" in js
    assert "'[?&]section=' + n" in js
    assert "mods.has(m[1])" in js


def test_section_visibility_is_read_from_which_link_moodle_offers():
    """There is no `visible` control on `editsection.php` at all (measured 2026-08-11 on
    9048: name, summary, the onetopic tab styling, availability — and nothing else). So the
    state is which of hide/show Moodle renders. Both present, or neither, returns null and
    the caller refuses — the alternative is inferring visibility from styling, which is the
    mistake `_ACTIVITY_STATE_JS` was written to stop making."""
    js = structure._READ_SECTION_VISIBLE_JS
    assert "canHide && !canShow" in js and "canShow && !canHide" in js
    assert "return null" in js


def test_the_dry_run_never_follows_the_section_link():
    """For a section the GET *is* the mutation — the same shape as the section-delete URL one
    module over. So the dry run stops at "the link exists" and issues nothing."""
    src = inspect.getsource(structure.set_section_visibility)
    dry = src[src.index("if dry_run:"):]
    assert "vpage.goto" not in dry.split("return out", 1)[0]
