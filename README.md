# MUSAI

A professor's cockpit for Moodle: read a course, map its activities to grading partials,
compute partial grades, write due dates back, publish course content, and message a roster —
each of them a slow, error-prone job that a university teacher otherwise does by hand, several
times a semester, across several groups.

MUSAI drives Moodle the way a person does — through the browser, with Playwright — because the
target install exposes no usable web service API. That single constraint shapes everything
here: every write is a form submission that can silently do nothing, so the interesting part of
this codebase is not the automation, it is the **verification and the refusals**.

> This repository is the engine, published generically. The working notes, the operational
> scripts and everything they touch are kept private, because they name real colleagues, real
> students and live courses. Nothing here identifies a person.

---

## Why it is built the way it is

Three properties drove most of the design, and they are worth stating up front because they
explain choices that otherwise look paranoid.

**A write can succeed and report nothing, or fail and report success.** Moodle's forms accept a
POST, redirect, and render a page that looks identical whether or not the field changed. So the
authoritative reading of any value is that activity's own edit form, re-fetched afterwards —
never the page you just submitted, and never the step message the writer emitted. Several
writers in this repo shipped in a state where their own post-check was the only thing reporting
that they had done nothing at all.

**A remote system's error message is a hypothesis, not a diagnosis.** A click that times out
after 30 seconds while the server is still working produces an exception naming the server. Act
on that and you retry an operation that already succeeded. Timeouts here resolve to
*unknown* — `ok=None`, never `ok=False` — because *failed* invites a retry and *unknown*
invites a verification, and only one of those is safe when the operation reaches students.

**The moment a second user exists, every unscoped query is a data leak.** Ownership is not a
filter applied at the call site; it is the only way a route may reach a course
(`web/deps.py`), and a NULL owner belongs to *nobody* rather than to everybody — the
helpful-looking `owner == me OR owner IS NULL` is the version that leaks. A test walks
`app.routes`, finds every path containing `{course_id}`, and asserts each one 404s for a
non-owner, so a route added next month is covered the day it is written.

## The three rails

1. **Save, never confirm.** The grade-upload adapter is structurally incapable of clicking the
   irreversible control. A human confirms grades.
2. **Dry-run by default.** Every write to a live system defaults to dry-run.
3. **The student-facing assistant is read-only**, in its own process, as a restricted database
   role. It can never reach grading or upload code.

## Layout

| path | what lives there |
|---|---|
| `musai/web/` | FastAPI cockpit — routers, the auth gate, ownership dependencies, templates |
| `musai/automation/` | Playwright drivers: backup, restore, gradebook export, messaging, credentials |
| `musai/coursebuild/` | Writers that create, rename, remove, publish and re-order course content |
| `musai/coursedates/` | Reads a course's structure and writes a whole term's due dates |
| `musai/grading/` | Partial-grade engine, curve handling, gradebook ingest |
| `musai/security/` | Fernet vault for delegated credentials; fails closed with no key |
| `musai/jobs.py`, `musai/checklists.py` | Background jobs and the honest waiting component |
| `musai/susai/` | Read-only student assistant (WhatsApp webhook) |
| `tests/` | ~925 tests, no network, structurally unable to authenticate anywhere |

## The waiting component

A course restore is a queued job on the Moodle server and takes as long as it takes — often
fifteen minutes. No interface change makes that faster, so the split is: **the user waits under
a second, the job takes what it takes.** A job id returns immediately, a worker thread drives
the browser, and the progress view renders a checklist whose items are ticked *only* by step
messages the job actually emitted.

Items have four states — done, current, pending, and **skipped**: passed without a matching
message, drawn quietly and never ticked, because inventing that evidence is the one thing the
component exists to refuse. Terminal states are three, not two: done, **refused** (nothing
changed, with the reason), and **lost track** (the worker vanished; says nothing about the
remote system, and never invites a re-run). An earlier version generated its step log from
timers — lines that appeared on schedule whether or not anything had happened. A test fails if
that returns.

## Authentication

Google OAuth, restricted to one institutional domain, enforced as **middleware** rather than a
per-route dependency — a rail that depends on the next author remembering to add a decorator is
not a rail. A test registers a route *after* the gate is installed and asserts it is gated
anyway.

It fails closed: missing credentials mean cockpit routes return 503 while the landing page
stays up and names the missing variable. And the domain check alone is not the gate — students
hold addresses in the same domain, so a domain-only rule would admit every student in the
university to the professor's console.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m playwright install chromium
copy .env.example .env   # then fill in real values
```

## Run

```powershell
.\.venv\Scripts\python -m uvicorn musai.web.app:app --reload   # http://localhost:8000
.\.venv\Scripts\python -m pytest -q
```

⚠️ `.env` changes need a **full restart** — `--reload` does not reread it, and the sign-in
values are read once at import.

## License

Not yet licensed. All rights reserved by the author.
