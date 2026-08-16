"""Tests for the course date setter — periods, the tab map, and the plan.

Everything here is pure, and that is the point: the expensive mistakes in this feature are
arithmetic and classification, not clicking. A deadline that lands a week late, or twenty
activities filed under the wrong parcial, is invisible in a screenshot and obvious in a test.

Three of these pin findings measured live on 2026-08-07 and would otherwise be re-litigated:

* a **forum** on this Moodle has no due date at all — only a ratings window;
* a **quiz** form's `attemptopen` / `marksclosed` / `rightansweropen` are *review options*,
  not dates, so nothing may ever pattern-match `*open` / `*closed`;
* the tab named **"Make-up Exam"** contains the word *exam* and must not become a period
  boundary.
"""

from datetime import date, datetime

import pytest

from musai.coursedates import plan as planning
from musai.coursedates import tabmap
from musai.coursedates.periods import PeriodError, shift, split_periods, with_makeup

# 1-LED-A as measured by scratchpad/probe_dates.py (2026-08-07).
REAL_TABS = [
    {"section": 0, "label": "Introduction"},
    {"section": 1, "label": "First Term"},
    {"section": 2, "label": "Watch and Write"},
    {"section": 3, "label": "Exam 1"},
    {"section": 4, "label": "Second Term"},
    {"section": 5, "label": "📘 Workbook Activity: My Daily Routine (Page 90)"},
    {"section": 6, "label": "Exam 2"},
    {"section": 7, "label": "Third Term"},
    {"section": 8, "label": "🏎️TypeRacer Practice Challenge"},
    {"section": 9, "label": "Exam 3"},
    {"section": 10, "label": "Final Remarks"},
    {"section": 11, "label": "Make-up Exam"},
    {"section": 12, "label": "English: Exploratory Exam"},
    {"section": 13, "label": "Other resources", "hidden": True},
]

CARLOS_START, CARLOS_END = date(2026, 8, 10), date(2026, 11, 22)


def carlos_calendar():
    return split_periods(CARLOS_START, CARLOS_END)


# --------------------------------------------------------------------------- periods ----

def test_carlos_semester_splits_into_three_equal_five_week_periods():
    cal = carlos_calendar()
    assert [p.weeks for p in cal.periods] == [5, 5, 5]
    assert (cal.periods[0].starts_on, cal.periods[0].ends_on) == (
        date(2026, 8, 10), date(2026, 9, 13))
    assert (cal.periods[1].starts_on, cal.periods[1].ends_on) == (
        date(2026, 9, 14), date(2026, 10, 18))
    assert (cal.periods[2].starts_on, cal.periods[2].ends_on) == (
        date(2026, 10, 19), date(2026, 11, 22))


def test_every_period_starts_monday_and_ends_sunday():
    for p in carlos_calendar().periods:
        assert p.starts_on.weekday() == 0, f"{p.name} no empieza en lunes"
        assert p.ends_on.weekday() == 6, f"{p.name} no termina en domingo"


def test_periods_are_contiguous_with_no_gap_and_no_overlap():
    ps = carlos_calendar().periods
    for a, b in zip(ps, ps[1:]):
        assert (b.starts_on - a.ends_on).days == 1


def test_the_exam_window_is_the_last_week_of_its_period():
    for p in carlos_calendar().periods:
        assert (p.ends_on - p.exam_opens_on).days == 6
        assert p.exam_opens_on.weekday() == 0


def test_a_period_closes_at_2359_not_midnight():
    """A close date of 00:00 on the 22nd means the 21st to every student who reads it."""
    _, close = carlos_calendar().periods[0].content_window()
    assert (close.hour, close.minute) == (23, 59)
    open_at, _ = carlos_calendar().periods[0].content_window()
    assert (open_at.hour, open_at.minute) == (0, 0)


def test_the_leftover_day_is_reported_and_never_absorbed():
    """The owner's original end date, 2026-11-23, is a Monday — 15 weeks and one day."""
    cal = split_periods(CARLOS_START, date(2026, 11, 23))
    assert cal.leftover_days == 1
    assert cal.ends_on == date(2026, 11, 22)          # coverage stops at the Sunday
    assert cal.requested_ends_on == date(2026, 11, 23)
    assert any("Sobran 1" in n for n in cal.notes)


