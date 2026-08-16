"""Grade computation engine.

Computes per-student partial totals from raw activity grades.

Formula (identical weights across all subjects; stored per-partial so they can change):
    Partial total (0–100%) = avg(general_activities) * weight_general
                           + special_activity       * weight_special
                           + exam_activity          * weight_exam

Converts the partial percentage → 0–10 scale for SEGA:
    grade_10 = round(pct / 10, 1), clamped [MIN_GRADE, 10]
"""

from __future__ import annotations
import json
import math
from dataclasses import dataclass, field

MIN_GRADE_10 = 0.1
ROUND_DECIMALS = 1


@dataclass
class ActivityResult:
    """One activity's percentage grade for one student."""
    activity_id: int
    category: str   # "general" | "special" | "exam" | "forum"
    name: str
    value_pct: float | None  # None means not-attempted / missing


@dataclass
class PartialComponents:
    general_avg: float | None
    special: float | None
    exam: float | None
    # raw per-item breakdown for cockpit display
    general_items: list[dict] = field(default_factory=list)
    special_items: list[dict] = field(default_factory=list)
    exam_items: list[dict] = field(default_factory=list)


def _safe_avg(values: list[float]) -> float | None:
    """Average of non-None values; None if the list is empty or all None."""
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def compute_partial(
    results: list[ActivityResult],
    weight_general: float = 0.60,
    weight_special: float = 0.20,
    weight_exam: float = 0.20,
    missing_as_zero: bool = True,
) -> tuple[float | None, PartialComponents]:
    """Compute the partial total percentage for one student.

    Args:
        results: All ActivityResult rows for this student × partial.
        weight_general/special/exam: Bucket weights (must sum ≤ 1).
        missing_as_zero: If True, not-attempted activities count as 0 in the
            general average. If False they are excluded from the average.
            (PLAN open question §15-1; default True is the conservative choice.)

    Returns:
        (total_pct | None, PartialComponents)
        total_pct is None only if there are zero activities at all.
    """
    general_items, special_items, exam_items = [], [], []

    for r in results:
        item = {"id": r.activity_id, "name": r.name, "pct": r.value_pct}
        if r.category == "general":
            general_items.append(item)
        elif r.category == "special":
            special_items.append(item)
        elif r.category in ("exam", "forum"):
            exam_items.append(item)

    def _resolve(items: list[dict], missing_zero: bool) -> float | None:
        vals = [
            (it["pct"] if it["pct"] is not None else (0.0 if missing_zero else None))
            for it in items
        ]
        return _safe_avg(vals)

    general_avg = _resolve(general_items, missing_as_zero)
    special = _resolve(special_items, missing_as_zero)
    exam = _resolve(exam_items, missing_as_zero)

    components = PartialComponents(
        general_avg=general_avg,
        special=special,
        exam=exam,
        general_items=general_items,
        special_items=special_items,
        exam_items=exam_items,
    )

    if all(v is None for v in (general_avg, special, exam)):
        return None, components

    total_pct = (
        (general_avg or 0.0) * weight_general
        + (special or 0.0) * weight_special
        + (exam or 0.0) * weight_exam
    )
    return total_pct, components


def pct_to_10(pct: float, min_grade: float = MIN_GRADE_10) -> float:
    """Convert a percentage (0–100) to a 0–10 grade, clamped and rounded."""
    raw = pct / 10.0
    clamped = max(min_grade, min(10.0, raw))
    return round(clamped, ROUND_DECIMALS)


def components_to_json(components: PartialComponents) -> str:
    return json.dumps(
        {
            "general_avg": components.general_avg,
            "special": components.special,
            "exam": components.exam,
            "general_items": components.general_items,
            "special_items": components.special_items,
            "exam_items": components.exam_items,
        }
    )
