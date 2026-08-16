"""Tests for `musai/coursebuild/remove.py` — the only operation in this project with no undo.

Two kinds of test here:

* **Pure logic** — `parse_submitted_count`, the locale handling that decides whether a delete
  is allowed to proceed. Testable without a browser, so it is tested exhaustively.
* **AST invariants** — the rails that are about *ordering*, which a docstring cannot express
  and a source grep cannot honestly check. ⚠️ One test elsewhere in this suite once passed
  because it matched **its own docstring** (COURSE_EDITING §6), so nothing here greps prose:
  the checks below walk the parsed function body and compare line numbers.
"""

import ast
import inspect

import pytest

from musai.coursebuild import remove, structure
from musai.coursebuild.remove import (
    DeleteRefused, LABEL_QUOTE_MIN, NO_USER_CONTENT, delete_activity, delete_section,
    label_identity_matches, parse_quiz_attempts, parse_submitted_count,
)

# The exact text `mod/quiz/report.php?id=1119434&mode=overview` returned on 9067, 2026-08-12
# (`scratchpad/probe_quiz_attempts.json`). Kept verbatim, including the Spanish, because the
# count line is the only thing on that page that carries the number.
_QUIZ_REPORT_ES = (
    "Simple Present III\n"
    "Intentos: 0\n"
    "Colapsar todo\n"
    "Qué incluir en el reporte\n"
    "Intentos de\n"
    "usuarios inscritos que han intentado este examen\n"
)

# The real §1 label of 9048 (2ED-B INGLES II), read off its own settings form 2026-08-10.
# Kept verbatim: the rail exists to tell THIS label apart from the three beside it.
_COURSE_OVERVIEW = (
    "First Term (Parcial 1) Imperatives (directions) Present Simple vs Present Continuous "
    "Adverbs of Frequency Can / Can’t (ability) Going to (plans / intentions) Will "
    "(basic use: decisions / promises) Second Term (Parcial 2) Countable / Uncountable nouns"
)


# --- the measured shape ------------------------------------------------------------------

def test_the_spanish_grading_summary_measured_live_reads_as_zero():
    """1-LED-A, cmid 1060306 (TypeRacer), 2026-08-09 — the exact rows that page returned."""
    rows = [["Participantes", "10"], ["Enviados", "0"], ["Necesita calificarse", "0"],
            ["Fecha de entrega", "Sunday, 22 de November de 2026, 23:55"],
            ["Tiempo restante", "105 días 5 horas"]]
    assert parse_submitted_count(rows) == 0


def test_an_english_moodle_reads_the_same_row():
    assert parse_submitted_count([["Participants", "10"], ["Submitted", "3"]]) == 3


def test_several_matching_rows_resolve_to_the_one_that_refuses():
    """With drafts AND submissions on the page, the safe answer is the larger — the count is
    used only to decide whether to refuse, so erring upward errs toward keeping the work."""
    rows = [["Drafts (not submitted)", "4"], ["Submitted", "1"]]
    assert parse_submitted_count(rows) == 4


def test_a_summary_that_names_no_submission_count_is_unmeasurable_not_empty():
    """None and 0 must stay distinguishable: `delete_activity` refuses on both, and it refuses
    for different reasons. Collapsing them is how a deleter learns to report a guess as a fact.
    """
    assert parse_submitted_count([["Participantes", "10"]]) is None
    assert parse_submitted_count([]) is None


def test_a_non_numeric_value_is_not_read_as_a_count():
    assert parse_submitted_count([["Enviados", "—"]]) is None
    assert parse_submitted_count([["Enviados", "0"], ["Entregados", "n/a"]]) == 0


def test_a_row_with_only_one_cell_is_ignored_rather_than_indexed():
    assert parse_submitted_count([["Enviados"], ["Submitted", "2"]]) == 2


# --- identifying a LABEL, which has no name to compare -------------------------------------

# --- the quiz probe: three readings that must agree before a zero is believed --------------

def test_the_real_quiz_report_measured_live_reads_as_zero():
    """9067 cmid 1119434, 2026-08-12 — the page text, link count and row count as captured."""
    assert parse_quiz_attempts(_QUIZ_REPORT_ES, 0, 0)[0] == 0


