"""
main.py - Internship Outreach Tool

Usage:
  python main.py                             Run daily email batch
  python main.py --dry-run [--limit N]       Preview emails without sending

  python main.py fetch columbia              Scrape Columbia alumni directory
  python main.py fetch linkedin              Scrape LinkedIn connections
  python main.py fetch all                   Scrape both

  python main.py send columbia [--limit N] [--dry-run] [--headless]
                                             Send messages via Columbia alumni portal
  python main.py send linkedin [--limit N] [--dry-run] [--headless]
                                             Send LinkedIn connection requests (with note)
  python main.py send all [--limit N] [--dry-run] [--headless]
                                             Run both channels back-to-back

  python main.py import linkedin  FILE       Import LinkedIn connections CSV
  python main.py import apollo    FILE       Import Apollo.io export CSV
  python main.py import columbia  FILE       Import Columbia alumni CSV
  python main.py import manual    FILE       Import generic contact CSV
  python main.py import enrich    FILE       Match Apollo-enriched CSV to DB (update emails)

  python main.py guess-emails [--dry-run]    Guess emails for known company domains
  python main.py verify-emails [--limit N]   SMTP-verify guessed emails
  python main.py scrape-emails [--limit N] [--dry-run]
                                             Web-search emails (GitHub, DDG, Hunter.io)

  python main.py export [FILE]               Export contacts needing enrichment to CSV
  python main.py stats                       Show DB statistics
  python main.py list [--status STATUS]      List contacts (filter by status)
  python main.py test-gmail                  Test Gmail API connection
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "15"))


# ---------------------------------------------------------------------------
# Email batch
# ---------------------------------------------------------------------------

def run_daily_batch(dry_run: bool = False, dry_run_limit: int = 5):
    from database import init_db, get_pending_contacts, mark_emailed, log_email, how_many_sent_today
    from gmail_client import send_email
    from email_generator import generate_email

    init_db()

    sent_today = how_many_sent_today()
    remaining = DAILY_LIMIT - sent_today

    if remaining <= 0:
        print(f"Daily limit of {DAILY_LIMIT} already reached. Come back tomorrow!")
        return

    contacts = get_pending_contacts(limit=dry_run_limit if dry_run else remaining)

    if not contacts:
        print(
            "No contacts with real emails ready to send. Run:\n"
            "  python main.py guess-emails      (pattern-based, instant)\n"
            "  python main.py scrape-emails     (web search, slower)\n"
        )
        return

    print(f"{'[DRY RUN] ' if dry_run else ''}Sending to {len(contacts)} contacts "
          f"(limit: {DAILY_LIMIT}/day, sent today: {sent_today})\n")

    for i, contact in enumerate(contacts, 1):
        try:
            subject, body = generate_email(contact)
        except Exception as e:
            print(f"[{i}] Email generation failed for {contact['first_name']}: {e}")
            continue

        email_addr = contact["email"]
        print(f"[{i}/{len(contacts)}] {contact['first_name']} {contact['last_name']} "
              f"<{email_addr}>")
        print(f"  Company: {contact.get('company', 'N/A')} | Role: {contact.get('role', 'N/A')}")
        print(f"  Subject: {subject}")

        if dry_run:
            print(f"  Body:\n{body}")
            print("-" * 60)
            continue

        try:
            send_email(to=email_addr, subject=subject, body=body)
            mark_emailed(contact["id"])
            log_email(contact["id"], email_addr, subject, body)
            print(f"  Sent!\n")
        except Exception as e:
            print(f"  Failed: {e}\n")

    print("Batch complete.")


# ---------------------------------------------------------------------------
# Outreach via portal + LinkedIn
# ---------------------------------------------------------------------------

def run_send(channel: str, limit: int = None, dry_run: bool = False, headless: bool = False):
    channels = []
    if channel in ("columbia", "all"):
        channels.append("columbia")
    if channel in ("linkedin", "all"):
        channels.append("linkedin")
    if not channels:
        print(f"Unknown channel '{channel}'. Use: columbia, linkedin, all")
        sys.exit(1)

    for ch in channels:
        if ch == "columbia":
            print("\n--- Columbia Alumni Portal Messages ---")
            from columbia_messenger import run_batch as columbia_send
            asyncio.run(columbia_send(limit=limit, dry_run=dry_run, headless=headless))
        elif ch == "linkedin":
            print("\n--- LinkedIn Connection Requests ---")
            from linkedin_connector import run_batch as linkedin_send
            asyncio.run(linkedin_send(limit=limit, dry_run=dry_run, headless=headless))


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def run_fetch(source: str):
    sources = []
    if source in ("columbia", "all"):
        sources.append("columbia")
    if source in ("linkedin", "all"):
        sources.append("linkedin")
    if not sources:
        print(f"Unknown source '{source}'. Use: columbia, linkedin, all")
        sys.exit(1)

    for src in sources:
        if src == "columbia":
            print("\n--- Fetching Columbia Alumni ---")
            from columbia_alumni_scraper import run_scrape as columbia_scrape
            asyncio.run(columbia_scrape())
        elif src == "linkedin":
            print("\n--- Fetching LinkedIn Connections ---")
            from linkedin_scraper import run_scrape as linkedin_scrape
            asyncio.run(linkedin_scrape())


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def run_import(source: str, filepath: str):
    from database import init_db
    from contact_importer import import_linkedin, import_apollo, import_manual, enrich_from_apollo

    init_db()

    if source == "linkedin":
        count = import_linkedin(filepath)
    elif source == "apollo":
        count = import_apollo(filepath)
    elif source == "enrich":
        count = enrich_from_apollo(filepath)
    elif source in ("columbia", "manual"):
        default_columbia = 1 if source == "columbia" else 0
        count = import_manual(filepath, default_columbia=default_columbia)
    else:
        print(f"Unknown import source '{source}'. Use: linkedin, apollo, enrich, columbia, manual")
        sys.exit(1)

    print(f"Imported/updated {count} contacts from {filepath}")


# ---------------------------------------------------------------------------
# Email enrichment
# ---------------------------------------------------------------------------

def run_guess_emails(dry_run: bool = False):
    from email_enricher import guess_emails_from_company
    n, skipped = guess_emails_from_company(dry_run=dry_run)
    tag = "[DRY RUN] Would update" if dry_run else "Updated"
    print(f"\n{tag} {n} contacts with guessed emails.")
    print(f"Skipped {skipped} contacts (company domain unknown or email already taken).")
    print("Run 'python main.py stats' to see how many are now ready to send.")


def run_verify_emails(dry_run: bool = False, limit: int = None):
    from email_enricher import verify_guessed_emails
    verify_guessed_emails(dry_run=dry_run, limit=limit)


def run_scrape_emails(limit: int = None, dry_run: bool = False):
    from email_scraper import run_enrichment
    run_enrichment(limit=limit, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_for_enrichment(filepath: str = "enrich_export.csv"):
    import csv
    from database import init_db, get_conn

    init_db()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT first_name, last_name, linkedin_url, company, role
            FROM contacts
            WHERE email LIKE '%-pending.local'
            ORDER BY priority DESC, id ASC
        """).fetchall()

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["First Name", "Last Name", "LinkedIn URL", "Company", "Title"]
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "First Name":   row["first_name"],
                "Last Name":    row["last_name"],
                "LinkedIn URL": row["linkedin_url"] or "",
                "Company":      row["company"] or "",
                "Title":        row["role"] or "",
            })

    print(f"Exported {len(rows)} contacts needing enrichment -> {filepath}")


