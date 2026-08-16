"""Publish a rendered block into a Moodle course as an activity. LOCAL RUNNER ONLY.

The path is deliberately short — verified against virtual3 on 2026-08-06, which is why this
does NOT reproduce the human click-path (gear → Activar edición → activity chooser → Etiqueta
→ Añadir → toolbar toggle → source-code modal → Actualizar):

    1. GET  /course/view.php?id=<idc>&sesskey=<k>&edit=on     (URL, not the localized gear)
    2. GET  /course/modedit.php?add=label&course=<idc>&section=<n>
    3. JS   tinyMCE.get('id_introeditor').setContent(html)    (no source-code modal)
    4. click #id_submitbutton2                                 ← the only mutating step

Nothing in steps 1-3 depends on Spanish UI text, so an English Moodle works unchanged.

RAIL: ``dry_run=True`` by default (CLAUDE.md). A dry run performs every step INCLUDING
filling the editor, then screenshots and abandons the form without clicking save.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.moodle_export import _find_tile, _login_campusvirtual, _open_course, _shot
from musai.automation.credentials import resolve
from musai.config import settings
from musai.coursebuild.render import MARKER_PREFIX, find_marker

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

# Find an existing MUSAI block by its embedded marker comment and return its cmid, so a
# re-run UPDATES instead of stacking a duplicate. Comment nodes aren't reachable by
# querySelector, hence the TreeWalker.
_FIND_BY_MARKER_JS = """
(prefix) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_COMMENT);
  let node;
  while ((node = walker.nextNode())) {
    if (!node.nodeValue || node.nodeValue.indexOf(prefix) === -1) continue;
    let el = node.parentElement;
    while (el && !(el.id && /^module-\\d+$/.test(el.id))) el = el.parentElement;
    if (el) return {cmid: el.id.replace('module-', ''), marker: node.nodeValue.trim()};
    return {cmid: null, marker: node.nodeValue.trim()};
  }
  return null;
}
"""


def enter_course(ctx, page, idc: str, *, as_user: Optional[str] = None, identity=None):
    """Log in and open the course. Returns (moodle_page, host).

    Shared with `publish_section.py` — the preamble is identical for every write path, and a
    second copy is a second thing to fix when campusvirtual changes.

    **Two different ways to say who this run is, and they must not be confused:**

    * `as_user` — 🔴 log in as **another professor**, via `credentials.resolve`, which reads
      `MOODLE_PWD_<USERNAME>` from `.env` and **refuses rather than falling back to the owner's
      own login**. This is the CLI road: a human at a terminal acting for a colleague who
      consented. Moodle attributes the edit to whoever's account is used; consent is not a
      code path.
    * `identity` — a `MoodleIdentity` the caller already resolved, which in the cockpit means
      `resolve_for_professor()`: the **signed-in professor's own** password out of the vault.

    🔴 Passing a bare `as_user` from the web is a bug that looks like it works. The username
    resolves down the `.env` road, finds no `MOODLE_PWD_MGOMEZ`, and raises
    `CredentialsMissing` for a professor whose password *is* stored — right next to the more
    dangerous version of the same mistake, where `as_user=None` silently signs in as the owner
    and writes to a colleague's course under his name. Hence: the two are mutually exclusive.
    """
    if identity is not None and as_user:
        # They name different accounts. Guessing which one the caller meant is exactly the
        # decision that must never be made silently.
        raise ValueError("Pass either `identity` or `as_user`, not both — they name "
                         "different accounts.")
    identity = identity or resolve(as_user)
    user, pwd = identity.username, identity.password
    if not user or not pwd:
        raise RuntimeError("UACH credentials missing (UACH_USERNAME / UACH_PASSWORD in .env).")
    _login_campusvirtual(page, settings.moodle_base_url_prod, user, pwd)
    tile, host = _find_tile(page, idc, None, None)
    if tile is None:
        raise RuntimeError(f"Course tile not found for idc={idc}.")
    vpage = _open_course(ctx, page, tile, host)
    return vpage, (host or "virtual3.uach.mx")


def editing_on(vpage, host: str, idc: str, section: Optional[int] = None) -> None:
    """Switch editing on by URL — language-proof and idempotent.

    `section` matters on this Moodle: 1-LED-A uses the **onetopic** format, which renders one
    tab at a time, so per-section controls (the *Editar sección* link) only exist in the DOM
    for the section actually being displayed.
    """
    sesskey = vpage.evaluate("() => (window.M && M.cfg && M.cfg.sesskey) || null")
    if not sesskey:
        raise RuntimeError("Could not read M.cfg.sesskey from the course page.")
    url = f"https://{host}/course/view.php?id={idc}&sesskey={sesskey}&edit=on"
    if section is not None:
        url += f"&section={section}"
    vpage.goto(url, wait_until="domcontentloaded", timeout=60000)
    vpage.wait_for_load_state("networkidle", timeout=25000)


def _set_editor(page, html: str) -> str:
    """Put HTML into the activity's rich editor. Returns a short status string."""
    return page.evaluate(
        """(html) => {
            try {
                if (window.tinyMCE && tinyMCE.get('id_introeditor')) {
                    tinyMCE.get('id_introeditor').setContent(html);
                    const ta = document.querySelector('textarea[name="introeditor[text]"]');
                    if (ta) ta.value = html;   // keep the POST body in sync
                    return 'tinymce';
                }
                const ta = document.querySelector('textarea[name*="introeditor"]');
                if (ta) { ta.value = html; return 'textarea'; }
                return 'no-editor';
            } catch (e) { return 'err:' + e.message; }
        }""",
        html,
    )


