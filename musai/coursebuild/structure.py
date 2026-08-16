"""Rename a section (tab), and show/hide an activity or a whole tab. LOCAL RUNNER ONLY.

The structural edits the 2026-2 course rework needs, and deliberately nothing more:

* **Rename a tab** — `editsection.php`, the same form `publish_section.py` already drives for
  the course-home summary. Only the *name* field is touched; the summary is left alone.
* **Hide / show an activity** — the action-menu URL, so `Watch and Write` can be retired
  without deleting it.
* **Hide / show a section** — the same shape one level up, and measured 2026-08-11 on 9048
  (`scratchpad/probe_section_visibility_and_filters.py`) rather than assumed. Two findings
  shaped `set_section_visibility` below: `editsection.php` carries **no `visible` control at
  all** — its twelve fields are name, summary, the onetopic tab styling and availability — so
  the action link is not merely the convenient path, it is the *only* one; and that link is a
  **mutating GET**, the same trap `remove.delete_section` documents one module over. The dry
  run therefore stops at "the link exists", exactly as that one does.

  🔴 This module still issues no destructive URL of any kind, and
  `test_there_is_no_delete_in_this_module` reads the source to keep it that way — it caught an
  earlier draft of *this very paragraph* for naming the destructive query parameter in prose.
  A ban that scans source cannot tell a comment from a call, and that is the fail-closed
  direction to be wrong in.

🔴 **There is no delete here, and there should not be.** the owner's own rule for REMOVE is
"prefer hide over delete; build deletion last and build it paranoid". Hiding is reversible and
keeps every submission and grade behind it; deleting a Moodle activity is not obviously
reversible. Retiring `Watch and Write` is exactly the case this was written for.

🔴 **Renaming is safe; MOVING is not.** A section move renumbers every later section, and both
`Activity.partial_id` and the Cronograma's stored manual tab overrides are keyed by section
number — a move would silently re-point activities at the wrong parcial and surface as a wrong
*grade*, not a wrong layout. So this module renames in place and offers no move.
"""

import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.coursebuild.publish import editing_on, enter_course

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

_EDIT_LINK_SEL = '#section-{n} a[href*="editsection.php"]'

# 🔴 The fields are `name[value]` and `name[customize]` — measured 2026-08-08
# (`scratchpad/probe_sectionname.py`), NOT the `name_value` / `name_customize` the underscore
# convention suggests.
#
# 🔴 `name[customize]` appears TWICE: a hidden `value=0` (so an unticked box still posts
# something) and the real checkbox. `querySelector` returns the hidden one, which cannot be
# clicked — so the checkbox must be selected by type, not by name alone. Same shape as
# `completionsubmit` on the assign form.
#
# The checkbox controls a text input Moodle DISABLES while it is unticked, and a disabled input
# is never submitted — so a rename would silently do nothing. Tick it by CLICKING, never by
# assigning `.checked`, so Moodle's own handler runs and re-enables the field. (Exactly the
# trap the Cronograma hit with the date-enable checkboxes.)
_NAME_FIELDS_JS = """
const box = document.querySelector('input[type="checkbox"][name="name[customize]"], '
                                 + 'input#id_name_customize');
const input = document.querySelector('input[name="name[value]"], input#id_name_value');
"""

_READ_SECTION_NAME_JS = """
() => {
  %s
  if (!input) return null;
  return {name: input.value || '', customize: box ? box.checked : null,
          disabled: input.disabled};
}
""" % _NAME_FIELDS_JS

_SET_SECTION_NAME_JS = """
(wanted) => {
  %s
  if (!input) return {ok: false, why: 'no name[value] input on this form'};
  if (box && !box.checked) box.click();      // click — Moodle listens and re-enables the input
  if (input.disabled) return {ok: false, why: 'name input still disabled after ticking'};
  input.value = wanted;
  input.dispatchEvent(new Event('input', {bubbles: true}));
  return {ok: input.value === wanted, got: input.value,
          customize: box ? box.checked : null};
}
""" % _NAME_FIELDS_JS

