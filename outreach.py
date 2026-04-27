"""
outreach.py — Multi-touch SMS + email outreach for skip-traced leads.
Safe to run daily — checks outreach_log to avoid double-sends.
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import anthropic
import sendgrid
from dotenv import load_dotenv
from sendgrid.helpers.mail import Mail
from twilio.rest import Client as TwilioClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger("outreach")

DB_PATH = "leads.db"

SEQUENCE = [
    {"day": 0, "channels": ["sms", "email"]},
    {"day": 3, "channels": ["sms"]},
    {"day": 5, "channels": ["email"]},
    {"day": 7, "channels": ["sms"]},
]

CLAUDE_SYSTEM = (
    "You are a respectful real estate investor writing outreach for AVVA Properties, "
    "a home buying company in Orlando Florida. Write genuine, human-sounding messages. "
    "Never be pushy or salesy. Never mention this message was AI-generated. "
    "For probate leads, open with genuine condolences before any business mention. "
    "Always be concise."
)


# ---------------------------------------------------------------------------
# Message generation
# ---------------------------------------------------------------------------

def _generate_sms(source: str, owner_name: str, address: str, county: str) -> str:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    source_label = "inherited property" if source == "probate" else "your property"
    prompt = (
        f"Write a single SMS message under 140 characters for a real estate buyer reaching out "
        f"about {source_label} at {address} in {county.title()} County FL. "
        f"Owner name: {owner_name}. Source type: {source}. "
        "Do not include the opt-out line — it will be appended automatically. "
        "Return only the message text, nothing else."
    )
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=200,
        system=CLAUDE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _generate_email(source: str, owner_name: str, address: str, county: str) -> tuple[str, str]:
    """Returns (subject, body)."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    source_label = "inherited property" if source == "probate" else "your property"
    prompt = (
        f"Write an outreach email for a real estate buyer interested in {source_label} "
        f"at {address} in {county.title()} County FL. Owner: {owner_name}. Source: {source}. "
        "Format: first line must be 'Subject: <subject line here>' then a blank line then 3 short paragraphs. "
        "Warm opener, value proposition, soft call to action. Return only the formatted email, nothing else."
    )
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=600,
        system=CLAUDE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    lines = raw.split("\n", 2)
    subject = lines[0].replace("Subject:", "").strip() if lines else "A question about your property"
    body = "\n".join(lines[2:]).strip() if len(lines) > 2 else raw
    return subject, body


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------

def _send_sms(to_number: str, body: str) -> tuple[str, str]:
    """Returns (status, twilio_sid)."""
    client = TwilioClient(
        os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
    )
    full_body = f"{body}\nReply STOP to opt out"
    # Trim to 160 chars total
    if len(full_body) > 160:
        trim = 160 - len("\nReply STOP to opt out")
        full_body = body[:trim] + "\nReply STOP to opt out"
    msg = client.messages.create(
        from_=os.getenv("TWILIO_FROM_NUMBER"),
        to=to_number,
        body=full_body,
    )
    return "sent", msg.sid


def _send_email(to_email: str, subject: str, body: str) -> tuple[str, str]:
    """Returns (status, message_id)."""
    sg = sendgrid.SendGridAPIClient(api_key=os.getenv("SENDGRID_API_KEY"))
    html_body = body.replace("\n", "<br>")
    mail = Mail(
        from_email=(os.getenv("OUTREACH_FROM_EMAIL"), "AVVA Properties"),
        to_emails=to_email,
        subject=subject,
        html_content=html_body,
    )
    resp = sg.send(mail)
    msg_id = resp.headers.get("X-Message-Id", "")
    return "sent", msg_id


# ---------------------------------------------------------------------------
# Sequence logic
# ---------------------------------------------------------------------------

