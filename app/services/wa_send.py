import logging
from typing import Optional
import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def send_wa_text(to: str, body: str, settings: Optional[Settings] = None) -> None:
    """Send an outbound WhatsApp message via Twilio's REST API (async, non-blocking).

    `to` format: 'whatsapp:+<countrycode><number>'
    """
    if settings is None:
        settings = get_settings()

    if not body or not body.strip():
        logger.warning("Attempted to send empty WhatsApp message to %s; skipping.", to)
        return

    # Ensure format 'whatsapp:+...'
    target_to = to.strip()
    if not target_to.startswith("whatsapp:"):
        target_to = f"whatsapp:{target_to}"

    print(f"[WA-SLACK-BRIDGE] Sending WhatsApp to {target_to}: \"{body}\"")

    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
        auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        data = {
            "From": settings.TWILIO_WHATSAPP_NUMBER,
            "To": target_to,
            "Body": body,
        }
        try:
            print(f"[DEBUG] FROM={settings.TWILIO_WHATSAPP_NUMBER} TO={target_to} SID={settings.TWILIO_ACCOUNT_SID}")
            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, auth=auth, timeout=10.0)
                if response.status_code in (200, 201):
                    logger.info("Twilio WhatsApp message sent to %s", target_to)
                    return
                logger.error(
                    "Twilio API returned error %d for %s: %s",
                    response.status_code, target_to, response.text,
                )
                if not settings.DEBUG:
                    raise RuntimeError(f"Twilio send failed ({response.status_code}): {response.text}")
                return
        except httpx.HTTPError as exc:
            logger.exception("Failed to send WhatsApp message via Twilio to %s: %s", target_to, exc)
            if not settings.DEBUG:
                raise
            return

    logger.info("Twilio credentials not configured; simulated outbound message to %s: %s", target_to, body)
