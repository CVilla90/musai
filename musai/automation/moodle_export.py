"""Moodle gradebook ODS export adapter (LOCAL RUNNER ONLY — read-only).

Replays exactly what the owner does by hand:
  1. Log in to campusvirtual.uach.mx (the UACH course portal).
  2. Click the course tile (an ``a.submit-info`` element that carries data-username/
     data-pass/data-server/data-idc and SSOs into the right virtual* Moodle server).
  3. On the Moodle server, go straight to the deterministic ODS export URL
     (``/grade/export/ods/index.php?id=<idc>``) — far more robust than walking the
     sidebar, and language-proof (we target stable element ids, not ES/EN labels).
  4. Tick "Porcentaje" (``#id_display_percentage``) and download the .ods.

This is a READ. It never writes to Moodle or SEGA, so it touches none of the three
rails. Playwright runs here on the owner's machine/IP only — never in the cloud app.

CLI:
    python -m musai.automation.moodle_export --group 1-LED-A
    python -m musai.automation.moodle_export --group 1-LED-A --idc 7713 --no-import
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PWTimeout

from musai.config import settings
from musai.automation._log import logger as log
from musai.automation._loop import ensure_subprocess_capable_loop

DOWNLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "downloads"
SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"


# ── helpers ───────────────────────────────────────────────────────────────────
def _shot(page: Page, name: str) -> None:
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
        page.screenshot(path=str(path), full_page=True)
        log.warning(f"Saved screenshot → {path}")
    except Exception:
        pass


def _tiles_present(page: Page) -> bool:
    try:
        return page.locator("a.submit-info").count() > 0
    except Exception:
        return False


def _login_campusvirtual(page: Page, base_url: str, user: str, pwd: str) -> None:
    """Log in to the campusvirtual portal. No-op if already authenticated."""
    log.step("Opening campusvirtual portal…")
    page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass

    if _tiles_present(page):
        log.success("Already logged in (course tiles visible).")
        return

    log.step("Entering credentials…")
    filled_user = False
    for loc in (
        page.locator("input[name='username']"),
        page.locator("input[name='user']"),
        page.get_by_placeholder("Usuario"),
        page.locator("input[type='text']"),
    ):
        try:
            if loc.count():
                loc.first.fill(user, timeout=5000)
                filled_user = True
                break
        except Exception:
            continue

    filled_pwd = False
    for loc in (
        page.locator("input[name='password']"),
        page.get_by_placeholder("Contraseña"),
        page.locator("input[type='password']"),
    ):
        try:
            if loc.count():
                loc.first.fill(pwd, timeout=5000)
                filled_pwd = True
                break
        except Exception:
            continue

    if not (filled_user and filled_pwd):
        _shot(page, "login_form_not_found")
        raise RuntimeError(
            "Could not locate the campusvirtual login fields "
            f"(user={filled_user}, pwd={filled_pwd}). Screenshot saved."
        )

    clicked = False
    _login_rx = re.compile(r"entrar|ingresar|iniciar|login|acceder", re.I)
    for loc in (
        page.get_by_role("button", name=_login_rx),
        page.locator("button[type='submit']"),
        page.locator("input[type='submit']"),
    ):
        try:
            if loc.count():
                loc.first.click(timeout=5000)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        page.keyboard.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PWTimeout:
        pass

    if not _tiles_present(page):
        _shot(page, "login_no_tiles")
        raise RuntimeError("Login submitted but no course tiles appeared. Screenshot saved.")
    log.success("Logged in to campusvirtual.")


def _find_tile(page: Page, idc: str, materia: str | None, grupo: str | None):
    """Return (tile_locator, virtual_host) for the wanted course, or (None, None)."""
    # Preferred: match by the Moodle course id — language- and layout-proof.
    by_idc = page.locator(f"a.submit-info[data-idc='{idc}']")
    if by_idc.count():
        server = (by_idc.first.get_attribute("data-server") or "").strip().lower()
        host = f"{server}.uach.mx" if server else None
        return by_idc.first, host

    # Fallback: match by visible text (materia + grupo).
    tiles = page.locator("a.submit-info")
    n = tiles.count()
    wanted = [w.upper() for w in (materia, grupo) if w]
    for i in range(n):
        t = tiles.nth(i)
        try:
            txt = (t.inner_text() or "").upper()
        except Exception:
            continue
        if all(w in txt for w in wanted):
            server = (t.get_attribute("data-server") or "").strip().lower()
            host = f"{server}.uach.mx" if server else None
            return t, host
    return None, None


def _open_course(context: BrowserContext, page: Page, tile, host_hint: str | None) -> Page:
    """Click the tile and return the Moodle page (new tab or same tab)."""
    log.step("Opening the course (SSO into Moodle)…")
    pages_before = set(context.pages)
    try:
        with context.expect_page(timeout=8000) as pinfo:
            tile.click()
        vpage = pinfo.value
        log.info("Course opened in a new tab.")
    except PWTimeout:
        vpage = page  # same-tab navigation
        log.info("Course opened in the same tab.")
    # If a popup we didn't capture appeared, prefer the newest one.
    extra = [p for p in context.pages if p not in pages_before]
    if extra:
        vpage = extra[-1]
    try:
        vpage.wait_for_load_state("domcontentloaded", timeout=60000)
    except PWTimeout:
        pass
    return vpage


def _open_export_form(vpage: Page, host: str, idc: str, attempts: int = 3) -> bool:
    """Navigate to the export form, clearing any interstitial Moodle puts in the way.

    At the start of a semester (and after a course restore) Moodle marks the gradebook
    ``needsupdate`` and serves a ``grades/gradesneedregrading`` notice INSTEAD of the export
    form. Its *Continuar* button is a red herring — it returns you to the dashboard and the
    flag survives. What actually clears it is loading the **grader report**, which runs
    ``grade_regrade_final_grades()`` as a side effect. So: on the interstitial, visit the
    report, then retry the export.

    Returns True once the form is on screen.
    """
    export_url = f"https://{host}/grade/export/ods/index.php?id={idc}"
    report_url = f"https://{host}/grade/report/grader/index.php?id={idc}"

    for attempt in range(1, attempts + 1):
        vpage.goto(export_url, wait_until="domcontentloaded", timeout=60000)
        try:
            vpage.wait_for_load_state("networkidle", timeout=20000)
        except PWTimeout:
            pass

        if vpage.locator("#id_display_percentage").count():
            return True

        body = ""
        try:
            body = (vpage.inner_text("body") or "").lower()
        except Exception:
            pass
        if "regrad" not in body and "recalcul" not in body and attempt > 1:
            return False  # some other failure — don't spin

        log.warning(f"Gradebook needs recalculation (attempt {attempt}/{attempts}) — "
                    f"loading the grader report to force a regrade…")
        try:
            vpage.goto(report_url, wait_until="domcontentloaded", timeout=90000)
            vpage.wait_for_load_state("networkidle", timeout=90000)
        except Exception:
            pass
    return False


def _download_export(vpage: Page, host: str, idc: str, out_path: Path) -> Path:
    """On the Moodle server, drive the ODS export form and save the file."""
    url = f"https://{host}/grade/export/ods/index.php?id={idc}"
    log.step(f"Navigating to gradebook export → {url}")
    if not _open_export_form(vpage, host, idc):
        _shot(vpage, "export_no_percentage")
        raise RuntimeError(
            "Could not reach the ODS export form (#id_display_percentage never appeared, "
            "and no Continuar interstitial to clear). Screenshot saved."
        )

    # Expand "Export format options" if collapsed (so the checkbox is interactable).
    try:
        toggle = vpage.locator("a.fheader[aria-controls='id_options'], a.fheader[aria-expanded='false']")
        if toggle.count() and (toggle.first.get_attribute("aria-expanded") == "false"):
            toggle.first.click(timeout=3000)
            vpage.wait_for_timeout(300)
    except Exception:
        pass

    # Tick "Porcentaje" by stable id (works in ES and EN).
    pct = vpage.locator("#id_display_percentage")
    try:
        pct.first.check(timeout=4000)
    except Exception:
        # Force it on even if the fieldset is still visually collapsed.
        vpage.evaluate("() => { const c = document.querySelector('#id_display_percentage'); if (c) c.checked = true; }")
    log.success("Selected percentage display.")

    submit = vpage.locator("#id_submitbutton")
    if not submit.count():
        submit = vpage.locator("input[type='submit'], button[type='submit']")
    log.step("Downloading the .ods export…")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with vpage.expect_download(timeout=120000) as dl:
        submit.first.click(timeout=10000)
    download = dl.value
    download.save_as(str(out_path))
    log.success(f"Downloaded → {out_path}")
    return out_path


# ── public entry ────────────────────────────────────────────────────────────
def export_gradebook_ods(
    idc: str,
    *,
    materia: str | None = None,
    grupo: str | None = None,
    headless: bool = False,
    download_dir: Path = DOWNLOAD_DIR,
    keep_open: bool = False,
    as_user: str | None = None,
    identity=None,
    on_step=None,
) -> Path:
    """Fetch the Moodle gradebook ODS for one course. Returns the saved file path.

    🔴 `identity` / `as_user` (2026-08-14) — until this was added, this function read
    `settings.uach_*` directly, exactly like `backup_course` did before English IV. That is the
    same defect one surface over: the cockpit's *"Refresh from Moodle"* button would have signed
    in as whatever `.env` holds, so **Colleague D pressing it on her own course would have downloaded
    her gradebook while logged in as the owner** — either a permissions error she cannot read, or a
    successful read of a course list that is not hers. `identity` is an already-resolved account
    (the vault road, used by the web app); `as_user` is the CLI's delegate road through
    `resolve()`. Passing both states two different intentions about whose account this is, so it
    raises rather than picking.

    It stays read-only either way: one login, one page open, one export download. Nothing in the
    course changes, which is why there is no dry-run branch here.
    """
    from musai.automation.credentials import CredentialsMissing, resolve

    if identity is not None and as_user:
        raise RuntimeError("Pass either `identity` or `as_user`, never both — they name "
                           "different accounts and there is no safe way to pick one.")
    if identity is None:
        try:
            identity = resolve(as_user)
        except CredentialsMissing as e:
            raise RuntimeError(str(e)) from e
    user, pwd = identity.username, identity.password
    if not user or not pwd:
        raise RuntimeError(
            "UACH credentials missing. Set UACH_USERNAME / UACH_PASSWORD in MUSAI/.env "
            "(gitignored). See .env.example."
        )

    def step(msg: str) -> None:
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    base_url = settings.moodle_base_url_prod  # campusvirtual.uach.mx
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = download_dir / f"{(grupo or idc)}_{idc}_{ts}.ods"

    log.header(f"Moodle gradebook export · idc={idc} · {grupo or ''} {materia or ''}".strip())
    ensure_subprocess_capable_loop()  # see musai/automation/_loop.py
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            step(f"Signing in to campusvirtual as {identity.username}…")
            _login_campusvirtual(page, base_url, user, pwd)
            step(f"Signed in — opening course {idc}")

            tile, host = _find_tile(page, idc, materia, grupo)
            if tile is None:
                _shot(page, "tile_not_found")
                raise RuntimeError(
                    f"Course tile not found for idc={idc} ({grupo} {materia}). Screenshot saved."
                )
            if not host:
                host = "virtual3.uach.mx"
                log.warning(f"data-server missing on tile; defaulting host to {host}")

            vpage = _open_course(context, page, tile, host)
            step("Course opened — opening the gradebook export form")
            result = _download_export(vpage, host, idc, out_path)
            step(f"Export downloaded → {result.name}")
            if keep_open:
                log.info("keep_open=True — leaving browser open for 20s…")
                vpage.wait_for_timeout(20000)
            return result
        except Exception:
            _shot(page, "export_error")
            raise
        finally:
            context.close()
            browser.close()


def list_courses(headless: bool = True) -> list[dict]:
    """Log in to campusvirtual and return every course tile as {idc, server, text}.
    Read-only — used to discover which groups exist before onboarding them."""
    user, pwd = settings.uach_username, settings.uach_password
    if not user or not pwd:
        raise RuntimeError("UACH credentials missing. Set UACH_USERNAME / UACH_PASSWORD in .env.")
    out: list[dict] = []
    log.header("Listing campusvirtual course tiles")
    ensure_subprocess_capable_loop()  # see musai/automation/_loop.py
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            _login_campusvirtual(page, settings.moodle_base_url_prod, user, pwd)
            tiles = page.locator("a.submit-info")
            n = tiles.count()
            log.step(f"Found {n} course tile(s).")
            for i in range(n):
                t = tiles.nth(i)
                try:
                    out.append({
                        "idc": (t.get_attribute("data-idc") or "").strip(),
                        "server": (t.get_attribute("data-server") or "").strip(),
                        "text": " ".join((t.inner_text() or "").split()),
                    })
                except Exception:
                    continue
            return out
        except Exception:
            _shot(page, "list_error")
            raise
        finally:
            context.close()
            browser.close()


def _ingest(idc: str, group: str, ods_path: Path, semester: str | None = None) -> None:
    """Upsert the downloaded ODS into the DB and write an AuditLog row."""
    from sqlmodel import Session
    from musai.db import init_db, engine
    from musai.grading.ingest import ingest_gradebook
    from musai.semesters import course_for
    from musai.audit import log as audit_log

    init_db()
    with Session(engine, expire_on_commit=False) as sess:
        # Semester-scoped: group codes repeat every semester, so an unscoped lookup would
        # ingest this semester's grades into last semester's course.
        course = course_for(sess, group, semester_name=semester)
        if course is None:
            log.error(f"No course '{group}' in {semester or 'the current semester'} — run "
                      f"`python -m musai.new_semester --discover` first. "
                      f"Skipping ingest. File kept at {ods_path}")
            return
        counts = ingest_gradebook(sess, course, ods_path)
        audit_log(sess, "moodle_export", actor="carlos",
                  target=f"course:{course.id} idc:{idc}", env=course.moodle_env,
                  dry_run=True, detail={"file": str(ods_path), **counts})
        sess.commit()
    log.success(
        f"Ingested: {counts['students_new']} new / {counts['students_renamed']} renamed students, "
        f"{counts['enrollments_new']} new enrollments, "
        f"{counts['activities_new']} new activities, "
        f"{counts['grades_new']} new / {counts['grades_updated']} updated grades."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch a Moodle gradebook ODS export (read-only).")
    ap.add_argument("--group", help="Course group_code, e.g. 1-LED-A")
    ap.add_argument("--list", action="store_true", dest="list_courses",
                    help="List all your course tiles (idc, server, name) and exit")
    ap.add_argument("--idc", help="Moodle course id (overrides the value stored on the course)")
    ap.add_argument("--materia", help="Subject text for tile fallback match, e.g. 'INGLES I'")
    ap.add_argument("--headless", action="store_true", help="Run without a visible browser")
    ap.add_argument("--no-import", action="store_true", help="Download only; do not ingest into the DB")
    ap.add_argument("--keep-open", action="store_true", help="Pause with the browser open after download")
    ap.add_argument("--semester", help="Semester name (default: the active one), e.g. 2026-2")
    args = ap.parse_args()

    if args.list_courses:
        for c in list_courses(headless=args.headless):
            log.info(f"idc={c['idc']:>6}  server={c['server']:<10}  {c['text']}")
        return
    if not args.group:
        ap.error("--group is required (or use --list)")

    idc, materia = args.idc, args.materia
    if not idc or not materia:
        # Resolve missing bits from the DB course record — semester-scoped, or we'd pull
        # LAST semester's Moodle course (its idc is a different, still-live course).
        from sqlmodel import Session
        from musai.db import init_db, engine
        from musai.semesters import course_for
        init_db()
        with Session(engine) as sess:
            course = course_for(sess, args.group, semester_name=args.semester)
        if course:
            idc = idc or course.moodle_course_id
            materia = materia or course.subject
    if not idc:
        log.error(f"No Moodle idc for group {args.group} in "
                  f"{args.semester or 'the current semester'}. Pass --idc, or set "
                  f"moodle_course_id on the course.")
        sys.exit(1)

    try:
        ods = export_gradebook_ods(
            str(idc), materia=materia, grupo=args.group,
            headless=args.headless, keep_open=args.keep_open,
        )
    except Exception as e:
        log.error(f"Export failed: {e}")
        sys.exit(1)

    if not args.no_import:
        _ingest(str(idc), args.group, ods, semester=args.semester)


if __name__ == "__main__":
    main()
