---
id: dry-run
title: Dry run — what the badge in the header means
summary: DRY-RUN means nothing you click can reach Moodle or SEGA. It is the default, and the badge is in the same place on every page.
applies_to: musai
keywords: dry run, dry-run, simulation, safe mode, live, writes, badge, simulacro, preview
---

Every page carries a badge in the header, in the same position and the same colours regardless
of which tab you are on:

- **DRY-RUN · no writes** — nothing you click can change anything in Moodle or SEGA. Jobs still
  run, still sign in, still read, and still show you a full report of what they *would* have
  done. They simply do not write.
- **LIVE · writes enabled** — writes reach the real systems.

The badge answers exactly one question: *can anything I click right now reach Moodle?* It looks
identical on every tab on purpose. A safety indicator that changes appearance is decoration.

## Dry run is the default

MUSAI starts in dry run. Leaving it on costs you nothing except the write itself: you still get
the plan, the counts and the list of what each step touched.

## Where to check before something irreversible

Before a restore, a message send, or a grade upload, look at the badge. Those three are the
ones you cannot take back by pressing the button again:

- a **restore** deletes the target course's contents before it writes;
- a **message** cannot be unsent;
- a SEGA **upload** puts numbers in front of the office (though MUSAI never confirms them —
  see `upload-grades-to-sega`).

## Dry run is not the only guard

Several actions have a second one on top of it. A message send additionally requires you to
type the group code by hand. A restore runs a read-only pre-flight first. These are described
in each action's own topic — dry run is the floor, not the whole staircase.

## Turning it off

Dry run is a server setting, not a checkbox in the interface, and deliberately so: a switch on
the page is a switch that gets flipped by accident at 11pm. If the badge says DRY-RUN and you
need a real write, that is a decision made at the server, by whoever runs it.