# Moodle renders hide/show as an action-menu link carrying `hide=<cmid>` or `show=<cmid>` plus
# a sesskey. Read the real href rather than building one: the sesskey is per session and the
# path has moved between Moodle versions.
_FIND_VISIBILITY_LINK_JS = """
([cmid, action]) => {
  const want = new RegExp(`[?&]${action}=${cmid}\\\\b`);
  for (const a of document.querySelectorAll('a[href]')) {
    if (want.test(a.getAttribute('href'))) return a.href;
  }
  return null;
}
"""

# Finds the activity on the course page — used to prove the cmid is really in this section, and
# to put a human-readable name in the result. 🔴 It deliberately does NOT report hidden/shown:
# measured 2026-08-08, this Moodle's course page carries **no class or badge that distinguishes
# a hidden activity from a visible one** for a teacher in editing mode. An earlier version
# guessed at `.hidden`/`.badge` and reported three *successful* hides as failures. Visibility is
# read from the authoritative place instead — see `_MODEDIT_VISIBLE_JS`.
_ACTIVITY_STATE_JS = """
(cmid) => {
  const el = document.getElementById('module-' + cmid);
  if (!el) return null;
  return {text: (el.innerText || '').trim().slice(0, 120)};
}
"""

# The one unambiguous source: the activity's own settings form.
# Measured options (both the assign and the forum form, 2026-08-08):
#   1 = Mostrar en página del curso · 0 = Ocultarle a estudiantes ·
#  -1 = Hacer disponible, pero no mostrar en página del curso
# ⚠️ An earlier version of this comment said the third state was `2`. It is **-1**. Nothing
# depended on it (MUSAI only writes 0 or 1), but a stealth-mode feature would have written a
# value the select does not carry. See COURSE_EDITING.md §3.
_MODEDIT_VISIBLE_JS = """
() => {
  const sel = document.querySelector('select[name="visible"]');
  return sel ? sel.value : null;
}
"""


VISIBLE_SHOWN = "1"
VISIBLE_HIDDEN = "0"

# A SECTION's hide/show link, told apart from an activity's by two independent things at once:
# the href names the section (`section=<n>`) *and* the toggled id equals that same section
# number. An activity's link carries a seven-digit cmid there, so the pair can only agree for a
# real section link. Measured 2026-08-11 on 9048 §10:
#   https://virtual3.uach.mx/course/view.php?id=9048&section=10&sesskey=…&show=10
_FIND_SECTION_VISIBILITY_LINK_JS = """
([n, action]) => {
  const mods = new Set([...document.querySelectorAll('[id^="module-"]')]
      .map(m => m.id.replace('module-', '')));
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(new RegExp('[?&]' + action + '=(\\\\d+)\\\\b'));
    if (!m || m[1] !== String(n)) continue;
    if (!new RegExp('[?&]section=' + n + '\\\\b').test(href)) continue;
    if (mods.has(m[1])) continue;              // a cmid that happens to look like a section
    return a.href;
  }
  return null;
}
"""

# 🔴 Which link Moodle renders IS the state, and it is the only state this Moodle exposes:
# there is no `visible` field on the section form to read back from (unlike an activity, whose
# `modedit.php` is authoritative). A hidden section offers *Mostrar tópico/tema*; a shown one
# offers *Ocultar*. Reading which of the two exists is therefore an authoritative read of
# Moodle's own opinion, not an inference from styling — the mistake `_ACTIVITY_STATE_JS`
# documents.
_READ_SECTION_VISIBLE_JS = """
(n) => {
  const has = (action) => {
    for (const a of document.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href') || '';
      if (new RegExp('[?&]' + action + '=' + n + '\\\\b').test(href) &&
          new RegExp('[?&]section=' + n + '\\\\b').test(href)) return true;
    }
    return false;
  };
  const canHide = has('hide'), canShow = has('show');
  if (canHide && !canShow) return '1';
  if (canShow && !canHide) return '0';
  return null;                                  // ambiguous — refuse rather than guess
}
"""


