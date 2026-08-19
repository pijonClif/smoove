import logging
from typing import Optional, Tuple
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlmodel import Session
from twilio.request_validator import RequestValidator

from app.config import Settings, get_settings
from app.db import (
    create_initial_ticket,
    format_ticket_history,
    get_db_session,
    get_session,
    get_ticket_by_message_sid,
    get_ticket_history,
    update_ticket_details,
)
from app.models import Ticket, TicketExtraction
from app.services.llm import extract_ticket
from app.services.slack_ticket import create_slack_ticket
from app.services.wa_send import send_wa_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["whatsapp"])

EMPTY_TWIML = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response/>"


async def verify_twilio_signature(request: Request, settings: Settings) -> dict[str, str]:
    """Verify inbound Twilio webhook signature unless DEBUG mode is active.
    
    Extracts form-encoded parameters and validates against X-Twilio-Signature header.
    """
    form_data = await request.form()
    params = {k: str(v) for k, v in form_data.items()}

    if settings.DEBUG:
        logger.debug("DEBUG mode enabled: skipping Twilio signature validation.")
        return params

    if not settings.TWILIO_AUTH_TOKEN:
        logger.warning("TWILIO_AUTH_TOKEN is not configured; skipping signature check.")
        return params

    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        logger.error("Twilio webhook rejected: Missing 'X-Twilio-Signature' header.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing Twilio signature header",
        )

    # Determine public URL (accounting for reverse proxies / tunnels like ngrok)
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_proto and forwarded_host:
        url = f"{forwarded_proto}://{forwarded_host}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
    else:
        url = str(request.url)

    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
    if not validator.validate(url, params, signature):
        logger.error("Twilio webhook rejected: Invalid signature for URL: %s", url)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Twilio signature",
        )

    return params


async def process_whatsapp_message(
    ticket_id: int,
    wa_number: str,
    wa_message_sid: str,
    body: str,
    settings: Settings,
) -> None:
    """Background task to extract ticket metadata, handle clarification, post to Slack, and reply to user."""
    print(f"\n-------------------------------------------------------")
    print(f"[WA-SLACK-BRIDGE] Inbound WhatsApp from {wa_number}")
    print(f"  Message: \"{body}\" (SID: {wa_message_sid})")

    try:
        # 1. Fetch user's prior ticket history from tickets.db
        history_text = "No prior tickets found."
        with get_db_session() as session:
            prior_tickets = get_ticket_history(
                session=session,
                wa_number=wa_number,
                exclude_sid=wa_message_sid,
                limit=5,
            )
            history_text = format_ticket_history(prior_tickets)
            print(f"[WA-SLACK-BRIDGE] Found {len(prior_tickets)} prior tickets in tickets.db for context")
            if prior_tickets:
                for pt in prior_tickets:
                    print(f"  - #{pt.id} [{pt.status.upper()}]: \"{pt.title}\"")

        # 2. Extract structured ticket information via Groq LLM with tickets.db context
        extraction = await extract_ticket(raw_text=body, ticket_history=history_text)
        print(f"[WA-SLACK-BRIDGE] LLM Extracted:")
        print(f"  Title: \"{extraction.title}\" | Priority: {extraction.priority} | Category: {extraction.category}")
        if extraction.is_followup:
            print(f"  [Follow-up] Linked to Ticket #{extraction.related_ticket_id}")

        # 3. Handle Clarification Flow
        if extraction.needs_clarification:
            clarification_question = (
                extraction.clarification_question
                or "Could you please provide more details about your request so we can assist you better?"
            )
            print(f"  [Needs Clarification] Question: \"{clarification_question}\"")
            print(f"[WA-SLACK-BRIDGE] Sending clarification to user and stopping.")

            with get_db_session() as session:
                update_ticket_details(
                    session=session,
                    ticket_id=ticket_id,
                    title=extraction.title,
                    status="needs_clarification",
                )

            # Send clarification question back to customer on WhatsApp and STOP
            send_wa_text(to=wa_number, body=clarification_question, settings=settings)
            print(f"-------------------------------------------------------\n")
            return

        # 4. Standard Support Ticket Flow: Create Slack Ticket
        channel_id, slack_ts = await create_slack_ticket(
            ticket=extraction,
            wa_number=wa_number,
            settings=settings,
        )

        # 5. Update SQLite database record with status='open', title, channel, ts
        with get_db_session() as session:
            ticket = update_ticket_details(
                session=session,
                ticket_id=ticket_id,
                title=extraction.title,
                slack_channel=channel_id,
                slack_ts=slack_ts,
                status="open",
            )
            print(f"[WA-SLACK-BRIDGE] DB Ticket #{ticket_id} updated -> status: open | channel: {channel_id} | ts: {slack_ts}")

        # 6. Send WhatsApp confirmation message with title to customer
        confirmation_msg = f"Your support request has been received: \"{extraction.title}\". Our team is on it!"
        send_wa_text(to=wa_number, body=confirmation_msg, settings=settings)

        print(f"[WA-SLACK-BRIDGE] Posted to Slack {channel_id} (ts: {slack_ts})")
        print(f"-------------------------------------------------------\n")

    except Exception as exc:
        logger.exception("Error processing background WhatsApp message %s: %s", wa_message_sid, exc)
        print(f"[WA-SLACK-BRIDGE] Error: {exc}")


# ---------------------------------------------------------------------------
# Twilio WhatsApp Webhook Endpoint
# ---------------------------------------------------------------------------


@router.post("/wa", response_class=Response)
async def receive_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Twilio WhatsApp Inbound Webhook.
    
    - Accepts form-encoded params: From, Body, MessageSid
    - Verifies Twilio signature using RequestValidator (unless DEBUG=True)
    - Deduplicates by MessageSid
    - Schedules asynchronous processing in BackgroundTasks
    - Returns empty TwiML 200 OK immediately
    """
    params = await verify_twilio_signature(request, settings)

    wa_from: Optional[str] = params.get("From")
    body: str = params.get("Body", "")
    message_sid: Optional[str] = params.get("MessageSid")

    if not message_sid or not wa_from:
        logger.warning("Twilio webhook received missing required fields: From=%s, MessageSid=%s", wa_from, message_sid)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required form fields: 'From' and 'MessageSid'",
        )

    # Normalize phone number (handle cases where '+' is URL-decoded as space)
    wa_from = wa_from.replace("whatsapp: ", "whatsapp:+").strip()

    # Check for duplicate message
    existing_ticket = get_ticket_by_message_sid(session, message_sid=message_sid)
    if existing_ticket:
        print(f"[WA-SLACK-BRIDGE] Duplicate MessageSid received ({message_sid}) -> Skipping background tasks.")
        return Response(content=EMPTY_TWIML, media_type="application/xml", status_code=status.HTTP_200_OK)

    # Persist initial ticket record
    ticket = create_initial_ticket(
        session=session,
        wa_number=wa_from,
        wa_message_sid=message_sid,
    )

    # Process message: extract ticket metadata, handle clarification, post to Slack
    await process_whatsapp_message(
        ticket_id=ticket.id,
        wa_number=wa_from,
        wa_message_sid=message_sid,
        body=body,
        settings=settings,
    )

    # Return empty TwiML 200 immediately
    return Response(content=EMPTY_TWIML, media_type="application/xml", status_code=status.HTTP_200_OK)
