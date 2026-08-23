"""Change a course's FORMAT on `course/edit.php`. LOCAL RUNNER ONLY.

The first writer in this project that touches course *settings* rather than course *content*,
written for a delegated colleague's Facultad de Enfermería courses on `aulas1.uach.mx`
(Moodle **4.5**), where five blank shells have to become `onetopic` before anything else
is worth doing.

Measured on the live form 2026-08-22 (`scratchpad/probe_45_writepath.py`, idc 6253):

    GET  /course/edit.php?id=<idc>
      #id_format            <select name="format">   value 'topics'
                            options: topics · weeks · singleactivity · social · buttons ·
                                     grid · multitopic · **onetopic** · remuiformat ·
                                     softcourse · tiles
      #id_updatecourseformat  <input type=submit name="updatecourseformat">
                                                     "Actualizar formato"
      #id_saveanddisplay      <input type=submit name="saveanddisplay">
                                                     "Guardar cambios y mostrar"
      #id_cancel              <input type=submit name="cancel">

⚠️ Note what is NOT on that form: `saveandreturn`. 3.3's two-save-button habit does not carry
over, so the button is matched by id and never by position — the same lesson `editsection.php`
taught (three submits, and `input[type=submit]").first` was the *search box*).

## The rails

1. **`expect_shortname` is required.** An idc is a number, and a number aimed at the wrong
   course reformats somebody's live semester. The course names itself on the same form we are
   about to submit, so the check is free and the read is authoritative.
2. **`ALLOWED_FORMATS` is an allow-list, checked before a browser exists.** Not because the
   other formats are dangerous in themselves, but because `singleactivity` *hides every
   section but one* and `social` replaces the course with a forum — a typo away from a course
   that looks wiped.
3. **`dry_run=True` by default.** A dry run selects the value, screenshots the form, and
   navigates away without submitting.
4. **The read-back is the form, re-fetched.** Never the page the save redirected to. A course
   page rendered under a new format looks different whether or not the setting stuck, which is
   precisely the class of false success `COURSE_EDITING.md` exists to document.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.coursebuild.publish import SHOT_DIR, enter_course

#: Formats this module may select. Deliberately short — see rail 2.
#:
#: `onetopic` renders one section per tab and is what every MUSAI course already uses.
#: `topics` is here only so a change can be undone by the same code path that made it; a
#: writer that can get you into a state but not out of it is half a writer.
ALLOWED_FORMATS = {
    "onetopic": "one section per tab — the shape every other MUSAI course uses",
    "topics": "sections stacked on one page — Moodle's default, and the way back",
}

#: What Moodle 4.5's Spanish install calls them, measured off the select's own option list.
#: Recorded so a human reading a log can match it to what they see on screen; never used to
#: FIND the option, because a label is a translation and a value is an identifier.
FORMAT_LABELS_ES = {
    "onetopic": "Formato un_tópico",
    "topics": "Secciones personalizadas",
}


class FormatRefused(RuntimeError):
    """A precondition failed. Nothing has been written when this is raised."""


def plan_format_change(*, new_format: str, expect_shortname: str) -> dict:
    """Validate the request. Pure — no browser, fully testable, refuses in a millisecond.

    Returns the `{field: value}` that would be written, in the same shape `activity.plan_fields`
    returns, so the two read alike at the call site.
    """
    fmt = (new_format or "").strip()
    if fmt not in ALLOWED_FORMATS:
        raise FormatRefused(
            f"{fmt!r} is not in ALLOWED_FORMATS ({sorted(ALLOWED_FORMATS)}). Add it there "
            "deliberately, with a reason: `singleactivity` shows one activity and hides every "
            "section, and `social` replaces the course with a forum — both look like a wipe."
        )
    if not (expect_shortname or "").strip():
        raise FormatRefused(
            "expect_shortname is required: an idc is a number, and a number aimed at the "
            "wrong course reformats somebody's live semester."
        )
    return {"format": fmt}


_READ_COURSE_FORM_JS = """
() => {
  const sel = document.querySelector('#id_format');
  const sn = document.querySelector('#id_shortname');
  const fn = document.querySelector('#id_fullname');
  return {
    format: sel ? sel.value : null,
    options: sel ? [...sel.options].map(o => o.value) : [],
    shortname: sn ? sn.value : null,
    fullname: fn ? fn.value : null,
  };
}
"""

# Select the format and read it straight back. A select that silently rejects a value looks
# exactly like one that took it — the same reason `activity._SET_FIELD_JS` exists.
_SET_FORMAT_JS = """
(wanted) => {
  const sel = document.querySelector('#id_format');
  if (!sel) return {ok: false, why: 'no #id_format on this page'};
  if (sel.disabled) return {ok: false, why: 'format select is disabled'};
  if (![...sel.options].some(o => o.value === wanted)) {
    return {ok: false, why: 'no-such-option',
            options: [...sel.options].map(o => o.value)};
  }
  sel.value = wanted;
  sel.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: sel.value === wanted, got: sel.value};
}
"""


def _settle(pg, timeout: int = 15000) -> None:
    try:
        pg.wait_for_load_state("networkidle", timeout=timeout)
    except PWTimeout:
        pass


def set_course_format(*, idc: str, new_format: str, expect_shortname: str,
                      dry_run: bool = True, headless: bool = True,
                      as_user: Optional[str] = None, group_label: str = "",
                      on_step=None) -> dict:
    """Set one course's format. Reversible: pass the old format back to undo.

    Returns a result dict carrying `before`, `after` and `ok`. `ok=None` means **unknown** —
    a timeout, never `False`, because *failed* invites a retry and *unknown* invites a
    verification, and only one of those is safe against a live course.
    """
    planned = plan_format_change(new_format=new_format, expect_shortname=expect_shortname)
    want = planned["format"]

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "want": want,
                 "expect_shortname": expect_shortname, "found_shortname": None,
                 "before": None, "after": None, "host": None, "screenshot": None,
                 "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"Course format → {want!r} (idc={idc}) {'[DRY RUN]' if dry_run else '[LIVE]'}")
    ensure_subprocess_capable_loop()

    browser = ctx = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
            vpage, host = enter_course(ctx, ctx.new_page(), idc, as_user=as_user)
            out["host"] = host
            step(f"Signed in; course {idc} is on {host}")

            vpage.goto(f"https://{host}/course/edit.php?id={idc}",
                       wait_until="domcontentloaded", timeout=60000)
            _settle(vpage)

            before = vpage.evaluate(_READ_COURSE_FORM_JS)
            if before.get("format") is None:
                _shot(vpage, "format_no_form")
                raise FormatRefused(
                    f"No #id_format on course/edit.php for idc={idc} — either this account "
                    f"cannot edit the course settings, or the form has moved.")
            out["before"] = before["format"]
            out["found_shortname"] = before.get("shortname")

            # Rail 1 — the course names itself on the form we are about to submit.
            if (before.get("shortname") or "").strip() != expect_shortname.strip():
                raise FormatRefused(
                    f"idc={idc} is {before.get('shortname')!r}, not {expect_shortname!r}. "
                    f"Refusing — this is aimed at a different course.")
            step(f"Confirmed idc={idc} is {before['shortname']!r} "
                 f"(currently {before['format']!r})")

            if before["format"] == want:
                out["ok"] = True
                out["after"] = want
                step(f"Already {want!r} — nothing to do.")
                return out

            res = vpage.evaluate(_SET_FORMAT_JS, want)
            if not res.get("ok"):
                _shot(vpage, "format_set_failed")
                raise FormatRefused(
                    f"Could not select {want!r}: {res.get('why')} "
                    f"{res.get('options', '')}")
            step(f"Selected {want!r} ({FORMAT_LABELS_ES.get(want, '')})")

            shot = SHOT_DIR / (f"format_{'dryrun' if dry_run else 'live'}_"
                               f"{group_label or idc}_{datetime.now():%Y%m%d_%H%M%S}.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — form filled and abandoned, nothing submitted.")
                out["ok"] = True
                out["after"] = before["format"]
                return out

            # 🔴 «Actualizar formato» is PRESENT AND INVISIBLE on 4.5, and the difference
            # between those two costs a 30-second timeout.
            #
            # Measured live 2026-08-22 on 6253: the control exists in the DOM as
            #   <input type="submit" name="updatecourseformat" id="id_updatecourseformat"
            #          class="btn btn-primary … d-none" data-formatchooser-field="updateButton">
            # `d-none` — Moodle's formatchooser hides it whenever JS is available and re-renders
            # the format options over AJAX on the select's own `change` instead. It is the
            # no-JavaScript fallback, not the path a browser takes.
            #
            # ⭐ `locator(...).count()` answers "is it in the DOM", which is not the question a
            # click asks. The gate is `is_visible()`. Same family as the single-section view
            # trap in `editing_on`: **a container being present is not the same as the thing
            # you need being usable.**
            update_btn = vpage.locator("#id_updatecourseformat")
            try:
                clickable = update_btn.count() > 0 and update_btn.first.is_visible()
            except Exception:
                clickable = False
            if clickable:
                update_btn.first.click()
                vpage.wait_for_load_state("domcontentloaded", timeout=60000)
                _settle(vpage)
                step("Clicked «Actualizar formato» (no-JS fallback path)")
            else:
                # The select's `change` event already fired in _SET_FORMAT_JS; let the
                # formatchooser's AJAX re-render land before saving over the top of it.
                _settle(vpage)
                step("«Actualizar formato» is d-none (JS formatchooser) — not clicking it")

            again = vpage.evaluate(_SET_FORMAT_JS, want)
            if not again.get("ok"):
                _shot(vpage, "format_lost_after_update")
                raise FormatRefused(
                    f"The format select no longer holds {want!r} after the options "
                    f"re-rendered ({again.get('why')}). Nothing saved.")

            # 🔴 The only mutating step, matched by id. There is no `saveandreturn` on this
            # form, and `input[type=submit]` .first is not the save button.
            vpage.click("#id_saveanddisplay")
            vpage.wait_for_load_state("domcontentloaded", timeout=90000)
            _settle(vpage, 20000)
            step("Submitted «Guardar cambios y mostrar»")

            # Rail 4 — the authoritative read is the form, re-fetched. Not this page.
            vpage.goto(f"https://{host}/course/edit.php?id={idc}",
                       wait_until="domcontentloaded", timeout=60000)
            _settle(vpage)
            after = vpage.evaluate(_READ_COURSE_FORM_JS)
            out["after"] = after.get("format")
            out["ok"] = (after.get("format") == want)
            if out["ok"]:
                log.success(f"idc={idc} is now {want!r} (was {out['before']!r})")
            else:
                log.error(f"idc={idc} still reads {out['after']!r}, wanted {want!r}")
                _shot(vpage, "format_not_applied")
            return out

        except PWTimeout as e:
            # A timeout says nothing about whether Moodle applied the change. UNKNOWN, never
            # failed — see README, "a remote system's error message is a hypothesis".
            out["ok"] = None
            out["error"] = f"timeout: {describe_exception(e)}"
            log.warning("Timed out. The change MAY have been applied server-side — re-read "
                        "course/edit.php before re-running.")
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            raise
        finally:
            if ctx is not None:
                ctx.close()
            if browser is not None:
                browser.close()
