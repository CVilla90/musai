---
id: getting-started
title: Your first morning with MUSAI
summary: Sign in, store your Moodle password, map your courses — in that order, because each step needs the one before it.
applies_to: musai
tab: Overview
keywords: start, setup, first time, onboarding, new user, begin
---

Four steps. Each one needs the one before it, so the order is not a suggestion.

## 1. Sign in

Open MUSAI and press **Sign in with Google**. Use your `@uach.mx` account. Nothing else is
accepted — see the `who-can-sign-in` topic.

The first time you sign in, MUSAI creates your professor record. You will land on an empty
cockpit: no courses yet, because it has not looked at Moodle on your behalf.

## 2. Store your Moodle password

Go to `/settings`. Under **Passwords**, enter your Moodle username and password and save.

You have to do this, and it is worth understanding why before you do. Moodle gives a teacher
no API token, no app password and no way to delegate — so the only way for MUSAI to act as you
is to type your password into the login form the way you would. That means MUSAI can read this
password back. The `passwords-and-what-musai-can-read` topic says exactly what that means and
what protects it.

Press **Test this password** afterwards. That signs in, checks your dashboard renders, and
signs out. It writes nothing.

## 3. Map your courses

Back on the cockpit home page, press **Load my courses from Moodle**. (Once you already have
courses, the same button sits at the top and reads **Update from Moodle**.) MUSAI opens your
Moodle dashboard, reads the course tiles it finds there, and creates a course record for each
one in the current semester. It takes about half a minute.

This is read-only — it does not change anything in Moodle. If a course is missing afterwards,
it was not on your Moodle dashboard. Re-mapping is additive: it never removes a course you
already have.

## 4. Open one course

Click a group code. That opens the course workspace, which has seven tabs across the top:
Overview, Activities, Dates, Grades, Content, Transfer, Messages. Each has its own topic —
start with `tab-overview`, which tells you what MUSAI is still missing for that course.

## What you do NOT need to do first

You do not need to store your SEGA password until you are ready to upload grades, and you do
not need to import a gradebook until you want grades computed. MUSAI will tell you what is
missing on the Overview tab of each course rather than making you guess.
