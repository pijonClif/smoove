import hmac
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.db import get_db_session, get_session
from app.models import Ticket

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["slack"])


def verify_slack_signature(
    body: bytes,
    timestamp: Optional[str],
    signature: Optional[str],
    signing_secret: str,
) -> bool:
    """Verify inbound Slack webhook signature using HMAC SHA256."""
    if not timestamp or not signature or not signing_secret:
        return False

    # Prevent replay attacks (> 5 minutes old)
    current_ts = int(time.time())
    if abs(current_ts - int(timestamp)) > 300:
        logger.warning("Slack signature timestamp is out of range: %s", timestamp)
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    my_signature = (
        "v0="
        + hmac.new(
            signing_secret.encode("utf-8"),
            sig_basestring,
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(my_signature, signature)


from app.services.llm import summarize_update
from app.services.wa_send import send_wa_text


async def process_slack_event(event_data: Dict[str, Any], settings: Settings) -> None:
    """Background task to handle Slack thread replies and bridge back to WhatsApp."""
    event = event_data.get("event", {})
    event_type = event.get("type")

    # Only process message events inside a thread (agent replying to ticket)
    if event_type == "message":
        # Ignore bot messages to avoid infinite loops
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        thread_ts = event.get("thread_ts")
        text = event.get("text", "")

        if not thread_ts:
            logger.debug("Ignoring non-threaded Slack message in channel")
            return

        with get_db_session() as session:
            # Find ticket matching Slack thread timestamp
            statement = select(Ticket).where(Ticket.slack_ts == thread_ts)
            ticket = session.exec(statement).first()

            if not ticket:
                logger.debug("No matching ticket found for Slack thread_ts: %s", thread_ts)
                return

            logger.info(
                "Found Ticket ID %d for Slack reply from user %s. Summarizing for WhatsApp: %s",
                ticket.id,
                event.get("user"),
                ticket.wa_number,
            )

            # Check if message indicates resolution/closure (case-insensitive)
            text_lower = text.lower()
            if "resolved" in text_lower or "closed" in text_lower:
                ticket.status = "closed"
                session.add(ticket)
                session.commit()
                session.refresh(ticket)
                logger.info("Ticket ID %d status updated to 'closed' based on Slack message.", ticket.id)

            # Summarize Slack update using LLM
            whatsapp_message = await summarize_update(
                slack_text=text,
                ticket_title=ticket.title or "Support Request",
            )

            if whatsapp_message is None:
                logger.info("Slack update skipped (internal note or non-user facing).")
                return

            # Relay the summarized message back to the WhatsApp user
            send_wa_text(
                to=ticket.wa_number,
                body=whatsapp_message,
                settings=settings,
            )


# ---------------------------------------------------------------------------
# Slack Events Webhook Endpoint
# ---------------------------------------------------------------------------


@router.post("/slack")
async def receive_slack_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Slack Events API Webhook.
    
    - Handles Slack URL verification handshake challenge
    - Validates Slack request signature (unless DEBUG=True)
    - Processes Slack thread messages and dispatches WhatsApp reply in BackgroundTasks
    - Returns 200 OK immediately
    """
    body_bytes = await request.body()

    # Signature verification
    if not settings.DEBUG and settings.SLACK_SIGNING_SECRET:
        timestamp = request.headers.get("X-Slack-Request-Timestamp")
        signature = request.headers.get("X-Slack-Signature")
        if not verify_slack_signature(body_bytes, timestamp, signature, settings.SLACK_SIGNING_SECRET):
            logger.error("Slack webhook rejected: Invalid signature")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Slack signature",
            )
    elif settings.DEBUG:
        logger.debug("DEBUG mode enabled: skipping Slack signature validation.")

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to parse Slack JSON payload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    # 1. Slack URL Verification Challenge
    if payload.get("type") == "url_verification":
        logger.info("Handling Slack URL verification handshake")
        return {"challenge": payload.get("challenge")}

    # 2. Slack Event Callback
    if payload.get("type") == "event_callback":
        background_tasks.add_task(
            process_slack_event,
            event_data=payload,
            settings=settings,
        )

    return {"status": "ok"}
