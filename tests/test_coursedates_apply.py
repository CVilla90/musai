"""Tests for the writer — the only part of the date setter that touches Moodle.

The browser is faked rather than mocked away, so the real sequence runs: open the form, check
it is the right one, read what is there, write, read back, save, verify. The fake models the
two things that make this path dangerous:

* a settings form is reached by **cmid in a URL**, and a URL is easy to get wrong;
* a Moodle date is **six inputs and a checkbox**, and the checkbox gates whether the other
  five are submitted at all.

`FakeForm` therefore keeps a *saved* state and a *DOM* state, reset on every navigation, so a
dry run genuinely cannot persist anything and `drop_on_save` can reproduce Moodle accepting a
value and not keeping it.
"""

import copy
import json
import re

import pytest

from musai.coursedates import apply as applier
from musai.coursedates.apply import apply_plan
from musai.coursedates.plan import ActivityPlan, CoursePlan, FieldChange, PLANNED

from datetime import datetime

OPENS = datetime(2026, 8, 10, 0, 0)
CLOSES = datetime(2026, 9, 13, 23, 59)

# What the .mbz restore actually left behind on every quiz: last semester's opening date, and
# no closing date at all.
STALE = {"timeopen": {"enabled": True, "day": 26, "month": 1, "year": 2026,
                      "hour": 0, "minute": 0},
         "timeclose": {"enabled": False, "day": 7, "month": 8, "year": 2026,
                       "hour": 19, "minute": 19}}


def quiz_plan(*cmids, name="Alphabet", section=1):
    acts = [ActivityPlan(cmid=str(c), modname="quiz", name=f"{name} {c}", section=section,
                         tab_label="First Term", status=PLANNED, period=1, slot="content",
                         changes=[FieldChange("timeopen", True, OPENS),
                                  FieldChange("timeclose", True, CLOSES)])
            for c in cmids]
    return CoursePlan(idc="9023", activities=acts)


class FakeForm:
    def __init__(self, cmid, modulename="quiz", fields=None, has_save=True,
                 reject=(), drop_on_save=(), minute_step=1, refuse_save=None):
        self.cmid = str(cmid)
        self.modulename = modulename
        self.minute_step = minute_step       # 5 on an assignment form, 1 on a quiz's
        self.refuse_save = refuse_save       # e.g. ["La fecha límite debe ser posterior…"]
        self.saved = copy.deepcopy(fields if fields is not None else STALE)
        self.dom = copy.deepcopy(self.saved)
        self.has_save = has_save
        self.reject = set(reject)
        self.drop_on_save = set(drop_on_save)

    def load(self):
        self.dom = copy.deepcopy(self.saved)

    def commit(self):
        keep = copy.deepcopy(self.dom)
        for f in self.drop_on_save:          # Moodle took it and did not keep it
            if f in self.saved:
                keep[f] = copy.deepcopy(self.saved[f])
        self.saved = keep