def test_an_english_moodle_reads_the_same_line():
    assert parse_quiz_attempts("Exam 1\nAttempts: 12\nCollapse all", 12, 12)[0] == 12


def test_a_report_that_prints_no_count_is_unmeasurable_not_empty():
    """The failure this guards is a page that did not finish loading, or a permissions notice.
    Both render zero links and zero rows — i.e. they look exactly like an empty quiz."""
    count, why = parse_quiz_attempts("No tienes permiso para ver esta página", 0, 0)
    assert count is None
    assert "Intentos" in why


def test_a_zero_count_contradicted_by_the_page_refuses_instead_of_choosing():
    """🔴 The whole reason the probe reads three things. If the printed count says nothing is
    there but the page renders attempt rows, the honest answer is 'I cannot tell'."""
    assert parse_quiz_attempts(_QUIZ_REPORT_ES, 3, 3)[0] is None
    assert parse_quiz_attempts(_QUIZ_REPORT_ES, 0, 1)[0] is None
    assert parse_quiz_attempts(_QUIZ_REPORT_ES, 1, 0)[0] is None


def test_the_count_line_wins_when_it_is_non_zero_because_the_links_can_undercount():
    """The overview report pages. 40 attempts render 20 review links on page one, so the links
    may be *lower* than the truth — and a delete rail must never round down."""
    count, why = parse_quiz_attempts("Attempts: 40\n", 20, 20)
    assert count == 40
    assert "count" in why


def test_the_count_is_matched_on_its_own_line_not_anywhere_in_the_prose():
    """`Intentos de` and `Los intentos que hay` are both on that page, and an unanchored
    pattern would happily read a number out of the filter form below them."""
    text = "Intentos de\nusuarios inscritos, hayan o no intentado este examen\nIntentos: 0\n"
    assert parse_quiz_attempts(text, 0, 0)[0] == 0
    assert parse_quiz_attempts("Intentos de 25 usuarios\n", 0, 0)[0] is None


def test_the_quiz_probe_is_wired_into_the_content_count():
    """A pure parser nothing calls would pass every test above and still refuse every delete."""
    src = inspect.getsource(remove._count_user_content)
    assert 'modname == "quiz"' in src
    assert "parse_quiz_attempts" in src
    # 🔴 mod/quiz/view.php shows a *student's own* attempts, so for a professor it reads empty
    # on a quiz the whole class has sat. The teacher-side record is the overview report.
    assert "report.php" in src and "mode=overview" in src


def test_a_label_is_identified_by_quoting_its_own_text():
    ok, why = label_identity_matches(_COURSE_OVERVIEW, "First Term (Parcial 1) Imperatives")
    assert ok, why


def test_the_quote_may_differ_in_whitespace_and_case_but_not_in_words():
    """The text is read out of HTML, so line breaks and runs of spaces are an artefact of the
    markup, not of the label. Wording is not."""
    ok, _ = label_identity_matches(_COURSE_OVERVIEW, "adverbs   of\n  frequency  can / can’t")
    assert ok
    ok, why = label_identity_matches(_COURSE_OVERVIEW, "Adverbs of Frequency Can / Cannot")
    assert not ok and "does not appear" in why


def test_a_short_quote_is_refused_even_when_it_genuinely_matches():
    """🔴 The rail this stands in for is EQUALITY. A substring match is weaker by construction,
    so the floor is what keeps it from being permissive: 'First Term' really is in the label,
    and is still refused — it is also in the other three, and in half the course."""
    ok, why = label_identity_matches(_COURSE_OVERVIEW, "First Term")
    assert not ok
    assert str(LABEL_QUOTE_MIN) in why


def test_a_label_with_no_readable_text_cannot_be_identified_so_is_refused():
    """Unmeasurable refuses, exactly like an unmeasurable submission count: an empty haystack
    would otherwise make every sufficiently long quote fail 'for the wrong reason', or — worse
    with a looser implementation — make an empty quote succeed."""
    ok, why = label_identity_matches("", "First Term (Parcial 1) Imperatives")
    assert not ok and "no intro text" in why
    ok, _ = label_identity_matches("   ", "                      ")
    assert not ok


