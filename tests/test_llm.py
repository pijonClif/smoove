import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import TicketExtraction
from app.services.llm import (
    DEFAULT_CLARIFICATION_FALLBACK,
    extract_ticket,
    summarize_update,
)


@pytest.mark.asyncio
async def test_extract_ticket_success():
    """Test successful ticket extraction with valid LLM JSON response."""
    mock_json_content = json.dumps({
        "title": "Fix billing invoice charge",
        "description": "User was billed twice for subscription #12345.",
        "priority": "high",
        "category": "billing",
        "needs_clarification": False,
        "clarification_question": None,
        "is_followup": False,
        "related_ticket_id": None,
    })

    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = mock_json_content
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with patch("app.services.llm._get_groq_client", return_value=mock_client):
        result = await extract_ticket("I got charged twice on invoice 12345")

        assert isinstance(result, TicketExtraction)
        assert result.title == "Fix billing invoice charge"
        assert result.priority == "high"
        assert result.category == "billing"
        assert result.needs_clarification is False
        assert result.clarification_question is None


@pytest.mark.asyncio
async def test_extract_ticket_with_history_followup():
    """Test ticket extraction when tickets.db history indicates a follow-up."""
    mock_json_content = json.dumps({
        "title": "Check refund status",
        "description": "User asking if refund for Ticket #4 has been processed yet.",
        "priority": "medium",
        "category": "billing",
        "needs_clarification": False,
        "clarification_question": None,
        "is_followup": True,
        "related_ticket_id": 4,
    })

    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = mock_json_content
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    history_text = '- Ticket #4 [OPEN] (2026-08-19): Title: "Resolve double subscription charge"'

    with patch("app.services.llm._get_groq_client", return_value=mock_client):
        result = await extract_ticket("Any updates on my refund?", ticket_history=history_text)

        assert isinstance(result, TicketExtraction)
        assert result.is_followup is True
        assert result.related_ticket_id == 4
        assert result.needs_clarification is False


@pytest.mark.asyncio
async def test_extract_ticket_model_fallback():
    """Test fallback to second model when primary model fails."""
    mock_json_content = json.dumps({
        "title": "Reset password issue",
        "description": "User cannot login to account.",
        "priority": "medium",
        "category": "access",
        "needs_clarification": False,
        "clarification_question": None,
        "is_followup": False,
        "related_ticket_id": None,
    })

    mock_fallback_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = mock_json_content
    mock_fallback_completion.choices = [mock_choice]

    mock_client = MagicMock()
    # First call (primary model) fails, second call (fallback model) succeeds
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[Exception("Groq rate limit exceeded"), mock_fallback_completion]
    )

    with patch("app.services.llm._get_groq_client", return_value=mock_client):
        result = await extract_ticket("I cannot log in to my account")

        assert result.title == "Reset password issue"
        assert result.category == "access"
        assert mock_client.chat.completions.create.call_count == 2


@pytest.mark.asyncio
async def test_extract_ticket_json_parse_fallback():
    """Test fallback to needs_clarification=True when LLM returns invalid JSON."""
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Not a valid JSON response from LLM"
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with patch("app.services.llm._get_groq_client", return_value=mock_client):
        result = await extract_ticket("help please")

        assert isinstance(result, TicketExtraction)
        assert result.needs_clarification is True
        assert result.clarification_question is not None
        assert "details" in result.clarification_question.lower()


@pytest.mark.asyncio
async def test_extract_ticket_no_groq_client():
    """Test graceful fallback when Groq client is not available."""
    with patch("app.services.llm._get_groq_client", return_value=None):
        result = await extract_ticket("hello")

        assert isinstance(result, TicketExtraction)
        assert result.needs_clarification is True
        assert result.clarification_question is not None


@pytest.mark.asyncio
async def test_summarize_update_user_facing():
    """Test summarizing a user-facing Slack update into a WhatsApp message."""
    mock_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "We have refunded the extra charge to your card. It should appear in 2-3 business days."
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

    with patch("app.services.llm._get_groq_client", return_value=mock_client):
        result = await summarize_update(
            slack_text="Processed refund via Stripe dashboard ref #9988.",
            ticket_title="Fix billing charge",
        )

        assert result == "We have refunded the extra charge to your card. It should appear in 2-3 business days."


@pytest.mark.asyncio
async def test_summarize_update_skip_internal_note():
    """Test that internal notes resulting in SKIP return None."""
    for skip_text in ["SKIP", "SKIP.", '"SKIP"', "skip"]:
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = skip_text
        mock_completion.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        with patch("app.services.llm._get_groq_client", return_value=mock_client):
            result = await summarize_update(
                slack_text="Looking into db logs, checking celery worker.",
                ticket_title="Server 500 error",
            )
            assert result is None
