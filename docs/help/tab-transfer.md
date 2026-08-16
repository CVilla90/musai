---
id: tab-transfer
title: Transfer — backing a course up and restoring an archive into it
summary: Backup is safe and additive. Restore deletes the target course's contents first, and it is the most destructive thing MUSAI does.
applies_to: musai
tab: Transfer
keywords: backup, restore, mbz, archive, copy, transfer, respaldo, restaurar, destructive
---

At `/courses/{course_id}/transfer`. Two operations that look similar on the page and are not
similar at all.

## Backup — safe, additive, about a minute

Press **Create a backup**, then **Yes — back up {your group} now**. MUSAI signs in to Moodle as
you and creates a backup in your Moodle private backup area, then downloads the `.mbz` to this
machine. The file is yours — it came out of your course. **Download the .mbz** hands it to you.

Nothing in the course changes.

## Restore — destructive, about fifteen minutes

**Restoring replaces everything in the target course.** Its activities are deleted first, then
the archive's contents are written in. Enrolled students stay enrolled; their submissions and
grades do not.

The flow is deliberately two steps:

1. **Check what this would do.** Upload the `.mbz` and MUSAI runs a read-only pre-flight. It
   reads the archive and the target and reports both: *this archive is INGLES IV, 106
   activities, no user data* / *the target is <name>, with N activities across M sections that
   will be deleted*. Nothing is written to Moodle. A file that turns out not to be a backup
   costs you a second here rather than ten minutes later.

2. **Replace N activities — restore now.** The button says the number it is about to destroy,
   read live from the target rather than from anything you typed.

If MUSAI holds grades for that course, there is an extra checkbox you must tick, saying you have
re-fetched them or accept losing them. A restore wipes them.

## After starting a restore, do not start another

A restore takes about fifteen minutes for a 50 MB backup and you can close the tab. If the
progress card later says it lost track of the job, **that does not mean the job failed** — it
means MUSAI stopped being able to see it. Moodle may well have finished.

Open the course and look before running anything again. Re-running a restore on a job that
actually succeeded is what deletes a course. See `waiting-on-a-job`.

## What a restore carries, and what it does not

It carries the activities, their dates, filter overrides and tab visibility. It does **not**
carry the same activity ids: a restore mints new ones. If you are comparing two courses
afterwards, pair activities on their names, never on their ids.

## Restore is not the way to copy a course you can edit

If both courses are yours to edit, Moodle's own course-to-course import is much faster and does
not delete anything. See `copy-a-course-faster`.
