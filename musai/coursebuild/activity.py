"""Create a real graded activity (an `assign`) inside a chosen section. LOCAL RUNNER ONLY.

`publish.py` creates **labels** — decorative HTML with no gradebook column. This creates the
thing a parcial's *special activity* actually is: a submission an student hands in and a
professor grades.

Field names are not guessed. They were read off the live form on 2026-08-08
(`scratchpad/probe_assign_form.py` → `probe_assign_form_9023.json`), together with the values
of the owner's own `📘 Workbook Activity: My Daily Routine (Page 90)`, which is the template the
defaults here copy rather than invent.

### The rails

1. **Dry-run by default** (CLAUDE.md rail 2) — fills every field, screenshots, saves nothing.
2. 🔴 **Created HIDDEN by default.** A visible new activity raises calendar events and can
   notify the students already enrolled. Rail 4 says MUSAI writes to the *course*, not at
   students, so revealing it is a separate, deliberate act.
3. 🔴 **`ALLOWED_FIELDS` is an allow-list, checked before a browser exists.** The same rail the
   Cronograma runs on, for the same reason: an `assign` form carries 111 named controls, and a
   pattern match that strays into `completion*` or `assignsubmission_*` changes behaviour the
   professor never asked to change.
4. 🔴 **This module sets NO dates.** `musai/coursedates/` owns every date field on every
   activity type, and it is idempotent and verified. Two writers for one field is how a course
   ends up with a due date nobody chose. A new activity is dated by re-running the Cronograma.
5. **Idempotent** via the same `musai:block:<id>` marker the label publisher uses — a re-run
   edits the activity it made last time instead of stacking a second copy.
6. **Read back after saving.** The save click navigates whether or not Moodle accepted the
   form, so "it saved" is not evidence (learned the expensive way in `coursedates/apply.py`).

Nothing here deletes, and nothing here can: there is no delete path in this module.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.config import settings
from musai.coursebuild.publish import (
    _FIND_BY_MARKER_JS, _set_editor, editing_on, enter_course,
)
from musai.coursebuild.render import MARKER_PREFIX, find_marker, lint

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

#: Every form control this module may write, per module type. Measured on the live forms
#: (2026-08-08); anything not listed keeps Moodle's own default. Dates are deliberately absent
#: from both — see rail 4 in the module docstring.
ALLOWED_FIELDS = {
    "assign": frozenset({
        "name",
        "grade[modgrade_type]",
        "grade[modgrade_point]",
        "assignsubmission_file_enabled",
        "assignsubmission_file_maxfiles",
        "assignsubmission_onlinetext_enabled",
        "submissiondrafts",
        "sendnotifications",
        "visible",
    }),
    "forum": frozenset({
        "name",
        "type",
        "assessed",
        "scale[modgrade_type]",
        "scale[modgrade_point]",
        "visible",
    }),
    #: A `book` is a container: it has no grade, no dates and no submission. Only the two
    #: appearance fields matter, and both matter because of what the CHAPTERS look like —
    #: see `_book_fields`. Measured on the live blank form 2026-08-09
    #: (`scratchpad/probe_book_forms.py` → 53 controls).
    "book": frozenset({
        "name",
        "numbering",
        "customtitles",
        "visible",
    }),
}

#: Field names that exist on the forms and are *forbidden* here, with the reason. Kept explicit
#: so a future edit that adds one has to delete a line that says why not.
FORBIDDEN_FIELDS = {
    "allowsubmissionsfromdate": "dates belong to musai/coursedates (Cronograma)",
    "duedate": "dates belong to musai/coursedates (Cronograma)",
    "cutoffdate": "dates belong to musai/coursedates (Cronograma)",
    "gradingduedate": "dates belong to musai/coursedates (Cronograma)",
    "assesstimestart": "dates belong to musai/coursedates (Cronograma)",
    "assesstimefinish": "dates belong to musai/coursedates (Cronograma)",
    "gradecat": "grade categories are the professor's gradebook design, not ours",
    "completion": "completion rules change what students see as done",
    "sendstudentnotifications": "rail 4 — MUSAI does not push messages at students",
    "forcesubscribe": "forced subscription mails every student — rail 4",
}

VISIBLE_SHOWN = "1"
VISIBLE_HIDDEN = "0"

#: `assessed` on a forum: 0 = no ratings, 1 = average, 2 = count, 3 = max, 4 = min, 5 = sum.
#: 🔴 **0 is the bug.** `Watch and Write` in 1-LED-A is a *graded* forum sitting at 0, and that
#: is precisely why it has no gradebook column and contributed nothing all semester. On this
#: Moodle **no `grade_forum*` control exists on either the blank or the update form**, i.e.
#: whole-forum grading is absent — so ratings are the *only* way a forum reaches the gradebook.
#: ⚠️ Precision, because the earlier wording here misled: a **blank** `add=forum` form DOES
#: show a *Calificación* fieldset (83 controls / 12 fieldsets — but it holds only `gradecat` +
#: `gradepass`, not whole-forum grading). The **update** form of `Watch and Write` does not
#: (80 / 11). See COURSE_EDITING.md §3 for the measurement and the falsifiable hypothesis.
FORUM_NO_RATINGS = "0"
FORUM_AGGREGATE_AVERAGE = "1"

#: Forum `type`: general · eachuser · single · qanda · blog. `eachuser` gives every student
#: exactly one discussion of their own that everybody else can reply to — which is the shape
#: of "post your video, then comment on your classmates'".
FORUM_TYPE_EACH_USER = "eachuser"


class ActivityRefused(RuntimeError):
    """A precondition failed. Nothing has been written when this is raised."""


@dataclass
class ActivitySpec:
    """What to create. `intro_html` must carry a `musai:block:` marker for idempotency."""

    section: int
    name: str
    intro_html: str
    modname: str = "assign"
    grade_point: int = 100
    visible: bool = False             # rail 2 — reveal is a separate decision
    #: Edit THIS activity, skipping the name lookup entirely.
    #:
    #: The lookup resolves an assign by *name within its section* (its description, and so its
    #: `musai:block:` marker, is not rendered on the course page). That makes two things
    #: impossible: **renaming** an activity — the new name is by definition not the one to
    #: search for — and editing one a professor has renamed by hand, which otherwise produces
    #: a silent duplicate. Passing the cmid is the durable identity COURSE_EDITING.md §5 asks
    #: for; it is verified against the form after loading, never trusted blind.
    cmid: Optional[str] = None
    # assign only
    submission: str = "file"          # "file" | "onlinetext" | "both"
    max_files: int = 1
    # forum only
    forum_type: str = FORUM_TYPE_EACH_USER
    aggregate: str = FORUM_AGGREGATE_AVERAGE
    # book only — the chapters themselves belong to `musai/coursebuild/book.py`
    numbering: str = "0"
    extras: dict = field(default_factory=dict)

    @property
    def marker(self) -> Optional[str]:
        return find_marker(self.intro_html)


def _assign_fields(spec: ActivitySpec) -> dict:
    if spec.submission not in ("file", "onlinetext", "both"):
        raise ActivityRefused(f"Unknown submission type {spec.submission!r}.")
    fields = {
        "grade[modgrade_type]": "point",
        "grade[modgrade_point]": str(int(spec.grade_point)),
        "assignsubmission_file_enabled": spec.submission in ("file", "both"),
        "assignsubmission_onlinetext_enabled": spec.submission in ("onlinetext", "both"),
        "submissiondrafts": "0",
        "sendnotifications": "0",
    }
    if spec.submission in ("file", "both"):
        fields["assignsubmission_file_maxfiles"] = str(int(spec.max_files))
    return fields


def _forum_fields(spec: ActivitySpec) -> dict:
    # 🔴 The whole reason this branch exists. A forum with `assessed=0` has no gradebook column
    # on this Moodle — measured on `Watch and Write`, which is graded on paper and scored
    # nothing all semester. Refusing here means MUSAI cannot reproduce that defect by default.
    if str(spec.aggregate) == FORUM_NO_RATINGS:
        raise ActivityRefused(
            "assessed=0 means no ratings, and on this Moodle ratings are the ONLY way a forum "
            "reaches the gradebook (there is no whole-forum grading fieldset). That is exactly "
            "why `Watch and Write` is a graded forum with no gradebook column. Pick an "
            "aggregation (1=average, 2=count, 3=max, 4=min, 5=sum)."
        )
    return {
        "type": spec.forum_type,
        "assessed": str(spec.aggregate),
        "scale[modgrade_type]": "point",
        "scale[modgrade_point]": str(int(spec.grade_point)),
    }


#: `numbering` on a book: 0 none · 1 numbers · 2 bullets · 3 indented.
#: `navstyle`: 0 TOC only · 1 images · 2 text. Not written — Moodle's default (1) is fine and
#: an unwritten field is one fewer thing to have an opinion about.
BOOK_NUMBERING_NONE = "0"
BOOK_NUMBERING_NUMBERS = "1"


def _book_fields(spec: ActivitySpec) -> dict:
    # Both of these exist to stop Moodle from printing a SECOND title above content that
    # already has its own. A Vellum chapter opens with a full hero carrying the chapter
    # number and name; with `customtitles` off Moodle prepends its own <h2>, and with
    # `numbering` on it prepends "1." to a title that already reads "1 · There is/There are".
    # Measured defaults on the blank form: customtitles UNCHECKED, numbering = 1.
    return {
        "numbering": str(spec.numbering),
        "customtitles": True,
    }


_BUILDERS = {"assign": _assign_fields, "forum": _forum_fields, "book": _book_fields}


def plan_fields(spec: ActivitySpec) -> dict:
    """Turn a spec into `{form_field_name: value}`. Pure — no browser, fully testable.

    Raises `ActivityRefused` before anything opens a browser if the spec names a field this
    module is not allowed to write. That ordering is the point: the refusal costs a
    millisecond, not a login and a half-filled form on a live course.
    """
    if not spec.name.strip():
        raise ActivityRefused("An activity needs a name; Moodle rejects a blank one.")
    if spec.modname not in _BUILDERS:
        raise ActivityRefused(
            f"Only {sorted(_BUILDERS)} are supported, not {spec.modname!r}. Quizzes carry far "
            "more options and should reuse moodle_suite/generators' Moodle-XML instead of "
            "driving this form."
        )
    if not spec.marker:
        raise ActivityRefused(
            "intro_html carries no `musai:block:<id>` marker, so a re-run could not find this "
            "activity again and would create a duplicate."
        )
    problems = lint(spec.intro_html)
    if problems:
        raise ActivityRefused(f"intro_html failed the lint: {'; '.join(problems)}")

    fields: dict = {"name": spec.name.strip(),
                    "visible": VISIBLE_SHOWN if spec.visible else VISIBLE_HIDDEN}
    fields.update(_BUILDERS[spec.modname](spec))
    fields.update(spec.extras)

    allowed = ALLOWED_FIELDS[spec.modname]
    for key in fields:
        base = key.split("[")[0]
        if base in FORBIDDEN_FIELDS:
            raise ActivityRefused(f"Refusing to write {key!r}: {FORBIDDEN_FIELDS[base]}.")
        if key not in allowed:
            raise ActivityRefused(
                f"Refusing to write {key!r} — it is not in ALLOWED_FIELDS[{spec.modname!r}]. "
                "Add it there deliberately, with a reason, or leave Moodle's default alone."
            )
    return fields


# Set one named control, whatever kind it is, and read it straight back. A select that silently
# rejects a value and a checkbox that never toggled both look like success from the outside.
_SET_FIELD_JS = """
([name, value]) => {
  const els = [...document.querySelectorAll(`[name="${CSS.escape(name)}"]`)]
      .filter(e => e.type !== 'hidden');
  const el = els[0];
  if (!el) return {ok: false, why: 'not-found'};
  if (el.disabled) return {ok: false, why: 'disabled'};
  if (el.type === 'checkbox') {
    const want = (value === true || value === 'true' || value === '1');
    if (el.checked !== want) el.click();          // click, never assign — Moodle listens
    return {ok: el.checked === want, got: el.checked ? '1' : '0'};
  }
  if (el.tagName === 'SELECT') {
    const has = [...el.options].some(o => o.value === String(value));
    if (!has) return {ok: false, why: 'no-such-option',
                      options: [...el.options].slice(0, 12).map(o => o.value)};
    el.value = String(value);
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: el.value === String(value), got: el.value};
  }
  el.value = String(value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  return {ok: el.value === String(value), got: el.value};
}
"""

# Find an activity in the displayed section by the name the professor sees. This is what makes
# a re-run idempotent for assigns and forums, whose descriptions (and therefore whose
# `musai:block:` markers) are NOT rendered on the course page — unlike a label's.
# ⚠️ The consequence: rename an activity by hand in Moodle and a re-run creates a second one.
# The durable fix is storing the cmid MUSAI-side, the same conclusion the messaging work
# reached about needing its own history table.
_FIND_BY_NAME_JS = """
([section, wanted]) => {
  const root = document.getElementById('section-' + section) || document;
  for (const el of root.querySelectorAll('[id^="module-"]')) {
    if (el.id.endsWith('_shim')) continue;
    const link = el.querySelector('.instancename, .activityname a, a.aalink');
    const text = ((link ? link.innerText : el.innerText) || '').trim().split('\\n')[0].trim();
    if (text === wanted) return {cmid: el.id.replace('module-', ''), name: text};
  }
  return null;
}
"""

# A save that Moodle refused still navigates, so "we clicked it" proves nothing. A successful
# save leaves modedit.php; a refused one stays and renders its errors inline.
_SAVE_REFUSED_JS = """
() => {
  if (!location.pathname.includes('/course/modedit.php')) return null;
  const errs = [...document.querySelectorAll('.error, .invalid-feedback, [id$="_error"]')]
      .map(e => e.innerText.trim()).filter(Boolean);
  return {url: location.href, errors: errs.slice(0, 6)};
}
"""


def create_activity(
    *,
    idc: str,
    spec: ActivitySpec,
    dry_run: bool = True,
    headless: bool = True,
    group_label: str = "",
    as_user: str | None = None,
    on_step=None,
) -> dict:
    """Create (or update) `spec` in course `idc`. Returns a result dict; never raises for
    ordinary failure — the caller gets `ok=False` plus `error`, `steps` and a screenshot.

    🔴 `as_user` (2026-08-13) creates the activity in **another professor's** course, resolved
    through `credentials.resolve`, which refuses rather than falling back to the owner's own login.
    Added for English IV, the first level where **no target belongs to the owner at all** — his
    account cannot open any INGLES IV course, so without this the build has no write path.
    """

    fields = plan_fields(spec)          # refuse before a browser exists
    marker_id = spec.marker

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": spec.section,
                 "name": spec.name, "marker": marker_id, "mode": "create", "cmid": None,
                 "fields": fields, "as_user": as_user, "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass                     # progress reporting must never break the job

    log.header(f"Create {spec.modname} '{spec.name}' → idc={idc} section={spec.section} "
               f"{group_label} {'[DRY RUN]' if dry_run else '[LIVE]'}")

    if ensure_subprocess_capable_loop():
        log.info("Restored the Proactor event-loop policy so the browser can start.")

    browser = ctx = page = None
    with sync_playwright() as p:            # the `finally` must live INSIDE this
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc, as_user=as_user)

            # Onetopic renders one tab at a time, so name the section or the DOM you get back
            # belongs to whichever tab happened to be open.
            editing_on(vpage, host, idc, section=spec.section)
            step(f"Editing mode on (section {spec.section} displayed)")

            # An explicit cmid wins outright: it is the only identity that survives a rename,
            # and a rename is precisely the case the name lookup below cannot serve.
            if spec.cmid:
                existing = {"cmid": str(spec.cmid)}
                step(f"Targeting cmid={spec.cmid} directly (no name lookup)")
            else:
                # Marker first (works for labels and for anything showing its description),
                # then the name within the section — what actually resolves an assign/forum.
                existing = vpage.evaluate(_FIND_BY_MARKER_JS, f"{MARKER_PREFIX}{marker_id}")
                if not (existing and existing.get("cmid")):
                    existing = vpage.evaluate(_FIND_BY_NAME_JS,
                                              [spec.section, spec.name.strip()])
            if existing and existing.get("cmid"):
                out["mode"], out["cmid"] = "update", existing["cmid"]
                url = f"https://{host}/course/modedit.php?update={existing['cmid']}&return=0&sr=0"
                step(f"Found it already (cmid={existing['cmid']}) — editing in place")
            else:
                url = (f"https://{host}/course/modedit.php"
                       f"?add={spec.modname}&course={idc}&section={spec.section}&return=0&sr=0")
                step(f"Creating a new {spec.modname} in section {spec.section}")

            vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
            vpage.wait_for_load_state("networkidle", timeout=25000)
            if not vpage.locator("#id_submitbutton2").count():
                _shot(vpage, "activity_no_form")
                raise RuntimeError("The activity form did not load (no #id_submitbutton2).")

            # Confirm the form is the module type we asked for — payload, never position.
            got_mod = vpage.evaluate(
                "() => (document.querySelector('input[name=modulename]') || {}).value || null")
            if got_mod and got_mod != spec.modname:
                raise RuntimeError(
                    f"This form is a {got_mod!r}, not a {spec.modname!r}. Refusing to type "
                    "into it.")

            # What was here before we typed. With an explicit cmid this is the only evidence
            # that the id pointed at what the caller believed it did — a mistyped cmid loads a
            # perfectly valid form for the wrong activity, and every later check would pass.
            out["previous_name"] = vpage.evaluate(
                "() => (document.querySelector('input[name=name]') || {}).value || null")
            if spec.cmid:
                step(f"Editing {out['previous_name']!r} (cmid={spec.cmid})")

            how = _set_editor(vpage, spec.intro_html)
            if how.startswith("err") or how == "no-editor":
                _shot(vpage, "activity_no_editor")
                raise RuntimeError(f"Could not set the description ({how}).")
            step(f"Description filled via {how}")

            refused = []
            for name, value in fields.items():
                res = vpage.evaluate(_SET_FIELD_JS, [name, value])
                if not res.get("ok"):
                    refused.append(f"{name} ({res.get('why') or res.get('got')})")
            if refused:
                _shot(vpage, "activity_field_refused")
                raise RuntimeError("The form would not take: " + "; ".join(refused))
            step(f"{len(fields)} fields set and read back in-page")

            shot = SHOT_DIR / (f"activity_{'dryrun' if dry_run else 'live'}_"
                               f"{group_label or idc}_s{spec.section}_"
                               f"{datetime.now():%Y%m%d_%H%M%S}.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — form filled and screenshotted, NOT saved")
                out["ok"] = True
                return out

            vpage.locator("#id_submitbutton2").first.click(timeout=15000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=90000)
            except PWTimeout:
                pass

            still = vpage.evaluate(_SAVE_REFUSED_JS)
            if still is not None:
                _shot(vpage, "activity_save_refused")
                raise RuntimeError(
                    "Moodle refused the save (still on modedit.php): "
                    + ("; ".join(still["errors"]) or "no message shown"))
            step("Saved")

            # 🔴 The marker walk CANNOT verify an assign or a forum. A label's HTML *is* its
            # body, so its marker comment is always in the course page. An assign's description
            # is only rendered there when `showdescription=1`, which is off by default — so the
            # first live run reported "saved, but the marker was not visible" about three
            # activities that had all been created correctly. Verify by the professor-visible
            # identity instead: the name, in the section we targeted.
            vpage.goto(f"https://{host}/course/view.php?id={idc}&section={spec.section}",
                       wait_until="domcontentloaded", timeout=60000)
            found = vpage.evaluate(_FIND_BY_NAME_JS, [spec.section, spec.name.strip()])
            if found and found.get("cmid"):
                out["cmid"] = found["cmid"]
                step(f"Verified in section {spec.section} (cmid={out['cmid']})")
            else:
                step(f"WARNING: saved, but {spec.name!r} was not found in section "
                     f"{spec.section}")
            out["ok"] = True
            out["course_url"] = f"https://{host}/course/view.php?id={idc}&section={spec.section}"
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            try:
                if page is not None:
                    _shot(page, "activity_error")
            except Exception:
                pass
            log.error(f"Create activity failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass
