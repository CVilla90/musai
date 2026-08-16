---
id: tab-messages
title: Messages — one message to every student in a group
summary: You see the recipient list by name before anything is sent, Simulacro walks the whole path safely, and Enviar needs the group code typed by hand.
applies_to: musai
tab: Messages
keywords: message, mensaje, send, enviar, students, whatsapp, moodle message, simulacro, recipients
---

At `/courses/{course_id}/mensajes`. Compose one message, see exactly who would receive it, then
send it to the whole group.

Its screens are in Spanish.

## Why this page exists

Moodle's own compose screen tells you *"Agregado nuevo receptor 1"* — a count, never a list of
names. So the **Destinatarios** table here is not a convenience. It is the only place that
information exists before the message is gone.

## Before you can send

MUSAI needs the roster, which comes from the gradebook. If it has no enrolled students for the
group it says so and refuses, because it cannot verify the recipient list against Moodle's.

Before sending, MUSAI compares its own list against what Moodle's page offers. **If they do not
match, it does not send.** A roster that has drifted is a reason to stop, not to proceed with
whichever list is bigger.

## The two buttons

- **👓 Simulacro** — walks the real path all the way to Moodle's own *Vista previa* and stops.
  Nothing is sent. Use it freely.
- **✉️ Enviar de verdad** — sends. It additionally requires you to **type the group code by
  hand** in the confirmation box.

The typed code is not decoration and it does not degrade politely: if you press *Enviar*
without it, MUSAI refuses loudly and tells you what to type. It deliberately does **not**
quietly fall back to a simulacro — that would teach you the box is optional, and the next time
you fill it in the message goes out.

There is also **Sólo a mí**, which really sends, but only to you. It is the honest way to test
the whole path without involving students.

## Sending is not idempotent

Moodle offers no marker that makes a re-send safe, so MUSAI's own record is the only thing that
knows a message already went out. That is why **Enviar otra vez aunque sea idéntico** is a
separate, explicit checkbox — and why a timeout is not a reason to press send again. See
`waiting-on-a-job`.

## The Evaluación docente counters

The panel at the top counts sends per purpose against the professor-evaluation rubric —
bienvenida, seguimiento, cierre. **Only real sends count. A simulacro reached nobody**, so it
does not.

## A message is a copy of the course, and copies go stale

Naming an activity a student cannot find, or a date that has since moved, is the same failure as
a wrong date. Before sending, check the claim against the course itself rather than against a
document about the course.
