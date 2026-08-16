---
id: who-can-sign-in
title: Who can sign in, and what you can see once you do
summary: Only @uach.mx staff accounts, through Google. You see your own courses and nobody else's.
applies_to: musai
keywords: sign in, login, access, permissions, privacy, students, colleague, who can see
---

## Getting in

MUSAI has no password of its own. Sign-in is Google, and it accepts one kind of account:

- the address must be `@uach.mx`;
- Google must have verified it;
- it must not be a student account. Student addresses at UACH are a letter followed by digits
  (`a227222@uach.mx`), and those are refused by shape, because the domain alone does not keep
  students out — they have `@uach.mx` addresses too.

There is no invite step and no approval queue. A colleague who meets those three conditions can
sign in and start using MUSAI without anyone provisioning them.

## What you can see

Your own courses. Nothing else.

Every course in MUSAI has an owner, and every screen, download and AI answer is filtered to the
courses you own. Asking the assistant about a group code that belongs to a colleague returns
"you have no group X", not their numbers — even when you and they teach group codes with the
same name, which is normal at FCCF.

A course with no owner belongs to nobody, not to everybody. If a course you expect is missing,
the fix is to press **Update from Moodle** on the cockpit home page so it is mapped to you —
not to look for it somewhere else in the app.

## What students can see

Nothing. Students never sign in to MUSAI. The student-facing side of this system is SUSAI, a
separate WhatsApp assistant in its own process, and it is read-only.

## What MUSAI itself can reach

MUSAI acts as *you*, with the password you stored, so in Moodle and SEGA it can reach exactly
what you can reach and no more. It has no institutional account and no elevated access. See the
`passwords-and-what-musai-can-read` topic.
