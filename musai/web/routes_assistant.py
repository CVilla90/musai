"""In-app AI analyst routes — a read-only cockpit chat over the gradebook."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from musai.config import settings
from musai.analyst.agent import ask
from musai.web.app import templates

router = APIRouter()


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request):
    return templates.TemplateResponse("assistant.html", {
        "request": request,
        "dry_run": settings.dry_run,
        "has_key": bool(settings.gemini_api_key),
        "model": settings.gemini_model,
    })


@router.post("/assistant/ask", response_class=HTMLResponse)
def assistant_ask(request: Request, q: str = Form(...)):
    result = ask(q.strip()) if q.strip() else {"answer": "", "tools": [], "ok": False}
    return templates.TemplateResponse("assistant_reply.html", {
        "request": request, "q": q.strip(), "result": result,
    })
