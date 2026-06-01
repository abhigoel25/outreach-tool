"""
run_daily.py - Scheduled daily outreach runner

Runs every morning via Windows Task Scheduler (see run_daily.bat).
Sends:
  - Up to LINKEDIN_DAILY_LIMIT LinkedIn connection requests  (default: 12)
  - Up to COLUMBIA_DAILY_LIMIT Columbia alumni portal msgs   (default: 20)

Logs everything to logs/outreach_YYYY-MM-DD.log so you can review
what happened if you were away from your computer.

Usage (direct):
  python run_daily.py
  python run_daily.py --dry-run
  python run_daily.py --headless        (no visible browser window)
"""

import asyncio
import os
import socket
import sys
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Per-channel daily limits (override in .env)
LINKEDIN_DAILY_LIMIT = int(os.getenv("LINKEDIN_DAILY_LIMIT", "12"))
COLUMBIA_DAILY_LIMIT = int(os.getenv("COLUMBIA_DAILY_LIMIT", "20"))

# Logs directory (created automatically)
LOGS_DIR = Path(__file__).parent / "logs"


def _is_connected(host: str = "8.8.8.8", port: int = 53, timeout: float = 3.0) -> bool:
    """Return True if we can open a TCP connection to the given host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def _wait_for_internet(log, max_wait_minutes: int = 30, check_interval_seconds: int = 60) -> bool:
    """Block until internet is available or the max wait is exceeded."""
    if _is_connected():
        return True
    log.warning("No internet connection detected. Waiting for connectivity...")
    waited = 0
    while waited < max_wait_minutes * 60:
        await asyncio.sleep(check_interval_seconds)
        waited += check_interval_seconds
        if _is_connected():
            log.info(f"Internet connection restored after {waited // 60}m {waited % 60}s.")
            return True
        log.warning(f"Still no internet ({waited // 60}m elapsed). Retrying...")
    log.error(f"No internet connection after {max_wait_minutes} minutes. Aborting.")
    return False


def setup_logging():
    LOGS_DIR.mkdir(exist_ok=True)
    log_file = LOGS_DIR / f"outreach_{datetime.now().strftime('%Y-%m-%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


async def main(dry_run: bool = False, headless: bool = False):
    log_file = setup_logging()
    log = logging.getLogger(__name__)

    # Ensure DB schema (including any pending migrations) is up to date
    from database import init_db
    init_db()

    log.info("=" * 60)
    log.info(f"Daily outreach starting  (dry_run={dry_run}, headless={headless})")
    log.info(f"LinkedIn limit: {LINKEDIN_DAILY_LIMIT}  |  Columbia limit: {COLUMBIA_DAILY_LIMIT}")
    log.info("=" * 60)

    # Wait up to 30 minutes for internet before proceeding
    if not await _wait_for_internet(log):
        return

    # ---- LinkedIn ----
    try:
        log.info("\n--- LinkedIn Connection Requests ---")
        from linkedin_connector import run_batch as linkedin_send
        from database import how_many_linkedin_sent_today
        already = how_many_linkedin_sent_today()
        remaining = LINKEDIN_DAILY_LIMIT - already
        if remaining <= 0:
            log.info(f"[linkedin] Daily limit ({LINKEDIN_DAILY_LIMIT}) already reached.")
        else:
            await linkedin_send(limit=LINKEDIN_DAILY_LIMIT, dry_run=dry_run, headless=headless)
    except Exception as e:
        log.error(f"[linkedin] FATAL ERROR: {e}", exc_info=True)

    # ---- Columbia ----
    try:
        log.info("\n--- Columbia Alumni Portal Messages ---")
        from columbia_messenger import run_batch as columbia_send
        from database import how_many_columbia_sent_today
        already = how_many_columbia_sent_today()
        remaining = COLUMBIA_DAILY_LIMIT - already
        if remaining <= 0:
            log.info(f"[columbia] Daily limit ({COLUMBIA_DAILY_LIMIT}) already reached.")
        else:
            await columbia_send(limit=COLUMBIA_DAILY_LIMIT, dry_run=dry_run, headless=headless)
    except Exception as e:
        log.error(f"[columbia] FATAL ERROR: {e}", exc_info=True)

    log.info("\n" + "=" * 60)
    log.info(f"Daily outreach complete. Log saved to: {log_file}")
    log.info("=" * 60)


if __name__ == "__main__":
    dry_run  = "--dry-run"  in sys.argv
    headless = "--headless" in sys.argv
    asyncio.run(main(dry_run=dry_run, headless=headless))