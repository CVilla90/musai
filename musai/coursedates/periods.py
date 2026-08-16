"""Split a teaching window into equal periods of whole weeks. Pure arithmetic.

The owner's own rule, and the defaults are biased to it (`PLAN`/HANDOFF, 2026-08-07):

    "I personally like to focus my course dates in three main periods: 1st, 2nd & 3rd.
     Equally distributed in the amount of weeks Monday through Sunday."

Two rules that look like details and are not:

**Leftover days are REPORTED, never absorbed.** 2026-08-10 → 2026-11-23 is 106 days = 15 weeks
and one day. Silently stretching the last period by a day moves a real deadline for real
students; silently dropping it hides a day the professor may have meant to use. So the split
uses whole weeks and hands back a note naming the leftover. (the owner chose to end on Sunday
2026-11-22 and the spare Monday became the make-up window's first day — see `makeup_window`.)

**The teaching window is not the semester row.** `Semester.ends_on` for 2026-2 is the
university's administrative date (2026-12-18); the owner stops teaching 2026-11-23. Passing the
administrative date in here would push every deadline a month late, so the caller supplies the
teaching window explicitly and `Semester` is only ever a *default*.

Times are the professor-facing convention, not UTC: a period opens at **00:00** on its first
day and closes at **23:59** on its last. A close date of "00:00 on the 22nd" means the 21st to
every student who reads it, which is the kind of off-by-one nobody notices until a grade is
disputed.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

WEEK = 7
DAY_NAMES_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

OPEN_TIME = time(0, 0)
CLOSE_TIME = time(23, 59)

DEFAULT_PERIODS = 3
DEFAULT_EXAM_WINDOW_DAYS = 7      # the last week of the period, the owner 2026-08-07
DEFAULT_MAKEUP_WINDOW_DAYS = 7


class PeriodError(ValueError):
    """The requested split cannot be made — raised instead of returning a wrong calendar."""


@dataclass(frozen=True)
class Period:
    """One partial. `ends_on` is INCLUSIVE — it is the last day students can work."""

    index: int                 # 1-based, matches "Parcial 1"
    name: str
    starts_on: date
    ends_on: date
    exam_opens_on: date

    @property
    def weeks(self) -> int:
        return ((self.ends_on - self.starts_on).days + 1) // WEEK

    @property
    def days(self) -> int:
        return (self.ends_on - self.starts_on).days + 1

    def content_window(self) -> Tuple[datetime, datetime]:
        """When ordinary material is available: the whole period."""
        return (datetime.combine(self.starts_on, OPEN_TIME),
                datetime.combine(self.ends_on, CLOSE_TIME))

    def exam_window(self) -> Tuple[datetime, datetime]:
        """When the period's exam is available: its last `exam_window_days`."""
        return (datetime.combine(self.exam_opens_on, OPEN_TIME),
                datetime.combine(self.ends_on, CLOSE_TIME))


@dataclass(frozen=True)
class Calendar:
    """The whole teaching calendar: the periods plus everything the UI must say out loud."""

    periods: List[Period]
    starts_on: date
    ends_on: date               # the last day actually covered (may be < the requested end)
    requested_ends_on: date
    leftover_days: int
    notes: List[str]
    # An explicitly chosen make-up window. `None` means "derive it", which is the default
    # below; a date here is the professor's decision and is never recomputed from the periods.
    makeup_starts_on: Optional[date] = None
    makeup_ends_on: Optional[date] = None

    @property
    def total_weeks(self) -> int:
        return sum(p.weeks for p in self.periods)

    def period(self, index: int) -> Period:
        for p in self.periods:
            if p.index == index:
                return p
        raise PeriodError(f"No period {index} (have 1..{len(self.periods)}).")

    def makeup_window(self, days: int = DEFAULT_MAKEUP_WINDOW_DAYS
                      ) -> Tuple[datetime, datetime]:
        """The make-up exam sits AFTER the last period — the owner: *"at the very end of the
        course, even after exam 3."*

        By default it starts the day after teaching ends, which is exactly where the leftover
        day goes when the teaching window is not a whole number of weeks.

        ⚠️ The derived window is only ever a **suggestion**. The real one is an administrative
        date the faculty sets, and for 2026-2 the owner chose 2026-11-30 → 2026-12-13 — a week
        *after* teaching ends, open for two weeks — which no arithmetic over the teaching
        window could have produced. `with_makeup()` records that choice; once recorded it wins,
        and `days` is ignored.
        """
        if self.makeup_starts_on is not None:
            end = self.makeup_ends_on or (self.makeup_starts_on
                                          + timedelta(days=days - 1))
            return (datetime.combine(self.makeup_starts_on, OPEN_TIME),
                    datetime.combine(end, CLOSE_TIME))
        start = self.ends_on + timedelta(days=1)
        return (datetime.combine(start, OPEN_TIME),
                datetime.combine(start + timedelta(days=days - 1), CLOSE_TIME))


