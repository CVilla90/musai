"""Set availability dates across a whole course, tab by tab.

    # read the course and print the plan — no browser writes, nothing saved
    .venv\\Scripts\\python -m musai.coursedates --group 1-LED-A \\
        --starts 2026-08-10 --ends 2026-11-22

    # the same, then fill every form and screenshot without saving (RAIL: the default)
    .venv\\Scripts\\python -m musai.coursedates --group 1-LED-A --dry-run-live

    # for real, once the plan above has been read
    .venv\\Scripts\\python -m musai.coursedates --group 1-LED-A --apply [--limit 1]

    # put back what a previous run replaced
    .venv\\Scripts\\python -m musai.coursedates --restore backups/dates_9023_… .json --apply

    # EXTEND parcial 2 by a week — the operation that actually repeats
    .venv\\Scripts\\python -m musai.coursedates --group 1-LED-A --shift-days 7 \\
        --shift-period 2 --apply

    # map the gradebook's activities onto the parciales, using the same tab map
    .venv\\Scripts\\python -m musai.coursedates --group 1-LED-A --map-activities [--apply]

`--snapshot`/`--from-snapshot` cache the course structure so the plan can be re-cut and
re-read for free; without them every tweak costs 14 page loads.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from musai.coursedates import discover, periods, tabmap
from musai.coursedates.apply import apply_plan
from musai.coursedates.plan import (
    ActivityPlan, CoursePlan, FieldChange, PLANNED, build_plan,
)
from musai.db import engine
from musai.models import Course
from musai.semesters import active_semester


def _resolve(group: Optional[str], idc: Optional[str]):
    """Return (idc, group_label). A group code is resolved in the CURRENT semester only —
    the university reissues the same codes every semester with new idcs."""
    if idc:
        return idc, group or idc
    if not group:
        raise SystemExit("Falta --group o --idc.")
    with Session(engine) as s:
        sem = active_semester(s)
        course = s.exec(select(Course).where(Course.group_code == group,
                                             Course.semester_id == sem.id)).first()
        if not course or not course.moodle_course_id:
            raise SystemExit(f"No encuentro el grupo {group} en el semestre {sem.name}.")
        return course.moodle_course_id, group


def _print_plan(plan: CoursePlan, full: bool) -> None:
    cal = plan.calendar
    if cal:
        print("\nCALENDARIO")
        for p in cal.periods:
            print(f"  {p.name}  {p.starts_on} → {p.ends_on}  ({p.weeks} sem)"
                  f"   examen desde {p.exam_opens_on}")
        mo, mc = cal.makeup_window()
        print(f"  Recuperación  {mo:%Y-%m-%d} → {mc:%Y-%m-%d}")
    for n in plan.notes:
        print(f"  ⚠ {n}")

    print("\nPLAN")
    rows = plan.activities if full else None
    if rows is None:
        seen, rows = set(), []
        for a in plan.activities:
            key = (a.section, a.status, a.modname,
                   tuple(c.describe() for c in a.changes))
            if key in seen:
                continue
            seen.add(key)
            rows.append(a)
    for a in rows:
        n = sum(1 for b in plan.activities
                if (b.section, b.status, b.modname,
                    tuple(c.describe() for c in b.changes))
                == (a.section, a.status, a.modname,
                    tuple(c.describe() for c in a.changes)))
        tag = "" if full else f"×{n}"
        what = " · ".join(c.describe() for c in a.changes) or a.note
        print(f"  §{a.section:<3} {(a.modname or '?'):<6} {tag:<5} "
              f"{(a.name[:28] if full else ''):<30} {a.status:<9} {what}")
    print(f"\n  {plan.summary()}")
    print(f"  {len(plan.writable)} actividades se escribirían.")


def _plan_from_backup(path: Path) -> CoursePlan:
    """Turn an undo file back into a plan. The backup was written in plan shape precisely so
    restoring needs no second code path."""
    data = json.loads(path.read_text(encoding="utf-8"))
    acts = []
    for a in data["activities"]:
        changes = [FieldChange(c["field"], bool(c["enable"]),
                               datetime.fromisoformat(c["when"]) if c.get("when") else None)
                   for c in a["changes"]]
        acts.append(ActivityPlan(cmid=a["cmid"], modname=a.get("modname"),
                                 name=a.get("name", ""), section=a.get("section", 0),
                                 tab_label="", status=PLANNED, changes=changes))
    return CoursePlan(idc=data["idc"], activities=acts,
                      notes=[f"Restauración de {path.name}"])


def _map_activities(idc, label, snap, tmap, *, apply, regrade, set_category) -> int:
    """`Activity.partial_id` from the same tab map. Writes to MUSAI's DB, never to Moodle."""
    from musai.coursedates import mapping

    with Session(engine) as sess:
        sem = active_semester(sess)
        course = sess.exec(select(Course).where(Course.moodle_course_id == str(idc),
                                                Course.semester_id == sem.id)).first()
        if course is None:
            raise SystemExit(f"{label} no está en MUSAI para el semestre {sem.name}.")
        rep = mapping.map_activities(sess, course, snapshot=snap, tab_map=tmap,
                                     apply=apply, regrade=regrade,
                                     set_category=set_category)

    print(f"\nMAPEO DE ACTIVIDADES — {label}"
          f"{'  (SIMULACRO)' if rep.dry_run else '  · ESCRITO'}")
    order = (mapping.MAPPED, mapping.CHANGED, mapping.ALREADY, mapping.REVIEW,
             mapping.AMBIGUOUS, mapping.UNMATCHED, mapping.SKIPPED)
    for status in order:
        rows = rep.by_status(status)
        if not rows:
            continue
        print(f"\n  {status.upper()} ({len(rows)})")
        for m in rows[:40]:
            where = f"§{m.section}" if m.section is not None else "  "
            print(f"    {where:<5} {m.name[:40]:<42} {m.partial_name[:22]:<24} {m.reason[:44]}")
    for n in rep.notes:
        print(f"\n  {n}")
    print(f"\n  {rep.written} actividad(es) escritas. "
          f"{'Añade --apply para guardar.' if rep.dry_run else ''}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="musai.coursedates")
    ap.add_argument("--group", help="p.ej. 1-LED-A")
    ap.add_argument("--idc", help="id de curso en Moodle, si ya lo sabes")
    ap.add_argument("--starts", default=None, help="AAAA-MM-DD (por omisión, el semestre)")
    ap.add_argument("--ends", default=None, help="AAAA-MM-DD, inclusive")
    ap.add_argument("--periods", type=int, default=periods.DEFAULT_PERIODS)
    ap.add_argument("--exam-days", type=int, default=periods.DEFAULT_EXAM_WINDOW_DAYS)
    ap.add_argument("--makeup-starts", default=None,
                    help="AAAA-MM-DD; fija la recuperación en vez de derivarla del calendario")
    ap.add_argument("--makeup-ends", default=None, help="AAAA-MM-DD, inclusive")
    ap.add_argument("--cutoff", action="store_true",
                    help="además de la fecha de entrega, cierra en firme las tareas")
    ap.add_argument("--snapshot", help="guarda la estructura leída en este archivo")
    ap.add_argument("--from-snapshot", help="usa una estructura ya leída (sin navegador)")
    ap.add_argument("--restore", help="archivo backups/dates_*.json a devolver")
    ap.add_argument("--shift-days", type=int, default=0,
                    help="recorre fechas N días (negativo = adelantar)")
    ap.add_argument("--shift-period", type=int, default=None,
                    help="qué parcial se extiende; sin él se recorre TODO el calendario")
    ap.add_argument("--map-activities", action="store_true",
                    help="asigna Activity.partial_id desde el mapa de pestañas (no toca Moodle)")
    ap.add_argument("--regrade", action="store_true",
                    help="permite MOVER una actividad que ya tenía otro parcial")
    ap.add_argument("--set-category", action="store_true",
                    help="además del parcial, escribe la categoría sugerida (cambia pesos)")
    ap.add_argument("--dry-run-live", action="store_true",
                    help="abre cada formulario y lo llena sin guardar")
    ap.add_argument("--apply", action="store_true", help="GUARDA de verdad")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--full", action="store_true", help="lista actividad por actividad")
    args = ap.parse_args(argv)

    if args.restore:
        plan = _plan_from_backup(Path(args.restore))
        idc, label = plan.idc, args.group or plan.idc
        print(f"RESTAURACIÓN — {len(plan.writable)} actividades de {args.restore}")
    else:
        idc, label = _resolve(args.group, args.idc)

        if args.from_snapshot:
            snap = json.loads(Path(args.from_snapshot).read_text(encoding="utf-8"))
        else:
            snap = discover.read_course_structure(idc, headless=not args.headed)
            if args.snapshot:
                Path(args.snapshot).write_text(
                    json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")

        starts, ends = args.starts, args.ends
        if not starts or not ends:
            with Session(engine) as s:
                sem = active_semester(s)
            starts = starts or sem.starts_on.isoformat()
            ends = ends or sem.ends_on.isoformat()
            print(f"⚠ Fechas tomadas del semestre {sem.name}: {starts} → {ends}. "
                  f"Son las fechas administrativas, no las de clase — pásalas con "
                  f"--starts/--ends si enseñas otro periodo.")

        cal = periods.split_periods(date.fromisoformat(starts), date.fromisoformat(ends),
                                    count=args.periods, exam_window_days=args.exam_days)
        if args.makeup_ends and not args.makeup_starts:
            raise SystemExit("🔴 --makeup-ends necesita --makeup-starts.")
        if args.makeup_starts:
            try:
                cal = periods.with_makeup(
                    cal, date.fromisoformat(args.makeup_starts),
                    date.fromisoformat(args.makeup_ends) if args.makeup_ends else None)
            except periods.PeriodError as e:
                raise SystemExit(f"🔴 {e}")
        if args.shift_days:
            # Print the BEFORE alongside the after. An extension is a change to dates the
            # professor already announced, so the diff is the thing worth reading, not the
            # result — "Parcial 2 termina el 18/10" means nothing without "…was the 11th".
            before = {p.index: (p.starts_on, p.ends_on, p.exam_opens_on) for p in cal.periods}
            try:
                cal = periods.shift(cal, args.shift_period, args.shift_days)
            except periods.PeriodError as e:
                raise SystemExit(f"🔴 {e}")
            scope = (f"parcial {args.shift_period}" if args.shift_period
                     else "todo el calendario")
            print(f"\nRECORRIDO — {scope}, {args.shift_days:+d} día(s)")
            for p in cal.periods:
                was = before[p.index]
                mark = "  " if was == (p.starts_on, p.ends_on, p.exam_opens_on) else "→ "
                print(f"  {mark}{p.name}  {was[0]}…{was[1]}  ⇒  {p.starts_on}…{p.ends_on}"
                      f"   examen desde {p.exam_opens_on} (antes {was[2]})")

        tabs = [{"section": s["section"], "label": s["name"], "hidden": s.get("hidden")}
                for s in snap["sections"]]
        tmap = tabmap.guess(tabs, periods=args.periods)

        if args.map_activities:
            return _map_activities(idc, label, snap, tmap, apply=args.apply,
                                   regrade=args.regrade, set_category=args.set_category)

        print(f"\nMAPA DE PESTAÑAS — {label} (idc {idc})")
        for r in tmap.rules:
            who = f"P{r.period} {r.slot}" if r.kind == tabmap.KIND_PERIOD else r.kind
            print(f"  §{r.section:<3} {r.label[:36]:<38} {who:<14} {r.reason}")
        if tmap.needs_review:
            print("  🔴 EL MAPA NECESITA REVISIÓN — no se reconocieron suficientes exámenes.")

        plan = build_plan(idc, snap["sections"], tmap, cal,
                          include_optional=("cutoffdate",) if args.cutoff else ())
        _print_plan(plan, args.full)

    if not (args.apply or args.dry_run_live):
        print("\n(Sólo el plan. Añade --dry-run-live para llenar los formularios sin "
              "guardar, o --apply para guardar.)")
        return 0

    out = apply_plan(idc=idc, plan=plan, dry_run=not args.apply,
                     headless=not args.headed, verify=not args.no_verify,
                     limit=args.limit, group_label=label)
    print(f"\n{'GUARDADO' if args.apply else 'SIMULACRO'} — "
          f"{out['written']} escritas · {out['unchanged']} sin cambio · "
          f"{out['failed']} con error")
    print(f"  deshacer: {out['backup']}")
    for f in out["failures"][:15]:
        print(f"  ✗ §{f.get('section')} {f.get('name', '')[:34]} — {f.get('error')}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
