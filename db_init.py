"""
db_init.py — Initialize the AVVA Properties SQLite database.
Safe to re-run (all CREATE TABLE IF NOT EXISTS).
"""

import sqlite3
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger("db_init")

DB_PATH = "leads.db"

DDL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    owner_name TEXT,
    property_address TEXT,
    city TEXT,
    zip_code TEXT,
    county TEXT,
    filing_date TEXT,
    extra_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    phone_1 TEXT,
    phone_2 TEXT,
    email_1 TEXT,
    email_2 TEXT,
    mailing_address TEXT,
    skip_traced_at TIMESTAMP,
    raw_response TEXT
);

CREATE TABLE IF NOT EXISTS outreach_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    contact_id INTEGER REFERENCES contacts(id),
    channel TEXT,
    message_body TEXT,
    subject_line TEXT,
    sent_at TIMESTAMP,
    sequence_day INTEGER,
    status TEXT,
    twilio_sid TEXT,
    sendgrid_message_id TEXT
);

CREATE TABLE IF NOT EXISTS deal_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id),
    arv REAL,
    arv_confidence TEXT,
    estimated_repair_cost REAL,
    mao REAL,
    suggested_offer REAL,
    wholesale_fee REAL,
    deal_grade TEXT,
    summary TEXT,
    raw_comps TEXT,
    analyzed_at TIMESTAMP
);
"""


def init_db(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(DDL)
        conn.commit()
        logger.info("All tables created or already exist.")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
