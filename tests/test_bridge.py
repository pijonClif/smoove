import os
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

# Set test environment
os.environ["DEBUG"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./test_tickets.db"
os.environ["TWILIO_AUTH_TOKEN"] = "test_auth_token"
os.environ["SLACK_SIGNING_SECRET"] = "test_signing_secret"

from app.config import get_settings
from app.db import get_session, init_db
from app.main import app
from app.models import Ticket, TicketExtraction


@pytest.fixture(autouse=True)
def setup_test_db():
    """Create clean tables for each test."""
    from app.db import engine
    settings = get_settings()
    settings.DEBUG = True
    settings.DATABASE_URL = "sqlite:///./test_tickets.db"
    
    # Drop all existing tables and re-create schema
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield
    SQLModel.metadata.drop_all(engine)


def test_health_check():
    """Test health check endpoint."""
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["service"] == "wa-slack-bridge"


def test_root_endpoint():
    """Test root metadata endpoint."""
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "webhooks" in data
        assert data["webhooks"]["whatsapp"] == "/webhook/wa"
        assert data["webhooks"]["slack"] == "/webhook/slack"


def test_whatsapp_webhook_flow():
    """Test standard inbound WhatsApp webhook flow."""
    from unittest.mock import patch, AsyncMock
    mock_extraction = TicketExtraction(
        title="Incorrect billing charge",
        description="Billing statement has an incorrect charge.",
        priority="medium",
        category="billing",
        needs_clarification=False,
    )
    with patch("app.routers.wa.extract_ticket", new=AsyncMock(return_value=mock_extraction)):
        with TestClient(app) as client:
            # First message delivery
            form_data = {
                "From": "whatsapp:+1234567890",
                "Body": "Hello, my billing statement has an incorrect charge.",
                "MessageSid": "SM_TEST_MESSAGE_001",
            }
            response = client.post("/webhook/wa", data=form_data)
            assert response.status_code == 200
            assert "application/xml" in response.headers["content-type"]
            assert "<Response/>" in response.text

        # Verify ticket in database
        from app.db import engine
        with Session(engine) as session:
            statement = select(Ticket).where(Ticket.wa_message_sid == "SM_TEST_MESSAGE_001")
            ticket = session.exec(statement).first()
            assert ticket is not None
            assert ticket.wa_number == "whatsapp:+1234567890"
            assert ticket.status == "open"
            assert ticket.title is not None
            assert ticket.description == "Billing statement has an incorrect charge."
            assert ticket.priority == "medium"
            assert ticket.category == "billing"
            assert ticket.slack_channel is not None
            assert ticket.slack_ts is not None


def test_whatsapp_webhook_deduplication():
    """Test that duplicate MessageSids are deduplicated and return 200 without creating extra tickets."""
    with TestClient(app) as client:
        form_data = {
            "From": "whatsapp:+1234567890",
            "Body": "Duplicate test message",
            "MessageSid": "SM_TEST_DEDUP_001",
        }
        # First request
        res1 = client.post("/webhook/wa", data=form_data)
        assert res1.status_code == 200

        # Duplicate request with identical MessageSid
        res2 = client.post("/webhook/wa", data=form_data)
        assert res2.status_code == 200
        assert "<Response/>" in res2.text

        # Verify only 1 record exists
        from app.db import engine
        with Session(engine) as session:
            statement = select(Ticket).where(Ticket.wa_message_sid == "SM_TEST_DEDUP_001")
            results = session.exec(statement).all()
            assert len(results) == 1


def test_whatsapp_webhook_missing_fields():
    """Test webhook with missing required fields returns 400."""
    with TestClient(app) as client:
        response = client.post("/webhook/wa", data={"Body": "Just a body"})
        assert response.status_code == 400


def test_slack_url_verification_challenge():
    """Test Slack URL verification handshake."""
    with TestClient(app) as client:
        payload = {
            "type": "url_verification",
            "token": "Jhj54552434wf55B54WY7F92",
            "challenge": "3eZbrAqagTmVa5xYqiLoAggHGXkWjhfZWfqHfGea-its-a-challenge-token",
        }
        response = client.post("/webhook/slack", json=payload)
        assert response.status_code == 200
        assert response.json()["challenge"] == "3eZbrAqagTmVa5xYqiLoAggHGXkWjhfZWfqHfGea-its-a-challenge-token"


def test_slack_event_callback_thread_reply():
    """Test Slack event callback for agent replying to a ticket thread."""
    with TestClient(app) as client:
        # First create a ticket with a known slack_ts
        from app.db import engine
        with Session(engine) as session:
            ticket = Ticket(
                wa_number="whatsapp:+1987654321",
                wa_message_sid="SM_SLACK_THREAD_TEST",
                slack_channel="C12345678",
                slack_ts="1710000000.123456",
                title="Slack Thread Test",
                status="open",
            )
            session.add(ticket)
            session.commit()

        # Send Slack event callback replying in that thread with resolved keyword
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "user": "U12345678",
                "text": "This issue is now resolved and your refund has been processed.",
                "thread_ts": "1710000000.123456",
                "ts": "1710000005.654321",
                "channel": "C12345678",
            },
        }
        response = client.post("/webhook/slack", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        # Verify ticket status updated to closed
        with Session(engine) as session:
            statement = select(Ticket).where(Ticket.wa_message_sid == "SM_SLACK_THREAD_TEST")
            updated_ticket = session.exec(statement).first()
            assert updated_ticket is not None
            assert updated_ticket.status == "closed"


def test_twilio_signature_verification_enforced():
    """Test that Twilio signature validation rejects missing/invalid signatures when DEBUG=False."""
    settings = get_settings()
    settings.DEBUG = False
    settings.TWILIO_AUTH_TOKEN = "real_secret_token"

    with TestClient(app) as client:
        form_data = {
            "From": "whatsapp:+1234567890",
            "Body": "Test message without signature",
            "MessageSid": "SM_SIG_TEST_001",
        }
        # Missing signature
        response = client.post("/webhook/wa", data=form_data)
        assert response.status_code == 403

        # Invalid signature
        headers = {"X-Twilio-Signature": "invalid_signature_hash"}
        response = client.post("/webhook/wa", data=form_data, headers=headers)
        assert response.status_code == 403

    # Reset debug mode
    settings.DEBUG = True


def test_slack_signature_verification_enforced():
    """Test that Slack signature validation rejects invalid signatures when DEBUG=False."""
    settings = get_settings()
    settings.DEBUG = False
    settings.SLACK_SIGNING_SECRET = "my_slack_secret"

    with TestClient(app) as client:
        payload = {"type": "url_verification", "challenge": "challenge_token"}
        # Missing headers
        response = client.post("/webhook/slack", json=payload)
        assert response.status_code == 403

        # Invalid signature
        headers = {
            "X-Slack-Request-Timestamp": "1710000000",
            "X-Slack-Signature": "v0=invalid_sha256",
        }
        response = client.post("/webhook/slack", json=payload, headers=headers)
        assert response.status_code == 403

    # Reset debug mode
    settings.DEBUG = True


def test_twilio_signature_check_fails_closed_when_unconfigured():
    """If TWILIO_AUTH_TOKEN is unset outside DEBUG, requests must be rejected, not passed through."""
    settings = get_settings()
    settings.DEBUG = False
    original_token = settings.TWILIO_AUTH_TOKEN
    settings.TWILIO_AUTH_TOKEN = ""

    with TestClient(app) as client:
        form_data = {
            "From": "whatsapp:+1234567890",
            "Body": "Test message",
            "MessageSid": "SM_UNCONFIGURED_TEST",
        }
        response = client.post("/webhook/wa", data=form_data)
        assert response.status_code == 403

    settings.DEBUG = True
    settings.TWILIO_AUTH_TOKEN = original_token


def test_slack_signature_check_fails_closed_when_unconfigured():
    """If SLACK_SIGNING_SECRET is unset outside DEBUG, requests must be rejected, not passed through."""
    settings = get_settings()
    settings.DEBUG = False
    original_secret = settings.SLACK_SIGNING_SECRET
    settings.SLACK_SIGNING_SECRET = ""

    with TestClient(app) as client:
        payload = {"type": "url_verification", "challenge": "challenge_token"}
        response = client.post("/webhook/slack", json=payload)
        assert response.status_code == 403

    settings.DEBUG = True
    settings.SLACK_SIGNING_SECRET = original_secret


def test_slack_event_dedup_on_retry():
    """A Slack event delivered twice with the same event_id should only be processed once."""
    with TestClient(app) as client:
        from app.db import engine
        with Session(engine) as session:
            ticket = Ticket(
                wa_number="whatsapp:+1987654321",
                wa_message_sid="SM_DEDUP_TEST",
                slack_channel="C12345678",
                slack_ts="1720000000.123456",
                title="Dedup Test",
                status="open",
            )
            session.add(ticket)
            session.commit()

        payload = {
            "type": "event_callback",
            "event_id": "Ev_DUPLICATE_001",
            "event": {
                "type": "message",
                "user": "U12345678",
                "text": "Resolved, all set.",
                "thread_ts": "1720000000.123456",
                "ts": "1720000005.654321",
                "channel": "C12345678",
            },
        }
        first = client.post("/webhook/slack", json=payload)
        second = client.post("/webhook/slack", json=payload)
        assert first.status_code == 200
        assert second.status_code == 200

        from app.models import SlackEvent
        with Session(engine) as session:
            events = session.exec(
                select(SlackEvent).where(SlackEvent.event_id == "Ev_DUPLICATE_001")
            ).all()
            assert len(events) == 1


def test_format_ticket_history_includes_priority_category_description():
    """format_ticket_history should surface priority/category/description, not just title."""
    from app.db import format_ticket_history

    ticket = Ticket(
        wa_number="whatsapp:+1111111111",
        wa_message_sid="SM_HISTORY_TEST",
        title="Cannot log in",
        description="User locked out after password reset.",
        priority="high",
        category="access",
        status="open",
    )
    formatted = format_ticket_history([ticket])
    assert "Cannot log in" in formatted
    assert "Priority: high" in formatted
    assert "Category: access" in formatted
    assert "User locked out after password reset." in formatted


def test_format_ticket_history_empty():
    """No prior tickets should produce a clear 'new user' message, not blow up on None fields."""
    from app.db import format_ticket_history

    assert format_ticket_history([]) == "No prior tickets found (new user)."
