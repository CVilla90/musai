"""Restore a Moodle course from a `.mbz` backup. LOCAL RUNNER ONLY.

Ported from `moodle_suite/automation/upload_course_backup.py`, which drove this exact wizard
successfully across 17 courses — its `restore_review_*.png` screenshots are the evidence, which
is why the *selectors* are kept as-proven rather than re-derived. What changed is everything
around them:

* **Credentials come from `.env`.** The original hardcoded FOUR professors' plaintext passwords
  (`professor`, `colleague2`, `colleague3`, `colleague1` — three of them not the owner's).
* **One course per call, chosen by `idc`** — no hardcoded professor/course tables.
* **The professor's file path is handed straight to Moodle** via Playwright `set_input_files`.
  MUSAI never stores, uploads or even receives the backup bytes (they are 21-75 MB each).
* **Dry-run by default** (rail 2): every step runs, then it stops on the review page and
  screenshots what *would* be restored.
* **The critical setting fails CLOSED** — see below.

🔴 **A RESTORE WIPES THE GRADEBOOK.** The wizard deliberately selects *"Eliminar los contenidos
de este curso y después restaurar"*. The order is therefore **restore → re-fetch → map
activities**, never map first. `restore_for_course()` refuses outright if MUSAI's DB already
holds grades for the course, unless the caller passes `force=True`.

🔴 **`keep_roles_and_enrolments = Sí` is what saves the already-enrolled students.** The original
logged `"CRITICAL: Could not find …"` and then **carried on with the restore** — so a renamed or
missing selector would have silently wiped every enrolment (83 of them across 2026-2 today).
Here, failing to *set and verify* that field aborts before anything mutates.

⚠️ Expect `gradesneedregrading` on the next gradebook export after every restore. That is
already handled in `moodle_export._open_export_form()`.
"""

import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.backup import inspect_mbz
from musai.automation.credentials import CredentialsMissing, resolve as resolve_identity
from musai.automation.moodle_export import _find_tile, _login_campusvirtual, _open_course, _shot
from musai.config import settings

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

# How long to let Moodle chew on the actual restore. A 50 MB backup took minutes; the browser
# must stay open for ALL of it, because closing it mid-restore leaves the course wiped and
# unpopulated (observed 2026-08-07).
RESTORE_TIMEOUT_MS = 900_000  # 15 minutes

#: How long a post-restore count of ZERO is treated as *"Moodle has not finished writing"*
#: rather than as a failure. Leaving the review page means the request completed, not that the
#: course exists yet — measured on 9046, 2026-08-11, where an immediate count said 0 about a
#: restore that had placed 79 activities. A full re-count is ~24 page loads, so the poll is
#: deliberately slow; the cost of being wrong in the other direction is a deleted course.
SETTLE_TIMEOUT_S = 300
SETTLE_POLL_MS = 20_000

# Restore wizard defaults. `keep_roles_enrolments` is the one that matters; the rest mirror the
# choices the original made and the owner verified.
DEFAULT_SETTINGS = {
    "overwrite_course_config": False,   # Sobrescribir configuración del curso: No
    "keep_roles_enrolments": True,      # Mantener roles e inscripciones: Sí  ← CRITICAL
    "keep_groups": False,               # Mantener grupos y agrupamientos: No
    "include_enrolment_methods": False,  # Incluir métodos de inscripción: No
}

# Each forward action lists proven selector first, language-proof fallbacks after. The wizard is
# a real multi-step form (unlike publish, which had a URL shortcut), so some Spanish text is
# unavoidable — but `input.proceedbutton` is the class Moodle puts on "the forward button" and
# works on any language, so it leads wherever it applies.
_FORWARD = {
    "continue": ['input[type="submit"][value="Continuar"]',
                 'input.proceedbutton[type="submit"]',
                 '#id_submitbutton'],
    "next": ['input.proceedbutton[type="submit"][value="Siguiente"]',
             'input.proceedbutton[type="submit"]',
             '#id_submitbutton'],
    "perform": ['input.proceedbutton[name="submitbutton"][value*="Realizar restauración"]',
                'input.proceedbutton[name="submitbutton"]',
                'input.proceedbutton[type="submit"]'],
}


class RestoreAborted(RuntimeError):
    """Raised when a safety precondition fails. Nothing has been mutated when this is raised."""


# ── Targeting: the rail that matters when the course is not yours ─────────────
# 🔴 PRODUCT_DIRECTION's stated risk for coordinator mode, in one sentence: *a colleague teaches
# other subjects in other schools, and a restore wipes the destination first.* An `idc` typo is
# the whole failure — it opens a perfectly valid course and every later check passes.
#
# ⚠️ Longest-first alternation is not cosmetic: `INGLES I` is a prefix of `INGLES II` and
# `INGLES III`. `(I|II|III|IV)` would match "I" inside "INGLES III" and cheerfully report that an
# INGLES I backup belongs in an INGLES III course — which is precisely the wipe this prevents.
_SUBJECT = re.compile(r"\bINGL[EÉ]S\s+(IV|III|II|I)\b", re.I)