def publish_block(
    *,
    idc: str,
    html: str,
    section: int = 0,
    modname: str = "label",
    group_label: str = "",
    dry_run: bool = True,
    headless: bool = True,
    replace: bool = True,
    on_step=None,
) -> dict:
    """Create (or update) one activity carrying `html`. Returns a result dict.

    `replace=True` looks for a previously published block with the same marker and edits it
    in place; otherwise a re-run appends a duplicate.
    """
    user, pwd = settings.uach_username, settings.uach_password
    if not user or not pwd:
        raise RuntimeError("UACH credentials missing (UACH_USERNAME / UACH_PASSWORD in .env).")

    marker_id = find_marker(html)
    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "section": section,
                 "marker": marker_id, "mode": "create", "cmid": None,
                 "screenshot": None, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)          # progress must never break the job
            except Exception:
                pass

    log.header(f"Publish {modname} → idc={idc} section={section} "
               f"{group_label} {'[DRY RUN]' if dry_run else '[LIVE]'}")

    # Under `uvicorn --reload` on Windows the loop policy cannot spawn subprocesses, and
    # launching the browser IS a subprocess. See musai/automation/_loop.py.
    if ensure_subprocess_capable_loop():
        log.info("Restored the Proactor event-loop policy so the browser can start.")

    # Launch INSIDE the try: a failure here used to escape publish_block entirely, skipping
    # the result dict, the steps and the screenshot, and surfacing as an unexplained job error.
    browser = ctx = page = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context()
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc)

            # 1 ── editing on, by URL (language-proof, idempotent)
            editing_on(vpage, host, idc)
            step("Editing mode on")

            # 2 ── does this block already exist? (idempotent re-runs)
            existing = None
            if replace and marker_id:
                existing = vpage.evaluate(_FIND_BY_MARKER_JS, f"{MARKER_PREFIX}{marker_id}")
            if existing and existing.get("cmid"):
                out["mode"], out["cmid"] = "update", existing["cmid"]
                edit_url = (f"https://{host}/course/modedit.php"
                            f"?update={existing['cmid']}&return=0&sr=0")
                step(f"Found existing block (cmid={existing['cmid']}) — updating in place")
            else:
                edit_url = (f"https://{host}/course/modedit.php"
                            f"?add={modname}&course={idc}&section={section}&return=0&sr=0")
                step(f"Creating a new {modname} in section {section}")

            vpage.goto(edit_url, wait_until="domcontentloaded", timeout=60000)
            vpage.wait_for_load_state("networkidle", timeout=25000)
            if not vpage.locator("#id_submitbutton2").count():
                _shot(vpage, "publish_no_form")
                raise RuntimeError("Activity edit form did not load (no #id_submitbutton2).")

            # 3 ── fill the editor directly; no source-code modal, no toolbar toggle
            how = _set_editor(vpage, html)
            if how.startswith("err") or how == "no-editor":
                _shot(vpage, "publish_no_editor")
                raise RuntimeError(f"Could not set the editor content ({how}).")
            step(f"Editor filled via {how}")

            shot = SHOT_DIR / (f"publish_{'dryrun' if dry_run else 'live'}_"
                               f"{group_label or idc}_{datetime.now():%Y%m%d_%H%M%S}.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            # 4 ── the only mutating step
            if dry_run:
                step("DRY RUN — form filled and screenshotted, NOT saved")
                out["ok"] = True
                return out

            vpage.locator("#id_submitbutton2").first.click(timeout=15000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=90000)
            except PWTimeout:
                pass
            step("Saved")

            # 5 ── verify by finding our own marker back on the course page
            if marker_id:
                found = vpage.evaluate(_FIND_BY_MARKER_JS, f"{MARKER_PREFIX}{marker_id}")
                if found:
                    out["cmid"] = found.get("cmid") or out["cmid"]
                    step(f"Verified on the course page (cmid={out['cmid']})")
                else:
                    step("WARNING: saved, but the marker was not found on the course page")
            out["ok"] = True
            out["course_url"] = f"https://{host}/course/view.php?id={idc}"
            return out
        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            try:
                if page is not None:
                    _shot(page, "publish_error")
            except Exception:
                pass
            log.error(f"Publish failed: {out['error']}")
            return out
        finally:
            # Both may be None if the launch itself was what failed.
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def publish_for_course(course, html: str, *, section: int = 0, dry_run: bool = True,
                       headless: bool = True, replace: bool = True, on_step=None) -> dict:
    """Publish for a DB Course row, writing an AuditLog either way."""
    from sqlmodel import Session

    from musai.audit import log as audit_log
    from musai.db import engine

    if not course.moodle_course_id:
        raise RuntimeError(f"Course {course.group_code} has no moodle_course_id.")
    result = publish_block(
        idc=str(course.moodle_course_id), html=html, section=section,
        group_label=course.group_code, dry_run=dry_run, headless=headless, replace=replace,
        on_step=on_step,
    )
    with Session(engine) as sess:
        audit_log(sess, "coursebuild_publish", actor="carlos",
                  target=f"course:{course.id} idc:{course.moodle_course_id} section:{section}",
                  env=course.moodle_env, dry_run=dry_run,
                  detail={k: v for k, v in result.items() if k != "steps"})
        sess.commit()
    return result