def test_the_label_path_never_falls_back_to_the_name_comparison():
    """AST, not prose: rail 1 must branch on modulename == 'label' BEFORE the equality check,
    or a label (whose name is None) is compared to a non-empty expect_name and always refuses.
    That failure mode is silent — it reads as 'wrong cmid' forever."""
    src = inspect.getsource(delete_activity)
    tree = ast.parse(src)   # module-level def, so it parses at column 0 as-is
    calls = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "label_identity_matches"]
    equality = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Compare) and any(isinstance(o, ast.NotEq) for o in n.ops)
                and "expect_name" in ast.dump(n)]
    assert calls, "delete_activity no longer consults label_identity_matches"
    assert equality, "the name-equality rail is gone entirely"
    assert min(calls) < min(equality), (
        f"label check at line {min(calls)} must come before the name equality at "
        f"{min(equality)}")


def test_what_a_label_delete_destroys_is_recorded_before_it_is_destroyed():
    """A label's text is its entire content, so 'what exactly did we destroy?' is answerable
    only if the intro was captured into the audit detail BEFORE the delete was issued."""
    src = inspect.getsource(delete_activity)
    assert 'out["intro_text"]' in src
    tree = ast.parse(src)   # module-level def, so it parses at column 0 as-is
    captures = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assign)
                and 'intro_text' in ast.dump(n.targets[0])]
    clicks = _call_lines(delete_activity, "click")
    assert captures, "the intro is never recorded"
    assert not clicks or min(captures) < min(clicks), (
        "the intro must be captured before anything is clicked")


# --- the rails ---------------------------------------------------------------------------

def test_deleting_without_naming_the_target_is_refused_before_a_browser_opens():
    """A mistyped cmid loads a perfectly valid form for the WRONG activity, and every later
    check would pass. `expect_name` is the only thing that turns that into a loud failure."""
    with pytest.raises(DeleteRefused, match="expect_name"):
        delete_activity(idc="9023", section=2, cmid="1060265", expect_name="  ")
    with pytest.raises(DeleteRefused, match="expect_name"):
        delete_section(idc="9023", section=7, expect_name="")


def test_the_types_that_hold_student_work_are_never_assumed_empty():
    """`NO_USER_CONTENT` short-circuits the content probe. An `assign`, `forum` or `quiz` in
    that set would let a delete through without ever looking for submissions."""
    assert NO_USER_CONTENT.isdisjoint({"assign", "forum", "quiz", "workshop", "lesson"})


def test_an_unimplemented_module_type_is_unmeasurable_rather_than_empty():
    """`_count_user_content` returns (None, why) for anything it has not been taught to read,
    and None refuses. Defaulting an unknown type to zero is how this module would start lying.
    """
    src = inspect.getsource(remove._count_user_content)
    tree = ast.parse(src)
    fn = tree.body[0]
    final = fn.body[-1]
    assert isinstance(final, ast.Return), "the fall-through must be a return, not a fallthrough"
    assert isinstance(final.value, ast.Tuple)
    assert isinstance(final.value.elts[0], ast.Constant)
    assert final.value.elts[0].value is None, "unknown types must report None, never 0"


def test_delete_lives_in_its_own_module_so_importing_it_is_a_decision():
    """`structure.py` promises reversible-by-construction. Its own test asserts it holds no
    delete; this one asserts the delete did not simply get re-exported back into it."""
    assert not hasattr(structure, "delete_activity")
    assert not hasattr(structure, "delete_section")


# --- ordering: the invariant a dry run depends on ----------------------------------------

def _guard_line(fn) -> int:
    """Line number of the top-level `if dry_run:` guard inside `fn`."""
    tree = ast.parse(inspect.getsource(fn))
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                and node.test.id == "dry_run"):
            return node.lineno
    raise AssertionError(f"{fn.__name__} has no `if dry_run:` guard at all")


