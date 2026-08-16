from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from markupsafe import Markup
from sqlmodel import Session, select

from musai import i18n
from musai.config import settings
from musai.db import init_db, engine
from musai.models import Course, Semester
from musai.professors import courses_owned_by, credential_status, semester_ids_with_courses
from musai.security.vault import key_configured as vault_key_configured
from musai.semesters import (active_semester, courses_in, ensure_current_semester,
                             resolve_semester, semester_label)

from musai.web import auth as auth_mod
from musai.web import language as lang_mod
from musai.web.deps import current_professor
from musai.web.format import grade_pill_style, grade_colors

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["grade_pill_style"] = grade_pill_style
templates.env.globals["grade_colors"] = grade_colors
# Every template can ask who is signed in without each route passing it down.
templates.env.globals["current_user"] = auth_mod.current_user


@pass_context
def t(ctx, text: str, **params) -> Markup:
    """`{{ t("Cockpit") }}` — this string, in this request's language.

    A Jinja global rather than something each route passes down, for the same reason
    `current_user` is one: forty routes remembering to include a translator is forty chances to
    forget, and the ones that forgot would render English inside an otherwise Spanish page —
    which reads as a broken translation rather than as a missing wire.

    `@pass_context` is what makes the call site short. The language depends on the request, the
    request is already in every template context, so the template does not have to say so.

    ⚠️ Returns `Markup`, because a fair number of these sentences carry a `<strong>` around the
    load-bearing half. That is safe only as long as the catalogue is ours: interpolated values
    go through `escape()` inside `i18n.translate`, and nothing user-supplied is ever a key.
    """
    request = ctx.get("request")
    lang = lang_mod.current(request) if request is not None else i18n.DEFAULT
    return Markup(i18n.translate(text, lang, **params))


@pass_context
def lang(ctx) -> str:
    """The BCP-47 code for `<html lang="…">`.

    🔴 Not a constant, which is what it used to be: `base.html` declared `lang="es"` and served
    English while `landing.html` declared `lang="en"`, so the two disagreed with each other and
    both disagreed with the page. That is not cosmetic — it is what makes a screen reader
    pronounce English prose with Spanish phonetics, and what makes a browser offer to translate
    a page that is already in the reader's language.
    """
    request = ctx.get("request")
    return lang_mod.current(request) if request is not None else i18n.DEFAULT


templates.env.globals["t"] = t
templates.env.globals["lang"] = lang
templates.env.globals["LANGUAGES"] = i18n.LANGUAGES
templates.env.globals["LANGUAGE_NAMES"] = i18n.LANGUAGE_NAMES


def usage_meter(request: Request):
    """Month-to-date MUSAI spend for the signed-in professor, or `None` when signed out.

    A template global rather than a value each route passes down, for the same reason
    `current_user` is one: the meter belongs to the nav, and forty routes remembering to
    include it is forty chances to forget. The ones that forgot would silently render a nav
    with no meter, which reads as "nothing spent" — the wrong answer, told confidently.

    One indexed SUM over a table that holds ~50 rows per professor per month. Cached on the
    request so a page that renders the nav twice does not query twice.

    🔴 Never raises. `current_professor` 401s when signed out, and an exception here would take
    down every page in the app to report an accounting total.
    """
    cached = getattr(request.state, "_usage_meter", ...)
    if cached is not ...:
        return cached
    meter = None
    try:
        if auth_mod.current_user(request):
            from musai import metering

            with Session(engine) as sess:
                prof = current_professor(request, sess)
                meter = metering.month_to_date(sess, prof.email, is_admin=prof.is_admin)
    except Exception:                                            # noqa: BLE001
        meter = None
    request.state._usage_meter = meter
    return meter


templates.env.globals["usage_meter"] = usage_meter


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On local dev (SQLite): creates all tables on startup.
    # On Replit Postgres: Alembic migration handles it; init_db is a no-op.
    init_db()
    yield


app = FastAPI(
    title="MUSAI",
    description="Moodle UACH Suite + AI — Professor Cockpit",
    lifespan=lifespan,
)

# Default-deny gate + the signed session cookie. The ordering trap lives in `install()`.
auth_mod.install(app)


@app.get("/health")
def health():
    return {"status": "ok", "dry_run": settings.dry_run, "app": "MUSAI"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, semester: str | None = None):
    """The landing page signed out; the cockpit signed in.

    Kept on `/` rather than split into `/` + `/cockpit` so that every existing link, bookmark
    and `href="/"` in the templates keeps working, and so signing in lands you where you were.
    """
    if not auth_mod.current_user(request):
        return templates.TemplateResponse(
            "landing.html",
            {
                "request": request,
                "dry_run": settings.dry_run,
                "auth_configured": settings.auth_configured,
                "missing_config": auth_mod.missing_config(),
                "allowed_domain": settings.allowed_email_domain,
                "auth_error": request.query_params.get("auth_error"),
                "attempted": request.query_params.get("attempted"),
                "signed_out": request.query_params.get("signed_out"),
                "next": auth_mod._safe_next(request.query_params.get("next")),
            },
        )
    return _cockpit(request, semester)


def _cockpit(request: Request, semester: str | None = None):
    """Cockpit home for ONE professor, scoped to one semester.

    🔴 Two scopes, and both are load-bearing:

    * **By professor.** `courses_owned_by` replaced `courses_in` on 2026-08-14, when a second
      professor was about to sign in. `courses_in` returns every course in the semester
      regardless of owner — correct while the database had one user, and a cross-professor
      roster leak the moment it had two.
    * **By semester.** Group codes repeat every semester, so an unscoped list shows the same
      seven codes twice with no way to tell them apart.

    A professor with no courses yet is not an error state: the semester row is created from the
    calendar and the page offers to map their courses from Moodle.
    """
    with Session(engine, expire_on_commit=False) as sess:
        prof = current_professor(request, sess)
        # The calendar decides which semester it is, so a professor signing in for the first
        # time on a fresh database still lands somewhere real. Existing rows are never rewritten.
        current = ensure_current_semester(sess)
        shown = resolve_semester(sess, semester) or current
        courses = courses_owned_by(sess, prof.id, semester_id=shown.id)

        # Only semesters this professor actually taught in — offering someone a dropdown of
        # terms they have no history in is an invitation to a blank page they cannot explain.
        mine = semester_ids_with_courses(sess, prof.id) | {current.id, shown.id}
        all_semesters = sorted(
            (s for s in sess.exec(select(Semester)).all() if s.id in mine),
            key=lambda s: s.starts_on, reverse=True,
        )
        creds = credential_status(sess, prof.id)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "dry_run": settings.dry_run,
            "courses": courses,
            "professor": prof,
            "semester": shown,
            "semester_label": semester_label(shown.name),
            "semesters": all_semesters,
            "is_current": bool(shown and current and shown.id == current.id),
            "moodle_stored": creds["moodle"]["stored"],
            "vault_ready": vault_key_configured(),
        },
    )


from musai.web import (routes_assistant, routes_build, routes_course,  # noqa: E402
                       routes_dates, routes_hub, routes_messages, routes_partial,
                       routes_settings, routes_transfer)
from musai.susai import webhook as susai_webhook  # noqa: E402
app.include_router(auth_mod.router)
app.include_router(lang_mod.router)
app.include_router(routes_settings.router)
app.include_router(routes_transfer.router)
app.include_router(routes_course.router)
app.include_router(routes_partial.router)
app.include_router(routes_assistant.router)
app.include_router(routes_build.router)
app.include_router(routes_hub.router)
app.include_router(routes_dates.router)
app.include_router(routes_messages.router)
app.include_router(susai_webhook.router)
