"""Which tab belongs to which period — and the guess that pre-fills it.

The owner's rule, in his words: *"everything in Exam 1 tab and before is part of the partial 1;
everything between exam 1 and in exam 2 is partial 2"*. So the boundaries are **cut points
between tabs**, not dates attached to names. That distinction is the whole reason this file
generalizes: 1-LED-A is a master template destined for every professor in the academia
(HANDOFF, *THE ACTUAL GOAL*), and a colleague's tabs will not be named the same.

Three rails on the guess:

1. **The guess never picks a date.** It picks a *period*; `periods.py` owns the arithmetic.
   Same split as the course builder, where the model emits block JSON and never HTML.
2. **The guess is always a pre-fill the professor confirms.** A wrong cut moves twenty
   activities into the wrong partial, and `Activity.partial_id` feeds the grade engine — so a
   silent misclassification would surface as a wrong grade, not as a wrong date.
3. **A tab Moodle already hides is skipped whatever it is called.** the owner's *"Other
   resources"* is a deprecation bank; its 9 quizzes must never be dated. The hidden flag is a
   measurement, the name is a heuristic, so the measurement wins.

No AI here yet, deliberately. All seven of the owner's 2026-2 courses were restored from three
`.mbz` backups, so their tab names are identical and the deterministic pass resolves them
outright. The AI fallback is worth building the first time a real course fails to classify —
`TabMap.needs_review` is where it plugs in.
"""

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence
import re
import unicodedata

KIND_PERIOD = "period"            # ordinary teaching content, dated from its period
KIND_ALWAYS_OPEN = "always_open"  # no dates at all — the exploratory/placement exam
KIND_MAKEUP = "makeup"            # after every period ends
KIND_SKIP = "skip"                # a bank of deprecated material — never touched

SLOT_CONTENT = "content"          # available for the whole period
SLOT_EXAM = "exam"                # available only in the period's last week

# Order matters: "Make-up Exam" and "Exploratory Exam" both contain "exam", so the special
# patterns must be tried before the exam pattern or every special becomes a cut point.
_MAKEUP = re.compile(r"make\s*-?\s*up|recuperaci|extraordinari|regulariza", re.I)
_ALWAYS_OPEN = re.compile(r"explorator|diagnostic|colocaci|placement|nivelaci", re.I)
_BANK = re.compile(r"other resources|otros recursos|banco|bank|repositor|archivad", re.I)
_EXAM = re.compile(r"\bexams?\b|\bexamen(es)?\b|midterm|\bparcial\b|\bprueba\b|\btest\b", re.I)


def _norm(label: str) -> str:
    """Fold accents and collapse whitespace so `Recuperación` matches `recuperacion`."""
    flat = unicodedata.normalize("NFKD", label or "")
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", flat).strip()


@dataclass
class TabRule:
    """What happens to one tab. `reason` is shown in the UI so the guess is auditable."""

    section: int
    label: str
    kind: str = KIND_PERIOD
    period: Optional[int] = None
    slot: str = SLOT_CONTENT
    hidden: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TabMap:
    rules: List[TabRule] = field(default_factory=list)
    periods: int = 3
    needs_review: bool = False
    notes: List[str] = field(default_factory=list)

    def rule_for(self, section: int) -> Optional[TabRule]:
        for r in self.rules:
            if r.section == section:
                return r
        return None

    def to_dict(self) -> dict:
        return {"periods": self.periods, "needs_review": self.needs_review,
                "notes": list(self.notes), "rules": [r.to_dict() for r in self.rules]}

    @classmethod
    def from_dict(cls, data: dict) -> "TabMap":
        return cls(
            rules=[TabRule(**r) for r in data.get("rules", [])],
            periods=int(data.get("periods", 3)),
            needs_review=bool(data.get("needs_review", False)),
            notes=list(data.get("notes", [])),
        )


def classify(label: str) -> Optional[str]:
    """The special kind a tab name implies, or None for ordinary content/exam tabs."""
    flat = _norm(label)
    if _MAKEUP.search(flat):
        return KIND_MAKEUP
    if _ALWAYS_OPEN.search(flat):
        return KIND_ALWAYS_OPEN
    if _BANK.search(flat):
        return KIND_SKIP
    return None


def is_exam_tab(label: str) -> bool:
    """True for a tab that acts as a period boundary. Specials are never boundaries."""
    if classify(label) is not None:
        return False
    return bool(_EXAM.search(_norm(label)))


def guess(tabs: Sequence[Dict], periods: int = 3) -> TabMap:
    """Pre-fill a tab map from the course's own tab strip.

    `tabs` is the probe's output: dicts with `section`, `label` and (optionally) `hidden`,
    **in course order** — the order is the data, not the names.
    """
    ordered = sorted(tabs, key=lambda t: t["section"])
    exam_sections = [t["section"] for t in ordered
                     if not t.get("hidden") and is_exam_tab(t.get("label", ""))]

    notes: List[str] = []
    needs_review = False
    # Only the first `periods - 1` exams are boundaries; the last exam closes the last period.
    cuts = set(exam_sections[:periods - 1])
    if len(exam_sections) < periods - 1:
        needs_review = True
        notes.append(
            f"Sólo se reconocieron {len(exam_sections)} pestaña(s) de examen y hacen falta "
            f"{periods - 1} para dividir en {periods} parciales. Revisa el mapa a mano."
        )

    rules: List[TabRule] = []
    current = 1
    for t in ordered:
        section, label = t["section"], t.get("label", "")
        hidden = bool(t.get("hidden"))

        if hidden:
            rules.append(TabRule(section, label, KIND_SKIP, hidden=True,
                                 reason="La pestaña ya está oculta en Moodle."))
            continue

        special = classify(label)
        if special is not None:
            reason = {
                KIND_MAKEUP: "Nombre de examen de recuperación → va después del último parcial.",
                KIND_ALWAYS_OPEN: "Examen exploratorio/diagnóstico → siempre abierto, sin fechas.",
                KIND_SKIP: "Parece un banco de recursos → no se toca.",
            }[special]
            rules.append(TabRule(section, label, special, hidden=hidden, reason=reason))
            continue

        exam = section in exam_sections
        rules.append(TabRule(
            section, label, KIND_PERIOD,
            period=min(current, periods),
            slot=SLOT_EXAM if exam else SLOT_CONTENT,
            hidden=hidden,
            reason=("Pestaña de examen → última semana del parcial." if exam
                    else f"Contenido del parcial {min(current, periods)}."),
        ))
        if section in cuts:
            current += 1

    return TabMap(rules=rules, periods=periods, needs_review=needs_review, notes=notes)