def subject_of(course_name: str | None) -> str | None:
    """The subject a course name states, e.g. `"INGLES II"`. `None` = could not tell."""
    if not course_name:
        return None
    m = _SUBJECT.search(course_name)
    return f"INGLES {m.group(1).upper()}" if m else None


def _read_course_name(page, host: str, idc: str) -> str:
    """The target's name as Moodle renders it right now. Read, never assumed from a DB row."""
    page.goto(f"https://{host}/course/view.php?id={idc}",
              wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    name = page.evaluate("""() => {
        const h = document.querySelector('.page-header-headings h1, #page-header h1, h1');
        const t = (h && h.innerText) || document.title || '';
        return t.replace(/\\s+/g, ' ').trim();
    }""")
    # His own courses render "Curso: <name>"; a course he is not in renders the bare name.
    return re.sub(r"^(Curso|Course)\s*:\s*", "", name or "").strip()


def verify_target(*, target_name: str, expect_course_name: str | None,
                  backup_name: str | None, strict: bool) -> None:
    """Refuse a restore whose destination does not look like the one that was asked for.

    Pure — no browser, no I/O — so it is testable and so it can refuse before anything mutates.

    `strict` (set whenever the run acts as another professor) turns *"could not tell"* into a
    refusal. An unknown subject is not a matching subject, the same way `remove.py` treats an
    uncountable submission list as "has submissions".
    """
    if expect_course_name:
        wanted = " ".join(expect_course_name.split()).casefold()
        if wanted not in " ".join(target_name.split()).casefold():
            raise RestoreAborted(
                f"🔴 Target check failed. Expected a course named like {expect_course_name!r}; "
                f"Moodle says this course is {target_name!r}. A restore WIPES the destination — "
                f"refusing. Check the idc.")
    elif strict:
        raise RestoreAborted(
            "🔴 Acting as another professor requires `expect_course_name`. Their account can "
            "reach every course they teach, in every school, and a wrong idc opens a perfectly "
            "valid course that every later check would pass.")

    want, got = subject_of(backup_name), subject_of(target_name)
    if want and got and want != got:
        raise RestoreAborted(
            f"🔴 Subject mismatch: the backup is {want} ({backup_name!r}) and the target is "
            f"{got} ({target_name!r}). Refusing — this is the mistake that costs a colleague "
            f"a course.")
    if strict and not (want and got):
        raise RestoreAborted(
            f"🔴 Could not read the subject from "
            f"{'the backup name' if not want else 'the target name'} "
            f"(backup={backup_name!r}, target={target_name!r}). Acting for another professor, an "
            f"unverifiable target is a refused target.")


def _click_forward(page, kind: str, *, timeout: int = 15000,
                   no_wait_after: bool = False) -> str:
    """Click the wizard's forward button, trying proven selectors before generic ones.

    🔴 `no_wait_after=True` is REQUIRED for the final "Realizar restauración". A normal click
    blocks until the navigation settles, and the restore itself takes minutes — so the click
    times out, the exception unwinds, and `finally` closes the browser **while the restore is
    running**. That is not theoretical: it happened on the first live run and left the course
    emptied but not repopulated.
    """
    for sel in _FORWARD[kind]:
        loc = page.locator(sel).first
        if loc.count():
            if no_wait_after:
                loc.click(timeout=timeout, no_wait_after=True)
                return sel
            loc.click(timeout=timeout)
            try:
                page.wait_for_load_state("networkidle", timeout=60000)
            except PWTimeout:
                pass
            return sel
    raise RestoreAborted(f"No '{kind}' button found on the restore wizard page.")


def _set_and_verify(page, name: str, value: str, label: str) -> None:
    """Set a wizard <select> and read it back. A setting we cannot confirm is a failure.

    This is the difference between the original and this port: it logged and continued.
    """
    loc = page.locator(f'select[name="{name}"]').first
    if not loc.count():
        raise RestoreAborted(
            f"Restore setting '{label}' ({name}) not found on the page. Refusing to continue — "
            f"proceeding would restore with Moodle's defaults."
        )
    loc.select_option(value)
    got = loc.input_value()
    if got != value:
        raise RestoreAborted(
            f"Restore setting '{label}' ({name}) would not take: wanted {value!r}, got {got!r}."
        )
    log.success(f"✓ {label} = {value}")


def restore_course(
    *,
    idc: str,
    backup_path: str | Path,
    dry_run: bool = True,
    headless: bool = True,
    group_label: str = "",
    restore_settings: dict | None = None,
    as_user: str | None = None,
    identity=None,
    expect_course_name: str | None = None,
    on_step=None,
) -> dict:
    """Restore one `.mbz` into the Moodle course `idc`. Returns a result dict.

    `dry_run=True` (default) walks the entire wizard, configures every setting, screenshots the
    review page — and stops without clicking *Realizar restauración*.

    `as_user` logs in as **another professor** (password from `MOODLE_PWD_<USERNAME>`; see
    `musai/automation/credentials.py`). When it is set, `expect_course_name` becomes **required**
    and the subject check becomes strict — their account can reach every course they teach, so a
    mistyped idc opens a valid course and wipes it.
    """
    conf = {**DEFAULT_SETTINGS, **(restore_settings or {})}
    # `identity` (2026-08-14) is an already-resolved account, used by the web cockpit so a
    # signed-in professor restores as THEMSELVES. It is not an override for `as_user`: those
    # name different accounts, and a restore into the wrong one wipes the wrong course.
    if identity is not None and as_user:
        raise RestoreAborted("Pass either `identity` or `as_user`, never both — they name "
                             "different accounts and a restore deletes the destination.")
    if identity is None:
        try:
            identity = resolve_identity(as_user)
        except CredentialsMissing as e:
            raise RestoreAborted(str(e)) from e
    user, pwd = identity.username, identity.password
    strict = not identity.is_self

    path = Path(backup_path).expanduser().resolve()
    if not path.is_file():
        raise RestoreAborted(f"Backup file not found: {path}")
    if path.suffix.lower() != ".mbz":
        raise RestoreAborted(f"Not a Moodle backup (.mbz): {path.name}")

    # Read the archive before a browser exists — its name is what the subject check compares
    # against, and "unreadable" should cost two seconds, not fifteen minutes.
    manifest = inspect_mbz(path)
    if not manifest["ok"]:
        raise RestoreAborted(f"Not a readable Moodle backup: {manifest.get('error')}")
    if strict and manifest.get("includes_users") is not False:
        raise RestoreAborted(
            f"🔴 {path.name} either carries user data or does not say that it doesn't "
            f"(users={manifest.get('includes_users')!r}). Restoring it into another professor's "
            f"course would move students. Refusing.")

    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "backup": path.name,
                 "backup_bytes": path.stat().st_size, "settings": conf,
                 "screenshot": None, "restored": False, "steps": [],
                 "as_user": identity.username, "acting_for_another": strict,
                 "manifest": {k: manifest[k] for k in
                              ("course_id", "fullname", "activities", "includes_users")},
                 "target_name": None}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"Restore .mbz → idc={idc} {group_label} "
               f"{'[DRY RUN]' if dry_run else '[LIVE — WIPES THE GRADEBOOK]'}")
    log.info(f"Backup: {path.name} ({path.stat().st_size / 1_048_576:.1f} MB) — "
             f"streamed straight from disk to Moodle, never stored by MUSAI.")
    log.info(f"Signed in as: {identity.describe()}")
    if strict:
        log.warning("🔴 Moodle will record THIS PROFESSOR as the author of the restore, not "
                    "the owner. MUSAI records `on_behalf_of` because Moodle cannot.")

    ensure_subprocess_capable_loop()  # see musai/automation/_loop.py

    browser = ctx = page = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(accept_downloads=False)
            page = ctx.new_page()

            _login_campusvirtual(page, settings.moodle_base_url_prod, user, pwd)
            tile, host = _find_tile(page, idc, None, None)
            if tile is None:
                raise RestoreAborted(f"Course tile not found for idc={idc}.")
            vpage = _open_course(ctx, page, tile, host)
            host = host or "virtual3.uach.mx"
            step("Course opened")

            # ── Targeting — before the wizard, because the wizard is where wiping starts ─
            target_name = _read_course_name(vpage, host, idc)
            out["target_name"] = target_name
            step(f"Target reads as: {target_name!r}")
            verify_target(target_name=target_name, expect_course_name=expect_course_name,
                          backup_name=manifest.get("fullname"), strict=strict)

            # 🔴 Say out loud what is about to be destroyed. `keep_roles_enrolments` saves the
            # students; it saves nothing they submitted. At semester start this is 0 and costs
            # nothing to print; a week in, it is the number that should stop the run.
            existing, _ = _count_activities(vpage, host, idc)
            out["target_activities_before"] = existing
            step(f"Target currently holds {existing} activities — the restore DELETES them")

            # ── Restore landing page ────────────────────────────────────────────────────
            # 🔴 `restorefile.php` takes a CONTEXT id, NOT the course id. Passing courseid
            # silently lands you back on the course page with no file picker. `M.cfg.contextid`
            # is published on every course page (verified: idc 9023 → contextid 1235228); the
            # <a href> is the fallback, matched on the URL rather than the Spanish "Restaurar".
            vpage.goto(f"https://{host}/course/view.php?id={idc}",
                       wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass

            restore_url = vpage.evaluate("""() => {
                const cid = window.M && M.cfg && M.cfg.contextid;
                if (cid) return '/backup/restorefile.php?contextid=' + cid;
                const a = document.querySelector('a[href*="restorefile.php"]');
                return a ? a.getAttribute('href') : null;
            }""")
            if not restore_url:
                _shot(vpage, "restore_no_entry_point")
                raise RestoreAborted(
                    "Could not find the restore entry point (no M.cfg.contextid and no "
                    "restorefile.php link). Do you have the restore capability on this course?")
            if restore_url.startswith("/"):
                restore_url = f"https://{host}{restore_url}"

            vpage.goto(restore_url, wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass
            # The <input type=file> does not exist yet — it appears only inside the picker
            # modal, after choosing "Subir un archivo". The choose button is the real signal.
            if not vpage.locator("input.fp-btn-choose").count():
                _shot(vpage, "restore_no_filepicker")
                raise RestoreAborted(f"Restore page loaded but has no file picker: {restore_url}")
            step("Restore page open")

            _upload_backup(vpage, path, step)
            _run_wizard(vpage, conf, step)

            # ── Review — the last read-only moment ──────────────────────────────────────
            shot = SHOT_DIR / (f"restore_{'dryrun' if dry_run else 'live'}_"
                               f"{group_label or idc}_{datetime.now():%Y%m%d_%H%M%S}.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)
            step("Review page reached — screenshot saved")

            if dry_run:
                step("DRY RUN — wizard configured and screenshotted, restore NOT performed")
                out["ok"] = True
                return out

            step("Performing the restore — this takes minutes; do NOT close the browser…")
            perform = None
            for sel in _FORWARD["perform"]:
                loc = vpage.locator(sel).first
                if loc.count():
                    perform = loc
                    break
            if perform is None:
                _shot(vpage, "restore_no_perform_button")
                raise RestoreAborted("No 'Realizar restauración' button on the review page.")

            review_url = vpage.url
            out["restored"] = True  # the POST is away; the course is being rewritten NOW
            perform.click(timeout=30000, no_wait_after=True)

            # 🔴 `no_wait_after` returns BEFORE the POST is even issued, so waiting on
            # `networkidle` here can settle on the review page and report a restore that never
            # ran. Poll the URL until Moodle actually moves us off it.
            deadline = time.monotonic() + RESTORE_TIMEOUT_MS / 1000
            last_note = 0.0
            while time.monotonic() < deadline:
                if vpage.url != review_url:
                    break
                elapsed = time.monotonic() - (deadline - RESTORE_TIMEOUT_MS / 1000)
                if elapsed - last_note >= 30:
                    last_note = elapsed
                    step(f"…still restoring ({int(elapsed)}s)")
                vpage.wait_for_timeout(2000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=RESTORE_TIMEOUT_MS)
            except PWTimeout:
                pass
            out["after_url"] = vpage.url
            step(f"Moodle moved on → {vpage.url[:80]}")
            _shot(vpage, "restore_after_perform")  # always: this is what Moodle actually said

            # Verify by COUNTING what landed, not by trusting a banner.
            #
            # 🔴 **This in-session count is UNRELIABLE, and a zero from it means nothing.**
            # Measured across three restores on 2026-08-11: 9046 counted 0, 9045 counted 0
            # through a full five-minute settle window — and both held all **79 activities**,
            # confirmed minutes later by logging in again. 9044 counted 79 immediately. Same
            # code, same archive, three different answers.
            #
            # The settle loop below was written for the first case on the theory that Moodle
            # had not finished writing. **9045 disproved that**: waiting longer changed nothing
            # while a fresh login saw a complete course. So it is not a timing problem, it is a
            # **session** one — after the wizard, this browser context's `course/view.php` no
            # longer renders the course's modules. The loop is kept because it costs nothing and
            # does resolve the slow case, but it is not the fix, and the wording below no longer
            # pretends it is.
            #
            # 🔴 **A false FAILED is more dangerous than a false OK here**, because the obvious
            # response is to run the restore again — and a restore DELETES the target's contents
            # first. The authoritative check is `scratchpad/verify_english_ii.py`, which logs in
            # from scratch as the owning professor and counts every section.
            mods, per_section = _count_activities(vpage, host, idc)
            settle_deadline = time.monotonic() + SETTLE_TIMEOUT_S
            waited = 0.0
            while mods == 0 and time.monotonic() < settle_deadline:
                step(f"Count is still 0 after {int(waited)}s — Moodle may not have finished "
                     "writing. Re-counting rather than reporting a failure.")
                vpage.wait_for_timeout(SETTLE_POLL_MS)
                waited += SETTLE_POLL_MS / 1000
                mods, per_section = _count_activities(vpage, host, idc)
            out["modules"] = mods
            out["sections"] = per_section
            out["settle_seconds"] = round(waited, 1)
            out["ok"] = mods > 0
            out["course_url"] = f"https://{host}/course/view.php?id={idc}"
            if mods:
                step(f"Course now holds {mods} activities"
                     + (f" (appeared after {int(waited)}s of settling)" if waited else ""))
            else:
                step(f"⚠ This session counts ZERO activities after {int(waited)}s — which on "
                     "2026-08-11 was WRONG twice out of three restores, both times about a "
                     "course that held all 79. 🔴 Do NOT re-run on this. COUNT IT YOURSELF "
                     "from a fresh login (scratchpad/verify_english_ii.py): a restore deletes "
                     "the target first, so re-running on a false alarm is what destroys a "
                     "course that actually succeeded.")
            return out

        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            try:
                if page is not None:
                    _shot(page, "restore_error")
            except Exception:
                pass
            log.error(f"Restore failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def _count_activities(page, host: str, idc: str, *, max_sections: int = 24) -> tuple[int, dict]:
    """Count the course's activities across ALL sections. Returns (total, {section: n}).

    🔴 Counting `[id^=module-]` on `course/view.php` alone reports **0** for this course, and
    that is not a failure — the restored format shows one section at a time, and section 0
    ("Introduction") is empty. A successful 80-activity restore was reported as "ZERO
    activities" because of exactly that. Ask each section, not the landing page.

    ⚠️ Sections are not contiguous: in 1-LED-A section 12 is empty while 13 holds 9 activities.
    An early "stop after N empty sections" optimisation is therefore a way to undercount — and
    since this count is what decides success, undercounting reports a good restore as a failed
    one. Every section is scanned; ~24 page loads is nothing next to a 15-minute restore.
    """
    total, per_section = 0, {}
    for n in range(max_sections):
        page.goto(f"https://{host}/course/view.php?id={idc}&section={n}",
                  wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        mods = page.evaluate("() => document.querySelectorAll('[id^=module-]').length")
        if mods:
            total += mods
            per_section[n] = mods
    return total, per_section


def _upload_backup(page, path: Path, step) -> None:
    """Moodle's multi-step file picker: choose → 'Subir un archivo' → input → upload → Restaurar.

    🔴 The upload must be WAITED FOR, not slept through. The first cut used a fixed 3-second
    pause, which is nowhere near enough for a 50 MB `.mbz`, so *Restaurar* got clicked while the
    POST was still in flight. Moodle reports that as **"Su sesión ha excedido el tiempo
    límite"** — a login page. Nothing is wrong with the session; the symptom just points
    somewhere else entirely, which is worth remembering before debugging auth for an hour.
    """
    choose = page.locator("input.fp-btn-choose").first
    if choose.count():
        choose.click()
        page.wait_for_timeout(1500)
        step("File picker opened")

    upload_tab = page.locator(
        '.fp-repo-name:has-text("Subir un archivo"), span:has-text("Subir un archivo")').first
    if upload_tab.count():
        upload_tab.click()
        page.wait_for_timeout(1500)

    file_input = page.locator('input[name="repo_upload_file"], input[type="file"]').first
    if not file_input.count():
        _shot(page, "restore_no_file_input")
        raise RestoreAborted("No file input in the Moodle file picker.")

    # The bytes go from the professor's disk straight to Moodle. MUSAI never holds them.
    file_input.set_input_files(str(path))
    page.wait_for_timeout(1000)
    mb = path.stat().st_size / 1_048_576
    step(f"Uploading {path.name} ({mb:.1f} MB)…")

    submit = page.locator('button.fp-upload-btn, input[value*="Subir"]').first
    if submit.count():
        submit.click()

    # Wait for the file to actually LAND in the file manager. Budget scales with size
    # (~6s per MB, floor 2 min) — a big backup over a slow campus link is not an error.
    timeout_ms = max(120_000, int(mb * 6_000))
    try:
        page.wait_for_function(
            """(name) => [...document.querySelectorAll(
                   '.fp-file, .fp-filename, .filepicker-filename')]
                   .some(e => (e.textContent || '').includes(name))""",
            arg=path.name, timeout=timeout_ms)
    except PWTimeout:
        _shot(page, "restore_upload_never_landed")
        raise RestoreAborted(
            f"{path.name} never appeared in Moodle's file manager after "
            f"{timeout_ms // 1000}s. Nothing was restored.")
    step(f"Upload landed ({mb:.1f} MB)")

    # `input[name=submitbutton]` is the form's own Restaurar. The page ALSO carries 'Enviar'
    # (course search) and per-row 'Restaurar' actions for existing backups, so never match on
    # the word alone.
    restaurar = page.locator('input[name="submitbutton"]').first
    if not restaurar.count():
        _shot(page, "restore_no_restaurar_button")
        raise RestoreAborted("Upload finished but the 'Restaurar' submit was not found.")
    restaurar.click()
    try:
        page.wait_for_load_state("networkidle", timeout=180000)
    except PWTimeout:
        pass
    if page.locator('input[name="username"]').count():
        _shot(page, "restore_session_lost")
        raise RestoreAborted(
            "Moodle returned the login page after submitting the backup — usually the form was "
            "posted before the upload finished. Nothing was restored.")
    step("Backup accepted — entering the wizard")


def _run_wizard(page, conf: dict, step) -> None:
    """Validation → destination → schema → course settings. Stops before the review screenshot."""
    # 1 ── validation
    _click_forward(page, "continue")
    step("1/4 Backup validated")

    # 2 ── destination: delete this course's contents, then restore
    target = page.locator('input[type="radio"][name="target"][value="0"]').first
    if not target.count():
        _shot(page, "restore_no_target")
        raise RestoreAborted("Destination radio (target=0) not found — refusing to guess.")
    target.check()
    if not target.is_checked():
        raise RestoreAborted("Destination radio would not stay checked.")
    _click_forward(page, "continue")
    step("2/4 Destination = replace this course's contents")

    # 3 ── schema: enrolment methods
    enrol = page.locator("select#id_setting_root_enrolments").first
    if enrol.count():
        enrol.select_option("1" if conf["include_enrolment_methods"] else "0")
    _click_forward(page, "next")
    step("3/4 Schema configured")

    # 4 ── course settings. Every one is set AND read back; an unverifiable setting aborts.
    _set_and_verify(page, "setting_course_keep_roles_and_enrolments",
                    "1" if conf["keep_roles_enrolments"] else "0",
                    "Mantener roles e inscripciones (CRITICAL — saves enrolled students)")
    _set_and_verify(page, "setting_course_overwrite_conf",
                    "1" if conf["overwrite_course_config"] else "0",
                    "Sobrescribir configuración del curso")
    _set_and_verify(page, "setting_course_keep_groups_and_groupings",
                    "1" if conf["keep_groups"] else "0",
                    "Mantener grupos y agrupamientos")
    _click_forward(page, "next")
    step("4/4 Course settings set and verified")


def check_target(*, idc: str, backup_path: str | Path | None = None,
                 as_user: str | None = None, identity=None,
                 expect_course_name: str | None = None,
                 headless: bool = True) -> dict:
    """Read-only pre-flight: *would* this restore be allowed, and what would it destroy?

    The dry run already walks the whole wizard — but it **uploads the 50 MB file first**, so a
    target that was going to be refused costs minutes before it says so. This runs only the
    part that can refuse: log in (as whoever), open the course, read its live name, apply
    `verify_target`, and count what is currently in it.

    Nothing is uploaded and nothing is submitted. Safe to run against every candidate target
    before committing to a single restore.
    """
    if identity is None:
        try:
            identity = resolve_identity(as_user)
        except CredentialsMissing as e:
            raise RestoreAborted(str(e)) from e
    strict = not identity.is_self

    manifest = inspect_mbz(backup_path) if backup_path else {}
    out = {"idc": idc, "as_user": identity.username, "acting_for_another": strict,
           "backup": manifest.get("fullname"), "allowed": False}

    log.header(f"Target check — idc={idc} as {identity.describe()}")
    ensure_subprocess_capable_loop()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            _login_campusvirtual(page, settings.moodle_base_url_prod,
                                 identity.username, identity.password)
            tile, host = _find_tile(page, idc, None, None)
            if tile is None:
                raise RestoreAborted(
                    f"Course tile not found for idc={idc} on {identity.username}'s dashboard.")
            vpage = _open_course(ctx, page, tile, host)
            host = host or "virtual3.uach.mx"

            name = _read_course_name(vpage, host, idc)
            out["target_name"] = name
            out["target_subject"] = subject_of(name)
            log.info(f"Target name    : {name!r}")
            log.info(f"Target subject : {out['target_subject']}")
            if manifest:
                log.info(f"Backup subject : {subject_of(manifest.get('fullname'))} "
                         f"({manifest.get('activities')} activities, "
                         f"users={'YES' if manifest.get('includes_users') else 'no'})")

            verify_target(target_name=name, expect_course_name=expect_course_name,
                          backup_name=manifest.get("fullname"), strict=strict)

            total, per_section = _count_activities(vpage, host, idc)
            out["target_activities"] = total
            out["target_sections"] = per_section
            out["allowed"] = True
            log.warning(f"A restore here would DELETE {total} activities across "
                        f"{len(per_section)} sections (enrolments are kept).")
            return out
        finally:
            for c in (ctx, browser):
                try:
                    c.close()
                except Exception:
                    pass


def verify_course(idc: str, *, headless: bool = True) -> dict:
    """Count what is in a course right now. Read-only — no upload, no wizard, no writes."""
    ensure_subprocess_capable_loop()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            _login_campusvirtual(page, settings.moodle_base_url_prod,
                                 settings.uach_username, settings.uach_password)
            tile, host = _find_tile(page, idc, None, None)
            if tile is None:
                raise RestoreAborted(f"Course tile not found for idc={idc}.")
            vpage = _open_course(ctx, page, tile, host)
            host = host or "virtual3.uach.mx"
            total, per_section = _count_activities(vpage, host, idc)
            return {"idc": idc, "modules": total, "sections": per_section}
        finally:
            for c in (ctx, browser):
                try:
                    c.close()
                except Exception:
                    pass


def audit_restore(result: dict, *, env: str = "prod", extra: dict | None = None) -> None:
    """Record a restore attempt — including the ones that refused, and the ones with no DB row.

    🔴 The `--idc` path used to write no audit row at all, and that is exactly the path a
    colleague's course goes through: their course is not in MUSAI's DB, so `restore_for_course`
    (which does audit) cannot serve it. The least-recorded path was the most consequential one.

    `on_behalf_of` is the field Moodle *cannot* hold: it logs the account, so a run as `colleague1`
    is attributed to Colleague A. This row is the only place that says who actually decided.
    """
    from sqlmodel import Session

    from musai.audit import log as audit_log
    from musai.db import engine

    detail = {k: v for k, v in result.items() if k != "steps"}
    detail.update(extra or {})
    if result.get("acting_for_another"):
        detail["on_behalf_of"] = result.get("as_user")
    with Session(engine) as sess:
        audit_log(sess, "course_restore", actor="carlos",
                  target=f"idc:{result.get('idc')} {result.get('target_name') or ''}".strip(),
                  env=env, dry_run=bool(result.get("dry_run", True)), detail=detail)
        sess.commit()


def restore_for_course(course, backup_path: str | Path, *, dry_run: bool = True,
                       headless: bool = True, force: bool = False,
                       restore_settings: dict | None = None,
                       as_user: str | None = None, expect_course_name: str | None = None,
                       on_step=None) -> dict:
    """Restore for a DB `Course` row, with the gradebook guard and an AuditLog either way."""
    from sqlmodel import Session, select

    from musai.audit import log as audit_log
    from musai.db import engine
    from musai.models import Activity, Grade

    if not course.moodle_course_id:
        raise RestoreAborted(f"Course {course.group_code} has no moodle_course_id.")

    # 🔴 A restore deletes the course contents, and the gradebook with them. At semester start
    # that costs nothing (0 activities); once grades exist it is destructive and irreversible.
    with Session(engine) as sess:
        act_ids = list(sess.exec(select(Activity.id).where(Activity.course_id == course.id)).all())
        n_grades = 0
        if act_ids:
            n_grades = len(list(sess.exec(
                select(Grade.id).where(Grade.activity_id.in_(act_ids))).all()))
    if n_grades and not force:
        raise RestoreAborted(
            f"{course.group_code} already holds {n_grades} grades across {len(act_ids)} "
            f"activities. A restore WIPES the gradebook. Re-run with force=True only if you "
            f"have re-fetched them or accept losing them."
        )

    result = restore_course(
        idc=str(course.moodle_course_id), backup_path=backup_path, dry_run=dry_run,
        headless=headless, group_label=course.group_code,
        restore_settings=restore_settings, as_user=as_user,
        expect_course_name=expect_course_name, on_step=on_step,
    )
    detail = {**{k: v for k, v in result.items() if k != "steps"},
              "grades_at_risk": n_grades, "forced": force}
    if result.get("acting_for_another"):
        detail["on_behalf_of"] = result.get("as_user")
    with Session(engine) as sess:
        audit_log(sess, "course_restore", actor="carlos",
                  target=f"course:{course.id} idc:{course.moodle_course_id}",
                  env=course.moodle_env, dry_run=dry_run, detail=detail)
        sess.commit()
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Restore a .mbz backup into a Moodle course (DRY RUN unless --apply).")
    ap.add_argument("--group", help="Course group_code, e.g. 1-LED-A")
    ap.add_argument("--file", help="Path to the .mbz backup on YOUR disk")
    ap.add_argument("--verify", action="store_true",
                    help="Only count what is in the course right now. Read-only.")
    ap.add_argument("--check-target", action="store_true",
                    help="Read-only pre-flight: would this restore be allowed, and what would "
                         "it destroy? No upload, no wizard.")
    ap.add_argument("--idc", help="Moodle course id (overrides the course record)")
    ap.add_argument("--semester", help="Semester name (default: the active one)")
    ap.add_argument("--apply", action="store_true",
                    help="Actually perform the restore. WIPES THE COURSE GRADEBOOK.")
    ap.add_argument("--force", action="store_true",
                    help="Proceed even though MUSAI holds grades for this course")
    ap.add_argument("--headless", action="store_true", help="Run without a visible browser")
    ap.add_argument("--as-user", metavar="USERNAME",
                    help="Log in as this Moodle account instead of your own. Password comes "
                         "from MOODLE_PWD_<USERNAME>; requires --expect-name.")
    ap.add_argument("--expect-name", metavar="TEXT",
                    help="Refuse unless the target course's live name contains TEXT. "
                         "REQUIRED with --as-user.")
    args = ap.parse_args()

    if not args.group and not args.idc:
        ap.error("--group or --idc is required")
    if not args.verify and not args.check_target and not args.file:
        ap.error("--file is required (or use --verify / --check-target)")
    # `verify_target` enforces this too, but only after a login and a page load. Acting as
    # someone else should fail in the first millisecond, not the first minute.
    if args.as_user and not args.expect_name:
        ap.error("--expect-name is required with --as-user: name the course you mean, because "
                 "their account can open every course they teach and a restore wipes it.")

    if args.check_target:
        if not args.idc:
            ap.error("--check-target needs --idc")
        try:
            info = check_target(idc=args.idc, backup_path=args.file, as_user=args.as_user,
                                expect_course_name=args.expect_name, headless=args.headless)
        except RestoreAborted as e:
            log.error(str(e))
            sys.exit(2)
        log.success(f"ALLOWED — idc={info['idc']} {info.get('target_name')!r} currently holds "
                    f"{info.get('target_activities')} activities.")
        return

    if args.verify:
        idc = args.idc
        if not idc:
            from sqlmodel import Session

            from musai.db import engine, init_db
            from musai.semesters import course_for
            init_db()
            with Session(engine) as sess:
                c = course_for(sess, args.group, semester_name=args.semester)
            if c is None or not c.moodle_course_id:
                log.error(f"No idc for {args.group}.")
                sys.exit(1)
            idc = c.moodle_course_id
        info = verify_course(str(idc), headless=args.headless)
        log.success(f"{info['modules']} activities in idc={idc}")
        for sec, n in sorted(info["sections"].items()):
            log.info(f"   section {sec:<3} {n} activities")
        return

    dry_run = not args.apply
    if not dry_run and settings.dry_run:
        log.warning("Global DRY_RUN=true in .env, but --apply was passed for this one action.")

    try:
        if args.group:
            from sqlmodel import Session

            from musai.db import engine, init_db
            from musai.semesters import course_for
            init_db()
            with Session(engine, expire_on_commit=False) as sess:
                course = course_for(sess, args.group, semester_name=args.semester)
            if course is None:
                log.error(f"No course {args.group} in "
                          f"{args.semester or 'the active semester'}.")
                sys.exit(1)
            if args.idc:
                course.moodle_course_id = args.idc
            result = restore_for_course(course, args.file, dry_run=dry_run,
                                        headless=args.headless, force=args.force,
                                        as_user=args.as_user,
                                        expect_course_name=args.expect_name)
        else:
            result = restore_course(idc=args.idc, backup_path=args.file, dry_run=dry_run,
                                    headless=args.headless, as_user=args.as_user,
                                    expect_course_name=args.expect_name)
            audit_restore(result)
    except RestoreAborted as e:
        log.error(str(e))
        # A refusal while acting for someone else is exactly what an audit trail must hold.
        if args.as_user:
            try:
                audit_restore({"idc": args.idc, "dry_run": dry_run, "ok": False,
                               "as_user": args.as_user, "acting_for_another": True,
                               "error": str(e)})
            except Exception:
                pass
        sys.exit(2)

    if result.get("ok"):
        log.success("DRY RUN complete — nothing was restored." if result["dry_run"]
                    else f"Restore performed — {result.get('modules')} activities in the course.")
        if result.get("screenshot"):
            log.info(f"Review screenshot → {result['screenshot']}")
    else:
        log.error(f"Failed: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
