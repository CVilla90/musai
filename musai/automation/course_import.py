"""Copy a course into another course with **no file at all** — `/backup/import.php`.

The fast lane. `restore.py` makes a 50 MB `.mbz`, uploads it, and waits ~15 minutes for
Moodle's queued PHP job. Import skips every one of those steps: Moodle already has both
courses, so it copies server-side. Measured elsewhere at **~2 minutes against ~15**, and this
module times itself so that number gets *verified* rather than repeated.

🔴 **It buys the speed by trading one danger for a different one, and the new one is worse to
undo.** A restore WIPES the destination first — dangerous, but self-correcting: a wrong restore
is one right restore away from fixed. **Import MERGES.** Run it into a course that already has
content and Moodle does not replace anything; it adds a second copy of all 79 activities, each
with its own gradebook item, and unpicking that is hand work across every section. So the
central rail here is the mirror image of `restore.py`'s:

    restore.py refuses when the target has grades.
    course_import.py refuses when the target has CONTENT.

⚠️ **`import.php` is only offered on courses this account can EDIT — both ends.**
`COURSE_EDITING §7b` measured that with a control: from Carlos's account, four colleagues'
courses report `import.php` absent while his own report it present. So this module serves the
case where one professor holds several groups of the same subject (the common one for a new
MUSAI user) and **cannot** serve propagation across professors — that stays `backup.py` +
`restore.py` with `--as-user`. A source course that is not in the wizard's list is a refusal
naming that reason, not a retry.

⚠️ **UNMEASURED and decisive: does import carry the course FORMAT?** `COURSE_EDITING §10.1`
has been carrying this question since 2026-08-08. If `onetopic` does not travel, the copy has
no tab strip and the Cronograma's tab map has nothing to read. This module therefore **reads
the format and section count at both ends, before and after**, and reports them — so the first
real run answers the question instead of raising it again.

🔴 **The selectors below past the first stage are UNVERIFIED.** The course-chooser radio
(`input[type=radio][name="importid"]`) is measured — `scratchpad/probe_copy_paths.py` listed
all seven courses through it on 2026-08-08. Everything after it is derived from Moodle's form
conventions and has never been driven. This file's own doctrine (`COURSE_EDITING §4`) is that a
selector believed is a selector unmeasured, so every step here **refuses loudly when it does not
find what it expects** rather than falling through to Moodle's defaults, and `probe_wizard()`
exists to turn the guesses into measurements without submitting anything.

Run it:

    python -m musai.automation.course_import --source 9067 --target 9072 --probe
    python -m musai.automation.course_import --source 9067 --target 9072          # dry run
    python -m musai.automation.course_import --source 9067 --target 9072 --apply
"""

import time
from datetime import datetime

from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.automation.credentials import CredentialsMissing, resolve as resolve_identity
from musai.automation.moodle_export import _login_campusvirtual, _shot
from musai.automation.restore import subject_of
from musai.config import settings

#: Ceiling for the copy itself. Generous next to the ~2 minutes this is expected to take —
#: the cost of being wrong downwards is a browser closed mid-import, which `restore.py` learned
#: leaves a course in a state nobody asked for.
IMPORT_TIMEOUT_MS = 600_000

#: A brand-new Moodle course is not empty: it ships with the default `Avisos`/`Announcements`
#: forum. Treating "1 activity" as content would refuse every legitimate first import, so the
#: emptiness rail counts against this floor — and `--allow-merge` is the only way past it.
EMPTY_COURSE_ACTIVITIES = 1


class ImportAborted(RuntimeError):
    """A safety precondition failed. Nothing has been mutated when this is raised."""


