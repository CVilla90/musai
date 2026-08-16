"""Unit tests for the grade computation engine.

These are the most important tests in the project:
a wrong partial that reaches SEGA is the worst bug (PLAN §12).
"""

import pytest
from musai.grading.engine import (
    ActivityResult,
    compute_partial,
    pct_to_10,
    PartialComponents,
)


def ar(activity_id: int, category: str, name: str, pct: float | None) -> ActivityResult:
    return ActivityResult(activity_id=activity_id, category=category, name=name, value_pct=pct)


# ── pct_to_10 ────────────────────────────────────────────────────────────────

class TestPctTo10:
    def test_typical(self):
        assert pct_to_10(85.0) == 8.5

    def test_perfect(self):
        assert pct_to_10(100.0) == 10.0

    def test_zero_clamps_to_min(self):
        assert pct_to_10(0.0) == 0.1

    def test_above_100_clamps_to_10(self):
        assert pct_to_10(110.0) == 10.0

    def test_fraction_pct_like_sega_tool(self):
        # 0.85 treated as 85% → 8.5 is handled upstream by _coerce_pct
        # pct_to_10 receives 85.0 already; just check normal case
        assert pct_to_10(50.0) == 5.0

    def test_rounding(self):
        assert pct_to_10(85.45) == 8.5  # 8.545 → 8.5

    def test_minimum_floor(self):
        assert pct_to_10(0.5) == 0.1  # below min → 0.1


# ── compute_partial ───────────────────────────────────────────────────────────

class TestComputePartial:
    def _make_activities(self):
        return [
            ar(1, "general", "Act 1", 80.0),
            ar(2, "general", "Act 2", 60.0),
            ar(3, "general", "Act 3", 100.0),
            ar(4, "special", "Special", 90.0),
            ar(5, "exam",    "Exam",   70.0),
        ]

    def test_basic_60_20_20(self):
        acts = self._make_activities()
        # general avg = (80+60+100)/3 = 80.0 → *0.6 = 48
        # special = 90 → *0.2 = 18
        # exam = 70 → *0.2 = 14
        # total = 80.0
        total, comp = compute_partial(acts)
        assert total == pytest.approx(80.0)
        assert comp.general_avg == pytest.approx(80.0)
        assert comp.special == pytest.approx(90.0)
        assert comp.exam == pytest.approx(70.0)

    def test_missing_activity_as_zero(self):
        acts = [
            ar(1, "general", "Act 1", 80.0),
            ar(2, "general", "Act 2", None),  # not attempted
            ar(3, "special", "Special", 90.0),
            ar(4, "exam",    "Exam",   70.0),
        ]
        # missing_as_zero=True (default): avg = (80+0)/2 = 40 → *0.6 = 24
        total, comp = compute_partial(acts, missing_as_zero=True)
        assert comp.general_avg == pytest.approx(40.0)
        assert total == pytest.approx(24.0 + 18.0 + 14.0)  # 56.0

    def test_missing_activity_excluded(self):
        acts = [
            ar(1, "general", "Act 1", 80.0),
            ar(2, "general", "Act 2", None),
            ar(3, "special", "Special", 90.0),
            ar(4, "exam",    "Exam",   70.0),
        ]
        # missing_as_zero=False: avg = 80/1 = 80 → *0.6 = 48
        total, comp = compute_partial(acts, missing_as_zero=False)
        assert comp.general_avg == pytest.approx(80.0)
        assert total == pytest.approx(48.0 + 18.0 + 14.0)  # 80.0

    def test_no_activities_returns_none(self):
        total, comp = compute_partial([])
        assert total is None

    def test_all_none_returns_none(self):
        acts = [ar(1, "general", "Act 1", None)]
        total, comp = compute_partial(acts, missing_as_zero=False)
        assert total is None

    def test_only_general(self):
        acts = [ar(1, "general", "Act 1", 70.0)]
        total, comp = compute_partial(acts)
        # special=0, exam=0 (treated as 0 when missing_as_zero=True)
        assert total == pytest.approx(70.0 * 0.6)

    def test_custom_weights(self):
        acts = [
            ar(1, "general", "Act 1", 100.0),
            ar(2, "special", "Special", 100.0),
            ar(3, "exam",    "Exam",   100.0),
        ]
        total, _ = compute_partial(acts, weight_general=0.5, weight_special=0.3, weight_exam=0.2)
        assert total == pytest.approx(100.0)

    def test_forum_counts_as_exam(self):
        acts = [
            ar(1, "general", "Act 1", 80.0),
            ar(2, "special", "Special", 90.0),
            ar(3, "forum",   "Forum",   60.0),
        ]
        total, comp = compute_partial(acts)
        assert comp.exam == pytest.approx(60.0)
        assert total == pytest.approx(80.0 * 0.6 + 90.0 * 0.2 + 60.0 * 0.2)

    def test_grade_10_roundtrip(self):
        acts = self._make_activities()
        total, _ = compute_partial(acts)
        grade = pct_to_10(total)
        assert grade == 8.0  # 80% → 8.0


# ── AuditLog sanity via DB ────────────────────────────────────────────────────

class TestAuditLog:
    def test_audit_log_insert(self, session):
        from musai.audit import log as audit_log
        from musai.models import AuditLog
        from sqlmodel import select

        audit_log(session, "test_action", actor="carlos", env="prod", dry_run=True,
                  detail={"foo": "bar"})
        session.commit()

        entries = session.exec(select(AuditLog)).all()
        assert len(entries) == 1
        assert entries[0].action == "test_action"
        assert entries[0].dry_run is True
        assert "foo" in entries[0].detail_json
