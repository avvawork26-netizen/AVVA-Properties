# AVVA Properties

AVVA Properties is an AI-automated real estate wholesaling pipeline for the Orlando, Florida market — it scrapes motivated seller leads from 4 counties, skip traces owner contact info, sends personalized SMS and email outreach via Claude AI, and surfaces hot deals with instant Telegram alerts and a web dashboard.

## Prerequisites

- Python 3.11+
- Google Chrome installed (for Selenium scraping)
- Active accounts: Anthropic, Twilio, SendGrid, BatchSkipTracing, Telegram

## Setup

```bash
# 1. Clone the repo
git clone <repo-url> avva-properties
cd avva-properties

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# Open .env and fill in all API keys

# 4. Initialize the database
python db_init.py

# 5. Start the dashboard
python dashboard.py
# Open http://localhost:5000
```

## Modules

| File | Purpose |
|---|---|
| `db_init.py` | Creates `leads.db` with all tables — safe to re-run |
| `scraper.py` | Scrapes probate + tax-delinquent leads from Orange, Osceola, Seminole, and Polk county portals |
| `skiptracer.py` | Calls BatchSkipTracing API to find phone/email for all new leads |
| `outreach.py` | Runs 4-touch SMS+email sequence (days 0/3/5/7) using Claude-generated messages via Twilio + SendGrid |
| `analyzer.py` | Performs ARV/MAO deal analysis for hot leads using Zillow comps + Claude API |
| `bot.py` | Telegram bot for managing the pipeline on mobile — commands for listing leads, marking deals, triggering modules |
| `dashboard.py` | Flask web dashboard on port 5000 — overview metrics, hot lead cards, full lead table, live tool streaming |

## Cron Automation

See `cron.sh` for ready-to-paste crontab entries:
- Scraper runs nightly at 2:00 AM
- Skip tracer runs nightly at 2:30 AM
- Outreach runs every morning at 9:00 AM

```bash
crontab -e
# Paste the lines from cron.sh, update the path
```

## TCPA Compliance

All SMS messages automatically include "Reply STOP to opt out" per TCPA requirements. Outreach targets property owners who appear in public county records (probate filings, tax delinquent lists). This software is intended for use by licensed or exempt real estate professionals. Consult an attorney for compliance guidance in your jurisdiction.
