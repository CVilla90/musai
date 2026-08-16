---
id: waiting-on-a-job
title: Slow jobs — what the progress card means, and when not to press again
summary: You never wait; the job does. A job ends in one of three states, and "lost track" is not "failed" — re-running on that assumption is what deletes a course.
applies_to: musai
keywords: job, progress, waiting, slow, timeout, stale, failed, refused, retry, re-run, close the tab
---

Anything that drives a browser is a job: mapping courses, reading a course, refreshing a
gradebook, a backup, a restore, writing the Cronograma, publishing, sending messages.

## You do not wait — the job does

Pressing the button returns immediately with a progress card. The browser work happens in the
background. **You can close the tab.** Start all your restores, close everything, come back
later.

MUSAI cannot make Moodle faster. A restore is a queued job on UACH's server and takes about
fifteen minutes for a 50 MB archive. What MUSAI can do is make sure you are not sitting in
front of it.

## The progress card is honest

- Every tick on it corresponds to a step the job actually reported. **Nothing advances because
  time passed.**
- The elapsed clock is real and always shown. Without it, a slow job and a hung job look
  identical.
- A refused job gets no clock, because nothing ran and a duration would be noise dressed up as
  a measurement.

## Three ways a job ends

**Done.** It finished. The card says what it did, in numbers.

**Refused — "MUSAI didn't do this — nothing was changed".** A deliberate stop, with a reason
and no step trail. A refusal is not a failure and does not want a retry; it wants you to read
the one sentence and do what it says.

**🔴 Lost track — "MUSAI stopped following this job".** This is the one that matters. It means
the app was restarted, or the laptop slept, while the job ran. **It does not tell you the job
failed.** Moodle may well have finished it.

## What to do when MUSAI lost track

Open the course in Moodle and look.

Then decide. Do not re-run on the assumption that nothing happened:

- Re-running a **restore** that actually succeeded is what deletes a course.
- Re-running a **message send** that actually succeeded sends it twice, and a message cannot be
  unsent. This has happened on a real course, to real students, for exactly this reason: a
  timeout was read as a failure and the send was repeated.

A timeout means *unknown*. It does not mean *no*.

## If MUSAI blames the remote system

When MUSAI says Moodle or SEGA was down, treat that as its best guess, not as a fact it
verified. From outside, a slow host, a wrong password and a real outage look similar. Check
before acting on the diagnosis.
