---
id: copy-a-course-faster
title: Copying a course you teach into another course you teach
summary: Moodle can copy course-to-course server-side in about two minutes with no file. It merges rather than replaces, so the target must be empty.
applies_to: virtual3
tab: Transfer
keywords: copy, import, duplicate, same subject, several groups, faster, two minutes, merge, import.php
---

This topic is about **Campus Virtual (Moodle 3.3)**.

If you teach several groups of the same subject and want them all to have the same content,
there are two routes and they are very different.

## Backup and restore — about fifteen minutes

Make a `.mbz` from the source, upload it to the target, wait for Moodle's queued job. This is
what MUSAI's Transfer tab does, and it is the route that works even when the two courses belong
to different professors.

**It wipes the target first.** Dangerous, but self-correcting: a wrong restore is one right
restore away from fixed.

## Course-to-course import — about two minutes

Moodle can copy directly from one course to another with **no file at all**, because it already
has both. No 50 MB download, no upload, no queue.

**It merges. It does not replace.** Run it into a course that already has content and Moodle
does not overwrite anything — it adds a second copy of every activity, each with its own
gradebook entry. Unpicking that is hand work across every section, and unlike a bad restore
there is no single operation that undoes it.

So the rule is the mirror image of a restore's: **the target must be empty.**

## When it is available

Only between two courses **you can edit**. Moodle offers the import screen based on your own
permissions at both ends, so this is the route for one professor with several groups of the
same subject. It cannot copy into a colleague's course — that is backup and restore.

## Status in MUSAI

MUSAI has this lane built as a command-line tool, not yet as a button on the Transfer tab. It
is deliberately not wired to a button: past its first screen its steps have never been run
against a live course, and a button would be one click away from an unverified sequence on a
real group. It also has a read-only probe mode that walks the screens without submitting
anything.

Ask about it before using it — do not assume the Transfer tab does this.

## One thing to check afterwards, whichever route you take

Whether the **course format** came across. These courses use a tabbed format (*onetopic*), and
if the tab strip did not travel, the copy has no tabs — which also means the Dates tab has
nothing to read when it tries to work out which period an activity belongs to.