def test_uneven_weeks_go_to_the_earliest_periods_and_say_so():
    cal = split_periods(date(2026, 8, 10), date(2026, 11, 15))   # 14 weeks
    assert [p.weeks for p in cal.periods] == [5, 5, 4]
    assert any("no se dividen" in n for n in cal.notes)


def test_a_start_that_is_not_monday_is_called_out():
    cal = split_periods(date(2026, 8, 12), date(2026, 11, 10))
    assert any("no en lunes" in n for n in cal.notes)


def test_a_window_too_short_for_the_periods_refuses_rather_than_inventing_one():
    with pytest.raises(PeriodError):
        split_periods(date(2026, 8, 10), date(2026, 8, 23), count=3)   # 2 weeks, 3 periods


def test_an_end_before_the_start_refuses():
    with pytest.raises(PeriodError):
        split_periods(date(2026, 11, 22), date(2026, 8, 10))


def test_a_one_week_period_shrinks_its_exam_window_and_reports_it():
    cal = split_periods(date(2026, 8, 10), date(2026, 8, 30), count=3, exam_window_days=7)
    assert all(p.weeks == 1 for p in cal.periods)
    assert all(p.exam_opens_on == p.starts_on for p in cal.periods)


def test_the_makeup_window_starts_the_day_after_teaching_ends():
    cal = carlos_calendar()
    opens, closes = cal.makeup_window()
    assert opens.date() == date(2026, 11, 23)     # the leftover Monday
    assert closes.date() == date(2026, 11, 29)
    assert (closes.hour, closes.minute) == (23, 59)


# --- the make-up window the faculty actually chose ---------------------------------------

def test_a_pinned_makeup_window_wins_over_the_derived_one():
    """The owner, 2026-08-09: *"one week after the regular semester ends and open for two weeks"*
    — 2026-11-30 → 2026-12-13. No arithmetic over the teaching window produces that, because
    the gap week is an administrative decision, not a leftover."""
    cal = with_makeup(carlos_calendar(), date(2026, 11, 30), date(2026, 12, 13))
    opens, closes = cal.makeup_window()
    assert opens.date() == date(2026, 11, 30)
    assert (opens.hour, opens.minute) == (0, 0)
    assert closes.date() == date(2026, 12, 13)
    assert (closes.hour, closes.minute) == (23, 59)


def test_the_days_argument_is_ignored_once_a_window_is_pinned():
    """Otherwise a caller passing the old default would silently shorten a chosen window."""
    cal = with_makeup(carlos_calendar(), date(2026, 11, 30), date(2026, 12, 13))
    assert cal.makeup_window(days=7)[1].date() == date(2026, 12, 13)


def test_the_gap_between_teaching_and_the_makeup_window_is_reported_not_absorbed():
    """Same rule as leftover days: a week of silence is indistinguishable from a mistake."""
    cal = with_makeup(carlos_calendar(), date(2026, 11, 30), date(2026, 12, 13))
    assert any("hueco" in n and "7" in n for n in cal.notes), cal.notes


def test_a_makeup_window_that_opens_before_teaching_ends_is_refused():
    """A recuperación open during Exam 3 is a student sitting the make-up for a partial they
    have not failed yet."""
    with pytest.raises(PeriodError, match="después del último parcial"):
        with_makeup(carlos_calendar(), date(2026, 11, 15))
    with pytest.raises(PeriodError, match="antes de empezar"):
        with_makeup(carlos_calendar(), date(2026, 11, 30), date(2026, 11, 25))


def test_extending_a_period_never_slides_a_pinned_makeup_window():
    """An extension moves deadlines the professor owns; the recuperación is a date the faculty
    published. Carrying it along silently would move a date nobody agreed to move."""
    cal = with_makeup(carlos_calendar(), date(2026, 11, 30), date(2026, 12, 13))
    out = shift(cal, 2, 3)
    assert out.makeup_window()[0].date() == date(2026, 11, 30)
    assert any("NO se recorrió" in n for n in out.notes), out.notes


def test_an_extension_that_would_swallow_the_makeup_window_refuses():
    cal = with_makeup(carlos_calendar(), date(2026, 11, 30), date(2026, 12, 13))
    with pytest.raises(PeriodError, match="recuperación"):
        shift(cal, 3, 10)          # teaching would end 2026-12-02, past the makeup opening


def test_a_calendar_with_no_pinned_window_still_derives_one_after_a_shift():
    out = shift(carlos_calendar(), None, 7)
    assert out.makeup_window()[0].date() == date(2026, 11, 30)


