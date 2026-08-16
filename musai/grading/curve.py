"""Curve protocols — explicit, group-level grade adjustments.

The engine grade (``value_0_10``) is always EXACT and never touched here. A curve is
a transparent transform applied *on top*, producing a separate ``final`` grade that is
what gets uploaded. The human can apply the standard auto-curve, override per-student,
or discard it entirely (PLAN §6 "Exactness, and explicit curves").

Standard auto-curve = **square-root** (the owner's choice, 2026-06-13): order-preserving,
helps the weakest most, barely moves the top, and clears genuine borderline students.
"""

from __future__ import annotations

import math

MIN_GRADE_10 = 0.1
ROUND_DECIMALS = 1


def _clamp_round(x: float) -> float:
    return round(max(MIN_GRADE_10, min(10.0, x)), ROUND_DECIMALS)


def square_root(values: list[float]) -> list[float]:
    """grade' = √(grade/10) × 10. Per-student; group passed for a uniform interface."""
    return [_clamp_round(math.sqrt(max(0.0, v) / 10.0) * 10.0) for v in values]


def mean_to_target(values: list[float], target: float = 7.0) -> list[float]:
    """Shift everyone by the same amount so the class mean reaches ``target``."""
    if not values:
        return []
    shift = target - (sum(values) / len(values))
    return [_clamp_round(v + shift) for v in values]


def lift_to_top(values: list[float], ceiling: float = 10.0) -> list[float]:
    """Add (ceiling − max) to everyone (curve to the highest scorer)."""
    if not values:
        return []
    shift = ceiling - max(values)
    return [_clamp_round(v + shift) for v in values]


def border_bump(values: list[float], low: float = 5.5, pass_mark: float = 7.0) -> list[float]:
    """Lift students within [low, pass_mark) up to exactly pass_mark; others unchanged."""
    return [_clamp_round(pass_mark if low <= v < pass_mark else v) for v in values]


# Registry — `square_root` is the standard. Others are available for future per-group choice.
PROTOCOLS = {
    "square_root": square_root,
    "mean_to_target": mean_to_target,
    "lift_to_top": lift_to_top,
    "border_bump": border_bump,
}

STANDARD = "square_root"

PROTOCOL_LABELS = {
    "square_root": "Square-root curve",
    "mean_to_target": "Mean → 7.0",
    "lift_to_top": "Lift to top",
    "border_bump": "Border bump",
}


def apply_protocol(protocol: str, values: list[float]) -> list[float]:
    """Apply a named curve protocol to a list of 0–10 grades. Returns curved grades."""
    fn = PROTOCOLS.get(protocol)
    if fn is None:
        raise ValueError(f"Unknown curve protocol: {protocol!r}")
    return fn(list(values))