def split_periods(
    starts_on: date,
    ends_on: date,
    count: int = DEFAULT_PERIODS,
    exam_window_days: int = DEFAULT_EXAM_WINDOW_DAYS,
) -> Calendar:
    """Divide [starts_on, ends_on] into `count` periods of whole weeks.

    `ends_on` is inclusive. Weeks are anchored to `starts_on`'s weekday, so a Monday start
    gives the Monday-to-Sunday weeks the owner wants; any other start day is called out in the
    notes rather than quietly re-anchored.
    """
    if count < 1:
        raise PeriodError("A course needs at least one period.")
    if ends_on < starts_on:
        raise PeriodError(f"The course ends ({ends_on}) before it starts ({starts_on}).")
    if exam_window_days < 1:
        raise PeriodError("The exam window must be at least one day.")

    notes: List[str] = []
    total_days = (ends_on - starts_on).days + 1
    whole_weeks = total_days // WEEK
    leftover = total_days - whole_weeks * WEEK

    if whole_weeks < count:
        raise PeriodError(
            f"{total_days} días ({whole_weeks} semanas completas) no alcanzan para {count} "
            f"periodos de al menos una semana."
        )

    if starts_on.weekday() != 0:
        notes.append(
            f"El curso empieza en {DAY_NAMES_ES[starts_on.weekday()]}, no en lunes: las "
            f"semanas corren de {DAY_NAMES_ES[starts_on.weekday()]} a "
            f"{DAY_NAMES_ES[(starts_on.weekday() - 1) % 7]}."
        )

    base, extra = divmod(whole_weeks, count)
    if extra:
        notes.append(
            f"{whole_weeks} semanas no se dividen en {count} partes iguales: "
            f"los primeros {extra} periodo(s) llevan una semana más."
        )

    periods: List[Period] = []
    cursor = starts_on
    for i in range(count):
        weeks = base + (1 if i < extra else 0)
        p_end = cursor + timedelta(days=WEEK * weeks - 1)
        window = min(exam_window_days, WEEK * weeks)
        if window < exam_window_days:
            notes.append(
                f"Parcial {i + 1} dura {weeks} semana(s); la ventana de examen se recorta a "
                f"{window} día(s)."
            )
        periods.append(Period(
            index=i + 1,
            name=f"Parcial {i + 1}",
            starts_on=cursor,
            ends_on=p_end,
            exam_opens_on=p_end - timedelta(days=window - 1),
        ))
        cursor = p_end + timedelta(days=1)

    covered_end = periods[-1].ends_on
    if leftover:
        notes.append(
            f"Sobran {leftover} día(s): el curso queda cubierto hasta el "
            f"{covered_end.isoformat()} ({DAY_NAMES_ES[covered_end.weekday()]}) y no hasta el "
            f"{ends_on.isoformat()}. Ese tiempo queda libre para el examen de recuperación."
        )

    return Calendar(
        periods=periods,
        starts_on=starts_on,
        ends_on=covered_end,
        requested_ends_on=ends_on,
        leftover_days=leftover,
        notes=notes,
    )


def with_makeup(calendar: Calendar, starts_on: date,
                ends_on: Optional[date] = None) -> Calendar:
    """Pin the make-up window to dates the faculty chose, instead of deriving it.

    The gap between the end of teaching and the start of the make-up window is **reported, not
    absorbed** — the same rule this module applies to leftover days. A professor who sees
    *"queda una semana de hueco"* can decide it is deliberate; a professor who sees nothing
    cannot.

    Raises rather than returning a wrong calendar when the window would open while the course
    is still running: a recuperación that opens before Exam 3 closes is a student sitting the
    make-up for a partial they have not failed yet.
    """
    if ends_on is not None and ends_on < starts_on:
        raise PeriodError(
            f"La ventana de recuperación termina ({ends_on}) antes de empezar ({starts_on}).")
    if starts_on <= calendar.ends_on:
        raise PeriodError(
            f"La recuperación abriría el {starts_on}, pero las clases terminan el "
            f"{calendar.ends_on}. Debe abrir después del último parcial.")

    gap = (starts_on - calendar.ends_on).days - 1
    notes = list(calendar.notes)
    if gap > 0:
        notes.append(
            f"Recuperación fijada a mano: {starts_on.isoformat()} → "
            f"{(ends_on or starts_on).isoformat()}. Quedan {gap} día(s) de hueco entre el fin "
            f"de clases ({calendar.ends_on.isoformat()}) y la apertura.")
    else:
        notes.append(f"Recuperación fijada a mano: {starts_on.isoformat()} → "
                     f"{(ends_on or starts_on).isoformat()}.")

    return Calendar(
        periods=calendar.periods,
        starts_on=calendar.starts_on,
        ends_on=calendar.ends_on,
        requested_ends_on=calendar.requested_ends_on,
        leftover_days=calendar.leftover_days,
        notes=notes,
        makeup_starts_on=starts_on,
        makeup_ends_on=ends_on,
    )


