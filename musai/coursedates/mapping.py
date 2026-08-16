"""Feed `Activity.partial_id` from the tab map — one map, two features.

The Cronograma already answers "which tab belongs to which parcial?", and the gradebook
already holds 63 activities with `partial_id = NULL`. That NULL is why the grade engine has
nothing to compute: a partial grade is the weighted mean of *its* activities, and none of
them claim a partial. The owner spotted the reuse himself — *"we could also use a similar tab
logic for the activity mapping"*.

## The join, and why it was broken

The gradebook knows an activity by its **column name** (`Activity.moodle_item_name`, read
from the CSV export). The snapshot knows it by its **name on the course page**. Those are the
same string — except `discover.py` was reading `.instancename` whole, which includes a
screen-reader-only span naming the activity TYPE. So every one of the 80 names arrived as
`"Alphabet Examen"`, `"Watch and Write Foro"`, `"First Term Libro"`, and matched nothing:
**63 activities, 64 snapshot entries, zero overlap.** Fixed at the source in `discover.py`;
`normalize()` here still strips a trailing type word so a snapshot captured before that fix
does not silently map nothing.

## What this module will and will not change

It writes **`partial_id` only**. It *reports* a suggested `category` and never writes one
without being asked, because `category` selects the weight (general .60 / special .20 /
exam .20) and a silently recategorized activity is a silently wrong grade — the exact failure
CLAUDE.md calls the worst bug in the project. Suggesting is free; applying is a decision.

Nothing here touches Moodle. It reads a snapshot that is already on disk.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence
import re
import unicodedata

from sqlmodel import Session, select

from musai.coursedates import tabmap as T
from musai.models import Activity, Course, Partial

# Only these carry a grade. A book or a label has no gradebook column, so an activity row
# that matches one is a sign the gradebook and the course have drifted, not a mapping.
GRADEABLE = {"quiz", "assign", "forum", "workshop", "lesson", "scorm", "choice"}

MAPPED = "mapped"          # will be given a partial
ALREADY = "already"        # already points at that same partial — nothing to do
CHANGED = "changed"        # points at a DIFFERENT partial; needs `regrade=True` to move
UNMATCHED = "unmatched"    # no activity of that name on the course page
AMBIGUOUS = "ambiguous"    # two activities share the name — refuse rather than guess
SKIPPED = "skipped"        # in a bank / always-open tab: deliberately has no partial
REVIEW = "review"          # a make-up: real, gradeable, but which parcial is a human call

# The trailing activity-type word Moodle's accesshide span contributes. Only used to rescue
# snapshots captured before the `discover.py` fix — new reads never contain it.
_TYPE_SUFFIX = re.compile(
    r"\s+(examen|tarea|foro|libro|etiqueta|pagina|archivo|carpeta|url|leccion|taller|consulta"
    r"|quiz|assignment|forum|book|label|page|file|folder|lesson|workshop|choice)$", re.I)


def normalize(name: str) -> str:
    """Fold to a comparable key: accents, case, curly quotes, whitespace, type suffix.

    The curly apostrophe matters here and is not hypothetical — one real activity is
    `(S1) Possessive Case ’s / s’`, and the gradebook export and the course page do not
    agree about which apostrophe character that is.
    """
    flat = unicodedata.normalize("NFKD", name or "")
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    flat = flat.replace("’", "'").replace("‘", "'")
    flat = flat.replace("“", '"').replace("”", '"')
    flat = re.sub(r"\s+", " ", flat).strip()
    flat = _TYPE_SUFFIX.sub("", flat)
    return flat.casefold()


@dataclass
class ActivityMatch:
    activity_id: Optional[int]
    name: str
    status: str
    section: Optional[int] = None
    tab_label: str = ""
    modname: Optional[str] = None
    period: Optional[int] = None
    partial_id: Optional[int] = None
    partial_name: str = ""
    was_partial_id: Optional[int] = None
    suggested_category: Optional[str] = None
    current_category: Optional[str] = None
    reason: str = ""


@dataclass
class MappingReport:
    course_id: int
    group_code: str = ""
    dry_run: bool = True
    matches: List[ActivityMatch] = field(default_factory=list)
    written: int = 0
    notes: List[str] = field(default_factory=list)

    def by_status(self, status: str) -> List[ActivityMatch]:
        return [m for m in self.matches if m.status == status]

    @property
    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for m in self.matches:
            out[m.status] = out.get(m.status, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {"course_id": self.course_id, "group_code": self.group_code,
                "dry_run": self.dry_run, "written": self.written,
                "counts": self.counts, "notes": list(self.notes),
                "matches": [m.__dict__ for m in self.matches]}


def index_snapshot(snapshot: dict) -> Dict[str, List[dict]]:
    """normalized name → every gradeable course-page activity carrying it.

    A list, not a single entry: two activities really can share a name (a duplicated quiz
    left in a bank), and collapsing them would map the gradebook column to whichever the
    iteration order happened to reach last.
    """
    index: Dict[str, List[dict]] = {}
    for section in snapshot.get("sections", []):
        for act in section.get("activities", []):
            if act.get("modname") not in GRADEABLE:
                continue
            key = normalize(act.get("name", ""))
            if not key:
                continue
            index.setdefault(key, []).append({
                "section": section.get("section"),
                "section_name": section.get("name", ""),
                "cmid": act.get("cmid"),
                "modname": act.get("modname"),
                "name": act.get("name", ""),
            })
    return index


def partials_by_period(partials: Sequence[Partial], periods: int) -> Dict[int, Partial]:
    """Period 1..N → the Partial row it grades into.

    On this faculty a course has three teaching periods but SEGA's third slot is called
    *Examen Final Ordinario*, so a name match on "Parcial 3" finds nothing. Position wins:
    the partials are created in course order, and the Nth period grades into the Nth row.
    A name match is tried first only so an unusual course that really does name them
    "Parcial 1/2/3" is not renumbered by accident.
    """
    ordered = sorted(partials, key=lambda p: p.id or 0)
    out: Dict[int, Partial] = {}
    for n in range(1, periods + 1):
        named = [p for p in ordered if re.search(rf"\b{n}\b", p.name or "")]
        if len(named) == 1:
            out[n] = named[0]
        elif n <= len(ordered):
            out[n] = ordered[n - 1]
    return out


def _suggest_category(modname: Optional[str], slot: str) -> str:
    if modname == "forum":
        return "forum"
    if slot == T.SLOT_EXAM:
        return "exam"
    return "general"


def map_activities(
    sess: Session,
    course: Course,
    *,
    snapshot: dict,
    tab_map: T.TabMap,
    apply: bool = False,
    regrade: bool = False,
    set_category: bool = False,
) -> MappingReport:
    """Match every gradebook activity to a parcial through the tab it lives in.

    `apply=False` (the default) computes and returns the plan without touching a row —
    the same rail every other write in MUSAI carries.

    `regrade=False` refuses to MOVE an activity that already has a different partial. That
    is not timidity: `PartialGrade` rows have already been computed from the old mapping,
    and moving an activity silently invalidates them without recomputing.
    """
    report = MappingReport(course_id=course.id, group_code=course.group_code,
                           dry_run=not apply)

    index = index_snapshot(snapshot)
    partials = sess.exec(select(Partial).where(Partial.course_id == course.id)).all()
    if not partials:
        report.notes.append("El curso no tiene parciales; no hay a dónde mapear.")
        return report
    by_period = partials_by_period(partials, tab_map.periods)

    activities = sess.exec(select(Activity).where(Activity.course_id == course.id)).all()
    if not activities:
        report.notes.append("El curso no tiene actividades en el libro de calificaciones.")

    for act in activities:
        key = normalize(act.moodle_item_name or act.name)
        hits = index.get(key, [])

        if not hits:
            report.matches.append(ActivityMatch(
                act.id, act.name, UNMATCHED, current_category=act.category,
                was_partial_id=act.partial_id,
                reason="No hay una actividad con ese nombre en la página del curso."))
            continue
        if len(hits) > 1:
            where = ", ".join(f"§{h['section']}" for h in hits)
            report.matches.append(ActivityMatch(
                act.id, act.name, AMBIGUOUS, current_category=act.category,
                was_partial_id=act.partial_id,
                reason=f"El nombre aparece en {len(hits)} pestañas ({where})."))
            continue

        hit = hits[0]
        rule = tab_map.rule_for(hit["section"])
        base = dict(section=hit["section"], tab_label=hit["section_name"] or (
            rule.label if rule else ""), modname=hit["modname"],
            current_category=act.category, was_partial_id=act.partial_id)

        if rule is None:
            report.matches.append(ActivityMatch(
                act.id, act.name, UNMATCHED, **base,
                reason="La pestaña no está en el mapa."))
            continue

        if rule.kind in (T.KIND_SKIP, T.KIND_ALWAYS_OPEN):
            why = ("Banco de recursos / pestaña oculta." if rule.kind == T.KIND_SKIP
                   else "Examen exploratorio: no cuenta para un parcial.")
            report.matches.append(ActivityMatch(act.id, act.name, SKIPPED, **base,
                                                reason=why))
            continue

        if rule.kind == T.KIND_MAKEUP:
            report.matches.append(ActivityMatch(
                act.id, act.name, REVIEW, **base,
                reason="Recuperación: a qué parcial repone es una decisión del profesor."))
            continue

        period = rule.period
        target = by_period.get(period) if period else None
        if target is None:
            report.matches.append(ActivityMatch(
                act.id, act.name, UNMATCHED, **base, period=period,
                reason=f"No hay un parcial para el periodo {period}."))
            continue

        suggested = _suggest_category(hit["modname"], rule.slot)
        status = (ALREADY if act.partial_id == target.id
                  else CHANGED if act.partial_id is not None
                  else MAPPED)
        match = ActivityMatch(act.id, act.name, status, **base, period=period,
                              partial_id=target.id, partial_name=target.name,
                              suggested_category=suggested,
                              reason=(rule.reason or ""))
        report.matches.append(match)

        if not apply or status == ALREADY:
            continue
        if status == CHANGED and not regrade:
            match.reason = ("Ya pertenece a otro parcial; usa regrade=True para moverla "
                            "(las calificaciones parciales ya calculadas quedan obsoletas).")
            continue

        act.partial_id = target.id
        if set_category and act.category != suggested:
            act.category = suggested
        sess.add(act)
        report.written += 1

    if apply and report.written:
        sess.commit()

    unmatched = len(report.by_status(UNMATCHED))
    if unmatched:
        report.notes.append(
            f"{unmatched} actividad(es) del libro de calificaciones no existen en la página "
            f"del curso. Suele significar que el libro es de un semestre anterior.")

    report.notes.extend(_weight_warnings(sess, course, report, applied_category=set_category))
    return report


def _weight_warnings(sess: Session, course: Course, report: MappingReport,
                     *, applied_category: bool) -> List[str]:
    """🔴 Mapping the parcial is only half the job, and the other half is not obvious.

    `engine.compute_partial` is `general×.60 + special×.20 + exam×.20` and it does **not**
    renormalize when a bucket is empty. Every activity ingested from the gradebook defaults
    to `category="general"`, so today a student who scores 100 % on everything computes to
    **6.0/10** — measured, not reasoned: an all-general perfect set returns 60.0.

    This is exactly the "wrong partial that reaches SEGA" failure CLAUDE.md names as the
    worst bug in the project, so the mapping refuses to be quiet about it even though
    fixing it is not its job.
    """
    notes: List[str] = []
    live = [m for m in report.matches if m.status in (MAPPED, ALREADY, CHANGED)]
    if not live:
        return notes

    current = {(m.current_category or "general") for m in live}
    if applied_category:
        current = {(m.suggested_category or "general") for m in live}

    if not (current & {"exam", "forum"}):
        notes.append(
            "⚠️ Ninguna actividad está marcada como examen, y los pesos no se renormalizan: "
            "con todo en 'general' un alumno perfecto saca 6.0. Falta clasificar categorías.")
    if "special" not in current:
        candidates = [m.name for m in live if m.modname in ("assign", "forum")]
        notes.append(
            "⚠️ El 20 % de 'special' no lo reclama ninguna actividad. Candidatas: "
            + (", ".join(candidates[:4]) if candidates else "ninguna evidente")
            + ". Es una decisión de peso, así que no se asigna sola.")
    return notes
