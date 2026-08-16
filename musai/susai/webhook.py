"""SUSAI WhatsApp webhook — receives inbound messages from Meta's Cloud API.

Two endpoints (PLAN §7):
  GET  /webhook  — Meta verification handshake (echo hub.challenge once on setup).
  POST /webhook  — inbound events. Verify X-Hub-Signature-256 against the app
                   secret, ACK 200 immediately, then process in the background.

This skeleton only *logs* inbound messages so we can confirm delivery end-to-end.
Persisting to Conversation/Message and the read-only Gemini reply come next — and
stay read-only (SUSAI rail): this module never touches grading/upload code.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import PlainTextResponse, Response

from musai.config import settings
from musai.automation._log import logger
from musai.susai import brain

router = APIRouter()

# Meta's dashboard "Test" button uses this canned sender — don't try to reply to it.
_META_SAMPLE_FROM = "16315551181"


@router.get("/webhook")
def verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Meta calls this once when you save the webhook. Echo the challenge back
    only if the verify token matches the one we configured."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.success("Webhook verified by Meta.")
        return PlainTextResponse(hub_challenge)
    logger.warning("Webhook verify failed (token mismatch).")
    return PlainTextResponse("Forbidden", status_code=403)


def _valid_signature(body: bytes, header: str | None) -> bool:
    """HMAC-SHA256 of the raw body, keyed by the app secret, must equal the
    sha256=... value in X-Hub-Signature-256."""
    secret = settings.whatsapp_app_secret
    if not secret:
        logger.warning("WHATSAPP_APP_SECRET not set — skipping signature check (dev only).")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def _process(payload: dict) -> None:
    """Walk the webhook payload and log each inbound message / status update."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {
                c["wa_id"]: c.get("profile", {}).get("name", "?")
                for c in value.get("contacts", [])
            }
            for msg in value.get("messages", []):
                sender = msg.get("from", "?")
                who = names.get(sender, "?")
                if sender == _META_SAMPLE_FROM:
                    logger.info("📩 Meta sample event — acknowledged, no reply.")
                    continue
                if msg.get("type") == "text":
                    body = msg.get("text", {}).get("body", "")
                    logger.success(f"📩 {who} ({sender}): {body}")
                    brain.respond(sender, who, body, msg.get("id"))
                else:
                    logger.info(f"📩 {who} ({sender}): [{msg.get('type')}] non-text")
                    brain.respond_nontext(sender, msg.get("type", "unknown"))
            for status in value.get("statuses", []):
                logger.info(f"· delivery status: {status.get('status')} ({status.get('id', '')[:18]}…)")


@router.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not _valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        logger.error("Invalid webhook signature — rejecting.")
        return Response(status_code=403)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Response(status_code=400)
    # ACK immediately; do the work after responding (Meta retries on slow/failed acks).
    background.add_task(_process, payload)
    return Response(status_code=200)
