"""SUSAI outbound — send WhatsApp messages via the Meta Graph API.

This is the *only* thing SUSAI writes to the outside world: a text reply, inside
the free 24h service window that the student's own inbound message opened. No
templates (those cost money — Phase 4). Read-only rail is unaffected: this sends
a message, it never touches grading/upload code.
"""

import httpx

from musai.config import settings
from musai.automation._log import logger


def normalize_recipient(to: str) -> str:
    """Mexico WhatsApp quirk: inbound wa_ids come as `52 + 1 + 10 digits`
    (e.g. 5216141837420), but the dialable/registered number is `52 + 10 digits`
    (526141837420). Meta accepts the shorter form in both dev and production, and
    it's what the dev-mode allowed-recipient list matches — so drop the inserted
    '1'. All SUSAI users are Mexican mobiles, so this is safe and universal."""
    digits = to.lstrip("+")
    if digits.startswith("521") and len(digits) == 13:
        return "52" + digits[3:]
    return digits


def send_text(to: str, body: str) -> dict | None:
    """Send a plain-text WhatsApp message to `to` (E.164 digits, no '+').
    Returns Meta's JSON on success, or None on failure (logged, never raises)."""
    to = normalize_recipient(to)
    token = settings.whatsapp_access_token
    phone_id = settings.whatsapp_phone_number_id
    if not token or not phone_id:
        logger.error("Cannot send: WHATSAPP_ACCESS_TOKEN / PHONE_NUMBER_ID not set.")
        return None

    url = f"https://graph.facebook.com/{settings.graph_api_version}/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    try:
        r = httpx.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {token}"}, timeout=20,
        )
    except httpx.HTTPError as e:
        logger.error(f"Send failed (network): {e}")
        return None

    if r.status_code >= 400:
        # 401 here almost always means the temporary 24h token expired → mint the
        # permanent System User token (SUSAI_META_SETUP.md, Part 8).
        logger.error(f"Send failed {r.status_code}: {r.text[:300]}")
        return None

    data = r.json()
    mid = (data.get("messages") or [{}])[0].get("id", "?")
    logger.success(f"↩ replied to {to} (id {mid[:24]}…)")
    return data
