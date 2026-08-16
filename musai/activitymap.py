"""Import a course's activities from Moodle, and propose what each one counts towards.

The cascade `MAPPING_AUTOMATION.md` designed in June, built for the web. Two halves:

**Import.** A course's activities arrive from `coursedates.discover.read_course_structure` —
the same read the Cronograma already does. One browser trip now feeds both tabs, which matters
because that trip costs a page load per tab and a course has fourteen of them.

**Propose.** For every activity that is *not yet mapped*, work through signals from most
reliable to most general and stop at the first hit, recording which one fired and why.

🔴 **Nothing here ever saves a mapping.** A proposal pre-fills the form; the professor presses
Save. That is a deliberate departure from `MAPPING_AUTOMATION.md`'s *"confidence ≥ threshold →
auto-apply"*, and the reason is the standing rule that weights and grades are the owner's
decision: `Activity.partial_id` and `Activity.category` are **inputs to the grade engine**, so
a confidently wrong auto-mapping does not surface as a wrong label, it surfaces as a wrong
grade — weeks later, in SEGA. Pre-fill plus one click costs the professor a second and keeps
every mapping human-confirmed by construction. It also means the guarantee *"never overwrite a
human-confirmed mapping"* needs no `source` column to enforce: proposals are only ever computed
for activities with no partial, so a mapped activity is untouchable by definition.

**The signals, in order:**

1. **Memory** — another course this professor owns, of the same subject, where an activity
   with this exact name is already mapped. This is `MAPPING_AUTOMATION`'s "saved template" and
   "sibling replication" collapsed into one mechanism, because they are the same question
   asked of a different course. Deliberately no `MappingTemplate` table: the previous
   semester's course row **is** the template, it is already there, and a table that duplicates
   it is a second copy of the truth that can disagree with the first.
2. **Structure** — the course's own tab strip, through `coursedates.tabmap.guess()`, which
   already encodes the owner's rule (*"everything in Exam 1 tab and before is partial 1"*). The
   tab decides the partial; the module type and the tab's slot decide the category.
3. **Nothing** — left for the human, which is where the whole cascade is allowed to end.

⚠️ No AI step yet. All of the owner's courses and all of Colleague D's were restored from a handful of
`.mbz` masters, so their activity names are identical and signals 1 and 2 resolve them
outright. The AI fallback is worth building the first time a real course fails to classify —
`Proposal.source == ""` is where it plugs in.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from musai.coursedates import tabmap
from musai.models import Activity, Course, Partial, Semester

#: Module types that can reach the gradebook on this Moodle. Everything else in a course —
#: books, pages, URLs, labels, folders, files — is *content*: real, visible to students, and
#: not something a grade is computed from.
#:
#: 🔴 An allow-list rather than a deny-list, and that direction is the point. `Activity` rows
#: are what the grade engine iterates; an unknown module type silently becoming one adds a
#: zero-scored row to a partial and drags every student's average down. A gradable type this
#: list has not heard of shows up as a *missing* activity, which a professor notices, instead
#: of a *phantom* one, which nobody does.
GRADABLE_MODULES = ("quiz", "assign", "forum", "workshop", "lesson")

#: Names that mean "this is the exam", in both languages the courses are written in.
_EXAM_NAME = re.compile(r"\bexams?\b|\bexamen(es)?\b|midterm|\bparcial\b", re.I)


@dataclass(frozen=True)
class Proposal:
    """One suggested mapping, and the evidence for it. Never saved by anything here."""

    activity_id: int
    name: str
    partial_id: Optional[int]
    partial_name: str
    category: str
    source: str        # "memory" | "structure" | "" (nothing to say)
    why: str


def _norm(name: str) -> str:
    """Fold accents, case and whitespace — `Workbook 1 ` and `workbook 1` are one activity."""
    flat = unicodedata.normalize("NFKD", name or "")
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", flat).strip().lower()


# ── import ────────────────────────────────────────────────────────────────────
def activities_in(snapshot: dict) -> list[dict]:
    """Every gradable activity in a course-structure snapshot, with the section it sits in.

    Returns `{"name", "cmid", "modname", "section", "section_name", "hidden"}`. Hidden
    activities are included and flagged rather than dropped: a hidden quiz is still in the
    gradebook, and deciding it does not count is the professor's call, not this function's.
    """
    out = []
    for section in snapshot.get("sections", []):
        for act in section.get("activities", []):
            if act.get("modname") not in GRADABLE_MODULES:
                continue
            if not (act.get("name") or "").strip():
                continue
            out.append({
                "name": act["name"].strip(),
                "cmid": act.get("cmid"),
                "modname": act.get("modname"),
                "section": section.get("section"),
                "section_name": section.get("name") or "",
                "hidden": bool(act.get("hidden") or section.get("hidden")),
            })
    return out


def skipped_types(snapshot: dict) -> dict[str, int]:
    """What was left out of the import, by module type, so the count is explainable.

    A professor who sees "64 activities imported" on a course with 106 things in it needs to
    be able to find out where the other 42 went without asking anyone.
    """
    counts: dict[str, int] = {}
    for section in snapshot.get("sections", []):
        for act in section.get("activities", []):
            mod = act.get("modname") or "unknown"
            if mod not in GRADABLE_MODULES:
                counts[mod] = counts.get(mod, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def import_activities(sess: Session, course: Course, snapshot: dict) -> dict:
    """Create `Activity` rows for this course from a structure snapshot. Additive only.

    🔴 **Never deletes, never re-categorises, never clears a partial.** An activity that has
    disappeared from Moodle is reported by the caller and kept here, for the same reason
    `mapping.apply_mapping` keeps a vanished course: the row owns grades, and deleting it
    deletes them. An activity that already exists is matched by name and left completely
    alone — re-importing after a rename must not silently undo an afternoon of mapping.
    """
    found = activities_in(snapshot)
    existing = {
        _norm(a.moodle_item_name or a.name): a
        for a in sess.exec(select(Activity).where(Activity.course_id == course.id)).all()
    }

    # Names Moodle itself lists more than once. Worth reporting: it is legitimate (the same
    # quiz can appear in a tab and in the make-up bank) but it means one gradebook column
    # feeds one MUSAI row, so the professor should know which copy they are looking at.
    seen_names: dict[str, int] = {}
    for item in found:
        k = _norm(item["name"])
        seen_names[k] = seen_names.get(k, 0) + 1
    repeated = sorted(n for n, c in seen_names.items() if c > 1)

    created, matched = 0, 0
    for item in found:
        key = _norm(item["name"])
        if key in existing:
            matched += 1
            continue
        row = Activity(
            course_id=course.id,
            name=item["name"],
            moodle_item_name=item["name"],
            # `general` is the row's default, not a proposal. What the activity actually
            # counts as is decided by `propose()` and confirmed by a human.
            category="general",
        )
        sess.add(row)
        # 🔴 Claim the name immediately. A course really can carry the same activity name in
        # two tabs — 9067 had "Workbook 1" in both First Term and the make-up bank — and
        # without this line the second copy does not see the first (it is not in the database
        # yet) and imports a duplicate. Two `Activity` rows with one name is not cosmetic: the
        # gradebook column matches both, so every student's grade for it is counted twice.
        existing[key] = row
        created += 1
    sess.commit()

    gone = sorted(set(existing) - {_norm(i["name"]) for i in found})
    return {
        "created": created,
        "matched": matched,
        "found": len(found),
        "vanished": [existing[k].name for k in gone],
        "repeated": repeated,
        "skipped": skipped_types(snapshot),
    }


# ── propose ───────────────────────────────────────────────────────────────────
def _partials(sess: Session, course_id: int) -> list[Partial]:
    return list(sess.exec(
        select(Partial).where(Partial.course_id == course_id).order_by(Partial.id)).all())


def _memory(sess: Session, course: Course) -> dict[str, tuple[int, str, Course]]:
    """`{normalised activity name: (partial INDEX, category, source course)}` from elsewhere.

    Reads every OTHER course this professor owns with the same subject, newest semester first,
    and remembers how each activity name was mapped there.

    🔴 The partial is carried as an **index**, not an id. Partial ids are per course, so
    copying `partial_id=41` from last semester's course points this semester's activity at a
    partial belonging to a different course — which the grade engine would happily compute
    from, producing a number with no relationship to anything.
    """
    if not course.professor_id:
        return {}

    others = sess.exec(
        select(Course).where(
            Course.professor_id == course.professor_id,
            Course.subject == course.subject,
            Course.id != course.id,
        )
    ).all()
    if not others:
        return {}

    # Newest semester first, so this term's sibling beats last year's memory of the same name.
    starts = {s.id: s.starts_on for s in sess.exec(select(Semester)).all()}
    others = sorted(others, key=lambda c: (starts.get(c.semester_id) or _EPOCH), reverse=True)

    memory: dict[str, tuple[int, str, Course]] = {}
    for other in others:
        index_of = {p.id: i for i, p in enumerate(_partials(sess, other.id))}
        for act in sess.exec(select(Activity).where(Activity.course_id == other.id)).all():
            if act.partial_id is None:
                continue
            idx = index_of.get(act.partial_id)
            if idx is None:
                continue
            memory.setdefault(_norm(act.name), (idx, act.category, other))
    return memory


def _category_from_structure(modname: str, name: str, slot: str) -> tuple[str, str]:
    """`(category, why)` from what the activity IS and where it sits."""
    if modname == "forum":
        # `MAPPING_AUTOMATION`: the special is usually the one distinctive hand-in, and on
        # these courses that is the graded forum.
        # ⚠️ Deliberately NOT the `forum` category, even though the model allows it:
        # `grading/engine.py` folds `forum` into the EXAM weight bucket, which is almost
        # certainly not what a professor picking "forum" from a dropdown expects. Flagged for
        # The owner rather than worked around quietly — see HANDOFF.
        return "special", "a graded forum is usually the partial's special activity"
    if _EXAM_NAME.search(name or ""):
        return "exam", "the name says exam"
    if slot == tabmap.SLOT_EXAM:
        return "exam", "it sits in the tab that closes the partial"
    return "general", "ordinary content in this tab"


def propose(sess: Session, course: Course, snapshot: Optional[dict] = None) -> list[Proposal]:
    """A suggested partial + category for every activity that has none. Saves nothing.

    Returns proposals in the order the activities are listed. An activity the cascade has
    nothing to say about still gets a `Proposal` with `source=""` — silence about it is worse
    than a row saying "no idea", because the professor cannot tell the two apart on screen.
    """
    partials = _partials(sess, course.id)
    unmapped = [a for a in sess.exec(
        select(Activity).where(Activity.course_id == course.id,
                               Activity.partial_id.is_(None)).order_by(Activity.name)).all()]
    if not unmapped or not partials:
        return []

    memory = _memory(sess, course)

    # Structure: which section each activity is in, and what that section is for.
    by_name: dict[str, dict] = {}
    rules: dict[int, tabmap.TabRule] = {}
    if snapshot:
        for item in activities_in(snapshot):
            by_name.setdefault(_norm(item["name"]), item)
        tabs = [{"section": s.get("section"), "label": s.get("name") or "",
                 "hidden": bool(s.get("hidden"))} for s in snapshot.get("sections", [])]
        guessed = tabmap.guess(tabs, periods=len(partials))
        rules = {r.section: r for r in guessed.rules}

    out: list[Proposal] = []
    for act in unmapped:
        key = _norm(act.name)

        # ── 1. Memory ────────────────────────────────────────────────────────
        if key in memory:
            idx, category, source_course = memory[key]
            if idx < len(partials):
                p = partials[idx]
                out.append(Proposal(
                    activity_id=act.id, name=act.name, partial_id=p.id, partial_name=p.name,
                    category=category, source="memory",
                    why=f"mapped this way in {source_course.group_code}"))
                continue

        # ── 2. Structure ─────────────────────────────────────────────────────
        item = by_name.get(key)
        rule = rules.get(item["section"]) if item else None
        if rule is not None and rule.kind == tabmap.KIND_PERIOD and rule.period:
            idx = min(rule.period, len(partials)) - 1
            p = partials[idx]
            category, why = _category_from_structure(item["modname"], act.name, rule.slot)
            out.append(Proposal(
                activity_id=act.id, name=act.name, partial_id=p.id, partial_name=p.name,
                category=category, source="structure",
                why=f"in “{rule.label or ('§' + str(rule.section))}” → {p.name}; {why}"))
            continue

        # ── 3. Nothing ───────────────────────────────────────────────────────
        reason = "not in the last course read — read the course to use its tabs"
        if rule is not None and rule.kind != tabmap.KIND_PERIOD:
            reason = f"its tab is set to “{rule.kind}”, which belongs to no partial"
        elif not snapshot:
            reason = "MUSAI has not read this course's tabs yet"
        out.append(Proposal(activity_id=act.id, name=act.name, partial_id=None,
                            partial_name="", category=act.category, source="", why=reason))

    return out


def summarise(proposals: list[Proposal]) -> dict:
    """Counts for the banner. A number a professor can check against the table below it."""
    return {
        "total": len(proposals),
        "memory": sum(1 for p in proposals if p.source == "memory"),
        "structure": sum(1 for p in proposals if p.source == "structure"),
        "unknown": sum(1 for p in proposals if not p.source),
        "confident": sum(1 for p in proposals if p.source),
    }
