---
id: tab-overview
title: The course workspace and its seven tabs
summary: Every course page has the same seven tabs and the same header, so you always know which course a button belongs to.
applies_to: musai
tab: Overview
keywords: tabs, workspace, course page, navigation, sections, where is
---

Click a group code on the cockpit home page and you land in that course's workspace. Its header
shows the group code and, underneath, what Moodle calls the course — because "which course am I
on?" must never be a question a destructive button leaves open.

Under the header, seven tabs, always in this order:

| tab | address | what it is for |
|---|---|---|
| **Overview** | `/courses/{course_id}` | what MUSAI holds for this course, and what is missing |
| **Activities** | `/courses/{course_id}/activities` | which partial each activity counts towards |
| **Dates** | `/courses/{course_id}/cronograma` | set availability dates on everything at once |
| **Grades** | `/courses/{course_id}/grades` | partials, curve, extra credit, SEGA dry-run |
| **Content** | `/courses/{course_id}/hub` | the course home page you own and edit |
| **Transfer** | `/courses/{course_id}/transfer` | back this course up, restore an archive into it |
| **Messages** | `/courses/{course_id}/mensajes` | one message to every student in the group |

Each tab has its own colour. That is not decoration — it is so the tab you are on and the
buttons on it agree, and a red *Restore* button is never two tabs away from looking like a blue
*Save*.

## What the Overview tab tells you

It is the honest inventory: what MUSAI has read from this course and what it still needs before
it can help. Typically that is one of

- no Moodle id — the course was created by hand and has never been mapped;
- no activities read yet — go to **Activities** and press **Read the course**;
- no gradebook imported — go to **Activities** and refresh the gradebook, or **Grades**;
- activities read but not assigned to a partial — the grade engine cannot run until they are.

It also links to the **Course Builder**, the AI writing surface described in
`build-content-with-ai`.

## The student count is a date, not a fact

If the Overview shows a student count, it is the size of the last gradebook file MUSAI read,
with the date it read it. It is not a live count of who is enrolled today. A course that says
"10 students" may well have 30 — it means the file is old, not that the course shrank.
