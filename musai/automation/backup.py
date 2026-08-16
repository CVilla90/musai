"""Create a Moodle course backup (`.mbz`) and bring it to disk. LOCAL RUNNER ONLY.

The mirror image of `musai/automation/restore.py`. Together they are the whole
course-copying story: **back up once, restore many.**

The wizard the owner drives by hand is five pages long; this drives the two that matter:

1. `GET /backup/backup.php?id=<idc>` — the navbar's *Copia de respaldo* link is exactly this
   URL, so the nav never has to be walked.
2. `input[name="oneclickbackup"]` (*Saltar al paso final*) — Moodle's own shortcut. It accepts
   every default and runs steps 2-5 in one POST.
3. *Continuar* → `/backup/restorefile.php?contextid=<ctx>`, where the finished file is listed
   in **Zona de respaldos privados del usuario**.
4. The `pluginfile.php/…/user/backup/<name>.mbz?forcedownload=1` link is fetched through the
   **browser context's own `request`** — same cookies, no download dialog, no temp path, and
   the bytes land under the name we chose.

🔴 **"The newest `.mbz`" is not a safe way to pick the file.** The private backup area is
per-user and cumulative — it holds every backup of every course you have ever made. A run that
grabs the newest one silently hands you another course's content. So the filenames are
snapshotted *before* the backup runs and the file we take is **the one that appeared**, with
"newest whose name contains `-course-<idc>-`" only as a fallback.

🔴 **Verify the bytes, never the banner.** `inspect_mbz()` opens the archive and reads
`moodle_backup.xml`. That is what says which course it really is and — the part that matters
for handing a file to a colleague — **whether it carries user data**.

⚠️ **A backup is the cheap half; a restore is the expensive half.** Creating one takes seconds
to a couple of minutes. Restoring a 50 MB file takes ~15 minutes. Never back up per target —
back up once and pass the same path to every `restore_course()` call.

⚠️ **Same account on both ends? Do not use this module at all.** `/backup/import.php?id=<target>`
copies course→course with no file, no upload and no 15-minute restore (COURSE_EDITING §7). The
`.mbz` road exists for the case Import cannot serve: **source and target owned by different
people**, where the file is the only thing that crosses.
"""

import re
import tarfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree as ET

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.credentials import CredentialsMissing, resolve
from musai.automation.moodle_export import _find_tile, _login_campusvirtual, _open_course, _shot
from musai.config import settings

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"
BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "course_backups"

# How long to let Moodle build the file. 50 MB took well under a minute on this instance, but
# the step is a queued PHP job and a busy server is not an error.
BUILD_TIMEOUT_MS = 1_200_000  # 20 minutes

# Moodle names a course backup
#   respaldo-moodle2-course-<idc>-<shortname>-<YYYYMMDD>-<HHMM>[-nu].mbz
# ("backup-" on an English instance). 🔴 The `-nu` suffix means **no users** — see
# `carries_user_data()`; it is a hint, not the proof.
_MBZ_STAMP = re.compile(r"-(\d{8})-(\d{4})")


class BackupAborted(RuntimeError):
    """Raised when a precondition fails. No file has been created when this is raised."""


