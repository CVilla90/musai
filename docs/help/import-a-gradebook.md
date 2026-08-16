---
id: import-a-gradebook
title: Getting your students and their scores into MUSAI
summary: Refresh the gradebook from the Activities tab. It is the only source of enrolment MUSAI has, and the student count you see is the size of the last file it read.
applies_to: musai
tab: Activities
keywords: gradebook, import, students, roster, enrolment, refresh, calificaciones, matricula, student count
---

MUSAI learns who is in your group from **one place only**: your Moodle gradebook export. Not
from the participants page, not from Moodle's enrolment API — from the file.

## How to do it

On the Activities tab of a course (`/courses/{course_id}/activities`), refresh the gradebook.
MUSAI signs in to Moodle as you, opens the course, downloads the gradebook export, and reads it
in. One login, one course, one download.

It is read-only against Moodle. It writes only MUSAI's own database, which is why there is no
dry-run step and no confirmation on it.

## It is safe to re-run, and you should

The import is an idempotent upsert. Re-running with a fresh export updates student names and
grade values in place, and **does not disturb the activity → partial mapping you have already
made**. Refreshing weekly is the intended rhythm — the newest export is meant to be the source
of truth.

If you press it twice, MUSAI shows you the run that is already going rather than starting a
second one. Two browser sessions on one Moodle account at the same time is a real hazard, not a
tidiness concern.

## 🔴 The student count is the size of a file, with a date on it

If a course says *"Students · 10"* and the live course has thirty-something, nothing shrank. It
means the last gradebook MUSAI read had ten students in it, and that was a while ago.

This has caught people out on a real course. MUSAI shows the date it last imported next to the
count for exactly that reason — read the date, not just the number.

**Anything that acts on the roster is acting on that file.** Before sending a message, MUSAI
compares its list against Moodle's live one and refuses if they disagree, rather than sending to
whichever list it has.

## If a student is missing

Refresh the gradebook. If they are still missing, they are not in the Moodle gradebook export —
which usually means they enrolled after the export you are looking at, or they are enrolled in a
different group.
