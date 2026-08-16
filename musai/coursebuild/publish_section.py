"""Publish HTML into a course SECTION SUMMARY. LOCAL RUNNER ONLY.

Why this exists alongside `publish.py` (found live, 2026-08-07): a Moodle course's **home page
is the section summary**, not an activity. The owner's own hand-written course hub lives in the
summary of section 0, so publishing MUSAI's hub as a *label* stacked a second hub underneath
his instead of replacing it. Labels are right for content *inside* a course; the summary is
right for the page that IS the course's front door.

The click-path, verified against virtual3 on 2026-08-07:

    1. GET  /course/view.php?id=<idc>&sesskey=<k>&edit=on&section=<n>
    2. read the `Editar sección` link inside `#section-<n>`  →  editsection.php?id=<SECTION ID>
    3. GET  that URL
    4. JS   tinyMCE.get('id_summary_editor').setContent(html)
    5. click #id_submitbutton                                  ← the only mutating step

🔴 **The section id is not the section number and not the id in the summary's image URLs.**
For 1-LED-A section 0 the file-area id is 107684 and the real `course_section.id` is 127099.
Both are plausible; only one works. It is always read from the link (step 2), never derived.

🔴 **Three submit buttons on that form** — an unnamed *Enviar* (the course search box) comes
FIRST in the DOM, then *Guardar cambios*, then *Cancelar*. `input[type=submit]").first` submits
the search box. Matched by `#id_submitbutton` — the same lesson the delete-confirm page and the
restore page each taught independently.

RAIL: ``dry_run=True`` by default. A dry run fills the editor, screenshots it, and navigates
away without saving.

RAIL: a summary that already holds content MUSAI did not write is the professor's own work.
Overwriting it needs ``overwrite_foreign=True`` — see `_is_foreign`.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _shot
from musai.coursebuild.publish import SHOT_DIR, editing_on, enter_course
from musai.coursebuild.render import MARKER_PREFIX, find_marker

# Where a replaced summary is kept. The AuditLog carries it too, but a file is what a panicking
# professor can actually paste back into Moodle at 11pm.
BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "backups"

# Read the CURRENT summary out of the form before touching it. TinyMCE first (it owns the live
# content once it has initialised); the raw textarea is the fallback for a page where it hasn't.
_READ_SUMMARY_JS = """
() => {
    try {
        if (window.tinyMCE && tinyMCE.get('id_summary_editor')) {
            return tinyMCE.get('id_summary_editor').getContent();
        }
    } catch (e) { /* fall through to the textarea */ }
    const ta = document.querySelector('textarea[name="summary_editor[text]"]')
            || document.querySelector('textarea[name*="summary"]');
    return ta ? ta.value : null;
}
"""

_SET_SUMMARY_JS = """
(html) => {
    try {
        if (window.tinyMCE && tinyMCE.get('id_summary_editor')) {
            tinyMCE.get('id_summary_editor').setContent(html);
            const ta = document.querySelector('textarea[name="summary_editor[text]"]');
            if (ta) ta.value = html;      // keep the POST body in sync
            return 'tinymce';
        }
        const ta = document.querySelector('textarea[name="summary_editor[text]"]');
        if (ta) { ta.value = html; return 'textarea'; }
        return 'no-editor';
    } catch (e) { return 'err:' + e.message; }
}
"""

# The per-section edit link, scoped to the section we mean. Scoped because onetopic renders the
# open tab only — an unscoped match would silently pick whichever section happens to be shown.
_EDIT_LINK_SEL = '#section-{n} a[href*="editsection.php"]'


def _is_foreign(previous_html: Optional[str]) -> bool:
    """Is the summary we are about to replace somebody else's work?

    Empty (or whitespace) is free to take. Anything carrying a MUSAI marker is ours to update.
    Anything else is the professor's hand-written page and must not be destroyed by default.
    """
    text = (previous_html or "").strip()
    if not text:
        return False
    return MARKER_PREFIX not in text


def _backup(idc: str, section: int, previous_html: str) -> Optional[str]:
    """Write the outgoing summary to disk. Never let a backup failure stop a publish — but
    never claim a backup that did not happen, either: the path is None if it failed."""
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path = BACKUP_DIR / (f"section_summary_{idc}_{section}_"
                             f"{datetime.now():%Y%m%d_%H%M%S}.html")
        path.write_text(previous_html, encoding="utf-8")
        return str(path)
    except Exception as e:
        log.warning(f"Could not back up the previous summary: {describe_exception(e)}")
        return None


def publish_section_summary(
    *,
    idc: str,
    html: str,
    section: int = 0,
    group_label: str = "",
    dry_run: bool = True,
    headless: bool = True,
    overwrite_foreign: bool = False,
    as_user: Optional[str] = None,
    on_step=None,
) -> dict:
    """Replace one section's summary with `html`. Returns a result dict.

    A dry run always proceeds to the preview, even when it would overwrite foreign content —
    seeing what *would* happen is the whole point of a dry run. Only the save refuses.
    """
    marker_id = find_marker(html)
    out: dict = {
        "ok": False, "dry_run": dry_run, "idc": idc, "section": section,
        "marker": marker_id, "section_id": None, "cmid": None, "mode": "section_summary",
        "previous_html": None, "previous_backup": None, "would_overwrite_foreign": False,
        "screenshot": None, "steps": [],
    }

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"Publish section summary → idc={idc} section={section} "
               f"{group_label} {'[DRY RUN]' if dry_run else '[LIVE]'}")

    if ensure_subprocess_capable_loop():
        log.info("Restored the Proactor event-loop policy so the browser can start.")

    browser = ctx = page = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context()
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc, as_user=as_user)

            # 1 ── editing on, with the target section displayed
            editing_on(vpage, host, idc, section=section)
            step(f"Editing mode on (section {section} displayed)")

            # 2 ── the section id comes from the link, never from a guess
            link = vpage.locator(_EDIT_LINK_SEL.format(n=section)).first
            if not link.count():
                _shot(vpage, "publish_section_no_edit_link")
                raise RuntimeError(
                    f"No 'Editar sección' link inside #section-{section}. Either editing mode "
                    "did not engage or this section is not the one being displayed."
                )
            href = link.get_attribute("href") or ""
            match = re.search(r"editsection\.php\?id=(\d+)", href)
            if not match:
                raise RuntimeError(f"Could not read a section id out of {href!r}.")
            out["section_id"] = match.group(1)
            edit_url = href if href.startswith("http") else f"https://{host}{href}"
            step(f"Section id {out['section_id']} resolved from the page")

            vpage.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            vpage.wait_for_load_state("networkidle", timeout=25000)
            if not vpage.locator("#id_submitbutton").count():
                _shot(vpage, "publish_section_no_form")
                raise RuntimeError("Section edit form did not load (no #id_submitbutton).")

            # 3 ── read what is there BEFORE replacing it
            previous = vpage.evaluate(_READ_SUMMARY_JS)
            out["previous_html"] = previous
            out["would_overwrite_foreign"] = _is_foreign(previous)
            if previous:
                out["previous_backup"] = _backup(idc, section, previous)
                step(f"Read the current summary ({len(previous):,} chars)"
                     + (f" → backed up to {Path(out['previous_backup']).name}"
                        if out["previous_backup"] else " — BACKUP FAILED"))
            else:
                step("The current summary is empty")

            if out["would_overwrite_foreign"]:
                step("⚠ This summary was NOT written by MUSAI — it is the professor's own page")

            # 4 ── fill the editor
            how = vpage.evaluate(_SET_SUMMARY_JS, html)
            if how.startswith("err") or how == "no-editor":
                _shot(vpage, "publish_section_no_editor")
                raise RuntimeError(f"Could not set the summary content ({how}).")
            step(f"Summary filled via {how}")

            shot = SHOT_DIR / (f"section_{'dryrun' if dry_run else 'live'}_"
                               f"{group_label or idc}_s{section}_"
                               f"{datetime.now():%Y%m%d_%H%M%S}.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — summary filled and screenshotted, NOT saved")
                out["ok"] = True
                return out

            # 5 ── refuse to destroy the professor's own page without being told to
            if out["would_overwrite_foreign"] and not overwrite_foreign:
                out["error"] = (
                    "Refused: this section summary holds content MUSAI did not write. "
                    "Re-run with overwrite_foreign=True to replace it "
                    f"(a copy is at {out['previous_backup'] or 'nowhere — the backup failed'})."
                )
                step("REFUSED — would destroy the professor's own page")
                return out

            # 6 ── the only mutating step. By id: the first submit on this page is the
            #      course SEARCH box, and clicking that looks exactly like a failed save.
            vpage.locator("#id_submitbutton").first.click(timeout=15000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=90000)
            except PWTimeout:
                pass
            step("Saved")

            # 7 ── verify our own marker came back on the course page
            if marker_id:
                vpage.goto(f"https://{host}/course/view.php?id={idc}&section={section}",
                           wait_until="domcontentloaded", timeout=60000)
                vpage.wait_for_load_state("networkidle", timeout=25000)
                found = f"{MARKER_PREFIX}{marker_id}" in vpage.content()
                step("Verified on the course page" if found
                     else "WARNING: saved, but the marker was not found on the course page")
                out["verified"] = found
            out["ok"] = True
            out["course_url"] = f"https://{host}/course/view.php?id={idc}&section={section}"
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            try:
                if page is not None:
                    _shot(page, "publish_section_error")
            except Exception:
                pass
            log.error(f"Section publish failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def publish_summary_for_course(course, html: str, *, section: int = 0, dry_run: bool = True,
                               headless: bool = True, overwrite_foreign: bool = False,
                               on_step=None) -> dict:
    """Publish for a DB Course row, writing an AuditLog either way.

    The audit detail deliberately keeps the FULL previous summary: per ROADMAP, an audit row
    that carries the prior content is a recovery path, not just a record that something changed.
    """
    from sqlmodel import Session

    from musai.audit import log as audit_log
    from musai.db import engine

    if not course.moodle_course_id:
        raise RuntimeError(f"Course {course.group_code} has no moodle_course_id.")
    result = publish_section_summary(
        idc=str(course.moodle_course_id), html=html, section=section,
        group_label=course.group_code, dry_run=dry_run, headless=headless,
        overwrite_foreign=overwrite_foreign, on_step=on_step,
    )
    with Session(engine) as sess:
        audit_log(sess, "coursebuild_publish_section", actor="carlos",
                  target=(f"course:{course.id} idc:{course.moodle_course_id} "
                          f"section:{section} section_id:{result.get('section_id')}"),
                  env=course.moodle_env, dry_run=dry_run,
                  detail={k: v for k, v in result.items() if k != "steps"})
        sess.commit()
    return result
