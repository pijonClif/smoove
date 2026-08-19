from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Field, SQLModel


def get_utc_now() -> datetime:
    """Return timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class Ticket(SQLModel, table=True):
    """Database model for WhatsApp support tickets bridged to Slack."""

    __tablename__ = "tickets"

    id: Optional[int] = Field(default=None, primary_key=True)
    wa_number: str = Field(index=True, description="WhatsApp sender phone number")
    wa_message_sid: str = Field(unique=True, index=True, description="Twilio MessageSid for deduplication")
    slack_channel: Optional[str] = Field(default=None, description="Slack channel ID where ticket is posted")
    slack_ts: Optional[str] = Field(default=None, description="Slack message timestamp for thread replies")
    title: Optional[str] = Field(default=None, description="Extracted ticket title")
    status: str = Field(default="open", description="Ticket status: open, in_progress, resolved, closed")
    created_at: datetime = Field(default_factory=get_utc_now, description="UTC timestamp of creation")


class TicketExtraction(BaseModel):
    """Structured extraction model for LLM analysis of inbound WhatsApp message."""

    title: str = PydanticField(..., description="Short imperative summary, <10 words")
    description: str = PydanticField(..., description="Cleaned up description")
    priority: str = PydanticField(default="medium", description="Priority level: low, medium, high, urgent")
    category: str = PydanticField(default="other", description="Category: billing, technical, access, other")
    needs_clarification: bool = PydanticField(default=False, description="Whether the message is vague and needs clarification")
    clarification_question: Optional[str] = PydanticField(default=None, description="Specific follow-up question if clarification is needed")
    is_followup: bool = PydanticField(default=False, description="Whether this message is a follow-up to a previous ticket from tickets.db")
    related_ticket_id: Optional[int] = PydanticField(default=None, description="ID of related ticket from tickets.db if identified")
