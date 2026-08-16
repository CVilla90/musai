"""Setting availability dates across a whole course, tab by tab.

Rubric criterion 4 (`EVALUACION_DOCENTE.md`) — *"Estableció las fechas de disponibilidad en
todas las actividades"* — scored all-or-nothing, and the tab-by-tab job the owner used to do by
hand with `moodle_suite/automation/set_quiz_dates.py`.

The split, deliberately:

* `periods.py` — pure calendar arithmetic. No browser, no DB, no Moodle.
* `tabmap.py`  — which tab belongs to which period, and the guess that pre-fills it.
* `plan.py`    — tab map + periods + the live activity list → concrete per-activity changes.
* `apply.py`   — the only part that writes.

Everything except `apply.py` is pure and testable, because the expensive mistakes here are
arithmetic and classification ("Exam 2 landed in Parcial 3"), not clicking.
"""
