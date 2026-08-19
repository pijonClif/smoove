import logging
from typing import Optional
from twilio.rest import Client

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def send_wa_text(to: str, body: str, settings: Optional[Settings] = None) -> None:
    """Send an outbound WhatsApp message using Twilio REST Client.
    
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
        try:
            client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
            message = client.messages.create(
                from_=settings.TWILIO_WHATSAPP_NUMBER,
                to=target_to,
                body=body,
            )
            logger.info("Twilio WhatsApp message sent to %s (SID: %s)", target_to, message.sid)
            return
        except Exception as exc:
            logger.exception("Failed to send WhatsApp message via Twilio Client to %s: %s", target_to, exc)
            if not settings.DEBUG:
                raise
            return

    logger.info("Twilio credentials not configured; simulated outbound message to %s: %s", target_to, body)
