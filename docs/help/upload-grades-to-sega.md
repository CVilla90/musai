---
id: upload-grades-to-sega
title: Getting grades into SEGA
summary: MUSAI prepares and checks the grades and shows you a dry-run diff. It does not upload them — the typing into SEGA is still yours.
applies_to: musai
tab: Grades
keywords: sega, upload, subir calificaciones, confirmar, guardar, dry run, diff, parcial
---

Be clear about what MUSAI does here, because it is less than people assume.

## What MUSAI does

It computes each student's partial grade, applies whatever curve, override and extra credit you
decided, and shows you the **SEGA dry-run** at
`/courses/{course_id}/partial/{partial_id}/dryrun`.

That page signs in to SEGA as you, opens the group, selects the evaluación, reads the grade
table that is there now, and diffs it against what MUSAI computed — per student, side by side.
It is read-only, so it is safe to run even when the grading window is closed.

## What MUSAI does not do

**It does not save grades in SEGA.** There is no upload button, and there is no code in MUSAI
that can click *Guardar* or *Confirmar* in SEGA. That is not a setting or a flag you can turn
on — the write path is simply not implemented.

So the sequence today is: MUSAI computes and checks, you read the diff, and you enter the grades
in SEGA yourself.

## Why it is built that way

Saving a grade in SEGA is reversible. **Confirming it is not** — that is the moment a number
becomes the student's official record. A program that can confirm a grade is a program that can
make an irreversible academic decision with nobody in the room, and no amount of confirmation
dialogs changes that.

If a save path is ever added, it will require an explicit human action and will click only the
evaluación's own *Guardar Cambios*, never *Confirmar*. Confirming stays a person's job.

## Reading the dry-run diff

Check three things before you type anything into SEGA:

1. **The count.** Does the number of students match the group?
2. **The rows that differ.** A row that changed is either something you did on purpose (a curve,
   an override) or something you did not.
3. **Any blanks.** A student with no grade usually means an activity is unmapped or the
   gradebook is stale — see `import-a-gradebook`.