def shift(calendar: Calendar, period_index: Optional[int], days: int) -> Calendar:
    """Move dates later (or earlier) — the operation the owner actually repeats.

        "some students will ask for an extension, so I would have to go back and extend the
         whole thing, every tab, every quiz, assignment…"

    `period_index=None` shifts the whole calendar; a number shifts that period's END only,
    which is what an extension really is: the deadline moves, the start does not, and every
    later period slides to stay contiguous.

    Returns a NEW Calendar — the original is kept so a diff can be shown before writing.
    """
    if days == 0:
        return calendar
    delta = timedelta(days=days)

    def makeup_note(new_ends_on: date) -> List[str]:
        """A PINNED make-up window does not slide with an extension.

        It is an administrative date the faculty published, not a consequence of the teaching
        window — so an extension must not move it silently. It can, however, invalidate it, and
        that is a refusal rather than a shrug: extending teaching past the day the recuperación
        opens is a real scheduling conflict the professor has to resolve.
        """
        if calendar.makeup_starts_on is None:
            return []
        if calendar.makeup_starts_on <= new_ends_on:
            raise PeriodError(
                f"Recorrer {days:+d} día(s) llevaría el fin de clases al {new_ends_on}, pero "
                f"la recuperación está fijada al {calendar.makeup_starts_on}. Vuelve a fijar "
                f"la ventana de recuperación antes de recorrer el calendario.")
        return [f"La recuperación ({calendar.makeup_starts_on} → "
                f"{calendar.makeup_ends_on or '?'}) está fijada a mano y NO se recorrió."]

    if period_index is None:
        moved = [Period(p.index, p.name, p.starts_on + delta, p.ends_on + delta,
                        p.exam_opens_on + delta) for p in calendar.periods]
        extra = makeup_note(calendar.ends_on + delta)
        return Calendar(
            periods=moved,
            starts_on=calendar.starts_on + delta,
            ends_on=calendar.ends_on + delta,
            requested_ends_on=calendar.requested_ends_on + delta,
            leftover_days=calendar.leftover_days,
            notes=list(calendar.notes) + [
                f"Todo el calendario se recorrió {days:+d} día(s)."] + extra,
            makeup_starts_on=calendar.makeup_starts_on,
            makeup_ends_on=calendar.makeup_ends_on,
        )

    calendar.period(period_index)  # raises if it does not exist
    moved = []
    for p in calendar.periods:
        if p.index < period_index:
            moved.append(p)
        elif p.index == period_index:
            moved.append(Period(p.index, p.name, p.starts_on, p.ends_on + delta,
                                p.exam_opens_on + delta))
        else:
            moved.append(Period(p.index, p.name, p.starts_on + delta, p.ends_on + delta,
                                p.exam_opens_on + delta))

    for a, b in zip(moved, moved[1:]):
        if b.starts_on <= a.ends_on:
            raise PeriodError(
                f"Recorrer {days:+d} día(s) haría que {b.name} empiece antes de que termine "
                f"{a.name}."
            )
    # The shifted period's START does not move, so a big negative shift collapses it. Its end
    # and its exam window move together, which is why the guard is against the start and not
    # against each other.
    target = moved[period_index - 1]
    if target.ends_on < target.starts_on:
        raise PeriodError(
            f"Recorrer {days:+d} día(s) haría que {target.name} termine "
            f"({target.ends_on}) antes de empezar ({target.starts_on})."
        )
    if target.exam_opens_on < target.starts_on:
        raise PeriodError(
            f"Recorrer {days:+d} día(s) abriría el examen de {target.name} "
            f"({target.exam_opens_on}) antes de que empiece el parcial ({target.starts_on})."
        )

    return Calendar(
        periods=moved,
        starts_on=calendar.starts_on,
        ends_on=moved[-1].ends_on,
        requested_ends_on=calendar.requested_ends_on,
        leftover_days=calendar.leftover_days,
        notes=list(calendar.notes) + [
            f"{moved[period_index - 1].name} se extendió {days:+d} día(s); "
            f"los periodos posteriores se recorrieron igual."] + makeup_note(moved[-1].ends_on),
        makeup_starts_on=calendar.makeup_starts_on,
        makeup_ends_on=calendar.makeup_ends_on,
    )
