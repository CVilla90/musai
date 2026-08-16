---
id: moodle-course-settings-that-bite
title: Two Moodle course settings that silently break published content
summary: The activity-name auto-link filter repaints your headings at display time, and "0" in the filter form means inherit (on), not off.
applies_to: virtual3
tab: Content
keywords: filter, autolink, auto-link, activity names, heading, link, colour, unreadable, filtro, published content looks wrong
---

This topic is about **Campus Virtual (Moodle 3.3)**, the host your courses are on.

## What Moodle stores is not what Moodle shows

Publish a page and check the *rendered* course, never the stored HTML. Two things happen
between the two, and both have cost this project real work.

## 1. The activity-name auto-link filter

Moodle's `filter_activitynames` rewrites any occurrence of an activity's name into a link to
that activity — **at display time**, long after your markup was saved.

The visible symptom is a heading that comes out the wrong colour. A white title on a coloured
banner is repainted as a purple link on that banner: measured at roughly 1.1 : 1 contrast
against a 3.0 : 1 minimum, i.e. effectively unreadable, across a dozen headings.

You cannot fix this in the content. The filter replaces the text node, so the link's own colour
beats any style of yours, and renaming the heading does not help either — the filter matches the
activity name as a whole word anywhere in the text. **The fix is a course setting**, not an edit.

## 2. 🔴 In the filter form, `0` does not mean off

Course filters are set per course. Each filter is a dropdown with three values:

| value | label | what it does |
|---|---|---|
| `0` | Por defecto (Activado) | **inherit the site default — which is ON here** |
| `-1` | Desactivado | off |
| `1` | Activado | on |

Every course starts at `0`, which reads like "nothing set" and behaves like "on". That is why a
course can carry this defect while every dropdown looks neutral. **Off is `-1`.**

## Be careful which filter you touch

All the filters sit on one form behind one Save button. One of them, the multimedia filter, is
what turns a bare YouTube link into an embedded player — switching it off silently un-embeds
every video in the course, and nothing looks wrong until a student opens a chapter.

Change the one you mean and leave the rest alone. MUSAI's own filter writer allows exactly one
filter to be changed and compares every other one before and after, failing loudly if any of
them moved.

## A backup does carry this setting

Useful to know when copying a course: the filter override travels with a backup, so a restored
copy keeps whatever the source had. Measured — the assumption that it does not was wrong.