# ── Reading a .mbz, which is the only honest verification ─────────────────────
def inspect_mbz(path: str | Path) -> dict:
    """Open a `.mbz` and report what is actually inside it. Pure, offline, read-only.

    A `.mbz` is a **gzipped tar**, and `moodle_backup.xml` at its root is the manifest. This is
    the authoritative answer to "which course is this?" and "does it carry user data?" — the
    filename is only a hint, and the download page is only a banner.
    """
    path = Path(path)
    out: dict = {"path": str(path), "bytes": path.stat().st_size if path.is_file() else 0,
                 "ok": False, "course_id": None, "fullname": None, "shortname": None,
                 "includes_users": None, "anonymized": None, "activities": None,
                 "moodle_release": None, "backup_date": None}
    if not path.is_file():
        out["error"] = f"No such file: {path}"
        return out
    try:
        with tarfile.open(path, "r:*") as tar:
            member = tar.extractfile("moodle_backup.xml")
            if member is None:
                out["error"] = "No moodle_backup.xml — not a Moodle course backup."
                return out
            root = ET.fromstring(member.read())
    except tarfile.ReadError as e:
        out["error"] = f"Not a readable gzipped tar: {e}"
        return out
    except KeyError:
        out["error"] = "No moodle_backup.xml — not a Moodle course backup."
        return out

    info = root.find("information")
    if info is not None:
        out["moodle_release"] = (info.findtext("moodle_release") or "").strip() or None
        out["backup_date"] = (info.findtext("backup_date") or "").strip() or None
        orig = info.find("original_course_id")
        if orig is not None and (orig.text or "").strip():
            out["course_id"] = orig.text.strip()
        out["fullname"] = (info.findtext("original_course_fullname") or "").strip() or None
        out["shortname"] = (info.findtext("original_course_shortname") or "").strip() or None
        out["activities"] = len(info.findall(".//contents/activities/activity"))

    # The root-level settings are where "were users included?" is recorded, and it is a
    # *setting*, not a count — a backup with users enabled but no students enrolled still
    # says 1. Fail towards "this might carry user data".
    #
    # ⚠️ `.//settings/setting` also walks the per-section and per-activity settings, which are
    # named `section_<id>_userinfo` / `<mod>_<id>_userinfo` — only the root one is bare `users`.
    # Take the FIRST match anyway: a later collision must not be able to overwrite the answer
    # to the one question this function exists to answer.
    for setting in root.findall(".//settings/setting"):
        name = (setting.findtext("name") or "").strip()
        value = (setting.findtext("value") or "").strip()
        truthy = value not in ("0", "", "false")
        if name == "users" and out["includes_users"] is None:
            out["includes_users"] = truthy
        elif name == "anonymize" and out["anonymized"] is None:
            out["anonymized"] = truthy
        elif name == "filename" and not out.get("moodle_filename"):
            out["moodle_filename"] = value

    out["ok"] = bool(out["course_id"])
    return out


def carries_user_data(path: str | Path) -> bool | None:
    """True / False / **None = could not tell**. Callers must treat `None` as "assume yes".

    🔴 This is the gate for handing a file to another professor. COURSE_EDITING §7 measured that
    the teacher-role backup form has **no user-data option at all** (`setting_root_users` is
    absent), so a teacher's backup structurally cannot carry students — but "structurally cannot"
    was measured on one form on one day, and student data is the one thing not worth trusting a
    remembered measurement about. Read the file.
    """
    return inspect_mbz(path).get("includes_users")


# ── The wizard ────────────────────────────────────────────────────────────────
def _mbz_links(page) -> list[str]:
    """Absolute hrefs of every `.mbz` listed in the user's private backup area."""
    hrefs = page.evaluate(
        """() => [...document.querySelectorAll('a[href*="pluginfile.php"]')]
                   .map(a => a.getAttribute('href') || '')
                   .filter(h => h.includes('.mbz'))"""
    )
    return [urljoin(page.url, h) for h in hrefs]


def _name_of(href: str) -> str:
    return unquote(href.split("?")[0].rsplit("/", 1)[-1])


def _stamp_of(name: str) -> str:
    m = _MBZ_STAMP.search(name)
    return f"{m.group(1)}{m.group(2)}" if m else ""


def _pick_new_backup(before: list[str], after: list[str], idc: str) -> str:
    """The file this run produced.

    🔴 **The course filter comes first and is never skipped.** An earlier cut short-circuited on
    "exactly one file appeared" *before* checking the course, which is wrong in the one case that
    matters: the private area is shared by every course you own, so a backup of another course
    landing in the same window is also "the one new file" — and it would be downloaded, restored
    into a colleague's group, and look entirely successful. Narrow to this course, *then* prefer
    what is new, *then* take the newest. A name that does not identify the course is not a
    candidate at all.
    """
    mine = [h for h in after if f"-course-{idc}-" in _name_of(h)]
    if not mine:
        raise BackupAborted(
            f"No backup for course {idc} in the private backup area after the run "
            f"({len(after)} .mbz files there, none naming this course). Nothing downloaded."
        )
    seen = {_name_of(h) for h in before}
    fresh = [h for h in mine if _name_of(h) not in seen]
    return max(fresh or mine, key=lambda h: _stamp_of(_name_of(h)))


# 🔴 MEASURED 2026-08-13 on `aulas1.uach.mx` (Moodle **4.5**): the forward control is a
# `<button>`, not an `<input type="submit">`. 3.3 renders it as an input, and this list held
# only the input shapes — so on 4.5 `has_continue` was False for the whole run.
#
# That mattered far more than a missed click, because `_wait_for_build` uses it as the **gate**:
# `if has_continue and (ok_rx.search(body) or …)`. Colleague B's EGB1A backup finished server-side and
# the page said «El proceso de respaldo ha completado exitosamente» at 100 % — and this waited
# out all 20 minutes and raised a timeout over the top of a success. The archive was there the
# whole time; only re-reading the private area found it.
#
# ⭐ Same disease as every "cried wolf" entry in HANDOFF: **an instrument shaped like the old
# system answers confidently and backwards about the new one.** The text signal was correct on
# both versions; the widget selector was what was version-specific, and it was the gate.
_CONTINUE_SEL = ('input[type="submit"][value*="Continuar"], '
                 'input[type="submit"][value*="Continue"], '
                 'button:has-text("Continuar"), '
                 'button:has-text("Continue")')


def _wait_for_build(page, step) -> None:
    """Wait out the backup job, then confirm it finished rather than assuming it did.

    Moodle answers *Saltar al paso final* with a progress page and only then the result page.
    The forward control on the result page is a plain `Continuar` submit — the same widget the
    error page uses, so its presence is necessary but not sufficient; the text is checked too.

    🔴 **Every DOM read in this loop must tolerate a navigation in flight.** The click that
    starts the backup is `no_wait_after=True`, so the first poll frequently lands mid-navigation
    and *any* call — `inner_text`, and just as much `locator.count()` — dies with
    `Execution context was destroyed`. The first live run failed exactly there, on the
    `count()` that was left unguarded because it "only counts". The exception then unwinds
    through `finally` and closes the browser while Moodle is still building the file: the job
    finishes server-side and the run reports failure. A destroyed context is not an error here,
    it is the normal appearance of progress.
    """
    deadline = time.monotonic() + BUILD_TIMEOUT_MS / 1000
    started, last_note = time.monotonic(), 0.0
    ok_rx = re.compile(r"(respaldo|backup).{0,40}(exitosa|exitosamente|successfully)", re.I)
    err_rx = re.compile(r"(error|excepci[oó]n|exception)", re.I)
    while time.monotonic() < deadline:
        body, has_continue, url = "", False, ""
        try:
            url = page.url
            body = page.inner_text("body", timeout=10000)
            has_continue = page.locator(_CONTINUE_SEL).first.count() > 0
        except Exception:
            pass  # navigating — nothing readable yet, and that is progress, not failure

        if has_continue and (ok_rx.search(body) or "restorefile.php" in url):
            step("Backup file created")
            return
        if has_continue and err_rx.search(body[:400]):
            _shot(page, "backup_build_error")
            raise BackupAborted(
                "Moodle's backup step finished on an error page. Screenshot saved; nothing "
                "was downloaded.")

        elapsed = time.monotonic() - started
        if elapsed - last_note >= 30:
            last_note = elapsed
            step(f"…still building the backup ({int(elapsed)}s)")
        try:
            page.wait_for_timeout(2000)
        except Exception:
            time.sleep(2)
    _shot(page, "backup_build_timeout")
    raise BackupAborted(
        f"The backup did not finish within {BUILD_TIMEOUT_MS // 60000} minutes. It may still be "
        f"running server-side — check the private backup area before re-running.")


