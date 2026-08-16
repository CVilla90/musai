"""⭐ The help corpus is a CACHE of the app, so it is tested against the app.

A help topic describing a button that moved is fiction with a working retrieval pipeline behind
it. It is the same failure as a course document that drifts from the course, with one thing
made worse: retrieval gives the wrong answer a citation, and a cited wrong answer is harder to
doubt than an uncited one.

The cheap fix, which exists nowhere else in this project: **every in-app path named in
`docs/help/` is checked against `app.routes`.** Enumerate from the system, never from the
inventory. A route renamed tomorrow fails here, in a test whose message says which topic to
edit — rather than being discovered by the colleague the corpus was written for.

The convention that makes it work: an in-app path is written inside backticks in a topic body.
`test_the_corpus_uses_backticks_for_paths` is what keeps that convention true, because a path
written bare is a path this file never checks.
"""

from __future__ import annotations

import re

import pytest

from musai.assistant import help as H
from musai.web.app import app

CORPUS = H.reload()

#: Backticked tokens that start with `/`. Templated segments stay as written (`{course_id}`),
#: so a topic quotes the route the way the router declares it and the comparison is exact.
_PATH = re.compile(r"`(/[A-Za-z0-9_/{}.-]*)`")

#: Paths a topic may legitimately name that are not FastAPI routes.
NON_ROUTES = {
    "/backup/import.php": "Moodle's own screen, not MUSAI's — named in copy-a-course-faster",
    "/filter/manage.php": "Moodle's own screen — named in moodle-course-settings-that-bite",
}


def _routes() -> set[str]:
    return {r.path for r in app.routes if getattr(r, "path", None)}


def _paths_in(topic) -> list[str]:
    return _PATH.findall(topic.body)


# ---------------------------------------------------------------------------
# The corpus loads at all
# ---------------------------------------------------------------------------

def test_the_corpus_exists_and_parses():
    """A malformed topic raises at parse time rather than vanishing from the index.

    🔴 The failure mode worth naming: a topic that is silently skipped makes the assistant
    answer "no topic covers that" about something that IS documented — a missing answer wearing
    the costume of a correct refusal.
    """
    assert CORPUS, f"no topics found in {H.HELP_DIR}"
    assert len(CORPUS) >= 10, "the corpus is too thin to answer a professor's first questions"


@pytest.mark.parametrize("topic_id", sorted(CORPUS))
def test_every_topic_is_well_formed(topic_id):
    t = CORPUS[topic_id]
    assert t.id == topic_id
    assert t.title and not t.title.endswith("."), "a title is a name, not a sentence"
    assert t.summary, "the summary is all the model sees before choosing to read the topic"
    assert len(t.summary) <= 200, "the index is read in full on every help question — keep it short"
    assert t.applies_to, "a topic that applies to nothing is filtered out of every index"
    assert set(t.applies_to) <= H.VALID_APPLIES
    assert len(t.body) > 200, "a stub topic is worse than no topic: it invites the model to fill in"


# ---------------------------------------------------------------------------
# 🔴 The docs-as-cache check
# ---------------------------------------------------------------------------

def test_every_path_named_in_the_corpus_is_a_real_route():
    """Walk `app.routes`, not a list. A renamed route fails here on the day it is renamed."""
    live = _routes()
    broken = []
    for topic in CORPUS.values():
        for path in _paths_in(topic):
            if path in NON_ROUTES or path in live:
                continue
            broken.append(f"{topic.id}.md names `{path}`")
    assert not broken, (
        "The help corpus describes paths this app does not serve:\n  "
        + "\n  ".join(broken)
        + "\n\nThe docs are a cache of the app. Fix the topic, or add the path to NON_ROUTES "
          "with a reason if it belongs to Moodle rather than to MUSAI.")