class StructureRefused(RuntimeError):
    """A precondition failed. Nothing has been written when this is raised."""


def _read_visible(vpage, host: str, cmid: str) -> str | None:
    """Authoritative visibility of one activity: its own settings form.

    🔴 Deliberately not read off the course page. Measured 2026-08-08: this Moodle renders no
    class or badge that separates a hidden activity from a visible one in editing mode, so a
    course-page check reported three *successful* hides as failures — a verification that cries
    wolf costs exactly as much trust as one that misses a real fault.
    """
    vpage.goto(f"https://{host}/course/modedit.php?update={cmid}",
               wait_until="domcontentloaded", timeout=60000)
    try:
        vpage.wait_for_load_state("networkidle", timeout=25000)
    except PWTimeout:
        pass
    return vpage.evaluate(_MODEDIT_VISIBLE_JS)


def _shot_path(kind: str, dry_run: bool, label: str) -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SHOT_DIR / (f"{kind}_{'dryrun' if dry_run else 'live'}_{label}_"
                       f"{datetime.now():%Y%m%d_%H%M%S}.png")


def rename_section(*, idc: str, section: int, new_name: str, dry_run: bool = True,
                   headless: bool = True, group_label: str = "", as_user: str | None = None,
                   on_step=None) -> dict:
    """Rename one tab. Touches the name field only — never the section summary."""
    if not new_name.strip():
        raise StructureRefused("A tab needs a name; a blank one reverts to Moodle's default.")

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": section,
                 "new_name": new_name, "old_name": None, "section_id": None,
                 "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"Rename section {section} → {new_name!r} (idc={idc}) "
               f"{'[DRY RUN]' if dry_run else '[LIVE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = page = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc, as_user=as_user)

            # Onetopic renders one tab at a time, so the *Editar sección* link only exists in
            # the DOM for the section actually displayed.
            editing_on(vpage, host, idc, section=section)
            step(f"Editing mode on (section {section} displayed)")

            link = vpage.locator(_EDIT_LINK_SEL.format(n=section)).first
            if not link.count():
                _shot(vpage, "rename_no_edit_link")
                raise StructureRefused(
                    f"No editsection link inside #section-{section}. Is that section displayed?")
            href = link.get_attribute("href") or ""
            m = re.search(r"editsection\.php\?id=(\d+)", href)
            if not m:
                raise StructureRefused(f"Could not read a section id out of {href!r}.")
            out["section_id"] = m.group(1)
            step(f"Section id {out['section_id']} resolved from the page")

            vpage.goto(f"https://{host}/course/editsection.php?id={out['section_id']}",
                       wait_until="domcontentloaded", timeout=60000)
            vpage.wait_for_load_state("networkidle", timeout=25000)
            if not vpage.locator("#id_submitbutton").count():
                _shot(vpage, "rename_no_form")
                raise StructureRefused("The section form did not load (no #id_submitbutton).")

            before = vpage.evaluate(_READ_SECTION_NAME_JS)
            if before is None:
                _shot(vpage, "rename_no_name_field")
                raise StructureRefused(
                    "No name field on this section form. Refusing to guess which control holds "
                    "the tab name.")
            out["old_name"] = before["name"]
            step(f"Current name: {before['name']!r} (customize={before['customize']})")
            if before["name"] == new_name.strip():
                step("Already named that — nothing to do")
                out["ok"] = True
                return out

            res = vpage.evaluate(_SET_SECTION_NAME_JS, new_name)
            if not res.get("ok"):
                _shot(vpage, "rename_refused")
                raise StructureRefused(f"The name field would not take it: {res}")
            step(f"Name field set to {new_name!r} and read back")

            shot = _shot_path("rename", dry_run, f"{group_label or idc}_s{section}")
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — name filled and screenshotted, NOT saved")
                out["ok"] = True
                return out

            # 🔴 `#id_submitbutton`, never position: this form's FIRST submit is the course
            # SEARCH box (the third page in this project with that shape).
            vpage.locator("#id_submitbutton").first.click(timeout=15000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=60000)
            except PWTimeout:
                pass
            step("Saved")

            # Verify from the course page, not from the fact that we clicked.
            vpage.goto(f"https://{host}/course/view.php?id={idc}&section={section}",
                       wait_until="domcontentloaded", timeout=60000)
            body = vpage.inner_text("body")
            if new_name.strip() in body:
                step(f"Verified: {new_name!r} is on the course page")
            else:
                step(f"WARNING: saved, but {new_name!r} was not found on the course page")
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            log.error(f"Rename failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def set_activity_visibility(*, idc: str, section: int, cmid: str, visible: bool,
                            dry_run: bool = True, headless: bool = True,
                            group_label: str = "", as_user: str | None = None,
                            on_step=None) -> dict:
    """Hide or show one activity, by cmid. Reversible by construction — nothing is deleted."""
    action = "show" if visible else "hide"
    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": section,
                 "cmid": str(cmid), "action": action, "before": None, "after": None,
                 "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"{action.title()} cmid={cmid} in section {section} (idc={idc}) "
               f"{'[DRY RUN]' if dry_run else '[LIVE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = page = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc, as_user=as_user)
            editing_on(vpage, host, idc, section=section)
            step(f"Editing mode on (section {section} displayed)")

            # Read the activity BEFORE deciding anything — the name in the result is what makes
            # the audit row readable later, and it proves we found the right cmid.
            found = vpage.evaluate(_ACTIVITY_STATE_JS, str(cmid))
            if found is None:
                _shot(vpage, "visibility_no_activity")
                raise StructureRefused(
                    f"cmid {cmid} is not on section {section}. Refusing to act on a cmid this "
                    "page does not show — a wrong cmid is a wrong activity.")
            want = VISIBLE_SHOWN if visible else VISIBLE_HIDDEN
            state = _read_visible(vpage, host, cmid)
            out["before"] = {"text": found["text"], "visible": state}
            step(f"Found: {found['text'][:60]!r} (visible={state})")

            if state == want:
                step(f"Already {'hidden' if not visible else 'shown'} — nothing to do")
                out["ok"] = True
                out["after"] = out["before"]
                return out

            # 🔴 `_read_visible` NAVIGATED AWAY — it is standing on `modedit.php`, which has no
            # action menu and therefore no show/hide link. Without coming back, the lookup
            # below searches the settings form and always fails.
            #
            # This was COURSE_EDITING §10's open question 5, *"why does this work on 9023 and
            # refuse on 9026?"* — filed as a difference between two courses. It is not: it is
            # this line missing, and it fails on every course, in both directions, whenever the
            # activity is not already in the wanted state. It looked course-specific because
            # the only runs that passed were the ones that no-oped early.
            # → **A refusal that names the remote system is a hypothesis, not a diagnosis.**
            editing_on(vpage, host, idc, section=section)
            url = vpage.evaluate(_FIND_VISIBILITY_LINK_JS, [str(cmid), action])
            if not url:
                _shot(vpage, "visibility_no_link")
                raise StructureRefused(
                    f"No {action}={cmid} link on the page. Moodle renders it in the activity's "
                    "action menu; the href carries the sesskey, so it is read, never built.")
            step(f"Found the {action} link")

            shot = _shot_path("visibility", dry_run, f"{group_label or idc}_cm{cmid}")
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step(f"DRY RUN — would {action} cmid {cmid}, link NOT followed")
                out["ok"] = True
                return out

            vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=45000)
            except PWTimeout:
                pass

            after = _read_visible(vpage, host, cmid)
            out["after"] = {"text": found["text"], "visible": after}
            if after is None:
                raise StructureRefused(
                    f"cmid {cmid} has no settings form after the {action} — did it vanish?")
            if after != want:
                raise StructureRefused(
                    f"Followed the {action} link but cmid {cmid} still reads visible={after!r} "
                    f"(wanted {want!r}).")
            step(f"Verified on the settings form: visible={after}")
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            log.error(f"{action.title()} failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def set_section_visibility(*, idc: str, section: int, visible: bool, expect_name: str,
                           dry_run: bool = True, headless: bool = True,
                           group_label: str = "", as_user: str | None = None,
                           on_step=None) -> dict:
    """Hide or reveal a whole tab. Reversible by construction — nothing is deleted.

    `expect_name` is required, and it is the same rail `remove.delete_section` carries for the
    same reason: **section numbers shift under every insert and delete**, so the number alone
    cannot say which tab this is. Revealing the wrong tab publishes content to students that
    someone deliberately hid; that is cheap to undo and expensive to not notice.

    ⚠️ Revealing a tab changes what `coursedates/tabmap.py` sees — hidden tabs resolve to
    `skip` — so the caller owes the course a fresh read and a re-run of the mapper afterwards.
    """
    if not (expect_name or "").strip():
        raise StructureRefused(
            "expect_name is required: section numbers renumber under inserts and deletes, so a "
            "bare number cannot identify a tab.")

    action = "show" if visible else "hide"
    want = VISIBLE_SHOWN if visible else VISIBLE_HIDDEN
    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": section,
                 "action": action, "expect_name": expect_name, "found_name": None,
                 "before": None, "after": None, "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"{action.title()} section {section} of idc={idc} "
               f"{'[DRY RUN]' if dry_run else '[LIVE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
            vpage, host = enter_course(ctx, ctx.new_page(), idc, as_user=as_user)
            editing_on(vpage, host, idc, section=section)
            step(f"Editing mode on (section {section} displayed)")

            name = vpage.evaluate(
                """(n) => {
                    const sec = document.querySelector('#section-' + n);
                    if (!sec) return null;
                    const h = sec.querySelector('.sectionname, h3');
                    return ((h && h.textContent) || '').replace(/\\s+/g, ' ').trim();
                }""", section)
            if name is None:
                _shot(vpage, "secvis_no_section")
                raise StructureRefused(f"Section {section} is not on this course page.")
            out["found_name"] = name
            if name.strip() != expect_name.strip():
                raise StructureRefused(
                    f"§{section} is named {name!r}, not {expect_name!r}. Refusing — a "
                    "renumbering has moved the target, or the caller means a different tab.")
            step(f"Confirmed §{section} is {name!r}")

            state = vpage.evaluate(_READ_SECTION_VISIBLE_JS, section)
            out["before"] = state
            if state is None:
                _shot(vpage, "secvis_ambiguous")
                raise StructureRefused(
                    f"Could not tell whether §{section} is hidden: the page offers neither a "
                    "hide nor a show link for it, or both. Refusing to guess.")
            step(f"Currently visible={state}")

            if state == want:
                step(f"Already {'shown' if visible else 'hidden'} — nothing to do")
                out["ok"] = True
                out["after"] = state
                return out

            url = vpage.evaluate(_FIND_SECTION_VISIBILITY_LINK_JS, [section, action])
            if not url:
                _shot(vpage, "secvis_no_link")
                raise StructureRefused(
                    f"No section-level {action}={section} link on the page. The href carries the "
                    "sesskey, so it is read, never built.")
            step(f"Found the section {action} link")

            shot = _shot_path("secvis", dry_run, f"{group_label or idc}_s{section}")
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step(f"DRY RUN — would {action} §{section}. 🔴 The link is NOT followed: for a "
                     "section this GET is the mutation, so there is nothing to open and abandon.")
                out["ok"] = True
                return out

            vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=45000)
            except PWTimeout:
                pass
            step(f"{action.title()} GET issued (this was the mutation)")

            editing_on(vpage, host, idc, section=section)
            after = vpage.evaluate(_READ_SECTION_VISIBLE_JS, section)
            out["after"] = after
            if after != want:
                raise StructureRefused(
                    f"Followed the {action} link but §{section} still reads visible={after!r} "
                    f"(wanted {want!r}).")
            step(f"Verified: Moodle now offers the opposite link — visible={after}")
            out["ok"] = True
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            log.error(f"Section {action} failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass
