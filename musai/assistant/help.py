"""The help corpus — what MUSAI can do, read off disk and handed to the assistant verbatim.

A professor's first question is never about a mean grade. It is *"can this thing do X?"*, and
until now the assistant could only answer questions about the gradebook — so the one thing a
colleague needs in order to start using MUSAI without Carlos in the room was the one thing it
could not tell them.

**Shape: give the model an index, let it pull the page.** `index()` + `read()` over a few dozen
short topics in `docs/help/`. No embeddings and no vector store: the corpus is small enough to
list in a couple of hundred tokens, and this composes with the existing function-calling
exactly like `student_status` does.

Four things this module exists to get right.

1. 🔴 **The corpus is written for professors, and it is not the build docs.** `HANDOFF.md`,
   `RUNBOOK.md`, `COURSE_EDITING.md` and `PLAN.md` are full of colleague names, live course ids
   and operational detail — they are gitignored for exactly that reason. Pointing a user-facing
   assistant at them would hand back, one question at a time, precisely what was kept out of
   the public repo. `docs/help/` is a separate corpus, written to be read by its subject.

2. 🔴 **Topics are filtered to the professor's own Moodle.** `virtual3.uach.mx` is Moodle 3.3
   and `aulas1.uach.mx` is 4.5 — different UIs, different menus. A procedure that is right for
   4.5 and wrong for 3.3, delivered confidently, is worse than no answer: the professor follows
   it and finds a menu that does not exist. Every topic declares `applies_to`, and `index()`
   filters it against the hosts that professor's courses actually live on.

3. 🔴 **`read()` returns the body verbatim.** For a gradebook question a wrong answer is a
   number the professor can sanity-check. For "how do I do X" a confident wrong answer sends
   them to a button that is not there — or tells them something destructive is safe. The
   contract with the model is: the text you were handed, with its id, and nothing invented.
   "No topic covers that" is a valid and expected answer.

4. ⭐ **The docs are a cache of the app, so they are tested like one.** A topic describing a
   button that moved is fiction with a working retrieval pipeline behind it — the same failure
   as a course doc that drifts from the course. `tests/test_help_docs.py` walks every path
   named in the corpus and asserts it is still in `app.routes`. Enumerate from the system,
   never from the inventory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: The corpus lives outside the package, next to the code rather than inside it, because it is
#: content a human edits — and because a professor asking "where is this written down?" should
#: be pointed at a folder of Markdown, not at an importable module.
HELP_DIR = Path(__file__).resolve().parents[2] / "docs" / "help"

#: The Moodles UACH actually runs, keyed by the `data-server` slug the portal tile carries and
#: `Course.moodle_server` stores. `musai` is the third value `applies_to` accepts: a topic about
#: MUSAI's own screens is true regardless of which Moodle sits behind them.
#:
#: ⚠️ The versions are what was measured on each host, not what UACH publishes. `aulas1` was
#: confirmed 4.5 during the Enfermería pilot; the two hosts are different installations, not
#: one site with two names.
MOODLE_HOSTS = {
    "virtual3": {"host": "virtual3.uach.mx", "version": "3.3",
                 "label": "Campus Virtual (Moodle 3.3)"},
    "aulas1": {"host": "aulas1.uach.mx", "version": "4.5",
               "label": "Aulas Virtuales (Moodle 4.5)"},
}

APPLIES_ANY = "musai"
VALID_APPLIES = set(MOODLE_HOSTS) | {APPLIES_ANY}

REQUIRED = ("id", "title", "summary", "applies_to")

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
#: Comma-separated in the file; a list in Python. Two fields, both small vocabularies.
_LIST_FIELDS = ("applies_to", "keywords")


class HelpError(ValueError):
    """A malformed topic. Raised at parse time so a bad file fails the suite, not a professor.

    🔴 Deliberately not swallowed. The tempting version logs a warning and skips the file,
    which means a typo in one topic's frontmatter silently removes it from the index — and the
    assistant then answers "no topic covers that" about something that is, in fact, documented.
    A missing answer that looks like a correct refusal is the worst shape this can fail in.
    """


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    summary: str
    applies_to: tuple[str, ...]
    body: str
    keywords: tuple[str, ...] = ()
    tab: str = ""

    def entry(self) -> dict:
        """One row of the index the model sees. Deliberately without the body."""
        row = {"id": self.id, "title": self.title, "summary": self.summary,
               "applies_to": list(self.applies_to)}
        if self.tab:
            row["tab"] = self.tab
        return row


def _parse(path: Path) -> Topic:
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER.match(raw)
    if not m:
        raise HelpError(f"{path.name}: no `---` frontmatter block at the top of the file")

    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise HelpError(f"{path.name}: frontmatter line is not `key: value` — {line!r}")
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()

    missing = [k for k in REQUIRED if not meta.get(k)]
    if missing:
        raise HelpError(f"{path.name}: frontmatter is missing {', '.join(missing)}")

    lists = {k: tuple(p.strip() for p in meta.get(k, "").split(",") if p.strip())
             for k in _LIST_FIELDS}

    bad = set(lists["applies_to"]) - VALID_APPLIES
    if bad:
        raise HelpError(
            f"{path.name}: applies_to has {sorted(bad)} — allowed: {sorted(VALID_APPLIES)}. "
            f"A host nobody recognises silently filters the topic out of every index.")

    if meta["id"] != path.stem:
        raise HelpError(f"{path.name}: id is '{meta['id']}' but the filename says "
                        f"'{path.stem}'. The id is how the model cites it — it has to be "
                        f"findable from the id alone.")

    body = raw[m.end():].strip()
    if not body:
        raise HelpError(f"{path.name}: frontmatter with no body. `read()` would return nothing "
                        f"and the model would have to invent the procedure.")

    return Topic(id=meta["id"], title=meta["title"], summary=meta["summary"],
                 applies_to=lists["applies_to"], keywords=lists["keywords"],
                 tab=meta.get("tab", ""), body=body)


@lru_cache(maxsize=1)
def load() -> dict[str, Topic]:
    """Every topic, keyed by id. Cached — the corpus ships with the app and does not change
    under a running process. Call `reload()` after editing a file in a test."""
    if not HELP_DIR.is_dir():
        return {}
    topics = {}
    for path in sorted(HELP_DIR.glob("*.md")):
        topic = _parse(path)
        if topic.id in topics:
            raise HelpError(f"duplicate topic id '{topic.id}'")
        topics[topic.id] = topic
    return topics


def reload() -> dict[str, Topic]:
    load.cache_clear()
    return load()


def index(hosts: set[str] | frozenset[str] | None = None) -> list[dict]:
    """The topic index, filtered to the Moodle(s) this professor actually teaches on.

    `hosts` is the set of `Course.moodle_server` slugs across their courses. **Empty means
    "unknown", not "none"** — a professor who has not mapped their courses yet gets the whole
    index rather than an empty one, with `applies_to` on every row so the model can say which
    Moodle a procedure is for instead of guessing. Same distinction as a nullable column: the
    version that reads "no hosts, so no matches" turns a first-day professor's every question
    into "no topic covers that".
    """
    topics = load().values()
    hosts = set(hosts or ())
    if hosts:
        keep = hosts | {APPLIES_ANY}
        topics = [t for t in topics if keep & set(t.applies_to)]
    return [t.entry() for t in sorted(topics, key=lambda t: t.id)]


def read(topic_id: str) -> dict:
    """One topic, body included and unmodified.

    A miss returns the ids that exist rather than a bare error: the model's next move after
    "not found" is otherwise to answer from memory, which is the one thing this must not do.
    """
    topics = load()
    key = (topic_id or "").strip().lower()
    topic = topics.get(key)
    if topic is None:
        return {"error": f"No help topic '{topic_id}'.",
                "available": sorted(topics),
                "note": "Answer only from a topic you have read. If none of these cover the "
                        "question, say so — do not describe the procedure from memory."}
    return {"id": topic.id, "title": topic.title, "applies_to": list(topic.applies_to),
            "body": topic.body}


def host_label(slug: str) -> str:
    """`virtual3` → `Campus Virtual (Moodle 3.3)`. The slug alone means nothing to a professor."""
    return MOODLE_HOSTS.get(slug, {}).get("label", slug)
