import logging
from typing import Optional, Tuple
from slack_sdk.web.async_client import AsyncWebClient

from app.config import Settings, get_settings
from app.models import TicketExtraction

logger = logging.getLogger(__name__)


def _get_slack_client(settings: Settings) -> Optional[AsyncWebClient]:
    """Retrieve an AsyncWebClient instance if SLACK_BOT_TOKEN is configured."""
    if not settings.SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN is not configured in settings.")
        return None
    return AsyncWebClient(token=settings.SLACK_BOT_TOKEN)


async def create_slack_ticket(
    ticket: TicketExtraction,
    wa_number: str,
    settings: Optional[Settings] = None,
) -> Tuple[str, str]:
    """Post structured support ticket to Slack channel using Block Kit.
    
    Layout:
      - Header: Ticket title
      - Section: Description
      - Section Fields: Priority, Category, WhatsApp Customer
      - Context Footer: 'via WhatsApp'
      
    Returns:
      (channel_id, message_ts)
    """
    if settings is None:
        settings = get_settings()

    client = _get_slack_client(settings)
    target_channel = settings.SLACK_TICKET_CHANNEL or "#support-tickets"

    # Build Block Kit message payload
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ticket.title[:150],
                "emoji": False,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Description:*\n{ticket.description}",
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Priority:*\n{ticket.priority.capitalize()}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Category:*\n{ticket.category.capitalize()}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Customer:*\n`{wa_number}`",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Status:*\nOpen",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"via WhatsApp ({wa_number})",
                },
            ],
        },
    ]

    fallback_text = f"New WhatsApp Support Ticket from {wa_number}: {ticket.title}"

    if client:
        try:
            logger.info("Posting ticket to Slack channel: %s", target_channel)
            response = await client.chat_postMessage(
                channel=target_channel,
                text=fallback_text,
                blocks=blocks,
            )
            channel_id = str(response.get("channel", target_channel))
            ts = str(response.get("ts", ""))
            logger.info("Ticket posted to Slack successfully (channel: %s, ts: %s)", channel_id, ts)
            return channel_id, ts
        except Exception as exc:
            logger.exception("Failed to post message to Slack API: %s", exc)
            if not settings.DEBUG:
                raise

    # Fallback / mock values when SLACK_BOT_TOKEN is not configured or in debug mode
    mock_channel = target_channel
    import time
    mock_ts = f"{int(time.time())}.{hash(ticket.title) % 1000000:06d}"
    logger.info("Generated mock Slack ticket (channel: %s, ts: %s)", mock_channel, mock_ts)
    return mock_channel, mock_ts