def test_the_corpus_uses_backticks_for_paths():
    """The check above only sees backticked paths, so a bare one would slip through silently.

    Catches `/courses/1/hub` written as plain prose. Without this, the previous test degrades
    quietly into checking nothing — the worst way for a guard to fail.
    """
    bare = re.compile(r"(?<![`/\w])(/courses/|/settings|/assistant)[A-Za-z0-9_/{}.-]*")
    offenders = []
    for topic in CORPUS.values():
        stripped = re.sub(r"`[^`]*`", "", topic.body)      # remove the correctly-quoted ones
        for m in bare.finditer(stripped):
            offenders.append(f"{topic.id}.md: {m.group(0)}")
    assert not offenders, (
        "In-app paths must be written in `backticks` so the route check can see them:\n  "
        + "\n  ".join(offenders))


def test_every_tab_named_in_frontmatter_is_a_real_tab():
    """The `tab:` facet has to match the tab strip a professor actually sees.

    Read out of `course_base.html` rather than duplicated here — a list in this file would be
    the second copy that drifts, which is the failure the whole module is about.
    """
    from pathlib import Path

    from musai.web import app as app_mod

    src = Path(app_mod.TEMPLATES_DIR) / "course_base.html"
    body = src.read_text(encoding="utf-8")
    tabs = {label for label in re.findall(r"\(\s*'[a-z]+',\s*'([A-Za-z]+)'", body)}
    assert tabs, "could not read the tab strip out of course_base.html"

    extra = {"Settings", "Overview"}          # a real page, not a course tab
    for topic in CORPUS.values():
        if topic.tab:
            assert topic.tab in tabs | extra, (
                f"{topic.id}.md says tab '{topic.tab}'; the strip has {sorted(tabs)}")


# ---------------------------------------------------------------------------
# It must not become a second copy of the private build docs
# ---------------------------------------------------------------------------

def test_the_corpus_does_not_leak_the_private_docs():
    """🔴 `HANDOFF.md`, `RUNBOOK.md`, `COURSE_EDITING.md` and `PLAN.md` are gitignored because
    they carry colleague names, live course ids and operational detail. Pointing a user-facing
    assistant at that material would hand it back one question at a time.

    So the corpus may not cite them, and may not carry a live Moodle course id — the four-digit
    `90xx` numbers that identify real FCCF courses.
    """
    private = ("HANDOFF.md", "RUNBOOK.md", "COURSE_EDITING.md", "PLAN.md", "NEXT_LEVEL.md",
               "scratchpad/")
    course_id = re.compile(r"\b90[0-9]{2}\b")
    problems = []
    for topic in CORPUS.values():
        for name in private:
            if name in topic.body:
                problems.append(f"{topic.id}.md cites the private {name}")
        for m in course_id.finditer(topic.body):
            problems.append(f"{topic.id}.md names live course id {m.group(0)}")
    assert not problems, "\n".join(problems)


def test_no_topic_names_a_person():
    """The corpus is read by colleagues. It describes the app, never who used it on what."""
    named = re.compile(r"\b(Carlos|Susana|Morayma|Hanna|Dalia)\b")
    hits = [f"{t.id}.md: {named.search(t.body).group(0)}"
            for t in CORPUS.values() if named.search(t.body)]
    assert not hits, hits


# ---------------------------------------------------------------------------
# The Moodle-version facet
# ---------------------------------------------------------------------------

def test_a_procedure_for_one_moodle_is_not_offered_for_the_other():
    """🔴 The reason `applies_to` exists. virtual3 is Moodle 3.3, aulas1 is 4.5.

    A 4.5 procedure delivered confidently to a 3.3 professor sends them looking for a menu that
    does not exist — and they cannot tell whether they misread it or it was wrong.
    """
    virtual3 = {t["id"] for t in H.index({"virtual3"})}
    aulas1 = {t["id"] for t in H.index({"aulas1"})}

    for topic in CORPUS.values():
        if topic.applies_to == ("virtual3",):
            assert topic.id in virtual3 and topic.id not in aulas1, topic.id
        if topic.applies_to == (H.APPLIES_ANY,):
            assert topic.id in virtual3 and topic.id in aulas1, (
                f"{topic.id} is about MUSAI itself and should reach every professor")


