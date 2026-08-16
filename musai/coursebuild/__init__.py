"""Course content generation — AI proposes a BLOCK, deterministic code renders the HTML.

Split on purpose (see HANDOFF "Course Builder"):

    compose.py   natural language  ->  validated block JSON      (the ~5% that is AI)
    render.py    block JSON        ->  Moodle-safe inline HTML   (deterministic, tested)
    publish.py   HTML              ->  a real activity in Moodle (Playwright, local runner)

The model never writes markup. Moodle's KSES sanitizer silently strips things, and a
sanitizer surprise must be a fixable property of OUR renderer, not of a sentence the model
happened to produce that day.

Converges with `Brainstorm/Vellum/` later — Vellum already solves this for book chapters
(16 block types + a `moodle_safe` lint). Kept self-contained for now rather than reaching
across project directories.
"""