# Proven selector first, language-proof fallbacks after — the same shape as `restore._FORWARD`.
# `input.proceedbutton` is the class Moodle puts on "the forward button" regardless of language,
# so it leads wherever a Spanish `value=` would otherwise be the only handle.
_FORWARD = {
    "continue": ['input.proceedbutton[type="submit"][value="Continuar"]',
                 'input.proceedbutton[type="submit"]',
                 '#id_submitbutton'],
    "next": ['input.proceedbutton[type="submit"][value="Siguiente"]',
             'input.proceedbutton[type="submit"]',
             '#id_submitbutton'],
    # ⚠️ Moodle's own shortcut through the two settings stages, and the reason this lane is
    # ~2 minutes: it accepts every default in one POST. The defaults are what we want — Moodle
    # structurally cannot carry user data through an import, so "everything except user data"
    # is already the behaviour, and hand-setting ten `setting_root_*` checkboxes would only add
    # ten ways to be wrong. `backup.py` uses the same trick via `oneclickbackup`.
    "jump": ['input[type="submit"][name="oneclickbackup"]',
             'input[type="submit"][value*="Saltar al paso final"]',
             'input[type="submit"][value*="Jump to final step"]'],
    "perform": ['input.proceedbutton[name="submitbutton"][value*="Realizar la importación"]',
                'input.proceedbutton[name="submitbutton"][value*="Realizar"]',
                'input.proceedbutton[name="submitbutton"]'],
}


# ---------------------------------------------------------------------------
# The pure part — refuses before a browser exists
# ---------------------------------------------------------------------------

def plan_import(*, source_idc: str, target_idc: str,
                source_name: str | None = None, target_name: str | None = None,
                target_activities: int | None = None,
                expect_target_name: str | None = None,
                allow_merge: bool = False, strict: bool = False) -> dict:
    """Decide whether this import may happen. Pure: no browser, no I/O, fully testable.

    The house shape (`CLAUDE.md`: *a pure `plan_*` function with an allow-list that refuses
    before a browser exists*), and here it earns it — every refusal below is one a live run
    would otherwise reach only after a login, a page load and a wizard stage.

    `strict` is set whenever the run acts as another professor, and it turns every *"could not
    tell"* into a refusal. An unreadable name is not a matching name.
    """
    source_idc, target_idc = str(source_idc).strip(), str(target_idc).strip()

    if not source_idc or not target_idc:
        raise ImportAborted("Both --source and --target are required.")
    if source_idc == target_idc:
        raise ImportAborted(
            f"🔴 Source and target are the same course ({source_idc}). Moodle would happily "
            f"import a course into itself and double every activity in it.")

    # 🔴 The rail this module exists for. Import MERGES — it does not replace — so a target
    # holding content comes out holding two of everything, each with its own gradebook item.
    if target_activities is None:
        if not allow_merge:
            raise ImportAborted(
                "🔴 Could not count the target's activities, so it cannot be shown to be empty. "
                "Import merges rather than replaces; refusing. Pass --allow-merge only if you "
                "have looked at the course yourself and want a second copy of its contents.")
    elif target_activities > EMPTY_COURSE_ACTIVITIES and not allow_merge:
        raise ImportAborted(
            f"🔴 Target course {target_idc} already holds {target_activities} activities. "
            f"An import ADDS to a course, it does not replace it — this would leave "
            f"{target_activities} originals plus a full second copy, each with its own "
            f"gradebook item. Refusing. Use restore.py (which wipes first) to overwrite a "
            f"populated course, or --allow-merge if a merge is genuinely what you want.")

    if expect_target_name:
        if not target_name:
            raise ImportAborted(
                "🔴 --expect-name was given but the target's live name could not be read. "
                "An unverifiable target is a refused target.")
        wanted = " ".join(expect_target_name.split()).casefold()
        if wanted not in " ".join(target_name.split()).casefold():
            raise ImportAborted(
                f"🔴 Target check failed. Expected a course named like {expect_target_name!r}; "
                f"Moodle says course {target_idc} is {target_name!r}. Refusing — an idc typo "
                f"opens a perfectly valid course that every later check would pass.")
    elif strict:
        raise ImportAborted(
            "🔴 Acting as another professor requires --expect-name. Their account can reach "
            "every course they teach, in every school.")

    # Longest-first subject matching, from restore.py — `INGLES I` is a prefix of `INGLES III`.
    want, got = subject_of(source_name), subject_of(target_name)
    if want and got and want != got:
        raise ImportAborted(
            f"🔴 Subject mismatch: the source is {want} ({source_name!r}) and the target is "
            f"{got} ({target_name!r}). Refusing.")
    if strict and not (want and got):
        raise ImportAborted(
            f"🔴 Could not read the subject from "
            f"{'the source name' if not want else 'the target name'} "
            f"(source={source_name!r}, target={target_name!r}). Acting for another professor, "
            f"an unverifiable pairing is a refused one.")

    return {
        "source_idc": source_idc,
        "target_idc": target_idc,
        "source_name": source_name,
        "target_name": target_name,
        "target_activities": target_activities,
        "merge": bool(target_activities) and target_activities > EMPTY_COURSE_ACTIVITIES,
        "subject": got or want,
    }


