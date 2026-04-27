"""
scraper.py — Scrape motivated seller leads from 4 Florida county public portals.
Covers: Orange, Osceola, Seminole, Polk — probate and tax-delinquent sources.
"""

import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger("scraper")

DB_PATH = "leads.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------

def _build_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument(f"user-agent={HEADERS['User-Agent']}")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def _random_delay() -> None:
    time.sleep(random.uniform(2, 5))


def _get_soup(driver: webdriver.Chrome, url: str, wait_selector: Optional[str] = None) -> BeautifulSoup:
    driver.get(url)
    if wait_selector:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
        )
    _random_delay()
    return BeautifulSoup(driver.page_source, "lxml")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_existing_addresses(conn: sqlite3.Connection) -> set:
    cur = conn.execute("SELECT property_address FROM leads")
    return {row[0] for row in cur.fetchall()}


def _insert_lead(conn: sqlite3.Connection, lead: dict) -> bool:
    """Insert lead; return True if inserted, False if duplicate."""
    existing = _get_existing_addresses(conn)
    if lead.get("property_address") in existing:
        return False
    conn.execute(
        """INSERT INTO leads
           (source, owner_name, property_address, city, zip_code, county,
            filing_date, extra_data, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')""",
        (
            lead.get("source"),
            lead.get("owner_name"),
            lead.get("property_address"),
            lead.get("city"),
            lead.get("zip_code"),
            lead.get("county"),
            lead.get("filing_date"),
            json.dumps(lead.get("extra_data", {})),
        ),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Orange County scrapers
# ---------------------------------------------------------------------------

def _scrape_orange_probate(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://myorangeclerk.com/divisions/civil/probate"
    logger.info("Scraping Orange County probate: %s", url)
    try:
        soup = _get_soup(driver, url)
        cutoff = datetime.now() - timedelta(days=30)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                case_num = cells[0].get_text(strip=True)
                name = cells[1].get_text(strip=True)
                date_str = cells[2].get_text(strip=True)
                filing_dt = datetime.strptime(date_str, "%m/%d/%Y")
                if filing_dt < cutoff:
                    continue
                leads.append({
                    "source": "probate",
                    "owner_name": name,
                    "property_address": "",
                    "city": "Orlando",
                    "zip_code": "",
                    "county": "orange",
                    "filing_date": date_str,
                    "extra_data": {"case_number": case_num},
                })
            except Exception as e:
                logger.debug("Orange probate row parse error: %s", e)
        logger.info("Orange probate: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Orange probate scrape failed: %s", e)
    return leads


def _scrape_orange_tax(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://www.octaxcol.com/delinquent"
    logger.info("Scraping Orange County tax delinquent: %s", url)
    try:
        soup = _get_soup(driver, url)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                name = cells[0].get_text(strip=True)
                address = cells[1].get_text(strip=True)
                amount = cells[2].get_text(strip=True)
                if not address:
                    continue
                parts = address.rsplit(",", 1)
                city = parts[-1].strip() if len(parts) > 1 else "Orlando"
                leads.append({
                    "source": "tax_delinquent",
                    "owner_name": name,
                    "property_address": address,
                    "city": city,
                    "zip_code": "",
                    "county": "orange",
                    "filing_date": datetime.now().strftime("%m/%d/%Y"),
                    "extra_data": {"amount_owed": amount},
                })
            except Exception as e:
                logger.debug("Orange tax row parse error: %s", e)
        logger.info("Orange tax delinquent: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Orange tax delinquent scrape failed: %s", e)
    return leads


# ---------------------------------------------------------------------------
# Osceola County scrapers
# ---------------------------------------------------------------------------

def _scrape_osceola_probate(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://www.osceolaclerk.com/public-records/case-search"
    logger.info("Scraping Osceola County probate: %s", url)
    try:
        soup = _get_soup(driver, url)
        cutoff = datetime.now() - timedelta(days=30)
        rows = soup.select("table tr, .case-row")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                case_num = cells[0].get_text(strip=True)
                name = cells[1].get_text(strip=True)
                date_str = cells[2].get_text(strip=True)
                filing_dt = datetime.strptime(date_str, "%m/%d/%Y")
                if filing_dt < cutoff:
                    continue
                leads.append({
                    "source": "probate",
                    "owner_name": name,
                    "property_address": "",
                    "city": "Kissimmee",
                    "zip_code": "",
                    "county": "osceola",
                    "filing_date": date_str,
                    "extra_data": {"case_number": case_num},
                })
            except Exception as e:
                logger.debug("Osceola probate row parse error: %s", e)
        logger.info("Osceola probate: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Osceola probate scrape failed: %s", e)
    return leads


def _scrape_osceola_tax(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://www.osceola.county-taxes.com/public/real_estate/delinquent"
    logger.info("Scraping Osceola County tax delinquent: %s", url)
    try:
        soup = _get_soup(driver, url)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                name = cells[0].get_text(strip=True)
                address = cells[1].get_text(strip=True)
                amount = cells[2].get_text(strip=True)
                if not address:
                    continue
                parts = address.rsplit(",", 1)
                city = parts[-1].strip() if len(parts) > 1 else "Kissimmee"
                leads.append({
                    "source": "tax_delinquent",
                    "owner_name": name,
                    "property_address": address,
                    "city": city,
                    "zip_code": "",
                    "county": "osceola",
                    "filing_date": datetime.now().strftime("%m/%d/%Y"),
                    "extra_data": {"amount_owed": amount},
                })
            except Exception as e:
                logger.debug("Osceola tax row parse error: %s", e)
        logger.info("Osceola tax delinquent: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Osceola tax delinquent scrape failed: %s", e)
    return leads


# ---------------------------------------------------------------------------
# Seminole County scrapers
# ---------------------------------------------------------------------------

def _scrape_seminole_probate(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://www.seminoleclerk.org/public-records/case-search"
    logger.info("Scraping Seminole County probate: %s", url)
    try:
        soup = _get_soup(driver, url)
        cutoff = datetime.now() - timedelta(days=30)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                case_num = cells[0].get_text(strip=True)
                name = cells[1].get_text(strip=True)
                date_str = cells[2].get_text(strip=True)
                filing_dt = datetime.strptime(date_str, "%m/%d/%Y")
                if filing_dt < cutoff:
                    continue
                leads.append({
                    "source": "probate",
                    "owner_name": name,
                    "property_address": "",
                    "city": "Sanford",
                    "zip_code": "",
                    "county": "seminole",
                    "filing_date": date_str,
                    "extra_data": {"case_number": case_num},
                })
            except Exception as e:
                logger.debug("Seminole probate row parse error: %s", e)
        logger.info("Seminole probate: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Seminole probate scrape failed: %s", e)
    return leads


def _scrape_seminole_tax(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://www.seminolecounty.tax/delinquent"
    logger.info("Scraping Seminole County tax delinquent: %s", url)
    try:
        soup = _get_soup(driver, url)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                name = cells[0].get_text(strip=True)
                address = cells[1].get_text(strip=True)
                amount = cells[2].get_text(strip=True)
                if not address:
                    continue
                parts = address.rsplit(",", 1)
                city = parts[-1].strip() if len(parts) > 1 else "Sanford"
                leads.append({
                    "source": "tax_delinquent",
                    "owner_name": name,
                    "property_address": address,
                    "city": city,
                    "zip_code": "",
                    "county": "seminole",
                    "filing_date": datetime.now().strftime("%m/%d/%Y"),
                    "extra_data": {"amount_owed": amount},
                })
            except Exception as e:
                logger.debug("Seminole tax row parse error: %s", e)
        logger.info("Seminole tax delinquent: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Seminole tax delinquent scrape failed: %s", e)
    return leads


# ---------------------------------------------------------------------------
# Polk County scrapers
# ---------------------------------------------------------------------------

def _scrape_polk_probate(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://www.polkcountyclerk.net/probate-case-search"
    logger.info("Scraping Polk County probate: %s", url)
    try:
        soup = _get_soup(driver, url)
        cutoff = datetime.now() - timedelta(days=30)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                case_num = cells[0].get_text(strip=True)
                name = cells[1].get_text(strip=True)
                date_str = cells[2].get_text(strip=True)
                filing_dt = datetime.strptime(date_str, "%m/%d/%Y")
                if filing_dt < cutoff:
                    continue
                leads.append({
                    "source": "probate",
                    "owner_name": name,
                    "property_address": "",
                    "city": "Lakeland",
                    "zip_code": "",
                    "county": "polk",
                    "filing_date": date_str,
                    "extra_data": {"case_number": case_num},
                })
            except Exception as e:
                logger.debug("Polk probate row parse error: %s", e)
        logger.info("Polk probate: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Polk probate scrape failed: %s", e)
    return leads


def _scrape_polk_tax(driver: webdriver.Chrome) -> list[dict]:
    leads = []
    url = "https://www.polktaxes.com/delinquent"
    logger.info("Scraping Polk County tax delinquent: %s", url)
    try:
        soup = _get_soup(driver, url)
        rows = soup.select("table tr")
        for row in rows[1:]:
            cells = row.select("td")
            if len(cells) < 3:
                continue
            try:
                name = cells[0].get_text(strip=True)
                address = cells[1].get_text(strip=True)
                amount = cells[2].get_text(strip=True)
                if not address:
                    continue
                parts = address.rsplit(",", 1)
                city = parts[-1].strip() if len(parts) > 1 else "Lakeland"
                leads.append({
                    "source": "tax_delinquent",
                    "owner_name": name,
                    "property_address": address,
                    "city": city,
                    "zip_code": "",
                    "county": "polk",
                    "filing_date": datetime.now().strftime("%m/%d/%Y"),
                    "extra_data": {"amount_owed": amount},
                })
            except Exception as e:
                logger.debug("Polk tax row parse error: %s", e)
        logger.info("Polk tax delinquent: %d raw leads found", len(leads))
    except Exception as e:
        logger.error("Polk tax delinquent scrape failed: %s", e)
    return leads


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run() -> tuple[int, int]:
    """Run all county scrapers. Returns (new_count, duplicate_count)."""
    driver = _build_driver()
    conn = sqlite3.connect(DB_PATH)
    new_count = 0
    dup_count = 0

    scrapers = [
        _scrape_orange_probate,
        _scrape_orange_tax,
        _scrape_osceola_probate,
        _scrape_osceola_tax,
        _scrape_seminole_probate,
        _scrape_seminole_tax,
        _scrape_polk_probate,
        _scrape_polk_tax,
    ]

    try:
        for scraper_fn in scrapers:
            try:
                leads = scraper_fn(driver)
                for lead in leads:
                    try:
                        inserted = _insert_lead(conn, lead)
                        if inserted:
                            new_count += 1
                        else:
                            dup_count += 1
                    except Exception as e:
                        logger.error("Lead insert error: %s", e)
            except Exception as e:
                logger.error("Scraper %s failed: %s", scraper_fn.__name__, e)
    finally:
        driver.quit()
        conn.close()

    return new_count, dup_count


if __name__ == "__main__":
    new, dupes = run()
    print(f"{new} new leads added, {dupes} duplicates skipped")