def _get_days_since_first_contact(conn: sqlite3.Connection, lead_id: int) -> Optional[int]:
    cur = conn.execute(
        "SELECT sent_at FROM outreach_log WHERE lead_id = ? AND sequence_day = 0 ORDER BY sent_at LIMIT 1",
        (lead_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    first_contact = datetime.fromisoformat(row[0])
    return (datetime.now() - first_contact).days


def _already_sent(conn: sqlite3.Connection, lead_id: int, day: int, channel: str) -> bool:
    cur = conn.execute(
        "SELECT id FROM outreach_log WHERE lead_id = ? AND sequence_day = ? AND channel = ? AND status = 'sent'",
        (lead_id, day, channel),
    )
    return cur.fetchone() is not None


def _has_replied(conn: sqlite3.Connection, lead_id: int) -> bool:
    cur = conn.execute(
        "SELECT id FROM outreach_log WHERE lead_id = ? AND status = 'replied'",
        (lead_id,),
    )
    return cur.fetchone() is not None


def _log_outreach(
    conn: sqlite3.Connection,
    lead_id: int,
    contact_id: int,
    channel: str,
    message_body: str,
    subject_line: str,
    sequence_day: int,
    status: str,
    twilio_sid: str = "",
    sendgrid_id: str = "",
) -> None:
    conn.execute(
        """INSERT INTO outreach_log
           (lead_id, contact_id, channel, message_body, subject_line, sent_at,
            sequence_day, status, twilio_sid, sendgrid_message_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            lead_id,
            contact_id,
            channel,
            message_body,
            subject_line,
            datetime.now().isoformat(),
            sequence_day,
            status,
            twilio_sid,
            sendgrid_id,
        ),
    )
    conn.commit()


def _process_lead(conn: sqlite3.Connection, lead: dict) -> None:
    lead_id = lead["id"]

    if _has_replied(conn, lead_id):
        logger.info("Lead %d has replied — skipping sequence", lead_id)
        return

    cur = conn.execute(
        "SELECT id, phone_1, email_1 FROM contacts WHERE lead_id = ?", (lead_id,)
    )
    contact_row = cur.fetchone()
    if not contact_row:
        logger.warning("Lead %d has no contact record", lead_id)
        return

    contact_id, phone, email = contact_row
    days_since = _get_days_since_first_contact(conn, lead_id)

    for step in SEQUENCE:
        day = step["day"]

        # Day 0: always eligible (first touch)
        if day == 0 and days_since is None:
            pass  # eligible
        elif day == 0 and days_since is not None:
            continue  # already started sequence
        elif days_since is None:
            continue  # haven't started yet
        elif days_since < day:
            continue  # not due yet
        elif days_since > day + 1:
            continue  # missed window, skip

        for channel in step["channels"]:
            if _already_sent(conn, lead_id, day, channel):
                continue

            try:
                if channel == "sms":
                    if not phone:
                        logger.info("Lead %d — no phone, skipping SMS day %d", lead_id, day)
                        continue
                    sms_body = _generate_sms(
                        lead["source"], lead["owner_name"],
                        lead["property_address"], lead["county"]
                    )
                    status, twilio_sid = _send_sms(phone, sms_body)
                    _log_outreach(conn, lead_id, contact_id, "sms", sms_body, "", day, status, twilio_sid=twilio_sid)
                    # Update lead status to contacted
                    conn.execute("UPDATE leads SET status = 'contacted' WHERE id = ? AND status = 'skip_traced'", (lead_id,))
                    conn.commit()
                    logger.info("Lead %d — SMS day %d sent (%s)", lead_id, day, twilio_sid)

                elif channel == "email":
                    if not email:
                        logger.info("Lead %d — no email, skipping email day %d", lead_id, day)
                        continue
                    subject, body = _generate_email(
                        lead["source"], lead["owner_name"],
                        lead["property_address"], lead["county"]
                    )
                    status, msg_id = _send_email(email, subject, body)
                    _log_outreach(conn, lead_id, contact_id, "email", body, subject, day, status, sendgrid_id=msg_id)
                    conn.execute("UPDATE leads SET status = 'contacted' WHERE id = ? AND status = 'skip_traced'", (lead_id,))
                    conn.commit()
                    logger.info("Lead %d — email day %d sent (%s)", lead_id, day, msg_id)

            except Exception as e:
                logger.error("Lead %d — %s send error day %d: %s", lead_id, channel, day, e)
                _log_outreach(conn, lead_id, contact_id, channel, "", "", day, "failed")


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT id, source, owner_name, property_address, city, county "
            "FROM leads WHERE status IN ('skip_traced', 'contacted')"
        )
        leads = [
            {"id": r[0], "source": r[1], "owner_name": r[2],
             "property_address": r[3], "city": r[4], "county": r[5]}
            for r in cur.fetchall()
        ]
        logger.info("Processing outreach for %d leads", len(leads))
        for lead in leads:
            try:
                _process_lead(conn, lead)
            except Exception as e:
                logger.error("Unhandled error for lead %d: %s", lead["id"], e)
                try:
                    import bot
                    bot.notify_error(f"Outreach error for lead {lead['id']}: {e}")
                except Exception:
                    pass
    finally:
        conn.close()


def run_for_lead(lead_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT id, source, owner_name, property_address, city, county FROM leads WHERE id = ?",
            (lead_id,),
        )
        row = cur.fetchone()
        if not row:
            logger.error("Lead %d not found", lead_id)
            return
        lead = {"id": row[0], "source": row[1], "owner_name": row[2],
                "property_address": row[3], "city": row[4], "county": row[5]}
        _process_lead(conn, lead)
    finally:
        conn.close()


if __name__ == "__main__":
    run()
