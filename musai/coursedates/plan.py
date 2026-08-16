"""Tab map + calendar + the live activity list → a concrete, reviewable list of changes.

This is the artefact the owner approves. Nothing here touches Moodle.

**Every activity type carries different date fields, and this file is where that stops being
a footnote.** Measured on virtual3 2026-08-07 by `scratchpad/probe_dates.py`, not remembered:

    quiz    timeopen · timeclose                                    ✅ both real dates
    assign  allowsubmissionsfromdate · duedate · cutoffdate · gradingduedate
    forum   assesstimestart · assesstimefinish   ← RATINGS, not a deadline
    book    (none)
    label   (none)

Two consequences that a single "date per tab" design would have gotten wrong:

* **A forum cannot be given a deadline on this Moodle.** `Watch and Write` is a graded forum
  in Parcial 1 and there is no `duedate` field on it. It is reported as `no_fields`, never
  silently counted as done — the rubric criterion this feature serves is all-or-nothing, so a
  feature that over-reports its own coverage defeats the point of running it.
* **Never pattern-match `*open` / `*closed` on a quiz form.** Those names also belong to the
  *review options* checkboxes (`attemptopen`, `marksclosed`, `rightansweropen`, …). Matching
  by pattern would quietly rewrite what students see after an attempt. Fields are named here,
  exactly, one by one.

The plan states INTENT. It deliberately does not claim to know each activity's current dates:
reading 54 settings forms costs 54 page loads, and `apply.py` is already standing on each form
when it writes. Current values are read, backed up and diffed there.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from musai.coursedates.periods import Calendar
from musai.coursedates.tabmap import (
    KIND_ALWAYS_OPEN, KIND_MAKEUP, KIND_PERIOD, KIND_SKIP, SLOT_EXAM, TabMap,
)

# --- status values on an ActivityPlan ---------------------------------------------------
PLANNED = "planned"          # dates will be written
CLEARED = "cleared"          # existing dates will be switched OFF (always-open tabs)
SKIPPED = "skipped"          # deliberately untouched (hidden bank)
NO_FIELDS = "no_fields"      # the type has no date fields — forum, and resources
UNKNOWN = "unknown"          # a module type nobody measured; never guessed at

# Resources carry no availability dates of their own. Listing them explicitly (rather than
# falling through to UNKNOWN) is what lets the summary say "16 sin fechas por diseño" instead
# of "16 no reconocidas", which are very different sentences for the professor reading it.
RESOURCE_TYPES = {"label", "book", "page", "url", "resource", "folder", "imscp"}

# The complete set of form fields this feature is ever allowed to write. `apply.py` refuses a
# plan that names anything else, which is what keeps a quiz's *review options* — `attemptopen`,
# `marksclosed`, `rightansweropen` — permanently out of reach of a date setter.
ALLOWED_FIELD_NAMES = frozenset({
    "timeopen", "timeclose",
    "allowsubmissionsfromdate", "duedate", "cutoffdate", "gradingduedate",
})


@dataclass(frozen=True)
class ModDates:
    """The date fields one module type actually has. `open_field` may be None."""

    open_field: Optional[str]
    close_fields: Tuple[str, ...]        # written with the period's close datetime
    optional_fields: Tuple[str, ...] = ()  # exist, but left alone unless asked for
    note: str = ""


MOD_DATES: Dict[str, ModDates] = {
    "quiz": ModDates("timeopen", ("timeclose",)),
    # duedate ON, cutoffdate OFF by default and deliberately: a due date marks a late
    # submission, a cut-off refuses it. The owner teaches students who lose internet access —
    # "sometimes students that don't have easy access to technology will ask for an
    # extension" — so the default lets the work arrive late and flagged rather than bounced.
    "assign": ModDates("allowsubmissionsfromdate", ("duedate",),
                       ("cutoffdate", "gradingduedate")),
    # Measured: this Moodle's forum form has no duedate/cutoffdate, only the ratings window.
    "forum": ModDates(None, (), ("assesstimestart", "assesstimefinish"),
                      note="En este Moodle el foro no tiene fecha de entrega, sólo ventana "
                           "de calificación por rating."),
}


@dataclass
class FieldChange:
    field: str
    enable: bool
    when: Optional[datetime] = None      # None when `enable` is False

    def describe(self) -> str:
        return f"{self.field} = {self.when:%d/%m/%Y %H:%M}" if self.enable \
            else f"{self.field} = (desactivado)"


@dataclass
class ActivityPlan:
    cmid: str
    modname: Optional[str]
    name: str
    section: int
    tab_label: str
    status: str
    changes: List[FieldChange] = field(default_factory=list)
    period: Optional[int] = None
    slot: Optional[str] = None
    note: str = ""
    # Moodle orders an assignment's dates: cutoffdate >= duedate >= allowsubmissionsfromdate.
    # So a field this plan does NOT manage can still invalidate one it does — measured live on
    # `TypeRacer Practice Challenge`, whose May cut-off made a November due date illegal and
    # made Moodle refuse the whole form. These three let `apply.py` carry such a field forward
    # instead of leaving the activity unwritable.
    close_field: Optional[str] = None
    close_at: Optional[datetime] = None
    dependents: Tuple[str, ...] = ()


@dataclass
class CoursePlan:
    idc: str
    activities: List[ActivityPlan] = field(default_factory=list)
    calendar: Optional[Calendar] = None
    notes: List[str] = field(default_factory=list)

    @property
    def writable(self) -> List[ActivityPlan]:
        return [a for a in self.activities if a.status in (PLANNED, CLEARED) and a.changes]

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for a in self.activities:
            out[a.status] = out.get(a.status, 0) + 1
        return out

    def summary(self) -> str:
        c = self.counts()
        parts = [f"{c.get(PLANNED, 0)} con fechas nuevas"]
        if c.get(CLEARED):
            parts.append(f"{c[CLEARED]} liberadas")
        if c.get(SKIPPED):
            parts.append(f"{c[SKIPPED]} omitidas")
        if c.get(NO_FIELDS):
            parts.append(f"{c[NO_FIELDS]} sin campos de fecha")
        if c.get(UNKNOWN):
            parts.append(f"{c[UNKNOWN]} de tipo no reconocido")
        return " · ".join(parts)


def carry_forward(old_close: Optional[datetime], old_value: datetime,
                  new_close: datetime) -> Optional[datetime]:
    """Move a dependent date so it keeps the gap it had from the close date it trailed.

    A professor who set a cut-off two days after the due date meant *two days of grace*. When
    the due date moves to a new period, inventing a fresh grace period would overwrite that
    intent, and dropping the cut-off would remove a limit they chose. Preserving the gap is
    the only option that carries their decision forward rather than replacing it.

    Whole days are preserved, and the dependent field keeps its own time of day. Returns None
    when the gap cannot be known (there was no close date to measure from) — the caller must
    then refuse rather than guess.
    """
    if old_close is None:
        return None
    gap = (old_value.date() - old_close.date()).days
    return datetime.combine(new_close.date() + timedelta(days=gap), old_value.time())


def _window(rule, calendar: Calendar) -> Tuple[datetime, datetime]:
    """The (open, close) datetimes a rule implies."""
    if rule.kind == KIND_MAKEUP:
        return calendar.makeup_window()
    period = calendar.period(rule.period or 1)
    return period.exam_window() if rule.slot == SLOT_EXAM else period.content_window()


def build_plan(
    idc: str,
    sections: Sequence[Dict],
    tab_map: TabMap,
    calendar: Calendar,
    *,
    include_optional: Sequence[str] = (),
) -> CoursePlan:
    """Turn the probe's section dump into a per-activity list of intended changes.

    `sections` is `probe_dates.py`'s output: dicts with `section` and `activities`
    (each `cmid`, `modname`, `name`). `include_optional` opts into fields that exist but are
    off by default, e.g. `("cutoffdate",)`.
    """
    plan = CoursePlan(idc=idc, calendar=calendar, notes=list(calendar.notes))
    plan.notes.extend(tab_map.notes)

    for sec in sorted(sections, key=lambda s: s["section"]):
        n = sec["section"]
        rule = tab_map.rule_for(n)
        label = (rule.label if rule else sec.get("name", "")) or f"§{n}"

        for act in sec.get("activities", []):
            base = dict(cmid=act["cmid"], modname=act.get("modname"),
                        name=act.get("name", ""), section=n, tab_label=label)

            if rule is None or rule.kind == KIND_SKIP:
                plan.activities.append(ActivityPlan(
                    **base, status=SKIPPED,
                    note="Pestaña omitida." if rule else "Pestaña sin regla."))
                continue

            modname = act.get("modname")
            if modname in RESOURCE_TYPES:
                plan.activities.append(ActivityPlan(
                    **base, status=NO_FIELDS,
                    note="Recurso: no tiene fechas de disponibilidad propias."))
                continue

            spec = MOD_DATES.get(modname or "")
            if spec is None:
                plan.activities.append(ActivityPlan(
                    **base, status=UNKNOWN,
                    note=f"Tipo «{modname}» no medido: no se toca."))
                continue
            if spec.open_field is None and not spec.close_fields:
                plan.activities.append(ActivityPlan(
                    **base, status=NO_FIELDS, note=spec.note))
                continue

            if rule.kind == KIND_ALWAYS_OPEN:
                changes = [FieldChange(f, False) for f in
                           filter(None, (spec.open_field, *spec.close_fields))]
                plan.activities.append(ActivityPlan(
                    **base, status=CLEARED, changes=changes,
                    note="Siempre abierto: se desactivan las fechas existentes."))
                continue

            if rule.kind != KIND_PERIOD and rule.kind != KIND_MAKEUP:
                plan.activities.append(ActivityPlan(
                    **base, status=SKIPPED, note=f"Regla «{rule.kind}» sin acción."))
                continue

            opens, closes = _window(rule, calendar)
            changes = []
            if spec.open_field:
                changes.append(FieldChange(spec.open_field, True, opens))
            for f in spec.close_fields:
                changes.append(FieldChange(f, True, closes))
            for f in spec.optional_fields:
                if f in include_optional:
                    changes.append(FieldChange(f, True, closes))

            written = {c.field for c in changes}
            plan.activities.append(ActivityPlan(
                **base, status=PLANNED, changes=changes,
                period=rule.period, slot=rule.slot,
                close_field=spec.close_fields[0] if spec.close_fields else None,
                close_at=closes if spec.close_fields else None,
                dependents=tuple(f for f in spec.optional_fields
                                 if f not in written and f in ALLOWED_FIELD_NAMES),
                note="Examen de recuperación." if rule.kind == KIND_MAKEUP else ""))

    return plan
