import asyncio
import os
import httpx
from dotenv import load_dotenv
from sqlmodel import Session, select, create_engine

load_dotenv()

from app.models import Ticket

BASE_URL = "http://localhost:8000"


def print_db_tickets():
    engine = create_engine("sqlite:///./tickets.db")
    with Session(engine) as session:
        tickets = session.exec(select(Ticket).order_by(Ticket.id.desc()).limit(5)).all()
        print("\n--- Recent Tickets in SQLite Database ---")
        for t in reversed(tickets):
            print(f"ID: {t.id} | Status: {t.status:<20} | Title: {t.title} | Channel: {t.slack_channel} | SID: {t.wa_message_sid}")
        print("------------------------------------------\n")


async def main():
    print("=" * 65)
    print("TESTING END-TO-END FLOW: SLACK TICKET CREATION & CLARIFICATION")
    print("=" * 65)

    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # -------------------------------------------------------------------
        # Test Case 1: Clear Issue (needs_clarification = False)
        # Should: Create Slack ticket, insert DB row status='open', send confirmation
        # -------------------------------------------------------------------
        print("\n[Test Case 1] Sending clear technical support request:")
        data1 = {
            "From": "whatsapp:+15551234567",
            "Body": "Our payment webhook is failing with HTTP 504 gateway timeout on customer checkout.",
            "MessageSid": "SM_FLOW_TEST_CLEAR_001",
        }
        res1 = await client.post("/webhook/wa", data=data1)
        print(f"Webhook HTTP Status: {res1.status_code}")
        print(f"Webhook Response XML: {res1.text}")

        # Wait for background task to complete LLM extraction & Slack posting
        print("Waiting 4 seconds for background tasks...")
        await asyncio.sleep(4)
        print_db_tickets()

        # -------------------------------------------------------------------
        # Test Case 2: Vague Message (needs_clarification = True)
        # Should: Send clarification question to WhatsApp, stop (no Slack ticket)
        # -------------------------------------------------------------------
        print("\n[Test Case 2] Sending vague user message:")
        data2 = {
            "From": "whatsapp:+15559876543",
            "Body": "help me please",
            "MessageSid": "SM_FLOW_TEST_VAGUE_002",
        }
        res2 = await client.post("/webhook/wa", data=data2)
        print(f"Webhook HTTP Status: {res2.status_code}")
        print(f"Webhook Response XML: {res2.text}")

        # Wait for background task to complete clarification processing
        print("Waiting 4 seconds for background tasks...")
        await asyncio.sleep(4)
        print_db_tickets()

    print("=" * 65)
    print("FLOW TESTING COMPLETE")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
