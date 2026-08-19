import logging
from typing import Optional
import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def send_wa_text(
    to_number: str,
    body: str,
    settings: Optional[Settings] = None,
) -> bool:
    """Send an outbound WhatsApp message to customer via Twilio REST API.
    
    If Twilio credentials are not configured, logs the message and returns True.
    """
    if settings is None:
        settings = get_settings()

    if not body or not body.strip():
        return False

    print(f"[WA-SLACK-BRIDGE] Sending WhatsApp to {to_number}: \"{body}\"")

    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
            auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            data = {
                "From": settings.TWILIO_WHATSAPP_NUMBER,
                "To": to_number,
                "Body": body,
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, auth=auth, timeout=10.0)
                if response.status_code in (200, 201):
                    logger.info("WhatsApp message sent successfully to %s", to_number)
                    return True
                else:
                    logger.error("Twilio API returned error %d: %s", response.status_code, response.text)
                    return False
        except Exception as exc:
            logger.exception("Failed to send WhatsApp message via Twilio: %s", exc)
            return False

    logger.info("Simulated outbound WhatsApp message to %s: %s", to_number, body)
    return True