# ---------------------------------------------------------------------------
# Stats + list
# ---------------------------------------------------------------------------

def show_stats():
    from database import init_db, get_stats, how_many_columbia_sent_today, how_many_linkedin_sent_today
    init_db()
    stats = get_stats()
    print("\n=== Outreach Stats ===")
    print(f"  Total contacts:     {stats.get('total_contacts', 0):,}")
    print(f"  Columbia alumni:    {stats.get('columbia_alumni', 0):,}")
    print()
    print(f"  Real emails:        {stats.get('real_emails', 0):,}")
    print(f"  Placeholder emails: {stats.get('placeholder_emails', 0):,}")
    print(f"  Ready to send (email): {stats.get('ready_to_send', 0):,}")
    print()
    print(f"  Portal msgs sent today:     {how_many_columbia_sent_today()}")
    print(f"  LinkedIn requests sent today: {how_many_linkedin_sent_today()}")
    print(f"  Total emails sent:  {stats.get('total_emails_sent', 0):,}")
    print()
    for key, val in stats.items():
        if key.startswith("status_"):
            label = key.replace("status_", "").ljust(12)
            print(f"  Status {label}: {val:,}")


def list_contacts(status: str = None):
    from database import init_db, list_contacts as db_list
    init_db()
    contacts = db_list(status=status)
    if not contacts:
        print("No contacts found.")
        return
    print(f"\n{'ID':<5} {'Name':<25} {'Company':<25} {'Status':<10} {'Email'}")
    print("-" * 100)
    for c in contacts:
        name = f"{c['first_name']} {c['last_name']}"
        print(f"{c['id']:<5} {name:<25} {(c['company'] or ''):<25} {c['status']:<10} {c['email']}")


def test_gmail():
    from gmail_client import test_connection
    test_connection()


# ---------------------------------------------------------------------------
# CLI router
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    if not args:
        run_daily_batch(dry_run=False)

    elif args[0] == "--dry-run":
        limit = 5
        if "--limit" in args:
            idx = args.index("--limit")
            if idx + 1 < len(args):
                limit = int(args[idx + 1])
        run_daily_batch(dry_run=True, dry_run_limit=limit)

    elif args[0] == "fetch":
        source = args[1] if len(args) > 1 else "all"
        run_fetch(source)

    elif args[0] == "send":
        channel = args[1] if len(args) > 1 else "all"
        limit = None
        if "--limit" in args:
            idx = args.index("--limit")
            if idx + 1 < len(args):
                limit = int(args[idx + 1])
        run_send(
            channel,
            limit=limit,
            dry_run="--dry-run" in args,
            headless="--headless" in args,
        )

    elif args[0] == "import":
        if len(args) < 3:
            print("Usage: python main.py import <source> <file.csv>")
            sys.exit(1)
        run_import(args[1], args[2])

    elif args[0] == "guess-emails":
        run_guess_emails(dry_run="--dry-run" in args)

    elif args[0] == "verify-emails":
        limit = None
        if "--limit" in args:
            idx = args.index("--limit")
            if idx + 1 < len(args):
                limit = int(args[idx + 1])
        run_verify_emails(dry_run="--dry-run" in args, limit=limit)

    elif args[0] == "scrape-emails":
        limit = None
        if "--limit" in args:
            idx = args.index("--limit")
            if idx + 1 < len(args):
                limit = int(args[idx + 1])
        run_scrape_emails(limit=limit, dry_run="--dry-run" in args)

    elif args[0] == "export":
        out = args[1] if len(args) > 1 else "enrich_export.csv"
        export_for_enrichment(out)

    elif args[0] == "stats":
        show_stats()

    elif args[0] == "list":
        status = None
        if "--status" in args:
            idx = args.index("--status")
            if idx + 1 < len(args):
                status = args[idx + 1]
        list_contacts(status=status)

    elif args[0] == "test-gmail":
        test_gmail()

    else:
        print(__doc__)


if __name__ == "__main__":
    main()