def backup_course(
    *,
    idc: str,
    dry_run: bool = True,
    headless: bool = True,
    out_dir: str | Path | None = None,
    out_name: str | None = None,
    download: bool = True,
    group_label: str = "",
    as_user: str | None = None,
    identity=None,
    on_step=None,
) -> dict:
    """Back up Moodle course `idc` and download the `.mbz`. Returns a result dict.

    `dry_run=True` (default, rail 2) opens the backup form, proves this account may back this
    course up, screenshots it — and does **not** click *Saltar al paso final*. A backup is the
    least destructive write MUSAI makes (it adds a file, it does not touch the course), but the
    dry run is still worth its two page loads: it is the cheap capability check that tells you
    whether the expensive run can work at all.

    🔴 `as_user` (2026-08-13) backs up **another professor's** course, resolved through
    `credentials.resolve`, which refuses rather than falling back to the owner's own login. Added
    for English IV: the master course of that level is Colleague A's 4EF-A, so *"back the finished
    master up and restore it into the others"* — the propagation step every previous level
    used — was otherwise impossible. `dump_targets_english_iv.py` said this out loud as a
    limitation before it was one.

    ⚠️ It writes a file into **that professor's private backup area**, which is the one visible
    trace of this run in their account. That is a real side effect, it is theirs to see, and it
    is why the audit row records `as_user`.
    """
    # `identity` (2026-08-14) is an already-resolved account, used by the web cockpit so a
    # signed-in professor backs up as THEMSELVES rather than as whatever `.env` holds. Passing
    # it skips `resolve()` entirely — it does not override `as_user`, because a caller that
    # supplied both would be stating two different intentions about whose account this is.
    if identity is not None and as_user:
        raise BackupAborted("Pass either `identity` or `as_user`, never both — they name "
                            "different accounts and there is no safe way to pick one.")
    if identity is None:
        try:
            identity = resolve(as_user)
        except CredentialsMissing as e:
            raise BackupAborted(str(e)) from e
    user, pwd = identity.username, identity.password
    if not user or not pwd:
        raise BackupAborted("UACH credentials missing (UACH_USERNAME / UACH_PASSWORD in .env). "
                            "Run from the project root — settings reads .env relative to CWD.")

    dest_dir = Path(out_dir) if out_dir else BACKUP_DIR
    out: dict = {"ok": False, "dry_run": dry_run, "idc": idc, "created": False,
                 "file": None, "bytes": 0, "url": None, "screenshot": None,
                 "manifest": None, "as_user": identity.username,
                 "acting_for_another": not identity.is_self, "steps": []}

    def step(msg: str) -> None:
        out["steps"].append(msg)
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    log.header(f"Backup course idc={idc} {group_label} "
               f"{'[DRY RUN]' if dry_run else '[LIVE — creates a .mbz]'}")

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
                raise BackupAborted(
                    f"Course tile not found for idc={idc} on this account's dashboard. The "
                    f"campusvirtual portal only lists courses YOU are enrolled in — a colleague's "
                    f"course cannot be reached from here.")
            vpage = _open_course(ctx, page, tile, host)
            host = host or "virtual3.uach.mx"
            step("Course opened")

            # `restorefile.php` wants a CONTEXT id, not a course id (restore.py §the hard way).
            vpage.goto(f"https://{host}/course/view.php?id={idc}",
                       wait_until="domcontentloaded", timeout=60000)
            contextid = vpage.evaluate("() => (window.M && M.cfg && M.cfg.contextid) || null")

            # ── Snapshot the private area BEFORE, so "the new file" is knowable ──────────
            before: list[str] = []
            if contextid:
                vpage.goto(f"https://{host}/backup/restorefile.php?contextid={contextid}",
                           wait_until="domcontentloaded", timeout=60000)
                before = _mbz_links(vpage)
                step(f"Private backup area holds {len(before)} .mbz before this run")

            # ── The backup form ─────────────────────────────────────────────────────────
            backup_url = f"https://{host}/backup/backup.php?id={idc}"
            vpage.goto(backup_url, wait_until="domcontentloaded", timeout=60000)
            try:
                vpage.wait_for_load_state("networkidle", timeout=25000)
            except PWTimeout:
                pass

            oneclick = vpage.locator('input[name="oneclickbackup"]').first
            if not oneclick.count():
                _shot(vpage, "backup_no_oneclick")
                raise BackupAborted(
                    f"{backup_url} has no 'Saltar al paso final' button. Either this account "
                    f"lacks the backup capability on course {idc}, or the page is an error.")
            step("Backup form open — 'Saltar al paso final' present")

            shot = SHOT_DIR / (f"backup_{'dryrun' if dry_run else 'live'}_"
                               f"{group_label or idc}_{datetime.now():%Y%m%d_%H%M%S}.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            vpage.screenshot(path=str(shot), full_page=True)
            out["screenshot"] = str(shot)

            if dry_run:
                step("DRY RUN — the account can back this course up; no file was created")
                out["ok"] = True
                return out

            oneclick.click(timeout=30000, no_wait_after=True)
            out["created"] = True  # the job is away
            step("Running the backup — accepting every default…")
            _wait_for_build(vpage, step)

            cont = vpage.locator(_CONTINUE_SEL).first
            if cont.count():
                cont.click(timeout=30000)
                try:
                    vpage.wait_for_load_state("networkidle", timeout=60000)
                except PWTimeout:
                    pass
            step("Landed on the private backup area")

            after = _mbz_links(vpage)
            if not after and contextid:
                vpage.goto(f"https://{host}/backup/restorefile.php?contextid={contextid}",
                           wait_until="domcontentloaded", timeout=60000)
                after = _mbz_links(vpage)
            href = _pick_new_backup(before, after, idc)
            out["url"] = href
            name = _name_of(href)
            step(f"Backup file → {name}")

            if not download:
                out["ok"] = True
                return out

            # ── Download through the session's own request context ──────────────────────
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / (out_name or name)
            resp = ctx.request.get(href, timeout=BUILD_TIMEOUT_MS)
            if not resp.ok:
                raise BackupAborted(f"Download returned HTTP {resp.status} for {name}.")
            body = resp.body()
            if len(body) < 1024 or body[:2] != b"\x1f\x8b":
                raise BackupAborted(
                    f"What came back is not a gzip archive ({len(body)} bytes). Moodle probably "
                    f"served an HTML page — a session that expired mid-download does exactly "
                    f"this. Nothing was written.")
            target.write_bytes(body)
            out["file"] = str(target)
            out["bytes"] = len(body)
            step(f"Downloaded {len(body) / 1_048_576:.1f} MB → {target}")

            # ── Verify by reading the archive, not the page ──────────────────────────────
            manifest = inspect_mbz(target)
            out["manifest"] = manifest
            if not manifest["ok"]:
                raise BackupAborted(
                    f"Downloaded file is not a readable Moodle backup: {manifest.get('error')}")
            if str(manifest["course_id"]) != str(idc):
                raise BackupAborted(
                    f"🔴 The downloaded backup is for course {manifest['course_id']}, not {idc}. "
                    f"Kept at {target} for inspection — do NOT restore it anywhere.")
            step(f"Verified: course {manifest['course_id']} · {manifest['fullname']} · "
                 f"{manifest['activities']} activities · "
                 f"users={'YES' if manifest['includes_users'] else 'no'}")
            if manifest["includes_users"]:
                log.warning("🔴 This backup INCLUDES USER DATA. Do not hand it to another "
                            "professor — it would carry your students into their course.")
            out["ok"] = True
            return out

        except Exception as e:
            out["error"] = describe_exception(e)
            step(f"ERROR: {out['error']}")
            try:
                if page is not None:
                    _shot(page, "backup_error")
            except Exception:
                pass
            log.error(f"Backup failed: {out['error']}")
            return out
        finally:
            for closeable in (ctx, browser):
                if closeable is not None:
                    try:
                        closeable.close()
                    except Exception:
                        pass


def backup_for_course(course, *, dry_run: bool = True, headless: bool = True,
                      out_dir: str | Path | None = None, out_name: str | None = None,
                      download: bool = True, on_step=None) -> dict:
    """Back up a DB `Course` row, with an AuditLog either way."""
    from sqlmodel import Session

    from musai.audit import log as audit_log
    from musai.db import engine

    if not course.moodle_course_id:
        raise BackupAborted(f"Course {course.group_code} has no moodle_course_id.")

    result = backup_course(idc=str(course.moodle_course_id), dry_run=dry_run, headless=headless,
                           out_dir=out_dir, out_name=out_name, download=download,
                           group_label=course.group_code, on_step=on_step)
    with Session(engine) as sess:
        audit_log(sess, "course_backup", actor="carlos",
                  target=f"course:{course.id} idc:{course.moodle_course_id}",
                  env=course.moodle_env, dry_run=dry_run,
                  detail={k: v for k, v in result.items() if k != "steps"})
        sess.commit()
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Create and download a Moodle course backup (DRY RUN unless --apply).")
    ap.add_argument("--group", help="Course group_code, e.g. 1-LED-A")
    ap.add_argument("--idc", help="Moodle course id (overrides the course record)")
    ap.add_argument("--semester", help="Semester name (default: the active one)")
    ap.add_argument("--apply", action="store_true", help="Actually create the backup")
    ap.add_argument("--out-dir", help=f"Where to save (default {BACKUP_DIR})")
    ap.add_argument("--out-name", help="Save under this filename instead of Moodle's")
    ap.add_argument("--no-download", action="store_true",
                    help="Create it on Moodle but leave the file there")
    ap.add_argument("--headless", action="store_true", help="Run without a visible browser")
    ap.add_argument("--inspect", metavar="MBZ",
                    help="Read an existing .mbz and print its manifest. Offline, no browser.")
    ap.add_argument("--as-user", metavar="USERNAME",
                    help="Back up as this Moodle account instead of your own. Password comes "
                         "from MOODLE_PWD_<USERNAME>. Needed for any course you do not teach.")
    args = ap.parse_args()

    if args.inspect:
        info = inspect_mbz(args.inspect)
        if not info["ok"]:
            log.error(info.get("error", "Unreadable backup."))
            sys.exit(1)
        log.success(f"{Path(info['path']).name} — {info['bytes'] / 1_048_576:.1f} MB")
        log.info(f"   course {info['course_id']} · {info['fullname']}")
        log.info(f"   shortname {info['shortname']} · Moodle {info['moodle_release']}")
        log.info(f"   {info['activities']} activities")
        if info["includes_users"]:
            log.warning("   🔴 INCLUDES USER DATA — never hand this to another professor.")
        else:
            log.success("   no user data — safe to hand to a colleague.")
        return

    if not args.group and not args.idc:
        ap.error("--group or --idc is required (or --inspect)")

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
                log.error(f"No course {args.group} in {args.semester or 'the active semester'}.")
                sys.exit(1)
            if args.idc:
                course.moodle_course_id = args.idc
            result = backup_for_course(course, dry_run=dry_run, headless=args.headless,
                                       out_dir=args.out_dir, out_name=args.out_name,
                                       download=not args.no_download)
        else:
            result = backup_course(idc=args.idc, dry_run=dry_run, headless=args.headless,
                                   out_dir=args.out_dir, out_name=args.out_name,
                                   as_user=args.as_user,
                                   download=not args.no_download)
    except BackupAborted as e:
        log.error(str(e))
        sys.exit(2)

    if result.get("ok"):
        if result["dry_run"]:
            log.success("DRY RUN complete — no backup was created.")
        elif result.get("file"):
            m = result["manifest"] or {}
            log.success(f"Backup saved → {result['file']} "
                        f"({result['bytes'] / 1_048_576:.1f} MB, "
                        f"{m.get('activities')} activities)")
        else:
            log.success(f"Backup created on Moodle → {result.get('url')}")
        if result.get("screenshot"):
            log.info(f"Form screenshot → {result['screenshot']}")
    else:
        log.error(f"Failed: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
