---
id: usage-and-cost
title: What MUSAI costs you, and the meter in the header
summary: Browsing is free. Only AI questions and browser jobs are metered, against a small monthly allowance shown in actions, not dollars.
applies_to: musai
tab: Settings
keywords: cost, price, usage, allowance, meter, billing, free, limit, quota, money
---

MUSAI is free to you. It does cost something to run — the AI and the server behind it — so it
keeps a meter, shown in the header and in full at `/settings` under **Usage**.

## The meter is in actions, not dollars

The header says something like *"about 100 more questions this month"*, not a dollar figure.
That is deliberate: a fraction of a cent means nothing to anyone, and the number you can act on
is how much you have left to do.

## What is metered

Two things only:

- **AI calls** — assistant questions, and composing a content block in the Builder.
- **Browser jobs** — mapping courses, reading a course, backups, restores, writing dates,
  publishing, sending messages.

## What is not metered

Browsing. Opening a tab, reading a grade sheet, changing a curve, saving the Hub form,
downloading your evidence workbook — none of it is counted, because none of it costs anything
worth counting. A page view is roughly one twelve-hundredth of one assistant question. Where
the money isn't is useful to know too.

## Rough costs, so the meter is checkable

Measured 2026-08-16:

| action | about |
|---|---|
| a page in the cockpit | $0.0000008 |
| map my courses (~30 s) | $0.0004 |
| one assistant question | $0.001 |
| a course backup (~3 min) | $0.002 |
| composing a content block | $0.003 |
| a course restore (~15 min) | $0.012 |

The Usage tab shows the full rate card, including which AI model and which prices those come
from, so you can check the arithmetic rather than trust it.

## Running out

The allowance resets on the 1st of each month. Today MUSAI **measures but does not enforce**
it: going over does not lock you out, because the underlying numbers are estimates of job
durations rather than measurements, and refusing a professor access to their own gradebook on a
guess is worse than the overspend. There is a separate daily limit on AI calls, which does
stop, and which exists to catch a runaway loop rather than a busy afternoon.

If the assistant tells you the daily AI budget is used up, it resets tomorrow.