def test_shifting_one_period_extends_it_and_slides_the_later_ones():
    cal = shift(carlos_calendar(), 2, 5)
    assert cal.periods[0].ends_on == date(2026, 9, 13)          # untouched
    assert cal.periods[1].starts_on == date(2026, 9, 14)        # start does not move
    assert cal.periods[1].ends_on == date(2026, 10, 23)         # +5
    assert cal.periods[2].starts_on == date(2026, 10, 24)       # slid
    assert cal.periods[2].ends_on == date(2026, 11, 27)


def test_shifting_the_whole_calendar_moves_every_period():
    cal = shift(carlos_calendar(), None, 7)
    assert cal.periods[0].starts_on == date(2026, 8, 17)
    assert cal.periods[2].ends_on == date(2026, 11, 29)


def test_shifting_returns_a_new_calendar_so_the_original_can_be_diffed():
    original = carlos_calendar()
    shifted = shift(original, 1, 3)
    assert original.periods[0].ends_on == date(2026, 9, 13)
    assert shifted.periods[0].ends_on == date(2026, 9, 16)


def test_a_shift_that_would_pull_a_period_before_its_own_exam_refuses():
    with pytest.raises(PeriodError):
        shift(carlos_calendar(), 1, -30)


# --------------------------------------------------------------------------- tab map ----

def test_carloss_own_split_is_reproduced_exactly():
    """The golden test: his words, turned into a mapping, checked tab by tab."""
    m = tabmap.guess(REAL_TABS)
    expected = {
        0: (tabmap.KIND_PERIOD, 1, tabmap.SLOT_CONTENT),
        1: (tabmap.KIND_PERIOD, 1, tabmap.SLOT_CONTENT),
        2: (tabmap.KIND_PERIOD, 1, tabmap.SLOT_CONTENT),
        3: (tabmap.KIND_PERIOD, 1, tabmap.SLOT_EXAM),
        4: (tabmap.KIND_PERIOD, 2, tabmap.SLOT_CONTENT),
        5: (tabmap.KIND_PERIOD, 2, tabmap.SLOT_CONTENT),
        6: (tabmap.KIND_PERIOD, 2, tabmap.SLOT_EXAM),
        7: (tabmap.KIND_PERIOD, 3, tabmap.SLOT_CONTENT),
        8: (tabmap.KIND_PERIOD, 3, tabmap.SLOT_CONTENT),
        9: (tabmap.KIND_PERIOD, 3, tabmap.SLOT_EXAM),
        10: (tabmap.KIND_PERIOD, 3, tabmap.SLOT_CONTENT),
    }
    for section, (kind, period, slot) in expected.items():
        r = m.rule_for(section)
        assert (r.kind, r.period, r.slot) == (kind, period, slot), f"§{section} {r.label}"
    assert m.rule_for(11).kind == tabmap.KIND_MAKEUP
    assert m.rule_for(12).kind == tabmap.KIND_ALWAYS_OPEN
    assert m.rule_for(13).kind == tabmap.KIND_SKIP
    assert not m.needs_review


def test_make_up_exam_contains_the_word_exam_but_is_not_a_boundary():
    """If it were, Parcial 3 would end at Exam 2 and everything after would shift a period."""
    assert not tabmap.is_exam_tab("Make-up Exam")
    assert tabmap.classify("Make-up Exam") == tabmap.KIND_MAKEUP


def test_the_exploratory_exam_is_not_a_boundary_either():
    assert not tabmap.is_exam_tab("English: Exploratory Exam")
    assert tabmap.classify("Examen exploratorio") == tabmap.KIND_ALWAYS_OPEN


def test_specials_are_recognised_with_and_without_accents():
    assert tabmap.classify("Examen de Recuperación") == tabmap.KIND_MAKEUP
    assert tabmap.classify("Examen de Recuperacion") == tabmap.KIND_MAKEUP
    assert tabmap.classify("Evaluación diagnóstica") == tabmap.KIND_ALWAYS_OPEN


def test_spanish_exam_names_are_boundaries_too():
    """The template goes to colleagues whose tabs are in Spanish."""
    tabs = [{"section": 0, "label": "Primer Periodo"},
            {"section": 1, "label": "Examen 1"},
            {"section": 2, "label": "Segundo Periodo"},
            {"section": 3, "label": "Examen 2"},
            {"section": 4, "label": "Tercer Periodo"},
            {"section": 5, "label": "Examen 3"}]
    m = tabmap.guess(tabs)
    assert [m.rule_for(s).period for s in range(6)] == [1, 1, 2, 2, 3, 3]
    assert m.rule_for(1).slot == tabmap.SLOT_EXAM
    assert not m.needs_review