def test_an_unmapped_professor_gets_the_whole_index_not_an_empty_one():
    """🔴 Empty means UNKNOWN, not none.

    A professor who has not mapped their courses has no known host. The tempting reading —
    "no hosts, so no matches" — turns their every question into "no topic covers that" on
    their first morning, which is precisely when they have the most questions.
    """
    assert len(H.index(set())) == len(CORPUS)
    assert len(H.index(None)) == len(CORPUS)


def test_the_two_hosts_get_different_indexes():
    """If both hosts saw the same list, `applies_to` would be decoration.

    Note which way round this is. The corpus today has Moodle 3.3 procedures and **no 4.5
    ones**, so a `virtual3` professor sees everything and an `aulas1` professor sees strictly
    less. That asymmetry is honest rather than an oversight: nobody has measured a 4.5 screen
    for this project, and writing one from general Moodle knowledge is the exact thing the
    corpus forbids the assistant from doing. An `aulas1` professor gets "no topic covers that",
    which is true.
    """
    virtual3 = {t["id"] for t in H.index({"virtual3"})}
    aulas1 = {t["id"] for t in H.index({"aulas1"})}
    assert aulas1 < virtual3, "the host filter removes nothing — it is not being exercised"


def test_a_professor_on_both_hosts_sees_both_sets():
    """Rare, but it is what the Enfermería pilot looks like if it ever reaches one account."""
    assert {t["id"] for t in H.index({"virtual3", "aulas1"})} == set(CORPUS)


# ---------------------------------------------------------------------------
# read() — the verbatim contract
# ---------------------------------------------------------------------------

def test_read_returns_the_body_unmodified():
    topic = next(iter(CORPUS.values()))
    out = H.read(topic.id)
    assert out["body"] == topic.body, "the model must see the text a human wrote, not a summary"
    assert out["id"] == topic.id, "the id is what it cites — it has to come back with the text"


def test_a_miss_lists_what_exists_and_says_not_to_improvise():
    out = H.read("how-do-i-fly")
    assert "error" in out
    assert set(out["available"]) == set(CORPUS)
    assert "do not describe the procedure from memory" in out["note"]


def test_read_is_case_and_padding_tolerant():
    topic_id = sorted(CORPUS)[0]
    assert H.read(f"  {topic_id.upper()} ")["id"] == topic_id


# ---------------------------------------------------------------------------
# The tools the model actually sees
# ---------------------------------------------------------------------------

def test_the_help_tools_are_in_the_bound_tool_set():
    from musai.assistant.tools import tools_for

    names = {f.__name__ for f in tools_for(None)}
    assert {"list_help_topics", "read_help_topic"} <= names


def test_the_help_tools_work_for_a_professor_with_no_courses():
    """Help is the one thing that must work before anything else does.

    A new professor has no courses, no gradebook and no host — and the questions they have are
    exactly the ones this corpus answers. Every other tool is empty-handed for them; these two
    must not be.
    """
    from musai.assistant.tools import tools_for

    tools = {f.__name__: f for f in tools_for(None)}
    topics = tools["list_help_topics"]()
    assert len(topics) == len(CORPUS)
    assert tools["read_help_topic"]("getting-started")["body"]


def test_the_system_prompt_forbids_inventing_a_procedure():
    """The rail lives in the prompt, so a change to the prompt that drops it fails here.

    The existing rail said never invent a GRADE. It did not say never invent a FEATURE, and the
    two fail differently: a wrong number gets sanity-checked, a wrong procedure gets followed.
    """
    from musai.assistant.agent import SYSTEM

    assert "list_help_topics" in SYSTEM and "read_help_topic" in SYSTEM
    assert "NEVER describe a MUSAI or Moodle procedure" in SYSTEM
    assert "say so plainly and stop" in SYSTEM
