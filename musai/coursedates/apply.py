"""Write a date plan into Moodle, one activity settings form at a time. LOCAL RUNNER ONLY.

RAIL: `dry_run=True` by default (CLAUDE.md). A dry run opens every form, reads what is there
today, fills the new values, reads them back in-page, screenshots — and never clicks save.

The path is the short one the course builder proved, not the human click-path (tab → gear →
per-activity action menu → *Editar ajustes*):

    GET /course/modedit.php?update=<cmid>     ← the whole settings form, any module type

Five things here are load-bearing, and each has a scar behind it:

1. **The form is checked against the cmid AND the module type before anything is typed.**
   Targeting by payload, never by position — the rule that came out of the delete-confirm page
   whose first submit button was the course search box.
2. **Fields are named one by one.** A quiz form carries `attemptopen`, `marksclosed`,
   `rightansweropen` — those are *review options*, not dates. Anything matching `*open` by
   pattern would rewrite what students see after an attempt.
3. **The enable checkbox is CLICKED, never assigned.** Moodle disables the day/month/year
   selects while the box is unchecked, and a disabled select is not submitted. Setting
   `.checked = true` in JS skips Moodle's own handler, leaves the selects disabled, and the
   date silently does not save — a success-shaped failure, the exact shape that reported
   "Moodle finished the restore" for a restore that never ran.
4. **The undo file is written before the write it undoes.** Current values are flushed to
   `backups/` after every read, so a crash halfway still leaves a complete record of
   everything already changed. The file is itself a valid plan, so restoring is re-applying.
5. **Read-back twice.** In-page (free) proves the select took the value; after saving
   (`verify=True`) proves Moodle kept it. The first catches our bug, the second catches
   Moodle's.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from playwright.sync_api import sync_playwright

from musai.automation._log import describe_exception, logger as log
from musai.automation._loop import ensure_subprocess_capable_loop
from musai.config import settings
from musai.coursebuild.publish import enter_course
from musai.coursedates.plan import (
    ALLOWED_FIELD_NAMES, ActivityPlan, CoursePlan, carry_forward,
)

BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "backups"
SHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots"

# Identify the form before touching it. `update` is the cmid Moodle thinks it is editing;
# `modulename` is the type. If either disagrees with the plan, this is not our activity.
_FORM_IDENTITY_JS = """
() => ({
  update: (document.querySelector('input[name="update"]') || {}).value || null,
  modulename: (document.querySelector('input[name="modulename"]') || {}).value || null,
  has_save: !!document.getElementById('id_submitbutton2'),
  title: (document.querySelector('#id_name') || {}).value || null,
})
"""

_READ_FIELD_JS = """
(field) => {
  const g = (k) => document.getElementById('id_' + field + '_' + k);
  if (!g('day')) return null;                       // this type has no such field
  const en = g('enabled');
  const num = (k) => { const el = g(k); return el ? parseInt(el.value, 10) : null; };
  const opts = (k) => {
    const el = g(k);
    return el ? Array.from(el.options).map(o => parseInt(o.value, 10))
                     .filter(v => !isNaN(v)) : [];
  };
  return {
    enabled: en ? !!en.checked : true,              // no checkbox = always on
    has_toggle: !!en,
    day: num('day'), month: num('month'), year: num('year'),
    hour: num('hour'), minute: num('minute'),
    // What this particular form CAN hold. Needed before comparing, not only before writing:
    // an assignment cannot store 23:59, so comparing the stored 23:55 against the intended
    // 23:59 would mark it dirty on every single run and it would never settle.
    opts: {hour: opts('hour'), minute: opts('minute')},
  };
}
"""

# The enable box is CLICKED so Moodle's own change handler runs and un-disables the selects.
# Assigning `.checked` leaves them disabled, and disabled inputs are never submitted.
#
# 🔴 The requested value may not be on the menu. Measured 2026-08-07: an **assignment's**
# minute select steps by 5 (`0,5,…,55`) while a **quiz's** offers all 60, so a close time of
# 23:59 is valid on one form and impossible on the other. The value is therefore snapped to
# the nearest option BELOW it — never above, because rounding a 23:59 close upward moves the
# deadline into the next day. What was actually applied comes back in `applied` so the caller
# compares the read-back against reality rather than against its own intention.
_WRITE_FIELD_JS = """
({field, enable, parts}) => {
  const g = (k) => document.getElementById('id_' + field + '_' + k);
  if (!g('day')) return {status: 'missing'};
  const en = g('enabled');
  if (en) {
    if (en.checked !== enable) { en.click(); }
  } else if (!enable) {
    return {status: 'no-toggle'};                   // cannot switch off what has no switch
  }
  if (!enable) return {status: 'disabled'};
  const applied = {};
  for (const k of ['day', 'month', 'year', 'hour', 'minute']) {
    const el = g(k);
    if (!el) return {status: 'missing:' + k};
    if (el.disabled) return {status: 'still-disabled:' + k};
    const want = parts[k];
    const opts = Array.from(el.options || [])
                      .map(o => parseInt(o.value, 10)).filter(v => !isNaN(v));
    let use = want;
    if (opts.length && opts.indexOf(want) === -1) {
      const lower = opts.filter(v => v < want);
      if (!lower.length) return {status: 'rejected:' + k, options: opts.slice(0, 12)};
      use = Math.max.apply(null, lower);
    }
    el.value = String(use);
    el.dispatchEvent(new Event('change', {bubbles: true}));
    if (String(el.value) !== String(use)) return {status: 'rejected:' + k};
    applied[k] = use;
  }
  return {status: 'set', applied: applied};
}
"""


# A successful save leaves modedit.php for the course page. Still being on the form means
# Moodle REFUSED it — and the click navigated, so nothing else here would notice.
_SAVE_REFUSED_JS = """
() => {
  if (!/modedit\\.php/.test(location.pathname)) return null;
  const msgs = new Set();
  document.querySelectorAll('.error, .invalid-feedback, [id^="id_error_"], .alert-danger')
    .forEach(e => { const t = (e.textContent || '').trim(); if (t) msgs.add(t); });
  return Array.from(msgs).slice(0, 4);
}
"""


def _parts(when: datetime) -> Dict[str, int]:
    return {"day": when.day, "month": when.month, "year": when.year,
            "hour": when.hour, "minute": when.minute}


def _applied(outcome: dict) -> Optional[datetime]:
    """The datetime the form actually holds after a write, per its own selects."""
    a = (outcome or {}).get("applied") or {}
    try:
        return datetime(a["year"], a["month"], a["day"], a["hour"], a["minute"])
    except (TypeError, ValueError, KeyError):
        return None


def _as_datetime(raw: Optional[dict]) -> Optional[datetime]:
    if not raw or not raw.get("enabled"):
        return None
    try:
        return datetime(raw["year"], raw["month"], raw["day"], raw["hour"], raw["minute"])
    except (TypeError, ValueError, KeyError):
        return None


def _snap(raw: Optional[dict], when: Optional[datetime]) -> Optional[datetime]:
    """The closest value at-or-below `when` that this form's own selects can hold.

    Applied to the INTENT before comparing, so "what we want" and "what is stored" are
    expressed in the same vocabulary. Without it an assignment — whose minute select steps by
    5 — is dirty forever against a 23:59 close, and `--apply` never reaches a steady state.
    """
    if not raw or not when:
        return when
    opts = raw.get("opts") or {}

    def down(values, want: int) -> int:
        lower = [v for v in (values or []) if v <= want]
        return max(lower) if lower else want

    return when.replace(hour=down(opts.get("hour"), when.hour),
                        minute=down(opts.get("minute"), when.minute))


def _same(raw: Optional[dict], enable: bool, when: Optional[datetime]) -> bool:
    """True when the form already says exactly what the plan wants."""
    if raw is None:
        return False
    if not enable:
        return not raw.get("enabled", False)
    return raw.get("enabled", False) and _as_datetime(raw) == when


def _undo_entry(act: ActivityPlan, before: Dict[str, Optional[dict]]) -> dict:
    """The prior state, shaped as a plan so `--restore` is just another apply."""
    changes = []
    for fieldname, raw in before.items():
        if raw is None:
            continue
        was = _as_datetime(raw)
        changes.append({"field": fieldname, "enable": bool(raw.get("enabled")),
                        "when": was.isoformat() if was else None})
    return {"cmid": act.cmid, "modname": act.modname, "name": act.name,
            "section": act.section, "changes": changes}


def apply_plan(
    *,
    idc: str,
    plan: CoursePlan,
    dry_run: bool = True,
    headless: bool = True,
    verify: bool = True,
    limit: Optional[int] = None,
    group_label: str = "",
    as_user: Optional[str] = None,
    identity=None,
    on_step: Optional[Callable[[str], None]] = None,
) -> dict:
    """Apply `plan` to the live course. Returns a result dict; writes nothing when dry.

    🔴 `as_user` (2026-08-13) writes dates into **another professor's** course. Yesterday this
    module deliberately had no such parameter, and a test pinned the absence: *reading* a
    colleague's course was allowed, *writing a date into it* was not. That was the right default
    with no instruction on the table. English IV put an instruction on the table — the owner owns
    no course at this level, so *"Dates, Etc."* is unsatisfiable without it.

    ⚠️ The rail that actually protected anything was never the missing parameter; it was the
    absence of a **CLI flag**, so no stray command line could write a date into someone else's
    course by accident. That is unchanged: `musai.coursedates.__main__` still has no
    `--as-user`, and a test still pins that. Only a script that names the professor can do this.
    """

    steps: List[str] = []

    def step(msg: str) -> None:
        steps.append(msg)
        log.step(msg)
        if on_step:
            on_step(msg)

    targets = plan.writable[:limit] if limit else plan.writable
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"dates_{idc}_{stamp}.json"

    result = {
        "ok": False, "dry_run": dry_run, "idc": idc,
        "targets": len(targets), "written": 0, "unchanged": 0, "failed": 0, "snapped": 0, "carried": 0,
        "backup": str(backup_path), "steps": steps, "failures": [], "changes": [],
    }
    checks: List[dict] = []      # what to re-read after saving, with EFFECTIVE values
    if not targets:
        result["ok"] = True
        step("El plan no cambia ninguna actividad.")
        return result

    # Every field the plan wants to touch must be on the allow list. This is the last gate
    # before a browser exists, so a bad plan can never reach a form.
    stray = {c.field for a in targets for c in a.changes} - ALLOWED_FIELD_NAMES
    if stray:
        raise RuntimeError(f"El plan quiere escribir campos no permitidos: {sorted(stray)}")

    undo: List[dict] = []

    def flush_undo() -> None:
        backup_path.write_text(json.dumps(
            {"idc": idc, "created_at": stamp, "dry_run": dry_run, "activities": undo},
            indent=2, ensure_ascii=False), encoding="utf-8")

    ensure_subprocess_capable_loop()
    # Cleanup belongs INSIDE `with sync_playwright()`. An outer `finally` runs after playwright
    # has stopped, and `ctx.close()` then raises "Event loop is closed" on top of the real
    # error — the failure gets replaced by a misleading one on its way out.
    with sync_playwright() as p:
        step("Abriendo el navegador…")
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        try:
            page = ctx.new_page()
            vpage, host = enter_course(ctx, page, idc, as_user=as_user, identity=identity)
            step(f"Curso abierto ({len(targets)} actividades por escribir).")

            for i, act in enumerate(targets, 1):
                label = f"[{i}/{len(targets)}] §{act.section} {act.name[:38]}"
                try:
                    vpage.goto(f"https://{host}/course/modedit.php?update={act.cmid}",
                               wait_until="domcontentloaded", timeout=60000)
                    vpage.wait_for_load_state("networkidle", timeout=25000)
                    # Moodle ships the time section collapsed; the fields must exist in the DOM.
                    vpage.evaluate("""() => document.querySelectorAll('[aria-expanded=false]')
                        .forEach(e => { try { e.click(); } catch (x) {} })""")
                    vpage.wait_for_timeout(400)

                    ident = vpage.evaluate(_FORM_IDENTITY_JS)
                    if str(ident.get("update") or "") != str(act.cmid):
                        raise RuntimeError(
                            f"El formulario dice update={ident.get('update')!r} y esperábamos "
                            f"{act.cmid!r}.")
                    if act.modname and ident.get("modulename") \
                            and ident["modulename"] != act.modname:
                        raise RuntimeError(
                            f"Tipo distinto: el formulario es «{ident['modulename']}» y el "
                            f"plan dice «{act.modname}».")
                    if not ident.get("has_save"):
                        raise RuntimeError("No hay botón de guardar en este formulario.")

                    # Dependents are read too, even though the plan does not manage them:
                    # this run may still have to move one, and an undo file missing it could
                    # not put it back.
                    fields_to_read = [c.field for c in act.changes] + list(act.dependents)
                    before = {f: vpage.evaluate(_READ_FIELD_JS, f) for f in fields_to_read}
                    undo.append(_undo_entry(act, before))
                    flush_undo()          # the undo file never lags the writes

                    # The target is the intent expressed in what THIS form can hold.
                    wanted = {c.field: _snap(before.get(c.field), c.when) for c in act.changes}
                    pending = [c for c in act.changes
                               if not _same(before.get(c.field), c.enable, wanted[c.field])]
                    if not pending:
                        result["unchanged"] += 1
                        step(f"{label} — ya estaba correcto.")
                        continue

                    # `effective` is what the form could actually hold, which is not always
                    # what was asked for — see the 5-minute step on assignment forms.
                    effective: Dict[str, Optional[datetime]] = {}
                    for c in pending:
                        outcome = vpage.evaluate(_WRITE_FIELD_JS, {
                            "field": c.field, "enable": c.enable,
                            "parts": _parts(c.when) if c.when else {},
                        })
                        status = (outcome or {}).get("status")
                        if status not in ("set", "disabled"):
                            extra = (outcome or {}).get("options")
                            raise RuntimeError(
                                f"{c.field}: {status}"
                                + (f" (el formulario ofrece {extra})" if extra else ""))
                        got = _applied(outcome) if status == "set" else None
                        effective[c.field] = got
                        if got and c.when and got != c.when:
                            step(f"{label} — {c.field} ajustado a {got:%H:%M} "
                                 f"(el formulario no acepta {c.when:%H:%M}).")
                            result["snapped"] += 1

                    # A date this plan does not manage can still make the form illegal:
                    # Moodle requires cutoffdate >= duedate. Carry such a field forward with
                    # the gap the professor originally chose, rather than leave the activity
                    # unwritable or silently drop their grace period.
                    for dep in act.dependents:
                        was = _as_datetime(before.get(dep))
                        if not was or not act.close_at or was >= act.close_at:
                            continue
                        moved = carry_forward(_as_datetime(before.get(act.close_field)),
                                              was, act.close_at)
                        if moved is None:
                            raise RuntimeError(
                                f"{dep} está en {was:%d/%m/%Y} y quedaría antes del nuevo "
                                f"cierre, pero no había fecha previa para calcular el margen. "
                                f"Ajústalo a mano o usa --cutoff.")
                        outcome = vpage.evaluate(_WRITE_FIELD_JS, {
                            "field": dep, "enable": True, "parts": _parts(moved)})
                        if (outcome or {}).get("status") != "set":
                            raise RuntimeError(f"{dep}: {(outcome or {}).get('status')}")
                        got = _applied(outcome)
                        effective[dep] = got
                        result["carried"] += 1
                        step(f"{label} — {dep} movido a {got:%d/%m/%Y} para conservar el "
                             f"margen que ya tenía.")
                        checks.append({"cmid": act.cmid, "name": act.name,
                                       "section": act.section, "field": dep,
                                       "enable": True, "when": got})

                    # In-page read-back: proves the selects took the value before we save.
                    for c in pending:
                        after = vpage.evaluate(_READ_FIELD_JS, c.field)
                        if not _same(after, c.enable, effective.get(c.field)):
                            raise RuntimeError(
                                f"{c.field} no quedó como se pidió (quedó {after!r}).")
                        checks.append({"cmid": act.cmid, "name": act.name,
                                       "section": act.section, "field": c.field,
                                       "enable": c.enable, "when": effective.get(c.field)})

                    result["changes"].append({
                        "cmid": act.cmid, "name": act.name, "section": act.section,
                        "before": {k: (_as_datetime(v).isoformat()
                                       if _as_datetime(v) else None)
                                   for k, v in before.items()},
                        "after": {c.field: (effective[c.field].isoformat()
                                            if effective.get(c.field) else None)
                                  for c in pending},
                    })

                    if dry_run:
                        if i == 1:
                            shot = SHOT_DIR / f"dates_dryrun_{group_label or idc}_{stamp}.png"
                            vpage.screenshot(path=str(shot), full_page=False)
                            result["screenshot"] = str(shot)
                        step(f"{label} — SIMULACRO, no se guardó.")
                        continue

                    vpage.click("#id_submitbutton2", timeout=30000)
                    vpage.wait_for_load_state("networkidle", timeout=45000)
                    refused = vpage.evaluate(_SAVE_REFUSED_JS)
                    if refused is not None:
                        raise RuntimeError(
                            "Moodle rechazó el formulario"
                            + (f": {' / '.join(refused)}" if refused else
                               " (sigue en la página de ajustes, sin mensaje visible)."))
                    result["written"] += 1
                    step(f"{label} — guardado.")

                except Exception as exc:  # one bad activity must not abort the other 53
                    result["failed"] += 1
                    result["failures"].append({
                        "cmid": act.cmid, "name": act.name, "section": act.section,
                        "error": describe_exception(exc)})
                    step(f"{label} — ERROR: {describe_exception(exc)}")

            if verify and not dry_run and checks:
                step("Verificando lo guardado…")
                bad = _verify(vpage, host, checks, step)
                result["verified"] = len(checks) - len(bad)
                result["mismatched"] = bad
                for m in bad:
                    result["failures"].append({**m, "error": "no coincide tras guardar"})

            flush_undo()
            result["ok"] = result["failed"] == 0 and not result.get("mismatched")
            step(f"Listo: {result['written']} escritas · {result['unchanged']} sin cambio · "
                 f"{result['failed']} con error"
                 + (" (SIMULACRO)" if dry_run else ""))
            return result
        finally:
            ctx.close()
            browser.close()


def _verify(vpage, host: str, checks: List[dict], step) -> List[dict]:
    """Re-open every form that was written and confirm Moodle kept the values.

    The in-page read-back already proved *we* set the selects; this proves Moodle did not
    normalise, clamp or drop them. Two separate claims, two separate checks — the project has
    been burnt once by treating "we sent it" as "it happened".

    `checks` carries the EFFECTIVE datetime (post-snap), so a field the form rounded is
    verified against what it could hold, not against what was asked for.
    """
    bad: List[dict] = []
    by_cmid: Dict[str, List[dict]] = {}
    for c in checks:
        by_cmid.setdefault(c["cmid"], []).append(c)

    for cmid, group in by_cmid.items():
        try:
            vpage.goto(f"https://{host}/course/modedit.php?update={cmid}",
                       wait_until="domcontentloaded", timeout=60000)
            vpage.wait_for_load_state("networkidle", timeout=25000)
            vpage.evaluate("""() => document.querySelectorAll('[aria-expanded=false]')
                .forEach(e => { try { e.click(); } catch (x) {} })""")
            vpage.wait_for_timeout(300)
            for c in group:
                raw = vpage.evaluate(_READ_FIELD_JS, c["field"])
                if not _same(raw, c["enable"], c["when"]):
                    bad.append({"cmid": cmid, "name": c["name"], "section": c["section"],
                                "field": c["field"], "found": str(_as_datetime(raw))})
        except Exception as exc:
            bad.append({"cmid": cmid, "name": group[0]["name"],
                        "section": group[0]["section"], "field": "?",
                        "found": describe_exception(exc)})
    if bad:
        step(f"⚠ {len(bad)} campo(s) no coinciden después de guardar.")
    return bad