def test_a_tab_moodle_already_hides_is_skipped_whatever_it_is_called():
    """The hidden flag is a measurement; the name is a heuristic. The measurement wins."""
    m = tabmap.guess([{"section": 0, "label": "First Term"},
                      {"section": 1, "label": "Exam 1", "hidden": True},
                      {"section": 2, "label": "Second Term"},
                      {"section": 3, "label": "Exam 2"}])
    assert m.rule_for(1).kind == tabmap.KIND_SKIP
    # ...and a hidden exam is not a boundary, so §2 stays in parcial 1.
    assert m.rule_for(2).period == 1


def test_too_few_exam_tabs_asks_for_review_instead_of_guessing():
    m = tabmap.guess([{"section": 0, "label": "Unidad A"},
                      {"section": 1, "label": "Unidad B"},
                      {"section": 2, "label": "Unidad C"}])
    assert m.needs_review
    assert m.notes


def test_the_map_round_trips_through_json():
    m = tabmap.guess(REAL_TABS)
    again = tabmap.TabMap.from_dict(m.to_dict())
    assert [(r.section, r.kind, r.period, r.slot) for r in again.rules] == \
           [(r.section, r.kind, r.period, r.slot) for r in m.rules]


# ------------------------------------------------------------------------------ plan ----

def _sections(*specs):
    """(section, label, [(modname, cmid), ...]) → probe-shaped sections."""
    out = []
    for section, label, acts in specs:
        out.append({"section": section, "name": label,
                    "activities": [{"cmid": str(c), "modname": m, "name": f"{m} {c}"}
                                   for m, c in acts]})
    return out


def _plan_for(sections, tabs=None):
    tabs = tabs or [{"section": s["section"], "label": s["name"]} for s in sections]
    return planning.build_plan("9023", sections, tabmap.guess(tabs), carlos_calendar())


def test_a_quiz_in_an_exam_tab_gets_the_exam_window_not_the_whole_period():
    p = _plan_for(_sections((0, "First Term", [("quiz", 1)]),
                            (1, "Exam 1", [("quiz", 2)]),
                            (2, "Second Term", []), (3, "Exam 2", [])))
    content = {c.field: c.when for c in p.activities[0].changes}
    exam = {c.field: c.when for c in p.activities[1].changes}
    assert content["timeopen"] == datetime(2026, 8, 10, 0, 0)
    assert exam["timeopen"] == datetime(2026, 9, 7, 0, 0)      # a week before the close
    assert content["timeclose"] == exam["timeclose"] == datetime(2026, 9, 13, 23, 59)


