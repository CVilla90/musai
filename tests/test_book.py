"""Pure-logic tests for `musai/coursebuild/book.py`.

Everything here runs without a browser, because everything that *decides* runs without one.
The AST tests at the bottom pin the two structural promises the docstring makes — that a dry
run cannot submit, and that no delete/show URL is reachable from this module — since those
are properties of the code's shape, not of any value it computes.
"""

import ast
import inspect

import pytest

from musai.coursebuild import book
from musai.coursebuild.book import (
    BookRefused, ChapterSpec, lint_chapter_html, plan_chapters, upsert_chapters,
)

YT = ('<div style="x"><iframe src="https://www.youtube-nocookie.com/embed/abc123" '
      'title="t"></iframe></div>')


# ── lint ──────────────────────────────────────────────────────────────────────
def test_a_youtube_embed_is_allowed():
    assert lint_chapter_html(YT) == []


def test_an_embed_to_any_other_host_is_refused():
    bad = '<iframe src="https://evil.example.com/x"></iframe>'
    problems = lint_chapter_html(bad)
    assert any("non-allowed host" in p and "evil.example.com" in p for p in problems)


def test_a_lookalike_host_does_not_pass_the_suffix_check():
    # `notyoutube.com` ends with "youtube.com" as a *string*; it must not pass as a subdomain.
    bad = '<iframe src="https://notyoutube.com/embed/x"></iframe>'
    assert any("non-allowed host" in p for p in lint_chapter_html(bad))


def test_a_real_youtube_subdomain_does_pass():
    ok = '<iframe src="https://www.youtube.com/embed/x"></iframe>'
    assert lint_chapter_html(ok) == []


def test_an_iframe_without_a_src_is_refused():
    assert any("without a src" in p for p in lint_chapter_html("<iframe></iframe>"))


@pytest.mark.parametrize("html,needle", [
    ("<script>x</script>", "<script>"),
    ("<style>a{}</style>", "<style>"),
    ('<link rel="x">', "<link>"),
    ('<div onclick="x()">', "event handler"),
    ('<a href="javascript:x">', "javascript:"),
    ("", "empty"),
])
def test_the_forbidden_constructs_are_each_caught(html, needle):
    assert any(needle in p for p in lint_chapter_html(html))


def test_inline_styles_are_not_a_problem():
    # The whole point of Vellum's output; MUSAI's other lint bans class= but never style=.
    assert lint_chapter_html('<div style="color:#fff">hi</div>') == []


# ── plan_chapters ─────────────────────────────────────────────────────────────
def test_a_book_with_no_chapters_is_refused():
    with pytest.raises(BookRefused, match="at least one chapter"):
        plan_chapters([])


def test_a_blank_title_is_refused():
    with pytest.raises(BookRefused, match="no title"):
        plan_chapters([ChapterSpec(title="   ", content_html="<p>x</p>")])


def test_two_chapters_with_the_same_title_are_refused():
    chapters = [ChapterSpec(title="Same", content_html="<p>a</p>"),
                ChapterSpec(title="Same", content_html="<p>b</p>")]
    with pytest.raises(BookRefused, match="both titled"):
        plan_chapters(chapters)


def test_the_duplicate_check_sees_through_surrounding_whitespace():
    chapters = [ChapterSpec(title="Same", content_html="<p>a</p>"),
                ChapterSpec(title="  Same  ", content_html="<p>b</p>")]
    with pytest.raises(BookRefused, match="both titled"):
        plan_chapters(chapters)


def test_a_bad_chapter_is_refused_by_its_index_and_title():
    chapters = [ChapterSpec(title="Fine", content_html="<p>ok</p>"),
                ChapterSpec(title="Broken", content_html="<script>x</script>")]
    with pytest.raises(BookRefused, match=r"Chapter 1 \('Broken'\)"):
        plan_chapters(chapters)


def test_planning_reports_the_bytes_and_the_embeds_per_chapter():
    planned = plan_chapters([ChapterSpec(title="One", content_html=YT)])
    assert planned[0]["iframes"] == 1
    assert planned[0]["bytes"] == len(YT)
    assert planned[0]["title"] == "One"


def test_titles_are_stored_stripped_so_the_toc_match_is_not_whitespace_sensitive():
    planned = plan_chapters([ChapterSpec(title="  Padded  ", content_html="<p>x</p>")])
    assert planned[0]["title"] == "Padded"


# ── the structural promises ───────────────────────────────────────────────────
def _tree(fn):
    return ast.parse(inspect.getsource(fn))


def _call_lines(fn, attr):
    """Line numbers (relative to the function) of every `<something>.attr(...)` call."""
    return [n.lineno for n in ast.walk(_tree(fn))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == attr]


def test_a_dry_run_cannot_reach_the_submit_click():
    """The `if dry_run:` branch must `continue` before any click, in the same loop body.

    A guard that only skips *some* of the write is the failure mode that deleted a live
    section on 2026-08-09, so it is pinned rather than trusted.
    """
    tree = _tree(upsert_chapters)
    dry_guards = [n for n in ast.walk(tree)
                  if isinstance(n, ast.If) and any(
                      isinstance(x, ast.Name) and x.id == "dry_run" for x in ast.walk(n.test))]
    assert dry_guards, "upsert_chapters has no `if dry_run:` guard at all"
    guard = dry_guards[0]
    assert any(isinstance(n, ast.Continue) for n in ast.walk(guard)), \
        "the dry-run branch must `continue`, not fall through into the save"

    clicks = _call_lines(upsert_chapters, "click")
    assert clicks, "the live path must still click submit somewhere"
    assert all(c > guard.lineno for c in clicks), \
        f"a click() at line(s) {[c for c in clicks if c <= guard.lineno]} precedes the guard"


def _executable_source(module) -> str:
    """The module's source with comments AND docstrings removed.

    `ast.unparse` already drops comments, but a docstring is an expression statement and
    survives — and this module's docstring names the two forbidden URLs in order to warn
    about them. Prose may name them; code may not.
    """
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_the_module_never_references_a_chapter_delete_or_show_url():
    """Rail 2. `mod/book/delete.php` destroys a chapter and `mod/book/show.php?...&chapterid=`
    toggles its visibility ON GET — the `editsection.php?delete=1` trap, one directory over.
    Neither may appear in executable code."""
    source = _executable_source(book)
    assert "book/delete.php" not in source
    assert "book/show.php" not in source


def test_the_docstring_stripper_would_actually_notice_a_forbidden_url():
    """Guard the guard: if `_executable_source` ever stopped returning code, the test above
    would pass vacuously. The one URL this module *does* use must be visible to it."""
    assert "book/edit.php" in _executable_source(book)
    assert "book/view.php" in _executable_source(book)


def test_every_field_written_is_in_the_allow_list():
    """The loop writes a literal pair of names; both must be declared allowed."""
    source = inspect.getsource(upsert_chapters)
    assert '"title"' in source and '"subchapter"' in source
    assert book.ALLOWED_CHAPTER_FIELDS == frozenset({"title", "subchapter"})


def test_subchapter_is_only_written_when_it_is_actually_wanted():
    """🔴 Measured on the live form 2026-08-09: Moodle renders **no** `subchapter` checkbox
    on the first chapter of a book — a book cannot open with a sub-chapter — so writing the
    default value fails with `not-found` on chapter one of every book. The field must be
    appended under a truth test, never written unconditionally."""
    tree = _tree(upsert_chapters)
    guards = [n for n in ast.walk(tree)
              if isinstance(n, ast.If)
              and any(isinstance(x, ast.Constant) and x.value == "subchapter"
                      for x in ast.walk(n))]
    assert guards, "`subchapter` must be written inside a conditional, not unconditionally"


def test_the_toc_reader_only_follows_view_urls():
    """🔴 With editing on, each TOC `<li>` also carries action icons, and both
    `delete.php?...&chapterid=` and `show.php?...&chapterid=` match a bare `chapterid=`
    search. The selector must pin `view.php` so an id can never be read out of a delete URL."""
    js = book._READ_TOC_JS
    assert 'a[href*="view.php"][href*="chapterid="]' in js
    assert "'a[href*=\"chapterid=\"]'" not in js, "a bare chapterid selector is back"


def test_the_current_chapters_title_comes_from_the_strong_not_the_li():
    """🔴 In editing mode `li.innerText` includes the icon labels — it came back as
    'Mover abajo capítulo\"Start here\"', matched no spec title, and the chapter was created a
    second time instead of updated. The title must be read from the `<strong>`."""
    js = book._READ_TOC_JS
    assert "querySelector('strong')" in js


def test_verification_treats_a_swallowed_iframe_as_a_failure():
    """Rail 5 — `ok` must be derived from `sanitized`, not just from the save succeeding."""
    source = inspect.getsource(upsert_chapters)
    assert 'rec["ok"] = not rec["verify"]["sanitized"]' in source


# ── the chapter lint must look where an attribute can actually be (2026-08-10) ────────────
#
# `plan_chapters` refused the entire First Term book over the sentence *Near + one = this* in
# chapter 11. The rail was right to run before a browser existed and wrong about what it saw.

@pytest.mark.parametrize("html", [
    '<p>ok</p><img src="x.png" onerror="alert(1)">',
    '<div  onclick = "x()">hi</div>',
    '<div ONMOUSEOVER="x()">hi</div>',
])
def test_a_real_event_handler_still_refuses_a_chapter(html):
    assert any("event handler" in p for p in lint_chapter_html(html))


@pytest.mark.parametrize("text", [
    "Near + one = this",
    "if only x = y then",
    "one = 1, two = 2",
])
def test_english_prose_is_not_mistaken_for_a_handler(text):
    assert lint_chapter_html(f'<p style="color:#000">{text}</p>') == []


def test_the_youtube_host_check_still_works_after_the_change():
    ok = ('<iframe src="https://www.youtube-nocookie.com/embed/abc" '
          'style="border:0"></iframe>')
    assert lint_chapter_html(ok) == []
    bad = '<iframe src="https://evil.example.com/embed/abc"></iframe>'
    assert any("non-allowed host" in p for p in lint_chapter_html(bad))
