"""
bot.py — Telegram bot for managing AVVA Properties leads on the go.
Uses python-telegram-bot v20+ (async).
"""

import asyncio
import logging
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
logger = logging.getLogger("bot")

DB_PATH = "leads.db"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _lead_counts() -> dict:
    conn = _db()
    cur = conn.execute(
        "SELECT status, COUNT(*) FROM leads GROUP BY status"
    )
    counts = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return counts


def _hot_leads() -> list[sqlite3.Row]:
    conn = _db()
    cur = conn.execute(
        """SELECT l.id, l.owner_name, l.property_address, l.city, l.county,
                  l.source, da.deal_grade, da.suggested_offer
           FROM leads l
           LEFT JOIN deal_analysis da ON da.lead_id = l.id
           WHERE l.status = 'hot'
           ORDER BY da.deal_grade, l.created_at DESC"""
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def _lead_detail(lead_id: int) -> dict:
    conn = _db()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    contact = conn.execute("SELECT * FROM contacts WHERE lead_id = ?", (lead_id,)).fetchone()
    outreach = conn.execute(
        "SELECT channel, sequence_day, status, sent_at FROM outreach_log WHERE lead_id = ? ORDER BY sent_at",
        (lead_id,),
    ).fetchall()
    analysis = conn.execute(
        "SELECT * FROM deal_analysis WHERE lead_id = ? ORDER BY analyzed_at DESC LIMIT 1",
        (lead_id,),
    ).fetchone()
    conn.close()
    return {"lead": lead, "contact": contact, "outreach": outreach, "analysis": analysis}


def _update_lead_status(lead_id: int, status: str) -> bool:
    conn = _db()
    conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    conn.commit()
    affected = conn.execute("SELECT changes()").fetchone()[0]
    conn.close()
    return affected > 0


def _stats() -> dict:
    conn = _db()
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    deals = conn.execute("SELECT COUNT(*) FROM leads WHERE status = 'deal'").fetchone()[0]
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    hot_week = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE status = 'hot' AND created_at >= ?", (week_ago,)
    ).fetchone()[0]
    conn.close()
    return {"total": total, "deals": deals, "hot_week": hot_week}


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Welcome to AVVA Properties Bot!\n\n"
        "I help you manage your wholesale real estate pipeline in Orlando FL.\n\n"
        "Commands:\n"
        "/leads — lead counts by status\n"
        "/hot — all hot leads\n"
        "/deal <id> — mark lead as deal\n"
        "/dead <id> — mark lead as dead\n"
        "/info <id> — full lead detail\n"
        "/run scraper|skiptracer|outreach — trigger pipeline module\n"
        "/stats — overall stats"
    )


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    counts = _lead_counts()
    lines = [f"*Lead Counts*"]
    for status in ["new", "skip_traced", "contacted", "hot", "dead", "deal"]:
        lines.append(f"{status}: {counts.get(status, 0)}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_hot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = _hot_leads()
    if not rows:
        await update.message.reply_text("No hot leads right now.")
        return
    lines = ["*Hot Leads*\n"]
    for r in rows:
        offer = f"${r['suggested_offer']:,.0f}" if r["suggested_offer"] else "TBD"
        grade = r["deal_grade"] or "?"
        lines.append(
            f"ID {r['id']} — {r['owner_name']}\n"
            f"{r['property_address']}, {r['city']} FL\n"
            f"Grade: {grade} | Offer: {offer}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_deal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /deal <lead_id>")
        return
    try:
        lead_id = int(context.args[0])
        if _update_lead_status(lead_id, "deal"):
            await update.message.reply_text(f"Lead {lead_id} marked as DEAL. Congratulations!")
        else:
            await update.message.reply_text(f"Lead {lead_id} not found.")
    except ValueError:
        await update.message.reply_text("Lead ID must be a number.")


async def cmd_dead(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /dead <lead_id>")
        return
    try:
        lead_id = int(context.args[0])
        if _update_lead_status(lead_id, "dead"):
            await update.message.reply_text(f"Lead {lead_id} marked as dead.")
        else:
            await update.message.reply_text(f"Lead {lead_id} not found.")
    except ValueError:
        await update.message.reply_text("Lead ID must be a number.")


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /info <lead_id>")
        return
    try:
        lead_id = int(context.args[0])
        detail = _lead_detail(lead_id)
        lead = detail["lead"]
        if not lead:
            await update.message.reply_text(f"Lead {lead_id} not found.")
            return

        lines = [
            f"*Lead {lead_id}*",
            f"Name: {lead['owner_name']}",
            f"Address: {lead['property_address']}, {lead['city']} FL",
            f"County: {lead['county']} | Source: {lead['source']}",
            f"Status: {lead['status']}",
            f"Created: {lead['created_at']}",
        ]

        contact = detail["contact"]
        if contact:
            lines += [
                "\n*Contact Info*",
                f"Phone 1: {contact['phone_1'] or 'N/A'}",
                f"Phone 2: {contact['phone_2'] or 'N/A'}",
                f"Email 1: {contact['email_1'] or 'N/A'}",
                f"Email 2: {contact['email_2'] or 'N/A'}",
                f"Mailing: {contact['mailing_address'] or 'N/A'}",
            ]

        outreach = detail["outreach"]
        if outreach:
            lines.append("\n*Outreach History*")
            for o in outreach:
                lines.append(f"Day {o['sequence_day']} {o['channel']} — {o['status']} @ {o['sent_at']}")

        analysis = detail["analysis"]
        if analysis:
            lines += [
                "\n*Deal Analysis*",
                f"ARV: ${analysis['arv']:,.0f} ({analysis['arv_confidence']} confidence)",
                f"Repair est: ${analysis['estimated_repair_cost']:,.0f}",
                f"MAO: ${analysis['mao']:,.0f}",
                f"Suggested offer: ${analysis['suggested_offer']:,.0f}",
                f"Grade: {analysis['deal_grade']}",
                f"Summary: {analysis['summary']}",
            ]

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("Lead ID must be a number.")


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /run scraper|skiptracer|outreach")
        return
    module = context.args[0].lower()
    scripts = {"scraper": "scraper.py", "skiptracer": "skiptracer.py", "outreach": "outreach.py"}
    if module not in scripts:
        await update.message.reply_text("Unknown module. Use: scraper, skiptracer, or outreach.")
        return
    await update.message.reply_text(f"Starting {module}...")
    try:
        result = subprocess.run(
            ["python", scripts[module]],
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (result.stdout + result.stderr)[-3000:]  # Telegram limit
        await update.message.reply_text(f"{module} finished:\n{output}")
    except subprocess.TimeoutExpired:
        await update.message.reply_text(f"{module} timed out after 5 minutes.")
    except Exception as e:
        await update.message.reply_text(f"{module} error: {e}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _stats()
    await update.message.reply_text(
        f"*AVVA Properties Stats*\n"
        f"Total leads: {s['total']}\n"
        f"Deals closed: {s['deals']}\n"
        f"Hot leads this week: {s['hot_week']}",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Proactive notification functions (importable without starting bot loop)
# ---------------------------------------------------------------------------

def notify_hot_lead(lead_id: int) -> None:
    """Send hot lead alert to TELEGRAM_CHAT_ID. Safe to call from any module."""
    conn = _db()
    try:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        analysis = conn.execute(
            "SELECT * FROM deal_analysis WHERE lead_id = ? ORDER BY analyzed_at DESC LIMIT 1",
            (lead_id,),
        ).fetchone()

        if not lead:
            logger.error("notify_hot_lead: lead %d not found", lead_id)
            return

        arv = f"${analysis['arv']:,.0f}" if analysis and analysis["arv"] else "TBD"
        repair = f"${analysis['estimated_repair_cost']:,.0f}" if analysis and analysis["estimated_repair_cost"] else "TBD"
        mao = f"${analysis['mao']:,.0f}" if analysis and analysis["mao"] else "TBD"
        offer = f"${analysis['suggested_offer']:,.0f}" if analysis and analysis["suggested_offer"] else "TBD"
        grade = analysis["deal_grade"] if analysis else "?"
        summary = analysis["summary"] if analysis else ""

        text = (
            f"🔥 Hot lead — {lead['owner_name']}\n"
            f"{lead['property_address']}, {lead['city']} FL\n"
            f"Source: {lead['source']}\n"
            f"ARV: {arv} | Repair est: {repair}\n"
            f"MAO: {mao} | Suggested offer: {offer}\n"
            f"Deal grade: {grade}\n"
            f"{summary}\n\n"
            f"/deal {lead_id} to close | /dead {lead_id} to kill | /info {lead_id} for details"
        )
        _send_telegram_sync(text)
    except Exception as e:
        logger.error("notify_hot_lead error: %s", e)
    finally:
        conn.close()


def notify_error(message: str) -> None:
    """Send error alert to TELEGRAM_CHAT_ID."""
    try:
        _send_telegram_sync(f"⚠️ AVVA Properties Error:\n{message}")
    except Exception as e:
        logger.error("notify_error failed: %s", e)


def _send_telegram_sync(text: str) -> None:
    """Send a message synchronously using requests (no bot loop needed)."""
    import requests as _requests
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    _requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text},
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Bot startup
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("leads", cmd_leads))
    app.add_handler(CommandHandler("hot", cmd_hot))
    app.add_handler(CommandHandler("deal", cmd_deal))
    app.add_handler(CommandHandler("dead", cmd_dead))
    app.add_handler(CommandHandler("info", cmd_info))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("stats", cmd_stats))

    logger.info("AVVA Properties bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
