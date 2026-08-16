"""Open a new semester — discover the groups from Moodle, create Course + Partial rows.

Replaces the hardcoded path in ``musai.seed`` (which only ever knew semester 2026-1 and
course 1-LED-A). Every semester the university issues NEW Moodle course shells with new
``idc``s for the same seven group slots, so onboarding is: read the tiles, derive the group
codes, create the rows.

Prints a plan and changes nothing unless ``--apply`` is passed.

    python -m musai.new_semester --discover --name 2026-2 \
        --starts 2026-08-10 --ends 2026-12-18
    python -m musai.new_semester --discover --name 2026-2 ... --apply

Rosters are pulled separately, per group, once the rows exist:

    python -m musai.automation.moodle_export --group 1-LED-A
"""

import argparse
import re
import sys
from datetime import date

from sqlmodel import Session, select

from musai.automation._log import logger as log
from musai.db import init_db, engine
from musai.models import Course, Partial, Semester

# The three partials every English course runs. `sega_date` is deliberately left unset —
# the SEGA capture windows differ each semester and inventing them would be a silent wrong.
PARTIALS = [
    dict(name="Parcial 1", sega_evaluacion="PARCIAL 1"),
    dict(name="Parcial 2", sega_evaluacion="PARCIAL 2"),
    dict(name="Examen Final Ordinario", sega_evaluacion="EXAMEN FINAL ORDINARIO"),
]

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}

# "INGLES I Ciclo: PRIMER SEMESTRE Grupo: 1ED-A"
_TILE_RX = re.compile(
    r"INGLES\s+(?P<roman>IV|III|II|I)\b.*?Grupo:\s*(?P<grupo>[0-9A-Z\-]+)",
    re.I | re.S,
)


def normalize_group_code(raw: str) -> str:
    """Moodle's tile label → MUSAI's group_code.

    The tiles read ``1ED-A`` / ``3MH-A``; MUSAI (and SEGA) use ``1-LED-A`` / ``3-LMH-A``.
    The rule is just "insert ``-L`` after the leading level digit". Anything that doesn't
    match that shape is passed through untouched rather than mangled.
    """
    raw = raw.strip().upper()
    m = re.fullmatch(r"(\d)([A-Z]{2})-([A-Z])", raw)
    if m:
        return f"{m.group(1)}-L{m.group(2)}-{m.group(3)}"
    return raw


def parse_tile(text: str) -> dict | None:
    """Pull (subject, level, group_code) out of one campusvirtual tile's text."""
    m = _TILE_RX.search(" ".join(text.split()))
    if not m:
        return None
    roman = m.group("roman").upper()
    level = ROMAN.get(roman)
    if not level:
        return None
    return {
        "subject": f"Inglés {roman}",
        "level": level,
        "group_code": normalize_group_code(m.group("grupo")),
    }


def discover() -> list[dict]:
    """Read the live course tiles and return one dict per recognized course."""
    from musai.automation.moodle_export import list_courses

    tiles = list_courses(headless=True)
    found, skipped = [], []
    for t in tiles:
        parsed = parse_tile(t.get("text", ""))
        if not parsed or not t.get("idc"):
            skipped.append(t.get("text", "?")[:70])
            continue
        parsed["moodle_course_id"] = str(t["idc"])
        parsed["tile"] = " ".join(t.get("text", "").split())[:70]
        found.append(parsed)
    for s in skipped:
        log.warning(f"Tile not recognized as an English course, skipping: {s}")
    return sorted(found, key=lambda c: c["group_code"])


def run(name: str, starts_on: date, ends_on: date, courses: list[dict], apply: bool) -> None:
    init_db()
    with Session(engine, expire_on_commit=False) as sess:
        existing = sess.exec(select(Semester).where(Semester.name == name)).first()
        others = [s for s in sess.exec(select(Semester)).all() if s.name != name]

        log.header(f"Open semester {name}  ({starts_on} → {ends_on})")
        if existing:
            log.info(f"Semester {name} already exists (id={existing.id}) — will reuse it.")
        else:
            log.step(f"CREATE Semester {name}")
        for o in others:
            if o.is_active:
                log.step(f"DEACTIVATE Semester {o.name} (is_active → False)")

        planned_new, planned_existing = [], []
        for c in courses:
            prior = sess.exec(
                select(Course).where(
                    Course.group_code == c["group_code"],
                    Course.semester_id == (existing.id if existing else -1),
                )
            ).first()
            (planned_existing if prior else planned_new).append(c)

        log.step(f"Courses ({len(planned_new)} new, {len(planned_existing)} already present)")
        for c in planned_new:
            log.info(f"CREATE  {c['group_code']:<10} {c['subject']:<12} idc={c['moodle_course_id']:<6} + 3 partials")
        for c in planned_existing:
            log.info(f"SKIP    {c['group_code']:<10} already in {name}")

        if not apply:
            log.warning("\nDRY RUN — nothing written. Re-run with --apply to commit.")
            return

        semester = existing
        if semester is None:
            semester = Semester(name=name, starts_on=starts_on, ends_on=ends_on, is_active=True)
            sess.add(semester)
            sess.flush()
        else:
            semester.starts_on, semester.ends_on, semester.is_active = starts_on, ends_on, True
            sess.add(semester)
        for o in others:
            if o.is_active:
                o.is_active = False
                sess.add(o)

        created = 0
        for c in planned_new:
            course = Course(
                semester_id=semester.id,
                subject=c["subject"],
                level=c["level"],
                group_code=c["group_code"],
                moodle_course_id=c["moodle_course_id"],
                moodle_env="prod",
                sega_group_label=c["group_code"],
            )
            sess.add(course)
            sess.flush()
            for p in PARTIALS:
                sess.add(Partial(course_id=course.id, **p))
            created += 1

        sess.commit()
        log.success(f"Semester {name} is open — {created} course(s) + {created * 3} partials created.")
        log.info("Next: pull rosters per group, e.g.")
        for c in courses[:2]:
            log.info(f"  python -m musai.automation.moodle_export --group {c['group_code']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Open a new semester from your live Moodle tiles.")
    ap.add_argument("--name", required=True, help="Semester name, e.g. 2026-2")
    ap.add_argument("--starts", required=True, help="First day, YYYY-MM-DD")
    ap.add_argument("--ends", required=True, help="Last day (inclusive), YYYY-MM-DD")
    ap.add_argument("--discover", action="store_true",
                    help="Read the live campusvirtual tiles to find groups + idcs")
    ap.add_argument("--apply", action="store_true", help="Actually write (default: dry run)")
    args = ap.parse_args()

    try:
        starts_on = date.fromisoformat(args.starts)
        ends_on = date.fromisoformat(args.ends)
    except ValueError as e:
        log.error(f"Bad date: {e}")
        sys.exit(1)
    if ends_on <= starts_on:
        log.error("--ends must be after --starts.")
        sys.exit(1)

    if not args.discover:
        log.error("--discover is required (there is no other source for the new idcs).")
        sys.exit(1)

    courses = discover()
    if not courses:
        log.error("No English course tiles found. Is the Moodle portal up?")
        sys.exit(1)

    run(args.name, starts_on, ends_on, courses, apply=args.apply)


if __name__ == "__main__":
    main()
