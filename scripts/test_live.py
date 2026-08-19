import asyncio
import os
from dotenv import load_dotenv

# Load real environment variables from .env
load_dotenv()

from app.services.llm import extract_ticket, summarize_update


async def run_live_tests():
    print("=" * 60)
    print("Testing Live LLM Service with Groq (Model: openai/gpt-oss-120b)")
    print("=" * 60)

    # Test 1: Clear Billing Issue
    msg1 = "Hi, I was charged $49 twice for my monthly subscription today. Please refund the duplicate transaction."
    print(f"\n[Test 1] Inbound message: '{msg1}'")
    extraction1 = await extract_ticket(msg1)
    print(f"Title:                 {extraction1.title}")
    print(f"Description:           {extraction1.description}")
    print(f"Priority:              {extraction1.priority}")
    print(f"Category:              {extraction1.category}")
    print(f"Needs Clarification:   {extraction1.needs_clarification}")
    print(f"Clarification Q:       {extraction1.clarification_question}")

    # Test 2: Vague Message
    msg2 = "ayudame por favor"
    print(f"\n[Test 2] Inbound message: '{msg2}'")
    extraction2 = await extract_ticket(msg2)
    print(f"Title:                 {extraction2.title}")
    print(f"Description:           {extraction2.description}")
    print(f"Priority:              {extraction2.priority}")
    print(f"Category:              {extraction2.category}")
    print(f"Needs Clarification:   {extraction2.needs_clarification}")
    print(f"Clarification Q:       {extraction2.clarification_question}")

    # Test 3: Technical Urgent Issue
    msg3 = "CRITICAL: Our webhook integration is down and throwing 500 errors on all checkout orders!"
    print(f"\n[Test 3] Inbound message: '{msg3}'")
    extraction3 = await extract_ticket(msg3)
    print(f"Title:                 {extraction3.title}")
    print(f"Description:           {extraction3.description}")
    print(f"Priority:              {extraction3.priority}")
    print(f"Category:              {extraction3.category}")
    print(f"Needs Clarification:   {extraction3.needs_clarification}")
    print(f"Clarification Q:       {extraction3.clarification_question}")

    # Test 4: Follow-up message with tickets.db history context
    msg4 = "Hey, has this been resolved yet? Still waiting on my refund."
    sample_history = '- Ticket #4 [OPEN] (2026-08-19 06:40 UTC): Title: "Resolve double subscription charge" | Slack Channel: #support-tickets'
    print(f"\n[Test 4 - With tickets.db context] Inbound message: '{msg4}'")
    print(f"History context passed:\n{sample_history}")
    extraction4 = await extract_ticket(raw_text=msg4, ticket_history=sample_history)
    print(f"Title:                 {extraction4.title}")
    print(f"Description:           {extraction4.description}")
    print(f"Priority:              {extraction4.priority}")
    print(f"Is Follow-up:          {extraction4.is_followup}")
    print(f"Related Ticket ID:     {extraction4.related_ticket_id}")
    print(f"Needs Clarification:   {extraction4.needs_clarification}")

    # Test 5: Summarize Slack Update (User-Facing)
    slack_update = "Hey team, I verified the Stripe logs and issued a full refund for the $49 charge. Transaction ID: ch_3Nxxx."
    print(f"\n[Test 5] Slack message: '{slack_update}'")
    summary = await summarize_update(slack_text=slack_update, ticket_title=extraction1.title)
    print(f"WhatsApp Summary:      '{summary}'")

    # Test 6: Summarize Slack Internal Note (Should SKIP)
    internal_note = "Checking k8s ingress logs. Might be an issue with nginx rate limiting rules on node 4."
    print(f"\n[Test 6] Slack internal note: '{internal_note}'")
    internal_summary = await summarize_update(slack_text=internal_note, ticket_title="Webhook 500 error")
    print(f"WhatsApp Summary (None expected): {internal_summary}")

    print("\n" + "=" * 60)
    print("Live Tests Complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_live_tests())
