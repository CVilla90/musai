---
id: tab-content
title: Content (the Course Hub) — your course home page, edited as a form
summary: Fill in a form, press Guardar y ver, then publish. Republishing edits the same block in Moodle instead of stacking a second copy.
applies_to: musai
tab: Content
keywords: hub, content, home page, course page, whatsapp link, publish, portada, contenido
---

At `/courses/{course_id}/hub`. This is the course's home page — the block students see at the
top of the course — written as a form instead of as HTML.

No AI is involved on this tab and nothing here costs anything.

## Two scopes on one form

- **Your profile** — typed once and reused across all your courses: your name, your role, your
  photo, your WhatsApp number, a short bio, what students can expect from you, how to ask you
  for help, your institution and its logo.
- **This group** — different per course: the course title and tagline, **this group's WhatsApp
  invite link**, the content badge, a description, the temario per parcial, the weighting, where
  to find things, the page language and the colour.

The WhatsApp *group* link is per course and the WhatsApp *number* is on your profile. That split
is the whole reason the form has two halves: your number is the same everywhere, and a group
link that ends up on the wrong course sends students to somebody else's chat.

Your phone number is written in exactly one place — the profile field — and appears in exactly
one place on the page, under contact. It is not repeated anywhere else.

## Language

The **Idioma de la página** choice changes the fixed headings (Contenido, Ponderación…), not
what you wrote. In *Bilingüe*, English goes first with Spanish underneath in smaller, quieter
type — and you can do the same inside your own text by writing `English ~~español~~`.

## Saving and publishing

**Guardar y ver** saves both scopes and shows you the preview. Then publish.

**Publish sends what is saved, never what is merely typed into the form.** If you edited a field
and did not save, that edit is not in what goes out.

Publishing is a browser job and honours dry run. Republishing **edits the same block in Moodle
in place** rather than adding a second copy of the page, so you can fix the WhatsApp link in
August and a weight in October without cleaning anything up.

## The Hub is not the Builder

The Content tab is a document you own and come back to. The **Course Builder**
(`build-content-with-ai`) is generative: you describe something, an AI writes it, you publish it
once. Different jobs, deliberately separate surfaces.

## After publishing, look at the course

Moodle renders what it stores; the two are not the same thing. In particular a course-level
filter can rewrite your markup at display time — see `moodle-course-settings-that-bite`.
