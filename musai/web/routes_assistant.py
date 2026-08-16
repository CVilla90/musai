"""In-app AI assistant routes — a read-only cockpit chat over the gradebook."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from musai import metering
from musai.config import settings
from musai.assistant.agent import ask
from musai.db import engine
from musai.web.app import templates
from musai.web.deps import current_professor

router = APIRouter()


def _me(request: Request) -> tuple[str, bool]:
    """(email, is_admin) for the signed-in professor — the key every AI call is billed to.

    🔴 Resolved here rather than inside `ask()` so the agent stays importable from a script
    with no request. Before 2026-08-16 every call in the app billed the literal `web:carlos`,
    so two professors shared one daily budget and one bill.
    """
    with Session(engine) as sess:
        prof = current_professor(request, sess)
        return prof.email, bool(prof.is_admin)


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request):
    actor, is_admin = _me(request)
    with Session(engine) as sess:
        spend = metering.month_to_date(sess, actor, is_admin=is_admin)
    return templates.TemplateResponse("assistant.html", {
        "request": request,
        "dry_run": settings.dry_run,
        "has_key": bool(settings.gemini_api_key),
        "model": settings.gemini_model,
        "spend": spend,
        "question_cost": metering.price_micro_usd(
            requests=1, seconds=3.0, tokens_in=1400, tokens_out=200),
    })


@router.post("/assistant/ask", response_class=HTMLResponse)
def assistant_ask(request: Request, q: str = Form(...)):
    actor, is_admin = _me(request)
    result = (ask(q.strip(), actor=actor, is_admin=is_admin) if q.strip()
              else {"answer": "", "tools": [], "ok": False})
    return templates.TemplateResponse("assistant_reply.html", {
        "request": request, "q": q.strip(), "result": result,
    })
