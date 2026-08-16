"""Settings ▸ Passwords — where a professor stores the logins MUSAI acts with.

Everything here is about one uncomfortable fact stated plainly rather than hidden: **MUSAI has
to be able to read these passwords.** Moodle has no delegation, no app password and no API
token for a teacher account, so the only way to act as someone is to type their password into
the login form. A hash cannot be typed into a form; therefore this is encryption, therefore it
is reversible, and therefore the page says so in the words a professor would use.

What the design does with that:

* The password field is **write-only**. There is no route that returns a stored secret, not
  even to its owner — the page can say *"stored, last worked 14:22"* and nothing more.
* **Delete is a real delete**, because it is the only way to withdraw consent, and a flag that
  leaves the row decryptable is not a withdrawal.
* **Test** signs in and immediately signs out, so *"is this password right?"* is answerable in
  fifteen seconds instead of at minute nine of a restore.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session

from musai import jobs, metering, professors as prof_store
from musai.config import settings
from musai.db import engine
from musai.security import vault
from musai.web.deps import current_professor

router = APIRouter(tags=["settings"])

#: Settings tabs, in nav order. A tab not listed here cannot be reached by URL — `_page`
#: falls back to the first — so a typo in a link lands on a real page instead of a blank one.
#:
#: ⚠️ Keys only, and the template owns what each one is CALLED. The keys are URL values
#: (`/settings?tab=usage`) that a professor may have bookmarked and that `docs/help/` cites, so
#: they stay English forever; the labels have to be translated, and a label translated here
#: would have to go through `t(variable)` in the template — which the translation audit cannot
#: see, so it would render English on a Spanish page with every check still green.
TABS = ("passwords", "usage", "language")


def _templates():
    from musai.web.app import templates

    return templates


def _page(request: Request, *, notice: str = "", error: str = "", tab: str = "passwords"):
    if tab not in TABS:
        tab = TABS[0]
    with Session(engine) as sess:
        prof = current_professor(request, sess)
        status = prof_store.credential_status(sess, prof.id)
        # 🔴 Scoped to `prof.email`, not to a shared constant. The whole point of the Usage tab
        # is that a professor sees THEIR spend; an unscoped rollup would show them the faculty's.
        spend = metering.month_to_date(sess, prof.email, is_admin=prof.is_admin)
        by_kind = metering.breakdown(sess, prof.email) if tab == "usage" else []
        events = metering.recent(sess, prof.email) if tab == "usage" else []
    return _templates().TemplateResponse(
        "settings.html",
        {
            "request": request,
            "dry_run": settings.dry_run,
            "professor": prof,
            "credentials": status,
            "systems": prof_store.SYSTEMS,
            "vault_ready": vault.key_configured(),
            "username_guess": prof.moodle_username_guess,
            "notice": notice,
            "error": error,
            "tab": tab,
            "tabs": TABS,
            "spend": spend,
            "by_kind": by_kind,
            "events": events,
            "rate_card": metering.rate_card(),
            "typical": metering.typical_costs(prof.is_admin),
            "recent_checks": jobs.recent(owner=prof.email, kind=jobs.CREDENTIAL_CHECK, limit=3),
            # 🔴 The RAW column, not the resolved language. The Language tab has to be able to
            # say "you have never chosen" — which is a different state from "you chose
            # English", and the only one from which a future change of default may move you.
            "stored_language": prof.language,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, notice: str = "", error: str = "", tab: str = "passwords"):
    return _page(request, notice=notice, error=error, tab=tab)


@router.post("/settings/passwords/{system}")
def save_password(request: Request, system: str,
                  username: str = Form(""), password: str = Form("")):
    """Store or replace one credential. The password is encrypted before anything is written."""
    with Session(engine) as sess:
        prof = current_professor(request, sess)
        try:
            prof_store.store_credential(sess, prof.id, system,
                                        username=username, password=password)
        except vault.VaultUnavailable as e:
            # A configuration problem, not the professor's mistake — say which value is missing
            # rather than blaming the input. Nothing was stored.
            return _page(request, error=str(e))
        except ValueError as e:
            return _page(request, error=str(e))
    label = prof_store.SYSTEM_INFO[system]["label"]
    return RedirectResponse(
        url=f"/settings?notice={_q(f'{label} password saved. Test it to be sure it works.')}",
        status_code=303)


@router.post("/settings/passwords/{system}/delete")
def delete_password(request: Request, system: str):
    with Session(engine) as sess:
        prof = current_professor(request, sess)
        had = prof_store.delete_credential(sess, prof.id, system)
    label = prof_store.SYSTEM_INFO.get(system, {}).get("label", system)
    msg = f"{label} password deleted." if had else f"No {label} password was stored."
    return RedirectResponse(url=f"/settings?notice={_q(msg)}", status_code=303)


@router.post("/settings/passwords/{system}/test", response_class=HTMLResponse)
def test_password(request: Request, system: str):
    """Sign in, confirm the dashboard renders, sign out. Read-only; nothing is written anywhere.

    Runs as a background job like every other browser task, because a campusvirtual login is
    ten to twenty seconds and an HTTP request that blocks that long is how a professor learns
    to distrust the app.
    """
    with Session(engine) as sess:
        prof = current_professor(request, sess)
        prof_id, email = prof.id, prof.email

    if system != prof_store.MOODLE:
        # SEGA's adapter drives a different site and its own rails (save-never-confirm); until
        # it is wired here, claiming to have tested it would be a lie with a green tick on it.
        return _templates().TemplateResponse(
            "work_progress.html",
            {"request": request, "job": {"status": "failed", "result": {
                "error": "Only the Moodle password can be tested from here so far. A SEGA "
                         "check has to go through the grade uploader's own rails.",
                "steps": []}}, "job_id": None},
        )

    job_id = jobs.start(
        jobs.CREDENTIAL_CHECK, owner=email, params={"system": system},
        work=lambda jid: _check_moodle(jid, prof_id, email),
    )
    return _job_fragment(request, job_id, email)


def _check_moodle(job_id: int, professor_id: int, email: str) -> dict:
    """Log in as this professor and confirm the dashboard appears. No writes, no navigation."""
    from musai.mapping import read_tiles
    from musai.models import Professor

    def step(msg: str) -> None:
        jobs.update(job_id, step=msg)

    with Session(engine) as sess:
        prof = sess.get(Professor, professor_id)

    try:
        tiles = read_tiles(prof, headless=True, on_step=step)
    except Exception as e:
        with Session(engine) as sess:
            prof_store.mark_used(sess, professor_id, prof_store.MOODLE, ok=False)
        raise e

    with Session(engine) as sess:
        prof_store.mark_used(sess, professor_id, prof_store.MOODLE, ok=True)
    step(f"Signed in — {len(tiles)} course(s) visible on the dashboard")
    return {"ok": True, "tiles": len(tiles),
            "sample": [t.subject for t in tiles[:4]]}


def _job_fragment(request: Request, job_id: int, owner: str):
    job = jobs.get(job_id, owner=owner)
    return _templates().TemplateResponse(
        "work_progress.html", {"request": request, "job": job, "job_id": job_id})


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text)
