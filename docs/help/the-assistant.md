---
id: the-assistant
title: The assistant — what it can answer and what it cannot do
summary: It reads your gradebook and this help corpus through read-only tools. It cannot change anything, and it will say "no topic covers that" rather than invent a procedure.
applies_to: musai
keywords: assistant, ai, chat, ask, analyst, questions, help, what can you do
---

At `/assistant`. Ask about your groups in English or Spanish.

## What it can answer

**Your gradebook.** How a group is doing, per partial: how many are graded, how many passing,
the mean, the spread. Whether a group is improving between partials. One student's grades across
every partial and their course total. Who is at risk in a group.

**MUSAI itself.** What the app can do, how a screen works, what a button does — from a set of
help topics like this one. That is where this page came from.

## Two things it cannot do

**It cannot change anything.** Every tool it has is read-only, at the database level, not by
policy. There is no write path for it to reach: it cannot alter a grade, publish content, send a
message or touch a course. This is structural — turning it off is not a setting.

**It cannot see anyone else's data.** Its tools are bound to your professor account before your
question is even sent. Ask about a group code that belongs to a colleague and it answers *"you
have no group X"*. It is not filtering their answer out of a larger one — it never had it.

## When it does not know

For a question about MUSAI, the assistant answers only from a help topic it has just read, and
cites which one. If no topic covers your question, **it says so and stops.**

That refusal is the feature. A wrong number you can sanity-check; a confidently wrong procedure
sends you to a button that does not exist, or tells you something destructive is safe. If you
get "no topic covers that", the answer genuinely is not written down yet.

Its help topics are also filtered to your Moodle. See `which-moodle-you-are-on`.

## What it costs

About a tenth of a cent per question, metered against your monthly allowance. There is a daily
limit as well, which exists to catch a runaway loop rather than a busy afternoon. See
`usage-and-cost`.

## What is recorded

That you asked, and which tools ran — for example `student_status`. **Not what you typed.** The
question text is never stored, so nobody can read your questions back, including whoever
administers MUSAI.
