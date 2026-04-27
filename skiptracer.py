"""
skiptracer.py — Skip trace leads using BatchSkipTracing API.
Processes all leads with status='new' in batches of 10.
"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger("skiptracer")

DB_PATH = "leads.db"
API_URL = "https://api.batchskiptracing.com/api"
BATCH_SIZE = 10


def _get_new_leads(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT id, owner_name, property_address FROM leads WHERE status = 'new'"
    )
    rows = cur.fetchall()
    return [{"id": r[0], "owner_name": r[1], "property_address": r[2]} for r in rows]


def _call_api(batch: list[dict]) -> list[dict]:
    api_key = os.getenv("BATCH_SKIP_TRACING_API_KEY", "")
    if not api_key:
        raise ValueError("BATCH_SKIP_TRACING_API_KEY not set")

    payload = {
        "leads": [
            {"name": lead["owner_name"], "address": lead["property_address"]}
            for lead in batch
        ]
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("results", [])


def _save_contact(conn: sqlite3.Connection, lead_id: int, result: dict) -> None:
    conn.execute(
        """INSERT INTO contacts
           (lead_id, phone_1, phone_2, email_1, email_2, mailing_address,
            skip_traced_at, raw_response)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            lead_id,
            result.get("phone1"),
            result.get("phone2"),
            result.get("email1"),
            result.get("email2"),
            result.get("mailing_address"),
            datetime.now().isoformat(),
            json.dumps(result),
        ),
    )


def _update_lead_status(conn: sqlite3.Connection, lead_id: int, status: str) -> None:
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        leads = _get_new_leads(conn)
        logger.info("Found %d leads to skip trace", len(leads))

        batches = [leads[i : i + BATCH_SIZE] for i in range(0, len(leads), BATCH_SIZE)]

        for batch_idx, batch in enumerate(batches):
            logger.info("Processing batch %d/%d", batch_idx + 1, len(batches))
            try:
                results = _call_api(batch)
                for lead, result in zip(batch, results):
                    lead_id = lead["id"]
                    try:
                        has_contact = result.get("phone1") or result.get("email1")
                        if has_contact:
                            _save_contact(conn, lead_id, result)
                            _update_lead_status(conn, lead_id, "skip_traced")
                            logger.info("Lead %d skip traced successfully", lead_id)
                        else:
                            _update_lead_status(conn, lead_id, "dead")
                            logger.info("Lead %d — no results, marking dead", lead_id)
                        conn.commit()
                    except Exception as e:
                        logger.error("Error saving lead %d contact: %s", lead_id, e)
            except Exception as e:
                logger.error("API error on batch %d: %s", batch_idx + 1, e)
                # Leave leads in 'new' status so they retry next run

            if batch_idx < len(batches) - 1:
                time.sleep(1)

        logger.info("Skip tracing complete")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