class FakePage:
    def __init__(self, forms, *, lies_about_cmid=None):
        self.forms = {f.cmid: f for f in forms}
        self.lies_about_cmid = lies_about_cmid or {}
        self.current = None
        self.refused = None                  # what the last save attempt left on screen
        self.visited, self.clicked, self.shots = [], [], []

    # -- navigation -------------------------------------------------------------------
    def goto(self, url, **_kw):
        self.visited.append(url)
        m = re.search(r"update=(\d+)", url)
        self.current = self.forms.get(m.group(1)) if m else None
        if self.current:
            self.current.load()

    def wait_for_load_state(self, *_a, **_kw):
        pass

    def wait_for_timeout(self, *_a, **_kw):
        pass

    def screenshot(self, path=None, **_kw):
        self.shots.append(path)

    def click(self, selector, **_kw):
        self.clicked.append((selector, self.current.cmid if self.current else None))
        if selector == "#id_submitbutton2" and self.current:
            if self.current.refuse_save:
                self.refused = list(self.current.refuse_save)   # still on the form
            else:
                self.refused = None
                self.current.commit()

    # -- the three scripts apply.py runs ----------------------------------------------
    def evaluate(self, script, arg=None):
        if "aria-expanded" in script:
            return None
        if "location.pathname" in script:               # was the save refused?
            return self.refused
        f = self.current
        if "modulename" in script:
            if f is None:
                return {"update": None, "modulename": None, "has_save": False, "title": None}
            return {"update": self.lies_about_cmid.get(f.cmid, f.cmid),
                    "modulename": f.modulename, "has_save": f.has_save, "title": "x"}
        if "has_toggle" in script:                      # read
            raw = f.dom.get(arg) if f else None
            if raw is None:
                return None
            return {**raw, "has_toggle": True,
                    "opts": {"hour": list(range(24)),
                             "minute": list(range(0, 60, f.minute_step))}}
        if "dispatchEvent" in script:                   # write
            field, enable, parts = arg["field"], arg["enable"], arg["parts"]
            if f is None or field not in f.dom:
                return {"status": "missing"}
            if field in f.reject:
                return {"status": "rejected:day"}
            f.dom[field]["enabled"] = enable
            if not enable:
                return {"status": "disabled"}
            applied = dict(parts)
            if f.minute_step > 1:               # the select only offers every Nth minute
                applied["minute"] = (parts["minute"] // f.minute_step) * f.minute_step
            f.dom[field].update(applied)
            return {"status": "set", "applied": applied}
        raise AssertionError(f"unexpected evaluate: {script[:70]}")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    holder = {}

    class _Ctx:
        def new_page(self):
            return holder["page"]

        def close(self):
            pass

    class _Browser:
        def new_context(self, **_kw):
            return _Ctx()

        def close(self):
            pass

    class _PW:
        chromium = type("c", (), {"launch": staticmethod(lambda **_kw: _Browser())})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(applier, "sync_playwright", lambda: _PW())
    # `as_user` is accepted and recorded so a test can assert WHICH professor a date was
    # written as — the parameter exists because English IV has no course of the owner's to write
    # into at all.
    # `identity` mirrors the real `enter_course` (2026-08-14): the cockpit resolves the
    # signed-in professor's own credential and passes the object, rather than a username
    # that would be re-resolved down the delegate `.env` road.
    def _enter(ctx, page, idc, *, as_user=None, identity=None):
        holder["as_user"] = as_user
        return holder["page"], "virtual3.uach.mx"

    monkeypatch.setattr(applier, "enter_course", _enter)
    monkeypatch.setattr(applier, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(applier, "SHOT_DIR", tmp_path / "shots")
    return holder


def run(fake, page, plan, **kw):
    fake["page"] = page
    return apply_plan(idc="9023", plan=plan, **kw)


def saves(page):
    return [c for c in page.clicked if c[0] == "#id_submitbutton2"]


# ── the dry-run rail ────────────────────────────────────────────────────────────────────

def test_a_dry_run_fills_every_form_and_saves_nothing(fake):
    page = FakePage([FakeForm(101), FakeForm(102)])
    out = run(fake, page, quiz_plan(101, 102))

    assert out["dry_run"] is True and out["ok"] is True
    assert saves(page) == [], "a dry run must never click save"
    assert out["written"] == 0
    assert len(out["changes"]) == 2
    for f in page.forms.values():
        assert f.saved["timeopen"]["month"] == 1, "the stale date survives a dry run"


def test_a_dry_run_screenshots_the_first_form_as_evidence(fake):
    page = FakePage([FakeForm(101), FakeForm(102)])
    out = run(fake, page, quiz_plan(101, 102))
    assert len(page.shots) == 1 and out["screenshot"].endswith(".png")


def test_a_dry_run_still_writes_the_undo_file(fake):
    """The dry run is where the professor discovers what the old dates were."""
    page = FakePage([FakeForm(101)])
    out = run(fake, page, quiz_plan(101))
    saved = json.loads(open(out["backup"], encoding="utf-8").read())
    assert saved["activities"][0]["cmid"] == "101"
    prior = {c["field"]: c for c in saved["activities"][0]["changes"]}
    assert prior["timeopen"]["when"] == "2026-01-26T00:00:00", "last semester's date"
    assert prior["timeclose"]["enable"] is False


# ── the live path ───────────────────────────────────────────────────────────────────────

def test_a_live_run_saves_each_activity_and_the_values_stick(fake):
    page = FakePage([FakeForm(101), FakeForm(102)])
    out = run(fake, page, quiz_plan(101, 102), dry_run=False)

    assert out["ok"] is True and out["written"] == 2 and out["failed"] == 0
    assert [c[1] for c in saves(page)] == ["101", "102"]
    for f in page.forms.values():
        assert f.saved["timeopen"] == {"enabled": True, "day": 10, "month": 8, "year": 2026,
                                       "hour": 0, "minute": 0}
        assert f.saved["timeclose"]["enabled"] is True
        assert f.saved["timeclose"]["minute"] == 59


def test_an_activity_that_is_already_correct_is_not_saved_again(fake):
    correct = {"timeopen": {"enabled": True, "day": 10, "month": 8, "year": 2026,
                            "hour": 0, "minute": 0},
               "timeclose": {"enabled": True, "day": 13, "month": 9, "year": 2026,
                             "hour": 23, "minute": 59}}
    page = FakePage([FakeForm(101, fields=correct)])
    out = run(fake, page, quiz_plan(101), dry_run=False)

    assert out["unchanged"] == 1 and out["written"] == 0
    assert saves(page) == [], "re-running must be idempotent, not a second write"


def test_only_the_first_n_activities_are_touched_when_limited(fake):
    page = FakePage([FakeForm(101), FakeForm(102), FakeForm(103)])
    out = run(fake, page, quiz_plan(101, 102, 103), dry_run=False, limit=1)
    assert out["targets"] == 1 and [c[1] for c in saves(page)] == ["101"]


# ── identify the target by its payload, never by position ───────────────────────────────

def test_a_form_reporting_a_different_cmid_is_refused_untouched(fake):
    """Moodle answering about another activity must never be typed into."""
    page = FakePage([FakeForm(101)], lies_about_cmid={"101": "999"})
    out = run(fake, page, quiz_plan(101), dry_run=False)

    assert out["failed"] == 1 and out["ok"] is False
    assert "999" in out["failures"][0]["error"]
    assert saves(page) == []
    assert page.forms["101"].saved["timeopen"]["month"] == 1, "nothing was changed"


def test_a_form_of_the_wrong_module_type_is_refused(fake):
    page = FakePage([FakeForm(101, modulename="assign")])
    out = run(fake, page, quiz_plan(101), dry_run=False)
    assert out["failed"] == 1
    assert "assign" in out["failures"][0]["error"]
    assert saves(page) == []


def test_a_form_with_no_save_button_fails_closed(fake):
    page = FakePage([FakeForm(101, has_save=False)])
    out = run(fake, page, quiz_plan(101), dry_run=False)
    assert out["failed"] == 1 and saves(page) == []


def test_a_plan_naming_a_field_outside_the_allow_list_never_opens_a_browser(fake):
    """`attemptopen` is a quiz REVIEW OPTION. It must be unreachable from here."""
    plan = quiz_plan(101)
    plan.activities[0].changes.append(FieldChange("attemptopen", True, CLOSES))
    fake["page"] = FakePage([FakeForm(101)])
    with pytest.raises(RuntimeError, match="attemptopen"):
        apply_plan(idc="9023", plan=plan, dry_run=False)


def test_a_minute_the_form_cannot_hold_is_snapped_down_never_up(fake):
    """Measured 2026-08-07: an assignment's minute select steps by 5, so a 23:59 close is
    impossible on that form while a quiz accepts it. Rounding UP would move the deadline into
    the next day, so the snap always goes down — and it is counted, not hidden."""
    page = FakePage([FakeForm(101, minute_step=5)])
    out = run(fake, page, quiz_plan(101), dry_run=False, verify=True)

    assert out["ok"] is True and out["snapped"] == 1
    closed = page.forms["101"].saved["timeclose"]
    assert (closed["hour"], closed["minute"]) == (23, 55)
    assert closed["day"] == 13, "still the same day — never rolled over"


def test_running_twice_against_a_form_that_cannot_hold_the_exact_time_is_a_no_op(fake):
    """The intent is snapped before comparing, not only before writing — otherwise an
    assignment that can only store 23:55 stays dirty against a 23:59 close forever and every
    run reports writes that changed nothing."""
    page = FakePage([FakeForm(101, minute_step=5)])
    first = run(fake, page, quiz_plan(101), dry_run=False)
    assert first["written"] == 1

    page.clicked.clear()
    second = run(fake, page, quiz_plan(101), dry_run=False)
    assert second["written"] == 0 and second["unchanged"] == 1
    assert saves(page) == [], "a settled course must reach a true steady state"


def test_verification_compares_against_what_the_form_could_hold(fake):
    """A snapped field must not then be reported as a mismatch against the original wish."""
    page = FakePage([FakeForm(101, minute_step=5)])
    out = run(fake, page, quiz_plan(101), dry_run=False, verify=True)
    assert out["mismatched"] == [] and out["failed"] == 0


# ── a save Moodle refuses, and the stale field that causes it ───────────────────────────

ASSIGN_STALE = {
    "allowsubmissionsfromdate": {"enabled": True, "day": 4, "month": 5, "year": 2026,
                                 "hour": 0, "minute": 0},
    "duedate": {"enabled": True, "day": 22, "month": 5, "year": 2026,
                "hour": 0, "minute": 0},
    # 🔴 the real one, off `TypeRacer Practice Challenge`: a cut-off left on from May.
    "cutoffdate": {"enabled": True, "day": 24, "month": 5, "year": 2026,
                   "hour": 23, "minute": 55},
}


def assign_plan(cmid=201):
    act = ActivityPlan(
        cmid=str(cmid), modname="assign", name="TypeRacer Practice Challenge", section=8,
        tab_label="TypeRacer", status=PLANNED, period=3, slot="content",
        changes=[FieldChange("allowsubmissionsfromdate", True, datetime(2026, 10, 19, 0, 0)),
                 FieldChange("duedate", True, datetime(2026, 11, 22, 23, 59))],
        close_field="duedate", close_at=datetime(2026, 11, 22, 23, 59),
        dependents=("cutoffdate", "gradingduedate"))
    return CoursePlan(idc="9023", activities=[act])


def test_a_save_moodle_refuses_is_a_failure_not_a_write(fake):
    """The click navigates either way. Without this check a refused form reports success."""
    page = FakePage([FakeForm(101, refuse_save=["La fecha límite debe ser posterior."])])
    out = run(fake, page, quiz_plan(101), dry_run=False, verify=False)

    assert out["written"] == 0 and out["failed"] == 1 and out["ok"] is False
    assert "rechazó" in out["failures"][0]["error"]
    assert "posterior" in out["failures"][0]["error"]


def test_a_stale_cutoff_is_carried_forward_keeping_the_gap_the_professor_chose(fake):
    """Moodle requires cutoffdate >= duedate, so a May cut-off makes a November due date
    illegal. The professor's 2-day grace is preserved rather than invented or dropped."""
    page = FakePage([FakeForm(201, modulename="assign", fields=ASSIGN_STALE, minute_step=5)])
    out = run(fake, page, assign_plan(201), dry_run=False, verify=True)

    assert out["ok"] is True and out["written"] == 1 and out["carried"] == 1
    cut = page.forms["201"].saved["cutoffdate"]
    assert (cut["day"], cut["month"], cut["year"]) == (24, 11, 2026)   # 22 Nov + 2 days
    assert (cut["hour"], cut["minute"]) == (23, 55), "keeps its own time of day"


def test_a_dependent_that_is_already_later_than_the_new_close_is_left_alone(fake):
    fields = {**copy.deepcopy(ASSIGN_STALE),
              "cutoffdate": {"enabled": True, "day": 31, "month": 12, "year": 2026,
                             "hour": 23, "minute": 55}}
    page = FakePage([FakeForm(201, modulename="assign", fields=fields, minute_step=5)])
    out = run(fake, page, assign_plan(201), dry_run=False)

    assert out["carried"] == 0
    assert page.forms["201"].saved["cutoffdate"]["month"] == 12


def test_a_disabled_dependent_is_never_switched_on(fake):
    """The Workbook assignment's cut-off is off; nothing here may quietly enable it."""
    fields = {**copy.deepcopy(ASSIGN_STALE),
              "cutoffdate": {"enabled": False, "day": 24, "month": 5, "year": 2026,
                             "hour": 23, "minute": 55}}
    page = FakePage([FakeForm(201, modulename="assign", fields=fields, minute_step=5)])
    out = run(fake, page, assign_plan(201), dry_run=False)

    assert out["carried"] == 0
    assert page.forms["201"].saved["cutoffdate"]["enabled"] is False


def test_a_dependent_with_no_previous_close_date_refuses_instead_of_guessing(fake):
    """No old due date means no gap to preserve — inventing one would be a made-up deadline."""
    fields = {**copy.deepcopy(ASSIGN_STALE),
              "duedate": {"enabled": False, "day": 22, "month": 5, "year": 2026,
                          "hour": 0, "minute": 0}}
    page = FakePage([FakeForm(201, modulename="assign", fields=fields, minute_step=5)])
    out = run(fake, page, assign_plan(201), dry_run=False)

    assert out["failed"] == 1 and saves(page) == []
    assert "margen" in out["failures"][0]["error"]


def test_the_undo_file_records_a_dependent_the_run_moved(fake):
    """A backup missing the field we moved could not put it back."""
    page = FakePage([FakeForm(201, modulename="assign", fields=ASSIGN_STALE, minute_step=5)])
    out = run(fake, page, assign_plan(201), dry_run=False)
    saved = json.loads(open(out["backup"], encoding="utf-8").read())
    prior = {c["field"]: c["when"] for c in saved["activities"][0]["changes"]}
    assert prior["cutoffdate"] == "2026-05-24T23:55:00"


def test_the_enable_checkbox_is_clicked_never_assigned(fake):
    """Assigning `.checked` leaves the day/month/year selects disabled, and a disabled input
    is not submitted — the date then silently does not save."""
    assert "en.click()" in applier._WRITE_FIELD_JS
    assert ".checked =" not in applier._WRITE_FIELD_JS


# ── failures are contained and reported ─────────────────────────────────────────────────

def test_one_bad_activity_does_not_abort_the_other_fifty_three(fake):
    page = FakePage([FakeForm(101, reject=("timeopen",)), FakeForm(102)])
    out = run(fake, page, quiz_plan(101, 102), dry_run=False)

    assert out["failed"] == 1 and out["written"] == 1
    assert [c[1] for c in saves(page)] == ["102"]
    assert out["failures"][0]["cmid"] == "101"


def test_a_value_the_form_refuses_is_caught_before_saving(fake):
    page = FakePage([FakeForm(101, reject=("timeclose",))])
    out = run(fake, page, quiz_plan(101), dry_run=False)
    assert out["failed"] == 1 and saves(page) == []


def test_verification_catches_moodle_accepting_a_value_and_dropping_it(fake):
    """"We sent it" and "it happened" are two claims. This is the second one."""
    page = FakePage([FakeForm(101, drop_on_save=("timeclose",))])
    out = run(fake, page, quiz_plan(101), dry_run=False, verify=True)

    assert out["written"] == 1, "the save itself looked fine"
    assert out["ok"] is False, "but the value did not survive it"
    assert out["mismatched"][0]["field"] == "timeclose"


def test_verification_passes_when_everything_stuck(fake):
    page = FakePage([FakeForm(101), FakeForm(102)])
    out = run(fake, page, quiz_plan(101, 102), dry_run=False, verify=True)
    # `verified` counts FIELDS re-read, not activities: 2 quizzes × (timeopen, timeclose).
    assert out["ok"] is True and out["mismatched"] == [] and out["verified"] == 4


def test_a_plan_with_nothing_to_write_says_so_without_opening_a_browser(fake):
    out = apply_plan(idc="9023", plan=CoursePlan(idc="9023"), dry_run=False)
    assert out["ok"] is True and out["targets"] == 0


def test_the_undo_file_is_flushed_before_the_save_it_undoes(fake):
    """A crash halfway must still leave a complete record of what was already changed."""
    page = FakePage([FakeForm(101), FakeForm(102, reject=("timeopen",))])
    out = run(fake, page, quiz_plan(101, 102), dry_run=False)
    saved = json.loads(open(out["backup"], encoding="utf-8").read())
    assert [a["cmid"] for a in saved["activities"]] == ["101", "102"], \
        "the failed activity's prior state is recorded too"
