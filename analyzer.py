"""
analyzer.py — Deal analysis for hot leads using Zillow scraping + Claude API.
"""

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime

import anthropic
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger("analyzer")

DB_PATH = "leads.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CLAUDE_SYSTEM = (
    "You are an expert real estate investor and wholesaler specializing in the Orlando Florida market."
)

ANALYSIS_PROMPT_TEMPLATE = """
You are analyzing a potential wholesale real estate deal. Here is the property data:

Property Address: {address}
Zestimate: {zestimate}
Beds: {beds} | Baths: {baths} | Sqft: {sqft}
Year Built: {year_built}
Last Sale Price: {last_sale_price} | Last Sale Date: {last_sale_date}

Comparable Sales (within ~0.5 miles):
{comps_text}

Based on this data, provide a deal analysis. Return ONLY valid JSON with no extra text, markdown, or explanation:
{{
  "arv": <number>,
  "arv_confidence": "<low|medium|high>",
  "estimated_repair_cost": <number>,
  "repair_notes": "<string>",
  "mao": <number>,
  "suggested_offer": <number>,
  "wholesale_fee": <number>,
  "deal_grade": "<A|B|C|D>",
  "summary": "<string max 2 sentences>"
}}

MAO formula: ARV * 0.70 - estimated_repair_cost
suggested_offer: MAO - wholesale_fee (default wholesale_fee = 15000 unless deal size suggests otherwise)
Deal grade: A = great deal, B = good deal, C = marginal, D = pass
"""


# ---------------------------------------------------------------------------
# Zillow scraping
# ---------------------------------------------------------------------------

def _zillow_search_url(address: str) -> str:
    encoded = requests.utils.quote(address)
    return f"https://www.zillow.com/homes/{encoded}_rb/"


def _scrape_subject_property(address: str) -> dict:
    url = _zillow_search_url(address)
    logger.info("Fetching Zillow page: %s", url)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")

        data: dict = {
            "zestimate": None,
            "beds": None,
            "baths": None,
            "sqft": None,
            "year_built": None,
            "last_sale_price": None,
            "last_sale_date": None,
        }

        # Zillow embeds data in a __NEXT_DATA__ JSON blob
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script:
            try:
                page_data = json.loads(script.string)
                props = (
                    page_data.get("props", {})
                    .get("pageProps", {})
                    .get("componentProps", {})
                    .get("gdpClientCache", {})
                )
                if props:
                    first_key = next(iter(props))
                    prop = props[first_key].get("property", {})
                    data["zestimate"] = prop.get("zestimate")
                    data["beds"] = prop.get("bedrooms")
                    data["baths"] = prop.get("bathrooms")
                    data["sqft"] = prop.get("livingArea")
                    data["year_built"] = prop.get("yearBuilt")
                    price_history = prop.get("priceHistory", [])
                    if price_history:
                        latest = price_history[0]
                        data["last_sale_price"] = latest.get("price")
                        data["last_sale_date"] = latest.get("date")
            except Exception as e:
                logger.debug("JSON parse from NEXT_DATA failed: %s", e)

        return data
    except Exception as e:
        logger.error("Zillow subject property scrape failed: %s", e)
        return {}


def _scrape_comps(address: str) -> list[dict]:
    url = _zillow_search_url(address + " recently sold")
    logger.info("Fetching Zillow comps: %s", url)
    comps = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")

        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script:
            page_data = json.loads(script.string)
            listings = (
                page_data.get("props", {})
                .get("pageProps", {})
                .get("searchPageState", {})
                .get("cat1", {})
                .get("searchResults", {})
                .get("listResults", [])
            )
            for item in listings[:5]:
                comps.append({
                    "address": item.get("address", ""),
                    "sale_price": item.get("price"),
                    "sqft": item.get("area"),
                    "beds": item.get("beds"),
                    "baths": item.get("baths"),
                    "sale_date": item.get("lastSoldDate", ""),
                })
    except Exception as e:
        logger.error("Zillow comps scrape failed: %s", e)
    return comps


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

def _format_comps_text(comps: list[dict]) -> str:
    if not comps:
        return "No recent comparable sales found."
    lines = []
    for c in comps:
        lines.append(
            f"- {c.get('address', 'N/A')}: ${c.get('sale_price', 'N/A'):,} | "
            f"{c.get('beds', '?')}bd/{c.get('baths', '?')}ba | "
            f"{c.get('sqft', '?')} sqft | sold {c.get('sale_date', 'N/A')}"
        )
    return "\n".join(lines)


def _call_claude(prop: dict, comps: list[dict]) -> dict:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        address=prop.get("address", ""),
        zestimate=f"${prop.get('zestimate', 'N/A'):,}" if prop.get("zestimate") else "N/A",
        beds=prop.get("beds", "N/A"),
        baths=prop.get("baths", "N/A"),
        sqft=prop.get("sqft", "N/A"),
        year_built=prop.get("year_built", "N/A"),
        last_sale_price=f"${prop.get('last_sale_price', 'N/A'):,}" if prop.get("last_sale_price") else "N/A",
        last_sale_date=prop.get("last_sale_date", "N/A"),
        comps_text=_format_comps_text(comps),
    )

    def _attempt() -> dict:
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=800,
            system=CLAUDE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())

    try:
        return _attempt()
    except json.JSONDecodeError as e:
        logger.warning("Claude returned malformed JSON, retrying: %s", e)
        try:
            return _attempt()
        except Exception as e2:
            logger.error("Claude analysis failed after retry: %s", e2)
            raise


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(lead_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT id, property_address, city, county FROM leads WHERE id = ?", (lead_id,)
        )
        row = cur.fetchone()
        if not row:
            logger.error("Lead %d not found", lead_id)
            return

        _, address, city, county = row
        full_address = f"{address}, {city}, FL"

        prop_data = _scrape_subject_property(full_address)
        prop_data["address"] = full_address
        comps = _scrape_comps(full_address)

        logger.info("Lead %d — property data: %s", lead_id, prop_data)
        logger.info("Lead %d — comps found: %d", lead_id, len(comps))

        analysis = _call_claude(prop_data, comps)

        conn.execute(
            """INSERT INTO deal_analysis
               (lead_id, arv, arv_confidence, estimated_repair_cost, mao,
                suggested_offer, wholesale_fee, deal_grade, summary, raw_comps, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                lead_id,
                analysis.get("arv"),
                analysis.get("arv_confidence"),
                analysis.get("estimated_repair_cost"),
                analysis.get("mao"),
                analysis.get("suggested_offer"),
                analysis.get("wholesale_fee"),
                analysis.get("deal_grade"),
                analysis.get("summary"),
                json.dumps(comps),
                datetime.now().isoformat(),
            ),
        )
        conn.execute("UPDATE leads SET status = 'hot' WHERE id = ?", (lead_id,))
        conn.commit()
        logger.info("Lead %d analysis complete — grade %s", lead_id, analysis.get("deal_grade"))

    except Exception as e:
        logger.error("Analysis failed for lead %d: %s", lead_id, e)
        try:
            import bot
            bot.notify_error(f"Analyzer error for lead {lead_id}: {e}")
        except Exception:
            pass
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyzer.py <lead_id>")
        sys.exit(1)
    run(int(sys.argv[1]))
