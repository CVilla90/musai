---
id: tab-grades
title: Grades — computing a partial, curving it, and checking it before SEGA
summary: The exact machine grade is never touched. A curve or an override sits on top of it and produces the final grade that uploads.
applies_to: musai
tab: Grades
keywords: grades, partial, parcial, curve, curva, override, extra credit, sega, compute, calificaciones
---

At `/courses/{course_id}/grades`, which lists this course's partials. Click one to open its
grade sheet at `/courses/{course_id}/partial/{partial_id}`.

## Two numbers per student, and they never overwrite each other

- **exact** — what the machine computed from the activity grades. It is never modified.
- **final** — exact, plus whatever you did on top: a curve, a manual override, extra credit.
  **This is the number that uploads to SEGA.**

Keeping them separate is the point. You can always see what the raw result was, and you can
always get back to it.

## How exact is computed

Per partial, from the activities you mapped on the Activities tab:

```
partial % = average(general activities) × weight_general
          + special activity            × weight_special
          + exam activity               × weight_exam
```

then converted to the 0–10 scale SEGA uses, rounded to one decimal. The weights are stored per
partial, so a course can change them without changing anyone else's.

For English at FCCF the three partials are *Parcial 1*, *Parcial 2* and *Examen Final
Ordinario*, and the course total weights them 30 / 30 / 40. Passing is 7.0.

## The buttons on a grade sheet

- **↻ Recompute exact** — recalculates from the imported activity grades, keeping each row's
  existing curve mode.
- **Auto-curve** — applies the standard curve to every row you have not touched by hand.
- **↺ Clear all** — discards every curve and override in this partial; everybody back to exact.
- **Save grade** on a row — a manual override for one student. Leave it blank to clear it.
- **Save extra** on a row — additive extra credit, e.g. for cultural participation. Blank or 0
  clears it.
- **↺ reset to exact / auto-curve** on a row — puts one student back.

## The standard curve

Square root: `final = √(exact ÷ 10) × 10`. It preserves the order of the class, helps the
weakest most, barely moves the top, and clears genuine borderline students. Grades are clamped
to between 0.1 and 10.

A curve is a group-level, explicit decision. It is visible on the sheet, per student, and
reversible in one click — never a hidden adjustment inside the calculation.

## Before SEGA

**SEGA dry-run →** shows the diff: exactly which numbers would be uploaded for which students,
against what is there now. Read it before uploading. See `upload-grades-to-sega`.

## If the numbers look wrong

Almost always one of three things, in this order of likelihood:

1. the gradebook is stale — refresh it from the Activities tab;
2. an activity is not mapped to a partial, so it is not in the average;
3. an activity is mapped to the wrong category (general / special / exam), so it is weighted
   wrongly.
