"""SEGA grade adapter — DRY-RUN read path (LOCAL RUNNER ONLY).

What this does: log into SEGA, open one group, select an evaluación (e.g. PARCIAL 1),
READ the (possibly locked) grade table, and diff it against the 0–10 grades MUSAI
computed. It prints the same kind of per-student summary the original uploader does.

RAILS (structural, not a flag):
  • This module contains NO code that clicks *Guardar* (save) or *Confirmar* (confirm).
    It is physically incapable of writing to SEGA. The save path is deliberately not
    implemented here yet; when it is added it must require dry_run=False + an explicit
    human action AND click only the evaluación-specific 'Guardar Cambios' — never
    'Confirmar'. (PLAN §0 rail 1; CLAUDE.md.)
  • Read-only, so it is safe even though UACH has closed the grading window — selecting
    an evaluación just shows its (locked) grades, which we read.

Adapted from the mature ``sega_grade_uploader/upload_grades.py`` (login, group-list nav,
open-group polling, native/bootstrap select, table snapshot), trimmed to the read path.

CLI:
    python -m musai.automation.sega --group 1-LED-A --partial "Parcial 1"
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

from musai.config import settings
from musai.automation._log import logger as log
from musai.automation._loop import ensure_subprocess_capable_loop

SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

# Materia label as it appears in the SEGA group list (no accents, upper).
MATERIA_BY_LEVEL = {1: "INGLES I", 2: "INGLES II", 3: "INGLES III", 4: "INGLES IV"}
# A grade token: 10, 10.0, or N.N (one decimal). Matrículas (6–10 digits) and faltas
# (plain ints) never match N.N, so this safely picks a grade out of locked row text.
_GRADE_RX = re.compile(r"\b(10(?:\.0)?|[0-9]\.[0-9])\b")


# ── browser helpers (ported, read-only) ───────────────────────────────────────
def _shot(page: Page, name: str) -> None:
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.png"
        page.screenshot(path=str(path), full_page=True)
        log.info(f"Screenshot → {path}")
    except Exception:
        pass


def _close_popup(page: Page) -> None:
    for sel in ("button:has-text('Cerrar')", "button[aria-label='Close']", "div[role='dialog'] button"):
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.click(timeout=800)
        except Exception:
            pass


def _login(page: Page, base_url: str, user: str, pwd: str) -> None:
    log.step("Logging in to SEGA…")
    page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
    _close_popup(page)

    def on_home() -> bool:
        try:
            if page.get_by_text(re.compile(r"Calificaciones?\s*y\s*faltas|Calificaciones por grupo", re.I)).count():
                return True
            return "sign_in" not in (page.url or "")
        except Exception:
            return False

    if on_home():
        log.success("Already authenticated.")
        return

    form = page.locator("form").first
    for loc in (
        page.get_by_label("Usuario UACH:", exact=False),
        page.get_by_placeholder(re.compile(r"usuario", re.I)),
        form.locator("input[name*='user' i]"),
        page.locator("input[type='text']"),
    ):
        try:
            if loc.count():
                loc.first.fill(user, timeout=5000); break
        except Exception:
            continue
    for loc in (
        page.get_by_label("Contraseña:", exact=False),
        page.get_by_placeholder(re.compile(r"contraseña|password", re.I)),
        form.locator("input[type='password']"),
    ):
        try:
            if loc.count():
                loc.first.fill(pwd, timeout=5000); break
        except Exception:
            continue
    clicked = False
    for loc in (
        page.get_by_role("button", name=re.compile(r"Entrar|Ingresar|Login", re.I)),
        form.locator("button[type='submit']"),
        form.locator("input[type='submit']"),
    ):
        try:
            if loc.count():
                loc.first.click(timeout=5000); clicked = True; break
        except Exception:
            continue
    if not clicked:
        page.keyboard.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=60000)
    except PWTimeout:
        pass
    _close_popup(page)
    if "sign_in" in (page.url or "") and not on_home():
        _shot(page, "sega_login_failed")
        raise RuntimeError("SEGA login failed. Screenshot saved.")
    log.success("Logged in to SEGA.")


def _origin(url: str) -> str:
    m = re.match(r"(https?://[^/]+)", url or "")
    return m.group(1) if m else ""


def _go_to_group_list(page: Page, base_url: str, timeout: int = 20000) -> None:
    log.step("Opening the group list…")
    target = _origin(page.url) or _origin(base_url)
    page.goto(target + "/calificaciones/maestro", wait_until="domcontentloaded", timeout=timeout)
    try:
        page.wait_for_selector("#Loader", state="hidden", timeout=timeout)
    except PWTimeout:
        pass
    _close_popup(page)


def _near_label(page: Page, label_text: str):
    lab = page.locator(f"label:has-text('{label_text}')").first
    if lab.count():
        parent = lab.locator("xpath=ancestor::*[self::div or self::section][1]")
        return parent if parent.count() else lab
    return page.locator(f"xpath=//*[contains(normalize-space(.), '{label_text}')]").first


def _open_group(page: Page, materia: str, grupo: str, timeout: float = 10.0) -> bool:
    wanted_grupo = (grupo or "").strip().upper()
    wanted_materia = (materia or "").strip().upper()
    grupo_rx = re.compile(rf"(?<!\w){re.escape(wanted_grupo)}(?!\w)")
    log.step(f"Opening group {grupo} ({materia})…")

    def read_rows():
        try:
            return page.evaluate(
                "() => Array.from(document.querySelectorAll('table tr'))"
                ".map(tr => (tr.innerText || '').toUpperCase())"
            )
        except Exception:
            return []

    deadline = time.time() + timeout
    rows = read_rows()
    while time.time() < deadline and not any(grupo_rx.search(t) for t in rows):
        page.wait_for_timeout(200)
        rows = read_rows()

    target_idx = fallback_idx = None
    for i, txt in enumerate(rows):
        if not grupo_rx.search(txt):
            continue
        if not wanted_materia or wanted_materia in txt:
            target_idx = i; break
        if fallback_idx is None:
            fallback_idx = i
    if target_idx is None:
        target_idx = fallback_idx
    if target_idx is None:
        _shot(page, "sega_group_not_found")
        return False

    try:
        page.locator("table tr").nth(target_idx).click(timeout=5000)
    except Exception:
        return False

    # Grade-entry page is ready when the Evaluación picker (a <select>) is present.
    deadline = time.time() + 10
    while time.time() < deadline:
        cont = _near_label(page, "Evaluación")
        if cont.count() and cont.locator("select").count():
            _close_popup(page)
            return True
        page.wait_for_timeout(200)
    return False


def _select_evaluacion(page: Page, evaluacion: str) -> bool:
    log.step(f"Selecting evaluación · {evaluacion}…")
    container = _near_label(page, "Evaluación")
    if not container.count():
        return False
    sel = container.locator("select").first
    if not sel.count():
        return False
    try:
        sel.locator("option").first.wait_for(state="attached", timeout=6000)
    except Exception:
        pass
    wanted = evaluacion.strip().lower()
    opts = sel.locator("option")
    for i in range(opts.count()):
        t = (opts.nth(i).inner_text() or "").strip()
        if wanted in t.lower():
            sel.select_option(value=opts.nth(i).get_attribute("value") or t)
            # Let the (locked) table for this evaluación render.
            try:
                page.wait_for_selector("#Loader", state="hidden", timeout=8000)
            except PWTimeout:
                pass
            page.wait_for_timeout(800)
            return True
    return False


def _snapshot(page: Page) -> dict[str, dict]:
    """matrícula → {grade, source, row_text}. Reads a <select> when present (open
    grading) and falls back to a grade token in the row text (locked grading)."""
    try:
        rows = page.evaluate(r"""
            () => Array.from(document.querySelectorAll('table tr')).map(tr => {
                const text = (tr.innerText || '').replace(/\s+/g, ' ').trim();
                const m = text.match(/\b(\d{6,10})\b/);
                const sel = tr.querySelector('select');
                let sv = '';
                if (sel) {
                    sv = (sel.value || '').trim();
                    if (sel.selectedIndex >= 0 && sel.options[sel.selectedIndex]) {
                        const t = (sel.options[sel.selectedIndex].textContent || '').trim();
                        if (t) sv = t;
                    }
                }
                return { matricula: m ? m[1] : '', has_select: !!sel, sel_value: sv, row_text: text };
            }).filter(r => r.matricula);
        """)
    except Exception:
        return {}

    out: dict[str, dict] = {}
    for r in rows:
        grade, src = None, "none"
        if r["has_select"]:
            g = _GRADE_RX.search(r["sel_value"] or "")
            if g:
                grade, src = float(g.group(1)), "select"
        if grade is None:
            # Locked view: strip the matrícula, then take the first grade-looking token.
            tail = re.sub(r"\b\d{6,10}\b", "", r["row_text"])
            g = _GRADE_RX.search(tail)
            if g:
                grade, src = float(g.group(1)), "text"
        out[r["matricula"]] = {"grade": grade, "source": src, "row_text": r["row_text"]}
    return out


# ── public entry ──────────────────────────────────────────────────────────────
def dry_run_upload(group_code: str, partial_name: str, *, headless: bool = False) -> dict:
    """Read-only SEGA dry-run: diff SEGA's current values vs MUSAI's computed grades."""
    from sqlmodel import Session, select
    from musai.db import init_db, engine
    from musai.models import Course, Partial, PartialGrade, Student, Enrollment
    from musai.audit import log as audit_log

    from musai.semesters import course_for

    init_db()
    with Session(engine) as sess:
        # Active semester — uploading last semester's grades would be the worst kind of bug.
        course = course_for(sess, group_code)
        if course is None:
            raise RuntimeError(f"No course '{group_code}' in the current semester.")
        partial = sess.exec(
            select(Partial).where(Partial.course_id == course.id, Partial.name == partial_name)
        ).first()
        if partial is None:
            raise RuntimeError(f"No partial '{partial_name}' for course '{group_code}'.")

        names = {s.matricula: s.full_name for s in sess.exec(
            select(Student).join(Enrollment, Enrollment.student_id == Student.id)
            .where(Enrollment.course_id == course.id)).all()}
        computed: dict[str, float] = {}
        for pg in sess.exec(select(PartialGrade).where(PartialGrade.partial_id == partial.id)).all():
            st = sess.get(Student, pg.student_id)
            if st:
                computed[st.matricula] = pg.sega_value  # curved/overridden value that uploads

        evaluacion = partial.sega_evaluacion
        materia = MATERIA_BY_LEVEL.get(course.level, course.subject)
        env = course.moodle_env

    if not computed:
        raise RuntimeError(
            f"No computed PartialGrades for {group_code} / {partial_name}. "
            f"Map activities and recompute first."
        )

    user, pwd = settings.sega_username, settings.sega_password
    if not user or not pwd:
        raise RuntimeError("SEGA credentials missing (set UACH_/SEGA_ creds in MUSAI/.env).")

    base_url = settings.sega_base_url
    log.header(f"SEGA DRY-RUN · {group_code} · {evaluacion}  (read-only, no writes)")

    ensure_subprocess_capable_loop()  # see musai/automation/_loop.py
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_context().new_page()
        try:
            _login(page, base_url, user, pwd)
            _go_to_group_list(page, base_url)
            if not _open_group(page, materia, group_code):
                raise RuntimeError(f"Could not open group {group_code} in SEGA. Screenshot saved.")
            if not _select_evaluacion(page, evaluacion):
                _shot(page, "sega_eval_not_found")
                raise RuntimeError(f"Could not select evaluación '{evaluacion}'. Screenshot saved.")
            _shot(page, f"sega_{group_code}_{evaluacion.replace(' ', '_')}")
            sega = _snapshot(page)
        finally:
            page.context.close()
            browser.close()

    diff = _print_diff(group_code, evaluacion, computed, names, sega)

    with Session(engine) as sess:
        from musai.models import Course as _C
        from sqlmodel import select as _sel
        audit_log(sess, "sega_dryrun_live", actor="carlos",
                  target=f"group:{group_code} eval:{evaluacion}", env=env, dry_run=True,
                  detail={k: diff[k] for k in ("matches", "differs", "missing_in_sega",
                                               "extra_in_sega", "unreadable")})
        sess.commit()
    return diff


def _print_diff(group_code, evaluacion, computed, names, sega) -> dict:
    log.header(f"DIFF · {group_code} · {evaluacion}")
    print(f"  {'matrícula':<9} {'student':<26} {'SEGA now':>9} {'MUSAI':>7}  status")
    print(f"  {'-'*9} {'-'*26} {'-'*9} {'-'*7}  {'-'*22}")
    matches = differs = missing = unreadable = 0
    for mat in sorted(computed, key=lambda m: names.get(m, m)):
        musai_v = computed[mat]
        row = sega.get(mat)
        nm = (names.get(mat, mat) or mat)[:26]
        if row is None:
            missing += 1
            print(f"  {mat:<9} {nm:<26} {'—':>9} {musai_v:>7.1f}  ⚠ not in SEGA table")
            continue
        sv = row["grade"]
        if sv is None:
            unreadable += 1
            print(f"  {mat:<9} {nm:<26} {'(locked)':>9} {musai_v:>7.1f}  · couldn't read value")
        elif abs(sv - musai_v) <= 0.05:
            matches += 1
            print(f"  {mat:<9} {nm:<26} {sv:>9.1f} {musai_v:>7.1f}  ✓ match")
        else:
            differs += 1
            print(f"  {mat:<9} {nm:<26} {sv:>9.1f} {musai_v:>7.1f}  Δ {sv - musai_v:+.1f}")
    extra = sorted(set(sega) - set(computed))
    for mat in extra:
        print(f"  {mat:<9} {'(in SEGA, not in MUSAI)':<26} {str(sega[mat]['grade']):>9} {'—':>7}  ⚠ extra")
    total = len(computed)
    log.info(f"{total} MUSAI grades · ✓ {matches} match · Δ {differs} differ · "
             f"⚠ {missing} missing · {unreadable} unreadable · {len(extra)} extra in SEGA")
    log.warning("DRY-RUN — nothing was written to SEGA.")
    return {"matches": matches, "differs": differs, "missing_in_sega": missing,
            "extra_in_sega": len(extra), "unreadable": unreadable, "total": total}


def main() -> None:
    ap = argparse.ArgumentParser(description="SEGA dry-run (read-only): diff vs MUSAI grades.")
    ap.add_argument("--group", required=True, help="group_code, e.g. 1-LED-A")
    ap.add_argument("--partial", default="Parcial 1", help="partial name, e.g. 'Parcial 1'")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    try:
        dry_run_upload(args.group, args.partial, headless=args.headless)
    except Exception as e:
        log.error(f"SEGA dry-run failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
