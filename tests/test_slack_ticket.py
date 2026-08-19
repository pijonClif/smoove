import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import TicketExtraction
from app.services.slack_ticket import create_slack_ticket
from app.services.twilio_wa import send_wa_text


@pytest.mark.asyncio
async def test_create_slack_ticket_block_kit():
    """Test that create_slack_ticket constructs proper Block Kit payload."""
    ticket = TicketExtraction(
        title="Fix double charge on invoice",
        description="Customer was charged twice for monthly subscription.",
        priority="high",
        category="billing",
        needs_clarification=False,
        clarification_question=None,
    )

    mock_client = MagicMock()
    mock_client.chat_postMessage = AsyncMock(
        return_value={"channel": "C998877", "ts": "1710000000.123456"}
    )

    with patch("app.services.slack_ticket._get_slack_client", return_value=mock_client):
        channel_id, ts = await create_slack_ticket(ticket, "whatsapp:+1234567890")

        assert channel_id == "C998877"
        assert ts == "1710000000.123456"
        assert mock_client.chat_postMessage.called

        # Check call arguments
        call_kwargs = mock_client.chat_postMessage.call_args[1]
        blocks = call_kwargs["blocks"]

        # Check header
        assert blocks[0]["type"] == "header"
        assert blocks[0]["text"]["text"] == "Fix double charge on invoice"

        # Check description
        assert blocks[1]["type"] == "section"
        assert "Customer was charged twice" in blocks[1]["text"]["text"]

        # Check fields (priority, category, customer)
        fields = blocks[2]["fields"]
        assert any("High" in f["text"] for f in fields)
        assert any("Billing" in f["text"] for f in fields)
        assert any("+1234567890" in f["text"] for f in fields)

        # Check context footer
        assert blocks[3]["type"] == "context"
        assert "via WhatsApp" in blocks[3]["elements"][0]["text"]


@pytest.mark.asyncio
async def test_create_slack_ticket_mock_fallback():
    """Test fallback when SLACK_BOT_TOKEN is not configured."""
    ticket = TicketExtraction(
        title="Mock ticket test",
        description="Testing mock fallback without Slack token.",
        priority="medium",
        category="other",
    )

    with patch("app.services.slack_ticket._get_slack_client", return_value=None):
        channel_id, ts = await create_slack_ticket(ticket, "whatsapp:+1234567890")
        assert channel_id is not None
        assert ts is not None


from app.services.wa_send import send_wa_text


def test_send_wa_text():
    """Test send_wa_text helper function from app.services.wa_send."""
    # Blank body should do nothing safely
    send_wa_text("whatsapp:+1234567890", "")

    # Mock successful twilio client call
    mock_messages = MagicMock()
    mock_messages.create = MagicMock(return_value=MagicMock(sid="SM_MOCK_123"))

    with patch("app.services.wa_send.Client") as mock_twilio_class:
        mock_instance = MagicMock()
        mock_instance.messages = mock_messages
        mock_twilio_class.return_value = mock_instance

        send_wa_text("whatsapp:+1234567890", "Hello there!")
        assert mock_instance.messages.create.called
        call_kwargs = mock_instance.messages.create.call_args[1]
        assert call_kwargs["to"] == "whatsapp:+1234567890"
        assert call_kwargs["body"] == "Hello there!"
