"""Course-level text FILTERS — the one course *setting* MUSAI is allowed to write.

It exists for a single measured defect. Moodle's **auto-linking of activity names**
(`filter_activitynames`) rewrites any occurrence of an activity's name into a link to that
activity **at render time**, long after KSES has stored our markup. Inside a Vellum hero it
repaints a white `<h1>` as a purple link on a purple gradient: measured **1.07 – 1.18 : 1**
contrast against WCAG's 3.0 minimum for large text, across 12 hero titles in English I's three
books (COURSE_EDITING §8, `scratchpad/probe_autolink.py`). Nothing we can put in the HTML wins
— the filter replaces the text node, so the link's own colour beats any `style` of ours, and
re-wording the title does not help because the filter matches the activity name as a whole word
anywhere in the text.

Measured on 9048, 2026-08-11 (`scratchpad/probe_section_visibility_and_filters.py`):

    /filter/manage.php?contextid=<M.cfg.contextid>
    six selects, one per filter, named by the filter's own slug
    options: 0 = «Por defecto (Activado)» · -1 = «Desactivado» · 1 = «Activado»
    one submit, name=savechanges

🔴 **`0` is not off.** It is *inherit the site default*, and the site default here is ON — which
is exactly why every course in this project carries the defect while every select reads `0`. Off
is `-1`.

## The rails, and why each one is here

1. 🔴 **`WRITABLE` is a one-entry allow-list, and the entry that matters is the one that is
   NOT in it.** `mediaplugin` is the filter that turns a bare YouTube URL into an embedded
   player — it is what makes the 21 chapters of English II's books carry video at all. Six
   selects sit on one form behind one Save button, so a writer that took `**changes` at face
   value is one typo away from silently un-embedding every video in the course, and the failure
   would look like nothing at all until a student opened a chapter.
2. **`ALLOWED_STATES` excludes `1`.** Turning a filter *on* course-wide is a decision with
   effects nobody measured; this module exists to turn one specific thing off.
3. **Dry-run by default** (rail 2 of the project), and the dry run fills the select, screenshots
   it and never submits.
4. 🔴 **Every untargeted filter is compared before and after.** One form, one Save, six selects:
   the danger is not the value we meant to write, it is the five we did not. A change to any of
   them fails the run loudly instead of being discovered a semester later.
"""

from datetime import datetime
from pathlib import Path
from typing import Mapping

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.coursebuild.publish import enter_course

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

#: The only filter this module may write. See rail 1 — the point of the list is `mediaplugin`'s
#: absence from it, not `activitynames`' presence.
WRITABLE = frozenset({"activitynames"})

#: `0` = «Por defecto (Activado)», `-1` = «Desactivado». `1` («Activado») is deliberately absent.
ALLOWED_STATES = {"-1": "Desactivado", "0": "Por defecto (Activado)"}

OFF = "-1"
INHERIT = "0"

_CONTEXTID_JS = "() => (window.M && M.cfg && M.cfg.contextid) || null"

_READ_FILTERS_JS = """
() => {
  const out = {};
  for (const sel of document.querySelectorAll('select')) {
    if (sel.name) out[sel.name] = sel.value;
  }
  return out;
}
"""

# Assign, then dispatch `change`, then read back — the same shape `structure.py` uses for the
# section-name input. A select that silently rejects a value is a documented trap in this
# codebase (COURSE_EDITING §2), so the return value is the select's own opinion, never ours.
_SET_FILTER_JS = """
([name, value]) => {
  const sel = document.querySelector('select[name="' + name + '"]');
  if (!sel) return {ok: false, why: 'no select named ' + name};
  if (![...sel.options].some(o => o.value === value))
    return {ok: false, why: 'option ' + value + ' is not offered'};
  sel.value = value;
  sel.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: sel.value === value, got: sel.value};
}
"""


class FilterRefused(RuntimeError):
    """A rail failed. **Nothing has been written** when this is raised."""


def plan_filter_changes(changes: Mapping[str, str]) -> dict[str, str]:
    """Validate a filter change against the allow-lists. Pure — refuses before a browser exists.

    This is the whole of rail 1 and rail 2, and it is a separate function so both can be tested
    without a network, a login, or a course.
    """
    if not changes:
        raise FilterRefused("No filter changes requested.")
    planned: dict[str, str] = {}
    for name, value in changes.items():
        if name not in WRITABLE:
            raise FilterRefused(
                f"{name!r} is not writable. This module may only change "
                f"{', '.join(sorted(WRITABLE))} — every other filter on that form is left "
                "exactly as the professor has it. In particular `mediaplugin` is what embeds "
                "the YouTube players inside the term books, and switching it off would empty "
                "them without any visible error.")
        value = str(value)
        if value not in ALLOWED_STATES:
            raise FilterRefused(
                f"{value!r} is not an allowed state for {name!r}. Allowed: "
                + ", ".join(f"{k} ({v})" for k, v in ALLOWED_STATES.items())
                + ". Note that `0` means «inherit the site default», which is ON here — off "
                  "is `-1`.")
        planned[name] = value
    return planned


def _shot_path(dry_run: bool, label: str) -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SHOT_DIR / (f"filters_{'dryrun' if dry_run else 'live'}_{label}_"
                       f"{datetime.now():%Y%m%d_%H%M%S}.png")


