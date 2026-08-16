---
id: build-content-with-ai
title: Course Builder — describing content and having MUSAI write it
summary: Describe what you want, MUSAI writes it and shows you exactly how it will look, then publishes it into a section you choose.
applies_to: musai
tab: Content
keywords: builder, ai, generate, write, banner, block, publish, preview, lucky, course builder
---

At `/courses/{course_id}/build`, reached from the Content tab and from the Overview tab.

Describe the content you want in plain words — English or Spanish. MUSAI writes it, renders it
exactly as it will appear, checks it is safe for Moodle to store, and then puts it in the
section you pick.

## The form

- **What do you want to add?** — a description, e.g. *un banner de bienvenida para el semestre
  agosto–diciembre 2026*.
- **Where** — which section of the course it goes into.
- **I'm feeling lucky** — skip the preview and publish in one shot.
- **Publish for real** — without this checkbox, publishing fills in the Moodle form, screenshots
  it, and stops before saving. With it, it saves.
- **Generate**.

## Preview first

The default is to show you the rendered result before anything reaches Moodle. Composing is
cheap — a fraction of a cent, no browser involved. Publishing is the slow part, and the part
that changes a live course.

The AI budget used today is shown at the top right of the page.

## Publishing edits in place

A published block carries a marker, so publishing the same block again **updates it** rather
than adding a second copy underneath. You can regenerate and republish without cleaning up.

## The Builder cannot read your grades

Two separate surfaces, deliberately. The **assistant** reads — it can look at gradebook data and
these help topics, and it has no way to write anything. The **Builder** writes — it composes and
publishes, and it never sees a grade. Bolting the two together would dissolve the guarantee that
the reading side cannot change anything.

## Builder or Hub?

- **Builder** — generative. Describe it once, publish it, done. Good for a banner, a notice, a
  one-off block.
- **Content tab / Hub** — a document you own and come back to. Good for the course home page you
  will edit again in October. See `tab-content`.

## After publishing, look at the rendered course

What Moodle stores is not what Moodle shows. See `moodle-course-settings-that-bite`.
