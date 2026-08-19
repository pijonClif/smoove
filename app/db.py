from contextlib import contextmanager
from typing import Generator, Optional
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import get_settings
from app.models import Ticket

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)


def init_db() -> None:
    """Initialize database tables."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency for obtaining a database session."""
    with Session(engine) as session:
        yield session


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """Context manager for obtaining a database session in background tasks."""
    with Session(engine) as session:
        yield session


def get_ticket_by_message_sid(session: Session, message_sid: str) -> Optional[Ticket]:
    """Retrieve an existing ticket by Twilio MessageSid."""
    statement = select(Ticket).where(Ticket.wa_message_sid == message_sid)
    return session.exec(statement).first()


def create_initial_ticket(session: Session, wa_number: str, wa_message_sid: str) -> Ticket:
    """Create and persist an initial ticket record."""
    ticket = Ticket(
        wa_number=wa_number,
        wa_message_sid=wa_message_sid,
        status="open",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def get_ticket_history(
    session: Session,
    wa_number: str,
    limit: int = 5,
    exclude_sid: Optional[str] = None,
) -> list[Ticket]:
    """Retrieve recent ticket history for a specific WhatsApp number from tickets.db."""
    statement = (
        select(Ticket)
        .where(Ticket.wa_number == wa_number)
        .order_by(Ticket.id.desc())
        .limit(limit)
    )
    if exclude_sid:
        statement = statement.where(Ticket.wa_message_sid != exclude_sid)
    return list(session.exec(statement).all())


def format_ticket_history(tickets: list[Ticket]) -> str:
    """Format list of Ticket records into readable text context for LLM prompt."""
    if not tickets:
        return "No prior tickets found (new user)."

    lines = []
    for t in tickets:
        created = t.created_at.strftime("%Y-%m-%d %H:%M UTC") if t.created_at else "N/A"
        lines.append(
            f"- Ticket #{t.id} [{t.status.upper()}] (Created: {created}): "
            f"Title: \"{t.title or 'Pending'}\" | Slack Channel: {t.slack_channel or 'N/A'}"
        )
    return "\n".join(lines)


def update_ticket_details(
    session: Session,
    ticket_id: int,
    title: Optional[str] = None,
    slack_channel: Optional[str] = None,
    slack_ts: Optional[str] = None,
    status: Optional[str] = None,
) -> Optional[Ticket]:
    """Update ticket with LLM extraction and Slack reference metadata."""
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        return None

    if title is not None:
        ticket.title = title
    if slack_channel is not None:
        ticket.slack_channel = slack_channel
    if slack_ts is not None:
        ticket.slack_ts = slack_ts
    if status is not None:
        ticket.status = status

    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket
