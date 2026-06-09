"""
send_apollo_multi.py
Send emails to the multi-company contacts from Apollo-enriched CSV.

The CSV (apollo_multi_company.csv) contains verified emails returned by Apollo.
Each contact already exists in outreach.db — we look them up by LinkedIn URL
(falling back to first+last+company) to pull columbia_alumni / grad_year for
personalisation, then send via Gmail and mark them emailed in the DB.

Usage:
  python send_apollo_multi.py --dry-run    # preview emails, no sends
  python send_apollo_multi.py              # actually send
"""

import csv
import sys
from pathlib import Path

from database import init_db, get_conn, mark_emailed, log_email
from gmail_client import send_email
from email_generator import generate_email

DRY_RUN  = "--dry-run" in sys.argv
CSV_PATH = Path(__file__).parent / "apollo_multi_company.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_linkedin(url: str) -> str:
    """Strip trailing slashes and force https for consistent comparison."""
    return (url or "").strip().rstrip("/").lower()


def _lookup_contact(conn, linkedin_url: str, first: str, last: str, company: str):
    """
    Return the DB row for this person, or None.
    Strategy: LinkedIn URL first (most reliable), then name+company fuzzy.
    """
    if linkedin_url:
        norm = _normalise_linkedin(linkedin_url)
        row = conn.execute(
            "SELECT * FROM contacts WHERE LOWER(TRIM(linkedin_url, '/')) = ?",
            (norm,)
        ).fetchone()
        if row:
            return row

    # Fallback: first + last + company (case-insensitive)
    row = conn.execute(
        """SELECT * FROM contacts
           WHERE LOWER(first_name) = LOWER(?)
             AND LOWER(last_name)  = LOWER(?)
             AND LOWER(company)    LIKE LOWER(?)""",
        (first.strip(), last.strip(), f"%{company.strip()[:20]}%"),
    ).fetchone()
    return row


def _already_emailed(conn, contact_id: int) -> bool:
    """True if we've already sent an email to this contact."""
    row = conn.execute(
        "SELECT 1 FROM emails_sent WHERE contact_id = ? LIMIT 1", (contact_id,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Load CSV
# ---------------------------------------------------------------------------

def load_apollo_csv() -> list[dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    init_db()

    rows = load_apollo_csv()
    print(f"Loaded {len(rows)} rows from {CSV_PATH.name}")

    conn = get_conn()

    to_send   = []
    skipped   = 0
    no_match  = 0

    for row in rows:
        first       = (row.get("First Name") or "").strip()
        last        = (row.get("Last Name")  or "").strip()
        email       = (row.get("Email")      or "").strip()
        role        = (row.get("Title")      or "").strip()
        company     = (row.get("Company Name") or row.get("Company Name for Emails") or "").strip()
        linkedin    = (row.get("Person Linkedin Url") or "").strip()
        email_status = (row.get("Email Status") or "").strip().lower()

        # Only proceed with verified emails
        if not email or "@" not in email or email_status not in ("verified", ""):
            skipped += 1
            continue

        db_row = _lookup_contact(conn, linkedin, first, last, company)

        if db_row is None:
            # Contact not in DB — build a minimal contact dict from CSV only
            no_match += 1
            contact = {
                "id":             None,
                "first_name":     first,
                "last_name":      last,
                "email":          email,
                "company":        company,
                "role":           role,
                "linkedin_url":   linkedin,
                "columbia_alumni": 0,
                "grad_year":      "",
                "status":         "pending",
            }
        else:
            contact_id = db_row["id"]

            # Skip if already emailed
            if db_row["status"] == "emailed" or _already_emailed(conn, contact_id):
                skipped += 1
                continue

            # Merge: use Apollo email (verified) + DB metadata for personalisation
            contact = dict(db_row)
            contact["email"] = email          # override with Apollo-verified email
            contact["role"]  = role or contact.get("role", "")

        to_send.append(contact)

    conn.close()

    print(f"  Ready to send : {len(to_send)}")
    print(f"  Skipped       : {skipped}  (already emailed or unverified)")
    print(f"  No DB match   : {no_match}  (sent from CSV data only)")
    print()

    if not to_send:
        print("Nothing to send.")
        return

    sent_count  = 0
    fail_count  = 0

    for i, contact in enumerate(to_send, 1):
        try:
            subject, body = generate_email(contact)
        except Exception as e:
            print(f"[{i}/{len(to_send)}] Email generation FAILED for {contact['first_name']}: {e}")
            fail_count += 1
            continue

        tag = f"[DRY RUN] " if DRY_RUN else ""
        print(f"{tag}[{i}/{len(to_send)}] {contact['first_name']} {contact['last_name']} <{contact['email']}>")
        print(f"  Company : {contact.get('company', 'N/A')} | Role: {contact.get('role', 'N/A')}")
        print(f"  Columbia: {'yes' if contact.get('columbia_alumni') else 'no'} | Grad: {contact.get('grad_year') or 'unknown'}")
        print(f"  Subject : {subject}")

        if DRY_RUN:
            print(f"  Body:\n{body}")
            print("-" * 60)
            continue

        try:
            send_email(to=contact["email"], subject=subject, body=body)
            if contact.get("id"):
                mark_emailed(contact["id"])
                log_email(contact["id"], contact["email"], subject, body)
            print(f"  Sent!\n")
            sent_count += 1
        except Exception as e:
            print(f"  FAILED: {e}\n")
            fail_count += 1

    if not DRY_RUN:
        print(f"\nDone. Sent: {sent_count} | Failed: {fail_count}")
    else:
        print(f"\n[DRY RUN complete — {len(to_send)} emails previewed, none sent]")


if __name__ == "__main__":
    main()
