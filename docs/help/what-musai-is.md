---
id: what-musai-is
title: What MUSAI is, and what it will not do
summary: MUSAI drives Moodle and SEGA on your behalf, for your own courses only, and never confirms a grade.
applies_to: musai
keywords: what is musai, overview, purpose, safe, rails, limits
---

MUSAI is a cockpit for the work around your English courses at UACH. It signs in to Moodle as
you, reads what is there, and — when you tell it to — writes back. It also computes partial
grades and prepares them for SEGA.

It is not a replacement for Moodle or SEGA. It is a way to do, in one place and in one pass,
the things those two make you do one activity and one student at a time.

## What it does

- Reads your Moodle dashboard and creates a record of your courses.
- Reads a course's activities and lets you say which partial each one belongs to.
- Downloads your Moodle gradebook and computes each student's partial grade, including curve
  and extra credit.
- Sets availability dates on every activity in a course from one calendar.
- Backs a course up and restores an archive into another course.
- Publishes a course home page (the *Hub*) and AI-written content blocks.
- Sends one message to every student in a group, with the recipient list shown first.

## Three things it will never do

**It never clicks *Confirmar* in SEGA.** MUSAI only ever clicks *Guardar*. Saving a grade in
SEGA is reversible; confirming it is not, and confirming is the moment a grade becomes the
student's official record. That decision stays with you, in front of your own screen.

**Every write starts as a dry run.** Anything that would change a live course runs in
simulation first and tells you what it *would* have done. See the `dry-run` topic.

**It only ever touches your own courses.** Your account reaches the courses you own in MUSAI
and nothing else — not a colleague's group, not a colleague's students, not the assistant you
are reading this from. See the `who-can-sign-in` topic.

## What it costs you

Nothing to use. The AI features cost a small amount per question and MUSAI meters it — see the
`usage-and-cost` topic.

## Where to start

The `getting-started` topic is the order to do things in on your first morning.
