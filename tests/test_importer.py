"""Tests for the Moodle gradebook importer.

Uses the real sample ODS from samples/ — no mocking, tests against actual data shape.
"""

import pytest
from pathlib import Path
from musai.grading.importer import load_gradebook, _coerce_pct, _base_name

SAMPLE_ODS = Path(__file__).parent.parent / "samples" / "1-LED-A- INGLES I- 5500- 01- 533711 Calificaciones (5).ods"


class TestCoercePct:
    def test_percent_string(self):
        assert _coerce_pct("90.00 %") == pytest.approx(90.0)

    def test_dash_is_none(self):
        assert _coerce_pct("-") is None

    def test_empty_is_none(self):
        assert _coerce_pct("") is None

    def test_nan_is_none(self):
        assert _coerce_pct("nan") is None

    def test_plain_float(self):
        assert _coerce_pct("85.5") == pytest.approx(85.5)

    def test_fraction_normalizes(self):
        # 0.90 → treated as 90%
        assert _coerce_pct("0.90") == pytest.approx(90.0)


class TestBaseName:
    def test_strips_porcentaje(self):
        assert _base_name("First Term Quiz (Porcentaje)") == "First Term Quiz"

    def test_strips_real(self):
        assert _base_name("First Term Quiz (Real)") == "First Term Quiz"

    def test_strips_letra(self):
        assert _base_name("First Term Quiz (Letra)") == "First Term Quiz"

    def test_strips_moodle_prefix(self):
        assert _base_name("Examen: Final Exam (Porcentaje)") == "Final Exam"

    def test_strips_tarea_prefix(self):
        assert _base_name("Tarea: Book Photo (Porcentaje)") == "Book Photo"

    def test_plain_name_unchanged(self):
        assert _base_name("Some Activity") == "Some Activity"


@pytest.mark.skipif(not SAMPLE_ODS.exists(), reason="sample ODS not available")
class TestLoadGradebook:
    def test_returns_dataframe(self):
        df = load_gradebook(SAMPLE_ODS)
        assert not df.empty
        assert list(df.columns) == ["matricula", "activity", "pct"]

    def test_matricula_are_digits(self):
        df = load_gradebook(SAMPLE_ODS)
        assert df["matricula"].str.match(r"^\d+$").all()

    def test_has_expected_students(self):
        df = load_gradebook(SAMPLE_ODS)
        unique_mats = df["matricula"].nunique()
        assert unique_mats == 20  # sample has 20 students

    def test_activities_detected(self):
        df = load_gradebook(SAMPLE_ODS)
        unique_acts = df["activity"].nunique()
        assert unique_acts >= 60  # sample has 63 activities

    def test_pct_range(self):
        df = load_gradebook(SAMPLE_ODS)
        valid = df["pct"].dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()