# ---------------------------------------------------------------------------
# Reading a course, without changing it
# ---------------------------------------------------------------------------

def _read_course(page, host: str, idc: str) -> dict:
    """Name, format and section count, as Moodle renders them *now*.

    ⚠️ The format is read here for the question `COURSE_EDITING §10.1` has been carrying since
    2026-08-08: **does import carry the course format?** Reading it at both ends before and
    after turns that from an open question into a measured one on the first real run. `body`'s
    `format-*` class is Moodle's own statement of the format and needs no course settings page.
    """
    page.goto(f"https://{host}/course/view.php?id={idc}",
              wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    return page.evaluate("""() => {
        const cls = [...document.body.classList].find(c => c.startsWith('format-')) || '';
        const h1 = document.querySelector('h1, .page-header-headings h1');
        return {
            name: (h1 ? h1.innerText : document.title).replace(/^Curso:\\s*/i, '').trim(),
            format: cls.replace('format-', ''),
            sections: document.querySelectorAll('[id^=section-]').length,
        };
    }""")


def _count_activities(page, host: str, idc: str, *, max_sections: int = 24) -> int:
    """Every section's activities, summed.

    🔴 Straight from `restore._count_activities`, including the reason it looks wasteful:
    counting on `course/view.php` alone reports **0** for a one-section-at-a-time format, and
    sections are not contiguous, so "stop after N empty ones" undercounts. Here an undercount
    is worse than in restore — it is what would let the emptiness rail wave through a course
    that is not empty.
    """
    total = 0
    for n in range(max_sections):
        page.goto(f"https://{host}/course/view.php?id={idc}&section={n}",
                  wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeout:
            pass
        total += page.evaluate("() => document.querySelectorAll('[id^=module-]').length")
    return total


def _click_forward(page, kind: str, *, timeout: int = 15000,
                   no_wait_after: bool = False, required: bool = True) -> str | None:
    """Click a wizard button, proven selectors before generic ones.

    🔴 `no_wait_after=True` is REQUIRED for the final "Realizar la importación", for the reason
    `restore.py` paid to learn: a normal click blocks until the navigation settles, the copy
    takes minutes, the click times out, and `finally` closes the browser **mid-import**.
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
    if required:
        raise ImportAborted(
            f"No '{kind}' button on the import wizard. Selectors tried: {_FORWARD[kind]}. "
            f"🔴 Refusing rather than guessing — run --probe to dump what is actually there.")
    return None


def _select_source(page, source_idc: str) -> None:
    """Tick the source course's radio, searching for it if it is not on the first page.

    ⚠️ The wizard lists only courses this account can EDIT (`COURSE_EDITING §7b`), and it
    paginates. "Not on the page" therefore has two very different causes — not listed at all,
    or listed further down — and they need different answers, so the search is tried before the
    refusal and the refusal names the permission reason.
    """
    sel = f'input[type=radio][name="importid"][value="{source_idc}"]'
    if not page.locator(sel).count():
        box = page.locator('input[name="search"]').first
        if box.count():
            log.info(f"  source {source_idc} not on the first page — searching")
            box.fill(source_idc)
            page.locator('input[type="submit"][name="searchcourses"], '
                         'button[type="submit"]').first.click()
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PWTimeout:
                pass
    if not page.locator(sel).count():
        listed = page.evaluate(
            "() => [...document.querySelectorAll('input[name=importid]')].map(r => r.value)")
        raise ImportAborted(
            f"🔴 Course {source_idc} is not offered as an import source. The wizard lists "
            f"{listed or 'nothing'}.\n"
            f"   Import only offers courses THIS ACCOUNT CAN EDIT — measured with a control in "
            f"COURSE_EDITING §7b. If the source belongs to a colleague, this lane cannot serve "
            f"it: use backup.py + restore.py --as-user instead.")
    page.locator(sel).first.check()
    log.success(f"✓ source course {source_idc} selected")


# ---------------------------------------------------------------------------
# The read-only probe
# ---------------------------------------------------------------------------

def probe_wizard(*, source_idc: str, target_idc: str, advance: bool = False,
                 as_user: str | None = None, headless: bool = False) -> dict:
    """Open the import wizard and DUMP what is on it. Submits nothing by default.

    This is how the unverified selectors above become measured ones. Two levels:

    * **default** — loads stage 1 (the course chooser) and lists every control. A plain page
      load; there is nothing to be careful about.
    * **`advance=True`** — walks on to the review page. Moodle does not touch the target course
      until the final *Realizar la importación*, so this is believed harmless — but *believed*
      is the operative word, which is why it is opt-in and why it stops one click short and
      screenshots instead.
    """
    ensure_subprocess_capable_loop()
    ident = resolve_identity(as_user)
    host = settings.moodle_base_url_prod.replace("https://", "").rstrip("/")
    found: dict = {"stages": []}

    def dump(page, label: str) -> None:
        info = page.evaluate("""() => ({
            title: document.title,
            heading: (document.querySelector('h1,h2') || {}).innerText || '',
            submits: [...document.querySelectorAll('input[type=submit],button[type=submit]')]
                .map(b => ({name: b.name, value: b.value || b.innerText, cls: b.className})),
            checkboxes: [...document.querySelectorAll('input[type=checkbox]')]
                .slice(0, 40).map(c => ({name: c.name, checked: c.checked})),
            radios: [...document.querySelectorAll('input[type=radio]')]
                .slice(0, 40).map(r => ({name: r.name, value: r.value})),
        })""")
        found["stages"].append({"label": label, **info})
        log.step(f"stage: {label} — {info['heading'][:70]!r}")
        for b in info["submits"]:
            log.info(f"    submit  name={b['name']!r:<22} value={str(b['value'])[:40]!r}")
        for c in info["checkboxes"][:12]:
            log.info(f"    check   name={c['name']!r:<22} checked={c['checked']}")
        _shot(page, f"import_probe_{label}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=False)
        page = ctx.new_page()
        try:
            _login_campusvirtual(page, ident.username, ident.password)
            page.goto(f"https://{host}/backup/import.php?id={target_idc}",
                      wait_until="domcontentloaded", timeout=60000)
            dump(page, "1_choose_source")

            if advance:
                log.warning("--advance: walking to the REVIEW page. Nothing is submitted there.")
                _select_source(page, source_idc)
                _click_forward(page, "continue")
                dump(page, "2_settings")
                if _click_forward(page, "jump", required=False):
                    log.success("✓ 'Saltar al paso final' exists — this is the ~2-minute lane")
                else:
                    log.warning("No jump button; walking the stages one at a time")
                    _click_forward(page, "next")
                    dump(page, "3_schema")
                    _click_forward(page, "next")
                dump(page, "4_review")
                log.success("STOPPED at review. 'Realizar la importación' was NOT clicked.")
        finally:
            for c in (ctx, browser):
                try:
                    c.close()
                except Exception:
                    pass
    return found


# ---------------------------------------------------------------------------
# The import itself
# ---------------------------------------------------------------------------

def import_course(*, source_idc: str, target_idc: str, dry_run: bool = True,
                  headless: bool = True, allow_merge: bool = False,
                  as_user: str | None = None,
                  expect_target_name: str | None = None) -> dict:
    """Copy `source_idc` into `target_idc` with no file. Dry run unless `dry_run=False`.

    Returns a result dict either way. A dry run performs **every** step except the last click,
    which means its refusals are the real ones — the emptiness check, the source-not-listed
    check and the subject check all run against the live pages.
    """
    ensure_subprocess_capable_loop()
    started = time.monotonic()
    strict = bool(as_user)
    result: dict = {
        "source_idc": str(source_idc), "target_idc": str(target_idc),
        "dry_run": dry_run, "as_user": as_user, "acting_for_another": strict,
        "ok": False, "steps": [], "started_at": datetime.now().isoformat(timespec="seconds"),
    }

    def step(msg: str) -> None:
        result["steps"].append({"t": f"{time.monotonic() - started:6.1f}s", "msg": msg})
        log.info(f"  [{time.monotonic() - started:6.1f}s] {msg}")

    try:
        ident = resolve_identity(as_user)
    except CredentialsMissing as e:
        result["error"] = str(e)
        return result

    host = settings.moodle_base_url_prod.replace("https://", "").rstrip("/")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=False)
        page = ctx.new_page()
        try:
            _login_campusvirtual(page, ident.username, ident.password)
            step(f"signed in as {ident.username}")

            # ── Read both ends BEFORE deciding anything ──────────────────────
            src = _read_course(page, host, str(source_idc))
            tgt = _read_course(page, host, str(target_idc))
            result["source"] = src
            result["target"] = tgt
            step(f"source {source_idc}: {src['name']!r} format={src['format']!r}")
            step(f"target {target_idc}: {tgt['name']!r} format={tgt['format']!r}")

            before = _count_activities(page, host, str(target_idc))
            result["target_activities_before"] = before
            step(f"target holds {before} activities before the import")

            # ── Refuse, on live readings rather than remembered ones ─────────
            plan = plan_import(
                source_idc=str(source_idc), target_idc=str(target_idc),
                source_name=src["name"], target_name=tgt["name"],
                target_activities=before, expect_target_name=expect_target_name,
                allow_merge=allow_merge, strict=strict)
            result["plan"] = plan
            log.success("✓ preconditions pass")

            if src["format"] != tgt["format"]:
                # Not a refusal: import may or may not carry the format, which is exactly the
                # open question. Say it loudly and let the after-reading settle it.
                log.warning(f"⚠️ formats differ — source {src['format']!r}, "
                            f"target {tgt['format']!r}. COURSE_EDITING §10.1's open question.")

            # ── The wizard ───────────────────────────────────────────────────
            page.goto(f"https://{host}/backup/import.php?id={target_idc}",
                      wait_until="domcontentloaded", timeout=60000)
            _select_source(page, str(source_idc))
            _click_forward(page, "continue")
            step("source chosen, on the settings stage")

            if _click_forward(page, "jump", required=False):
                step("'Saltar al paso final' — Moodle's own shortcut through both stages")
            else:
                _click_forward(page, "next")
                _click_forward(page, "next")
                step("walked the settings stages one at a time")

            _shot(page, f"import_review_{target_idc}")

            if dry_run:
                result["ok"] = True
                result["stopped_at"] = "review"
                result["elapsed_s"] = round(time.monotonic() - started, 1)
                log.success(f"DRY RUN — stopped at the review page in "
                            f"{result['elapsed_s']}s. Nothing was imported.")
                return result

            # ── The one destructive click ────────────────────────────────────
            step("clicking 'Realizar la importación' — the browser must stay open now")
            _click_forward(page, "perform", no_wait_after=True)
            page.wait_for_selector('input.proceedbutton, a.btn-primary, .box.generalbox',
                                   timeout=IMPORT_TIMEOUT_MS)
            step("Moodle reports the import finished")
            _shot(page, f"import_done_{target_idc}")

            after_course = _read_course(page, host, str(target_idc))
            after = _count_activities(page, host, str(target_idc))
            result["target_activities_after"] = after
            result["target_format_after"] = after_course["format"]
            result["gained"] = after - before
            result["format_travelled"] = after_course["format"] == src["format"]
            step(f"target now holds {after} activities (+{after - before}), "
                 f"format={after_course['format']!r}")

            # 🔴 `restore.py` learned this four times over: a post-write count of zero is far
            # more often the reader being wrong than the write having failed. Report it, never
            # act on it — and never re-run on the strength of it.
            if after <= before:
                log.warning(
                    f"⚠️ count did not rise ({before} → {after}). This is UNKNOWN, not failure: "
                    f"restore.py's own count cried wolf four times on courses holding 79 and 94 "
                    f"activities. Look at the course before doing anything about it.")
            result["ok"] = True

        except ImportAborted as e:
            result["error"] = str(e)
            log.error(str(e))
        except Exception as e:                                # pragma: no cover - live only
            result["error"] = describe_exception(e)
            log.error(result["error"])
            try:
                _shot(page, f"import_error_{target_idc}")
            except Exception:
                pass
        finally:
            result["elapsed_s"] = round(time.monotonic() - started, 1)
            for c in (ctx, browser):
                try:
                    c.close()
                except Exception:
                    pass

    return result


def audit_import(result: dict, *, env: str = "prod") -> None:
    """Record the attempt — including the refusals.

    🔴 `restore.py`'s note applies unchanged: the least-recorded path was the most consequential
    one. A trail holding only successes cannot answer *"what did we try to copy where?"*.
    """
    from sqlmodel import Session

    from musai.audit import log as audit_log
    from musai.db import engine

    detail = {k: v for k, v in result.items() if k != "steps"}
    if result.get("acting_for_another"):
        detail["on_behalf_of"] = result.get("as_user")
    with Session(engine) as sess:
        audit_log(sess, "course_import", actor="carlos",
                  target=f"idc:{result.get('target_idc')} ← idc:{result.get('source_idc')}",
                  env=env, dry_run=bool(result.get("dry_run", True)), detail=detail)
        sess.commit()


def main() -> None:                                           # pragma: no cover - CLI
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Copy a Moodle course into another with no file (DRY RUN unless --apply).")
    ap.add_argument("--source", required=True, help="Moodle course id to copy FROM")
    ap.add_argument("--target", required=True, help="Moodle course id to copy INTO")
    ap.add_argument("--probe", action="store_true",
                    help="Read-only: dump the wizard's real controls. Submits nothing.")
    ap.add_argument("--advance", action="store_true",
                    help="With --probe, walk to the review page (still submits nothing)")
    ap.add_argument("--apply", action="store_true", help="Actually perform the import")
    ap.add_argument("--allow-merge", action="store_true",
                    help="Proceed even though the target already has content. This leaves TWO "
                         "copies of everything — it does not replace.")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--as-user", metavar="USERNAME",
                    help="Log in as this Moodle account. Requires --expect-name.")
    ap.add_argument("--expect-name", metavar="TEXT",
                    help="Refuse unless the target's live name contains TEXT.")
    args = ap.parse_args()

    if args.as_user and not args.expect_name:
        ap.error("--expect-name is required with --as-user: name the course you mean.")

    if args.probe:
        probe_wizard(source_idc=args.source, target_idc=args.target, advance=args.advance,
                     as_user=args.as_user, headless=args.headless)
        return

    dry_run = not args.apply
    if not dry_run and settings.dry_run:
        log.warning("Global DRY_RUN=true in .env, but --apply was passed for this one action.")

    res = import_course(source_idc=args.source, target_idc=args.target, dry_run=dry_run,
                        headless=args.headless, allow_merge=args.allow_merge,
                        as_user=args.as_user, expect_target_name=args.expect_name)
    try:
        audit_import(res)
    except Exception:
        log.warning("Could not write the audit row (the import itself is unaffected).")

    if res.get("error"):
        log.error(res["error"])
        sys.exit(2)
    log.success(f"{'DRY RUN' if dry_run else 'IMPORTED'} in {res.get('elapsed_s')}s — "
                f"{res.get('gained', 0)} activities gained.")


if __name__ == "__main__":                                    # pragma: no cover
    main()