def test_an_assignment_uses_its_own_field_names():
    p = _plan_for(_sections((0, "First Term", [("assign", 1)]),
                            (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", [])))
    fields = {c.field for c in p.activities[0].changes}
    assert fields == {"allowsubmissionsfromdate", "duedate"}
    assert "timeopen" not in fields


def test_an_assignment_gets_no_hard_cutoff_by_default():
    """The owner teaches students who lose internet access; late-and-flagged beats refused."""
    p = _plan_for(_sections((0, "First Term", [("assign", 1)]),
                            (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", [])))
    assert "cutoffdate" not in {c.field for c in p.activities[0].changes}


def test_a_cutoff_can_be_opted_into():
    sections = _sections((0, "First Term", [("assign", 1)]),
                         (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", []))
    tabs = [{"section": s["section"], "label": s["name"]} for s in sections]
    p = planning.build_plan("9023", sections, tabmap.guess(tabs), carlos_calendar(),
                            include_optional=("cutoffdate",))
    assert "cutoffdate" in {c.field for c in p.activities[0].changes}


def test_a_forum_is_reported_as_having_no_date_fields_never_as_done():
    """Measured live: this Moodle's forum form has no duedate — only a ratings window."""
    p = _plan_for(_sections((0, "First Term", [("forum", 1)]),
                            (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", [])))
    a = p.activities[0]
    assert a.status == planning.NO_FIELDS
    assert a.changes == []
    assert "no tiene fecha de entrega" in a.note


def test_resources_are_no_fields_by_design_not_unknown():
    p = _plan_for(_sections((0, "First Term", [("label", 1), ("book", 2)]),
                            (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", [])))
    assert {a.status for a in p.activities[:2]} == {planning.NO_FIELDS}
    assert all("Recurso" in a.note for a in p.activities[:2])


def test_an_unmeasured_module_type_is_left_alone_and_flagged():
    p = _plan_for(_sections((0, "First Term", [("workshop", 1)]),
                            (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", [])))
    assert p.activities[0].status == planning.UNKNOWN
    assert p.activities[0].changes == []


def test_the_hidden_resource_bank_is_never_written_to():
    """The owner's 'Other resources' holds 9 real quizzes he has deprecated."""
    sections = _sections((0, "First Term", []), (1, "Exam 1", []), (2, "Second Term", []),
                         (3, "Exam 2", []),
                         (4, "Other resources", [("quiz", i) for i in range(9)]))
    tabs = [{"section": s["section"], "label": s["name"]} for s in sections]
    tabs[4]["hidden"] = True
    p = planning.build_plan("9023", sections, tabmap.guess(tabs), carlos_calendar())
    bank = [a for a in p.activities if a.section == 4]
    assert len(bank) == 9
    assert all(a.status == planning.SKIPPED and not a.changes for a in bank)
    assert not any(a.section == 4 for a in p.writable)


def test_an_always_open_tab_switches_stale_dates_OFF():
    """The restore carried January's dates over; 'always open' must clear them, not keep them."""
    sections = _sections((0, "First Term", []), (1, "Exam 1", []), (2, "Second Term", []),
                         (3, "Exam 2", []),
                         (4, "English: Exploratory Exam", [("quiz", 77)]))
    p = _plan_for(sections)
    a = [x for x in p.activities if x.section == 4][0]
    assert a.status == planning.CLEARED
    assert {(c.field, c.enable, c.when) for c in a.changes} == {
        ("timeopen", False, None), ("timeclose", False, None)}


def test_the_makeup_exam_opens_after_every_period_has_ended():
    sections = _sections((0, "First Term", []), (1, "Exam 1", []), (2, "Second Term", []),
                         (3, "Exam 2", []), (4, "Make-up Exam", [("quiz", 9)]))
    p = _plan_for(sections)
    a = [x for x in p.activities if x.section == 4][0]
    when = {c.field: c.when for c in a.changes}
    assert when["timeopen"] == datetime(2026, 11, 23, 0, 0)
    assert when["timeopen"].date() > carlos_calendar().periods[-1].ends_on


# The landmine the over-broad probe found: a quiz settings form carries `attemptopen`,
# `marksclosed`, `rightansweropen`, `overallfeedbackclosed`… — the REVIEW OPTIONS checkboxes.
# Matching field names by pattern would silently rewrite what students see after an attempt.
QUIZ_REVIEW_OPTION_FIELDS = {
    "attemptopen", "attemptclosed", "correctnessopen", "correctnessclosed",
    "marksopen", "marksclosed", "specificfeedbackopen", "specificfeedbackclosed",
    "generalfeedbackopen", "generalfeedbackclosed", "rightansweropen", "rightanswerclosed",
    "overallfeedbackopen", "overallfeedbackclosed",
}

ALLOWED_FIELDS = planning.ALLOWED_FIELD_NAMES


def test_no_plan_ever_targets_a_quiz_review_option():
    sections = _sections((0, "First Term", [("quiz", 1), ("assign", 2)]),
                         (1, "Exam 1", [("quiz", 3)]), (2, "Second Term", []),
                         (3, "Exam 2", []))
    p = _plan_for(sections)
    touched = {c.field for a in p.activities for c in a.changes}
    assert not (touched & QUIZ_REVIEW_OPTION_FIELDS)
    assert touched <= ALLOWED_FIELDS


def test_every_declared_date_field_is_on_the_allow_list():
    """A new module type added to MOD_DATES cannot smuggle in a non-date field."""
    for spec in planning.MOD_DATES.values():
        for f in filter(None, (spec.open_field, *spec.close_fields)):
            assert f in ALLOWED_FIELDS, f
        assert not (set(spec.optional_fields) & QUIZ_REVIEW_OPTION_FIELDS)


def test_an_assignment_declares_the_fields_that_could_block_its_own_save():
    """Moodle orders cutoffdate >= duedate, so an unmanaged field can invalidate a managed one."""
    p = _plan_for(_sections((0, "First Term", [("assign", 1), ("quiz", 2)]),
                            (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", [])))
    assign, quiz = p.activities[0], p.activities[1]
    assert assign.close_field == "duedate"
    assert set(assign.dependents) == {"cutoffdate", "gradingduedate"}
    assert quiz.dependents == (), "a quiz has no such ordering constraint"


def test_a_field_written_explicitly_is_not_also_treated_as_a_dependent():
    sections = _sections((0, "First Term", [("assign", 1)]),
                         (1, "Exam 1", []), (2, "Second Term", []), (3, "Exam 2", []))
    tabs = [{"section": s["section"], "label": s["name"]} for s in sections]
    p = planning.build_plan("9023", sections, tabmap.guess(tabs), carlos_calendar(),
                            include_optional=("cutoffdate",))
    assert "cutoffdate" not in p.activities[0].dependents


def test_carry_forward_preserves_the_gap_in_whole_days_and_the_time_of_day():
    """The real case: due 22/05 00:00, cut-off 24/05 23:55 → two days of grace, kept."""
    moved = planning.carry_forward(datetime(2026, 5, 22, 0, 0), datetime(2026, 5, 24, 23, 55),
                                   datetime(2026, 11, 22, 23, 59))
    assert moved == datetime(2026, 11, 24, 23, 55)


def test_carry_forward_refuses_when_there_is_no_gap_to_measure():
    assert planning.carry_forward(None, datetime(2026, 5, 24, 23, 55),
                                  datetime(2026, 11, 22, 23, 59)) is None


def test_the_summary_counts_add_up_to_every_activity():
    sections = _sections((0, "First Term", [("quiz", 1), ("label", 2), ("forum", 3)]),
                         (1, "Exam 1", [("quiz", 4)]), (2, "Second Term", []),
                         (3, "Exam 2", []))
    p = _plan_for(sections)
    assert sum(p.counts().values()) == len(p.activities) == 4
    assert len(p.writable) == 2


# ── Reading another professor's course (2026-08-12, English III propagation) ──────────────

def test_the_structure_reader_can_act_as_another_professor():
    """🔴 Added so a course restored into Colleague A's, Colleague B's or Colleague C's group can be VERIFIED:
    a restore carries the master's dates, and "carries" is a hypothesis until it is read in the
    target — which the owner's own account cannot do, because he has no access to their activities.

    Pinned at the call site, not the signature: a parameter that is accepted and then ignored
    would read *his* course and report success, which is the failure worth a test.
    """
    import inspect

    from musai.coursedates.discover import read_course_structure

    src = inspect.getsource(read_course_structure)
    assert "as_user: Optional[str] = None" in src
    assert "enter_course(ctx, page, idc, as_user=as_user, identity=identity)" in src
    # The cockpit's road, added 2026-08-14. Same rail, different source of the password: the
    # signed-in professor's own credential out of the vault. Accepting `identity` and dropping
    # it means every web-driven read authenticates as whoever `.env` names.
    assert "identity" in inspect.signature(read_course_structure).parameters
    assert "identity=identity" in src


def test_no_date_can_be_written_as_another_professor_FROM_A_COMMAND_LINE():
    """A wrong date in someone else's course is a wrong grade in someone else's gradebook, and
    nobody would see it happen.

    ⚠️ **Renamed and re-scoped 2026-08-13, and the assertion is deliberately unchanged.** This
    test used to be read as *"no date can be written as another professor at all"*, and on
    2026-08-12 that was true: `apply_plan` had no `as_user` either. English IV removed the
    premise — the owner owns **no** INGLES IV course, so *"Dates, Etc."* on a course of Colleague A's or
    Colleague B's is unsatisfiable without one, and he asked for exactly that. `apply_plan` therefore
    gained `as_user`; see `test_dates_can_be_written_for_a_colleague_but_only_by_a_named_script`.

    What this test protects was always the narrower and more valuable thing: **no *command line*
    can do it.** A library parameter is reached only by a script that names the professor and
    the course; a CLI flag is reached by a typo in a shell. Keeping the flag off the CLI is what
    stops a stray `-m musai.coursedates --idc 9012 --apply --as-user …` from ever existing.
    """
    import inspect

    from musai.coursedates import __main__ as cli

    src = inspect.getsource(cli)
    assert "--as-user" not in src and "as_user" not in src