def _call_lines(fn, attr: str) -> list:
    tree = ast.parse(inspect.getsource(fn))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def test_a_section_dry_run_opens_no_url_at_all_after_its_guard():
    """🔴 The invariant this module exists to protect. For a SECTION the trigger GET *is* the
    deletion — there is no confirm page to fill and abandon — so every `goto` must sit after
    the dry-run guard. Measured 2026-08-09: a probe that opened that URL expecting a confirm
    page deleted 1-LED-A's §7 instead.
    """
    guard = _guard_line(delete_section)
    late = [n for n in _call_lines(delete_section, "goto") if n > guard]
    early = [n for n in _call_lines(delete_section, "goto") if n < guard]
    assert not early, f"delete_section calls goto() at line(s) {early}, before its dry-run guard"
    assert late, "the live path must still open the delete URL somewhere"


def test_an_activity_dry_run_never_reaches_the_confirm_submit():
    """For an ACTIVITY the GET only renders the confirm page and the POST is the mutation, so a
    `goto` before the guard would be harmless — but a `click` would not be."""
    guard = _guard_line(delete_activity)
    clicks = _call_lines(delete_activity, "click")
    assert clicks, "the live path must click the confirm form"
    assert all(n > guard for n in clicks), \
        f"delete_activity clicks at line(s) {[n for n in clicks if n < guard]} before its guard"


def test_the_section_delete_submits_a_confirm_form_too():
    """🔴 Measured 2026-08-12 on 9067: `editsection.php?…&delete=1` renders a confirm page, it
    does not delete. Until this was found, `delete_section` issued the GET and then honestly
    reported that nothing had happened — a writer that could not write."""
    src = inspect.getsource(delete_section)
    assert "confirm" in src and "input[type=submit]" in src
    # Same disambiguation as the activity path: that page's FIRST form is the course search box.
    assert "input[name='confirm'][value='1']" in src
    assert "input[name='delete'][value='1']" in src
    assert "input[name='id'][value=" in src


def test_the_section_delete_still_issues_nothing_on_a_dry_run():
    """The GET turned out to be harmless, and the dry run is deliberately not relaxed: two
    Moodles can differ and this one has already been misread once."""
    tree = ast.parse(inspect.getsource(delete_section))
    fn = tree.body[0]
    dry_return = max(n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and n.value.startswith("DRY RUN"))
    goto = min(n.lineno for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "goto" and n.lineno > dry_return)
    assert dry_return < goto


def test_the_confirm_form_is_selected_by_its_payload_not_by_position():
    """This Moodle puts a course SEARCH box first on the delete-confirm page — the third page
    in this project with that shape. `.first` on a bare submit selector submits the search."""
    src = inspect.getsource(delete_activity)
    assert "input[name='confirm'][value='1']" in src
    assert "input[name='delete'][value='{cmid}']" in src


def test_the_activity_delete_link_is_read_off_the_page_never_built():
    """The href carries the sesskey and the section return; both are per-session state."""
    assert "querySelectorAll('a[href]')" in remove._FIND_DELETE_LINK_JS
    src = inspect.getsource(delete_activity)
    assert "mod.php?" not in src, "delete_activity must not assemble a mod.php URL itself"


def test_a_populated_section_can_never_be_deleted_by_any_flag():
    """The one rail with no escape hatch: deleting a populated section destroys every activity
    inside it. `allow_summary` exists; there is deliberately no `allow_activities`."""
    params = inspect.signature(delete_section).parameters
    assert "allow_summary" in params
    assert not [p for p in params if "activit" in p.lower()]


def test_both_deleters_default_to_a_dry_run():
    for fn in (delete_activity, delete_section):
        assert inspect.signature(fn).parameters["dry_run"].default is True


def test_both_deleters_audit_even_when_they_refuse():
    """`_audit` sits in the `finally`, so a refusal is recorded too — an audit trail that only
    holds successes cannot answer "what did we try to destroy?"."""
    for fn in (delete_activity, delete_section):
        tree = ast.parse(inspect.getsource(fn))
        finals = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.finalbody]
        assert finals, f"{fn.__name__} has no try/finally"
        audited = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                      and c.func.id == "_audit"
                      for t in finals for stmt in t.finalbody for c in ast.walk(stmt))
        assert audited, f"{fn.__name__} does not audit from its finally block"