def set_course_filters(*, idc: str, changes: Mapping[str, str], dry_run: bool = True,
                       headless: bool = True, group_label: str = "", as_user: str | None = None,
                       on_step=None) -> dict:
    """Set one or more course-level filters. Reversible: re-run with the previous value.

    🔴 `as_user` (2026-08-12) logs in as another professor, exactly as `enter_course` and
    `publish_section` already do — the owner's account cannot reach a colleague's filter page at
    all, so without this the filter of a propagated course could be neither set nor even read.

    ⚠️ **It was added on a WRONG premise, and the measurement is worth more than the premise
    was.** The reasoning was *"a course backup does not carry filter overrides, so every restored
    copy comes up with the auto-link filter back ON."* Measured the same day, with the three
    not-yet-restored English III targets as the control:

    | course | `activitynames` |
    |---|---|
    | 9068 / 9069 / 9071, blank shells, before any restore | `'0'` (inherit ⇒ ON) |
    | 9072, immediately after restoring the master | `'-1'` (OFF) |

    **A backup does carry the course-level filter override.** The propagated courses need no
    filter write at all. This parameter earns its place anyway — it is what made that
    measurement possible, and a *read* of a colleague's filter is the only way to know.
    ⭐ The general form: an addition justified by a belief about a remote system should be
    justified again by a measurement of it, or the belief ships in a docstring and outlives the
    session that invented it.

    ⚠️ The returned dict carries `as_user` so the caller can put it in the audit row's
    `on_behalf_of`: Moodle records the account, and only MUSAI can record who decided.
    """
    planned = plan_filter_changes(changes)          # refuses before a browser is launched

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "planned": planned,
                 "as_user": as_user, "contextid": None, "before": None, "after": None,
                 "collateral": None, "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"Course filters {planned} on idc={idc}"
               f"{f' as {as_user}' if as_user else ''} "
               f"{'[DRY RUN]' if dry_run else '[LIVE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1100})
            vpage, host = enter_course(ctx, ctx.new_page(), idc, as_user=as_user)

            contextid = vpage.evaluate(_CONTEXTID_JS)
            if not contextid:
                _shot(vpage, "filters_no_contextid")
                raise FilterRefused(
                    "The course page published no M.cfg.contextid, so there is no course "
                    "context to scope the filters to. Refusing — the site-wide filter page "
                    "lives at the same path and would change every course on this Moodle.")
            out["contextid"] = str(contextid)
            step(f"Course context {contextid}")

            url = f"https://{host}/filter/manage.php?contextid={contextid}"
            vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass

            before = vpage.evaluate(_READ_FILTERS_JS)
            if not before:
                _shot(vpage, "filters_no_selects")
                raise FilterRefused(f"No filter selects on {url}.")
            out["before"] = before
            step(f"Read {len(before)} filters: {before}")

            missing = [n for n in planned if n not in before]
            if missing:
                raise FilterRefused(
                    f"This course's filter page does not offer {missing}. Refusing rather than "
                    "writing a control that is not there.")

            if all(before[n] == v for n, v in planned.items()):
                step("Every requested filter already has the wanted value — nothing to do")
                out["ok"] = True
                out["after"] = before
                return out

            for name, value in planned.items():
                res = vpage.evaluate(_SET_FILTER_JS, [name, value])
                if not res.get("ok"):
                    _shot(vpage, "filters_set_refused")
                    raise FilterRefused(f"{name} would not take {value!r}: {res}")
                step(f"{name}: {before[name]!r} → {value!r} "
                     f"({ALLOWED_STATES[value]})")

            shot = _shot_path(dry_run, group_label or idc)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — selects filled and screenshotted, NOT saved")
                out["ok"] = True
                return out

            # 🔴 By name, never by position: the first submit on this page is the site search
            # box, the same shape that has caught this project on three other pages.
            btn = vpage.locator("input[name='savechanges'], button[name='savechanges']").first
            if not btn.count():
                _shot(vpage, "filters_no_save")
                raise FilterRefused("No savechanges button on the filter page.")
            btn.click(timeout=20000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=60000)
            except PWTimeout:
                pass
            step("Saved")

            # Verify by re-loading the page, not by trusting the click.
            vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass
            after = vpage.evaluate(_READ_FILTERS_JS)
            out["after"] = after

            # 🔴 Rail 4 — the five selects we did NOT mean to touch rode the same Save.
            collateral = {n: (before[n], after.get(n)) for n in before
                          if n not in planned and after.get(n) != before[n]}
            out["collateral"] = collateral
            if collateral:
                raise FilterRefused(
                    f"The save changed filters this run never targeted: {collateral}. "
                    "They shared the form and the button.")

            wrong = {n: after.get(n) for n, v in planned.items() if after.get(n) != v}
            if wrong:
                raise FilterRefused(f"Saved, but the page still reads {wrong} "
                                    f"(wanted {planned}).")
            step(f"Verified on a fresh load: {({n: after[n] for n in planned})}")
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"REFUSED/ERROR: {out['error']}")
            log.error(f"Filter change failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


__all__ = ["FilterRefused", "plan_filter_changes", "set_course_filters",
           "WRITABLE", "ALLOWED_STATES", "OFF", "INHERIT"]
