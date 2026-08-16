"""Rename one activity, and change **nothing else**. LOCAL RUNNER ONLY.

Deliberately not a function in `activity.py`, and deliberately not a flag on `create_activity`.

🔴 **`create_activity` cannot do this, and must not learn to.** It refuses `modname='quiz'`
outright — *"quizzes carry far more options and should reuse moodle_suite/generators' Moodle-XML
instead of driving this form"* — and that refusal is right: a quiz form carries review options,
grading method, question behaviour, attempt limits and its own dates, and `ActivitySpec` has an
opinion about none of them. It also **requires `intro_html`** and always writes it, so routing a
rename through it would silently replace the professor's description with whatever the caller
happened to pass.

English III needed four quizzes renamed (`Simple Present II` really contains Adverbs of
Frequency, and three more like it). The narrow operation that serves that is:

    load the activity's own settings form · verify it is the one named ·
    type into `input[name="name"]` · save · read the name back

Every other control on the page is submitted back **exactly as Moodle rendered it**, which is
what makes this safe on a module type nothing here understands. The rails:

1. 🔴 **`expect_name` is required**, and must equal the form's current name. Same rail as
   `remove.delete_activity`, for the same reason: a mistyped cmid loads a perfectly valid form
   for the *wrong* activity and every later check passes.
2. 🔴 **Refuses a `label`.** A label's `modedit.php` has no `input[name="name"]` at all
   (measured on 9048) — Moodle derives the displayed name from the intro HTML. There is nothing
   here to rename.
3. 🔴 **Refuses to create a duplicate name inside the section.** Two activities sharing a name
   make `coursedates/mapping.py` return AMBIGUOUS and assign **no `partial_id` at all** — which
   surfaces as a wrong *grade*, not a wrong layout. This is the one hazard a rename has that a
   delete does not, so it is the one rail this module adds.
4. **Dry run by default**, and a dry run proves rails 1–3 and leaves the form unsaved.
5. **Reads the date fields before and after** and reports them. `musai/coursedates` owns
   `timeopen`/`timeclose`, and COURSE_EDITING §4's standing rule is that no two writers may
   touch the same field; submitting the form round-trips them, so the result carries the
   evidence that they came back unchanged rather than an assurance that they did.

A rename is reversible — `previous_name` is in the result and in the audit row — so this module
is allowed to be less paranoid than `remove.py`. It is not allowed to be careless.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.coursebuild.publish import editing_on, enter_course

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

#: Module types with no `name` field on their settings form, so there is nothing to rename.
NO_NAME_FIELD = frozenset({"label"})

#: Fields another writer owns. Read before and after purely as evidence; never written here.
#: ⚠️ A quiz form also carries `attemptopen`/`attemptclose`-shaped names — COURSE_EDITING §4:
#: *"never pattern-match `*open` / `*closed` on a quiz form"*. These are named exactly.
WATCHED_FIELDS = ("timeopen", "timeclose", "duedate", "allowsubmissionsfromdate", "cutoffdate")

_MODEDIT_JS = """
() => ({
  name: (document.querySelector('input[name="name"]') || {}).value ?? null,
  modulename: (document.querySelector('input[name="modulename"]') || {}).value ?? null,
  visible: (document.querySelector('[name="visible"]') || {}).value ?? null,
  has_name_field: !!document.querySelector('input[name="name"]'),
})
"""

# Every date-ish control Moodle renders as a group of selects plus an enable checkbox. Read as
# a flat {control_name: value} map so a before/after comparison is a plain dict equality.
_WATCHED_JS = """
(names) => {
  const out = {};
  for (const base of names) {
    for (const el of document.querySelectorAll(
        `[name="${base}"], [name^="${base}["]`)) {
      out[el.getAttribute('name')] =
        el.type === 'checkbox' ? (el.checked ? '1' : '0') : String(el.value);
    }
  }
  return out;
}
"""

# The professor-visible name of every activity in the displayed section, by cmid. Rail 3 needs
# to know whether the NEW name is already taken by something else here.
_SECTION_NAMES_JS = """
(section) => {
  const root = document.getElementById('section-' + section) || document;
  const out = {};
  for (const el of root.querySelectorAll('[id^="module-"]')) {
    if (el.id.endsWith('_shim')) continue;
    const link = el.querySelector('.instancename, .activityname a, a.aalink');
    const text = ((link ? link.innerText : el.innerText) || '').trim().split('\\n')[0].trim();
    out[el.id.replace('module-', '')] = text;
  }
  return out;
}
"""

# Same as `activity.py`'s: a save Moodle refused still navigates, so clicking proves nothing.
_SAVE_REFUSED_JS = """
() => {
  if (!location.pathname.includes('/course/modedit.php')) return null;
  const errs = [...document.querySelectorAll('.error, .invalid-feedback, [id$="_error"]')]
      .map(e => e.innerText.trim()).filter(Boolean);
  return {url: location.href, errors: errs.slice(0, 6)};
}
"""


class RenameRefused(RuntimeError):
    """A rail failed. **Nothing has been renamed** when this is raised."""


def watched_diff(before: dict, after: dict) -> dict:
    """Which coursedates-owned controls genuinely moved: `{name: (before, after)}`.

    🔴 **A disabled date group is not state, it is a clock.** Measured 2026-08-12 on 9067: every
    quiz there has `timeclose[enabled] = 0`, and Moodle pre-fills the greyed-out day/hour/minute
    selects underneath with **the current wall-clock time**. Reading the form before a save and
    again after it therefore reports `timeclose[minute]: 32 → 33` whenever a minute happens to
    roll over in between — on a control Moodle does not store and this module never touched.

    A naive dict comparison shipped exactly that: three of the four English III renames reported
    `dates_unchanged=True` and the fourth reported a "change", differing only in how long the
    save took. **That is a crying-wolf rail, and a crying-wolf rail gets made precise, never
    permissive** — so a group is compared only while its own `[enabled]` box is on, and a change
    to `[enabled]` itself is always reported.
    """
    out: dict = {}
    for key, new in (after or {}).items():
        old = (before or {}).get(key)
        if old == new:
            continue
        base = key.split("[")[0]
        enable = f"{base}[enabled]"
        # The enable checkbox itself is the real state, so its movement always counts.
        if key != enable:
            was_on = str((before or {}).get(enable, "1")) == "1"
            is_on = str((after or {}).get(enable, "1")) == "1"
            if not (was_on or is_on):
                continue          # a disabled group: Moodle is showing "now", not a value
        out[key] = (old, new)
    return out


def _audit(action: str, target: str, dry_run: bool, detail: dict, out: dict) -> None:
    """Record the attempt. A failure to record must never be mistaken for a failure to act."""
    try:
        from sqlmodel import Session

        from musai.audit import log as audit_log
        from musai.db import engine

        with Session(engine) as sess:
            audit_log(sess, action, actor="carlos", target=target, dry_run=dry_run,
                      detail=detail)
            sess.commit()
    except Exception as e:                                   # pragma: no cover - env dependent
        out["audit_error"] = describe_exception(e)


def rename_activity(*, idc: str, section: int, cmid: str, new_name: str, expect_name: str,
                    dry_run: bool = True, headless: bool = True, group_label: str = "",
                    as_user: str | None = None, on_step=None) -> dict:
    """Rename `cmid` to `new_name`, having proved it is currently called `expect_name`.

    Returns a result dict; never raises for ordinary failure — the caller gets `ok=False` plus
    `error`, `steps` and a screenshot.
    """
    if not (expect_name or "").strip():
        raise RenameRefused(
            "expect_name is required: a rename must name the activity it believes it is "
            "changing, so a wrong cmid fails loudly instead of quietly renaming something else.")
    if not (new_name or "").strip():
        raise RenameRefused("new_name is empty; Moodle rejects a blank activity name.")

    new_name = new_name.strip()
    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": section,
                 "cmid": str(cmid), "expect_name": expect_name, "new_name": new_name,
                 "previous_name": None, "modname": None, "watched_before": None,
                 "watched_after": None, "watched_unchanged": None, "verified_name": None,
                 "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"RENAME cmid={cmid} → {new_name!r} (idc={idc} section={section}) "
               f"{'[DRY RUN]' if dry_run else '[LIVE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
            vpage, host = enter_course(ctx, ctx.new_page(), idc, as_user=as_user)

            # ── Rail 3, first, because it is the one that costs a grade ──────────────────
            editing_on(vpage, host, idc, section=section)
            names = vpage.evaluate(_SECTION_NAMES_JS, section)
            if str(cmid) not in names:
                _shot(vpage, "rename_not_in_section")
                raise RenameRefused(
                    f"cmid {cmid} is not inside section {section} (it holds "
                    f"{sorted(names)}). Refusing: a stale section number after a renumbering "
                    "is how the wrong activity gets edited.")
            clash = [c for c, n in names.items() if n == new_name and str(c) != str(cmid)]
            if clash:
                _shot(vpage, "rename_name_clash")
                raise RenameRefused(
                    f"section {section} already holds {new_name!r} as cmid {clash[0]}. Two "
                    "activities sharing a name make coursedates/mapping.py return AMBIGUOUS "
                    "and assign NO partial_id — a wrong grade, not a wrong layout. Delete or "
                    "rename the other one first.")
            step(f"Section {section} holds {len(names)} activities; {new_name!r} is free")

            # ── Rail 1 + 2 — the settings form is the record ─────────────────────────────
            vpage.goto(f"https://{host}/course/modedit.php?update={cmid}",
                       wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass
            info = vpage.evaluate(_MODEDIT_JS)
            out["previous_name"] = info["name"]
            out["modname"] = info["modulename"]

            if (info["modulename"] or "") in NO_NAME_FIELD or not info["has_name_field"]:
                raise RenameRefused(
                    f"cmid {cmid} is a {info['modulename']!r} and its settings form has no "
                    "name field — Moodle derives the displayed name from the intro HTML. "
                    "There is nothing here to rename.")
            if (info["name"] or "").strip() != expect_name.strip():
                raise RenameRefused(
                    f"cmid {cmid} is named {info['name']!r}, not {expect_name!r}. Refusing.")
            step(f"Confirmed {info['modulename']} {info['name']!r}")

            if not vpage.locator("#id_submitbutton2").count():
                _shot(vpage, "rename_no_form")
                raise RenameRefused("The settings form did not load (no #id_submitbutton2).")

            out["watched_before"] = vpage.evaluate(_WATCHED_JS, list(WATCHED_FIELDS))
            step(f"{len(out['watched_before'])} coursedates-owned controls read before touching "
                 "anything")

            # ── The one write ────────────────────────────────────────────────────────────
            field = vpage.locator('input[name="name"]').first
            field.fill(new_name, timeout=15000)
            got = vpage.evaluate(
                '() => (document.querySelector(\'input[name="name"]\') || {}).value ?? null')
            if got != new_name:
                _shot(vpage, "rename_field_refused")
                raise RenameRefused(f"The name field would not take {new_name!r} (reads "
                                    f"{got!r} after typing).")
            step(f"Name field now reads {got!r} — and nothing else on this form was touched")

            shot = SHOT_DIR / (f"rename_{'dryrun' if dry_run else 'live'}_"
                               f"{group_label or idc}_cm{cmid}_"
                               f"{datetime.now():%Y%m%d_%H%M%S}.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — every rail passed, form filled and screenshotted, NOT saved")
                out["ok"] = True
                return out

            vpage.locator("#id_submitbutton2").first.click(timeout=15000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=90000)
            except PWTimeout:
                pass
            still = vpage.evaluate(_SAVE_REFUSED_JS)
            if still is not None:
                _shot(vpage, "rename_save_refused")
                raise RenameRefused("Moodle refused the save (still on modedit.php): "
                                    + ("; ".join(still["errors"]) or "no message shown"))
            step("Saved")

            # ── Verify from the record, then from the page students would see ────────────
            vpage.goto(f"https://{host}/course/modedit.php?update={cmid}",
                       wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass
            after = vpage.evaluate(_MODEDIT_JS)
            out["verified_name"] = after["name"]
            out["watched_after"] = vpage.evaluate(_WATCHED_JS, list(WATCHED_FIELDS))
            changed = watched_diff(out["watched_before"], out["watched_after"])
            out["watched_unchanged"] = not changed
            if (after["name"] or "").strip() != new_name:
                raise RenameRefused(
                    f"Submitted the rename, but the form still reads {after['name']!r}.")
            step(f"Settings form now reads {after['name']!r}")
            if changed:
                # Not a refusal — the save already happened — but it must be impossible to
                # miss, because `coursedates` believes it is the only writer of these.
                out["watched_changed"] = changed
                log.error(f"🔴 coursedates-owned fields moved through a rename: {changed}")
                step(f"🔴 WARNING: {len(changed)} date control(s) differ after the save")

            editing_on(vpage, host, idc, section=section)
            page_names = vpage.evaluate(_SECTION_NAMES_JS, section)
            out["on_course_page"] = page_names.get(str(cmid))
            step(f"Course page shows {out['on_course_page']!r}")
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"REFUSED/ERROR: {out['error']}")
            log.error(f"Rename failed: {out['error']}")
            return out
        finally:
            _audit("coursebuild_rename_activity",
                   f"idc:{idc} section:{section} cmid:{cmid}", dry_run,
                   {k: v for k, v in out.items() if k != "steps"}, out)
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def plan_rename(*, cmid: str, current_name: str, new_name: str, modname: str,
                section_names: dict) -> Optional[str]:
    """The refusal `rename_activity` would raise, or `None` if every pure rail passes.

    Pure, so the rails can be tested without a browser — and so a batch script can refuse a
    whole plan before it opens one. `section_names` maps cmid → displayed name.

    ⚠️ `cmid` is not decoration: the clash rail has to exclude the activity being renamed, or
    re-running a finished rename would refuse itself.
    """
    if not (new_name or "").strip():
        return "new_name is empty; Moodle rejects a blank activity name."
    if modname in NO_NAME_FIELD:
        return (f"a {modname!r} has no name field on its settings form — Moodle derives the "
                "displayed name from the intro HTML")
    taken = [c for c, n in (section_names or {}).items()
             if n == new_name.strip() and str(c) != str(cmid)]
    if taken:
        return (f"{new_name!r} is already the name of cmid {taken[0]} in this section; two "
                "activities sharing a name make mapping.py return AMBIGUOUS")
    # A no-op rename (`current_name == new_name`) is deliberately allowed through: re-running a
    # finished batch should report "already there", not fail.
    return None


__all__ = ["RenameRefused", "rename_activity", "plan_rename", "watched_diff", "NO_NAME_FIELD",
           "WATCHED_FIELDS"]
