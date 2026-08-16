"""The renderer is the safety boundary between the model and Moodle.

The model's words arrive as untrusted input. These tests pin the two things that matter:
the output survives Moodle's sanitizer, and nothing the model writes can become markup.
"""

import pytest

from musai.coursebuild.render import (
    BLOCK_TYPES,
    PALETTES,
    find_marker,
    lint,
    palette,
    render,
    render_checked,
)

BANNER = {"type": "banner", "title": "Bienvenidos", "subtitle": "Ago–Dic 2026",
          "accent": "amber", "emoji": "📚", "items": ["Uno", "Dos"]}


# ── Moodle safety ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", BLOCK_TYPES)
def test_every_block_type_renders_moodle_safe(kind):
    out = render_checked({**BANNER, "type": kind})
    assert lint(out) == []


def test_output_uses_inline_styles_and_no_classes():
    """This Moodle keeps `style=` and drops everything else."""
    out = render(BANNER)
    assert "style=" in out
    assert 'class="' not in out
    assert "<style" not in out.lower()


def test_no_script_or_event_handlers():
    out = render(BANNER)
    assert "<script" not in out.lower()
    assert "onclick" not in out.lower()


# ── escaping: the model's words are content, never markup ─────────────────────
def test_model_text_cannot_inject_markup():
    """The security property is that no user text becomes a LIVE tag or attribute."""
    evil = {"type": "banner", "accent": "indigo",
            "title": '<script>alert(1)</script>',
            "body": '"><img src=x onerror=alert(1)>'}
    out = render(evil)
    # No live tags: every angle bracket from the payload is escaped.
    assert "<script" not in out.lower()
    assert "<img" not in out.lower()
    assert "&lt;script&gt;" in out
    assert "&lt;img" in out
    # The attribute never escapes its quotes either.
    assert '"><img' not in out


def test_lint_fails_closed_on_suspicious_escaped_text():
    """Deliberate false positive: the lint flags `onerror=` even when it is inert text.

    Refusing to publish an odd-looking banner is the right failure direction — a professor
    is not going to write ' onclick=' in a welcome message, and failing closed keeps the
    lint a blunt, trustworthy instrument.
    """
    out = render({"type": "banner", "accent": "indigo", "title": "x",
                  "body": '<img src=x onerror=alert(1)>'})
    assert lint(out) != []
    with pytest.raises(ValueError, match="not Moodle-safe"):
        render_checked({"type": "banner", "accent": "indigo", "title": "x",
                        "body": '<img src=x onerror=alert(1)>'})


def test_ordinary_content_passes_the_lint():
    out = render(BANNER)
    assert lint(out) == []


def test_injection_through_list_items_is_escaped():
    out = render({"type": "banner", "title": "x", "accent": "teal",
                  "items": ["<b>bold</b>", "<script>x</script>"]})
    assert "<b>bold</b>" not in out
    assert "&lt;b&gt;" in out


def test_render_checked_refuses_unsafe_output(monkeypatch):
    """If a future renderer regresses, render_checked must catch it rather than ship it."""
    monkeypatch.setitem(
        __import__("musai.coursebuild.render", fromlist=["RENDERERS"]).RENDERERS,
        "banner", lambda b: "<script>bad()</script>")
    with pytest.raises(ValueError, match="not Moodle-safe"):
        render_checked(BANNER)


# ── palettes are ours, not the model's ────────────────────────────────────────
def test_unknown_palette_falls_back_instead_of_emitting_junk():
    bg, accent, ink, subtle = palette("chartreuse-explosion")
    assert (bg, accent, ink, subtle) == PALETTES["indigo"]


def test_every_palette_is_a_full_quad_of_hex_colours():
    for name, colours in PALETTES.items():
        assert len(colours) == 4, name
        assert all(c.startswith("#") and len(c) == 7 for c in colours), name


# ── idempotency marker ────────────────────────────────────────────────────────
def test_marker_is_embedded_and_recoverable():
    """Lets a re-run UPDATE the block instead of appending a duplicate."""
    out = render(BANNER)
    assert find_marker(out) == "bienvenidos"


def test_marker_survives_losing_the_database():
    """The marker lives in the content, so it is recoverable from Moodle alone."""
    fetched_back_from_moodle = render(BANNER)
    assert find_marker(fetched_back_from_moodle) is not None


def test_marker_is_an_html_comment_so_students_never_see_it():
    out = render(BANNER)
    assert out.strip().startswith("<!--")


# ── optional fields ───────────────────────────────────────────────────────────
def test_minimal_block_renders():
    out = render_checked({"type": "banner", "title": "Solo título", "accent": "slate"})
    assert "Solo título" in out


def test_items_are_capped():
    out = render({"type": "banner", "title": "x", "accent": "teal",
                  "items": [f"item{i}" for i in range(20)]})
    assert out.count("<li") == 6


def test_unknown_block_type_raises():
    with pytest.raises(ValueError, match="Unknown block type"):
        render({"type": "spreadsheet", "title": "x"})


# ── why this lint is deliberately blunt ───────────────────────────────────────
def test_this_lint_stays_blunt_and_the_chapter_lint_is_the_precise_one():
    """The two gates must not drift into one.

    `render.lint` guards short generated banners and fails closed on escaped text
    (see `test_lint_fails_closed_on_suspicious_escaped_text`). Long hand-authored prose
    legitimately contains ` one = `, which matches the handler pattern, so Vellum chapters
    go through `book.lint_chapter_html` instead — that one searches tag interiors only.
    """
    from musai.coursebuild.book import lint_chapter_html

    prose = '<p style="color:#000">Near + one = this</p>'
    assert lint(prose) != [], "render.lint is supposed to be blunt; do not loosen it"
    assert lint_chapter_html(prose) == [], "the chapter lint must not read the prose"

    handler = '<img src="x.png" onerror="alert(1)">'
    assert any("event handler" in p for p in lint(handler))
    assert any("event handler" in p for p in lint_chapter_html(handler))
