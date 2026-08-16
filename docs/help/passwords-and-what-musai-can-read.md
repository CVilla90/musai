---
id: passwords-and-what-musai-can-read
title: Storing your Moodle and SEGA passwords
summary: MUSAI can read the passwords you store, because it has to type them into a login form. Here is what protects them.
applies_to: musai
tab: Settings
keywords: password, credentials, security, encryption, vault, moodle password, sega password, delete
---

Stored at `/settings`, under **Passwords**. Two systems: **Moodle** (`campusvirtual.uach.mx`)
and **SEGA** (`sega.uach.mx`).

## The uncomfortable part, first

**MUSAI can read these passwords.** That is not a shortcut anyone took — it follows from what
Moodle offers. Moodle gives a teacher no API key, no app password and no way to delegate access
to a program. The only way for MUSAI to read your course list, make a backup or run a restore
*as you* is to sign in with your password the same way you would, by typing it into the login
form.

A hash cannot be typed into a form. So this is encryption, which is reversible by design, and
the page says so rather than implying something safer.

## What protects them

- Encrypted with a key that lives outside the database, so a copy of the database alone is not
  enough to read them.
- Never shown back to you. The field is write-only — you can replace a password, never read it.
- Never written to any log.
- **Delete removes them for good**, immediately.

If the server has no encryption key configured, MUSAI refuses to store a password at all rather
than keeping it in the clear, and the Settings page says so instead of silently accepting one.

## Storing them is optional

You can leave both empty and type the password each time a job needs it. Nothing is kept in
that case. Storing is a convenience, and it is yours to decline.

## What each one is used for

- **Moodle** — reads your course list, reads a course's activities and gradebook, creates
  backups, runs restores, writes dates, publishes content, sends messages.
- **SEGA** — uploads partial grades. MUSAI only ever clicks *Guardar*, never *Confirmar*.

## Testing one

Press **Test this password** next to a stored credential. MUSAI signs in, confirms the
dashboard renders, and signs out. It is read-only and writes nothing anywhere.

If the test fails, the password is wrong, expired, or the remote system is down — MUSAI cannot
tell those apart from outside, and it will say which one it *suspects* rather than asserting it.
