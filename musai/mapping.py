"""Read a professor's live campusvirtual dashboard and turn it into `Course` rows.

The web equivalent of `python -m musai.new_semester --discover`, with two differences that
matter:

1. **It runs as the signed-in professor**, using their own stored Moodle password, so it maps
   *their* courses. The CLI has always used `.env`, i.e. The owner's account.
2. 🔴 **It is subject-agnostic.** `new_semester._TILE_RX` requires the literal `INGLES` and a
   roman numeral, which is correct for the seven English groups it was written for and useless
   for anyone else — a Nursing professor's tiles would all be "not recognized, skipping" and
   they would land on an empty dashboard with no explanation. The parser here reads the tile's
   *structure* (`… Ciclo: … Grupo: …`), which the portal renders identically for every faculty,
   and treats the subject as free text.

⚠️ The dashboard lists **only the courses that account is enrolled in** (COURSE_EDITING §7b,
measured with a control). That is the correct scope for this feature and also its hard limit:
there is no way to map a course you do not teach, and no reason to want one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from musai.models import Course, Partial, Professor

# The three partials an English course runs. Kept as the DEFAULT rather than the truth: a
# course in another faculty may evaluate differently, and `PLAN §6` already reads the scheme
# from a course's own `Partial` rows. Creating three is a starting point a professor can edit,
# not a claim about their syllabus.
DEFAULT_PARTIALS = [
    dict(name="Parcial 1", sega_evaluacion="PARCIAL 1"),
    dict(name="Parcial 2", sega_evaluacion="PARCIAL 2"),
    dict(name="Examen Final Ordinario", sega_evaluacion="EXAMEN FINAL ORDINARIO"),
]

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8,
         "IX": 9, "X": 10}

# The portal renders every tile as `<SUBJECT> Ciclo: <CYCLE> Grupo: <GROUP>`. Both labels are
# optional in this pattern so a tile missing one still yields a subject rather than nothing.
_CICLO = re.compile(r"\bCiclo\s*:", re.I)
_GRUPO = re.compile(r"\bGrupo\s*:", re.I)

# ⚠️ Longest-first, for the same reason `restore.subject_of` is: `I` is a prefix of `II`, `III`
# and `IV`, so `(I|II|III|IV)` matches the `I` inside `INGLES III` and reports level 1 for a
# third-level course. That mistake put an INGLES I backup one confirmation away from an
# INGLES III course once already.
_LEVEL_ROMAN = re.compile(r"\b(VIII|VII|III|IV|IX|VI|II|I|V|X)\b(?!\w)")


@dataclass
class Tile:
    """One parsed dashboard tile. `level` is 0 when nothing in the text implies one."""

    idc: str
    subject: str
    group_code: str
    level: int = 0
    cycle: Optional[str] = None
    server: Optional[str] = None
    raw: str = ""


def normalize_group_code(raw: str) -> str:
    """Moodle's tile label → MUSAI's group code, for the shapes where the rule is known.

    FCCF tiles read `1ED-A` while MUSAI and SEGA use `1-LED-A`; the rule is "insert `-L` after
    the leading level digit". ⚠️ That is an **FCCF licenciatura** convention, not a university
    one, so anything not matching `<digit><2 letters>-<letter>` is passed through untouched
    rather than mangled into a code SEGA has never heard of.
    """
    raw = (raw or "").strip().upper()
    m = re.fullmatch(r"(\d)([A-Z]{2})-([A-Z])", raw)
    return f"{m.group(1)}-L{m.group(2)}-{m.group(3)}" if m else raw


def _level_from(subject: str, group_code: str) -> int:
    """Roman numeral in the subject, else the leading digit of the group, else 0.

    0 means "this course does not state a level", which is a legitimate answer for most of the
    university. It must never be guessed as 1: `level` feeds nothing safety-critical today, but
    a fabricated 1 is indistinguishable from a real one to everything downstream.
    """
    m = _LEVEL_ROMAN.search((subject or "").upper())
    if m:
        return ROMAN.get(m.group(1), 0)
    m = re.match(r"^(\d)", (group_code or "").strip())
    return int(m.group(1)) if m else 0


def parse_tile(text: str, *, idc: str = "", server: str = "") -> Optional[Tile]:
    """One tile's text → a `Tile`, or `None` if it carries no course id.

    Subject-agnostic by construction: everything before `Ciclo:` is the subject, whatever it
    says. A tile with no `Grupo:` still parses — its group code becomes the subject's own text,
    which is wrong-looking on screen and therefore fixable, rather than silently dropped.
    """
    text = " ".join((text or "").split())
    if not idc:
        return None

    rest = text
    cycle = None
    group = ""

    gm = _GRUPO.search(rest)
    if gm:
        group = rest[gm.end():].strip()
        rest = rest[:gm.start()].strip()

    cm = _CICLO.search(rest)
    if cm:
        cycle = rest[cm.end():].strip() or None
        rest = rest[:cm.start()].strip()

    subject = rest.strip(" -·|") or (text[:60] if text else f"Curso {idc}")
    group_code = normalize_group_code(group) or subject[:24].strip().upper()
    return Tile(
        idc=str(idc).strip(),
        subject=subject,
        group_code=group_code,
        level=_level_from(subject, group),
        cycle=cycle,
        server=(server or "").strip().lower() or None,
        raw=text[:200],
    )


# ── planning, which is pure and therefore testable without a browser ──────────
@dataclass
class MappingPlan:
    """What a re-map would change. Rendered before anything is written."""

    new: list[Tile] = field(default_factory=list)
    updated: list[tuple[Tile, Course]] = field(default_factory=list)   # name/group drifted
    unchanged: list[tuple[Tile, Course]] = field(default_factory=list)
    vanished: list[Course] = field(default_factory=list)   # mapped before, not on the dashboard
    unparsed: list[str] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return len(self.new) + len(self.updated) + len(self.unchanged)


def plan_mapping(tiles: list[Tile], existing: list[Course]) -> MappingPlan:
    """Diff live tiles against what this professor already has in this semester. Pure.

    🔴 **`vanished` is reported and never acted on.** A course missing from today's dashboard
    is far more likely to be a portal hiccup, a slow SSO, or an enrolment being edited than a
    course that ceased to exist — and deleting a `Course` row takes its activities, grades and
    partial grades with it. Re-mapping is additive; removal stays a human decision.
    """
    by_idc = {str(c.moodle_course_id): c for c in existing if c.moodle_course_id}
    plan = MappingPlan()
    seen: set[str] = set()

    for t in tiles:
        seen.add(t.idc)
        course = by_idc.get(t.idc)
        if course is None:
            plan.new.append(t)
        elif (course.group_code != t.group_code or course.subject != t.subject
              or course.moodle_fullname != t.raw):
            plan.updated.append((t, course))
        else:
            plan.unchanged.append((t, course))

    plan.vanished = [c for idc, c in by_idc.items() if idc not in seen]
    return plan


def apply_mapping(sess: Session, plan: MappingPlan, *, professor_id: int,
                  semester_id: int) -> dict:
    """Write the plan. Creates courses (+ default partials) and refreshes drifted names.

    Never changes `professor_id` on an existing row: a course that somehow belongs to someone
    else must not be silently reassigned by whoever maps next.
    """
    now = datetime.utcnow()
    created = 0
    for t in plan.new:
        course = Course(
            professor_id=professor_id,
            semester_id=semester_id,
            subject=t.subject,
            level=t.level,
            group_code=t.group_code,
            moodle_course_id=t.idc,
            moodle_env="prod",
            sega_group_label=t.group_code,
            moodle_server=t.server,
            moodle_fullname=t.raw,
            cycle=t.cycle,
            mapped_at=now,
        )
        sess.add(course)
        sess.flush()
        for p in DEFAULT_PARTIALS:
            sess.add(Partial(course_id=course.id, **p))
        created += 1

    for t, course in plan.updated:
        course.subject = t.subject
        course.group_code = t.group_code
        course.moodle_fullname = t.raw
        course.cycle = t.cycle
        course.moodle_server = t.server or course.moodle_server
        course.level = t.level or course.level
        course.mapped_at = now
        sess.add(course)

    for _t, course in plan.unchanged:
        course.mapped_at = now
        sess.add(course)

    sess.commit()
    return {
        "created": created,
        "updated": len(plan.updated),
        "unchanged": len(plan.unchanged),
        "vanished": len(plan.vanished),
        "unparsed": len(plan.unparsed),
        "total_seen": plan.total_seen,
    }


# ── the browser half ──────────────────────────────────────────────────────────
def read_tiles(professor: Professor, *, headless: bool = True, on_step=None) -> list[Tile]:
    """Log in as this professor and read every course tile. Read-only — no writes anywhere.

    Raises `CredentialsMissing` when they have not stored a Moodle password, which the caller
    turns into "add it in Settings" rather than into a stack trace.
    """
    from playwright.sync_api import sync_playwright

    from musai.automation._log import logger as log
    from musai.automation._loop import ensure_subprocess_capable_loop
    from musai.automation.credentials import resolve_for_professor
    from musai.automation.moodle_export import _login_campusvirtual, _shot
    from musai.config import settings

    def step(msg: str) -> None:
        log.step(msg)
        if on_step:
            try:
                on_step(msg)
            except Exception:
                pass

    identity = resolve_for_professor(professor, system="moodle")
    step(f"Signing in to campusvirtual as {identity.username}…")

    ensure_subprocess_capable_loop()  # see musai/automation/_loop.py
    out: list[Tile] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            _login_campusvirtual(page, settings.moodle_base_url_prod,
                                 identity.username, identity.password)
            step("Signed in — reading the dashboard")
            tiles = page.locator("a.submit-info")
            n = tiles.count()
            step(f"{n} course tile(s) on the dashboard")
            for i in range(n):
                t = tiles.nth(i)
                try:
                    parsed = parse_tile(
                        t.inner_text() or "",
                        idc=(t.get_attribute("data-idc") or "").strip(),
                        server=(t.get_attribute("data-server") or "").strip(),
                    )
                except Exception:
                    continue
                if parsed:
                    out.append(parsed)
            return out
        except Exception:
            _shot(page, "map_courses_error")
            raise
        finally:
            for c in (ctx, browser):
                try:
                    c.close()
                except Exception:
                    pass
