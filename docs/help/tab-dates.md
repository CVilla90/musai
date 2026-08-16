---
id: tab-dates
title: Dates (Cronograma) — setting every activity's dates at once
summary: Give MUSAI the term window and the number of parciales; it cuts a plan tab by tab, you check it, then Simulacro before Aplicar.
applies_to: musai
tab: Dates
keywords: dates, cronograma, fechas, availability, due date, deadline, calendar, extension, prórroga, shift
---

At `/courses/{course_id}/cronograma`. This is the tab that replaces an afternoon: setting open
and close dates on every quiz, assignment and activity in a course, one form at a time.

Its screens are in Spanish; the rest of MUSAI is in English.

## How it thinks

You think *"whatever is under this tab happens in this period"*, so the page is built around the
course's tab strip and never around individual activities. What it writes is still per activity
and per type, because a quiz, an assignment and a book do not share a single date field.

## The four numbers at the top

- **Primer día de clases** — first teaching day.
- **Último día de clases** — last teaching day. Note this is almost never the semester's
  administrative end date, which is what MUSAI pre-fills when it has to guess. Adjust it.
- **Parciales** — how many periods to cut the term into.
- **Ventana de examen (días)** — how long an exam stays open.

There is also a checkbox to close assignments **en firme** at the end of a parcial. Without it,
a late submission is still received and marked late; with it, it is refused.

Press **Recalcular** and MUSAI cuts the plan.

## The tab map

MUSAI guesses which parcial each tab belongs to. The guess is only a pre-fill — correct it, and
your correction persists. The map is re-guessed on every re-read of the course, and a manual
decision is flagged so that re-reading never silently undoes it.

## Extensions

Adding an extension extends one parcial by N days, or slides the whole calendar if you do not
name a parcial. This is the operation that actually repeats: a student asks for an extension and
by hand it means going back through every tab and every quiz.

Adding an extension changes nothing in Moodle by itself. It re-cuts the plan, and the plan still
has to be run. Removing one is a single click, on purpose — undo has to be as cheap as the
action or nobody experiments.

## Running it

Two buttons at the bottom, plus one checkbox:

- **👓 Simulacro** — walks the whole path and writes nothing.
- **✅ Aplicar** — writes, but **only if the checkbox *Escribir de verdad en Moodle* is
  ticked**. Without the tick, *Aplicar* does a simulacro. That fallback is deliberate here: a
  dry run of a date change is useful on its own, so the safe outcome is also a useful one.
- An undo file is always saved, holding the previous values.

Writing dates is a browser job. It takes minutes, and you can close the tab — see
`waiting-on-a-job`.

## Re-reading the course

**Volver a leer el curso** re-reads the structure from Moodle and shows when it last did. Do
this after you have added or moved activities in Moodle, or the plan is cut from a stale
picture.
