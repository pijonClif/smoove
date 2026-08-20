import json
import logging
from typing import Optional
from groq import AsyncGroq

from app.config import get_settings
from app.models import TicketExtraction

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are a ticket-extraction assistant for a WhatsApp-to-Slack support bot.
You may be provided with previous support tickets from tickets.db for this user.
Use the database history to understand context, identify if this is a follow-up to an existing open ticket, and avoid asking redundant clarification questions if the context is already known.

Output ONLY valid JSON:
{
  "title": "<short imperative summary, <10 words>",
  "description": "<cleaned up description>",
  "priority": "low" | "medium" | "high" | "urgent",
  "category": "<billing/technical/access/other>",
  "needs_clarification": true | false,
  "clarification_question": "<only if needs_clarification, else null>",
  "is_followup": true | false,
  "related_ticket_id": <integer ticket ID if this relates to a prior ticket in history, else null>
}
Rules:
1. Vague message with no prior context -> needs_clarification=true + one specific question.
2. If the user message is clearly following up on an existing open ticket from history -> is_followup=true, related_ticket_id=<ticket_id>, needs_clarification=false.
3. Never invent details.
4. Priority=medium unless urgency is explicit.
5. Title must be terse and action-oriented."""

SUMMARIZE_SYSTEM_PROMPT = """Convert a Slack thread update into a short WhatsApp message for a non-technical 
user. 1-2 sentences, plain language, no markdown. If internal note with no 
user-facing info, output exactly: SKIP"""

DEFAULT_CLARIFICATION_FALLBACK = TicketExtraction(
    title="Clarification Needed",
    description="Unable to parse or extract structured details from the message.",
    priority="medium",
    category="other",
    needs_clarification=True,
    clarification_question="Could you please provide more details about the issue you are experiencing so we can assist you better?",
    is_followup=False,
    related_ticket_id=None,
)


def _fallback_extraction(raw_text: str) -> TicketExtraction:
    fallback = DEFAULT_CLARIFICATION_FALLBACK.model_copy()
    if raw_text.strip():
        fallback.description = raw_text.strip()
    return fallback


def _get_groq_client() -> Optional[AsyncGroq]:
    """Retrieve an AsyncGroq client if API key is configured."""
    settings = get_settings()
    if not settings.GROQ_API_KEY:
        logger.warning("GROQ_API_KEY is not configured in settings.")
        return None
    return AsyncGroq(api_key=settings.GROQ_API_KEY)


async def extract_ticket(raw_text: str, ticket_history: Optional[str] = None) -> TicketExtraction:
    """Extract structured ticket details from raw WhatsApp message and tickets.db history using Groq LLM.
    
    Tries primary model 'openai/gpt-oss-120b' with fallback to 'llama-3.3-70b-versatile'.
    On parsing/JSON failure, falls back to needs_clarification=True with a generic question.
    """
    client = _get_groq_client()
    if not client:
        logger.warning("No Groq client available; using default fallback ticket extraction.")
        return _fallback_extraction(raw_text)

    models_to_try = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    raw_response_content: Optional[str] = None

    if ticket_history and ticket_history.strip():
        user_content = f"User's Prior Ticket History (from tickets.db):\n{ticket_history}\n\nCurrent Incoming WhatsApp Message:\n{raw_text}"
    else:
        user_content = raw_text

    for model_name in models_to_try:
        try:
            logger.info("Attempting ticket extraction using model: %s", model_name)
            completion = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
            raw_response_content = completion.choices[0].message.content
            if raw_response_content:
                logger.info("Received extraction response from model %s", model_name)
                break
        except Exception as exc:
            logger.warning(
                "Ticket extraction failed with model %s: %s. Attempting fallback if available.",
                model_name,
                exc,
            )

    if not raw_response_content:
        logger.error("All LLM models failed to return content for ticket extraction.")
        return _fallback_extraction(raw_text)

    # Parse into TicketExtraction; on json.loads/validation failure, return fallback
    try:
        data = json.loads(raw_response_content)
        return TicketExtraction.model_validate(data)
    except Exception as parse_exc:
        logger.error(
            "Failed to parse LLM JSON response (%s): %s. Falling back to clarification ticket.",
            raw_response_content,
            parse_exc,
        )
        fallback = DEFAULT_CLARIFICATION_FALLBACK.model_copy()
        if raw_text.strip():
            fallback.description = raw_text.strip()
        return fallback


async def summarize_update(slack_text: str, ticket_title: str) -> Optional[str]:
    # uses openai/gpt-oss-120b on groq, returns None if it decides the update is
    # internal-only (agent said SKIP)
    client = _get_groq_client()
    if not client:
        logger.warning("No Groq client available; skipping Slack update summarization.")
        return None

    try:
        user_message = f"Ticket Title: {ticket_title}\nSlack Update: {slack_text}"
        completion = await client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
        )

        content = completion.choices[0].message.content
        if not content:
            return None

        clean_text = content.strip().strip('"').strip("'")
        if clean_text.upper() == "SKIP" or clean_text.upper().startswith("SKIP"):
            logger.info("Slack update classified as SKIP / internal note.")
            return None

        return clean_text

    except Exception as exc:
        logger.exception("Failed to summarize Slack update with Groq: %s", exc)
        return None
