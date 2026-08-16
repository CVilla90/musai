---
id: tab-activities
title: Activities — telling MUSAI which partial each one counts towards
summary: Read the course from Moodle, check the suggested partial for each activity, press Save mapping. Nothing is written to Moodle.
applies_to: musai
tab: Activities
keywords: activities, mapping, partial, parcial, category, grade engine, read the course
---

At `/courses/{course_id}/activities`. This is the input the whole grade engine runs on: a
grade cannot be computed until MUSAI knows which partial each activity belongs to.

## The order

1. **The course needs its grading scheme first.** An activity is mapped *to a partial*, so
   there is nothing to map to until the partials exist. If the page says the course has no
   partials, follow **See the grading scheme** to the Grades tab. MUSAI never assumes English's
   three partials — the scheme is per course.

2. **Press *Read the course*.** MUSAI signs in to Moodle as you, walks the course, and creates
   an activity record for each one it finds that is missing. Read-only in Moodle.

3. **Check the suggestions.** MUSAI proposes a partial for most activities, largely from which
   tab the activity sits in — the tab is how it works out which period something belongs to.
   Suggestions are shown, not applied.

4. **Press *Save mapping*.** Nothing is saved until you press it, because a mapping decides how
   a grade is computed and that stays a human decision. Activities you had already assigned are
   not touched.

**Saved in MUSAI only — nothing is written to Moodle by this tab.** Mapping changes how MUSAI
computes; it does not change the course.

## Also on this tab

**Refresh the gradebook** downloads this course's Moodle gradebook export and reads it into
MUSAI. That is where student names, matrículas and raw scores come from — see
`import-a-gradebook`.

## If an activity is missing

Press **Read the course** again; it is additive and will pick up anything new. If it is still
missing, it is either not in the course, or it sits in a hidden tab. An activity inside a
hidden section carries no hidden flag of its own, so check the section, not the activity.
